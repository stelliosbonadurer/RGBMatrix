"""NetworkManager operations used by the policy controller.

Non-secret values use individual ``nmcli`` argv entries and nothing invokes a shell.
Passphrases use the system D-Bus adapter so they never enter a process argument list.
Profile mutations use UUIDs after creation/lookup so duplicate human-readable names
cannot redirect a destructive operation.
"""

from __future__ import annotations

import re
from pathlib import Path

from .dbus_secrets import NetworkManagerDbusSecrets
from .models import DeviceSnapshot, ScanResult, WifiProfile, NetworkOperationError
from .nmcli import NmcliRunner, parse_property_lines, split_terse_line


AP_PROFILE_NAME = "rgb-matrix-setup-ap"
CANDIDATE_PREFIX = "rgb-matrix-candidate-"
MANAGED_PROFILE_PREFIX = "rgb-matrix-wifi-"


class NetworkManagerBackend:
    """A narrow NetworkManager adapter for one configured Wi-Fi interface."""

    def __init__(
        self,
        runner: NmcliRunner,
        interface: str = "wlan0",
        secret_store: NetworkManagerDbusSecrets | None = None,
    ):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", interface):
            raise ValueError("invalid Wi-Fi interface name")
        self.runner = runner
        self.interface = interface
        self.secret_store = secret_store

    def _require_secret_store(self) -> NetworkManagerDbusSecrets:
        if self.secret_store is None:
            raise NetworkOperationError(
                "dbus_unavailable",
                "NetworkManager secret storage is not configured",
            )
        return self.secret_store

    def device_snapshot(self) -> DeviceSnapshot:
        result = self.runner.execute(
            ["-t", "--escape", "yes", "-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS", "device", "show", self.interface]
        )
        properties = parse_property_lines(result.stdout)
        state_text = properties.get("GENERAL.STATE", "0")
        try:
            state_code = int(state_text.split(maxsplit=1)[0])
        except (ValueError, IndexError):
            state_code = 0
        connection = properties.get("GENERAL.CONNECTION") or None
        if connection in {"--", ""}:
            connection = None
        ipv4 = properties.get("IP4.ADDRESS[1]") or properties.get("IP4.ADDRESS") or None
        if ipv4:
            ipv4 = ipv4.split("/", 1)[0]
        return DeviceSnapshot(state_code=state_code, connection=connection, ipv4=ipv4)

    def wifi_profiles(self) -> list[WifiProfile]:
        result = self.runner.execute(
            ["-t", "--escape", "yes", "-f", "NAME,UUID,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY", "connection", "show"]
        )
        active_connection = self.device_snapshot().connection
        profiles: list[WifiProfile] = []
        for line in result.stdout.splitlines():
            fields = split_terse_line(line)
            if len(fields) != 5 or fields[2] != "802-11-wireless":
                continue
            try:
                priority = int(fields[4] or "0")
            except ValueError:
                priority = 0
            profiles.append(
                WifiProfile(
                    name=fields[0],
                    uuid=fields[1],
                    autoconnect=fields[3].lower() == "yes",
                    priority=priority,
                    active=fields[0] == active_connection,
                )
            )
        return profiles

    def known_profiles(self) -> list[WifiProfile]:
        profiles = [
            profile
            for profile in self.wifi_profiles()
            if profile.name != AP_PROFILE_NAME and not profile.name.startswith(CANDIDATE_PREFIX)
        ]
        return sorted(profiles, key=lambda profile: (-profile.priority, profile.name))

    def scan(self, *, rescan: bool = True, limit: int = 40) -> list[ScanResult]:
        result = self.runner.execute(
            [
                "-t",
                "--escape",
                "yes",
                "-f",
                "SSID,SIGNAL,SECURITY",
                "device",
                "wifi",
                "list",
                "ifname",
                self.interface,
                "--rescan",
                "yes" if rescan else "no",
            ],
            timeout=30.0,
        )
        best_by_network: dict[tuple[str, str], ScanResult] = {}
        for line in result.stdout.splitlines():
            fields = split_terse_line(line)
            if len(fields) != 3 or not fields[0]:
                continue
            try:
                signal = max(0, min(100, int(fields[1])))
            except ValueError:
                continue
            item = ScanResult(ssid=fields[0], signal=signal, security=fields[2] or "open")
            key = (item.ssid, item.security)
            previous = best_by_network.get(key)
            if previous is None or item.signal > previous.signal:
                best_by_network[key] = item
        return sorted(best_by_network.values(), key=lambda item: (-item.signal, item.ssid))[:limit]

    def ensure_ap_profile(self, *, ssid: str, passphrase: str, address: str) -> str:
        existing = next((p for p in self.wifi_profiles() if p.name == AP_PROFILE_NAME), None)
        if existing is None:
            self.runner.execute(
                [
                    "connection",
                    "add",
                    "type",
                    "wifi",
                    "ifname",
                    self.interface,
                    "con-name",
                    AP_PROFILE_NAME,
                    "ssid",
                    ssid,
                ],
                sensitive=True,
            )
            existing = next((p for p in self.wifi_profiles() if p.name == AP_PROFILE_NAME), None)
        if existing is None:
            raise NetworkOperationError("ap_profile_missing", "Setup access-point profile was not created")

        self.runner.execute(
            [
                "connection",
                "modify",
                "uuid",
                existing.uuid,
                "802-11-wireless.mode",
                "ap",
                "802-11-wireless.band",
                "bg",
                "ipv4.method",
                "shared",
                "ipv4.addresses",
                address,
                "ipv6.method",
                "disabled",
                "connection.autoconnect",
                "no",
                "connection.autoconnect-priority",
                "-999",
            ],
            sensitive=True,
        )
        self._require_secret_store().set_wifi_psk(existing.uuid, passphrase)
        return existing.uuid

    def activate(self, profile_uuid: str, *, timeout: int = 30, sensitive: bool = False) -> None:
        self.runner.execute(
            ["--wait", str(timeout), "connection", "up", "uuid", profile_uuid, "ifname", self.interface],
            timeout=timeout + 5,
            sensitive=sensitive,
        )

    def deactivate(self, profile_uuid: str) -> None:
        self.runner.execute(["--wait", "10", "connection", "down", "uuid", profile_uuid], timeout=15)

    def start_ap(self, ap_uuid: str) -> DeviceSnapshot:
        self.activate(ap_uuid, timeout=30, sensitive=True)
        snapshot = self.device_snapshot()
        if snapshot.connection != AP_PROFILE_NAME or snapshot.ipv4 is None:
            raise NetworkOperationError("ap_start_failed", "Setup access point did not become ready")
        return snapshot

    def create_candidate(self, *, job_id: str, ssid: str, passphrase: str, hidden: bool) -> WifiProfile:
        profile_name = f"{CANDIDATE_PREFIX}{job_id}"
        candidate_uuid = self._require_secret_store().add_wifi_candidate(
            profile_name=profile_name,
            interface=self.interface,
            ssid=ssid,
            passphrase=passphrase,
            hidden=hidden,
        )
        # The UUID is assigned before AddConnection2, so a successful atomic D-Bus
        # call needs no fallible nmcli rediscovery step and rollback always knows the
        # exact profile to delete.
        return WifiProfile(profile_name, candidate_uuid, False, 0)

    def promote_candidate(self, candidate: WifiProfile, *, job_id: str) -> None:
        self.runner.execute(
            [
                "connection",
                "modify",
                "uuid",
                candidate.uuid,
                "connection.id",
                f"{MANAGED_PROFILE_PREFIX}{job_id}",
                "connection.autoconnect",
                "yes",
                "connection.autoconnect-priority",
                "100",
            ],
            sensitive=True,
        )

    def delete_profile(self, profile_uuid: str, *, sensitive: bool = False) -> None:
        self.runner.execute(["connection", "delete", "uuid", profile_uuid], sensitive=sensitive)

    @staticmethod
    def read_interface_suffix(interface: str) -> str:
        path = Path("/sys/class/net") / interface / "address"
        try:
            compact = path.read_text(encoding="ascii").strip().replace(":", "").upper()
        except OSError:
            return "SETUP"
        return compact[-4:] if len(compact) >= 4 else "SETUP"
