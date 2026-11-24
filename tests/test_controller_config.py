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
from matrix_controller.matrix_settings import config_matrix_values
from matrix_controller.web_service import registry_for_config


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
        self.assertEqual(
            config.visualization_defaults["fft"]["audio_source"],
            "usb-mic",
        )
        self.assertEqual(config.visualization_defaults["master_theorem"]["a"], 2)
        self.assertFalse(config.visualization_defaults["master_theorem"]["animate"])
        self.assertEqual(config.visualization_defaults["graph_traversal"]["e"], 0)
        self.assertEqual(
            config.visualization_defaults["graph_traversal"]["display_mode"],
            "single",
        )
        self.assertEqual(
            config.visualization_defaults["graph_traversal"]["component_type"],
            "weak",
        )
        self.assertTrue(
            config.visualization_defaults["graph_traversal"]["ensure_path"]
        )
        self.assertFalse(
            config.visualization_defaults["graph_traversal"]["animate_back_edges"]
        )
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
        # D-035: a program's defaults no longer include the matrix block.
        self.assertEqual(config.defaults_for("fft"), {"theme": "warm"})
        self.assertEqual(config.defaults_for("sorts"), {})
        registry = registry_for_config(config)
        fft = registry.get("fft")
        values = fft.validate_parameters({})
        self.assertEqual(values["theme"], "warm")
        matrix = config_matrix_values(config.matrix_defaults)
        self.assertEqual(matrix["led_rows"], 32)
        self.assertEqual(matrix["led_cols"], 64)  # code default fills the rest
        argv = fft.build_argv("/fixed/python", values, matrix_values=matrix)
        pulse_index = argv.index("--led-no-hardware-pulse")
        self.assertEqual(argv[pulse_index + 1], "1")
        self.assertEqual(argv[argv.index("--led-rows") + 1], "32")

    def test_every_configured_program_gets_its_defaults(self) -> None:
        # Graph Traversal was left out of the service's defaults list until M4.
        config = load_controller_config(
            self.write_config(
                {
                    "version": 1,
                    "visualizations": {
                        "graph_traversal": {"n": 20, "traverse": "dfs"},
                        "sorts": {"sort": "bubble"},
                        "master_theorem": {"a": 3},
                    },
                }
            )
        )
        registry = registry_for_config(config)
        graph = registry.get("graph_traversal").validate_parameters({})
        self.assertEqual((graph["n"], graph["traverse"]), (20, "dfs"))
        self.assertEqual(registry.get("sorts").validate_parameters({})["sort"], "bubble")
        self.assertEqual(registry.get("master_theorem").validate_parameters({})["a"], 3)

    def test_shipped_config_applies_to_graph_traversal(self) -> None:
        config = load_controller_config(
            Path(__file__).resolve().parents[1] / "config" / "matrix-controller.json"
        )
        self.assertIn("graph_traversal", config.visualization_defaults)
        self.assertEqual(
            config_matrix_values(config.matrix_defaults)["led_gpio_mapping"],
            "adafruit-hat-pwm",
        )

    def test_access_switch_defaults_locked_and_is_strict(self) -> None:
        self.assertTrue(load_controller_config(self.write_config({"version": 1})).require_pairing)
        opened = load_controller_config(
            self.write_config({"version": 1, "access": {"require_pairing": False}})
        )
        self.assertFalse(opened.require_pairing)
        for access in ({"require_pairing": "no"}, {"require_pairing": 0}, {"other": True}, []):
            with self.subTest(access=access), self.assertRaises(ControllerConfigError):
                load_controller_config(self.write_config({"version": 1, "access": access}))

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
