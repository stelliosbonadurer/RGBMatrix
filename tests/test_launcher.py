"""Tests for launcher argument ownership and passthrough behavior."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import launch_rgbmatrix
from matrix_controller.controller_config import ControllerConfigError
from matrix_controller.definitions import MATRIX_PARAMETERS


def _parse(*extra: str):
    with patch.object(sys, "argv", ["launch_rgbmatrix.py", *extra]):
        return launch_rgbmatrix.parse_args()


def _flag_value(led_args: list[str], flag: str) -> str:
    return led_args[led_args.index(flag) + 1]


class LauncherArgumentTests(unittest.TestCase):
    def test_short_visualization_flags_are_not_launcher_abbreviations(self) -> None:
        argv = [
            "launch_rgbmatrix.py",
            "--program",
            "my_programs.dsa_visualizations.master_theorem",
            "--a",
            "4",
            "--b",
            "2",
            "--n",
            "64",
            "--k",
            "1",
            "--no-animate",
        ]
        with patch.object(sys, "argv", argv):
            args, passthrough = launch_rgbmatrix.parse_args()

        self.assertFalse(args.no_sudo)
        self.assertEqual(
            passthrough,
            ["--a", "4", "--b", "2", "--n", "64", "--k", "1", "--no-animate"],
        )

    def test_full_launcher_options_are_still_consumed(self) -> None:
        argv = ["launch_rgbmatrix.py", "--no-sudo", "--n", "32"]
        with patch.object(sys, "argv", argv):
            args, passthrough = launch_rgbmatrix.parse_args()

        self.assertTrue(args.no_sudo)
        self.assertEqual(passthrough, ["--n", "32"])


class LauncherMatrixConfigTests(unittest.TestCase):
    def _write_config(self, directory: str, matrix: dict) -> Path:
        path = Path(directory) / "matrix-controller.json"
        path.write_text(json.dumps({"version": 1, "matrix": matrix}), encoding="utf-8")
        return path

    def test_led_args_come_from_the_config_matrix_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_config(
                directory,
                {"led_gpio_mapping": "adafruit-hat-pwm", "led_slowdown_gpio": 3, "led_rows": 32},
            )
            args, _ = _parse("--config", str(path))
            led_args = launch_rgbmatrix.build_led_args(
                args, launch_rgbmatrix.load_matrix_settings(args.config)
            )

        self.assertEqual(_flag_value(led_args, "--led-gpio-mapping"), "adafruit-hat-pwm")
        self.assertEqual(_flag_value(led_args, "--led-slowdown-gpio"), "3")
        self.assertEqual(_flag_value(led_args, "--led-rows"), "32")
        # Values absent from the file fall back to the code-owned defaults.
        self.assertEqual(_flag_value(led_args, "--led-cols"), "64")

    def test_launcher_emits_the_same_flags_as_the_controller(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_config(directory, {"led_gpio_mapping": "adafruit-hat-pwm"})
            args, _ = _parse("--config", str(path))
            settings = launch_rgbmatrix.load_matrix_settings(args.config)
            led_args = launch_rgbmatrix.build_led_args(args, settings)

        expected: list[str] = []
        for parameter in MATRIX_PARAMETERS:
            expected.extend(parameter.cli_arguments(settings[parameter.name]))
        self.assertEqual(led_args, expected)

    def test_command_line_override_beats_the_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_config(directory, {"led_gpio_mapping": "adafruit-hat-pwm"})
            args, _ = _parse("--config", str(path), "--led-gpio-mapping", "adafruit-hat")
            led_args = launch_rgbmatrix.build_led_args(
                args, launch_rgbmatrix.load_matrix_settings(args.config)
            )

        self.assertEqual(_flag_value(led_args, "--led-gpio-mapping"), "adafruit-hat")

    def test_invalid_config_is_rejected_rather_than_silently_defaulted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_config(directory, {"led_gpio_mapping": "adafruit-hat-typo"})
            with self.assertRaises(ControllerConfigError):
                launch_rgbmatrix.load_matrix_settings(path)

    def test_repository_config_is_the_default_source(self) -> None:
        args, _ = _parse()
        self.assertEqual(args.config, launch_rgbmatrix.DEFAULT_CONFIG_PATH)
        launch_rgbmatrix.load_matrix_settings(args.config)


if __name__ == "__main__":
    unittest.main()
