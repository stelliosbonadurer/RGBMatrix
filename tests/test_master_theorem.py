"""Hardware-free tests for the Master Theorem visualization."""

from __future__ import annotations

import unittest

from my_programs.dsa_visualizations.model import RecurrenceSpec, analyze_recurrence
from my_programs.dsa_visualizations.native_matrix import NativeMatrixApp
from my_programs.dsa_visualizations.rendering import CASE_COLORS, MasterTheoremRenderer
from matrix_controller.definitions import application_parameters
from matrix_controller.registry import build_default_registry


class RecordingCanvas:
    def __init__(self, width: int = 64, height: int = 64):
        self.width = width
        self.height = height
        self.pixels: dict[tuple[int, int], tuple[int, int, int]] = {}

    def Clear(self) -> None:
        self.pixels.clear()

    def SetPixel(self, x: int, y: int, red: int, green: int, blue: int) -> None:
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise AssertionError(f"out-of-bounds pixel: {(x, y)}")
        self.pixels[(x, y)] = (red, green, blue)


class MasterTheoremModelTests(unittest.TestCase):
    def test_merge_sort_has_equal_work_at_every_level(self) -> None:
        analysis = analyze_recurrence(RecurrenceSpec(a=2, b=2, n=64, k=1))
        self.assertEqual(analysis.case, 2)
        self.assertEqual(analysis.dominance, "balanced")
        self.assertEqual(analysis.asymptotic, "Theta(n^1 log n)")
        self.assertEqual(len(analysis.levels), 7)
        self.assertTrue(all(level.total_work == 64 for level in analysis.levels))

        level = analysis.levels[2]
        self.assertEqual(level.node_count, 4)
        self.assertEqual(level.problem_size, 16)
        self.assertEqual(level.work_per_node, 16)

    def test_root_and_leaf_dominant_cases(self) -> None:
        root = analyze_recurrence(RecurrenceSpec(a=2, b=2, n=64, k=2))
        self.assertEqual((root.case, root.dominance, root.level_work_ratio), (3, "root", 0.5))
        self.assertEqual(root.asymptotic, "Theta(n^2)")
        self.assertEqual(root.dominant_level_indices, (0,))

        leaves = analyze_recurrence(RecurrenceSpec(a=4, b=2, n=64, k=1))
        self.assertEqual((leaves.case, leaves.dominance), (1, "leaves"))
        self.assertEqual(leaves.asymptotic, "Theta(n^2)")
        self.assertEqual(leaves.dominant_level_indices, (len(leaves.levels) - 1,))

    def test_invalid_recurrences_are_rejected(self) -> None:
        invalid = (
            RecurrenceSpec(a=0, b=2, n=64, k=1),
            RecurrenceSpec(a=2, b=1, n=64, k=1),
            RecurrenceSpec(a=2, b=2, n=1, k=1),
            RecurrenceSpec(a=2, b=2, n=64, k=-1),
        )
        for spec in invalid:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                analyze_recurrence(spec)

        stopped_at_root = analyze_recurrence(
            RecurrenceSpec(a=2, b=2, n=64, k=1, base_case=64)
        )
        self.assertEqual(len(stopped_at_root.levels), 1)


class MasterTheoremRendererTests(unittest.TestCase):
    def test_progressive_render_is_bounded_and_deterministic(self) -> None:
        analysis = analyze_recurrence(RecurrenceSpec(a=2, b=2, n=64, k=2))
        renderer = MasterTheoremRenderer()
        canvas = RecordingCanvas()

        renderer.render(canvas, analysis, reveal_levels=1)
        root_frame = dict(canvas.pixels)
        self.assertTrue(root_frame)
        self.assertFalse(any(y >= 12 for _, y in root_frame))

        renderer.render(canvas, analysis, reveal_levels=2)
        self.assertGreater(len(canvas.pixels), len(root_frame))
        second_frame = dict(canvas.pixels)
        renderer.render(canvas, analysis, reveal_levels=2)
        self.assertEqual(canvas.pixels, second_frame)

    def test_quadratic_root_is_a_five_row_box(self) -> None:
        analysis = analyze_recurrence(RecurrenceSpec(a=2, b=2, n=64, k=2))
        renderer = MasterTheoremRenderer()
        canvas = RecordingCanvas()
        renderer.render(canvas, analysis, reveal_levels=1)
        occupied_tree_rows = {
            y for (_x, y) in canvas.pixels if 6 <= y < 12
        }
        self.assertEqual(occupied_tree_rows, {6, 7, 8, 9, 10, 11})

    def test_linear_work_is_drawn_as_a_line(self) -> None:
        analysis = analyze_recurrence(RecurrenceSpec(a=2, b=2, n=64, k=1))
        renderer = MasterTheoremRenderer()
        canvas = RecordingCanvas()
        renderer.render(canvas, analysis, reveal_levels=1)
        occupied_tree_rows = {
            y for (_x, y) in canvas.pixels if 6 <= y < 12
        }
        self.assertEqual(occupied_tree_rows, {6, 11})

    def test_single_recursive_child_shrinks_with_problem_size(self) -> None:
        analysis = analyze_recurrence(RecurrenceSpec(a=1, b=2, n=64, k=0))
        renderer = MasterTheoremRenderer()
        canvas = RecordingCanvas()
        renderer.render(canvas, analysis, reveal_levels=3)
        widths = []
        for bottom_row in (11, 17, 23):
            widths.append(len({x for (x, y) in canvas.pixels if y == bottom_row}))
        self.assertEqual(widths, [64, 32, 16])

    def test_large_node_counts_are_aggregated_and_result_is_highlighted(self) -> None:
        analysis = analyze_recurrence(RecurrenceSpec(a=8, b=2, n=64, k=1))
        renderer = MasterTheoremRenderer()
        canvas = RecordingCanvas()
        renderer.render(canvas, analysis, show_result=True)

        last_display_index = len(renderer.visible_levels(analysis)) - 1
        last_band_top = 6 + last_display_index * 6
        white_pixels = [
            point for point, color in canvas.pixels.items()
            if color == (255, 255, 255) and last_band_top <= point[1] < last_band_top + 6
        ]
        self.assertTrue(white_pixels)
        self.assertIn(CASE_COLORS[1], canvas.pixels.values())

    def test_deep_tree_keeps_root_levels_and_final_leaf_level(self) -> None:
        analysis = analyze_recurrence(RecurrenceSpec(a=2, b=2, n=4096, k=1))
        renderer = MasterTheoremRenderer()
        visible = renderer.visible_levels(analysis)
        self.assertEqual(len(visible), renderer.maximum_visible_levels)
        self.assertEqual(visible[0].index, 0)
        self.assertEqual(visible[-1].index, len(analysis.levels) - 1)


class MasterTheoremCliTests(unittest.TestCase):
    def test_managed_argv_is_accepted_by_visualization_parser(self) -> None:
        definition = build_default_registry().get("master_theorem")
        values = definition.validate_parameters(
            {"example": "root_heavy", "cycles": 1, "led_no_hardware_pulse": True}
        )
        argv = definition.build_argv("/fixed/python", values)
        app = NativeMatrixApp()
        for parameter in application_parameters("master_theorem"):
            parameter.add_to_argparse(app.parser)
        parsed = app.parser.parse_args(argv[2:])
        self.assertEqual(parsed.example, "root_heavy")
        self.assertEqual(parsed.cycles, 1)
        self.assertTrue(parsed.led_no_hardware_pulse)


if __name__ == "__main__":
    unittest.main()
