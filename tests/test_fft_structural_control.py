"""Focused tests for whole-document FFT structure control (structure.set)."""

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
        compile_runtime_graph,
        runtime_capabilities_to_dict,
        runtime_definition_to_dict,
    )
    from matrix_controller.runtime_transport import (  # noqa: E402
        MAX_LAUNCH_STRUCTURE_BYTES,
        FrameBoundaryRuntimeBridge,
        RuntimeControlError,
        RuntimeOperationTimeout,
    )
    from matrix_controller.supervisor import VisualizationSupervisor  # noqa: E402
    from runtime_control import FftRuntimeController  # noqa: E402
    from structural_control import (  # noqa: E402
        FALLBACK_THEME,
        PreparedStructuralPlan,
        StructuralRuntimeCoordinator,
        StructuralValidationError,
        launch_theme_fallbacks,
        structure_document_to_definition,
    )
    from themes import registry as theme_registry  # noqa: E402
    from themes.color_stops import color_stop_theme  # noqa: E402
    from tools.fft_layers import build_structure_action  # noqa: E402
    from visualizers.bars_unified import BarsUnifiedVisualizer  # noqa: E402
finally:
    if _prior_sounddevice is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = _prior_sounddevice


NYQUIST = 22_050.0


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

    def create_projection(self, candidate, _noise_floor_dbfs):
        # Mirror AudioProcessor: the sample-rate check fails only here.
        if candidate.max_freq > NYQUIST:
            raise ValueError(
                f"max_freq must not exceed the {NYQUIST:g} Hz Nyquist frequency"
            )
        projection = _Analyzer(len(self.result))
        projection.candidate = candidate
        self.projections.append(projection)
        return projection

    def remove_projection(self, projection):
        self.projections.remove(projection)


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
    if backend == PILLOW_BULK_OUTPUT_PATH:
        width = 64
    definition = current_bars_runtime_definition(
        audio_settings=settings.audio,
        frequency_settings=settings.frequency,
        sensitivity_settings=settings.sensitivity,
        num_bins=width,
        output_backend=backend,
        layer_settings=current_bars_settings_from_runtime(settings),
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
        settings=settings,
        width=width,
        height=width,
    )
    controller = FftRuntimeController(run_id="structural-test")
    return settings, definition, capabilities, registry, analyzer, plan, controller


def _layer(layer_settings, /, **overrides):
    layer = {
        "layer_id": "current-bars",
        "layer_type": "current-bars",
        "analyzer_id": "main-audio",
        "visible": True,
        "settings": dict(layer_settings),
    }
    layer.update(overrides)
    return layer


def _document(layers, backend=DIRECT_OUTPUT_PATH):
    return {"output_backend": backend, "layers": list(layers)}


def _payload(document, revision=0):
    return {
        "command": {
            "operation": "action",
            "action": "structure.set",
            "args": document,
        },
        "expected_revision": revision,
    }


class StructureDocumentTests(unittest.TestCase):
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

    def test_document_replaces_every_layer_and_the_backend(self) -> None:
        candidate = structure_document_to_definition(
            self.definition,
            _document([
                _layer(self.layer_settings, layer_id="top", visible=False),
                _layer(self.layer_settings, layer_id="bottom"),
            ]),
            self.capabilities,
        )
        self.assertEqual([layer.layer_id for layer in candidate.layers], ["top", "bottom"])
        self.assertFalse(candidate.layers[0].visible)
        self.assertEqual(candidate.output_backend, DIRECT_OUTPUT_PATH)
        self.assertEqual(candidate.analyzers, self.definition.analyzers)
        # The base is never mutated.
        self.assertEqual([layer.layer_id for layer in self.definition.layers], ["current-bars"])

        empty = structure_document_to_definition(
            self.definition, _document([]), self.capabilities
        )
        self.assertEqual(empty.layers, ())

    def test_shape_reference_and_settings_errors_report_paths(self) -> None:
        settings = self.layer_settings
        missing_theme = dict(settings)
        missing_theme.pop("theme")
        retired_pair = dict(settings)
        retired_pair.pop("weight_points")
        retired_pair.update(low_freq_weight=0.45, high_freq_weight=10.0)

        def one(**overrides):
            return _document([_layer(settings, **overrides)])

        nine = _document([
            _layer(settings, layer_id=f"layer-{index}") for index in range(9)
        ])
        cases = (
            ("not a document", "invalid_structure", "structure"),
            ({"layers": []}, "invalid_structure", "structure"),
            ({**_document([]), "operations": []}, "invalid_structure", "structure"),
            ({"output_backend": "automatic", "layers": []}, "invalid_value", "structure.output_backend"),
            ({"output_backend": "direct", "layers": {}}, "invalid_structure", "structure.layers"),
            (nine, "limit_exceeded", "structure.layers"),
            (_document(["layer"]), "invalid_structure", "structure.layers[0]"),
            (one(extra=True), "invalid_structure", "structure.layers[0]"),
            (_document([_layer(settings), _layer(settings)]), "invalid_value", "structure.layers[1].layer_id"),
            (one(layer_id=" top"), "invalid_value", "structure.layers[0].layer_id"),
            (one(layer_id="x" * 65), "invalid_value", "structure.layers[0].layer_id"),
            (one(layer_type="spectrogram"), "invalid_reference", "structure.layers[0].layer_type"),
            (one(analyzer_id="missing"), "invalid_reference", "structure.layers[0].analyzer_id"),
            (one(visible=1), "invalid_value", "structure.layers[0].visible"),
            (one(settings=missing_theme), "invalid_value", "structure.layers[0].settings"),
            (one(settings={**settings, "extra": True}), "invalid_value", "structure.layers[0].settings"),
            (one(settings="ocean"), "invalid_value", "structure.layers[0].settings"),
            (one(settings={**settings, "theme": "unknown"}), "invalid_value", "structure.layers[0].settings.theme"),
            (one(settings={**settings, "gradient": "yes"}), "invalid_value", "structure.layers[0].settings.gradient"),
            (one(settings={**settings, "peak_color_mode": "x"}), "invalid_value", "structure.layers[0].settings.peak_color_mode"),
            (one(settings={**settings, "gain": 0}), "invalid_value", "structure.layers[0].settings.gain"),
            (one(settings={**settings, "gain": 10.5}), "invalid_value", "structure.layers[0].settings.gain"),
            (one(settings={**settings, "sensitivity_scalar": 1.0}), "invalid_value", "structure.layers[0].settings"),
            (one(settings={**settings, "overflow_multiplier": 1.5}), "invalid_value", "structure.layers[0].settings"),
            (one(settings={**settings, "peak_hold_seconds": True}), "invalid_value", "structure.layers[0].settings.peak_hold_seconds"),
            (one(settings={**settings, "smoothing_rise_seconds": float("nan")}), "invalid_value", "structure.layers[0].settings.smoothing_rise_seconds"),
            # fft-level-controls M2 (D-023): retired per-call keys are unknown fields.
            (one(settings={**settings, "smoothing_rise": 0.4}), "invalid_value", "structure.layers[0].settings"),
            (one(settings={**settings, "peak_hold_frames": 8}), "invalid_value", "structure.layers[0].settings"),
            (one(settings={**settings, "auto_level": "yes"}), "invalid_value", "structure.layers[0].settings.auto_level"),
            (one(settings={**settings, "auto_level_attack_seconds": 0}), "invalid_value", "structure.layers[0].settings.auto_level_attack_seconds"),
            (one(settings={**settings, "auto_level_headroom": 0.5}), "invalid_value", "structure.layers[0].settings.auto_level_headroom"),
            (one(settings={**settings, "gain": 10**400}), "invalid_value", "structure.layers[0].settings.gain"),
            (one(settings={**settings, "peak_fall_rate": 10**400}), "invalid_value", "structure.layers[0].settings.peak_fall_rate"),
            (one(settings={**settings, "max_freq": 50.0}), "invalid_value", "structure.layers[0].settings.max_freq"),
            (one(settings={**settings, "weight_points": []}), "invalid_value", "structure.layers[0].settings.weight_points"),
            (one(settings={**settings, "weight_points": {"position": 0, "weight": 1}}), "invalid_value", "structure.layers[0].settings.weight_points"),
            (one(settings={**settings, "weight_points": [{"position": 0, "weight": 1}, {"position": 1, "weight": 101}]}), "invalid_value", "structure.layers[0].settings.weight_points[1].weight"),
            (one(settings={**settings, "weight_points": [{"position": 0.5, "weight": 1}, {"position": 0.5, "weight": 2}]}), "invalid_value", "structure.layers[0].settings.weight_points[1].position"),
            (one(settings={**settings, "weight_points": [{"position": 0.5}]}), "invalid_value", "structure.layers[0].settings.weight_points[0]"),
            (one(settings={**settings, "low_freq_weight": 0.45, "high_freq_weight": 10.0}), "invalid_value", "structure.layers[0].settings"),
            (one(settings=retired_pair), "invalid_value", "structure.layers[0].settings"),
        )
        for document, code, path in cases:
            with self.subTest(code=code, path=path), self.assertRaises(
                StructuralValidationError
            ) as caught:
                structure_document_to_definition(
                    self.definition, document, self.capabilities
                )
            self.assertEqual((caught.exception.code, caught.exception.path), (code, path))

    def test_pillow_constraints_name_the_field_and_direct_accepts_it(self) -> None:
        gradient = {**self.layer_settings, "gradient": True}
        with self.assertRaises(StructuralValidationError) as caught:
            structure_document_to_definition(
                self.definition,
                _document([_layer(gradient)], PILLOW_BULK_OUTPUT_PATH),
                self.capabilities,
            )
        self.assertEqual(caught.exception.path, "structure.layers[0].settings.gradient")
        candidate = structure_document_to_definition(
            self.definition, _document([_layer(gradient)]), self.capabilities
        )
        self.assertTrue(candidate.layers[0].settings["gradient"])

    def test_pillow_startup_accepts_any_bounded_gain(self) -> None:
        constraints = current_bars_settings_descriptor()["backend_constraints"]
        self.assertNotIn("gain", constraints[PILLOW_BULK_OUTPUT_PATH])
        settings = Settings()
        settings.shadow.enabled = False
        settings.peak.enabled = False
        settings.scaling.gain = 2.0
        definition = current_bars_runtime_definition(
            audio_settings=settings.audio,
            frequency_settings=settings.frequency,
            sensitivity_settings=settings.sensitivity,
            num_bins=64,
            output_backend=PILLOW_BULK_OUTPUT_PATH,
            layer_settings=current_bars_settings_from_runtime(settings),
        )
        self.assertEqual(definition.layers[0].settings["gain"], 2.0)


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
            graph, registry, settings=settings, width=8, height=8
        )

        self.assertIs(plan.layers[0].analyzer, plan.layers[1].analyzer)
        self.assertIsNot(plan.layers[0].analyzer, plan.layers[2].analyzer)
        self.assertEqual(len(analyzer.projections), 2)

    def test_floor_splits_projections_and_max_boost_does_not(self) -> None:
        # fft-level-controls criterion 6: the floor is a projection setting, max
        # auto boost a presentation one.
        settings, definition, capabilities, registry, analyzer, _plan, _controller = _runtime()
        base = dict(definition.layers[0].settings)
        first = replace(definition.layers[0], layer_id="first", settings=dict(base))
        boosted = replace(
            definition.layers[0],
            layer_id="boosted",
            settings={**base, "auto_level_max_boost_db": 30.0},
        )
        floored = replace(
            definition.layers[0],
            layer_id="floored",
            settings={**base, "noise_floor_dbfs": -40.0},
        )
        graph = compile_runtime_graph(
            replace(definition, layers=(first, boosted, floored)),
            capabilities,
        )
        plan = compile_current_bars_layer_plan(
            graph, registry, settings=settings, width=8, height=8
        )

        self.assertIs(plan.layers[0].analyzer, plan.layers[1].analyzer)
        self.assertIsNot(plan.layers[0].analyzer, plan.layers[2].analyzer)

    def test_aggregation_splits_projections_and_curve_and_spread_do_not(self) -> None:
        # fft-level-controls M4 criterion 4 (D-012, D-040).
        settings, definition, capabilities, registry, analyzer, _plan, _controller = _runtime()
        base = dict(definition.layers[0].settings)
        first = replace(definition.layers[0], layer_id="first", settings=dict(base))
        shaped = replace(
            definition.layers[0],
            layer_id="shaped",
            settings={
                **base,
                "response_curve": 0.5,
                "spread_radius": 4,
                "spread_falloff": 0.8,
            },
        )
        energy = replace(
            definition.layers[0],
            layer_id="energy",
            settings={**base, "aggregation": "band_energy"},
        )
        loudest = replace(
            definition.layers[0],
            layer_id="loudest",
            settings={**base, "aggregation": "max"},
        )
        graph = compile_runtime_graph(
            replace(definition, layers=(first, shaped, energy, loudest)),
            capabilities,
        )
        plan = compile_current_bars_layer_plan(
            graph, registry, settings=settings, width=8, height=8
        )

        self.assertIs(plan.layers[0].analyzer, plan.layers[1].analyzer)
        analyzers = {id(layer.analyzer) for layer in plan.layers}
        self.assertEqual(len(analyzers), 3)
        self.assertEqual(
            sorted(projection.candidate.aggregation for projection in analyzer.projections),
            ["band_energy", "max"],
        )

    def test_equivalent_analyzer_references_share_one_physical_lease(self) -> None:
        settings, definition, capabilities, _registry, _analyzer, _plan, _controller = _runtime()
        alias = replace(definition.analyzers[0], analyzer_id="alias")
        layers = (
            definition.layers[0],
            replace(definition.layers[0], layer_id="top", analyzer_id="alias"),
        )
        aliased = replace(definition, analyzers=(*definition.analyzers, alias), layers=layers)
        candidate = structure_document_to_definition(
            aliased,
            _document([
                _layer(definition.layers[0].settings, analyzer_id="alias"),
                _layer(definition.layers[0].settings, layer_id="top", analyzer_id="alias"),
            ]),
            capabilities,
        )
        graph = compile_runtime_graph(candidate, capabilities)
        analyzer = _Analyzer(8)
        registry = AnalyzerRegistry(
            graph,
            {STANDARD_ANALYZER_TYPE: lambda _definition: analyzer},
        )
        plan = compile_current_bars_layer_plan(
            graph, registry, settings=settings, width=8, height=8
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
                graph, registry, settings=settings, width=8, height=8
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
            graph, registry, settings=settings, width=64, height=64
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
            graph, registry, settings=settings, width=64, height=64
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
            graph, registry, settings=settings, width=64, height=64
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

    def test_pillow_paints_layers_bottom_to_top_with_their_own_gains(self) -> None:
        settings, definition, capabilities, registry, _analyzer, _plan, _controller = _runtime(
            PILLOW_BULK_OUTPUT_PATH
        )
        base = dict(definition.layers[0].settings)
        short = replace(
            definition.layers[0], layer_id="short",
            settings={**base, "gain": 1.5},
        )
        tall = replace(
            definition.layers[0], layer_id="tall",
            settings={**base, "gain": 3.0},
        )

        def frame(*layers):
            graph = compile_runtime_graph(replace(definition, layers=layers), capabilities)
            plan = compile_current_bars_layer_plan(
                graph, registry, settings=settings, width=64, height=64
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
                graph, registry, settings=settings, width=8, height=8
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


class _StepClock:
    """A monotonic clock that advances a fixed step per read."""

    def __init__(self, step: float = 1.0 / 86.0) -> None:
        self.step = step
        self.now = 0.0

    def __call__(self) -> float:
        self.now += self.step
        return self.now


class GainTests(unittest.TestCase):
    """fft-level-controls M1: gain multiplies bar height (D-001, D-002, D-021)."""

    @staticmethod
    def _plan(backend, **overrides):
        settings, definition, capabilities, registry, _analyzer, _plan, _ = _runtime(backend)
        layer = replace(
            definition.layers[0],
            settings={**definition.layers[0].settings, **overrides},
        )
        graph = compile_runtime_graph(replace(definition, layers=(layer,)), capabilities)
        width = 64 if backend == PILLOW_BULK_OUTPUT_PATH else 8
        plan = compile_current_bars_layer_plan(
            graph, registry, settings=settings, width=width, height=width,
            clock=_StepClock(),
        )
        return plan, width

    def test_doubling_gain_doubles_bars_on_both_backends_and_modes(self) -> None:
        cases = (
            (DIRECT_OUTPUT_PATH, True),
            (DIRECT_OUTPUT_PATH, False),
            (PILLOW_BULK_OUTPUT_PATH, True),
        )
        for backend, overflow in cases:
            with self.subTest(backend=backend, overflow=overflow):
                bars = {}
                for gain in (1.0, 2.0):
                    plan, _width = self._plan(backend, overflow=overflow, gain=gain)
                    for _ in range(3):
                        plan.prepare_frame()
                    bars[gain] = np.array(plan.layers[0].smoothed_bars(), dtype=np.float64)
                self.assertGreater(bars[1.0].max(), 0.0)
                np.testing.assert_allclose(bars[2.0], 2.0 * bars[1.0], rtol=1e-5)

    def test_painted_height_follows_gain_on_both_backends(self) -> None:
        for backend in (DIRECT_OUTPUT_PATH, PILLOW_BULK_OUTPUT_PATH):
            with self.subTest(backend=backend):
                lit = {}
                for gain in (0.5, 1.0):
                    plan, width = self._plan(backend, gain=gain)
                    plan.prepare_frame()
                    if backend == DIRECT_OUTPUT_PATH:
                        canvas = _RecordingCanvas()
                        plan.draw_frame(canvas)
                        lit[gain] = len(canvas.pixels)
                    else:
                        canvas = _BulkCanvas()
                        plan.draw_frame(canvas)
                        lit[gain] = int(np.array(canvas.images[0]).any(axis=-1).sum())
                    expected = sum(
                        min(int(value * width), width)
                        for value in plan.layers[0].smoothed_bars()
                    )
                    self.assertEqual(lit[gain], expected)
                self.assertGreater(lit[1.0], lit[0.5])

    def test_direct_peaks_track_gain(self) -> None:
        peaks = {}
        for gain in (1.0, 3.0):
            plan, _width = self._plan(DIRECT_OUTPUT_PATH, peak_enabled=True, gain=gain)
            plan.prepare_frame()
            presentation = plan.layers[0].presentation_state
            np.testing.assert_array_equal(presentation.peaks, presentation.smoothed)
            peaks[gain] = np.array(presentation.peaks, dtype=np.float64)
        self.assertGreater(peaks[1.0].max(), 0.0)
        np.testing.assert_allclose(peaks[3.0], 3.0 * peaks[1.0], rtol=1e-5)

    def test_default_layer_paints_what_the_retired_multiplier_painted(self) -> None:
        # Before M1 the default layer drew int(s * h * 1.5) pixels from bars s
        # scaled with sensitivity scalar 1.0; now gain 1.5 is in the bars.
        default_gain = next(
            field["default"]
            for field in current_bars_settings_descriptor()["fields"]
            if field["name"] == "gain"
        )
        self.assertEqual(default_gain, 1.5)
        self.assertEqual(Settings().scaling.gain, 1.5)
        unit, width = self._plan(DIRECT_OUTPUT_PATH, gain=1.0)
        unit.prepare_frame()
        expected = sum(
            min(int(value * width * 1.5), width)
            for value in unit.layers[0].smoothed_bars()
        )
        plan, _width = self._plan(DIRECT_OUTPUT_PATH)
        plan.prepare_frame()
        canvas = _RecordingCanvas()
        plan.draw_frame(canvas)
        self.assertGreater(expected, 0)
        self.assertEqual(len(canvas.pixels), expected)


class _BlockingActivePlan(ActiveLayerPlan):
    """Holds the render thread inside structure.activate until released."""

    __slots__ = ("entered", "release")

    def __init__(self, plan) -> None:
        super().__init__(plan)
        self.entered = threading.Event()
        self.release = threading.Event()

    def stage(self, candidate) -> None:
        self.entered.set()
        self.release.wait(2)
        super().stage(candidate)


class StructuralCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._reset_runtime(8)

    def _reset_runtime(self, width: int, active_plan_type=ActiveLayerPlan) -> None:
        self.runtime_width = width
        (
            self.settings,
            self.definition,
            self.capabilities,
            self.registry,
            self.analyzer,
            self.plan,
            self.controller,
        ) = _runtime(width=width)
        self.base = dict(self.definition.layers[0].settings)
        self.owner = active_plan_type(self.plan)
        self.bridge = FrameBoundaryRuntimeBridge(response_timeout=1.0)

    def _compiler(self, definition):
        # The same projection transaction main.py uses.
        graph = compile_runtime_graph(definition, self.capabilities)
        prepared = self.registry.begin_projection_prepare()
        try:
            plan = compile_current_bars_layer_plan(
                graph,
                self.registry,
                settings=self.settings,
                width=self.runtime_width,
                height=self.runtime_width,
            )
        except BaseException:
            prepared.rollback()
            raise
        return PreparedStructuralPlan(
            plan=plan, commit=prepared.commit, rollback=prepared.rollback
        )

    def _coordinator(self, compiler=None, deadline=0.9):
        return StructuralRuntimeCoordinator(
            controller=self.controller,
            active_plan=self.owner,
            initial_definition=self.definition,
            capabilities=self.capabilities,
            compile_definition=compiler or self._compiler,
            bridge=self.bridge,
            deadline_seconds=deadline,
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

    def _two_ranges(self):
        return _document([
            _layer(self.base),
            _layer(
                {**self.base, "min_freq": 2000.0, "max_freq": 6300.0},
                layer_id="high",
            ),
        ])

    def _assert_unchanged(self) -> None:
        self.assertIs(self.owner.active, self.plan)
        self.assertEqual(self.controller.revision, 0)
        self.assertEqual(self.registry.projection_count, 1)
        self.assertEqual(self.analyzer.projections, [])

    def test_success_returns_the_normalized_document_after_one_revision(self) -> None:
        coordinator = self._coordinator()
        document = self._two_ranges()
        result = self._exchange(coordinator, _payload(document))
        self.assertNotIn("error", result)
        snapshot = result["value"]
        self.assertEqual(snapshot["revision"], 1)
        structure = snapshot["structure"]
        self.assertEqual(structure["output_backend"], document["output_backend"])
        self.assertEqual(
            [
                {name: layer[name] for name in document["layers"][0]}
                for layer in structure["layers"]
            ],
            document["layers"],
        )
        self.assertEqual(structure["capabilities"]["limits"], {"max_layers": 8})
        self.assertEqual(structure["capabilities"]["actions"], ["structure.set"])
        self.assertNotIn("operations", structure["capabilities"])
        self.assertEqual(structure["available_analyzer_ids"], ["main-audio"])
        # Nothing live owns analyzer settings any more, so they are published.
        analyzer_settings = structure["analyzers"][0]["settings"]
        self.assertIn("block_size", analyzer_settings)
        self.assertIn("fft_size", analyzer_settings)
        self.assertNotIn("launch_error", structure)
        self.assertEqual(structure["layers"], document["layers"])
        self.assertEqual(
            structure["bin_layout"],
            {
                "analyzer_id": "main-audio",
                "sample_rate": analyzer_settings["sample_rate"],
                "fft_size": analyzer_settings["fft_size"],
                "num_bars": self.runtime_width,
                "spacing": "log",
                "bar_center": "arithmetic-midpoint",
                "bar_bins": "half-open",
                "weight_curve": "piecewise-linear-position",
            },
        )
        self.assertEqual(len(self.owner.active.layers), 2)
        self.assertEqual(self.registry.projection_count, 2)
        json.dumps(snapshot)

    def test_surviving_layers_keep_level_bars_and_peaks_across_an_update(self) -> None:
        # fft-level-controls D-007, D-028: state carries over by layer ID when the
        # render thread promotes the new plan, even with changed settings.
        for _frame in range(6):
            self.assertIsNotNone(self.owner.begin_frame().prepare_frame())
        old = self.plan.layers[0].presentation_state.scaler
        self.assertTrue(old.started)
        self.assertGreater(float(old.smoothed_bars.max()), 0.0)
        before = (
            old.envelope,
            old.last_time,
            old.smoothed_bars.copy(),
            old.peak_heights.copy(),
            old.peak_hold_remaining.copy(),
        )

        coordinator = self._coordinator()
        document = _document([
            _layer({**self.base, "gain": 3.0, "auto_level_release_seconds": 1.0}),
            _layer(self.base, layer_id="added"),
        ])
        result = self._exchange(coordinator, _payload(document))
        self.assertNotIn("error", result)
        active = self.owner.active
        self.assertIsNot(active, self.plan)
        kept, added = (layer.presentation_state for layer in active.layers)
        self.assertIsNot(kept.scaler, old)
        self.assertEqual(kept.scaler.scaling.gain, 3.0)
        self.assertEqual((kept.scaler.envelope, kept.scaler.last_time), before[:2])
        np.testing.assert_array_equal(kept.scaler.smoothed_bars, before[2])
        np.testing.assert_array_equal(kept.scaler.peak_heights, before[3])
        np.testing.assert_array_equal(kept.scaler.peak_hold_remaining, before[4])
        # The presentation still hands out the scaler's own arrays.
        self.assertIs(kept.smoothed_bars(), kept.scaler.smoothed_bars)
        # A layer ID new to the structure starts fresh.
        self.assertFalse(added.scaler.started)
        self.assertIsNone(added.scaler.last_time)
        self.assertEqual(float(added.scaler.smoothed_bars.max()), 0.0)

    def test_state_is_copied_when_the_plan_is_promoted_not_when_compiled(self) -> None:
        # D-028: frames the old plan renders after the candidate is compiled and
        # staged still reach the new layer, so the copy happens at promotion on
        # the render thread, not on the compiling (listener) thread.
        for _frame in range(3):
            self.owner.begin_frame().prepare_frame()
        prepared = self._compiler(
            replace(self.definition, layers=(
                replace(self.definition.layers[0], settings={**self.base, "gain": 2.0}),
            ))
        )
        self.owner.stage(prepared.plan)
        old = self.plan.layers[0].presentation_state.scaler
        for _frame in range(4):
            self.plan.prepare_frame()  # still the active plan
        latest = (old.envelope, old.last_time, old.smoothed_bars.copy())
        self.assertIs(self.owner.begin_frame(), prepared.plan)
        prepared.commit()
        kept = prepared.plan.layers[0].presentation_state.scaler
        self.assertEqual((kept.envelope, kept.last_time), latest[:2])
        np.testing.assert_array_equal(kept.smoothed_bars, latest[2])

    def test_a_renamed_layer_starts_fresh(self) -> None:
        for _frame in range(3):
            self.owner.begin_frame().prepare_frame()
        coordinator = self._coordinator()
        result = self._exchange(
            coordinator, _payload(_document([_layer(self.base, layer_id="renamed")]))
        )
        self.assertNotIn("error", result)
        scaler = self.owner.active.layers[0].presentation_state.scaler
        self.assertFalse(scaler.started)
        self.assertEqual(float(scaler.smoothed_bars.max()), 0.0)

    def test_an_empty_structure_activates_renders_blank_and_can_be_refilled(self) -> None:
        # M5 slice 1 criterion 5: the page may send an empty layer list.
        coordinator = self._coordinator()
        result = self._exchange(coordinator, _payload(_document([])))
        self.assertNotIn("error", result)
        self.assertEqual(result["value"]["revision"], 1)
        self.assertEqual(result["value"]["structure"]["layers"], [])
        for _frame in range(3):
            plan = self.owner.begin_frame()
            self.assertEqual(plan.prepare_frame().size, 0)
            canvas = _DirectCanvas()
            plan.draw_frame(canvas)
            self.assertEqual(canvas.clear_calls, 1)
        self.assertEqual(self.analyzer.get_calls, 0)

        result = self._exchange(coordinator, _payload(_document([_layer(self.base)]), revision=1))
        self.assertNotIn("error", result)
        self.assertEqual(result["value"]["revision"], 2)
        plan = self.owner.begin_frame()
        self.assertEqual(plan.prepare_frame().size, self.runtime_width)
        self.assertEqual(self.analyzer.get_calls, 1)

    def test_an_empty_launch_structure_is_accepted(self) -> None:
        coordinator = self._coordinator()
        self.assertTrue(coordinator.apply_launch_structure(json.dumps(_document([]))))
        self.assertEqual(coordinator.structure_snapshot()["layers"], [])
        self.assertNotIn("launch_error", coordinator.structure_snapshot())
        self.assertEqual(self.owner.begin_frame().prepare_frame().size, 0)

    def test_weight_points_are_sorted_in_the_snapshot_and_round_trip(self) -> None:
        coordinator = self._coordinator()
        unsorted = [
            {"position": 1, "weight": 4},
            {"position": 0.0, "weight": 0.5},
            {"position": 0.4, "weight": 2.5},
        ]
        document = _document([
            _layer({**self.base, "weight_points": unsorted}),
            _layer(
                {**self.base, "weight_points": [{"position": 0.5, "weight": 1.0}]},
                layer_id="flat",
            ),
        ])
        result = self._exchange(coordinator, _payload(document))
        self.assertNotIn("error", result)
        layers = result["value"]["structure"]["layers"]
        self.assertEqual(
            layers[0]["settings"]["weight_points"],
            [
                {"position": 0.0, "weight": 0.5},
                {"position": 0.4, "weight": 2.5},
                {"position": 1.0, "weight": 4.0},
            ],
        )
        # Only the points differ from the launch layer, so each needs its own view.
        self.assertEqual(self.registry.projection_count, 3)

        again = self._exchange(
            coordinator,
            _payload(_document(layers), revision=result["value"]["revision"]),
        )
        self.assertNotIn("error", again)
        self.assertEqual(again["value"]["structure"]["layers"], layers)

    def test_retired_structure_apply_and_patches_are_rejected(self) -> None:
        coordinator = self._coordinator()
        retired = self._exchange(coordinator, {
            "command": {
                "operation": "action",
                "action": "structure.apply",
                "args": {"operations": [{"op": "remove_layer", "layer_id": "current-bars"}]},
            },
            "expected_revision": 0,
        })
        self.assertEqual(
            (retired["error"].code, retired["error"].path),
            ("invalid_path", "structure.apply"),
        )
        patch = self._exchange(coordinator, {
            "command": {"operation": "patch", "changes": [
                {"path": "visualizer.gradient", "value": True},
            ]},
            "expected_revision": 0,
        })
        # D-051: patch is no longer a command operation at all.
        self.assertEqual(patch["error"].code, "invalid_operation")
        self._assert_unchanged()

    def test_public_layer_tool_compiles_and_activates_all_counts_and_backends(self) -> None:
        # Pillow's accepted bulk path supports only the qualified 64x64 geometry.
        self._reset_runtime(64)
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
                        [layer["layer_id"] for layer in snapshot["structure"]["layers"]],
                        ["current-bars", *(f"layer-{index}" for index in range(2, count + 1))],
                    )
                    self.assertEqual(len(self.owner.active.layers), count)
                    self.assertEqual(
                        len({id(layer.analyzer) for layer in self.owner.active.layers}),
                        1,
                    )
                    self.assertEqual(
                        len({id(layer.presentation_state) for layer in self.owner.active.layers}),
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
                self._capture_error(coordinator, _payload(self._two_ranges())),
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
                "command", _payload(_document([_layer(self.base)]))
            )
        self.assertEqual(busy.exception.code, "busy")

        canvas = _DirectCanvas()
        self.owner.active.prepare_frame()
        self.owner.active.draw_frame(canvas)
        self.assertEqual(canvas.clear_calls, 1)
        # Another revision lands while this one compiles.
        self.controller.revision += 1
        release.set()
        while request.is_alive() and time.monotonic() < deadline + 1:
            coordinator.process_pending(max_items=4)
            time.sleep(0.001)
        request.join(1)
        self.assertIsInstance(result["result"], RuntimeControlError)
        self.assertEqual(result["result"].code, "revision_conflict")
        self.assertIs(self.owner.active, self.plan)
        self.assertEqual(self.controller.revision, 1)
        # The render thread rolled back the projection the rejected plan made.
        self.assertEqual(self.registry.projection_count, 1)
        self.assertEqual(self.analyzer.projections, [])

    def test_compile_failures_name_the_layer_and_leave_projections_unchanged(self) -> None:
        coordinator = self._coordinator()
        # Pillow-bulk supports only 64x64, so this 8-wide runtime can't compile it.
        pillow = self._exchange(
            coordinator,
            _payload(_document([_layer(self.base)], PILLOW_BULK_OUTPUT_PATH)),
        )
        self.assertEqual(pillow["error"].code, "invalid_structure")
        self._assert_unchanged()

        nyquist = self._exchange(coordinator, _payload(_document([
            _layer({**self.base, "min_freq": 2000.0, "max_freq": 6300.0}, layer_id="mid"),
            _layer({**self.base, "max_freq": 24000.0}, layer_id="too-high"),
        ])))
        self.assertEqual(
            (nyquist["error"].code, nyquist["error"].path),
            ("invalid_value", "structure.layers[1].settings"),
        )
        self.assertIn("Nyquist", nyquist["error"].message)
        # The projection created for layer 0 was rolled back with the rest.
        self._assert_unchanged()

        # The registry transaction was released, so the next request works.
        ok = self._exchange(coordinator, _payload(self._two_ranges()))
        self.assertNotIn("error", ok)

    def test_unstarted_activation_times_out_rolls_back_and_is_never_swapped_in(self) -> None:
        compiled = threading.Event()
        proceed = threading.Event()

        def compiler(definition):
            prepared = self._compiler(definition)
            compiled.set()
            proceed.wait(1)
            return prepared

        coordinator = self._coordinator(compiler, deadline=0.3)
        result = {}
        request = threading.Thread(
            target=lambda: result.setdefault(
                "value",
                self._capture_error(coordinator, _payload(self._two_ranges())),
            )
        )
        request.start()
        deadline = time.monotonic() + 1
        while not compiled.is_set() and time.monotonic() < deadline:
            coordinator.process_pending(max_items=4)
            time.sleep(0.001)
        self.assertTrue(compiled.is_set())
        self.assertEqual(self.registry.projection_count, 2)
        # Stop servicing frame boundaries before the activation is submitted.
        proceed.set()
        request.join(1)
        self.assertFalse(request.is_alive())
        self.assertIsInstance(result["value"], RuntimeControlError)
        self.assertEqual(result["value"].code, "timeout")
        if isinstance(result["value"], RuntimeOperationTimeout):
            self.assertFalse(result["value"].started)

        # A late frame boundary skips the cancelled activation.
        coordinator.process_pending(max_items=4)
        self._assert_unchanged()
        self.assertEqual(len(coordinator.structure_snapshot()["layers"]), 1)

    def test_activation_stalled_past_deadline_keeps_its_projections(self) -> None:
        # fft-modular-runtime D-004 finding 2 / web-control-refactor DoD 7: the
        # render thread is inside structure.activate when the listener's
        # deadline expires. The activated plan's projections must survive.
        self._reset_runtime(8, active_plan_type=_BlockingActivePlan)
        self.bridge = FrameBoundaryRuntimeBridge(response_timeout=1.0, started_grace=0.05)
        coordinator = self._coordinator(deadline=0.3)
        result = {}
        listener = threading.Thread(
            target=lambda: result.setdefault(
                "value",
                self._capture_error(coordinator, _payload(self._two_ranges())),
            )
        )
        stop_render = threading.Event()

        def render():
            while not stop_render.is_set():
                coordinator.process_pending(max_items=4)
                time.sleep(0.001)

        render_thread = threading.Thread(target=render, daemon=True)
        # Never leave the render thread blocked or running if an assertion fails.
        self.addCleanup(stop_render.set)
        self.addCleanup(self.owner.release.set)
        listener.start()
        render_thread.start()
        self.assertTrue(self.owner.entered.wait(1))
        listener.join(2)
        self.assertFalse(listener.is_alive())
        error = result["value"]
        self.assertIsInstance(error, RuntimeOperationTimeout)
        self.assertTrue(error.started)
        # Nothing was rolled back while the render thread owned the activation.
        self.assertEqual(self.registry.projection_count, 2)
        self.assertEqual(len(self.analyzer.projections), 1)

        # A new transaction waits for the unfinished activation.
        with self.assertRaises(RuntimeControlError) as busy:
            coordinator.handle_runtime_operation(
                "command", _payload(_document([_layer(self.base)]), revision=0)
            )
        self.assertEqual(busy.exception.code, "busy")

        self.owner.release.set()
        deadline = time.monotonic() + 1
        while self.controller.revision != 1 and time.monotonic() < deadline:
            time.sleep(0.001)
        stop_render.set()
        render_thread.join(1)

        self.assertEqual(self.controller.revision, 1)
        active = self.owner.begin_frame()
        self.assertEqual([layer.layer_id for layer in active.layers], ["current-bars", "high"])
        self.assertIn(active.layers[1].analyzer, self.analyzer.projections)
        self.assertEqual(self.registry.projection_count, 2)
        # The next frame reads every projection the activated plan uses.
        self.assertIsNotNone(active.prepare_frame())
        active.draw_frame(_DirectCanvas())

        # Once the activation finished, transactions proceed again.
        self.owner.release.set()
        follow_up = self._exchange(
            coordinator, _payload(_document([_layer(self.base)]), revision=1)
        )
        self.assertNotIn("error", follow_up)
        self.assertEqual(self.registry.projection_count, 1)

    def test_render_thread_owns_rollback_for_a_claimed_activation(self) -> None:
        class _FailingStage(ActiveLayerPlan):
            __slots__ = ("fail",)

            def stage(self, candidate):
                if self.fail:
                    self.fail = False
                    raise TypeError("stage failed")
                super().stage(candidate)

        self.owner = _FailingStage(self.plan)
        self.owner.fail = True
        rollbacks = []

        def counting(definition):
            prepared = self._compiler(definition)

            def rollback():
                rollbacks.append(definition)
                prepared.rollback()

            return PreparedStructuralPlan(
                plan=prepared.plan, commit=prepared.commit, rollback=rollback
            )

        coordinator = self._coordinator(counting)
        failed = self._exchange(coordinator, _payload(self._two_ranges()))
        self.assertEqual(failed["error"].code, "handler_error")
        # Rolled back once, by the render thread; the listener left it alone.
        self.assertEqual(len(rollbacks), 1)
        self._assert_unchanged()
        ok = self._exchange(coordinator, _payload(self._two_ranges()))
        self.assertNotIn("error", ok)

    def test_failure_after_swap_never_frees_the_live_plan_projections(self) -> None:
        def failing_commit(definition):
            prepared = self._compiler(definition)

            def commit():
                raise RuntimeError("commit failed")

            return PreparedStructuralPlan(
                plan=prepared.plan, commit=commit, rollback=prepared.rollback
            )

        coordinator = self._coordinator(failing_commit)
        failed = self._exchange(coordinator, _payload(self._two_ranges()))
        self.assertEqual(failed["error"].code, "handler_error")
        live = self.owner.active
        self.assertIsNot(live, self.plan)
        self.assertIn(live.layers[1].analyzer, self.analyzer.projections)
        self.assertEqual(self.registry.projection_count, 2)
        self.assertIsNotNone(live.prepare_frame())

    def test_snapshot_layers_round_trip_unchanged(self) -> None:
        coordinator = self._coordinator()
        first = self._exchange(coordinator, _payload(self._two_ranges()))
        structure = first["value"]["structure"]
        for layer in structure["layers"]:
            self.assertEqual(set(layer), {
                "layer_id", "layer_type", "analyzer_id", "visible", "settings"
            })
        echoed = self._exchange(coordinator, _payload(
            {
                "output_backend": structure["output_backend"],
                "layers": structure["layers"],
            },
            revision=first["value"]["revision"],
        ))
        self.assertNotIn("error", echoed)
        self.assertEqual(echoed["value"]["structure"]["layers"], structure["layers"])

    def test_request_shape_and_stale_base_are_rejected_without_mutation(self) -> None:
        coordinator = self._coordinator()
        document = _document([_layer(self.base, visible=False)])
        missing = _payload(document)
        del missing["expected_revision"]
        boolean = _payload(document)
        boolean["expected_revision"] = True
        for payload, code, path in (
            (missing, "invalid_value", "expected_revision"),
            (boolean, "invalid_value", "expected_revision"),
        ):
            with self.subTest(code=code, path=path), self.assertRaises(
                RuntimeControlError
            ) as caught:
                coordinator.handle_runtime_operation("command", payload)
            self.assertEqual((caught.exception.code, caught.exception.path), (code, path))

        operations = self._exchange(coordinator, _payload({"operations": []}))
        self.assertEqual(
            (operations["error"].code, operations["error"].path),
            ("invalid_structure", "structure"),
        )
        stale = self._exchange(coordinator, _payload(document, revision=3))
        self.assertEqual(stale["error"].code, "revision_conflict")
        invalid = self._exchange(
            coordinator, _payload(_document([_layer(self.base, analyzer_id="missing")]))
        )
        self.assertEqual(
            (invalid["error"].code, invalid["error"].path),
            ("invalid_reference", "structure.layers[0].analyzer_id"),
        )
        self._assert_unchanged()
        self.assertTrue(coordinator.structure_snapshot()["layers"][0]["visible"])

    def test_default_deadline_and_grace_finish_before_supervisor_timeout(self) -> None:
        deadline = inspect.signature(StructuralRuntimeCoordinator).parameters[
            "deadline_seconds"
        ].default
        grace = inspect.signature(FrameBoundaryRuntimeBridge).parameters[
            "started_grace"
        ].default
        supervisor_timeout = inspect.signature(VisualizationSupervisor).parameters[
            "runtime_request_timeout"
        ].default
        self.assertLess(deadline + grace, supervisor_timeout)

    def test_valid_launch_structure_is_active_before_any_frame(self) -> None:
        coordinator = self._coordinator()
        self.assertTrue(coordinator.apply_launch_structure(json.dumps(self._two_ranges())))
        active = self.owner.active
        self.assertIsNot(active, self.plan)
        self.assertEqual([layer.layer_id for layer in active.layers], ["current-bars", "high"])
        self.assertEqual(self.registry.projection_count, 2)
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot["revision"], 0)
        self.assertEqual(
            [layer["layer_id"] for layer in snapshot["structure"]["layers"]],
            ["current-bars", "high"],
        )
        self.assertNotIn("launch_error", snapshot["structure"])

    def test_invalid_launch_structure_keeps_default_and_reports_error(self) -> None:
        cases = (
            ("{not json", "invalid_structure", "structure"),
            (json.dumps({"output_backend": "direct"}), "invalid_structure", "structure"),
            (
                json.dumps(_document([_layer({**self.base, "theme": "plaid"})])),
                "invalid_value",
                "structure.layers[0].settings.theme",
            ),
            (
                json.dumps(_document([_layer({**self.base, "max_freq": 24000.0})])),
                "invalid_value",
                "structure.layers[0].settings",
            ),
            ("x" * (MAX_LAUNCH_STRUCTURE_BYTES + 1), "invalid_structure", "structure"),
        )
        for raw, code, path in cases:
            with self.subTest(code=code, path=path):
                self._reset_runtime(8)
                coordinator = self._coordinator()
                self.assertFalse(coordinator.apply_launch_structure(raw))
                self._assert_unchanged()
                error = self.controller.snapshot()["structure"]["launch_error"]
                self.assertEqual((error["code"], error["path"]), (code, path))
                self.assertTrue(error["message"])

        # A later successful structure.set clears the stale launch error.
        result = self._exchange(coordinator, _payload(self._two_ranges()))
        self.assertNotIn("error", result)
        self.assertNotIn("launch_error", result["value"]["structure"])

    def _register_custom_theme(self, name: str) -> None:
        saved = dict(theme_registry._THEME_REGISTRY)

        def restore() -> None:
            theme_registry._THEME_REGISTRY.clear()
            theme_registry._THEME_REGISTRY.update(saved)

        self.addCleanup(restore)
        theme_registry.register_theme(
            color_stop_theme(name, "test", [(0.0, (255, 0, 0)), (1.0, (0, 0, 255))])
        )

    def test_fallback_theme_is_the_descriptor_default(self) -> None:
        theme = next(
            field for field in current_bars_settings_descriptor()["fields"]
            if field["name"] == "theme"
        )
        self.assertEqual(theme["default"], FALLBACK_THEME)

    def test_launch_layer_with_missing_custom_theme_uses_default(self) -> None:
        # fft-themes M3 criterion 6 (D-013): one layer falls back, the rest launch.
        self._register_custom_theme("my_here")
        coordinator = self._coordinator()
        document = _document([
            _layer({**self.base, "theme": "my_gone"}),
            _layer({**self.base, "theme": "my_here", "min_freq": 2000.0}, layer_id="high"),
            _layer({**self.base, "theme": "warm", "min_freq": 3000.0}, layer_id="top"),
        ])
        raw = json.dumps(document)
        self.assertTrue(coordinator.apply_launch_structure(raw))
        structure = self.controller.snapshot()["structure"]
        self.assertNotIn("launch_error", structure)
        self.assertEqual(
            [layer["settings"]["theme"] for layer in structure["layers"]],
            [FALLBACK_THEME, "my_here", "warm"],
        )
        self.assertEqual(structure["theme_fallbacks"], [{
            "path": "structure.layers[0].settings.theme",
            "layer_id": "current-bars",
            "theme": "my_gone",
        }])
        self.assertEqual(json.loads(raw), document)  # the input is not mutated

        # A later structure.set clears the note, like the launch error.
        result = self._exchange(coordinator, _payload(self._two_ranges()))
        self.assertNotIn("error", result)
        self.assertNotIn("theme_fallbacks", result["value"]["structure"])

    def test_launch_without_fallbacks_publishes_none(self) -> None:
        coordinator = self._coordinator()
        self.assertTrue(coordinator.apply_launch_structure(json.dumps(self._two_ranges())))
        self.assertNotIn("theme_fallbacks", self.controller.snapshot()["structure"])

    def test_refused_launch_reports_error_and_no_fallbacks(self) -> None:
        coordinator = self._coordinator()
        raw = json.dumps(_document([
            _layer({**self.base, "theme": "my_gone"}),
            _layer({**self.base, "max_freq": 24000.0}, layer_id="high"),
        ]))
        self.assertFalse(coordinator.apply_launch_structure(raw))
        self._assert_unchanged()
        structure = self.controller.snapshot()["structure"]
        self.assertEqual(structure["launch_error"]["path"], "structure.layers[1].settings")
        self.assertNotIn("theme_fallbacks", structure)

    def test_live_structure_set_still_refuses_missing_custom_theme(self) -> None:
        coordinator = self._coordinator()
        result = self._exchange(
            coordinator, _payload(_document([_layer({**self.base, "theme": "my_gone"})]))
        )
        error = result.get("error")
        self.assertIsInstance(error, RuntimeControlError)
        self.assertEqual(
            (error.code, error.path), ("invalid_value", "structure.layers[0].settings.theme")
        )
        self._assert_unchanged()

    def test_fallback_only_touches_custom_theme_names(self) -> None:
        for theme in ("plaid", "my_", "MY_GONE", "my-gone", 7, None):
            with self.subTest(theme=theme):
                document = _document([_layer({**self.base, "theme": theme})])
                self.assertEqual(launch_theme_fallbacks(document), (document, []))
        for document in ([], {"layers": "x"}, {"layers": [1, {"settings": []}]}):
            with self.subTest(document=document):
                self.assertEqual(launch_theme_fallbacks(document), (document, []))

    @staticmethod
    def _capture_error(coordinator, payload):
        try:
            return coordinator.handle_runtime_operation("command", payload)
        except Exception as exc:
            return exc


class DebugModeRemovalTests(unittest.TestCase):
    def test_debug_mode_is_absent_from_snapshot_capabilities_and_sources(self) -> None:
        _settings, definition, capabilities, _registry, _analyzer, plan, controller = _runtime()
        visualizer = plan.layers[0].presentation_state.visualizer
        self.assertFalse(hasattr(visualizer, "debug_mode"))
        self.assertFalse(hasattr(BarsUnifiedVisualizer, "toggle_debug"))
        with self.assertRaises(RuntimeControlError):
            controller.handle_runtime_operation("command", {"command": {
                "operation": "patch",
                "changes": [{"path": "visualizer.debug", "value": True}],
            }})
        self.assertEqual(controller.revision, 0)
        encoded = json.dumps({
            "snapshot": controller.snapshot(),
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
