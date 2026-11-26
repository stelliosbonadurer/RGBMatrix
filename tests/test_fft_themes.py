"""Hardware-free tests for FFT themes (fft-themes milestones 1 and 2).

``CustomThemeTests`` covers FFT reading its custom-theme file (M2); the web
service's check on the same file is in ``test_program_files``.

``fixtures/theme_baseline_vectors.json`` holds the seven original themes' colors,
recorded from source ``d44f3e5`` before color-stop themes existed. Those themes must
keep returning exactly these colors.
"""

from __future__ import annotations

import colorsys
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest

try:
    import numpy as np
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("FFT theme tests require NumPy") from exc


ROOT = Path(__file__).resolve().parent.parent
FFT_ROOT = ROOT / "my_programs" / "fft_program"
BASELINE = ROOT / "tests" / "fixtures" / "theme_baseline_vectors.json"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))

_sounddevice = ModuleType("sounddevice")
_prior_sounddevice = sys.modules.get("sounddevice")
sys.modules["sounddevice"] = _sounddevice
try:
    from config.settings import Settings  # noqa: E402
    from core.output import DIRECT_OUTPUT_PATH  # noqa: E402
    from core.layer_plan import (  # noqa: E402
        current_bars_settings_descriptor,
        normalize_current_bars_settings,
    )
    from themes import get_theme, list_themes  # noqa: E402
    from themes import registry as theme_registry  # noqa: E402
    from themes.custom import (  # noqa: E402
        DEFAULT_PATH as CUSTOM_THEMES_DEFAULT,
        CustomThemeError,
        load_custom_themes,
        parse_custom_themes,
    )
    from themes.color_stops import (  # noqa: E402
        LUT_SIZE,
        ColorStopError,
        ColorStopTheme,
        build_lut,
        color_stop_theme,
    )
    from visualizers.bars_unified import BarsUnifiedVisualizer  # noqa: E402
    from visualizers.peaks import draw_peaks  # noqa: E402
finally:
    if _prior_sounddevice is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = _prior_sounddevice


ORIGINAL_THEMES = (
    "autumn", "dynamic", "dynamic_lateral_gradient", "forest", "ocean", "rainbow", "warm",
)
VICE_SINGLE_HUE = {
    "vice_pink": 330.0,
    "vice_orange": 22.0,
    "vice_purple": 265.0,
    "vice_teal": 180.0,
}
VICE_THEMES = (*VICE_SINGLE_HUE, "vice_city", "vice_skyline")


def _frozen(theme):
    """Pin a dynamic theme's animation the way the baseline was recorded."""
    if hasattr(theme, "reseed_start_hue"):
        theme.reseed_start_hue(0.2)
        theme.frozen = True
        theme._elapsed_offset = 0.0
    return theme


def _hue_degrees(rgb) -> float:
    h, _s, _v = colorsys.rgb_to_hsv(*(channel / 255 for channel in rgb))
    return h * 360.0


def _hue_distance(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


class OriginalThemesUnchangedTests(unittest.TestCase):
    def test_original_themes_match_recorded_baseline(self) -> None:
        baseline = json.loads(BASELINE.read_text())
        self.assertEqual(sorted(baseline["themes"]), sorted(ORIGINAL_THEMES))
        heights, columns, layers = baseline["heights"], baseline["columns"], baseline["layers"]
        for name, by_boost in baseline["themes"].items():
            for boost, expected in by_boost.items():
                with self.subTest(theme=name, boost=boost):
                    theme = _frozen(get_theme(name, brightness_boost=float(boost)))
                    color = [[list(theme.get_color(h, c)) for c in columns] for h in heights]
                    overflow = [
                        [[list(theme.get_overflow_color(l, h, c, 0, h)) for c in columns] for h in heights]
                        for l in layers
                    ]
                    peak = [list(theme.get_peak_color("peak", (10, 20, 30), c)) for c in columns]
                    self.assertEqual(color, expected["color"])
                    self.assertEqual(overflow, expected["overflow"])
                    self.assertEqual(peak, expected["peak"])

    def test_default_theme_is_still_ocean(self) -> None:
        self.assertEqual(Settings().color.theme, "ocean")
        theme_field = next(
            field for field in current_bars_settings_descriptor()["fields"]
            if field["name"] == "theme"
        )
        self.assertEqual(theme_field["default"], "ocean")


class BuiltinPaletteThemeTests(unittest.TestCase):
    def test_new_themes_are_registered_and_offered_to_the_page(self) -> None:
        names = list_themes()
        descriptor = current_bars_settings_descriptor()
        defaults = {field["name"]: field["default"] for field in descriptor["fields"]}
        theme_field = next(
            field for field in current_bars_settings_descriptor()["fields"]
            if field["name"] == "theme"
        )
        for name in (*ORIGINAL_THEMES, *VICE_THEMES):
            with self.subTest(theme=name):
                self.assertIn(name, names)
                self.assertIn(name, theme_field["choices"])
                self.assertEqual(get_theme(name).name, name)
                normalized = normalize_current_bars_settings(
                    {**defaults, "theme": name}, DIRECT_OUTPUT_PATH
                )
                self.assertEqual(normalized["theme"], name)

    def test_single_hue_themes_stay_in_their_hue(self) -> None:
        for name, hue in VICE_SINGLE_HUE.items():
            theme = get_theme(name)
            for layer in range(4):
                for step in range(0, 11):
                    ratio = step / 10
                    rgb = theme.get_overflow_color(layer, ratio, 0.5)
                    if max(rgb) - min(rgb) < 40:
                        continue  # near-white overflow top has no meaningful hue
                    with self.subTest(theme=name, layer=layer, ratio=ratio):
                        self.assertLess(_hue_distance(_hue_degrees(rgb), hue), 30.0)

    def test_single_hue_themes_brighten_toward_white_on_overflow(self) -> None:
        for name in VICE_SINGLE_HUE:
            with self.subTest(theme=name):
                theme = get_theme(name)
                top = theme.get_color(1.0)
                self.assertEqual(theme.get_overflow_color(1, 0.0), top)
                self.assertGreater(sum(theme.get_overflow_color(1, 1.0)), sum(top))
                self.assertEqual(theme.get_overflow_color(2, 1.0), (255, 255, 255))
                self.assertEqual(theme.get_overflow_color(5, 1.0), (255, 255, 255))

    def test_vice_city_runs_purple_pink_orange_peach_by_height(self) -> None:
        theme = get_theme("vice_city")
        hues = [_hue_degrees(theme.get_color(h)) for h in (0.0, 0.6, 0.82)]
        self.assertLess(_hue_distance(hues[0], 265.0), 30.0)
        self.assertLess(_hue_distance(hues[1], 330.0), 15.0)
        self.assertLess(_hue_distance(hues[2], 22.0), 15.0)
        self.assertEqual(theme.get_color(1.0), (255, 200, 120))
        self.assertEqual(theme.get_color(0.4, 0.0), theme.get_color(0.4, 1.0))

    def test_vice_skyline_runs_teal_purple_pink_orange_across_columns(self) -> None:
        theme = get_theme("vice_skyline")
        for column, hue in ((0.0, 180.0), (0.35, 265.0), (0.68, 330.0), (1.0, 22.0)):
            with self.subTest(column=column):
                self.assertLess(_hue_distance(_hue_degrees(theme.get_color(0.3, column)), hue), 15.0)
        self.assertEqual(theme.get_color(0.0, 0.4), theme.get_color(1.0, 0.4))
        base = theme.get_color(0.5, 0.0)
        washed = theme.get_overflow_color(2, 0.5, 0.0)
        self.assertGreater(min(washed), min(base))

    def test_brightness_boost_is_applied_and_clamped(self) -> None:
        plain = get_theme("vice_teal", brightness_boost=1.0).get_color(0.6)
        boosted = get_theme("vice_teal", brightness_boost=1.3).get_color(0.6)
        self.assertEqual(boosted, tuple(min(255, int(c * 1.3)) for c in plain))


class ColorStopThemeTests(unittest.TestCase):
    def test_lookups_return_prebuilt_colors_without_allocation(self) -> None:
        for name in VICE_THEMES:
            with self.subTest(theme=name):
                theme = get_theme(name)
                self.assertIsInstance(theme, ColorStopTheme)
                self.assertEqual(len(theme._lut), LUT_SIZE)
                self.assertIs(theme.get_color(0.5, 0.5), theme.get_color(0.5, 0.5))
                self.assertIs(
                    theme.get_overflow_color(1, 0.5, 0.5), theme.get_overflow_color(1, 0.5, 0.5)
                )

    def test_out_of_range_ratios_clamp_to_the_ends(self) -> None:
        theme = get_theme("vice_city")
        self.assertEqual(theme.get_color(-0.5), theme.get_color(0.0))
        self.assertEqual(theme.get_color(1.7), theme.get_color(1.0))

    def test_lut_interpolates_between_stops_and_hits_them_exactly(self) -> None:
        lut = build_lut(((0.0, (0, 0, 0)), (1.0, (255, 255, 255))))
        self.assertEqual(lut[0], (0, 0, 0))
        self.assertEqual(lut[-1], (255, 255, 255))
        self.assertEqual(lut[128], (128, 128, 128))

    def test_each_layer_gets_its_own_instance(self) -> None:
        self.assertIsNot(get_theme("vice_pink"), get_theme("vice_pink"))

    def test_overflow_defaults_to_the_main_gradient(self) -> None:
        cls = color_stop_theme("plain", "test", ((0.0, (0, 0, 0)), (1.0, (200, 100, 0))))
        theme = cls()
        self.assertEqual(theme.get_overflow_color(3, 0.5), theme.get_color(0.5))

    def test_invalid_definitions_are_rejected(self) -> None:
        cases = {
            "one stop": dict(stops=((0.0, (0, 0, 0)),)),
            "not starting at 0": dict(stops=((0.2, (0, 0, 0)), (1.0, (1, 1, 1)))),
            "not ending at 1": dict(stops=((0.0, (0, 0, 0)), (0.8, (1, 1, 1)))),
            "decreasing": dict(stops=((0.0, (0, 0, 0)), (0.7, (1, 1, 1)), (0.5, (2, 2, 2)), (1.0, (3, 3, 3)))),
            "channel too big": dict(stops=((0.0, (0, 0, 0)), (1.0, (256, 0, 0)))),
            "bad shape": dict(stops=((0.0, (0, 0)), (1.0, (0, 0, 0)))),
            "bad axis": dict(stops=((0.0, (0, 0, 0)), (1.0, (1, 1, 1))), axis="diagonal"),
            "bad overflow": dict(stops=((0.0, (0, 0, 0)), (1.0, (1, 1, 1))), overflow=(((0.0, (0, 0, 0)),),)),
        }
        for label, kwargs in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ColorStopError):
                    color_stop_theme("bad", "test", **kwargs)


class NanRatioTests(unittest.TestCase):
    """fft-themes D-006: NaN maps to the first color instead of raising."""

    def test_nan_ratios_give_the_first_color(self) -> None:
        for name in VICE_THEMES:
            with self.subTest(theme=name):
                theme = get_theme(name)
                first = theme._lut[0]
                self.assertIs(theme.get_color(math.nan, math.nan), first)
                for layer in (1, 2, 5):
                    lut = theme._overflow_luts[min(layer, len(theme._overflow_luts)) - 1]
                    self.assertIs(theme.get_overflow_color(layer, math.nan, math.nan), lut[0])


def _custom(name="my_sunset", **changes):
    theme = {
        "name": name,
        "description": "Purple to gold",
        "axis": "height",
        "stops": [
            {"position": 0.0, "color": [60, 20, 140]},
            {"position": 0.5, "color": [255, 46, 136]},
            {"position": 1.0, "color": [255, 200, 80]},
        ],
        "overflow": [],
    }
    theme.update(changes)
    return theme


class CustomThemeTests(unittest.TestCase):
    """fft-themes M2 acceptance criteria 2-4 (D-005, D-008-D-010)."""

    def setUp(self) -> None:
        saved = dict(theme_registry._THEME_REGISTRY)

        def restore() -> None:
            theme_registry._THEME_REGISTRY.clear()
            theme_registry._THEME_REGISTRY.update(saved)

        self.addCleanup(restore)
        self.builtins = list_themes()

    def load(self, document, **kwargs):
        stderr = io.StringIO()
        text = document if isinstance(document, str) else json.dumps(document)
        names = load_custom_themes(text, stderr=stderr, **kwargs)
        return names, stderr.getvalue()

    def test_the_tracked_default_has_no_custom_themes(self) -> None:
        document = json.loads(CUSTOM_THEMES_DEFAULT.read_text(encoding="utf-8"))
        self.assertEqual(parse_custom_themes(document), ())
        stderr = io.StringIO()
        self.assertEqual(load_custom_themes(None, stderr=stderr), ())
        self.assertEqual((stderr.getvalue(), list_themes()), ("", self.builtins))

    def test_without_the_override_the_given_file_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "custom_themes.json"
            path.write_text(json.dumps({"themes": [_custom()]}), encoding="utf-8")
            self.assertEqual(load_custom_themes(None, path), ("my_sunset",))
        self.assertIn("my_sunset", list_themes())

    def test_saved_themes_are_offered_to_layers_and_follow_their_stops(self) -> None:
        overflow = [[{"position": 0.0, "color": [255, 255, 255]},
                     {"position": 1.0, "color": [255, 0, 0]}]]
        names, stderr = self.load({"themes": [
            _custom(),
            _custom("my_skyline", axis="column", overflow=overflow),
        ]})
        self.assertEqual((names, stderr), (("my_sunset", "my_skyline"), ""))
        self.assertEqual(list_themes(), sorted([*self.builtins, *names]))
        descriptor = current_bars_settings_descriptor()
        defaults = {field["name"]: field["default"] for field in descriptor["fields"]}
        theme_field = next(f for f in descriptor["fields"] if f["name"] == "theme")
        for name in names:
            with self.subTest(theme=name):
                self.assertIn(name, theme_field["choices"])
                normalized = normalize_current_bars_settings(
                    {**defaults, "theme": name}, DIRECT_OUTPUT_PATH
                )
                self.assertEqual(normalized["theme"], name)
                self.assertIsInstance(get_theme(name), ColorStopTheme)

        sunset = get_theme("my_sunset", brightness_boost=1.0)
        self.assertEqual(sunset.get_color(0.0, 0.9), (60, 20, 140))
        self.assertEqual(sunset.get_color(0.5, 0.0), sunset._lut[127])
        self.assertEqual(sunset.get_color(1.0, 0.1), (255, 200, 80))
        self.assertEqual(sunset._lut, build_lut(
            ((0.0, (60, 20, 140)), (0.5, (255, 46, 136)), (1.0, (255, 200, 80)))
        ))
        skyline = get_theme("my_skyline")
        self.assertEqual(skyline.get_color(0.9, 0.0), (60, 20, 140))
        self.assertEqual(skyline.get_overflow_color(1, 0.0, 1.0), (255, 0, 0))
        self.assertEqual(skyline.get_overflow_color(3, 0.0, 0.0), (255, 255, 255))
        self.assertEqual(get_theme("my_sunset").get_color(math.nan), (60, 20, 140))

    def test_an_unusable_document_prints_one_line_and_registers_nothing(self) -> None:
        cases = (
            ("{", "Expecting"),
            ("NaN", "NaN is not valid JSON"),
            (json.dumps({"themes": [_custom(), _custom("ocean")]}), "must be my_"),
            (json.dumps({"themes": [_custom(stops=[])]}), "2 to 16 stops"),
        )
        for text, message in cases:
            with self.subTest(message=message):
                names, stderr = self.load(text)
                self.assertEqual(names, ())
                self.assertEqual(len(stderr.splitlines()), 1)
                self.assertIn(message, stderr)
                self.assertIn("using built-in themes only", stderr)
                self.assertEqual(list_themes(), self.builtins)

    def test_an_unreadable_default_file_prints_one_line(self) -> None:
        stderr = io.StringIO()
        missing = Path(tempfile.gettempdir()) / "no-such-dir-fft-themes" / "x.json"
        self.assertEqual(load_custom_themes(None, missing, stderr=stderr), ())
        self.assertIn(str(missing), stderr.getvalue())
        self.assertEqual(list_themes(), self.builtins)

    def test_a_registered_name_is_never_replaced(self) -> None:
        self.assertEqual(self.load({"themes": [_custom()]})[0], ("my_sunset",))
        first = theme_registry._THEME_REGISTRY["my_sunset"]
        names, stderr = self.load({"themes": [_custom("my_other"), _custom()]})
        self.assertEqual(names, ())
        self.assertIn("already registered: my_sunset", stderr)
        self.assertIs(theme_registry._THEME_REGISTRY["my_sunset"], first)
        self.assertNotIn("my_other", list_themes())

    def test_builtin_names_cannot_be_custom(self) -> None:
        for name in self.builtins:
            with self.subTest(theme=name), self.assertRaisesRegex(CustomThemeError, "must be my_"):
                parse_custom_themes({"themes": [_custom(name)]})

    def test_fft_registers_the_override_before_listing_themes(self) -> None:
        # main.py end to end, with the audio library stubbed as above.
        script = (
            "import runpy, sys, types\n"
            "sys.modules['sounddevice'] = types.ModuleType('sounddevice')\n"
            "sys.argv = ['main.py', '--list-themes']\n"
            "runpy.run_path('main.py', run_name='__main__')\n"
        )
        for override, listed, warned in (
            (json.dumps({"themes": [_custom()]}), True, False),
            ("{", False, True),
            (None, False, False),  # no variable: the tracked default, no custom themes
        ):
            with self.subTest(override=override):
                environ = {k: v for k, v in os.environ.items() if k != "RGB_MATRIX_PROGRAM_CONFIG"}
                if override is not None:
                    environ["RGB_MATRIX_PROGRAM_CONFIG"] = override
                result = subprocess.run(
                    [sys.executable, "-c", script], cwd=FFT_ROOT, env=environ,
                    capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual("  - my_sunset" in result.stdout.splitlines(), listed)
                self.assertIn("  - ocean", result.stdout.splitlines())
                self.assertEqual("Custom themes not loaded" in result.stderr, warned)


class _Canvas:
    def __init__(self, width: int, height: int) -> None:
        self.pixels = np.zeros((height, width, 3), dtype=np.uint8)

    def Clear(self) -> None:
        self.pixels.fill(0)

    def SetPixel(self, x, y, r, g, b) -> None:
        self.pixels[y, x] = (r, g, b)


class PaletteThemeRenderingTests(unittest.TestCase):
    def test_new_themes_render_through_the_direct_bars_path(self) -> None:
        width = height = 64
        bars = np.linspace(0.05, 2.4, width).astype(np.float32)
        for name in VICE_THEMES:
            for gradient in (False, True):
                for overflow in (False, True):
                    with self.subTest(theme=name, gradient=gradient, overflow=overflow):
                        settings = Settings()
                        settings.shadow.enabled = False
                        settings.peak.enabled = True
                        settings.peak.color_mode = "peak"
                        visualizer = BarsUnifiedVisualizer(width, height, settings)
                        theme = get_theme(name)
                        visualizer.set_theme(theme)
                        visualizer.gradient_mode = gradient
                        visualizer.overflow_mode = overflow
                        visualizer.bars_enabled = True
                        visualizer.full_mode = False
                        canvas = _Canvas(width, height)
                        visualizer.draw(canvas, bars)
                        draw_peaks(canvas, np.clip(bars, 0.0, 1.0), theme, settings, height)
                        self.assertGreater(int(canvas.pixels.any(axis=2).sum()), 0)


if __name__ == "__main__":
    unittest.main()
