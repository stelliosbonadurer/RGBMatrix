"""Strict launch-config tests without matrix hardware or subprocesses."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from matrix_controller.controller_config import (
    ControllerConfigError,
    load_controller_config,
)
from matrix_controller.registry import build_default_registry


class ControllerConfigTests(unittest.TestCase):
    def write_config(self, payload: dict) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "controller.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_checked_in_config_covers_matrix_and_visualization_defaults(self) -> None:
        config = load_controller_config(Path("config/matrix-controller.json"))
        self.assertEqual(config.matrix_defaults["led_rows"], 64)
        self.assertEqual(config.matrix_defaults["led_cols"], 64)
        self.assertEqual(config.matrix_defaults["led_slowdown_gpio"], 2)
        self.assertEqual(config.visualization_defaults["fft"]["theme"], "ocean")
        self.assertTrue(config.use_sudo)
        self.assertTrue(config.python_executable.endswith("/fftdemo/.venv/bin/python3"))

    def test_defaults_merge_into_registry_and_fixed_argv(self) -> None:
        config = load_controller_config(
            self.write_config(
                {
                    "version": 1,
                    "matrix": {
                        "led_rows": 32,
                        "led_no_hardware_pulse": True,
                    },
                    "visualizations": {"fft": {"theme": "warm"}},
                }
            )
        )
        defaults = {
            item: config.defaults_for(item)
            for item in ("system_info", "fft", "sorts")
        }
        registry = build_default_registry(parameter_defaults=defaults)
        fft = registry.get("fft")
        values = fft.validate_parameters({})
        self.assertEqual(values["led_rows"], 32)
        self.assertEqual(values["theme"], "warm")
        argv = fft.build_argv("/fixed/python", values)
        pulse_index = argv.index("--led-no-hardware-pulse")
        self.assertEqual(argv[pulse_index + 1], "1")

    def test_unknown_and_invalid_values_are_rejected(self) -> None:
        cases = (
            {"version": 2},
            {"version": 1, "unexpected": {}},
            {"version": 1, "runtime": {"use_sudo": "yes"}},
            {"version": 1, "matrix": {"led_rows": 0}},
            {"version": 1, "visualizations": {"fft": {"theme": "missing"}}},
            {"version": 1, "visualizations": {"unknown": {}}},
        )
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ControllerConfigError):
                load_controller_config(self.write_config(payload))

    def test_supplied_missing_file_does_not_silently_fall_back(self) -> None:
        with self.assertRaisesRegex(ControllerConfigError, "could not read"):
            load_controller_config(Path("/definitely/missing/controller.json"))


if __name__ == "__main__":
    unittest.main()
