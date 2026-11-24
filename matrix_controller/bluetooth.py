"""Bluetooth pairing, device, and audio-source control for the web service.

Web-control-refactor M3. The decisions this module implements:

- D-026: paired sessions may run every operation here. The guards are a pairing
  window that is off by default, opened explicitly, at most 120 s, and one at a
  time; explicit trust; and ``confirm: true`` to forget.
- D-027: BlueZ runs inside the web service. ``BluetoothController`` holds the policy
  over a narrow backend. ``DbusBluetoothBackend`` imports dbus-python and GLib
  lazily and reports itself unavailable instead of failing the service.
- D-028: the page confirms the pairing code. The agent is ``DisplayYesNo`` and is
  registered only while the window is open.
- D-029: the source catalog. Selection stays the ``audio_source`` launch parameter.
- D-030: opaque device IDs, and paired devices only.

Privacy rule (from bluetooth-audio-input): a Bluetooth address never leaves this
module. Responses carry an HMAC-derived ``device_id``. Errors carry fixed messages
plus a short token, never backend text, which can contain a BlueZ object path and
so an address. A passkey appears only in the one pending request.

Locking: agent callbacks arrive on the D-Bus thread while HTTP threads wait on that
thread for BlueZ replies. ``_state_lock`` guards controller state, is held only
briefly, and is never held while waiting on the backend; agent callbacks take only
this lock. ``_operation_lock`` serializes window changes, may be held across backend
calls, and is never taken by agent callbacks. So the two can't deadlock.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import math
import re
import secrets
import threading
import time
import unicodedata
from dataclasses import dataclass, replace
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


WINDOW_SECONDS = 120
MAX_IDENTIFIER_LENGTH = 64
MAX_DEVICE_NAME_LENGTH = 64
UNNAMED_DEVICE = "Unnamed device"
ASOUND_CARDS = Path("/proc/asound/cards")
# Same check as deploy/bluetooth-audio/install.sh: snd-aloop is card 10.
_BRIDGE_CARD_PATTERN = re.compile(r"^ *10 .*Loopback", re.MULTILINE)
_ADDRESS_PATTERN = re.compile(r"(?:[0-9a-f]{2}[:_-]){5}[0-9a-f]{2}", re.IGNORECASE)

_ACTION_FIELDS: dict[str, frozenset[str]] = {
    "window.open": frozenset(),
    "window.close": frozenset(),
    "request.confirm": frozenset({"request_id"}),
    "request.reject": frozenset({"request_id"}),
    "device.trust": frozenset({"device_id"}),
    "device.untrust": frozenset({"device_id"}),
    "device.connect": frozenset({"device_id"}),
    "device.disconnect": frozenset({"device_id"}),
    "device.forget": frozenset({"device_id", "confirm"}),
}
ACTIONS = tuple(_ACTION_FIELDS)


@dataclass(frozen=True)
class BluetoothDevice:
    """One BlueZ device as the backend reports it. Never sent to a client."""

    address: str
    name: str | None
    paired: bool
    trusted: bool
    connected: bool
    streaming: bool = False


class Decision(Protocol):
    """The answer to one agent call. Only the first accept or reject counts."""

    def accept(self) -> None: ...

    def reject(self) -> None: ...


class BluetoothUnavailable(Exception):
    """The backend can't reach BlueZ. ``reason`` is a fixed, safe sentence."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class BluetoothBackendError(Exception):
    """A BlueZ call failed. ``reason`` is a short token such as ``Failed``."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class BluetoothError(Exception):
    """A controller failure with a stable HTTP status and ``code``."""

    def __init__(self, code: str, message: str, *, status: HTTPStatus):
        super().__init__(message)
        self.code = code
        self.status = status

    def as_body(self) -> dict[str, Any]:
        return {"error": str(self), "code": self.code}


class BluetoothBackend(Protocol):
    def bind(self, handlers: "BluetoothController") -> None: ...

    def adapter_state(self) -> dict[str, bool]: ...

    def devices(self) -> list[BluetoothDevice]: ...

    def open_window(self, seconds: int) -> None: ...

    def close_window(self) -> None: ...

    def set_trusted(self, address: str, trusted: bool) -> None: ...

    def connect(self, address: str) -> None: ...

    def disconnect(self, address: str) -> None: ...

    def remove(self, address: str) -> None: ...

    def close(self) -> None: ...


@dataclass
class _PendingRequest:
    request_id: str
    kind: str
    address: str
    name: str
    code: str | None
    decision: Decision


def _invalid(message: str) -> BluetoothError:
    return BluetoothError("invalid_request", message, status=HTTPStatus.BAD_REQUEST)


def _unavailable_error(reason: str) -> BluetoothError:
    return BluetoothError(
        "bluetooth_unavailable",
        f"Bluetooth is unavailable: {reason}",
        status=HTTPStatus.SERVICE_UNAVAILABLE,
    )


def _default_timer(delay: float, callback: Callable[[], None]) -> Any:
    timer = threading.Timer(delay, callback)
    timer.daemon = True
    timer.start()
    return timer


def unavailable_status(reason: str, *, window_seconds: int = WINDOW_SECONDS) -> dict[str, Any]:
    """The status body when Bluetooth can't be used (M3 criterion 8)."""

    return {
        "available": False,
        "reason": reason,
        "adapter": None,
        "window": {"open": False, "seconds_remaining": None, "duration_seconds": window_seconds},
        "pending_request": None,
        "devices": [],
    }


def bluetooth_bridge_present(cards_path: Path = ASOUND_CARDS) -> bool:
    """Report whether the snd-aloop bridge card is loaded, as install.sh checks."""

    try:
        return _BRIDGE_CARD_PATTERN.search(cards_path.read_text(encoding="utf-8", errors="replace")) is not None
    except OSError:
        return False


def audio_source_catalog(
    supervisor_status: Mapping[str, Any] | None,
    *,
    bluetooth: "BluetoothController | None",
    bridge_probe: Callable[[], bool] = bluetooth_bridge_present,
) -> dict[str, Any]:
    """Describe the two public sources and the running FFT's choice (D-029).

    ``usb-mic`` is private to the FFT child (B-007), so its absence is reported by
    launch validation, not here. Bluetooth link state is ``None`` when BlueZ can't
    be reached. Selecting a source is the ``audio_source`` launch parameter.
    """

    active_source = None
    if isinstance(supervisor_status, Mapping) and supervisor_status.get("visualization_id") == "fft":
        parameters = supervisor_status.get("parameters")
        if isinstance(parameters, Mapping):
            active_source = parameters.get("audio_source")
    connected = streaming = None
    if bluetooth is not None:
        connected, streaming = bluetooth.link_state()
    return {
        "sources": [
            {"source_id": "usb-mic", "availability": "checked_at_launch"},
            {
                "source_id": "bluetooth",
                "bridge_present": bridge_probe(),
                "connected": connected,
                "streaming": streaming,
            },
        ],
        "active_source": active_source,
    }


def _same(expected: str, supplied: str) -> bool:
    """Constant-time ID comparison that accepts any client string."""

    return hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))


def _public_name(name: str | None, address: str) -> str:
    """Return a display name that can't carry the device address."""

    if not name:
        return UNNAMED_DEVICE
    cleaned = "".join(
        character for character in name if unicodedata.category(character)[0] != "C"
    ).strip()
    address_hex = re.sub(r"[^0-9a-f]", "", address.lower())
    name_hex = re.sub(r"[^0-9a-f]", "", cleaned.lower())
    if not cleaned or _ADDRESS_PATTERN.search(cleaned) or (address_hex and address_hex in name_hex):
        return UNNAMED_DEVICE
    return cleaned[:MAX_DEVICE_NAME_LENGTH]


class BluetoothController:
    """Pairing window, pending request, and device actions over one backend."""

    def __init__(
        self,
        backend: BluetoothBackend,
        *,
        clock: Callable[[], float] = time.monotonic,
        window_seconds: int = WINDOW_SECONDS,
        timer_factory: Callable[[float, Callable[[], None]], Any] | None = _default_timer,
        id_key: bytes | None = None,
        request_id_factory: Callable[[], str] | None = None,
    ):
        self._backend = backend
        self._clock = clock
        self.window_seconds = window_seconds
        self._timer_factory = timer_factory
        # D-030: a per-process key, so IDs change when the service restarts.
        self._id_key = id_key or secrets.token_bytes(32)
        self._request_id_factory = request_id_factory or (lambda: secrets.token_hex(8))
        self._state_lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._closes_at: float | None = None
        self._pending: _PendingRequest | None = None
        self._timer: Any = None
        backend.bind(self)

    # ----- public operations -------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._operation_lock:
            self._expire_locked()
        try:
            adapter = self._backend.adapter_state()
            devices = self._backend.devices()
        except BluetoothUnavailable as exc:
            self._forget_window_state()
            return unavailable_status(exc.reason, window_seconds=self.window_seconds)
        except BluetoothBackendError as exc:
            return unavailable_status(
                f"BlueZ didn't answer ({exc.reason})", window_seconds=self.window_seconds
            )
        with self._state_lock:
            pending = self._pending
            closes_at = self._closes_at
            now = self._clock()
        public_devices = [self._public_device(device) for device in devices if device.paired]
        listed = {device.address for device in devices if device.paired}
        pending_body = None
        if pending is not None:
            pending_body = {
                "request_id": pending.request_id,
                "kind": pending.kind,
                "device_id": self._device_id(pending.address),
                "name": pending.name,
            }
            if pending.code is not None:
                pending_body["code"] = pending.code
            if pending.address not in listed:
                public_devices.append(
                    {
                        "device_id": self._device_id(pending.address),
                        "name": pending.name,
                        "paired": False,
                        "trusted": False,
                        "connected": False,
                    }
                )
        return {
            "available": True,
            "reason": None,
            "adapter": {
                "powered": bool(adapter.get("powered")),
                "discoverable": bool(adapter.get("discoverable")),
                "pairable": bool(adapter.get("pairable")),
            },
            "window": {
                "open": closes_at is not None,
                "seconds_remaining": (
                    max(0, math.ceil(closes_at - now)) if closes_at is not None else None
                ),
                "duration_seconds": self.window_seconds,
            },
            "pending_request": pending_body,
            "devices": public_devices,
        }

    def perform(self, payload: Any) -> dict[str, Any]:
        """Run one action envelope and return the new status."""

        if not isinstance(payload, Mapping):
            raise _invalid("request body must be a JSON object")
        action = payload.get("action")
        if type(action) is not str or action not in _ACTION_FIELDS:
            raise _invalid(f"action must be one of: {', '.join(ACTIONS)}")
        expected = _ACTION_FIELDS[action] | {"action"}
        if set(payload) != expected:
            fields = ", ".join(sorted(_ACTION_FIELDS[action])) or "no other fields"
            raise _invalid(f"{action} takes {fields}")
        for field in ("request_id", "device_id"):
            if field in payload:
                value = payload[field]
                if type(value) is not str or not value or len(value) > MAX_IDENTIFIER_LENGTH:
                    raise _invalid(f"{field} must be a non-empty string")
        # Criterion 8: every action needs a reachable adapter. Any failure to read it
        # is "unavailable" (503), the same rule status() uses, so a hung bus isn't
        # reported as a 502 BlueZ failure.
        try:
            self._backend.adapter_state()
        except BluetoothUnavailable as exc:
            raise _unavailable_error(exc.reason) from None
        except BluetoothBackendError as exc:
            raise _unavailable_error(f"BlueZ didn't answer ({exc.reason})") from None

        if action == "window.open":
            self.open_window()
        elif action == "window.close":
            self.close_window()
        elif action == "request.confirm":
            self.decide(payload["request_id"], accept=True)
        elif action == "request.reject":
            self.decide(payload["request_id"], accept=False)
        else:
            self.device_action(
                action.removeprefix("device."),
                payload["device_id"],
                confirm=payload.get("confirm"),
            )
        return self.status()

    def open_window(self) -> None:
        """Open the pairing window. Opening it while it's open doesn't extend it."""

        with self._operation_lock:
            self._expire_locked()
            with self._state_lock:
                if self._closes_at is not None:
                    return
                # Open before the backend call, so a request that arrives as soon
                # as the adapter turns discoverable isn't refused.
                self._closes_at = self._clock() + self.window_seconds
            try:
                self._call("window.open", self._backend.open_window, self.window_seconds)
            except BluetoothError:
                # Undo a partly opened window. A request that arrived meanwhile is
                # refused with it (D-028).
                self._forget_window_state()
                self._close_backend_quietly()
                raise
            timer = None
            if self._timer_factory is not None:
                timer = self._timer_factory(self.window_seconds + 0.05, self._timer_expired)
            with self._state_lock:
                still_open = self._closes_at is not None
                if still_open:
                    self._timer = timer
            if not still_open:
                # BlueZ released the agent or went away while the window opened.
                # Agent callbacks can't take the operation lock, so they cleared the
                # window state without it; don't leave the adapter discoverable.
                if timer is not None:
                    timer.cancel()
                self._close_backend_quietly()
                raise BluetoothError(
                    "bluetooth_failed",
                    "Bluetooth window.open was interrupted",
                    status=HTTPStatus.BAD_GATEWAY,
                )

    def close_window(self) -> None:
        """Close the window now. The backend is told even if it looks closed."""

        with self._operation_lock:
            self._close_locked(raise_errors=True)

    def decide(self, request_id: str, *, accept: bool) -> None:
        with self._operation_lock:
            self._expire_locked()
        with self._state_lock:
            pending = self._pending
            if pending is None or not _same(pending.request_id, request_id):
                raise BluetoothError(
                    "stale_request",
                    "this pairing request is no longer pending",
                    status=HTTPStatus.CONFLICT,
                )
            self._pending = None
        if accept:
            pending.decision.accept()
        else:
            pending.decision.reject()

    def device_action(self, operation: str, device_id: str, *, confirm: Any = None) -> None:
        devices = self._call("device lookup", self._backend.devices)
        address = next(
            (
                device.address
                for device in devices
                if device.paired and _same(self._device_id(device.address), device_id)
            ),
            None,
        )
        if address is None:
            raise BluetoothError(
                "device_not_found", "no paired device has this ID", status=HTTPStatus.NOT_FOUND
            )
        if operation == "trust":
            self._call("trust", self._backend.set_trusted, address, True)
        elif operation == "untrust":
            self._call("untrust", self._backend.set_trusted, address, False)
        elif operation == "connect":
            self._call("connect", self._backend.connect, address)
        elif operation == "disconnect":
            self._call("disconnect", self._backend.disconnect, address)
        elif operation == "forget":
            if confirm is not True:
                raise BluetoothError(
                    "confirmation_required",
                    "forgetting a device requires confirm: true",
                    status=HTTPStatus.BAD_REQUEST,
                )
            self._call("forget", self._backend.remove, address)
        else:  # pragma: no cover - code-owned operation names
            raise RuntimeError(f"unknown device operation: {operation}")

    def link_state(self) -> tuple[bool | None, bool | None]:
        """Return (connected, streaming) over paired devices, or Nones if unknown."""

        try:
            devices = self._backend.devices()
        except (BluetoothUnavailable, BluetoothBackendError):
            return None, None
        paired = [device for device in devices if device.paired]
        return (
            any(device.connected for device in paired),
            any(device.streaming for device in paired),
        )

    def shutdown(self) -> None:
        """Close an open window and stop the backend (service exit)."""

        with self._operation_lock:
            with self._state_lock:
                was_open = self._closes_at is not None
            if was_open:
                self._close_locked(raise_errors=False)
        self._backend.close()

    # ----- agent callbacks (backend thread; take only _state_lock) -----------

    def agent_request(
        self,
        kind: str,
        address: str,
        name: str | None,
        passkey: int | None,
        decision: Decision,
    ) -> None:
        """Hold one confirmation or authorization request for the page (D-028)."""

        code = None
        if kind == "confirmation":
            if type(passkey) is not int or not 0 <= passkey <= 999_999:
                decision.reject()
                return
            code = f"{passkey:06d}"
        elif kind != "authorization":
            decision.reject()
            return
        with self._state_lock:
            accepted = (
                self._closes_at is not None
                and self._clock() < self._closes_at
                and self._pending is None
            )
            if accepted:
                self._pending = _PendingRequest(
                    request_id=self._request_id_factory(),
                    kind=kind,
                    address=address,
                    name=_public_name(name, address),
                    code=code,
                    decision=decision,
                )
        if not accepted:
            decision.reject()

    def agent_authorize_service(self, trusted: bool, decision: Decision) -> None:
        """Allow a profile connection only from a trusted device (B-006)."""

        if trusted is True:
            decision.accept()
        else:
            decision.reject()

    def agent_cancelled(self) -> None:
        """BlueZ gave up on the pending request; its ID is now stale."""

        with self._state_lock:
            self._pending = None

    def agent_released(self) -> None:
        """BlueZ dropped the agent (for example, bluetoothd restarted)."""

        self._forget_window_state()

    # ----- internals ---------------------------------------------------------

    def _timer_expired(self) -> None:
        with self._operation_lock:
            self._expire_locked()

    def _expire_locked(self) -> None:
        with self._state_lock:
            if self._closes_at is None or self._clock() < self._closes_at:
                return
        self._close_locked(raise_errors=False)

    def _close_locked(self, *, raise_errors: bool) -> None:
        pending = self._take_window_state()
        if pending is not None:
            pending.decision.reject()
        try:
            self._call("window.close", self._backend.close_window)
        except BluetoothError:
            if raise_errors:
                raise

    def _take_window_state(self) -> _PendingRequest | None:
        with self._state_lock:
            pending, self._pending = self._pending, None
            self._closes_at = None
            timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
        return pending

    def _forget_window_state(self) -> None:
        pending = self._take_window_state()
        if pending is not None:
            pending.decision.reject()

    def _close_backend_quietly(self) -> None:
        try:
            self._backend.close_window()
        except (BluetoothUnavailable, BluetoothBackendError):
            pass

    def _call(self, operation: str, function: Callable[..., Any], *args: Any) -> Any:
        try:
            return function(*args)
        except BluetoothUnavailable as exc:
            raise _unavailable_error(exc.reason) from None
        except BluetoothBackendError as exc:
            raise BluetoothError(
                "bluetooth_failed",
                f"Bluetooth {operation} failed ({exc.reason})",
                status=HTTPStatus.BAD_GATEWAY,
            ) from None

    def _device_id(self, address: str) -> str:
        digest = hmac.new(self._id_key, address.upper().encode("utf-8"), hashlib.sha256)
        return digest.hexdigest()[:16]

    def _public_device(self, device: BluetoothDevice) -> dict[str, Any]:
        return {
            "device_id": self._device_id(device.address),
            "name": _public_name(device.name, device.address),
            "paired": device.paired,
            "trusted": device.trusted,
            "connected": device.connected,
        }


# ----- fake backend ----------------------------------------------------------


class FakeDecision:
    """Records the first answer to one simulated agent call."""

    def __init__(self) -> None:
        self.outcome: str | None = None

    def accept(self) -> None:
        if self.outcome is None:
            self.outcome = "accepted"

    def reject(self) -> None:
        if self.outcome is None:
            self.outcome = "rejected"


class FakeBluetoothBackend:
    """An in-memory BlueZ for tests and for page work without hardware."""

    def __init__(self, devices: list[BluetoothDevice] | None = None):
        self.devices_by_address = {device.address: device for device in devices or []}
        self.adapter = {"powered": True, "discoverable": False, "pairable": False}
        self.saved_pairable: bool | None = None
        self.agent_registered = False
        self.unavailable_reason: str | None = None
        self.fail_with: str | None = None
        self.calls: list[tuple[Any, ...]] = []
        self.handlers: BluetoothController | None = None
        self.closed = False

    def bind(self, handlers: BluetoothController) -> None:
        self.handlers = handlers

    def _check(self, *call: Any) -> None:
        if self.unavailable_reason is not None:
            raise BluetoothUnavailable(self.unavailable_reason)
        if call:
            self.calls.append(call)
            if self.fail_with is not None:
                raise BluetoothBackendError(self.fail_with)

    def adapter_state(self) -> dict[str, bool]:
        self._check()
        return dict(self.adapter)

    def devices(self) -> list[BluetoothDevice]:
        self._check()
        return list(self.devices_by_address.values())

    def open_window(self, seconds: int) -> None:
        self._check("open_window", seconds)
        if self.saved_pairable is None:
            self.saved_pairable = self.adapter["pairable"]
        self.adapter.update(powered=True, discoverable=True, pairable=True)
        self.agent_registered = True

    def close_window(self) -> None:
        """Like the real backend, leave Pairable as the window found it."""

        self._check("close_window")
        self.adapter["discoverable"] = False
        if self.saved_pairable is not None:
            self.adapter["pairable"], self.saved_pairable = self.saved_pairable, None
        self.agent_registered = False

    def _replace(self, address: str, **changes: Any) -> None:
        self.devices_by_address[address] = replace(self.devices_by_address[address], **changes)

    def set_trusted(self, address: str, trusted: bool) -> None:
        self._check("set_trusted", address, trusted)
        self._replace(address, trusted=trusted)

    def connect(self, address: str) -> None:
        self._check("connect", address)
        self._replace(address, connected=True)

    def disconnect(self, address: str) -> None:
        self._check("disconnect", address)
        self._replace(address, connected=False, streaming=False)

    def remove(self, address: str) -> None:
        self._check("remove", address)
        del self.devices_by_address[address]

    def close(self) -> None:
        self.closed = True

    # Simulated BlueZ agent calls.
    def request_confirmation(self, address: str, name: str | None, passkey: int) -> FakeDecision:
        decision = FakeDecision()
        assert self.handlers is not None
        if not self.agent_registered:
            decision.reject()  # BlueZ wouldn't reach an unregistered agent
            return decision
        self.handlers.agent_request("confirmation", address, name, passkey, decision)
        return decision

    def request_authorization(self, address: str, name: str | None) -> FakeDecision:
        decision = FakeDecision()
        assert self.handlers is not None
        if not self.agent_registered:
            decision.reject()
            return decision
        self.handlers.agent_request("authorization", address, name, None, decision)
        return decision

    def authorize_service(self, address: str) -> FakeDecision:
        decision = FakeDecision()
        assert self.handlers is not None
        self.handlers.agent_authorize_service(self.devices_by_address[address].trusted, decision)
        return decision


# ----- BlueZ over dbus-python ------------------------------------------------

_BLUEZ = "org.bluez"
_OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
_PROPERTIES = "org.freedesktop.DBus.Properties"
_ADAPTER = "org.bluez.Adapter1"
_DEVICE = "org.bluez.Device1"
_TRANSPORT = "org.bluez.MediaTransport1"
_AGENT = "org.bluez.Agent1"
_AGENT_MANAGER = "org.bluez.AgentManager1"
_SAFE_TOKEN = re.compile(r"[A-Za-z]{1,40}")


class DbusBluetoothBackend:
    """BlueZ over dbus-python, with a GLib main loop on one worker thread (D-027).

    Every D-Bus call runs on the loop thread as an asynchronous call with a timeout;
    callers wait on a future. The agent is exported on the same thread. Nothing is
    imported until the first call, and any import, bus, or adapter failure becomes
    ``BluetoothUnavailable``. The next call retries.

    No local test talks to BlueZ (M3 criterion 11); a stand-in proxy checks the call
    shapes. The M3 Pi check (``b28e31d``) showed that the web unit's user reaches
    ``org.bluez``, that the window opens and closes with the agent registered as the
    default, and that close leaves the adapter as found. Pairing a real phone and
    streaming are M6.
    """

    AGENT_PATH = "/org/rgbmatrix/bluetooth_agent"

    def __init__(self, *, call_timeout: float = 5.0, connect_timeout: float = 20.0):
        self._call_timeout = call_timeout
        self._connect_timeout = connect_timeout
        self._setup_lock = threading.Lock()
        self._handlers: BluetoothController | None = None
        self._dbus: Any = None
        self._glib: Any = None
        self._bus: Any = None
        self._loop: Any = None
        self._agent: Any = None
        self._rejected: Any = None
        # Adapter settings open_window changed; close_window restores them.
        self._saved_adapter: dict[str, Any] | None = None

    def bind(self, handlers: BluetoothController) -> None:
        self._handlers = handlers

    # ----- setup and call plumbing -----

    def _ensure_started(self) -> None:
        with self._setup_lock:
            if self._bus is not None:
                return
            try:
                import dbus  # type: ignore[import-not-found]
                import dbus.mainloop.glib  # type: ignore[import-not-found]
                import dbus.service  # type: ignore[import-not-found]
            except ImportError:
                raise BluetoothUnavailable("dbus-python is not installed") from None
            try:
                from gi.repository import GLib  # type: ignore[import-not-found]
            except ImportError:
                raise BluetoothUnavailable("the GLib bindings (python3-gi) are not installed") from None
            try:
                dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
                bus = dbus.SystemBus()
            except Exception:
                raise BluetoothUnavailable("the system D-Bus is unreachable") from None
            loop = GLib.MainLoop()
            threading.Thread(target=loop.run, name="bluetooth-dbus", daemon=True).start()
            self._dbus, self._glib, self._bus, self._loop = dbus, GLib, bus, loop

    def _safe_error(self, exc: BaseException) -> Exception:
        if isinstance(exc, (BluetoothUnavailable, BluetoothBackendError)):
            return exc
        if self._dbus is not None and isinstance(exc, self._dbus.DBusException):
            name = str(exc.get_dbus_name() or "")
            if name in {
                "org.freedesktop.DBus.Error.ServiceUnknown",
                "org.freedesktop.DBus.Error.NameHasNoOwner",
            }:
                return BluetoothUnavailable("BlueZ is not running")
            if name in {"org.freedesktop.DBus.Error.AccessDenied"}:
                return BluetoothUnavailable("the service user may not use BlueZ")
            if name in {"org.freedesktop.DBus.Error.NoReply", "org.freedesktop.DBus.Error.Timeout"}:
                return BluetoothBackendError("timeout")
            if name.startswith("org.bluez.Error."):
                token = name.rsplit(".", 1)[-1]
                return BluetoothBackendError(token if _SAFE_TOKEN.fullmatch(token) else "Failed")
            return BluetoothBackendError("dbus_error")
        return BluetoothBackendError("internal_error")

    def _submit(self, start: Callable[[Callable[..., None], Callable[[BaseException], None]], None], timeout: float) -> Any:
        """Run ``start(ok, error)`` on the loop thread and wait for its result."""

        self._ensure_started()
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()

        def ok(*values: Any) -> None:
            try:
                future.set_result(values[0] if values else None)
            except concurrent.futures.InvalidStateError:
                pass

        def error(exc: BaseException) -> None:
            try:
                future.set_exception(self._safe_error(exc))
            except concurrent.futures.InvalidStateError:
                pass

        def run() -> bool:
            try:
                start(ok, error)
            except Exception as exc:  # keep tracebacks (paths, addresses) out of logs
                error(exc)
            return False

        self._glib.idle_add(run)
        try:
            return future.result(timeout=timeout + 1.0)
        except concurrent.futures.TimeoutError:
            raise BluetoothBackendError("timeout") from None

    def _interface(self, path: str, interface: str) -> Any:
        return self._dbus.Interface(
            self._bus.get_object(_BLUEZ, path, introspect=False), interface
        )

    def _async(
        self,
        path: str,
        interface: str,
        method: str,
        *args: Any,
        timeout: float | None = None,
        signature: str | None = None,
    ) -> Any:
        """Call a method on the loop thread and wait for its reply.

        Proxies skip introspection, so dbus-python guesses the signature from the
        argument types unless ``signature`` is given.
        """

        timeout = self._call_timeout if timeout is None else timeout
        extra = {} if signature is None else {"signature": signature}

        def start(ok: Callable[..., None], error: Callable[[BaseException], None]) -> None:
            getattr(self._interface(path, interface), method)(
                *args, reply_handler=ok, error_handler=error, timeout=timeout, **extra
            )

        return self._submit(start, timeout)

    def _objects(self) -> dict[str, Any]:
        objects = self._async("/", _OBJECT_MANAGER, "GetManagedObjects")
        return {str(path): interfaces for path, interfaces in objects.items()}

    @staticmethod
    def _adapter(objects: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
        for path in sorted(objects):
            if _ADAPTER in objects[path]:
                return path, objects[path][_ADAPTER]
        raise BluetoothUnavailable("no Bluetooth adapter was found")

    def _device_path(self, address: str) -> str:
        objects = self._objects()
        adapter_path, _ = self._adapter(objects)
        for path, interfaces in objects.items():
            properties = interfaces.get(_DEVICE)
            if (
                properties is not None
                and str(properties.get("Adapter", "")) == adapter_path
                and str(properties.get("Address", "")).upper() == address.upper()
            ):
                return path
        raise BluetoothBackendError("DoesNotExist")

    def _set(self, path: str, interface: str, name: str, value: Any) -> None:
        # Properties.Set is "ssv". A guessed signature ("ssb", "ssu") makes BlueZ
        # answer UnknownMethod.
        self._async(path, _PROPERTIES, "Set", interface, name, value, signature="ssv")

    # ----- backend interface -----

    def adapter_state(self) -> dict[str, bool]:
        _, properties = self._adapter(self._objects())
        return {
            "powered": bool(properties.get("Powered", False)),
            "discoverable": bool(properties.get("Discoverable", False)),
            "pairable": bool(properties.get("Pairable", False)),
        }

    def devices(self) -> list[BluetoothDevice]:
        objects = self._objects()
        adapter_path, _ = self._adapter(objects)
        streaming = {
            str(interfaces[_TRANSPORT].get("Device", ""))
            for interfaces in objects.values()
            if _TRANSPORT in interfaces and str(interfaces[_TRANSPORT].get("State", "")) == "active"
        }
        devices = []
        for path, interfaces in sorted(objects.items()):
            properties = interfaces.get(_DEVICE)
            if properties is None or str(properties.get("Adapter", "")) != adapter_path:
                continue
            devices.append(
                BluetoothDevice(
                    address=str(properties.get("Address", "")),
                    name=str(properties["Name"]) if "Name" in properties else None,
                    paired=bool(properties.get("Paired", False)),
                    trusted=bool(properties.get("Trusted", False)),
                    connected=bool(properties.get("Connected", False)),
                    streaming=path in streaming,
                )
            )
        return devices

    def open_window(self, seconds: int) -> None:
        dbus = self._dbus_module()
        adapter_path, properties = self._adapter(self._objects())
        if self._saved_adapter is None:
            # BlueZ stores the timeouts in its adapter settings, and the desktop panel
            # shares this adapter, so close_window puts these back as found. Timeouts
            # come first, so restoring Pairable uses the restored PairableTimeout.
            saved: dict[str, Any] = {}
            for name in ("DiscoverableTimeout", "PairableTimeout"):
                if name in properties:
                    saved[name] = dbus.UInt32(int(properties[name]))
            if "Pairable" in properties:
                saved["Pairable"] = dbus.Boolean(bool(properties["Pairable"]))
            self._saved_adapter = saved
        self._set(adapter_path, _ADAPTER, "Powered", dbus.Boolean(True))
        # The agent goes first, so no request arrives before it can answer.
        self._register_agent()
        self._set(adapter_path, _ADAPTER, "PairableTimeout", dbus.UInt32(seconds))
        self._set(adapter_path, _ADAPTER, "DiscoverableTimeout", dbus.UInt32(seconds))
        self._set(adapter_path, _ADAPTER, "Pairable", dbus.Boolean(True))
        self._set(adapter_path, _ADAPTER, "Discoverable", dbus.Boolean(True))

    def close_window(self) -> None:
        """Stop discoverability, restore what open_window changed, drop the agent."""

        dbus = self._dbus_module()
        adapter_path, _ = self._adapter(self._objects())
        writes: list[tuple[str, Any]] = [("Discoverable", dbus.Boolean(False))]
        if self._saved_adapter is not None:
            writes.extend(self._saved_adapter.items())
        first_error: Exception | None = None
        for name, value in writes:
            try:
                self._set(adapter_path, _ADAPTER, name, value)
            except BluetoothBackendError as exc:
                first_error = first_error or exc
        if first_error is None:
            # After a failed write the snapshot stays, so the next close retries it.
            self._saved_adapter = None
        try:
            self._async("/org/bluez", _AGENT_MANAGER, "UnregisterAgent", dbus.ObjectPath(self.AGENT_PATH))
        except BluetoothBackendError as exc:
            if exc.reason != "DoesNotExist":
                first_error = first_error or exc
        if first_error is not None:
            raise first_error

    def set_trusted(self, address: str, trusted: bool) -> None:
        self._set(self._device_path(address), _DEVICE, "Trusted", self._dbus_module().Boolean(trusted))

    def connect(self, address: str) -> None:
        self._async(self._device_path(address), _DEVICE, "Connect", timeout=self._connect_timeout)

    def disconnect(self, address: str) -> None:
        self._async(self._device_path(address), _DEVICE, "Disconnect")

    def remove(self, address: str) -> None:
        path = self._device_path(address)
        adapter_path, _ = self._adapter(self._objects())
        self._async(adapter_path, _ADAPTER, "RemoveDevice", self._dbus_module().ObjectPath(path))

    def close(self) -> None:
        if self._loop is not None:
            self._glib.idle_add(self._loop.quit)

    def _dbus_module(self) -> Any:
        self._ensure_started()
        return self._dbus

    # ----- agent -----

    def _register_agent(self) -> None:
        dbus = self._dbus_module()

        def start(ok: Callable[..., None], error: Callable[[BaseException], None]) -> None:
            if self._agent is None:
                agent_class, self._rejected = _agent_classes(dbus, self)
                self._agent = agent_class(self._bus, self.AGENT_PATH)
            manager = self._interface("/org/bluez", _AGENT_MANAGER)
            path = dbus.ObjectPath(self.AGENT_PATH)

            def make_default(*_ignored: Any) -> None:
                manager.RequestDefaultAgent(
                    path, reply_handler=ok, error_handler=error, timeout=self._call_timeout
                )

            def registered_error(exc: BaseException) -> None:
                if isinstance(exc, dbus.DBusException) and exc.get_dbus_name() == "org.bluez.Error.AlreadyExists":
                    make_default()
                else:
                    error(exc)

            manager.RegisterAgent(
                path,
                "DisplayYesNo",
                reply_handler=make_default,
                error_handler=registered_error,
                timeout=self._call_timeout,
            )

        self._submit(start, 2 * self._call_timeout)

    def _device_properties(self, device_path: Any) -> Mapping[str, Any]:
        """Read a device's properties from inside an agent call (loop thread)."""

        return self._interface(str(device_path), _PROPERTIES).GetAll(
            _DEVICE, timeout=self._call_timeout
        )

    def _decision(self, reply: Callable[..., None], error: Callable[[BaseException], None]) -> "_DbusDecision":
        return _DbusDecision(self._glib, reply, error, self._rejected)

    def _on_request(self, kind: str, device_path: Any, passkey: int | None, decision: "_DbusDecision") -> None:
        handlers = self._handlers
        try:
            properties = self._device_properties(device_path)
            address = str(properties.get("Address", ""))
            name = str(properties["Name"]) if "Name" in properties else None
        except Exception:
            decision.reject()
            return
        if handlers is None or not address:
            decision.reject()
            return
        handlers.agent_request(kind, address, name, passkey, decision)

    def _on_authorize_service(self, device_path: Any, decision: "_DbusDecision") -> None:
        handlers = self._handlers
        try:
            trusted = bool(self._device_properties(device_path).get("Trusted", False))
        except Exception:
            decision.reject()
            return
        if handlers is None:
            decision.reject()
            return
        handlers.agent_authorize_service(trusted, decision)


class _DbusDecision:
    """Answers one asynchronous agent call on the loop thread, once."""

    def __init__(self, glib: Any, reply: Callable[..., None], error: Callable[[BaseException], None], rejected: Any):
        self._glib = glib
        self._reply = reply
        self._error = error
        self._rejected = rejected
        self._lock = threading.Lock()
        self._settled = False

    def _settle(self, answer: Callable[[], None]) -> None:
        with self._lock:
            if self._settled:
                return
            self._settled = True

        def run() -> bool:
            try:
                answer()
            except Exception:
                pass
            return False

        self._glib.idle_add(run)

    def accept(self) -> None:
        self._settle(self._reply)

    def reject(self) -> None:
        self._settle(lambda: self._error(self._rejected("rejected")))


def _agent_classes(dbus: Any, backend: DbusBluetoothBackend) -> tuple[type, type]:
    """Build the ``org.bluez.Agent1`` object once dbus-python is importable."""

    class Rejected(dbus.DBusException):
        _dbus_error_name = "org.bluez.Error.Rejected"
        include_traceback = False

    class Agent(dbus.service.Object):
        @dbus.service.method(_AGENT, in_signature="", out_signature="")
        def Release(self) -> None:  # noqa: N802 - D-Bus method name
            if backend._handlers is not None:
                backend._handlers.agent_released()

        @dbus.service.method(
            _AGENT, in_signature="ou", out_signature="", async_callbacks=("reply", "error")
        )
        def RequestConfirmation(self, device, passkey, reply, error) -> None:  # noqa: N802
            backend._on_request("confirmation", device, int(passkey), backend._decision(reply, error))

        @dbus.service.method(
            _AGENT, in_signature="o", out_signature="", async_callbacks=("reply", "error")
        )
        def RequestAuthorization(self, device, reply, error) -> None:  # noqa: N802
            backend._on_request("authorization", device, None, backend._decision(reply, error))

        @dbus.service.method(
            _AGENT, in_signature="os", out_signature="", async_callbacks=("reply", "error")
        )
        def AuthorizeService(self, device, uuid, reply, error) -> None:  # noqa: N802
            backend._on_authorize_service(device, backend._decision(reply, error))

        @dbus.service.method(_AGENT, in_signature="o", out_signature="s")
        def RequestPinCode(self, device) -> str:  # noqa: N802
            raise Rejected("PIN entry is not supported")

        @dbus.service.method(_AGENT, in_signature="o", out_signature="u")
        def RequestPasskey(self, device) -> int:  # noqa: N802
            raise Rejected("passkey entry is not supported")

        @dbus.service.method(_AGENT, in_signature="os", out_signature="")
        def DisplayPinCode(self, device, pincode) -> None:  # noqa: N802
            raise Rejected("legacy PIN pairing is not supported")

        @dbus.service.method(_AGENT, in_signature="ouq", out_signature="")
        def DisplayPasskey(self, device, passkey, entered) -> None:  # noqa: N802
            return None

        @dbus.service.method(_AGENT, in_signature="", out_signature="")
        def Cancel(self) -> None:  # noqa: N802
            if backend._handlers is not None:
                backend._handlers.agent_cancelled()

    return Agent, Rejected
