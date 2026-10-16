"""Verify sorting CLI/mode behavior without importing the native matrix binding."""

from __future__ import annotations

import argparse
import importlib.util
import random
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


def load_sort_visualizer_module():
    """Load visualize_sorts with a minimal fake of upstream SampleBase."""

    fake_samplebase = types.ModuleType("samplebase")

    class FakeSampleBase:
        def __init__(self, *_args, **_kwargs):
            self.parser = argparse.ArgumentParser(exit_on_error=False)

    fake_samplebase.SampleBase = FakeSampleBase
    previous = sys.modules.get("samplebase")
    sys.modules["samplebase"] = fake_samplebase
    try:
        path = Path(__file__).resolve().parent.parent / "my_programs" / "visualize_sorts.py"
        spec = importlib.util.spec_from_file_location("visualize_sorts_under_test", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            del sys.modules["samplebase"]
        else:
            sys.modules["samplebase"] = previous


class SortModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_sort_visualizer_module()

    def test_direct_cli_preserves_auto_interactive_default(self) -> None:
        visualizer = self.module.VisualizeSorts()
        args = visualizer.parser.parse_args([])
        self.assertIsNone(args.mode)
        self.assertEqual(args.sort, "quick")
        self.assertIsNone(args.seed)

    def test_explicit_noninteractive_options_are_parsed_and_validated(self) -> None:
        visualizer = self.module.VisualizeSorts()
        args = visualizer.parser.parse_args(
            [
                "--mode",
                "single",
                "--sort",
                "bubble",
                "--delay",
                "0.01",
                "--seed",
                "12",
                "--max-runs",
                "3",
            ]
        )
        self.assertEqual(args.mode, "single")
        self.assertEqual(args.sort, "bubble")
        self.assertEqual(args.delay, 0.01)
        self.assertEqual(args.seed, 12)
        self.assertEqual(args.max_runs, 3)

    def test_bounded_random_mode_runs_requested_number(self) -> None:
        visualizer = self.module.VisualizeSorts()
        visualizer.args = SimpleNamespace(sort="quick", max_runs=3)
        observed: list[str] = []
        visualizer._run_named_sort = lambda sort_id, length, rng: observed.append(sort_id)
        visualizer._run_automatic("random", 64, random.Random(7))
        self.assertEqual(len(observed), 3)
        self.assertTrue(set(observed).issubset({item[0] for item in self.module.SORTS}))


if __name__ == "__main__":
    unittest.main()

