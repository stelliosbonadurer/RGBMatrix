from __future__ import annotations

import unittest

from matrix_network.service import NetworkDaemon


class FakeController:
    def status(self):
        return {"mode": "setup_ap"}

    def scan(self):
        return [{"ssid": "Home", "signal": 80, "security": "WPA2"}]


class NetworkDaemonTests(unittest.TestCase):
    def setUp(self):
        self.daemon = NetworkDaemon(
            FakeController(), admin_token="admin", setup_token="setup", transition_delay=60
        )

    def test_status_is_public_but_scan_requires_token(self) -> None:
        self.assertTrue(self.daemon.dispatch({"operation": "status", "payload": {}})["ok"])
        denied = self.daemon.dispatch({"operation": "scan", "payload": {}})
        self.assertEqual(denied["status"], 403)

    def test_invalid_credentials_fail_synchronously(self) -> None:
        response = self.daemon.dispatch(
            {
                "operation": "provision",
                "authorization": "setup",
                "payload": {
                    "ssid": "Home",
                    "passphrase": "short",
                    "hidden": False,
                    "confirm_disconnect": True,
                },
            }
        )
        self.assertEqual(response["status"], 400)

    def test_job_record_never_contains_passphrase(self) -> None:
        secret = "highly-secret"
        response = self.daemon.dispatch(
            {
                "operation": "provision",
                "authorization": "setup",
                "payload": {
                    "ssid": "Home",
                    "passphrase": secret,
                    "hidden": False,
                    "confirm_disconnect": True,
                },
            }
        )
        self.assertTrue(response["ok"])
        self.assertNotIn(secret, repr(response))


if __name__ == "__main__":
    unittest.main()
