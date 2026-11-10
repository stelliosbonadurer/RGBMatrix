"""Tests for launcher argument ownership and passthrough behavior."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

import launch_rgbmatrix


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


if __name__ == "__main__":
    unittest.main()
