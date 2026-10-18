from __future__ import annotations

import subprocess
import unittest

from matrix_network.models import NetworkOperationError
from matrix_network.nmcli import NmcliRunner, parse_property_lines, split_terse_line


class NmcliTests(unittest.TestCase):
    def test_escaped_terse_fields_are_parsed(self) -> None:
        self.assertEqual(split_terse_line(r"Cafe\: west:82:WPA2"), ["Cafe: west", "82", "WPA2"])
        self.assertEqual(
            parse_property_lines("GENERAL.CONNECTION:Cafe\\: west\nIP4.ADDRESS[1]:10.0.0.2/24\n"),
            {"GENERAL.CONNECTION": "Cafe: west", "IP4.ADDRESS[1]": "10.0.0.2/24"},
        )

    def test_runner_never_uses_a_shell(self) -> None:
        observed = {}

        def run(argv, **kwargs):
            observed.update(argv=argv, kwargs=kwargs)
            return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

        result = NmcliRunner(run=run).execute(["device", "status"])
        self.assertEqual(result.stdout, "ok")
        self.assertFalse(observed["kwargs"]["shell"])
        self.assertEqual(observed["argv"][:2], ["/usr/bin/nmcli", "device"])

    def test_sensitive_failure_does_not_echo_command_or_stderr(self) -> None:
        secret = "never-print-this"

        def run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 10, stdout="", stderr=f"bad {secret}")

        with self.assertRaises(NetworkOperationError) as caught:
            NmcliRunner(run=run).execute(["wifi-sec.psk", secret], sensitive=True)
        self.assertNotIn(secret, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
