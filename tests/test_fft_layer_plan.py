"""Hardware-free tests for the Step 3 in-process ordered layer plan."""

from __future__ import annotations

import copy
import inspect
from pathlib import Path
import sys
from types import ModuleType
import unittest

try:
    import numpy as np
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("FFT layer-plan tests require NumPy") from exc

try:
    from PIL import Image as _PillowImage  # noqa: F401
except ModuleNotFoundError:
    _PillowImage = None


FFT_ROOT = Path(__file__).resolve().parent.parent / "my_programs" / "fft_program"
MAIN_PATH = FFT_ROOT / "main.py"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))


_sounddevice = ModuleType("sounddevice")
_prior_sounddevice = sys.modules.get("sounddevice")
sys.modules["sounddevice"] = _sounddevice
try:
    from config.settings import Settings  # noqa: E402
    from core.analyzer_registry import (  # noqa: E402
        AnalyzerRegistry,
        STANDARD_ANALYZER_TYPE,
        standard_analyzer_capabilities,
        standard_analyzer_definition,
    )
    from core.layer_plan import (  # noqa: E402
        ActiveLayerPlan,
        LayerRuntimeComponents,
        compile_current_bars_graph,
        compile_current_bars_layer_plan,
        compile_ordered_layer_plan,
        current_bars_settings_descriptor,
        current_bars_settings_from_runtime,
        normalize_current_bars_settings,
    )
    from core.output import (  # noqa: E402
        DIRECT_OUTPUT_PATH,
        PILLOW_BULK_OUTPUT_PATH,
        compile_pillow_bulk_bars_output,
    )
    from core.runtime_graph import (  # noqa: E402
        LayerCapability,
        LayerDefinition,
        RuntimeCapabilities,
        RuntimeDefinition,
        RuntimeGraphValidationError,
        compile_runtime_graph,
    )
    from core.scaling import ScalingProcessor  # noqa: E402
    from themes import get_theme  # noqa: E402
    from visualizers.bars_unified import BarsUnifiedVisualizer  # noqa: E402
    from visualizers.peaks import draw_peaks  # noqa: E402
finally:
    if _prior_sounddevice is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = _prior_sounddevice


class RecordingAnalyzer:
    def __init__(self, result: np.ndarray) -> None:
        self.result = result
        self.get_calls = 0
        self.release_calls = 0
        self.leased = False

    def setup(self) -> int:
        return 44_100

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def get_fft_magnitudes(self):
        if self.leased:
            raise RuntimeError("result already leased")
        self.get_calls += 1
        self.leased = True
        return self.result

    def release_fft_magnitudes(self) -> None:
        if not self.leased:
            raise RuntimeError("no result lease")
        self.release_calls += 1
        self.leased = False


class PixelCanvas:
    def __init__(self, width: int, height: int) -> None:
        self.pixels = np.zeros((height, width, 3), dtype=np.uint8)
        self.clear_calls = 0
        self.pixel_calls = 0

    def Clear(self) -> None:
        self.clear_calls += 1
        self.pixels.fill(0)

    def SetPixel(self, x, y, r, g, b) -> None:
        self.pixel_calls += 1
        self.pixels[y, x] = (r, g, b)

    def SetImage(self, *_args, **_kwargs) -> None:
        raise AssertionError("direct output must not call SetImage")


class BulkCanvas:
    def __init__(self, width: int, height: int) -> None:
        self.pixels = np.zeros((height, width, 3), dtype=np.uint8)
        self.set_image_calls = 0
        self.images = []

    def SetImage(self, image, *_args, **_kwargs) -> None:
        self.set_image_calls += 1
        self.images.append(image)
        self.pixels = np.array(image, dtype=np.uint8, copy=True)

    def Clear(self) -> None:
        raise AssertionError("pillow-bulk must clear its reusable image")

    def SetPixel(self, *_args, **_kwargs) -> None:
        raise AssertionError("pillow-bulk must not fall back to SetPixel")


def _standard_definition(settings: Settings, analyzer_id: str):
    return standard_analyzer_definition(
        analyzer_id,
        audio_settings=settings.audio,
        frequency_settings=settings.frequency,
        sensitivity_settings=settings.sensitivity,
        num_bins=4,
    )


def _test_graph(
    layer_specs: tuple[tuple[str, str, bool], ...],
    *,
    analyzer_ids: tuple[str, ...] = ("shared",),
):
    settings = Settings()
    analyzers = tuple(
        _standard_definition(settings, analyzer_id)
        for analyzer_id in analyzer_ids
    )
    capabilities = RuntimeCapabilities(
        analyzers=standard_analyzer_capabilities().analyzers,
        layers=(
            LayerCapability(
                layer_type="recording-layer",
                analyzer_types=frozenset({STANDARD_ANALYZER_TYPE}),
                output_backends=frozenset({DIRECT_OUTPUT_PATH}),
            ),
        ),
    )
    return compile_runtime_graph(
        RuntimeDefinition(
            analyzers=analyzers,
            layers=tuple(
                LayerDefinition(
                    layer_id=layer_id,
                    layer_type="recording-layer",
                    analyzer_id=analyzer_id,
                    visible=visible,
                )
                for layer_id, analyzer_id, visible in layer_specs
            ),
            output_backend=DIRECT_OUTPUT_PATH,
        ),
        capabilities,
    )


def _recording_plan(graph, analyzer, events):
    registry = AnalyzerRegistry(
        graph,
        {STANDARD_ANALYZER_TYPE: lambda _definition: analyzer},
    )
    states = {}

    def factory(definition, _runtime, _backend):
        state = {
            "layer_id": definition.layer_id,
            "prepare_count": 0,
            "result_ids": [],
            "smoothed": np.zeros(4, dtype=np.float32),
        }
        states[definition.layer_id] = state

        def prepare(result):
            state["prepare_count"] += 1
            state["result_ids"].append(id(result))
            np.copyto(state["smoothed"], result)
            events.append(("prepare", definition.layer_id))

        def draw(_canvas):
            events.append(("draw", definition.layer_id))

        return LayerRuntimeComponents(
            presentation_state=state,
            prepare=prepare,
            draw=draw,
            smoothed_bars=lambda: state["smoothed"],
        )

    plan = compile_ordered_layer_plan(
        graph,
        registry,
        {"recording-layer": factory},
        begin_draw=lambda _canvas: events.append(("clear",)),
    )
    return plan, states


class OrderedLayerExecutionTests(unittest.TestCase):
    def test_exact_order_visibility_and_one_shared_analyzer_lease(self) -> None:
        graph = _test_graph(
            (
                ("bottom", "primary", True),
                ("hidden", "alias", False),
                ("top", "alias", True),
            ),
            analyzer_ids=("primary", "alias"),
        )
        analyzer = RecordingAnalyzer(np.arange(4, dtype=np.float32))
        events = []
        plan, states = _recording_plan(graph, analyzer, events)

        actual = plan.prepare_frame()
        plan.draw_frame(object())

        self.assertEqual([layer.layer_id for layer in plan.layers], [
            "bottom", "hidden", "top"
        ])
        self.assertEqual([layer.layer_id for layer in plan.visible_layers], [
            "bottom", "top"
        ])
        self.assertEqual(events, [
            ("prepare", "bottom"),
            ("prepare", "top"),
            ("clear",),
            ("draw", "bottom"),
            ("draw", "top"),
        ])
        self.assertEqual(states["hidden"]["prepare_count"], 0)
        self.assertEqual(analyzer.get_calls, 1)
        self.assertEqual(analyzer.release_calls, 1)
        self.assertFalse(analyzer.leased)
        self.assertIs(actual, states["bottom"]["smoothed"])

    def test_shared_result_keeps_layer_presentation_state_independent(self) -> None:
        graph = _test_graph(
            (("first", "shared", True), ("second", "shared", True))
        )
        analyzer = RecordingAnalyzer(np.ones(4, dtype=np.float32))
        plan, states = _recording_plan(graph, analyzer, [])

        plan.prepare_frame()

        self.assertIsNot(states["first"], states["second"])
        self.assertIsNot(states["first"]["smoothed"], states["second"]["smoothed"])
        self.assertEqual(
            states["first"]["result_ids"],
            states["second"]["result_ids"],
        )
        self.assertEqual(states["first"]["prepare_count"], 1)
        self.assertEqual(states["second"]["prepare_count"], 1)

    def test_analyzer_lease_is_released_when_layer_preparation_fails(self) -> None:
        graph = _test_graph((("broken", "shared", True),))
        analyzer = RecordingAnalyzer(np.ones(4, dtype=np.float32))
        registry = AnalyzerRegistry(
            graph,
            {STANDARD_ANALYZER_TYPE: lambda _definition: analyzer},
        )

        def factory(_definition, _runtime, _backend):
            def fail(_result):
                raise RuntimeError("injected preparation failure")

            return LayerRuntimeComponents(
                presentation_state=object(),
                prepare=fail,
                draw=lambda _canvas: None,
                smoothed_bars=lambda: np.zeros(4, dtype=np.float32),
            )

        plan = compile_ordered_layer_plan(
            graph,
            registry,
            {"recording-layer": factory},
            begin_draw=lambda _canvas: None,
        )

        with self.assertRaisesRegex(RuntimeError, "preparation failure"):
            plan.prepare_frame()

        self.assertEqual(analyzer.release_calls, 1)
        self.assertFalse(analyzer.leased)


class PlanReplacementTests(unittest.TestCase):
    def test_complete_candidate_activates_only_at_next_frame_boundary(self) -> None:
        analyzer = RecordingAnalyzer(np.ones(4, dtype=np.float32))
        first, _ = _recording_plan(
            _test_graph((
                ("bottom", "shared", True),
                ("remove-me", "shared", True),
            )), analyzer, []
        )
        second, _ = _recording_plan(
            _test_graph((
                ("added-top", "shared", True),
                ("bottom", "shared", True),
            )), analyzer, []
        )
        owner = ActiveLayerPlan(first)

        current_frame = owner.begin_frame()
        owner.stage(second)

        self.assertIs(current_frame, first)
        self.assertIs(owner.active, first)
        self.assertIs(owner.pending, second)
        self.assertIs(owner.begin_frame(), second)
        self.assertIs(owner.active, second)
        self.assertIsNone(owner.pending)
        self.assertEqual(
            [layer.layer_id for layer in owner.active.visible_layers],
            ["added-top", "bottom"],
        )

    def test_invalid_candidate_leaves_prior_plan_active(self) -> None:
        analyzer = RecordingAnalyzer(np.ones(4, dtype=np.float32))
        active, _ = _recording_plan(
            _test_graph((("active", "shared", True),)), analyzer, []
        )
        owner = ActiveLayerPlan(active)
        settings = Settings()
        capabilities = RuntimeCapabilities(
            analyzers=standard_analyzer_capabilities().analyzers,
            layers=(
                LayerCapability(
                    layer_type="recording-layer",
                    analyzer_types=frozenset({STANDARD_ANALYZER_TYPE}),
                    output_backends=frozenset({DIRECT_OUTPUT_PATH}),
                ),
            ),
        )

        with self.assertRaises(RuntimeGraphValidationError):
            compile_runtime_graph(
                RuntimeDefinition(
                    analyzers=(_standard_definition(settings, "present"),),
                    layers=(
                        LayerDefinition(
                            "invalid", "recording-layer", "missing"
                        ),
                    ),
                    output_backend=DIRECT_OUTPUT_PATH,
                ),
                capabilities,
            )

        self.assertIs(owner.active, active)
        self.assertIsNone(owner.pending)
        with self.assertRaises(TypeError):
            owner.stage(object())
        self.assertIs(owner.active, active)


def _direct_settings() -> Settings:
    settings = Settings()
    settings.shadow.enabled = False
    settings.peak.enabled = True
    settings.overflow.enabled = True
    settings.color.theme = "ocean"
    return settings


def _bulk_settings() -> Settings:
    settings = Settings()
    settings.shadow.enabled = False
    settings.peak.enabled = False
    settings.overflow.enabled = True
    settings.color.theme = "ocean"
    return settings


def _visualizer(width: int, height: int, settings: Settings):
    visualizer = BarsUnifiedVisualizer(width, height, settings)
    visualizer.set_theme(get_theme("ocean"))
    visualizer.gradient_mode = False
    visualizer.bars_enabled = True
    visualizer.full_mode = False
    return visualizer


def _scaler(settings: Settings, width: int) -> ScalingProcessor:
    return ScalingProcessor(
        settings.scaling,
        settings.smoothing,
        width,
        frame_rate=1.0 / settings.audio.sleep_delay,
    )


def _current_plan(
    settings: Settings,
    analyzer: RecordingAnalyzer,
    backend: str,
    width: int,
    height: int,
):
    graph = compile_current_bars_graph(
        audio_settings=settings.audio,
        frequency_settings=settings.frequency,
        sensitivity_settings=settings.sensitivity,
        num_bins=width,
        output_backend=backend,
        # The layer definition is authoritative for presentation, so it must be
        # built from the same settings the legacy reference pipeline uses.
        settings=settings,
    )
    registry = AnalyzerRegistry(
        graph,
        {STANDARD_ANALYZER_TYPE: lambda _definition: analyzer},
    )
    return compile_current_bars_layer_plan(
        graph,
        registry,
        settings=settings,
        width=width,
        height=height,
    )


class CurrentSceneEquivalenceTests(unittest.TestCase):
    def test_direct_bars_and_internal_peaks_exactly_match_legacy_sequence(self) -> None:
        width = height = 8
        legacy_settings = _direct_settings()
        planned_settings = copy.deepcopy(legacy_settings)
        legacy_visualizer = _visualizer(width, height, legacy_settings)
        legacy_scaler = _scaler(legacy_settings, width)
        raw_frames = (
            np.linspace(0.0, 0.7, width, dtype=np.float32),
            np.linspace(0.8, 0.1, width, dtype=np.float32),
        )
        analyzer = RecordingAnalyzer(raw_frames[0])
        plan = _current_plan(
            planned_settings,
            analyzer,
            DIRECT_OUTPUT_PATH,
            width,
            height,
        )
        legacy_canvas = PixelCanvas(width, height)
        planned_canvas = PixelCanvas(width, height)

        for raw in raw_frames:
            analyzer.result = raw
            _normalized, smoothed, peaks = legacy_scaler.process(
                raw,
                peak_hold_frames=legacy_settings.peak.hold_frames,
                peak_fall_speed=legacy_settings.peak.fall_speed,
            )
            legacy_visualizer.draw(legacy_canvas, smoothed, None)
            draw_peaks(
                legacy_canvas,
                peaks,
                legacy_visualizer.theme,
                legacy_settings,
                height,
            )

            actual_smoothed = plan.prepare_frame()
            plan.draw_frame(planned_canvas)

            np.testing.assert_array_equal(actual_smoothed, smoothed)
            np.testing.assert_array_equal(planned_canvas.pixels, legacy_canvas.pixels)

        self.assertEqual(planned_canvas.clear_calls, len(raw_frames))
        self.assertEqual(analyzer.get_calls, len(raw_frames))
        self.assertEqual(analyzer.release_calls, len(raw_frames))
        # The compiler builds every layer's own presentation objects.
        presentation = plan.layers[0].presentation_state
        self.assertIsNot(presentation.visualizer, legacy_visualizer)
        self.assertIsNot(presentation.scaler, legacy_scaler)

    @unittest.skipUnless(_PillowImage is not None, "requires Pillow")
    def test_pillow_bulk_scene_matches_existing_reusable_output(self) -> None:
        width = height = 64
        legacy_settings = _bulk_settings()
        planned_settings = copy.deepcopy(legacy_settings)
        legacy_visualizer = _visualizer(width, height, legacy_settings)
        legacy_scaler = _scaler(legacy_settings, width)
        legacy_output = compile_pillow_bulk_bars_output(
            legacy_visualizer, width, height
        )
        raw = np.linspace(0.0, 1.5, width, dtype=np.float32)
        analyzer = RecordingAnalyzer(raw)
        plan = _current_plan(
            planned_settings,
            analyzer,
            PILLOW_BULK_OUTPUT_PATH,
            width,
            height,
        )
        legacy_canvas = BulkCanvas(width, height)
        planned_canvas = BulkCanvas(width, height)

        _normalized, smoothed, _peaks = legacy_scaler.process(
            raw,
            peak_hold_frames=legacy_settings.peak.hold_frames,
            peak_fall_speed=legacy_settings.peak.fall_speed,
        )
        legacy_output.draw(legacy_canvas, smoothed, None)
        plan.prepare_frame()
        plan.draw_frame(planned_canvas)

        np.testing.assert_array_equal(planned_canvas.pixels, legacy_canvas.pixels)
        self.assertEqual(planned_canvas.set_image_calls, 1)
        self.assertEqual(len(planned_canvas.images), 1)


class LayerPlanHotPathTests(unittest.TestCase):
    def test_backend_and_registry_work_are_compiled_outside_main_loop(self) -> None:
        source = MAIN_PATH.read_text(encoding="utf-8")
        setup, loop = source.split("            while True:", maxsplit=1)

        self.assertIn("current_bars_runtime_definition(", setup)
        self.assertIn("compile_runtime_graph(", setup)
        self.assertIn("compile_current_bars_layer_plan(", setup)
        self.assertIn("ActiveLayerPlan(initial_layer_plan)", setup)
        self.assertIn("frame_plan.prepare_frame()", loop)
        self.assertIn("frame_plan.draw_frame(app.canvas)", loop)
        # D-016: the launch structure is applied after audio setup, before the
        # control listener can start a transaction and before the first frame.
        launch = setup.index("apply_launch_structure(")
        self.assertLess(setup.index("with ShutdownSignalHandler(), capture:"), launch)
        self.assertLess(launch, setup.index("runtime_thread.start()"))
        self.assertNotIn("apply_launch_structure(", loop)
        for forbidden in (
            "current_bars_runtime_definition(",
            "compile_runtime_graph(",
            "compile_current_bars_layer_plan(",
            "analyzer_registry.resolve(",
            "OUTPUT_PATH_ENVIRONMENT",
            "PILLOW_BULK_OUTPUT_PATH",
        ):
            self.assertNotIn(forbidden, loop)

    def test_frame_methods_do_not_build_commands_or_resolve_control_ids(self) -> None:
        prepare_source = inspect.getsource(
            sys.modules["core.layer_plan"].OrderedLayerPlan.prepare_frame
        )
        draw_source = inspect.getsource(
            sys.modules["core.layer_plan"].OrderedLayerPlan.draw_frame
        )

        for forbidden in (
            "resolve(",
            "compile_",
            "LayerRuntimeComponents(",
            "dict(",
            "command",
            "compositor",
            "output_backend ==",
        ):
            self.assertNotIn(forbidden, prepare_source)
            self.assertNotIn(forbidden, draw_source)


class CurrentBarsDescriptorTests(unittest.TestCase):
    def test_every_field_has_help_and_the_defaults_normalize(self) -> None:
        descriptor = current_bars_settings_descriptor()
        fields = descriptor["fields"]
        for field in fields:
            with self.subTest(field=field["name"]):
                self.assertIsInstance(field.get("help"), str)
                self.assertTrue(field["help"].strip())
        defaults = {field["name"]: field["default"] for field in fields}
        self.assertEqual(normalize_current_bars_settings(defaults, DIRECT_OUTPUT_PATH), defaults)
        self.assertEqual(defaults, current_bars_settings_from_runtime(Settings()))

    def test_every_field_has_a_known_group_and_a_label(self) -> None:
        # M5 slice 1 criterion 4 (D-053): the page draws headings and names from
        # the descriptor alone.
        descriptor = current_bars_settings_descriptor()
        group_ids = [group["id"] for group in descriptor["groups"]]
        self.assertEqual(group_ids, ["range", "look", "peaks", "motion", "dynamic"])
        self.assertTrue(all(group["label"].strip() for group in descriptor["groups"]))
        labels = []
        for field in descriptor["fields"]:
            with self.subTest(field=field["name"]):
                self.assertIn(field["group"], group_ids)
                self.assertTrue(field["label"].strip())
                labels.append(field["label"])
        self.assertEqual(len(labels), len(set(labels)))
        used = {field["group"] for field in descriptor["fields"]}
        self.assertEqual(used, set(group_ids))

    def test_show_conditions_name_real_fields_and_allowed_values(self) -> None:
        fields = {
            field["name"]: field
            for field in current_bars_settings_descriptor()["fields"]
        }
        conditional = {
            name: field["shown_when"]
            for name, field in fields.items()
            if "shown_when" in field
        }
        self.assertEqual(
            {name: condition["field"] for name, condition in conditional.items()},
            {
                "peak_color_mode": "peak_enabled",
                "peak_fall_speed": "peak_enabled",
                "peak_hold_frames": "peak_enabled",
                "dynamic_cycle_speed": "theme",
                "dynamic_frozen": "theme",
            },
        )
        for name, condition in conditional.items():
            with self.subTest(field=name):
                self.assertEqual(set(condition), {"field", "values"})
                target = fields[condition["field"]]
                self.assertNotEqual(condition["field"], name)
                self.assertNotIn("shown_when", target)
                self.assertTrue(condition["values"])
                if target["type"] == "boolean":
                    allowed = [True, False]
                elif target["type"] == "choice":
                    allowed = target["choices"]
                else:
                    self.fail(f"{name} depends on a {target['type']} field")
                for value in condition["values"]:
                    self.assertIn(value, allowed)
        self.assertEqual(
            fields["dynamic_frozen"]["shown_when"]["values"],
            ["dynamic", "dynamic_lateral_gradient"],
        )

    def test_weight_points_field_describes_its_items(self) -> None:
        fields = {
            field["name"]: field
            for field in current_bars_settings_descriptor()["fields"]
        }
        self.assertNotIn("low_freq_weight", fields)
        self.assertNotIn("high_freq_weight", fields)
        points = fields["weight_points"]
        self.assertEqual(points["type"], "points")
        self.assertEqual((points["min_items"], points["max_items"]), (1, 16))
        self.assertEqual(
            [
                (item["name"], item["min"], item["max"], bool(item["help"]))
                for item in points["item_fields"]
            ],
            [("position", 0.0, 1.0, True), ("weight", 0.0, 100.0, True)],
        )
        self.assertEqual(
            [point["position"] for point in points["default"]],
            [0.0, 0.1, 0.3, 0.6, 1.0],
        )


if __name__ == "__main__":
    unittest.main()
