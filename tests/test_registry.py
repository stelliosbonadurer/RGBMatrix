"""Hardware-free tests for the managed visualization contract."""

from __future__ import annotations

import math
import argparse
import unittest

from matrix_controller.registry import build_default_registry
from matrix_controller.schema import ValidationError


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = build_default_registry()

    def test_initial_allowlist_is_stable_and_sorted(self) -> None:
        self.assertEqual(
            [item.visualization_id for item in self.registry.list()],
            ["fft", "sorts", "system_info"],
        )

    def test_listing_does_not_import_hardware_applications(self) -> None:
        # Merely constructing/listing the registry must work on development machines.
        # If a future definition imports either application, this test environment's
        # missing rgbmatrix binding will make the regression visible.
        listing = [item.as_public_dict() for item in self.registry.list()]
        self.assertEqual(len(listing), 3)
        self.assertTrue(all(item["parameters"] for item in listing))

    def test_unknown_visualization_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "unknown visualization"):
            self.registry.get("../../bin/sh")

    def test_unknown_parameter_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "unknown parameters"):
            self.registry.get("fft").validate_parameters({"command": "shutdown"})

    def test_strict_parameter_types_and_ranges(self) -> None:
        fft = self.registry.get("fft")
        invalid_cases = (
            {"led_rows": True},
            {"led_rows": 0},
            {"led_brightness": 101},
            {"gradient": "true"},
            {"theme": "not-a-theme"},
        )
        for supplied in invalid_cases:
            with self.subTest(supplied=supplied), self.assertRaises(ValidationError):
                fft.validate_parameters(supplied)

        sorts = self.registry.get("sorts")
        with self.assertRaises(ValidationError):
            sorts.validate_parameters({"delay": math.inf})

    def test_defaults_are_fresh_and_deterministic(self) -> None:
        fft = self.registry.get("fft")
        first = fft.validate_parameters({})
        second = fft.validate_parameters({})
        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        first["theme"] = "warm"
        self.assertEqual(second["theme"], "ocean")

    def test_argv_is_fixed_and_never_uses_a_shell_string(self) -> None:
        sorts = self.registry.get("sorts")
        values = sorts.validate_parameters({"mode": "single", "sort": "bubble"})
        argv = sorts.build_argv("/fixed/python", values, ("sudo", "-n", "-E"))
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[:4], ["sudo", "-n", "-E", "/fixed/python"])
        self.assertIn("--sort", argv)
        self.assertIn("bubble", argv)
        self.assertNotIn("shell", " ".join(argv).lower())

    def test_argparse_adapter_uses_contract_validation(self) -> None:
        parser = argparse.ArgumentParser(exit_on_error=False)
        delay = self.registry.get("sorts").parameter("delay")
        delay.add_to_argparse(parser)
        self.assertEqual(parser.parse_args(["--delay", "0.125"]).delay, 0.125)
        with self.assertRaises(argparse.ArgumentError):
            parser.parse_args(["--delay", "99"])


if __name__ == "__main__":
    unittest.main()
