from __future__ import annotations

import unittest

from matrix_network.backend import AP_PROFILE_NAME, NetworkManagerBackend
from matrix_network.models import DeviceSnapshot, WifiProfile


class RecordingRunner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def execute(self, arguments, **_kwargs):
        self.calls.append(list(arguments))


class RecordingSecrets:
    def __init__(self):
        self.psk_updates = []
        self.candidates = []

    def set_wifi_psk(self, uuid, passphrase):
        self.psk_updates.append((uuid, passphrase))

    def add_wifi_candidate(self, **values):
        self.candidates.append(values)
        return "candidate-uuid"


class FakeBackend(NetworkManagerBackend):
    def __init__(self, runner, secrets, profiles):
        super().__init__(runner, secret_store=secrets)
        self._profiles = profiles

    def wifi_profiles(self):
        return list(self._profiles)

    def device_snapshot(self):
        return DeviceSnapshot(30, None, None)


class NetworkBackendTests(unittest.TestCase):
    def test_ap_passphrase_never_appears_in_nmcli_arguments(self) -> None:
        secret = "not-in-process-argv"
        runner = RecordingRunner()
        secrets = RecordingSecrets()
        profile = WifiProfile(AP_PROFILE_NAME, "ap-uuid", False, -999)
        backend = FakeBackend(runner, secrets, [profile])

        self.assertEqual(
            backend.ensure_ap_profile(
                ssid="RGB-Matrix-TEST",
                passphrase=secret,
                address="192.168.50.1/24",
            ),
            "ap-uuid",
        )
        self.assertEqual(secrets.psk_updates, [("ap-uuid", secret)])
        self.assertNotIn(secret, repr(runner.calls))

    def test_candidate_and_secret_are_one_dbus_operation(self) -> None:
        secret = "candidate-secret"
        runner = RecordingRunner()
        secrets = RecordingSecrets()
        candidate = WifiProfile("rgb-matrix-candidate-job", "candidate-uuid", False, 0)
        backend = FakeBackend(runner, secrets, [candidate])

        created = backend.create_candidate(
            job_id="job",
            ssid="Home",
            passphrase=secret,
            hidden=True,
        )
        self.assertEqual(created.uuid, "candidate-uuid")
        self.assertEqual(len(secrets.candidates), 1)
        self.assertEqual(secrets.candidates[0]["passphrase"], secret)
        self.assertNotIn(secret, repr(runner.calls))


if __name__ == "__main__":
    unittest.main()
