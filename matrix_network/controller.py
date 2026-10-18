"""Explicit known-network/fallback-AP state machine."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .backend import AP_PROFILE_NAME, NetworkManagerBackend
from .models import NetworkOperationError, NetworkValidationError


Clock = Callable[[], float]
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class NetworkPolicy:
    """Timing and addressing decisions for the first supported Pi target."""

    boot_grace_seconds: float = 60.0
    loss_grace_seconds: float = 120.0
    retry_backoff_seconds: tuple[float, ...] = (5.0, 15.0, 30.0)
    candidate_timeout_seconds: int = 45
    monitor_interval_seconds: float = 5.0
    ap_address: str = "192.168.50.1/24"


def validate_credentials(ssid: Any, passphrase: Any, hidden: Any) -> tuple[str, str, bool]:
    """Validate Wi-Fi values before any privileged command sees them.

    NetworkManager accepts UTF-8 SSIDs up to 32 bytes. Empty passphrase means an open
    network; otherwise the initial implementation supports WPA-PSK passphrases of
    8–63 characters. Control characters are rejected from both values.
    """

    if type(ssid) is not str:
        raise NetworkValidationError("ssid must be a string")
    encoded_ssid = ssid.encode("utf-8")
    if not (1 <= len(encoded_ssid) <= 32):
        raise NetworkValidationError("ssid must contain 1–32 UTF-8 bytes")
    if any(ord(character) < 32 or ord(character) == 127 for character in ssid):
        raise NetworkValidationError("ssid must not contain control characters")

    if type(passphrase) is not str:
        raise NetworkValidationError("passphrase must be a string")
    if passphrase and not (8 <= len(passphrase) <= 63):
        raise NetworkValidationError("passphrase must be empty or contain 8–63 characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in passphrase):
        raise NetworkValidationError("passphrase must not contain control characters")

    if type(hidden) is not bool:
        raise NetworkValidationError("hidden must be a boolean")
    return ssid, passphrase, hidden


class NetworkController:
    """Own connectivity policy while NetworkManager owns credentials and DHCP.

    ``_transition_lock`` serializes potentially disruptive profile changes. Status
    uses a separate short-held lock so the API remains responsive while nmcli waits
    for association or DHCP.
    """

    STATES = {
        "boot",
        "trying_known",
        "lan_connected",
        "loss_grace",
        "starting_ap",
        "ap_ready",
        "provisioning",
        "failed",
    }

    def __init__(
        self,
        backend: NetworkManagerBackend,
        *,
        ap_uuid: str,
        ap_ssid: str,
        policy: NetworkPolicy = NetworkPolicy(),
        clock: Clock = time.monotonic,
    ):
        self.backend = backend
        self.ap_uuid = ap_uuid
        self.ap_ssid = ap_ssid
        self.policy = policy
        self._clock = clock
        self._state_lock = threading.RLock()
        self._transition_lock = threading.Lock()

        now = clock()
        self._started_at = now
        self._state = "boot"
        self._mode = "trying"
        self._ssid: str | None = None
        self._ipv4: str | None = None
        self._last_error: str | None = None
        self._transition_id = 0
        self._ever_connected = False
        self._disconnected_since: float | None = None
        self._attempt_index = 0
        self._next_attempt_at = now

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            ap_active = self._state == "ap_ready"
            return {
                "mode": self._mode,
                "state": self._state,
                "interface": self.backend.interface,
                "ipv4": self._ipv4,
                "ssid": self._ssid,
                "ap": {
                    "active": ap_active,
                    "ssid": self.ap_ssid if ap_active else None,
                    "address": self.policy.ap_address.split("/", 1)[0] if ap_active else None,
                },
                "last_error": self._last_error,
                "transition_id": self._transition_id,
            }

    def tick(self) -> None:
        """Advance policy once; safe to call repeatedly from a monitor thread."""

        snapshot = self.backend.device_snapshot()
        now = self._clock()

        if snapshot.connection == AP_PROFILE_NAME and snapshot.ipv4:
            self._set_state(
                state="ap_ready",
                mode="setup_ap",
                ssid=self.ap_ssid,
                ipv4=snapshot.ipv4,
                error=None,
            )
            self._disconnected_since = None
            return

        if snapshot.connected:
            self._ever_connected = True
            self._disconnected_since = None
            self._attempt_index = 0
            self._next_attempt_at = now
            self._set_state(
                state="lan_connected",
                mode="lan",
                ssid=snapshot.connection,
                ipv4=snapshot.ipv4,
                error=None,
            )
            return

        if self._disconnected_since is None:
            self._disconnected_since = now
            self._next_attempt_at = now
            self._attempt_index = 0

        grace = (
            self.policy.loss_grace_seconds if self._ever_connected else self.policy.boot_grace_seconds
        )
        elapsed = now - self._disconnected_since
        self._set_state(
            state="loss_grace" if self._ever_connected else "trying_known",
            mode="trying",
            ssid=None,
            ipv4=None,
            error=self._last_error,
        )

        if now >= self._next_attempt_at:
            if self.try_known_once():
                return
            backoff = self.policy.retry_backoff_seconds[
                min(self._attempt_index, len(self.policy.retry_backoff_seconds) - 1)
            ]
            self._attempt_index += 1
            self._next_attempt_at = now + backoff

        # The monitor intentionally waits the full grace window even if no profiles
        # exist. This prevents a slow Wi-Fi radio during boot from immediately
        # replacing a connection that NetworkManager is still bringing up.
        if elapsed >= grace:
            self.start_ap()

    def try_known_once(self) -> bool:
        """Try saved non-AP profiles in priority order without deleting any of them."""

        if not self._transition_lock.acquire(blocking=False):
            return False
        try:
            profiles = self.backend.known_profiles()
            if not profiles:
                self._set_error("no saved Wi-Fi profiles are available")
                return False
            self._set_state("trying_known", "trying", None, None, None)
            for profile in profiles:
                try:
                    self.backend.activate(profile.uuid, timeout=15)
                    snapshot = self.backend.device_snapshot()
                    if snapshot.connected and snapshot.connection != AP_PROFILE_NAME:
                        self._ever_connected = True
                        self._disconnected_since = None
                        self._set_state(
                            "lan_connected",
                            "lan",
                            snapshot.connection,
                            snapshot.ipv4,
                            None,
                        )
                        return True
                except NetworkOperationError:
                    # Continue to the next saved UUID. Errors are summarized rather
                    # than exposing profile names or command output.
                    continue
            self._set_error("saved Wi-Fi profiles could not connect")
            return False
        finally:
            self._transition_lock.release()

    def start_ap(self) -> dict[str, Any]:
        """Activate the project-owned AP profile idempotently."""

        with self._transition_lock:
            current = self.backend.device_snapshot()
            if current.connection == AP_PROFILE_NAME and current.ipv4:
                self._set_state("ap_ready", "setup_ap", self.ap_ssid, current.ipv4, None)
                return self.status()
            self._set_state("starting_ap", "transitioning", self.ap_ssid, None, None)
            try:
                snapshot = self.backend.start_ap(self.ap_uuid)
            except NetworkOperationError as exc:
                self._set_state("failed", "failed", None, None, str(exc))
                raise
            self._disconnected_since = None
            self._set_state("ap_ready", "setup_ap", self.ap_ssid, snapshot.ipv4, None)
            return self.status()

    def provision(self, *, job_id: str, ssid: Any, passphrase: Any, hidden: Any) -> dict[str, Any]:
        """Try a candidate profile, preserving every previously working profile."""

        clean_ssid, clean_passphrase, clean_hidden = validate_credentials(ssid, passphrase, hidden)
        with self._transition_lock:
            self._set_state("provisioning", "transitioning", clean_ssid, None, None)
            candidate = None
            try:
                candidate = self.backend.create_candidate(
                    job_id=job_id,
                    ssid=clean_ssid,
                    passphrase=clean_passphrase,
                    hidden=clean_hidden,
                )
                self.backend.activate(
                    candidate.uuid,
                    timeout=self.policy.candidate_timeout_seconds,
                    sensitive=True,
                )
                snapshot = self.backend.device_snapshot()
                if not snapshot.connected or snapshot.connection != candidate.name:
                    raise NetworkOperationError(
                        "candidate_no_address",
                        "The network associated but did not provide a usable local address",
                    )
                self.backend.promote_candidate(candidate, job_id=job_id)
                self._ever_connected = True
                self._disconnected_since = None
                self._set_state(
                    "lan_connected",
                    "lan",
                    clean_ssid,
                    snapshot.ipv4,
                    None,
                )
                return self.status()
            except Exception as exc:
                if candidate is not None:
                    try:
                        self.backend.delete_profile(candidate.uuid, sensitive=True)
                    except NetworkOperationError:
                        pass
                try:
                    self.backend.start_ap(self.ap_uuid)
                    self._set_state(
                        "ap_ready",
                        "setup_ap",
                        self.ap_ssid,
                        self.policy.ap_address.split("/", 1)[0],
                        "Could not join that network; setup access point was restored",
                    )
                except NetworkOperationError:
                    self._set_state(
                        "failed",
                        "failed",
                        None,
                        None,
                        "Could not join the network or restore the setup access point",
                    )
                if isinstance(exc, NetworkValidationError):
                    raise
                if isinstance(exc, NetworkOperationError):
                    raise NetworkOperationError(
                        "provision_failed",
                        "Could not join that network; setup access point was restored",
                    ) from exc
                raise NetworkOperationError(
                    "provision_failed",
                    "Could not join that network; setup access point was restored",
                ) from exc

    def scan(self) -> list[dict[str, Any]]:
        return [
            {"ssid": item.ssid, "signal": item.signal, "security": item.security}
            for item in self.backend.scan()
        ]

    def profiles(self) -> list[dict[str, Any]]:
        return [
            {
                "id": profile.uuid,
                "name": profile.name,
                "active": profile.active,
                "autoconnect": profile.autoconnect,
                "priority": profile.priority,
            }
            for profile in self.backend.known_profiles()
        ]

    def forget_profile(self, profile_uuid: str) -> None:
        """Delete only an inactive profile when another known profile remains."""

        with self._transition_lock:
            profiles = self.backend.known_profiles()
            match = next((profile for profile in profiles if profile.uuid == profile_uuid), None)
            if match is None:
                raise NetworkValidationError("unknown saved network profile")
            if match.active:
                raise NetworkValidationError("the active network profile cannot be forgotten")
            if len(profiles) <= 1:
                raise NetworkValidationError("the last saved network profile cannot be forgotten")
            self.backend.delete_profile(match.uuid)

    def monitor(self, stop_event: threading.Event) -> None:
        """Run bounded policy ticks until systemd requests shutdown."""

        while not stop_event.is_set():
            try:
                self.tick()
            except NetworkOperationError as exc:
                self._set_error(str(exc))
            stop_event.wait(self.policy.monitor_interval_seconds)

    def _set_error(self, message: str) -> None:
        with self._state_lock:
            self._last_error = message[:300]

    def _set_state(
        self,
        state: str,
        mode: str,
        ssid: str | None,
        ipv4: str | None,
        error: str | None,
    ) -> None:
        if state not in self.STATES:
            raise ValueError(f"invalid network state: {state}")
        with self._state_lock:
            changed = (state, mode, ssid, ipv4) != (
                self._state,
                self._mode,
                self._ssid,
                self._ipv4,
            )
            self._state = state
            self._mode = mode
            self._ssid = ssid
            self._ipv4 = ipv4
            self._last_error = error[:300] if error else None
            if changed:
                self._transition_id += 1
                # State/mode are enough to diagnose lifecycle behavior without
                # placing SSIDs, profile UUIDs, or credentials in the journal.
                LOGGER.info(
                    "network transition id=%d state=%s mode=%s",
                    self._transition_id,
                    state,
                    mode,
                )
