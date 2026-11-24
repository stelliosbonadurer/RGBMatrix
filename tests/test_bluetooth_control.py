"""Bluetooth controller tests with a fake backend and clock (M3 criteria 5-8).

No local test talks to BlueZ (criterion 11). The real D-Bus backend's
"unavailable" paths are exercised with stand-in modules for dbus-python and GLib,
and its call shapes with a stand-in proxy.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import types
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest import mock

from matrix_controller.bluetooth import (
    UNNAMED_DEVICE,
    WINDOW_SECONDS,
    BluetoothBackendError,
    BluetoothController,
    BluetoothDevice,
    BluetoothError,
    DbusBluetoothBackend,
    FakeBluetoothBackend,
    FakeDecision,
    audio_source_catalog,
    bluetooth_bridge_present,
)


PHONE = BluetoothDevice("AA:BB:CC:DD:EE:01", "Pixel", paired=True, trusted=False, connected=False)
SPEAKER = BluetoothDevice(
    "AA:BB:CC:DD:EE:02", "Tablet", paired=True, trusted=True, connected=True, streaming=True
)
NEARBY = BluetoothDevice("AA:BB:CC:DD:EE:03", "Nearby", paired=False, trusted=False, connected=False)
NEW_PHONE = "AA:BB:CC:DD:EE:04"
ADDRESS_FORMS = ("AA:BB:CC:DD:EE", "AA_BB_CC_DD_EE", "aa:bb:cc:dd:ee", "AABBCCDDEE", "aabbccddee")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeTimer:
    def __init__(self, delay, callback) -> None:
        self.delay = delay
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class ControllerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeBluetoothBackend([PHONE, SPEAKER, NEARBY])
        self.clock = FakeClock()
        self.timers: list[FakeTimer] = []
        self.request_ids = iter(f"request-{index}" for index in range(1, 100))
        self.controller = BluetoothController(
            self.backend,
            clock=self.clock,
            timer_factory=self._timer,
            request_id_factory=lambda: next(self.request_ids),
        )

    def _timer(self, delay, callback) -> FakeTimer:
        timer = FakeTimer(delay, callback)
        self.timers.append(timer)
        return timer

    def act(self, **payload) -> dict:
        return self.controller.perform(payload)

    def assertBluetoothError(self, code: str, status: HTTPStatus, call, *args, **kwargs) -> BluetoothError:
        with self.assertRaises(BluetoothError) as caught:
            call(*args, **kwargs)
        self.assertEqual((caught.exception.code, caught.exception.status), (code, status))
        return caught.exception

    def device_id(self, name: str) -> str:
        status = self.controller.status()
        return next(device["device_id"] for device in status["devices"] if device["name"] == name)

    def assertNoAddress(self, value) -> None:
        text = json.dumps(value) if not isinstance(value, str) else value
        for form in ADDRESS_FORMS:
            self.assertNotIn(form, text)


class WindowTests(ControllerTestCase):
    def test_window_is_closed_by_default(self) -> None:
        status = self.controller.status()
        self.assertTrue(status["available"])
        self.assertEqual(
            status["window"], {"open": False, "seconds_remaining": None, "duration_seconds": 120}
        )
        self.assertFalse(status["adapter"]["discoverable"])
        self.assertFalse(self.backend.agent_registered)

    def test_open_makes_the_adapter_discoverable_and_registers_the_agent(self) -> None:
        status = self.act(action="window.open")
        self.assertEqual(status["window"]["open"], True)
        self.assertEqual(status["window"]["seconds_remaining"], WINDOW_SECONDS)
        self.assertEqual(
            status["adapter"], {"powered": True, "discoverable": True, "pairable": True}
        )
        self.assertTrue(self.backend.agent_registered)
        self.assertEqual(self.backend.calls, [("open_window", 120)])
        self.assertEqual(len(self.timers), 1)
        self.assertAlmostEqual(self.timers[0].delay, 120.05)

    def test_opening_again_does_not_extend_the_window(self) -> None:
        self.act(action="window.open")
        self.clock.now += 50
        status = self.act(action="window.open")
        self.assertEqual(status["window"]["seconds_remaining"], 70)
        self.assertEqual(self.backend.calls, [("open_window", 120)])
        self.assertEqual(len(self.timers), 1)

    def test_the_window_closes_at_120_seconds(self) -> None:
        self.act(action="window.open")
        self.clock.now += 119.5
        self.assertTrue(self.controller.status()["window"]["open"])
        self.clock.now += 0.5
        status = self.controller.status()
        self.assertFalse(status["window"]["open"])
        self.assertFalse(status["adapter"]["discoverable"])
        self.assertFalse(self.backend.agent_registered)
        self.assertEqual(self.backend.calls[-1], ("close_window",))
        self.assertTrue(self.timers[0].cancelled)

    def test_the_timer_closes_the_window_without_a_request(self) -> None:
        self.act(action="window.open")
        self.clock.now += WINDOW_SECONDS + 0.05
        self.timers[0].callback()
        self.assertFalse(self.backend.adapter["discoverable"])
        self.assertFalse(self.backend.agent_registered)
        self.assertEqual(self.backend.calls.count(("close_window",)), 1)
        # A timer that fires early (clock behind) leaves the window open.
        self.act(action="window.open")
        self.timers[-1].callback()
        self.assertTrue(self.backend.agent_registered)

    def test_close_on_request(self) -> None:
        self.act(action="window.open")
        status = self.act(action="window.close")
        self.assertFalse(status["window"]["open"])
        self.assertFalse(self.backend.agent_registered)
        # Closing while closed still tells the backend, so the page is authoritative.
        self.act(action="window.close")
        self.assertEqual(self.backend.calls.count(("close_window",)), 2)

    def test_a_failed_open_leaves_the_window_closed(self) -> None:
        self.backend.fail_with = "NotReady"
        error = self.assertBluetoothError(
            "bluetooth_failed", HTTPStatus.BAD_GATEWAY, self.controller.open_window
        )
        self.assertEqual(str(error), "Bluetooth window.open failed (NotReady)")
        self.backend.fail_with = None
        self.assertFalse(self.controller.status()["window"]["open"])
        self.assertEqual(self.timers, [])

    def test_shutdown_closes_an_open_window(self) -> None:
        self.act(action="window.open")
        self.controller.shutdown()
        self.assertFalse(self.backend.agent_registered)
        self.assertTrue(self.backend.closed)

    def test_a_request_that_arrived_during_a_failed_open_is_refused(self) -> None:
        original_open = self.backend.open_window
        arrived: list[FakeDecision] = []

        def open_then_fail(seconds: int) -> None:
            original_open(seconds)  # the agent is registered by now
            arrived.append(self.backend.request_confirmation(NEW_PHONE, "Early", 7))
            raise BluetoothBackendError("Failed")

        self.backend.open_window = open_then_fail
        self.assertBluetoothError(
            "bluetooth_failed", HTTPStatus.BAD_GATEWAY, self.controller.open_window
        )
        self.assertEqual(arrived[0].outcome, "rejected")
        status = self.controller.status()
        self.assertIsNone(status["pending_request"])
        self.assertFalse(status["window"]["open"])
        self.assertFalse(self.backend.agent_registered)
        self.assertBluetoothError(
            "stale_request",
            HTTPStatus.CONFLICT,
            self.controller.decide,
            "request-1",
            accept=True,
        )

    def test_a_window_dropped_while_opening_is_closed_and_reported(self) -> None:
        original_open = self.backend.open_window

        def open_then_release(seconds: int) -> None:
            original_open(seconds)
            # bluetoothd restarted: BlueZ's thread releases the agent mid-open.
            self.controller.agent_released()

        self.backend.open_window = open_then_release
        error = self.assertBluetoothError(
            "bluetooth_failed", HTTPStatus.BAD_GATEWAY, self.controller.open_window
        )
        self.assertEqual(str(error), "Bluetooth window.open was interrupted")
        self.assertFalse(self.controller.status()["window"]["open"])
        self.assertFalse(self.backend.adapter["discoverable"])
        self.assertFalse(self.backend.agent_registered)
        self.assertEqual(len(self.timers), 1)
        self.assertTrue(self.timers[0].cancelled)

    def test_close_leaves_pairable_as_the_window_found_it(self) -> None:
        for before in (True, False):
            with self.subTest(pairable=before):
                self.backend.adapter["pairable"] = before
                self.assertTrue(self.act(action="window.open")["adapter"]["pairable"])
                closed = self.act(action="window.close")
                self.assertEqual(closed["adapter"]["pairable"], before)
                self.assertFalse(closed["adapter"]["discoverable"])


class PairingRequestTests(ControllerTestCase):
    def test_a_request_outside_the_window_is_rejected(self) -> None:
        decision = FakeDecision()
        self.controller.agent_request("confirmation", NEW_PHONE, "New", 123456, decision)
        self.assertEqual(decision.outcome, "rejected")
        self.assertIsNone(self.controller.status()["pending_request"])
        # Also after expiry, even if BlueZ still reached the agent.
        self.act(action="window.open")
        self.clock.now += WINDOW_SECONDS
        late = FakeDecision()
        self.controller.agent_request("confirmation", NEW_PHONE, "New", 123456, late)
        self.assertEqual(late.outcome, "rejected")

    def test_confirm_accepts_the_shown_code(self) -> None:
        self.act(action="window.open")
        decision = self.backend.request_confirmation(NEW_PHONE, "New phone", 12345)
        self.assertIsNone(decision.outcome)
        status = self.controller.status()
        pending = status["pending_request"]
        self.assertEqual(
            {key: pending[key] for key in ("request_id", "kind", "name", "code")},
            {"request_id": "request-1", "kind": "confirmation", "name": "New phone", "code": "012345"},
        )
        listed = {device["device_id"]: device for device in status["devices"]}
        self.assertEqual(listed[pending["device_id"]]["paired"], False)
        status = self.act(action="request.confirm", request_id="request-1")
        self.assertEqual(decision.outcome, "accepted")
        self.assertIsNone(status["pending_request"])
        self.assertTrue(status["window"]["open"])

    def test_reject_refuses_the_request(self) -> None:
        self.act(action="window.open")
        decision = self.backend.request_confirmation(NEW_PHONE, "New phone", 1)
        self.act(action="request.reject", request_id="request-1")
        self.assertEqual(decision.outcome, "rejected")

    def test_closing_the_window_refuses_the_pending_request(self) -> None:
        for close in ("explicit", "expiry"):
            with self.subTest(close):
                self.act(action="window.open")
                decision = self.backend.request_confirmation(NEW_PHONE, "New", 1)
                if close == "explicit":
                    self.act(action="window.close")
                else:
                    self.clock.now += WINDOW_SECONDS
                    self.controller.status()
                self.assertEqual(decision.outcome, "rejected")
                self.assertIsNone(self.controller.status()["pending_request"])

    def test_only_one_request_can_be_pending(self) -> None:
        self.act(action="window.open")
        first = self.backend.request_confirmation(NEW_PHONE, "First", 111111)
        second = self.backend.request_confirmation("AA:BB:CC:DD:EE:05", "Second", 222222)
        self.assertIsNone(first.outcome)
        self.assertEqual(second.outcome, "rejected")
        self.assertEqual(self.controller.status()["pending_request"]["name"], "First")

    def test_stale_request_ids_conflict(self) -> None:
        self.act(action="window.open")
        self.assertBluetoothError(
            "stale_request", HTTPStatus.CONFLICT, self.act, action="request.confirm", request_id="request-1"
        )
        self.backend.request_confirmation(NEW_PHONE, "New", 1)
        for request_id in ("request-9", "réquest-1"):
            with self.subTest(request_id=request_id):
                self.assertBluetoothError(
                    "stale_request",
                    HTTPStatus.CONFLICT,
                    self.act,
                    action="request.confirm",
                    request_id=request_id,
                )
        self.act(action="request.confirm", request_id="request-1")
        self.assertBluetoothError(
            "stale_request", HTTPStatus.CONFLICT, self.act, action="request.reject", request_id="request-1"
        )

    def test_a_cancelled_request_becomes_stale(self) -> None:
        self.act(action="window.open")
        self.backend.request_confirmation(NEW_PHONE, "New", 1)
        self.controller.agent_cancelled()
        self.assertIsNone(self.controller.status()["pending_request"])
        self.assertBluetoothError(
            "stale_request", HTTPStatus.CONFLICT, self.act, action="request.confirm", request_id="request-1"
        )

    def test_authorization_requests_have_no_code(self) -> None:
        self.act(action="window.open")
        decision = self.backend.request_authorization(NEW_PHONE, "Headset")
        pending = self.controller.status()["pending_request"]
        self.assertEqual(pending["kind"], "authorization")
        self.assertNotIn("code", pending)
        self.act(action="request.confirm", request_id=pending["request_id"])
        self.assertEqual(decision.outcome, "accepted")

    def test_unsupported_request_kinds_and_bad_passkeys_are_rejected(self) -> None:
        self.act(action="window.open")
        for kind, passkey in (("pin", None), ("confirmation", None), ("confirmation", 1_000_000)):
            with self.subTest(kind=kind, passkey=passkey):
                decision = FakeDecision()
                self.controller.agent_request(kind, NEW_PHONE, "x", passkey, decision)
                self.assertEqual(decision.outcome, "rejected")
        self.assertIsNone(self.controller.status()["pending_request"])

    def test_profile_connections_need_trust(self) -> None:
        self.assertEqual(self.backend.authorize_service(PHONE.address).outcome, "rejected")
        self.assertEqual(self.backend.authorize_service(SPEAKER.address).outcome, "accepted")

    def test_released_agent_drops_the_window(self) -> None:
        self.act(action="window.open")
        decision = self.backend.request_confirmation(NEW_PHONE, "New", 1)
        self.controller.agent_released()
        self.assertEqual(decision.outcome, "rejected")
        self.assertFalse(self.controller.status()["window"]["open"])

    def test_an_agent_call_during_a_slow_open_neither_deadlocks_nor_is_refused(self) -> None:
        controller = self.controller
        outcome: list[FakeDecision] = []
        original_open = self.backend.open_window

        def open_window_with_request(seconds: int) -> None:
            original_open(seconds)
            # BlueZ's thread calls the agent while the HTTP thread still waits.
            decision = FakeDecision()
            worker = threading.Thread(
                target=controller.agent_request,
                args=("confirmation", NEW_PHONE, "Fast", 42, decision),
            )
            worker.start()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive(), "agent callback deadlocked")
            outcome.append(decision)

        self.backend.open_window = open_window_with_request
        controller.open_window()
        self.assertIsNone(outcome[0].outcome)
        self.assertEqual(controller.status()["pending_request"]["code"], "000042")


class DeviceTests(ControllerTestCase):
    def test_status_lists_only_paired_devices_under_opaque_ids(self) -> None:
        status = self.controller.status()
        self.assertEqual(
            [(device["name"], device["trusted"], device["connected"]) for device in status["devices"]],
            [("Pixel", False, False), ("Tablet", True, True)],
        )
        for device in status["devices"]:
            self.assertRegex(device["device_id"], r"^[0-9a-f]{16}$")
            self.assertEqual(set(device), {"device_id", "name", "paired", "trusted", "connected"})
        self.assertNoAddress(status)
        self.assertEqual(self.controller.status()["devices"], status["devices"])

    def test_device_ids_change_with_a_new_controller(self) -> None:
        other = BluetoothController(FakeBluetoothBackend([PHONE]), timer_factory=None)
        self.assertNotEqual(other.status()["devices"][0]["device_id"], self.device_id("Pixel"))

    def test_actions_map_to_backend_calls(self) -> None:
        phone = self.device_id("Pixel")
        for action, call in (
            ("device.trust", ("set_trusted", PHONE.address, True)),
            ("device.connect", ("connect", PHONE.address)),
            ("device.disconnect", ("disconnect", PHONE.address)),
            ("device.untrust", ("set_trusted", PHONE.address, False)),
        ):
            with self.subTest(action):
                status = self.act(action=action, device_id=phone)
                self.assertEqual(self.backend.calls[-1], call)
                self.assertNoAddress(status)
        status = self.act(action="device.forget", device_id=phone, confirm=True)
        self.assertEqual(self.backend.calls[-1], ("remove", PHONE.address))
        self.assertEqual([device["name"] for device in status["devices"]], ["Tablet"])

    def test_forget_needs_confirm_true(self) -> None:
        phone = self.device_id("Pixel")
        for confirm in (False, "true", 1):
            with self.subTest(confirm=confirm):
                self.assertBluetoothError(
                    "confirmation_required",
                    HTTPStatus.BAD_REQUEST,
                    self.act,
                    action="device.forget",
                    device_id=phone,
                    confirm=confirm,
                )
        self.assertBluetoothError(
            "invalid_request", HTTPStatus.BAD_REQUEST, self.act, action="device.forget", device_id=phone
        )
        self.assertEqual(self.backend.calls, [])

    def test_unknown_and_unpaired_devices_are_not_found(self) -> None:
        self.act(action="window.open")
        self.backend.request_confirmation(NEW_PHONE, "Pending", 1)
        pending_id = self.controller.status()["pending_request"]["device_id"]
        for device_id in ("0" * 16, pending_id, "ünknown"):
            with self.subTest(device_id=device_id):
                self.assertBluetoothError(
                    "device_not_found",
                    HTTPStatus.NOT_FOUND,
                    self.act,
                    action="device.trust",
                    device_id=device_id,
                )

    def test_backend_failures_are_reported_without_backend_text(self) -> None:
        self.backend.fail_with = "AlreadyConnected"
        error = self.assertBluetoothError(
            "bluetooth_failed",
            HTTPStatus.BAD_GATEWAY,
            self.act,
            action="device.connect",
            device_id=self.device_id("Pixel"),
        )
        self.assertEqual(error.as_body(), {"error": "Bluetooth connect failed (AlreadyConnected)", "code": "bluetooth_failed"})
        self.assertIsNone(error.__cause__)
        self.assertTrue(error.__suppress_context__)

    def test_invalid_envelopes(self) -> None:
        phone = self.device_id("Pixel")
        for payload in (
            [],
            {},
            {"action": "scan"},
            {"action": "window.open", "device_id": phone},
            {"action": "device.trust"},
            {"action": "device.trust", "device_id": 7},
            {"action": "device.trust", "device_id": ""},
            {"action": "device.trust", "device_id": "x" * 65},
            {"action": "request.confirm", "request_id": None},
        ):
            with self.subTest(payload=payload):
                self.assertBluetoothError(
                    "invalid_request", HTTPStatus.BAD_REQUEST, self.controller.perform, payload
                )

    def test_names_never_carry_an_address(self) -> None:
        backend = FakeBluetoothBackend(
            [
                BluetoothDevice("AA:BB:CC:DD:EE:11", None, paired=True, trusted=False, connected=False),
                BluetoothDevice("AA:BB:CC:DD:EE:12", "AA-BB-CC-DD-EE-12", paired=True, trusted=False, connected=False),
                BluetoothDevice("AA:BB:CC:DD:EE:13", "Phone aabbccddee13", paired=True, trusted=False, connected=False),
                BluetoothDevice("AA:BB:CC:DD:EE:14", "11:22:33:44:55:66", paired=True, trusted=False, connected=False),
                BluetoothDevice("AA:BB:CC:DD:EE:15", "\x07​", paired=True, trusted=False, connected=False),
                BluetoothDevice("AA:BB:CC:DD:EE:16", "Pixel\n" + "x" * 80, paired=True, trusted=False, connected=False),
            ]
        )
        status = BluetoothController(backend, timer_factory=None).status()
        names = [device["name"] for device in status["devices"]]
        self.assertEqual(names[:5], [UNNAMED_DEVICE] * 5)
        self.assertEqual(names[5], "Pixel" + "x" * 59)
        self.assertNoAddress(status)


class PrivacyTests(ControllerTestCase):
    def test_no_address_or_passkey_outside_the_pending_request(self) -> None:
        bodies = [self.controller.status(), self.act(action="window.open")]
        self.backend.request_confirmation(NEW_PHONE, "New", 987654)
        status = self.controller.status()
        self.assertEqual(status["pending_request"]["code"], "987654")
        without_pending = dict(status, pending_request=None)
        self.assertNotIn("987654", json.dumps(without_pending))
        bodies.append(status)
        bodies.append(self.act(action="request.confirm", request_id="request-1"))
        self.assertNotIn("987654", json.dumps(bodies[-1]))
        bodies.append(audio_source_catalog(None, bluetooth=self.controller, bridge_probe=lambda: True))
        for body in bodies:
            self.assertNoAddress(body)


class AvailabilityTests(ControllerTestCase):
    def test_unavailable_status_and_actions(self) -> None:
        self.act(action="window.open")
        decision = self.backend.request_confirmation(NEW_PHONE, "New", 1)
        self.backend.unavailable_reason = "BlueZ is not running"
        status = self.controller.status()
        self.assertEqual(status["available"], False)
        self.assertEqual(status["reason"], "BlueZ is not running")
        self.assertEqual(status["devices"], [])
        self.assertEqual(decision.outcome, "rejected")
        for payload in (
            {"action": "window.open"},
            {"action": "window.close"},
            {"action": "request.confirm", "request_id": "request-1"},
            {"action": "device.trust", "device_id": "0" * 16},
        ):
            with self.subTest(payload=payload):
                self.assertBluetoothError(
                    "bluetooth_unavailable",
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    self.controller.perform,
                    payload,
                )
        self.assertEqual(self.controller.link_state(), (None, None))
        self.backend.unavailable_reason = None
        self.assertTrue(self.controller.status()["available"])

    def test_an_adapter_that_does_not_answer_makes_actions_unavailable(self) -> None:
        def hung() -> dict:
            raise BluetoothBackendError("timeout")

        self.backend.adapter_state = hung
        self.assertEqual(self.controller.status()["available"], False)
        error = self.assertBluetoothError(
            "bluetooth_unavailable",
            HTTPStatus.SERVICE_UNAVAILABLE,
            self.controller.perform,
            {"action": "window.open"},
        )
        self.assertEqual(str(error), "Bluetooth is unavailable: BlueZ didn't answer (timeout)")
        self.assertNotIn(("open_window", WINDOW_SECONDS), self.backend.calls)

    def _stub_modules(self, *, gi: bool, bus_error: bool) -> dict:
        dbus = types.ModuleType("dbus")
        mainloop = types.ModuleType("dbus.mainloop")
        glib_loop = types.ModuleType("dbus.mainloop.glib")
        service = types.ModuleType("dbus.service")
        glib_loop.DBusGMainLoop = lambda **_kwargs: None

        def system_bus():
            raise RuntimeError("Could not connect: /run/dbus/system_bus_socket")

        dbus.SystemBus = system_bus if bus_error else (lambda: object())
        dbus.mainloop = mainloop
        mainloop.glib = glib_loop
        dbus.service = service
        modules = {
            "dbus": dbus,
            "dbus.mainloop": mainloop,
            "dbus.mainloop.glib": glib_loop,
            "dbus.service": service,
            "gi": None,
            "gi.repository": None,
        }
        if gi:
            gi_module = types.ModuleType("gi")
            repository = types.ModuleType("gi.repository")
            repository.GLib = types.SimpleNamespace(MainLoop=lambda: None)
            gi_module.repository = repository
            modules.update({"gi": gi_module, "gi.repository": repository})
        return modules

    def test_dbus_backend_reports_missing_pieces_as_unavailable(self) -> None:
        cases = {
            "dbus-python is not installed": {"dbus": None},
            "the GLib bindings (python3-gi) are not installed": self._stub_modules(gi=False, bus_error=False),
            "the system D-Bus is unreachable": self._stub_modules(gi=True, bus_error=True),
        }
        for reason, modules in cases.items():
            with self.subTest(reason), mock.patch.dict(sys.modules, modules):
                controller = BluetoothController(DbusBluetoothBackend(), timer_factory=None)
                status = controller.status()
                self.assertEqual((status["available"], status["reason"]), (False, reason))
                self.assertBluetoothError(
                    "bluetooth_unavailable",
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    controller.perform,
                    {"action": "window.open"},
                )
                self.assertNotIn("system_bus_socket", json.dumps(status))


class StandInDBusException(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._name = name

    def get_dbus_name(self) -> str:
        return self._name


class DbusBackendCallTests(unittest.TestCase):
    """The real backend's calls, through a stand-in proxy instead of a bus.

    This checks the call shapes dbus-python would send (method, arguments, and
    the explicit "ssv" signature that BlueZ's Properties.Set requires). It can't
    show that BlueZ accepts them; the Pi does that.
    """

    ADAPTER = "/org/bluez/hci0"

    def setUp(self) -> None:
        self.calls: list = []
        self.fail_set: set[str] = set()
        self.objects = {
            self.ADAPTER: {
                "org.bluez.Adapter1": {
                    "Powered": True,
                    "Discoverable": False,
                    "Pairable": True,
                    "DiscoverableTimeout": 180,
                    "PairableTimeout": 0,
                }
            },
            self.ADAPTER + "/dev_1": {
                "org.bluez.Device1": {"Adapter": self.ADAPTER, "Address": PHONE.address, "Paired": True}
            },
        }
        backend = DbusBluetoothBackend()
        backend._dbus = types.SimpleNamespace(
            Boolean=lambda value: ("b", bool(value)),
            UInt32=lambda value: ("u", int(value)),
            ObjectPath=lambda value: ("o", value),
            DBusException=StandInDBusException,
        )
        backend._ensure_started = lambda: None
        backend._submit = self._run
        backend._interface = self._proxy
        backend._register_agent = lambda: self.calls.append("register_agent")
        self.backend = backend

    def _run(self, start, _timeout):
        results: list = []
        failures: list = []
        start(
            lambda *values: results.append(values[0] if values else None),
            lambda exc: failures.append(self.backend._safe_error(exc)),
        )
        if failures:
            raise failures[0]
        return results[0] if results else None

    def _proxy(self, path: str, interface: str):
        test = self

        class Proxy:
            def __getattr__(self, method: str):
                def call(*args, reply_handler, error_handler, timeout, **kwargs):
                    if method == "GetManagedObjects":
                        reply_handler(test.objects)
                        return
                    test.calls.append((path, interface, method, args, kwargs.get("signature")))
                    if method == "Set" and args[1] in test.fail_set:
                        error_handler(StandInDBusException("org.bluez.Error.Failed"))
                    else:
                        reply_handler()

                return call

        return Proxy()

    def writes(self) -> list:
        """Property writes as (name, value); other calls by method name."""

        summary = []
        for call in self.calls:
            if call == "register_agent":
                summary.append(call)
            elif call[2] == "Set":
                self.assertEqual(call[4], "ssv", f"{call[3][1]} sent without the ssv signature")
                self.assertEqual(call[1], "org.freedesktop.DBus.Properties")
                summary.append((call[3][1], call[3][2]))
            else:
                summary.append(call[2])
        self.calls.clear()
        return summary

    def test_open_writes_with_ssv_and_close_restores_the_adapter_as_found(self) -> None:
        self.backend.open_window(120)
        self.assertEqual(
            self.writes(),
            [
                ("Powered", ("b", True)),
                "register_agent",
                ("PairableTimeout", ("u", 120)),
                ("DiscoverableTimeout", ("u", 120)),
                ("Pairable", ("b", True)),
                ("Discoverable", ("b", True)),
            ],
        )
        self.backend.close_window()
        self.assertEqual(
            self.writes(),
            [
                ("Discoverable", ("b", False)),
                ("DiscoverableTimeout", ("u", 180)),
                ("PairableTimeout", ("u", 0)),
                ("Pairable", ("b", True)),
                "UnregisterAgent",
            ],
        )
        # Nothing left to restore: a second close only stops discoverability.
        self.backend.close_window()
        self.assertEqual(self.writes(), [("Discoverable", ("b", False)), "UnregisterAgent"])

    def test_a_failed_restore_is_retried_by_the_next_close(self) -> None:
        self.backend.open_window(120)
        self.writes()
        self.fail_set = {"PairableTimeout"}
        with self.assertRaises(BluetoothBackendError) as caught:
            self.backend.close_window()
        self.assertEqual(caught.exception.reason, "Failed")
        self.assertIn("UnregisterAgent", self.writes())
        self.fail_set = set()
        self.backend.close_window()
        self.assertIn(("PairableTimeout", ("u", 0)), self.writes())

    def test_trust_writes_with_ssv_and_forget_passes_an_object_path(self) -> None:
        self.backend.set_trusted(PHONE.address, True)
        self.assertEqual(self.writes(), [("Trusted", ("b", True))])
        self.backend.remove(PHONE.address)
        self.assertEqual(self.calls[-1][2:4], ("RemoveDevice", (("o", self.ADAPTER + "/dev_1"),)))


class SourceCatalogTests(ControllerTestCase):
    def test_catalog_reports_both_sources_and_the_running_choice(self) -> None:
        running = {"state": "running", "visualization_id": "fft", "parameters": {"audio_source": "bluetooth"}}
        catalog = audio_source_catalog(running, bluetooth=self.controller, bridge_probe=lambda: True)
        self.assertEqual(
            catalog,
            {
                "sources": [
                    {"source_id": "usb-mic", "availability": "checked_at_launch"},
                    {"source_id": "bluetooth", "bridge_present": True, "connected": True, "streaming": True},
                ],
                "active_source": "bluetooth",
            },
        )
        self.backend.devices_by_address.pop(SPEAKER.address)
        idle = {"state": "idle", "visualization_id": None, "parameters": {}}
        catalog = audio_source_catalog(idle, bluetooth=self.controller, bridge_probe=lambda: False)
        self.assertEqual(
            catalog["sources"][1],
            {"source_id": "bluetooth", "bridge_present": False, "connected": False, "streaming": False},
        )
        self.assertIsNone(catalog["active_source"])
        sorts = {"state": "running", "visualization_id": "sorts", "parameters": {"audio_source": "x"}}
        self.assertIsNone(audio_source_catalog(sorts, bluetooth=None)["active_source"])

    def test_unknown_link_state_is_null(self) -> None:
        self.backend.unavailable_reason = "BlueZ is not running"
        for bluetooth in (self.controller, None):
            with self.subTest(controller=bluetooth is not None):
                source = audio_source_catalog(None, bluetooth=bluetooth, bridge_probe=lambda: True)["sources"][1]
                self.assertEqual((source["connected"], source["streaming"]), (None, None))

    def test_bridge_probe_matches_install_sh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cards = Path(directory) / "cards"
            cards.write_text(
                " 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n"
                " 3 [Device         ]: USB-Audio - iMM-6C\n"
                "10 [Loopback       ]: Loopback - Loopback\n"
                "                      Loopback 1\n"
            )
            self.assertTrue(bluetooth_bridge_present(cards))
            cards.write_text(" 2 [Loopback       ]: Loopback - Loopback\n")
            self.assertFalse(bluetooth_bridge_present(cards))
            self.assertFalse(bluetooth_bridge_present(Path(directory) / "missing"))


if __name__ == "__main__":
    unittest.main()
