"""Focused tests for bounded FFT structural controls."""

from __future__ import annotations

from dataclasses import replace
import inspect
import json
from pathlib import Path
import sys
import threading
import time
from types import ModuleType
import unittest

try:
    import numpy as np
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("FFT structural tests require NumPy") from exc


FFT_ROOT = Path(__file__).resolve().parent.parent / "my_programs" / "fft_program"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))

# Production code imports core.layer_plan lazily, so core stays cached after this
# import and core.audio may keep this stub for the rest of the run. Match the
# analyzer-registry stub so real AudioProcessor.setup() tests pass in any order.
_sounddevice = ModuleType("sounddevice")
_sounddevice.query_devices = lambda *_args, **_kwargs: {
    "name": "test input",
    "default_samplerate": 44_100,
}
_sounddevice.InputStream = object
_prior_sounddevice = sys.modules.get("sounddevice")
sys.modules["sounddevice"] = _sounddevice
try:
    from config import Settings  # noqa: E402
    from core.analyzer_registry import (  # noqa: E402
        AnalyzerRegistry,
        STANDARD_ANALYZER_TYPE,
    )
    from core.layer_plan import (  # noqa: E402
        ActiveLayerPlan,
        compile_current_bars_layer_plan,
        current_bars_runtime_capabilities,
        current_bars_runtime_definition,
        current_bars_settings_descriptor,
        current_bars_settings_from_runtime,
    )
    from core.output import DIRECT_OUTPUT_PATH, PILLOW_BULK_OUTPUT_PATH  # noqa: E402
    from core.runtime_graph import (  # noqa: E402
        LayerDefinition,
        compile_runtime_graph,
        runtime_capabilities_to_dict,
        runtime_definition_to_dict,
    )
    from matrix_controller.runtime_transport import (  # noqa: E402
        FrameBoundaryRuntimeBridge,
        RuntimeControlError,
    )
    from matrix_controller.supervisor import VisualizationSupervisor  # noqa: E402
    from runtime_control import (  # noqa: E402
        FftRuntimeController,
        RuntimeCommand,
        bounded_dynamic_cycle_speed,
    )
    from structural_control import (  # noqa: E402
        StructuralRuntimeCoordinator,
        StructuralValidationError,
        apply_structural_operations,
    )
    from themes import get_theme  # noqa: E402
    from tools.fft_layers import build_structure_action  # noqa: E402
    from visualizers.bars_unified import BarsUnifiedVisualizer  # noqa: E402
finally:
    if _prior_sounddevice is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = _prior_sounddevice


class _Analyzer:
    def __init__(self, width: int) -> None:
        self.result = np.linspace(0.0, 1.0, width, dtype=np.float32)
        self.get_calls = 0
        self.release_calls = 0
        self.last_acquired_analysis_sequence = 0
        self.last_acquired_analysis_publish_time_ns = 0
        self.projections = []

    def setup(self): return 44_100
    def start(self): pass
    def stop(self): pass

    def get_fft_magnitudes(self):
        self.get_calls += 1
        return self.result

    def release_fft_magnitudes(self):
        self.release_calls += 1

    def create_projection(self, _candidate, _noise_floor):
        projection = _Analyzer(len(self.result))
        self.projections.append(projection)
        return projection

    def remove_projection(self, projection):
        self.projections.remove(projection)


class _AudioAdapter:
    def __init__(self) -> None:
        self.analysis_candidate = type("Candidate", (), {
            "min_freq": 100,
            "max_freq": 6300,
            "low_freq_weight": 0.45,
            "high_freq_weight": 10.0,
        })()

    def activate_analysis_inputs(self): pass
    def compile_analysis_candidate(self, candidate): return candidate
    def activate_analysis_plan(self, candidate, _plan): self.analysis_candidate = candidate
    def update_frequency_range(self): pass


class _Cycler:
    def __init__(self) -> None:
        self.themes = ["ocean", "warm", "forest"]
        self.current_index = 0

    @property
    def current_theme_name(self): return self.themes[self.current_index]

    def next_theme(self):
        self.current_index = (self.current_index + 1) % len(self.themes)
        return get_theme(self.current_theme_name)

    def prev_theme(self):
        self.current_index = (self.current_index - 1) % len(self.themes)
        return get_theme(self.current_theme_name)


class _DirectCanvas:
    def __init__(self) -> None:
        self.clear_calls = 0
        self.pixel_calls = 0

    def Clear(self): self.clear_calls += 1
    def SetPixel(self, *_args): self.pixel_calls += 1


class _RecordingCanvas(_DirectCanvas):
    def __init__(self) -> None:
        super().__init__()
        self.pixels = {}

    def SetPixel(self, x, y, r, g, b):
        self.pixel_calls += 1
        self.pixels[(x, y)] = (r, g, b)


class _BulkCanvas:
    def __init__(self) -> None:
        self.set_image_calls = 0
        self.images = []

    def SetImage(self, image):
        self.set_image_calls += 1
        self.images.append(image)

    def Clear(self): raise AssertionError("bulk output must not clear native canvas")
    def SetPixel(self, *_args): raise AssertionError("bulk output must not use SetPixel")


def _runtime(backend: str = DIRECT_OUTPUT_PATH, width: int = 8):
    settings = Settings()
    settings.shadow.enabled = False
    settings.peak.enabled = False
    settings.color.theme = "ocean"
    visualizer = BarsUnifiedVisualizer(width, width, settings)
    visualizer.set_theme(get_theme("ocean"))
    layer_settings = current_bars_settings_from_runtime(settings, visualizer)
    if backend == PILLOW_BULK_OUTPUT_PATH:
        width = 64
        visualizer = BarsUnifiedVisualizer(width, width, settings)
        visualizer.set_theme(get_theme("ocean"))
        layer_settings = current_bars_settings_from_runtime(settings, visualizer)
    definition = current_bars_runtime_definition(
        audio_settings=settings.audio,
        frequency_settings=settings.frequency,
        sensitivity_settings=settings.sensitivity,
        num_bins=width,
        output_backend=backend,
        layer_settings=layer_settings,
    )
    capabilities = current_bars_runtime_capabilities()
    graph = compile_runtime_graph(definition, capabilities)
    analyzer = _Analyzer(width)
    registry = AnalyzerRegistry(
        graph,
        {STANDARD_ANALYZER_TYPE: lambda _definition: analyzer},
    )
    plan = compile_current_bars_layer_plan(
        graph,
        registry,
        visualizer=visualizer,
        settings=settings,
        width=width,
        height=width,
        theme_cycler=_Cycler(),
    )
    controller = FftRuntimeController(
        settings=settings,
        visualizer=visualizer,
        audio=_AudioAdapter(),
        theme_cycler=_Cycler(),
        width=width,
        height=width,
        run_id="structural-test",
        unavailable_command_paths=plan.incompatible_runtime_paths,
    )
    return settings, definition, capabilities, registry, analyzer, plan, controller


def _payload(operations, revision=0):
    return {
        "command": {
            "operation": "action",
            "action": "structure.apply",
            "args": {"operations": operations},
        },
        "expected_revision": revision,
    }


class StructuralOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        (
            self.settings,
            self.definition,
            self.capabilities,
            _registry,
            _analyzer,
            _plan,
            _controller,
        ) = _runtime()
        self.layer_settings = dict(self.definition.layers[0].settings)

    def test_all_seven_operations_apply_as_one_private_candidate(self) -> None:
        candidate = apply_structural_operations(
            self.definition,
            [
                {"op": "add_layer", "layer": {
                    "layer_id": "top", "layer_type": "current-bars",
                    "analyzer_id": "main-audio", "visible": True,
                    "settings": self.layer_settings,
                }},
                {"op": "set_layer_visibility", "layer_id": "top", "visible": False},
                {"op": "set_layer_analyzer", "layer_id": "top", "analyzer_id": "main-audio"},
                {"op": "set_layer_settings", "layer_id": "top", "settings": self.layer_settings},
                {"op": "set_layer_order", "layer_ids": ["top", "current-bars"]},
                {"op": "remove_layer", "layer_id": "current-bars"},
                {"op": "set_output_backend", "output_backend": "direct"},
            ],
            self.capabilities,
        )
        self.assertEqual([layer.layer_id for layer in candidate.layers], ["top"])
        self.assertFalse(candidate.layers[0].visible)
        self.assertEqual(candidate.output_backend, "direct")
        self.assertEqual([layer.layer_id for layer in self.definition.layers], ["current-bars"])

    def test_closed_shapes_references_order_and_limits_are_rejected_with_paths(self) -> None:
        invalid = (
            ([{"op": "remove_layer", "layer_id": "missing"}], "args.operations[0].layer_id"),
            ([{"op": "set_layer_order", "layer_ids": []}], "args.operations[0].layer_ids"),
            ([{"op": "remove_layer", "layer_id": "current-bars", "extra": True}], "args.operations[0]"),
            ([{"op": "unknown"}], "args.operations[0].op"),
        )
        for operations, path in invalid:
            with self.subTest(operations=operations), self.assertRaises(StructuralValidationError) as caught:
                apply_structural_operations(self.definition, operations, self.capabilities)
            self.assertEqual(caught.exception.path, path)

        layers = tuple(
            replace(self.definition.layers[0], layer_id=f"layer-{index}")
            for index in range(8)
        )
        full = replace(self.definition, layers=layers)
        with self.assertRaises(StructuralValidationError) as caught:
            apply_structural_operations(full, [{"op": "add_layer", "layer": {
                "layer_id": "ninth", "layer_type": "current-bars",
                "analyzer_id": "main-audio", "visible": True,
                "settings": self.layer_settings,
            }}], self.capabilities)
        self.assertEqual(caught.exception.code, "limit_exceeded")

    def test_batch_size_is_bounded_between_one_and_thirty_two(self) -> None:
        visibility = {"op": "set_layer_visibility", "layer_id": "current-bars", "visible": True}
        for operations in ([], [visibility] * 33, {"op": "remove_layer"}):
            with self.subTest(size=len(operations)), self.assertRaises(
                StructuralValidationError
            ) as caught:
                apply_structural_operations(self.definition, operations, self.capabilities)
            self.assertEqual(
                (caught.exception.code, caught.exception.path),
                ("invalid_structure", "args.operations"),
            )
        candidate = apply_structural_operations(
            self.definition, [visibility] * 32, self.capabilities
        )
        self.assertTrue(candidate.layers[0].visible)

    def test_references_types_and_settings_errors_report_bounded_paths(self) -> None:
        def add(**overrides):
            layer = {
                "layer_id": "top", "layer_type": "current-bars",
                "analyzer_id": "main-audio", "visible": True,
                "settings": self.layer_settings,
            }
            layer.update(overrides)
            return [{"op": "add_layer", "layer": layer}]

        missing_field = dict(self.layer_settings)
        missing_field.pop("theme")
        cases = (
            (add(layer_id="current-bars"), "invalid_value", "args.operations[0].layer.layer_id"),
            (add(layer_id=" top"), "invalid_value", "args.operations[0].layer.layer_id"),
            (add(layer_type="spectrogram"), "invalid_reference", "args.operations[0].layer.layer_type"),
            (add(analyzer_id="missing"), "invalid_reference", "args.operations[0].layer.analyzer_id"),
            (add(visible=1), "invalid_value", "args.operations[0].layer.visible"),
            (add(extra=True), "invalid_structure", "args.operations[0].layer"),
            (add(settings=missing_field), "invalid_value", "args.operations[0].layer.settings"),
            (add(settings={**self.layer_settings, "debug": True}), "invalid_value", "args.operations[0].layer.settings"),
            (add(settings={**self.layer_settings, "theme": "unknown"}), "invalid_value", "args.operations[0].layer.settings"),
            ([{"op": "set_layer_settings", "layer_id": "current-bars",
               "settings": {**self.layer_settings, "sensitivity_scalar": 0}}],
             "invalid_value", "args.operations[0].settings"),
            ([{"op": "set_layer_settings", "layer_id": "current-bars",
               "settings": {**self.layer_settings, "peak_hold_frames": True}}],
             "invalid_value", "args.operations[0].settings"),
            ([{"op": "set_layer_settings", "layer_id": "current-bars",
               "settings": {**self.layer_settings, "smoothing_rise": float("nan")}}],
             "invalid_value", "args.operations[0].settings"),
            ([{"op": "set_layer_settings", "layer_id": "current-bars",
               "settings": {**self.layer_settings, "sensitivity_scalar": 10**400}}],
             "invalid_value", "args.operations[0].settings"),
            ([{"op": "set_layer_settings", "layer_id": "current-bars",
               "settings": {**self.layer_settings, "peak_hold_frames": 10**400}}],
             "invalid_value", "args.operations[0].settings"),
            ([{"op": "set_layer_visibility", "layer_id": "current-bars", "visible": "no"}],
             "invalid_value", "args.operations[0].visible"),
            ([{"op": "set_layer_analyzer", "layer_id": "current-bars", "analyzer_id": "missing"}],
             "invalid_reference", "args.operations[0].analyzer_id"),
            ([{"op": "set_layer_order", "layer_ids": ["current-bars", "current-bars"]}],
             "invalid_value", "args.operations[0].layer_ids"),
            ([{"op": "set_layer_order", "layer_ids": ["other"]}],
             "invalid_value", "args.operations[0].layer_ids"),
            ([{"op": "set_layer_order", "layer_ids": "current-bars"}],
             "invalid_value", "args.operations[0].layer_ids"),
            ([{"op": "set_output_backend", "output_backend": "automatic"}],
             "invalid_value", "args.operations[0].output_backend"),
            ([{"op": "set_output_backend"}], "invalid_structure", "args.operations[0]"),
            (["remove_layer"], "invalid_structure", "args.operations[0]"),
        )
        for operations, code, path in cases:
            with self.subTest(code=code, path=path, operations=operations), self.assertRaises(
                StructuralValidationError
            ) as caught:
                apply_structural_operations(self.definition, operations, self.capabilities)
            self.assertEqual((caught.exception.code, caught.exception.path), (code, path))
        self.assertEqual([layer.layer_id for layer in self.definition.layers], ["current-bars"])

    def test_pillow_constraints_reject_incompatible_candidates_atomically(self) -> None:
        gradient = replace(
            self.definition.layers[0],
            settings={**self.layer_settings, "gradient": True},
        )
        direct = replace(self.definition, layers=(gradient,))
        with self.assertRaises(StructuralValidationError) as caught:
            apply_structural_operations(
                direct,
                [{"op": "set_output_backend", "output_backend": "pillow-bulk"}],
                self.capabilities,
            )
        self.assertEqual(caught.exception.path, "structure.layers[0].settings")
        self.assertEqual(direct.output_backend, DIRECT_OUTPUT_PATH)

        pillow_definition = _runtime(PILLOW_BULK_OUTPUT_PATH)[1]
        pillow_settings = dict(pillow_definition.layers[0].settings)
        with self.assertRaises(StructuralValidationError) as rejected:
            apply_structural_operations(
                pillow_definition,
                [{"op": "set_layer_settings", "layer_id": "current-bars",
                  "settings": {**pillow_settings, "shadow_enabled": True}}],
                self.capabilities,
            )
        self.assertEqual(rejected.exception.path, "args.operations[0].settings")

        # The same complete settings become valid once the batch selects direct.
        candidate = apply_structural_operations(
            pillow_definition,
            [
                {"op": "set_output_backend", "output_backend": "direct"},
                {"op": "set_layer_settings", "layer_id": "current-bars",
                 "settings": {**pillow_settings, "shadow_enabled": True}},
            ],
            self.capabilities,
        )
        self.assertEqual(candidate.output_backend, DIRECT_OUTPUT_PATH)
        self.assertTrue(candidate.layers[0].settings["shadow_enabled"])

    def test_pillow_startup_accepts_any_bounded_overflow_multiplier(self) -> None:
        constraints = current_bars_settings_descriptor()["backend_constraints"]
        self.assertNotIn("overflow_multiplier", constraints[PILLOW_BULK_OUTPUT_PATH])
        # HEAD started pillow-bulk with any multiplier; the startup definition
        # must still accept a non-default one.
        settings = Settings()
        settings.shadow.enabled = False
        settings.peak.enabled = False
        settings.overflow.multiplier = 2.0
        visualizer = BarsUnifiedVisualizer(64, 64, settings)
        visualizer.set_theme(get_theme("ocean"))
        definition = current_bars_runtime_definition(
            audio_settings=settings.audio,
            frequency_settings=settings.frequency,
            sensitivity_settings=settings.sensitivity,
            num_bins=64,
            output_backend=PILLOW_BULK_OUTPUT_PATH,
            layer_settings=current_bars_settings_from_runtime(settings, visualizer),
        )
        self.assertEqual(definition.layers[0].settings["overflow_multiplier"], 2.0)


class StructuralExecutionTests(unittest.TestCase):
    def test_layer_ranges_select_distinct_or_shared_projection_views(self) -> None:
        settings, definition, capabilities, registry, analyzer, _plan, _controller = _runtime()
        base = dict(definition.layers[0].settings)
        low = replace(
            definition.layers[0],
            layer_id="low",
            settings={**base, "min_freq": 80.0, "max_freq": 1950.0},
        )
        low_alias = replace(low, layer_id="low-alias")
        high = replace(
            definition.layers[0],
            layer_id="high",
            settings={**base, "min_freq": 2000.0, "max_freq": 6300.0},
        )
        graph = compile_runtime_graph(
            replace(definition, layers=(low, low_alias, high)),
            capabilities,
        )
        plan = compile_current_bars_layer_plan(
            graph, registry, visualizer=None, settings=settings, width=8, height=8
        )

        self.assertIs(plan.layers[0].analyzer, plan.layers[1].analyzer)
        self.assertIsNot(plan.layers[0].analyzer, plan.layers[2].analyzer)
        self.assertEqual(len(analyzer.projections), 2)

    def test_equivalent_analyzer_references_share_one_physical_lease(self) -> None:
        settings, definition, capabilities, _registry, _analyzer, _plan, _controller = _runtime()
        alias = replace(definition.analyzers[0], analyzer_id="alias")
        layers = (
            definition.layers[0],
            replace(definition.layers[0], layer_id="top", analyzer_id="alias"),
        )
        aliased = replace(definition, analyzers=(*definition.analyzers, alias), layers=layers)
        candidate = apply_structural_operations(
            aliased,
            [{
                "op": "set_layer_analyzer",
                "layer_id": "current-bars",
                "analyzer_id": "alias",
            }],
            capabilities,
        )
        graph = compile_runtime_graph(candidate, capabilities)
        analyzer = _Analyzer(8)
        registry = AnalyzerRegistry(
            graph,
            {STANDARD_ANALYZER_TYPE: lambda _definition: analyzer},
        )
        plan = compile_current_bars_layer_plan(
            graph, registry, visualizer=None, settings=settings, width=8, height=8
        )
        plan.prepare_frame()
        self.assertEqual(analyzer.get_calls, 1)
        self.assertEqual(analyzer.release_calls, 1)
        self.assertIs(plan.layers[0].analyzer, plan.layers[1].analyzer)

    def test_empty_and_all_hidden_direct_plans_clear_without_analyzer_lease(self) -> None:
        settings, definition, capabilities, registry, analyzer, _plan, _controller = _runtime()
        for layers in ((), (replace(definition.layers[0], visible=False),)):
            graph = compile_runtime_graph(replace(definition, layers=layers), capabilities)
            plan = compile_current_bars_layer_plan(
                graph, registry, visualizer=None, settings=settings, width=8, height=8
            )
            canvas = _DirectCanvas()
            self.assertEqual(plan.prepare_frame().size, 0)
            plan.draw_frame(canvas)
            self.assertEqual(canvas.clear_calls, 1)
        self.assertEqual(analyzer.get_calls, 0)

    def test_empty_pillow_plan_uploads_one_black_frame_without_analyzer_lease(self) -> None:
        try:
            import PIL  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("requires Pillow")
        settings, definition, capabilities, registry, analyzer, _plan, _controller = _runtime(
            PILLOW_BULK_OUTPUT_PATH
        )
        graph = compile_runtime_graph(replace(definition, layers=()), capabilities)
        plan = compile_current_bars_layer_plan(
            graph, registry, visualizer=None, settings=settings, width=64, height=64
        )
        canvas = _BulkCanvas()
        self.assertEqual(plan.prepare_frame().size, 0)
        plan.draw_frame(canvas)
        self.assertEqual(analyzer.get_calls, 0)
        self.assertEqual(canvas.set_image_calls, 1)
        self.assertIsNone(canvas.images[0].getbbox())

    def test_multiple_pillow_layers_share_analyzer_and_upload_one_stable_image(self) -> None:
        settings, definition, capabilities, registry, analyzer, _plan, _controller = _runtime(
            PILLOW_BULK_OUTPUT_PATH
        )
        layers = (
            definition.layers[0],
            replace(definition.layers[0], layer_id="top"),
        )
        graph = compile_runtime_graph(replace(definition, layers=layers), capabilities)
        plan = compile_current_bars_layer_plan(
            graph, registry, visualizer=None, settings=settings, width=64, height=64
        )
        canvas = _BulkCanvas()
        self.assertIsNot(plan.layers[0].presentation_state, plan.layers[1].presentation_state)
        self.assertIs(
            plan.layers[0].presentation_state.settings.sensitivity,
            settings.sensitivity,
        )
        self.assertIs(
            plan.layers[1].presentation_state.settings.sensitivity,
            settings.sensitivity,
        )
        for _ in range(2):
            plan.prepare_frame()
            plan.draw_frame(canvas)
        self.assertEqual(analyzer.get_calls, 2)
        self.assertEqual(analyzer.release_calls, 2)
        self.assertEqual(canvas.set_image_calls, 2)
        self.assertIs(canvas.images[0], canvas.images[1])

    def test_pillow_upload_failure_propagates_without_direct_fallback(self) -> None:
        settings, definition, capabilities, registry, _analyzer, _plan, _controller = _runtime(
            PILLOW_BULK_OUTPUT_PATH
        )
        layers = (
            definition.layers[0],
            replace(definition.layers[0], layer_id="top"),
        )
        graph = compile_runtime_graph(replace(definition, layers=layers), capabilities)
        plan = compile_current_bars_layer_plan(
            graph, registry, visualizer=None, settings=settings, width=64, height=64
        )

        class _FailingBulkCanvas(_BulkCanvas):
            def SetImage(self, image):
                self.set_image_calls += 1
                raise OSError("upload rejected")

        canvas = _FailingBulkCanvas()
        plan.prepare_frame()
        with self.assertRaisesRegex(RuntimeError, "bulk output upload failed"):
            plan.draw_frame(canvas)
        self.assertEqual(canvas.set_image_calls, 1)

    def test_pillow_paints_layers_bottom_to_top_with_their_own_multipliers(self) -> None:
        settings, definition, capabilities, registry, _analyzer, _plan, _controller = _runtime(
            PILLOW_BULK_OUTPUT_PATH
        )
        base = dict(definition.layers[0].settings)
        short = replace(
            definition.layers[0], layer_id="short",
            settings={**base, "overflow_multiplier": 1.5},
        )
        tall = replace(
            definition.layers[0], layer_id="tall",
            settings={**base, "overflow_multiplier": 3.0},
        )

        def frame(*layers):
            graph = compile_runtime_graph(replace(definition, layers=layers), capabilities)
            plan = compile_current_bars_layer_plan(
                graph, registry, visualizer=None, settings=settings, width=64, height=64
            )
            canvas = _BulkCanvas()
            plan.prepare_frame()
            plan.draw_frame(canvas)
            self.assertEqual(canvas.set_image_calls, 1)
            return np.array(canvas.images[0])

        short_only = frame(short)
        tall_only = frame(tall)
        self.assertFalse(np.array_equal(short_only, tall_only))

        def painted_over(bottom, top):
            # No alpha: wherever the top layer painted, its pixel wins.
            top_lit = top.any(axis=-1, keepdims=True)
            return np.where(top_lit, top, bottom)

        np.testing.assert_array_equal(
            frame(short, tall), painted_over(short_only, tall_only)
        )
        np.testing.assert_array_equal(
            frame(tall, short), painted_over(tall_only, short_only)
        )
        self.assertFalse(np.array_equal(frame(short, tall), frame(tall, short)))

    def test_direct_plan_clears_once_and_draws_layers_bottom_to_top(self) -> None:
        settings, definition, capabilities, registry, _analyzer, _plan, _controller = _runtime()
        base = dict(definition.layers[0].settings)
        ocean = replace(
            definition.layers[0], layer_id="ocean", settings={**base, "theme": "ocean"}
        )
        forest = replace(
            definition.layers[0], layer_id="forest", settings={**base, "theme": "forest"}
        )

        def frame(*layers):
            graph = compile_runtime_graph(replace(definition, layers=layers), capabilities)
            plan = compile_current_bars_layer_plan(
                graph, registry, visualizer=None, settings=settings, width=8, height=8
            )
            canvas = _RecordingCanvas()
            plan.prepare_frame()
            plan.draw_frame(canvas)
            return canvas

        ocean_only = frame(ocean).pixels
        forest_only = frame(forest).pixels
        contested = {
            pixel for pixel in ocean_only
            if pixel in forest_only and ocean_only[pixel] != forest_only[pixel]
        }
        self.assertTrue(contested)
        forest_on_top = frame(ocean, forest)
        ocean_on_top = frame(forest, ocean)
        self.assertEqual(forest_on_top.clear_calls, 1)
        self.assertEqual(ocean_on_top.clear_calls, 1)
        for pixel in contested:
            self.assertEqual(forest_on_top.pixels[pixel], forest_only[pixel])
            self.assertEqual(ocean_on_top.pixels[pixel], ocean_only[pixel])


class StructuralCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime_width = 8
        (
            self.settings,
            self.definition,
            self.capabilities,
            self.registry,
            self.analyzer,
            self.plan,
            self.controller,
        ) = _runtime()
        self.owner = ActiveLayerPlan(self.plan)
        self.bridge = FrameBoundaryRuntimeBridge(response_timeout=1.0)

    def _compiler(self, definition):
        graph = compile_runtime_graph(definition, self.capabilities)
        return compile_current_bars_layer_plan(
            graph,
            self.registry,
            visualizer=None,
            settings=self.settings,
            width=self.runtime_width,
            height=self.runtime_width,
        )

    def _coordinator(self, compiler=None):
        return StructuralRuntimeCoordinator(
            controller=self.controller,
            active_plan=self.owner,
            initial_definition=self.definition,
            capabilities=self.capabilities,
            compile_definition=compiler or self._compiler,
            bridge=self.bridge,
            deadline_seconds=0.9,
        )

    def _exchange(self, coordinator, payload):
        result = {}

        def request():
            try:
                result["value"] = coordinator.handle_runtime_operation("command", payload)
            except Exception as exc:
                result["error"] = exc

        thread = threading.Thread(target=request)
        thread.start()
        deadline = time.monotonic() + 1.5
        while thread.is_alive() and time.monotonic() < deadline:
            coordinator.process_pending(max_items=4)
            time.sleep(0.001)
        thread.join(1)
        self.assertFalse(thread.is_alive())
        return result

    def test_success_acknowledges_only_after_frame_boundary_and_exposes_capabilities(self) -> None:
        coordinator = self._coordinator()
        settings = dict(self.definition.layers[0].settings)
        result = self._exchange(coordinator, _payload([{"op": "add_layer", "layer": {
            "layer_id": "top", "layer_type": "current-bars",
            "analyzer_id": "main-audio", "visible": True,
            "settings": settings,
        }}]))
        self.assertNotIn("error", result)
        snapshot = result["value"]
        self.assertEqual(snapshot["revision"], 1)
        self.assertEqual(
            [layer["layer_id"] for layer in snapshot["structure"]["layers"]],
            ["current-bars", "top"],
        )
        self.assertEqual(snapshot["structure"]["capabilities"]["limits"], {
            "max_layers": 8,
            "max_operations": 32,
        })
        self.assertEqual(len(snapshot["structure"]["capabilities"]["operations"]), 7)
        self.assertEqual(snapshot["structure"]["available_analyzer_ids"], ["main-audio"])
        self.assertEqual(snapshot["structure"]["analyzers"][0]["analyzer_id"], "main-audio")
        # Live analysis controls own current analyzer values; the structure
        # snapshot publishes identity only.
        self.assertNotIn("settings", snapshot["structure"]["analyzers"][0])
        json.dumps(snapshot)

    def test_custom_current_layer_projection_uses_structural_controls(self) -> None:
        coordinator = self._coordinator()
        custom = {
            **self.definition.layers[0].settings,
            "min_freq": 80.0,
            "max_freq": 1950.0,
        }
        result = self._exchange(
            coordinator,
            _payload([{
                "op": "set_layer_settings",
                "layer_id": "current-bars",
                "settings": custom,
            }]),
        )
        self.assertNotIn("error", result)
        snapshot = result["value"]
        layer = snapshot["structure"]["layers"][0]
        self.assertEqual(
            (layer["settings"]["min_freq"], layer["settings"]["max_freq"]),
            (80.0, 1950.0),
        )
        controls = {item["path"]: item for item in snapshot["controls"]}
        self.assertFalse(controls["settings.noise_floor"]["available"])
        self.assertFalse(controls["settings.zoom_preset_index"]["available"])

    def test_public_layer_tool_compiles_and_activates_all_counts_and_backends(self) -> None:
        # Pillow's accepted bulk path intentionally supports only the qualified
        # 64x64 geometry, so run this public-surface matrix at that geometry.
        self.runtime_width = 64
        (
            self.settings,
            self.definition,
            self.capabilities,
            self.registry,
            self.analyzer,
            self.plan,
            self.controller,
        ) = _runtime(width=self.runtime_width)
        self.owner = ActiveLayerPlan(self.plan)
        self.bridge = FrameBoundaryRuntimeBridge(response_timeout=1.0)
        coordinator = self._coordinator()
        for backend in (DIRECT_OUTPUT_PATH, PILLOW_BULK_OUTPUT_PATH):
            for count in range(1, 9):
                with self.subTest(backend=backend, count=count):
                    action = build_structure_action(
                        self.controller.snapshot(), count, backend
                    )
                    result = self._exchange(coordinator, {
                        "command": {
                            "operation": "action",
                            "action": action["action"],
                            "args": action["args"],
                        },
                        "expected_revision": action["expected_revision"],
                    })
                    self.assertNotIn("error", result)
                    snapshot = result["value"]
                    self.assertEqual(snapshot["structure"]["output_backend"], backend)
                    self.assertEqual(
                        [
                            layer["layer_id"]
                            for layer in snapshot["structure"]["layers"]
                        ],
                        [
                            "current-bars",
                            *(f"layer-{index}" for index in range(2, count + 1)),
                        ],
                    )
                    self.assertEqual(len(self.owner.active.layers), count)
                    self.assertEqual(
                        len({id(layer.analyzer) for layer in self.owner.active.layers}),
                        1,
                    )
                    self.assertEqual(
                        len({
                            id(layer.presentation_state)
                            for layer in self.owner.active.layers
                        }),
                        count,
                    )

    def test_blocked_compiler_does_not_stop_frames_and_conflict_is_atomic(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def blocked(definition):
            entered.set()
            release.wait(1)
            return self._compiler(definition)

        coordinator = self._coordinator(blocked)
        result = {}
        request = threading.Thread(
            target=lambda: result.setdefault(
                "result",
                self._capture_error(
                    coordinator,
                    _payload([{
                        "op": "set_layer_visibility",
                        "layer_id": "current-bars",
                        "visible": False,
                    }]),
                ),
            )
        )
        request.start()
        deadline = time.monotonic() + 1
        while not entered.is_set() and time.monotonic() < deadline:
            coordinator.process_pending(max_items=4)
            time.sleep(0.001)
        self.assertTrue(entered.is_set())

        with self.assertRaises(RuntimeControlError) as busy:
            coordinator.handle_runtime_operation(
                "command",
                _payload([{
                    "op": "set_layer_visibility",
                    "layer_id": "current-bars",
                    "visible": True,
                }]),
            )
        self.assertEqual(busy.exception.code, "busy")

        canvas = _DirectCanvas()
        self.owner.active.prepare_frame()
        self.owner.active.draw_frame(canvas)
        self.assertEqual(canvas.clear_calls, 1)
        self.controller.apply_commands([
            RuntimeCommand("set", "visualizer.gradient", True)
        ])
        release.set()
        while request.is_alive() and time.monotonic() < deadline + 1:
            coordinator.process_pending(max_items=4)
            time.sleep(0.001)
        request.join(1)
        self.assertIsInstance(result["result"], RuntimeControlError)
        self.assertEqual(result["result"].code, "revision_conflict")
        self.assertIs(self.owner.active, self.plan)
        self.assertEqual(self.controller.revision, 1)
        self.assertTrue(self.controller.visualizer.gradient_mode)

    def test_compile_failure_timeout_and_missing_legacy_target_leave_clean_state(self) -> None:
        coordinator = self._coordinator()
        failed = self._exchange(
            coordinator,
            _payload([{
                "op": "set_output_backend",
                "output_backend": "pillow-bulk",
            }]),
        )
        self.assertIsInstance(failed["error"], RuntimeControlError)
        self.assertEqual(failed["error"].code, "invalid_structure")
        self.assertIs(self.owner.active, self.plan)
        self.assertEqual(self.controller.revision, 0)

        removed = self._exchange(
            coordinator,
            _payload([{
                "op": "remove_layer",
                "layer_id": "current-bars",
            }]),
        )
        self.assertEqual(removed["value"]["structure"]["layers"], [])
        controls = {
            control["path"]: control for control in removed["value"]["controls"]
        }
        self.assertFalse(controls["visualizer.gradient"]["available"])
        self.assertTrue(controls["settings.noise_floor"]["available"])
        unavailable = self.controller.apply_request({
            "commands": [{
                "op": "set",
                "path": "visualizer.gradient",
                "value": True,
            }]
        })
        self.assertFalse(unavailable["ok"])
        self.assertEqual(unavailable["error"]["code"], "unavailable")

        slow_bridge = FrameBoundaryRuntimeBridge(response_timeout=1.0)
        (
            slow_settings,
            slow_definition,
            slow_capabilities,
            slow_registry,
            _slow_analyzer,
            slow_plan,
            slow_controller,
        ) = _runtime()

        def slow_compiler(definition):
            time.sleep(0.02)
            graph = compile_runtime_graph(definition, slow_capabilities)
            return compile_current_bars_layer_plan(
                graph,
                slow_registry,
                visualizer=None,
                settings=slow_settings,
                width=8,
                height=8,
            )

        slow = StructuralRuntimeCoordinator(
            controller=slow_controller,
            active_plan=ActiveLayerPlan(slow_plan),
            initial_definition=slow_definition,
            capabilities=slow_capabilities,
            compile_definition=slow_compiler,
            bridge=slow_bridge,
            deadline_seconds=0.001,
        )
        timed_out = self._exchange(
            slow,
            _payload([{
                "op": "set_layer_visibility",
                "layer_id": "current-bars",
                "visible": False,
            }]),
        )
        self.assertEqual(timed_out["error"].code, "timeout")

    def test_live_legacy_settings_are_folded_into_later_structural_base(self) -> None:
        coordinator = self._coordinator()
        self.controller.apply_commands([
            RuntimeCommand("set", "visualizer.gradient", True)
        ])
        settings = dict(self.definition.layers[0].settings)
        result = self._exchange(
            coordinator,
            _payload([{"op": "add_layer", "layer": {
                "layer_id": "top", "layer_type": "current-bars",
                "analyzer_id": "main-audio", "visible": True,
                "settings": settings,
            }}], revision=1),
        )
        self.assertNotIn("error", result)
        layers = result["value"]["structure"]["layers"]
        self.assertTrue(layers[0]["settings"]["gradient"])
        self.assertFalse(layers[1]["settings"]["gradient"])

    def test_live_dynamic_speed_stays_inside_structural_range(self) -> None:
        for value, expected in (
            (6.06, 5.0), (-1, 0.0), (2.5, 2.5),
            (True, None), (float("nan"), None), (10**400, None), ("fast", None),
        ):
            with self.subTest(value=value):
                self.assertEqual(bounded_dynamic_cycle_speed(value), expected)

        coordinator = self._coordinator()
        self.controller.apply_commands([
            RuntimeCommand("set", "visualizer.theme", "dynamic")
        ])
        for _ in range(6):
            self.controller.apply_commands([
                RuntimeCommand("action", "dynamic.adjust_speed", args={"delta": 1.0})
            ])
        self.assertEqual(self.controller.visualizer.theme.cycle_speed, 5.0)
        # An unrelated structural edit still validates the folded legacy layer.
        result = self._exchange(coordinator, _payload([{"op": "add_layer", "layer": {
            "layer_id": "top", "layer_type": "current-bars",
            "analyzer_id": "main-audio", "visible": True,
            "settings": dict(self.definition.layers[0].settings),
        }}], revision=self.controller.revision))
        self.assertNotIn("error", result)
        legacy = result["value"]["structure"]["layers"][0]["settings"]
        self.assertEqual((legacy["theme"], legacy["dynamic_cycle_speed"]), ("dynamic", 5.0))

    def test_default_deadline_finishes_before_supervisor_request_timeout(self) -> None:
        deadline = inspect.signature(StructuralRuntimeCoordinator).parameters[
            "deadline_seconds"
        ].default
        supervisor_timeout = inspect.signature(VisualizationSupervisor).parameters[
            "runtime_request_timeout"
        ].default
        self.assertLess(deadline, supervisor_timeout)

    def test_unserviced_activation_times_out_and_is_never_swapped_in(self) -> None:
        compiled = threading.Event()
        proceed = threading.Event()

        def compiler(definition):
            plan = self._compiler(definition)
            compiled.set()
            proceed.wait(1)
            return plan

        coordinator = StructuralRuntimeCoordinator(
            controller=self.controller,
            active_plan=self.owner,
            initial_definition=self.definition,
            capabilities=self.capabilities,
            compile_definition=compiler,
            bridge=self.bridge,
            deadline_seconds=0.3,
        )
        result = {}
        request = threading.Thread(
            target=lambda: result.setdefault(
                "value",
                self._capture_error(
                    coordinator,
                    _payload([{
                        "op": "set_layer_visibility",
                        "layer_id": "current-bars",
                        "visible": False,
                    }]),
                ),
            )
        )
        request.start()
        deadline = time.monotonic() + 1
        while not compiled.is_set() and time.monotonic() < deadline:
            coordinator.process_pending(max_items=4)
            time.sleep(0.001)
        self.assertTrue(compiled.is_set())
        # Stop servicing frame boundaries before the activation is submitted.
        proceed.set()
        request.join(1)
        self.assertFalse(request.is_alive())
        self.assertIsInstance(result["value"], RuntimeControlError)
        self.assertEqual(result["value"].code, "timeout")

        # A late frame boundary skips the cancelled activation.
        coordinator.process_pending(max_items=4)
        self.assertIs(self.owner.active, self.plan)
        self.assertEqual(self.controller.revision, 0)
        self.assertTrue(coordinator.structure_snapshot()["layers"][0]["visible"])

    def test_request_shape_and_stale_base_are_rejected_without_mutation(self) -> None:
        coordinator = self._coordinator()
        operations = [{
            "op": "set_layer_visibility",
            "layer_id": "current-bars",
            "visible": False,
        }]
        missing = _payload(operations)
        del missing["expected_revision"]
        boolean = _payload(operations)
        boolean["expected_revision"] = True
        mixed = _payload(operations)
        mixed["command"]["args"]["commands"] = [
            {"op": "set", "path": "visualizer.gradient", "value": True}
        ]
        for payload, code, path in (
            (missing, "invalid_value", "expected_revision"),
            (boolean, "invalid_value", "expected_revision"),
            (mixed, "invalid_structure", "args"),
        ):
            with self.subTest(code=code, path=path), self.assertRaises(
                RuntimeControlError
            ) as caught:
                coordinator.handle_runtime_operation("command", payload)
            self.assertEqual((caught.exception.code, caught.exception.path), (code, path))

        stale = self._exchange(coordinator, _payload(operations, revision=3))
        self.assertEqual(stale["error"].code, "revision_conflict")
        invalid = self._exchange(
            coordinator,
            _payload([{"op": "remove_layer", "layer_id": "missing"}]),
        )
        self.assertEqual(
            (invalid["error"].code, invalid["error"].path),
            ("invalid_reference", "args.operations[0].layer_id"),
        )
        self.assertIs(self.owner.active, self.plan)
        self.assertEqual(self.controller.revision, 0)
        self.assertFalse(self.controller.visualizer.gradient_mode)
        self.assertTrue(coordinator.structure_snapshot()["layers"][0]["visible"])

    def test_legacy_live_controls_rebind_to_replacement_and_stay_layer_scoped(self) -> None:
        coordinator = self._coordinator()
        original_visualizer = self.controller.visualizer
        settings = dict(self.definition.layers[0].settings)
        result = self._exchange(coordinator, _payload([
            {"op": "add_layer", "layer": {
                "layer_id": "top", "layer_type": "current-bars",
                "analyzer_id": "main-audio", "visible": True,
                "settings": settings,
            }},
            {"op": "set_layer_settings", "layer_id": "current-bars",
             "settings": {**settings, "gradient": True}},
        ]))
        self.assertNotIn("error", result)
        self.assertIsNot(self.owner.active, self.plan)
        legacy, top = (layer.presentation_state for layer in self.owner.active.layers)
        self.assertIs(self.controller.visualizer, legacy.visualizer)
        self.assertIsNot(legacy.visualizer, original_visualizer)
        self.assertTrue(legacy.visualizer.gradient_mode)
        self.assertFalse(top.visualizer.gradient_mode)
        # Analyzer-owned settings stay shared with the standard analyzer owner.
        self.assertIs(legacy.settings.sensitivity, self.settings.sensitivity)
        self.assertIs(top.settings.sensitivity, self.settings.sensitivity)

        overflow = not settings["overflow"]
        live = self._exchange(coordinator, {
            "command": {"operation": "patch", "changes": [
                {"path": "visualizer.overflow", "value": overflow},
                {"path": "settings.sensitivity_scalar", "value": 2.5},
            ]},
            "expected_revision": 1,
        })
        self.assertNotIn("error", live)
        self.assertEqual(live["value"]["revision"], 2)
        self.assertEqual(legacy.visualizer.overflow_mode, overflow)
        self.assertEqual(top.visualizer.overflow_mode, settings["overflow"])
        self.assertEqual(legacy.settings.scaling.sensitivity_scalar, 2.5)
        self.assertEqual(
            top.settings.scaling.sensitivity_scalar,
            settings["sensitivity_scalar"],
        )
        layers = live["value"]["structure"]["layers"]
        self.assertEqual(layers[0]["settings"]["overflow"], overflow)
        self.assertEqual(layers[0]["settings"]["sensitivity_scalar"], 2.5)
        self.assertEqual(layers[1]["settings"]["overflow"], settings["overflow"])
        json.dumps(live["value"])

    def test_structural_edits_are_not_written_to_runtime_state(self) -> None:
        coordinator = self._coordinator()
        result = self._exchange(coordinator, _payload([
            {"op": "add_layer", "layer": {
                "layer_id": "structural-only", "layer_type": "current-bars",
                "analyzer_id": "main-audio", "visible": True,
                "settings": dict(self.definition.layers[0].settings),
            }},
        ]))
        self.assertNotIn("error", result)
        encoded = json.dumps(self.controller.runtime_state())
        for forbidden in ("structural-only", "layer_id", "output_backend", "structure"):
            self.assertNotIn(forbidden, encoded)

    @staticmethod
    def _capture_error(coordinator, payload):
        try:
            return coordinator.handle_runtime_operation("command", payload)
        except Exception as exc:
            return exc


class DebugModeRemovalTests(unittest.TestCase):
    def test_debug_mode_is_absent_from_controls_state_capabilities_and_sources(self) -> None:
        _settings, definition, capabilities, _registry, _analyzer, _plan, controller = _runtime()
        self.assertFalse(hasattr(controller.visualizer, "debug_mode"))
        self.assertFalse(hasattr(BarsUnifiedVisualizer, "toggle_debug"))
        rejected = controller.apply_request({
            "commands": [{"op": "set", "path": "visualizer.debug", "value": True}]
        })
        self.assertFalse(rejected["ok"])
        self.assertIsNone(controller.handle_key("d"))
        self.assertEqual(controller.revision, 0)
        encoded = json.dumps({
            "snapshot": controller.snapshot(),
            "state": controller.runtime_state(),
            "capabilities": runtime_capabilities_to_dict(capabilities),
            "definition": runtime_definition_to_dict(definition),
        }).lower()
        self.assertNotIn("debug", encoded)
        for relative in (
            "main.py",
            "runtime_control.py",
            "structural_control.py",
            "core/layer_plan.py",
            "core/output.py",
            "visualizers/bars_unified.py",
            "docs/README.md",
            "docs/architecture.md",
            "docs/visualizers.md",
        ):
            with self.subTest(source=relative):
                text = (FFT_ROOT / relative).read_text(encoding="utf-8").lower()
                self.assertNotIn("debug", text)


if __name__ == "__main__":
    unittest.main()
