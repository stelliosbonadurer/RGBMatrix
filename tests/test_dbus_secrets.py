from __future__ import annotations

import unittest

from matrix_network.dbus_secrets import NetworkManagerDbusSecrets
from matrix_network.models import NetworkOperationError


class FakeDictionary(dict):
    def __init__(self, values, signature):
        super().__init__(values)
        self.signature = signature


class FakeDbus:
    String = str
    Boolean = bool
    ByteArray = bytes
    UInt32 = int
    Dictionary = FakeDictionary

    @staticmethod
    def Interface(obj, _interface):
        return obj


class FakeSettings:
    def __init__(self):
        self.added = None
        self.paths = []
        self.failure = None

    def AddConnection2(self, settings, flags, options):
        if self.failure:
            raise self.failure
        self.added = (settings, flags, options)
        return "/connection/new", {}

    def ListConnections(self):
        if self.failure:
            raise self.failure
        return self.paths


class FakeConnection:
    def __init__(self, profile_uuid):
        self.settings = {
            "connection": {"uuid": profile_uuid},
            "802-11-wireless": {"ssid": b"Setup"},
        }
        self.updated = None

    def GetSettings(self):
        return self.settings

    def Update2(self, settings, flags, options):
        self.updated = (settings, flags, options)
        return {}


class FakeBus:
    def __init__(self, settings, connections=None):
        self.settings = settings
        self.connections = connections or {}

    def get_object(self, _service, path):
        if path.endswith("/Settings"):
            return self.settings
        return self.connections[path]


class DbusSecretsTests(unittest.TestCase):
    def test_candidate_payload_has_explicit_signatures_and_generated_uuid(self) -> None:
        settings = FakeSettings()
        store = NetworkManagerDbusSecrets(bus=FakeBus(settings), dbus_module=FakeDbus)
        candidate_uuid = store.add_wifi_candidate(
            profile_name="rgb-matrix-candidate-job",
            interface="wlan0",
            ssid="Home",
            passphrase="candidate-secret",
            hidden=True,
        )

        payload, flags, options = settings.added
        self.assertEqual(payload.signature, "sa{sv}")
        self.assertTrue(all(section.signature == "sv" for section in payload.values()))
        self.assertEqual(payload["connection"]["uuid"], candidate_uuid)
        self.assertEqual(payload["802-11-wireless"]["ssid"], b"Home")
        self.assertEqual(payload["802-11-wireless-security"]["psk"], "candidate-secret")
        self.assertEqual(flags, 1)
        self.assertEqual(options.signature, "sv")

    def test_existing_profile_psk_is_updated_with_typed_options(self) -> None:
        settings = FakeSettings()
        connection = FakeConnection("ap-uuid")
        settings.paths = ["/connection/ap"]
        store = NetworkManagerDbusSecrets(
            bus=FakeBus(settings, {"/connection/ap": connection}),
            dbus_module=FakeDbus,
        )
        store.set_wifi_psk("ap-uuid", "ap-secret")

        payload, flags, options = connection.updated
        self.assertEqual(payload["802-11-wireless-security"]["psk"], "ap-secret")
        self.assertEqual(flags, 1)
        self.assertEqual(options.signature, "sv")

    def test_dbus_error_text_is_sanitized(self) -> None:
        settings = FakeSettings()
        settings.failure = RuntimeError("serialized candidate-secret")
        store = NetworkManagerDbusSecrets(bus=FakeBus(settings), dbus_module=FakeDbus)

        with self.assertRaises(NetworkOperationError) as caught:
            store.add_wifi_candidate(
                profile_name="candidate",
                interface="wlan0",
                ssid="Home",
                passphrase="candidate-secret",
                hidden=False,
            )
        self.assertNotIn("candidate-secret", str(caught.exception))

        with self.assertRaises(NetworkOperationError) as lookup_error:
            store.set_wifi_psk("missing", "candidate-secret")
        self.assertNotIn("candidate-secret", str(lookup_error.exception))


if __name__ == "__main__":
    unittest.main()
