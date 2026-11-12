"""Hardware-free tests for the Master Theorem visualization."""

from __future__ import annotations

import unittest

from my_programs.dsa_visualizations.model import RecurrenceSpec, analyze_recurrence
from my_programs.dsa_visualizations.native_matrix import NativeMatrixApp
from my_programs.dsa_visualizations.rendering import (
    INPUT_COLOR,
    WORK_COLOR,
    MasterTheoremRenderer,
)
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
    def test_input_is_a_centered_one_pixel_line_with_literal_n_width(self) -> None:
        analysis = analyze_recurrence(RecurrenceSpec(a=2, b=2, n=32, k=2))
        renderer = MasterTheoremRenderer()
        canvas = RecordingCanvas()

        renderer.render(canvas, analysis)
        input_pixels = {
            x
            for (x, y), color in canvas.pixels.items()
            if y == 0 and color == INPUT_COLOR
        }
        self.assertEqual(input_pixels, set(range(16, 48)))
        self.assertFalse(any(y == 1 for _x, y in canvas.pixels))

    def test_input_n_is_drawn_without_visual_scaling(self) -> None:
        analysis = analyze_recurrence(RecurrenceSpec(a=2, b=2, n=20, k=1))
        canvas = RecordingCanvas()
        MasterTheoremRenderer().render(canvas, analysis)
        self.assertEqual(
            {
                x
                for (x, y), color in canvas.pixels.items()
                if y == 0 and color == INPUT_COLOR
            },
            set(range(22, 42)),
        )
    def test_quadratic_work_draws_one_square_per_child(self) -> None:
        analysis = analyze_recurrence(RecurrenceSpec(a=2, b=2, n=32, k=2))
        renderer = MasterTheoremRenderer()
        canvas = RecordingCanvas()
        renderer.render(canvas, analysis)

        first_layer = {
            (x, y) for (x, y), color in canvas.pixels.items()
            if color == WORK_COLOR and 2 <= y < 18
        }
        expected = {
            (x, y)
            for y in range(2, 18)
            for x in (*range(15, 31), *range(32, 48))
        }
        self.assertEqual(first_layer, expected)

    def test_linear_work_is_drawn_as_a_line(self) -> None:
        analysis = analyze_recurrence(RecurrenceSpec(a=2, b=2, n=32, k=1))
        renderer = MasterTheoremRenderer()
        canvas = RecordingCanvas()
        renderer.render(canvas, analysis)
        first_layer = [
            (x, y)
            for (x, y), color in canvas.pixels.items()
            if color == WORK_COLOR and 2 <= y < 18
        ]
        self.assertEqual(len(first_layer), 32)
        self.assertEqual(len({x for x, _y in first_layer}), 2)
        self.assertEqual({y for _x, y in first_layer}, set(range(2, 18)))

    def test_constant_work_is_one_pixel_per_node(self) -> None:
        analysis = analyze_recurrence(RecurrenceSpec(a=2, b=2, n=32, k=0))
        renderer = MasterTheoremRenderer()
        canvas = RecordingCanvas()
        renderer.render(canvas, analysis)
        self.assertEqual(
            len(
                [
                    (x, y)
                    for (x, y), color in canvas.pixels.items()
                    if y == 2 and color == WORK_COLOR
                ]
            ),
            2,
        )
        self.assertEqual(
            len(
                [
                    (x, y)
                    for (x, y), color in canvas.pixels.items()
                    if y == 4 and color == WORK_COLOR
                ]
            ),
            4,
        )

    def test_legacy_animation_arguments_do_not_change_static_frame(self) -> None:
        analysis = analyze_recurrence(RecurrenceSpec(a=2, b=2, n=32, k=2))
        renderer = MasterTheoremRenderer()
        canvas = RecordingCanvas()
        renderer.render(canvas, analysis)
        static_frame = dict(canvas.pixels)
        renderer.render(
            canvas,
            analysis,
            reveal_levels=1,
            highlight_level=0,
            show_result=True,
        )
        self.assertEqual(canvas.pixels, static_frame)

    def test_fractional_work_fills_full_and_partial_columns(self) -> None:
        analysis = analyze_recurrence(RecurrenceSpec(a=1, b=2, n=32, k=1.2))
        renderer = MasterTheoremRenderer()
        canvas = RecordingCanvas()
        renderer.render(canvas, analysis)
        first_layer = [
            (x, y)
            for (x, y), color in canvas.pixels.items()
            if color == WORK_COLOR and 2 <= y < 18
        ]
        self.assertEqual(len(first_layer), round(16**1.2))
        columns = sorted({x for x, _y in first_layer})
        self.assertEqual(len(columns), 2)
        self.assertEqual(
            [len([point for point in first_layer if point[0] == x]) for x in columns],
            [16, 12],
        )

    def test_linear_tree_keeps_every_layer_that_fits(self) -> None:
        analysis = analyze_recurrence(RecurrenceSpec(a=2, b=2, n=32, k=1))
        renderer = MasterTheoremRenderer()
        visible = renderer.visible_levels(analysis)
        self.assertEqual(len(visible), len(analysis.levels) - 1)
        self.assertEqual(visible[0].index, 1)
        self.assertEqual(visible[-1].index, len(analysis.levels) - 1)


class MasterTheoremCliTests(unittest.TestCase):
    def test_managed_argv_is_accepted_by_visualization_parser(self) -> None:
        definition = build_default_registry().get("master_theorem")
        values = definition.validate_parameters(
            {
                "example": "root_heavy",
                "animate": False,
                "cycles": 1,
                "led_no_hardware_pulse": True,
            }
        )
        argv = definition.build_argv("/fixed/python", values)
        app = NativeMatrixApp()
        for parameter in application_parameters("master_theorem"):
            parameter.add_to_argparse(app.parser)
        parsed = app.parser.parse_args(argv[2:])
        self.assertEqual(parsed.example, "root_heavy")
        self.assertEqual(parsed.cycles, 1)
        self.assertFalse(parsed.animate)
        self.assertTrue(parsed.led_no_hardware_pulse)


if __name__ == "__main__":
    unittest.main()
