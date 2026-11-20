"""Hardware-free tests for the qualification-only FFT bulk output path."""

from __future__ import annotations

import inspect
from pathlib import Path
import sys
from types import ModuleType
import unittest

try:
    import numpy as np
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("FFT output-path tests require NumPy") from exc

try:
    from PIL import Image as _PillowImage  # noqa: F401
except ModuleNotFoundError:
    _PillowImage = None


FFT_ROOT = Path(__file__).resolve().parent.parent / "my_programs" / "fft_program"
MAIN_PATH = FFT_ROOT / "main.py"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))


# Loading a submodule first executes core/__init__.py, which exposes the audio
# processor.  Keep these output-only tests independent of PortAudio.
_sounddevice = ModuleType("sounddevice")
_prior_sounddevice = sys.modules.get("sounddevice")
_prior_core_modules = {
    name: module
    for name, module in sys.modules.items()
    if name == "core" or name.startswith("core.")
}
sys.modules["sounddevice"] = _sounddevice
try:
    from config.settings import Settings  # noqa: E402
    from core.output import (  # noqa: E402
        PillowBulkBarsOutputPlan,
        compile_pillow_bulk_bars_output,
    )
    from themes import get_theme  # noqa: E402
    from visualizers.bars_unified import BarsUnifiedVisualizer  # noqa: E402
finally:
    if _prior_sounddevice is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = _prior_sounddevice
    for _module_name in tuple(sys.modules):
        if _module_name == "core" or _module_name.startswith("core."):
            sys.modules.pop(_module_name)
    sys.modules.update(_prior_core_modules)


WIDTH = 64
HEIGHT = 64


def _qualification_visualizer() -> BarsUnifiedVisualizer:
    settings = Settings()
    settings.color.theme = "ocean"
    settings.overflow.enabled = True
    settings.shadow.enabled = False
    settings.peak.enabled = False

    visualizer = BarsUnifiedVisualizer(WIDTH, HEIGHT, settings)
    visualizer.set_theme(get_theme("ocean"))
    visualizer.gradient_mode = False
    visualizer.bars_enabled = True
    visualizer.full_mode = False
    visualizer.debug_mode = False
    return visualizer


class _DirectCanvas:
    """Small in-memory stand-in for the native direct-draw canvas."""

    def __init__(self) -> None:
        self.pixels = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        self.clear_calls = 0
        self.pixel_calls = 0

    def Clear(self) -> None:
        self.clear_calls += 1
        self.pixels.fill(0)

    def SetPixel(self, x, y, r, g, b) -> None:
        self.pixel_calls += 1
        self.pixels[y, x] = (r, g, b)

    def SetImage(self, *_args, **_kwargs) -> None:
        raise AssertionError("the direct renderer must not use SetImage")


class _BulkCanvas:
    """Captures Pillow uploads without importing the rgbmatrix binding."""

    def __init__(self) -> None:
        self.set_image_calls = 0
        self.images = []
        self.pixels = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)

    def SetImage(self, image, *args, **kwargs) -> None:
        self.set_image_calls += 1
        self.images.append(image)
        self.pixels = np.array(image, dtype=np.uint8, copy=True)

    def Clear(self) -> None:
        raise AssertionError("the bulk renderer must clear its Pillow image")

    def SetPixel(self, *_args, **_kwargs) -> None:
        raise AssertionError("the bulk renderer must not fall back to SetPixel")


class _FailingBulkCanvas:
    def __init__(self) -> None:
        self.set_image_calls = 0
        self.clear_calls = 0
        self.pixel_calls = 0

    def SetImage(self, *_args, **_kwargs) -> None:
        self.set_image_calls += 1
        raise OSError("injected native upload failure")

    def Clear(self) -> None:
        self.clear_calls += 1

    def SetPixel(self, *_args, **_kwargs) -> None:
        self.pixel_calls += 1


@unittest.skipUnless(_PillowImage is not None, "FFT bulk output tests require Pillow")
class PillowBulkBarsOutputTests(unittest.TestCase):
    def test_qualification_frames_exactly_match_direct_output(self) -> None:
        visualizer = _qualification_visualizer()
        plan = compile_pillow_bulk_bars_output(visualizer, WIDTH, HEIGHT)
        direct_canvas = _DirectCanvas()
        bulk_canvas = _BulkCanvas()

        nan_and_zero = np.zeros(WIDTH, dtype=np.float32)
        nan_and_zero[::7] = np.nan
        partial_and_full = np.linspace(0.0, 2.0 / 3.0, WIDTH, dtype=np.float32)
        frames = (
            ("nan and zero", nan_and_zero),
            ("partial through exactly full", partial_and_full),
            ("first overflow band", np.full(WIDTH, 1.0, dtype=np.float32)),
            ("higher overflow band", np.full(WIDTH, 1.5, dtype=np.float32)),
            # An empty frame after full frames proves candidate-owned clearing.
            ("empty after overflow", np.zeros(WIDTH, dtype=np.float32)),
        )

        for name, bars in frames:
            with self.subTest(name=name):
                visualizer.draw(direct_canvas, bars, None)
                plan.draw(bulk_canvas, bars, None)
                np.testing.assert_array_equal(bulk_canvas.pixels, direct_canvas.pixels)

        self.assertEqual(bulk_canvas.set_image_calls, len(frames))

    def test_uniform_ocean_overflow_band_colors_are_exact(self) -> None:
        visualizer = _qualification_visualizer()
        plan = compile_pillow_bulk_bars_output(visualizer, WIDTH, HEIGHT)
        canvas = _BulkCanvas()
        cases = (
            ("full first layer", 2.0 / 3.0, (247, 251, 253)),
            ("second layer", 1.0, (123, 255, 255)),
            ("third layer", 1.5, (235, 255, 255)),
        )

        for name, raw_ratio, expected_color in cases:
            with self.subTest(name=name):
                bars = np.full(WIDTH, raw_ratio, dtype=np.float32)
                plan.draw(canvas, bars, None)
                self.assertEqual(tuple(canvas.pixels[0, 0]), expected_color)
                self.assertEqual(tuple(canvas.pixels[-1, -1]), expected_color)

    def test_image_storage_and_compiled_geometry_identities_remain_stable(self) -> None:
        visualizer = _qualification_visualizer()
        plan = compile_pillow_bulk_bars_output(visualizer, WIDTH, HEIGHT)
        self.assertIsInstance(plan, PillowBulkBarsOutputPlan)
        canvas = _BulkCanvas()

        image = plan.image
        image_storage = plan.image.im
        boxes = plan.geometry_boxes
        box_ids = tuple(id(box) for box in boxes)
        overflow_colors = plan.overflow_colors
        color_ids = tuple(id(layer) for layer in overflow_colors)

        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.size, (WIDTH, HEIGHT))
        self.assertEqual(len(boxes), WIDTH)
        self.assertTrue(
            all(len(column_boxes) == HEIGHT + 1 for column_boxes in boxes)
        )
        self.assertEqual(boxes[0][0], (0, HEIGHT, 1, HEIGHT))
        self.assertEqual(boxes[0][HEIGHT], (0, 0, 1, HEIGHT))
        self.assertEqual(
            boxes[WIDTH - 1][HEIGHT],
            (WIDTH - 1, 0, WIDTH, HEIGHT),
        )

        for value in (0.0, 0.5, 1.0, 1.5, np.nan):
            plan.draw(canvas, np.full(WIDTH, value, dtype=np.float32), None)
            self.assertIs(plan.image, image)
            self.assertIs(plan.image.im, image_storage)
            self.assertIs(plan.geometry_boxes, boxes)
            self.assertEqual(tuple(id(box) for box in plan.geometry_boxes), box_ids)
            self.assertIs(plan.overflow_colors, overflow_colors)
            self.assertEqual(
                tuple(id(layer) for layer in plan.overflow_colors),
                color_ids,
            )
            self.assertIs(canvas.images[-1], image)

    def test_each_draw_performs_exactly_one_bulk_upload(self) -> None:
        plan = compile_pillow_bulk_bars_output(
            _qualification_visualizer(), WIDTH, HEIGHT
        )
        canvas = _BulkCanvas()

        for draw_number in range(1, 4):
            plan.draw(canvas, np.full(WIDTH, draw_number / 4, dtype=np.float32))
            self.assertEqual(canvas.set_image_calls, draw_number)
            self.assertIs(canvas.images[-1], plan.image)

    def test_runtime_guard_covers_every_candidate_invalidating_control(self) -> None:
        plan = compile_pillow_bulk_bars_output(
            _qualification_visualizer(), WIDTH, HEIGHT
        )

        self.assertEqual(
            plan.incompatible_runtime_paths,
            frozenset({
                "dynamic.adjust_speed",
                "dynamic.cycle_speed",
                "dynamic.freeze",
                "dynamic.frozen",
                "dynamic.reseed",
                "settings.overflow_multiplier",
                "settings.peak_enabled",
                "settings.shadow_enabled",
                "theme.next",
                "theme.previous",
                "visualizer.bars",
                "visualizer.debug",
                "visualizer.full",
                "visualizer.gradient",
                "visualizer.overflow",
                "visualizer.theme",
            }),
        )

    def test_upload_failure_is_wrapped_once_without_retry_or_fallback(self) -> None:
        plan = compile_pillow_bulk_bars_output(
            _qualification_visualizer(), WIDTH, HEIGHT
        )
        canvas = _FailingBulkCanvas()

        with self.assertRaisesRegex(
            RuntimeError, "bulk output upload failed"
        ) as caught:
            plan.draw(canvas, np.ones(WIDTH, dtype=np.float32), None)

        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertEqual(canvas.set_image_calls, 1)
        self.assertEqual(canvas.clear_calls, 0)
        self.assertEqual(canvas.pixel_calls, 0)


class PillowBulkBarsCompilationTests(unittest.TestCase):
    def test_compiler_rejects_every_configuration_outside_qualification_scope(self) -> None:
        cases = {
            "non-64 width": lambda visualizer: None,
            "non-64 height": lambda visualizer: None,
            "missing theme": lambda visualizer: visualizer.set_theme(None),
            "non-ocean theme": lambda visualizer: visualizer.set_theme(
                get_theme("warm")
            ),
            "gradient": lambda visualizer: setattr(
                visualizer, "gradient_mode", True
            ),
            "shadow": lambda visualizer: setattr(
                visualizer.settings.shadow, "enabled", True
            ),
            "peaks": lambda visualizer: setattr(
                visualizer.settings.peak, "enabled", True
            ),
            "overflow disabled": lambda visualizer: setattr(
                visualizer, "overflow_mode", False
            ),
            "bars disabled": lambda visualizer: setattr(
                visualizer, "bars_enabled", False
            ),
            "full mode": lambda visualizer: setattr(visualizer, "full_mode", True),
            "debug mode": lambda visualizer: setattr(
                visualizer, "debug_mode", True
            ),
        }

        for name, mutate in cases.items():
            with self.subTest(name=name):
                visualizer = _qualification_visualizer()
                mutate(visualizer)
                width = 63 if name == "non-64 width" else WIDTH
                height = 63 if name == "non-64 height" else HEIGHT
                with self.assertRaises(ValueError):
                    compile_pillow_bulk_bars_output(visualizer, width, height)

    def test_output_selection_and_compilation_stay_outside_the_frame_loop(self) -> None:
        source = MAIN_PATH.read_text(encoding="utf-8")
        setup, loop = source.split("            while True:", maxsplit=1)
        layer_plan_source = (
            FFT_ROOT / "core" / "layer_plan.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "os.environ.get(OUTPUT_PATH_ENVIRONMENT, DIRECT_OUTPUT_PATH)",
            setup,
        )
        self.assertIn("compile_current_bars_layer_plan(", setup)
        self.assertIn("compile_pillow_bulk_bars_output(", layer_plan_source)
        self.assertIn("frame_plan.draw_frame(app.canvas)", loop)
        self.assertNotIn("OUTPUT_PATH_ENVIRONMENT", loop)
        self.assertNotIn("compile_pillow_bulk_bars_output(", loop)

    def test_candidate_draw_does_not_construct_configuration_or_messages(self) -> None:
        source = inspect.getsource(PillowBulkBarsOutputPlan.draw)

        for forbidden in (
            "Settings(",
            "dict(",
            "dataclass",
            "compile_",
            "registry",
            "Message(",
            "compositor",
            "get_overflow_color(",
        ):
            self.assertNotIn(forbidden, source)


class DirectBarsOutputTests(unittest.TestCase):
    def test_direct_renderer_remains_independent_of_bulk_output(self) -> None:
        visualizer = _qualification_visualizer()
        canvas = _DirectCanvas()
        bars = np.zeros(WIDTH, dtype=np.float32)
        bars[0] = 0.5

        visualizer.draw(canvas, bars, None)

        self.assertEqual(canvas.clear_calls, 1)
        self.assertGreater(canvas.pixel_calls, 0)
        self.assertTrue(np.any(canvas.pixels[:, 0]))
        self.assertFalse(np.any(canvas.pixels[:, 1:]))


if __name__ == "__main__":
    unittest.main()
