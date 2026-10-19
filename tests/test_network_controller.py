from __future__ import annotations

import unittest

from matrix_network.backend import AP_PROFILE_NAME
from matrix_network.controller import NetworkController, NetworkPolicy, validate_credentials
from matrix_network.models import DeviceSnapshot, NetworkOperationError, NetworkValidationError, WifiProfile


class FakeBackend:
    interface = "wlan0"

    def __init__(self):
        self.snapshot = DeviceSnapshot(30, None, None)
        self.saved: list[WifiProfile] = []
        self.activation_results: dict[str, DeviceSnapshot | Exception] = {}
        self.deleted: list[str] = []
        self.candidate: WifiProfile | None = None
        self.started_ap = 0
        self.activation_attempts: list[str] = []

    def device_snapshot(self):
        return self.snapshot

    def known_profiles(self):
        return list(self.saved)

    def activate(self, uuid, **_kwargs):
        self.activation_attempts.append(uuid)
        outcome = self.activation_results.get(uuid)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is not None:
            self.snapshot = outcome

    def start_ap(self, _uuid):
        self.started_ap += 1
        self.snapshot = DeviceSnapshot(100, AP_PROFILE_NAME, "192.168.50.1")
        return self.snapshot

    def create_candidate(self, **_kwargs):
        if self.candidate is None:
            self.candidate = WifiProfile("candidate", "candidate-uuid", False, 0)
        return self.candidate

    def promote_candidate(self, _candidate, **_kwargs):
        return None

    def delete_profile(self, uuid, **_kwargs):
        self.deleted.append(uuid)


class NetworkControllerTests(unittest.TestCase):
    def make_controller(self, backend, clock):
        return NetworkController(
            backend,
            ap_uuid="ap-uuid",
            ap_ssid="RGB-Matrix-BF6E",
            policy=NetworkPolicy(boot_grace_seconds=60, loss_grace_seconds=120),
            clock=lambda: clock[0],
        )

    def test_connected_lan_is_left_active(self) -> None:
        backend = FakeBackend()
        backend.snapshot = DeviceSnapshot(100, "Home", "10.0.0.9")
        controller = self.make_controller(backend, [0.0])
        controller.tick()
        self.assertEqual(controller.status()["mode"], "lan")

    def test_no_known_network_starts_ap_only_after_boot_grace(self) -> None:
        clock = [0.0]
        backend = FakeBackend()
        controller = self.make_controller(backend, clock)
        controller.tick()
        self.assertEqual(controller.status()["mode"], "trying")
        clock[0] = 61.0
        controller.tick()
        self.assertEqual(controller.status()["mode"], "setup_ap")

    def test_candidate_failure_deletes_only_candidate_and_restores_ap(self) -> None:
        backend = FakeBackend()
        backend.candidate = WifiProfile("candidate", "candidate-uuid", False, 0)
        backend.activation_results["candidate-uuid"] = NetworkOperationError("nm_failed", "no")
        controller = self.make_controller(backend, [0.0])
        with self.assertRaises(NetworkOperationError):
            controller.provision(job_id="job", ssid="Home", passphrase="password", hidden=False)
        self.assertEqual(backend.deleted, ["candidate-uuid"])
        self.assertEqual(controller.status()["mode"], "setup_ap")

    def test_sustained_loss_uses_longer_grace_and_reconnect_cancels_it(self) -> None:
        clock = [0.0]
        backend = FakeBackend()
        backend.snapshot = DeviceSnapshot(100, "Home", "10.0.0.9")
        controller = self.make_controller(backend, clock)
        controller.tick()

        backend.snapshot = DeviceSnapshot(30, None, None)
        clock[0] = 10.0
        controller.tick()
        clock[0] = 129.0
        controller.tick()
        self.assertEqual(backend.started_ap, 0)

        backend.snapshot = DeviceSnapshot(100, "Home", "10.0.0.10")
        controller.tick()
        backend.snapshot = DeviceSnapshot(30, None, None)
        clock[0] = 200.0
        controller.tick()
        clock[0] = 319.0
        controller.tick()
        self.assertEqual(backend.started_ap, 0)
        clock[0] = 320.0
        controller.tick()
        self.assertEqual(backend.started_ap, 1)

    def test_candidate_without_dhcp_address_is_removed_and_ap_restored(self) -> None:
        backend = FakeBackend()
        backend.candidate = WifiProfile("candidate", "candidate-uuid", False, 0)
        backend.activation_results["candidate-uuid"] = DeviceSnapshot(100, "candidate", None)
        controller = self.make_controller(backend, [0.0])

        with self.assertRaises(NetworkOperationError) as caught:
            controller.provision(job_id="job", ssid="Home", passphrase="password", hidden=False)
        self.assertEqual(caught.exception.code, "provision_failed")
        self.assertEqual(backend.deleted, ["candidate-uuid"])
        self.assertEqual(controller.status()["mode"], "setup_ap")

    def test_existing_ap_is_reconciled_after_service_restart(self) -> None:
        backend = FakeBackend()
        backend.snapshot = DeviceSnapshot(100, AP_PROFILE_NAME, "192.168.50.1")
        controller = self.make_controller(backend, [0.0])
        controller.tick()
        self.assertEqual(controller.status()["state"], "ap_ready")
        self.assertEqual(backend.started_ap, 0)

    def test_forget_rejects_active_and_last_profiles(self) -> None:
        backend = FakeBackend()
        backend.saved = [WifiProfile("Home", "one", True, 0, active=True)]
        controller = self.make_controller(backend, [0.0])
        with self.assertRaises(NetworkValidationError):
            controller.forget_profile("one")

    def test_credentials_are_strict(self) -> None:
        self.assertEqual(validate_credentials("Home", "password", False), ("Home", "password", False))
        with self.assertRaises(NetworkValidationError):
            validate_credentials("", "password", False)
        with self.assertRaises(NetworkValidationError):
            validate_credentials("Home", "short", False)


if __name__ == "__main__":
    unittest.main()
