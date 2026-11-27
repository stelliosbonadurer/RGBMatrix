"""Hardware-free tests for the Step 2 in-process analyzer registry."""

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest import mock

try:
    import numpy as np
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("analyzer registry tests require NumPy") from exc


FFT_ROOT = Path(__file__).resolve().parent.parent / "my_programs" / "fft_program"
MAIN_PATH = FFT_ROOT / "main.py"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))


_sounddevice = ModuleType("sounddevice")
_sounddevice.query_devices = lambda *_args, **_kwargs: {
    "name": "test input",
    "default_samplerate": 44_100,
}
_sounddevice.InputStream = object
_prior_sounddevice = sys.modules.get("sounddevice")
sys.modules["sounddevice"] = _sounddevice
try:
    from config.settings import (  # noqa: E402
        AudioSettings,
        FrequencySettings,
        SensitivitySettings,
    )
    from config.weighting import legacy_weight_points  # noqa: E402
    from core.analyzer_registry import (  # noqa: E402
        AnalyzerRegistry,
        AnalyzerRegistryError,
        STANDARD_ANALYZER_TYPE,
        compile_standard_analyzer_plan,
        standard_analyzer_capabilities,
        standard_analyzer_definition,
    )
    from core.audio import AudioProcessor  # noqa: E402
    from core.runtime_graph import (  # noqa: E402
        AnalyzerDefinition,
        DIRECT_OUTPUT_BACKEND,
        LayerCapability,
        LayerDefinition,
        RuntimeCapabilities,
        RuntimeDefinition,
        compile_runtime_graph,
    )
finally:
    if _prior_sounddevice is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = _prior_sounddevice


def _settings():
    return AudioSettings(), FrequencySettings(), SensitivitySettings()


def _alias_plan():
    audio, frequency, sensitivity = _settings()
    analyzers = tuple(
        standard_analyzer_definition(
            analyzer_id,
            audio_settings=audio,
            frequency_settings=frequency,
            sensitivity_settings=sensitivity,
            num_bins=64,
        )
        for analyzer_id in ("music-primary", "music-alias")
    )
    base_capabilities = standard_analyzer_capabilities()
    capabilities = RuntimeCapabilities(
        analyzers=base_capabilities.analyzers,
        layers=(
            LayerCapability(
                layer_type="test-layer",
                analyzer_types=frozenset({STANDARD_ANALYZER_TYPE}),
                output_backends=frozenset({DIRECT_OUTPUT_BACKEND}),
            ),
        ),
    )
    return compile_runtime_graph(
        RuntimeDefinition(
            analyzers=analyzers,
            layers=(
                LayerDefinition("bottom", "test-layer", "music-primary"),
                LayerDefinition("top", "test-layer", "music-alias"),
            ),
            output_backend=DIRECT_OUTPUT_BACKEND,
        ),
        capabilities,
    )


class RecordingRuntime:
    def __init__(
        self,
        *,
        setup_failure=None,
        start_failure=None,
        stop_failure=None,
    ) -> None:
        self.setup_failure = setup_failure
        self.start_failure = start_failure
        self.stop_failure = stop_failure
        self.setup_calls = 0
        self.start_calls = 0
        self.stop_calls = 0
        self.result = np.zeros(64, dtype=np.float32)
        self.leased = False
        self.projections = []

    def setup(self) -> int:
        self.setup_calls += 1
        if self.setup_failure is not None:
            raise self.setup_failure
        return 44_100

    def start(self) -> None:
        self.start_calls += 1
        if self.start_failure is not None:
            raise self.start_failure

    def stop(self) -> None:
        self.stop_calls += 1
        if self.stop_failure is not None:
            raise self.stop_failure

    def get_fft_magnitudes(self):
        if self.leased:
            raise RuntimeError("result already leased")
        self.leased = True
        return self.result

    def release_fft_magnitudes(self) -> None:
        if not self.leased:
            raise RuntimeError("no result lease")
        self.leased = False

    def create_projection(self, candidate, noise_floor_dbfs):
        projection = RecordingRuntime()
        projection.candidate = candidate
        projection.noise_floor_dbfs = noise_floor_dbfs
        self.projections.append(projection)
        return projection

    def remove_projection(self, projection):
        self.projections.remove(projection)


class AnalyzerRegistryCompilationTests(unittest.TestCase):
    def test_standard_identity_includes_source_and_effective_capture_format(self) -> None:
        audio, frequency, sensitivity = _settings()
        audio.source_id = "bluetooth"
        audio.device = "hw:10,1"
        audio.resolved_device = "Loopback capture"
        audio.resolved_sample_rate = 44_100
        definition = standard_analyzer_definition(
            "music",
            audio_settings=audio,
            frequency_settings=frequency,
            sensitivity_settings=sensitivity,
            num_bins=64,
        )

        self.assertEqual(definition.settings["source"], "bluetooth")
        self.assertEqual(definition.settings["resolved_device"], "Loopback capture")
        self.assertEqual(definition.settings["sample_rate"], 44_100)
        self.assertEqual(definition.settings["capture_channels"], 1)
        self.assertEqual(definition.settings["capture_dtype"], "float32")

    def test_equivalent_layer_references_construct_and_resolve_one_runtime(self) -> None:
        plan = _alias_plan()
        runtime = RecordingRuntime()
        factory_calls = []

        def factory(definition):
            factory_calls.append(definition)
            return runtime

        registry = AnalyzerRegistry(plan, {STANDARD_ANALYZER_TYPE: factory})

        self.assertEqual(registry.runtime_count, 1)
        self.assertEqual(len(factory_calls), 1)
        bottom = registry.resolve(plan.layers[0].analyzer)
        top = registry.resolve(plan.layers[1].analyzer)
        self.assertIs(bottom, runtime)
        self.assertIs(top, runtime)
        self.assertIs(bottom.get_fft_magnitudes(), runtime.result)
        with self.assertRaisesRegex(RuntimeError, "already leased"):
            top.get_fft_magnitudes()
        bottom.release_fft_magnitudes()

    def test_non_equivalent_fft_analyzers_are_deferred_before_factory_execution(self) -> None:
        audio, frequency, sensitivity = _settings()
        first = standard_analyzer_definition(
            "first",
            audio_settings=audio,
            frequency_settings=frequency,
            sensitivity_settings=sensitivity,
            num_bins=64,
        )
        second = AnalyzerDefinition(
            analyzer_id="second",
            analyzer_type=first.analyzer_type,
            result_shape=first.result_shape,
            result_dtype=first.result_dtype,
            settings={**first.settings, "noise_floor_dbfs": -50.0},
        )
        plan = compile_runtime_graph(
            RuntimeDefinition(
                analyzers=(first, second),
                layers=(),
                output_backend=DIRECT_OUTPUT_BACKEND,
            ),
            standard_analyzer_capabilities(),
        )
        factory = mock.Mock(return_value=RecordingRuntime())
        with self.assertRaisesRegex(
            AnalyzerRegistryError, "exactly one canonical FFT transform"
        ):
            AnalyzerRegistry(plan, {STANDARD_ANALYZER_TYPE: factory})
        factory.assert_not_called()

    def test_missing_factory_fails_before_lifecycle(self) -> None:
        plan = _alias_plan()
        with self.assertRaisesRegex(AnalyzerRegistryError, "no runtime factory"):
            AnalyzerRegistry(plan, {})

    def test_standard_plan_snapshots_result_affecting_inputs(self) -> None:
        audio, frequency, sensitivity = _settings()
        first = compile_standard_analyzer_plan(
            audio_settings=audio,
            frequency_settings=frequency,
            sensitivity_settings=sensitivity,
            num_bins=64,
            output_backend=DIRECT_OUTPUT_BACKEND,
        )
        sensitivity.noise_floor_dbfs = -50.0
        second = compile_standard_analyzer_plan(
            audio_settings=audio,
            frequency_settings=frequency,
            sensitivity_settings=sensitivity,
            num_bins=64,
            output_backend=DIRECT_OUTPUT_BACKEND,
        )

        self.assertNotEqual(
            first.analyzers[0].canonical_key,
            second.analyzers[0].canonical_key,
        )


class AnalyzerRegistryLifecycleTests(unittest.TestCase):
    def test_context_starts_and_stops_one_runtime_for_all_aliases(self) -> None:
        plan = _alias_plan()
        runtime = RecordingRuntime()
        registry = AnalyzerRegistry(
            plan,
            {STANDARD_ANALYZER_TYPE: lambda _definition: runtime},
        )

        with registry as active:
            self.assertIs(active, registry)
            self.assertEqual(registry.state, "running")
            self.assertEqual(runtime.setup_calls, 1)
            self.assertEqual(runtime.start_calls, 1)
            self.assertEqual(runtime.stop_calls, 0)

        self.assertEqual(registry.state, "stopped")
        self.assertEqual(runtime.stop_calls, 1)
        registry.stop()
        self.assertEqual(runtime.stop_calls, 1)

    def test_setup_failure_is_explicit_and_does_not_start(self) -> None:
        plan = _alias_plan()
        runtime = RecordingRuntime(setup_failure=RuntimeError("setup failed"))
        registry = AnalyzerRegistry(
            plan,
            {STANDARD_ANALYZER_TYPE: lambda _definition: runtime},
        )

        with self.assertRaisesRegex(RuntimeError, "setup failed"):
            registry.setup()

        self.assertEqual(registry.state, "failed")
        self.assertEqual(runtime.start_calls, 0)

    def test_start_failure_runs_bounded_cleanup(self) -> None:
        plan = _alias_plan()
        runtime = RecordingRuntime(start_failure=RuntimeError("start failed"))
        registry = AnalyzerRegistry(
            plan,
            {STANDARD_ANALYZER_TYPE: lambda _definition: runtime},
        )
        registry.setup()

        with self.assertRaisesRegex(RuntimeError, "start failed"):
            registry.start()

        self.assertEqual(registry.state, "failed")
        self.assertEqual(runtime.start_calls, 1)
        self.assertEqual(runtime.stop_calls, 1)

    def test_stop_failure_is_reported_and_registry_cannot_repeat_shutdown(self) -> None:
        plan = _alias_plan()
        runtime = RecordingRuntime(stop_failure=RuntimeError("stop failed"))
        registry = AnalyzerRegistry(
            plan,
            {STANDARD_ANALYZER_TYPE: lambda _definition: runtime},
        )
        registry.setup()
        registry.start()

        with self.assertRaisesRegex(RuntimeError, "stop failed"):
            registry.stop()

        self.assertEqual(registry.state, "stopped")
        self.assertEqual(runtime.stop_calls, 1)
        registry.stop()
        self.assertEqual(runtime.stop_calls, 1)


class RealAnalyzerRegistryBehaviorTests(unittest.TestCase):
    def _processor_and_registry(self):
        audio, frequency, sensitivity = _settings()
        processor = AudioProcessor(audio, frequency, sensitivity, 64)
        plan = compile_standard_analyzer_plan(
            audio_settings=audio,
            frequency_settings=frequency,
            sensitivity_settings=sensitivity,
            num_bins=64,
            output_backend=DIRECT_OUTPUT_BACKEND,
        )
        registry = AnalyzerRegistry(
            plan,
            {STANDARD_ANALYZER_TYPE: lambda _definition: processor},
        )
        return processor, registry, registry.resolve(plan.analyzers[0])

    def test_registry_preserves_exact_numerical_result_and_fixed_slots(self) -> None:
        processor, registry, analyzer = self._processor_and_registry()
        self.assertIs(analyzer, processor)
        slot_ids = tuple(id(slot) for slot in processor._analysis_transport.slots)
        registry.setup()
        positions = np.arange(512, dtype=np.float32)
        samples = (
            0.7 * np.sin(2 * np.pi * positions / 31)
            + 0.2 * np.cos(2 * np.pi * positions / 9)
        ).astype(np.float32)
        processor._audio_callback(samples.reshape(-1, 1), 512, None, None)
        self.assertTrue(processor._process_latest_samples_once())

        actual = analyzer.get_fft_magnitudes()
        expected = np.empty(64, dtype=np.float32)
        processor._analyze_into(
            samples,
            processor.active_plan,
            processor._worker_state.noise_floor,  # raw units, from the dBFS setting
            expected,
        )

        self.assertIs(actual, processor._analysis_transport.slots[0])
        self.assertEqual(actual.shape, (64,))
        self.assertEqual(actual.dtype, np.float32)
        np.testing.assert_array_equal(actual, expected)
        analyzer.release_fft_magnitudes()
        self.assertEqual(
            tuple(id(slot) for slot in processor._analysis_transport.slots),
            slot_ids,
        )
        registry.stop()

    def test_worker_failure_surface_remains_direct(self) -> None:
        plan = _alias_plan()

        class FailedRuntime(RecordingRuntime):
            def get_fft_magnitudes(self):
                raise RuntimeError("analysis worker failed")

        runtime = FailedRuntime()
        registry = AnalyzerRegistry(
            plan,
            {STANDARD_ANALYZER_TYPE: lambda _definition: runtime},
        )
        analyzer = registry.resolve(plan.layers[0].analyzer)

        with self.assertRaisesRegex(RuntimeError, "analysis worker failed"):
            analyzer.get_fft_magnitudes()

    def test_distinct_layer_ranges_run_one_rfft_and_share_equivalent_views(self) -> None:
        audio, frequency, sensitivity = _settings()
        processor = AudioProcessor(audio, frequency, sensitivity, 64)
        plan = compile_standard_analyzer_plan(
            audio_settings=audio,
            frequency_settings=frequency,
            sensitivity_settings=sensitivity,
            num_bins=64,
            output_backend=DIRECT_OUTPUT_BACKEND,
        )
        registry = AnalyzerRegistry(
            plan,
            {STANDARD_ANALYZER_TYPE: lambda _definition: processor},
        )
        registry.setup()
        low_settings = {
            "min_freq": 80.0,
            "max_freq": 1950.0,
            "noise_floor_dbfs": -120.0,
            "weight_points": legacy_weight_points(),
        }
        high_settings = {
            **low_settings,
            "min_freq": 2000.0,
            "max_freq": 6300.0,
        }
        low = registry.resolve_projection(plan.analyzers[0], low_settings)
        low_alias = registry.resolve_projection(plan.analyzers[0], low_settings)
        high = registry.resolve_projection(plan.analyzers[0], high_settings)
        self.assertIs(low, low_alias)
        self.assertIsNot(low, high)
        reweighted = registry.resolve_projection(
            plan.analyzers[0],
            {**low_settings, "weight_points": [{"position": 0.5, "weight": 2.0}]},
        )
        reordered = registry.resolve_projection(
            plan.analyzers[0],
            {**low_settings, "weight_points": list(reversed(legacy_weight_points()))},
        )
        self.assertIsNot(low, reweighted)
        self.assertIs(low, reordered)
        primary_alias = registry.resolve_projection(
            plan.analyzers[0],
            {
                "min_freq": frequency.active_min_freq,
                "max_freq": frequency.active_max_freq,
                "noise_floor_dbfs": sensitivity.noise_floor_dbfs,
                "weight_points": legacy_weight_points(),
            },
        )
        self.assertIs(primary_alias, processor)

        positions = np.arange(512, dtype=np.float32)
        samples = (
            np.sin(2 * np.pi * 300 * positions / 44_100)
            + 0.5 * np.sin(2 * np.pi * 4000 * positions / 44_100)
        ).astype(np.float32)
        with mock.patch("numpy.fft.rfft", wraps=np.fft.rfft) as rfft:
            processor._audio_callback(samples.reshape(-1, 1), 512, None, None)
            self.assertTrue(processor._process_latest_samples_once())
        self.assertEqual(rfft.call_count, 1)

        low_result = low.get_fft_magnitudes().copy()
        low.release_fft_magnitudes()
        high_result = high.get_fft_magnitudes().copy()
        high.release_fft_magnitudes()
        self.assertFalse(np.array_equal(low_result, high_result))
        registry.stop()

    def test_multiple_projection_views_open_one_input_stream(self) -> None:
        audio, frequency, sensitivity = _settings()
        processor = AudioProcessor(audio, frequency, sensitivity, 64)
        plan = compile_standard_analyzer_plan(
            audio_settings=audio,
            frequency_settings=frequency,
            sensitivity_settings=sensitivity,
            num_bins=64,
            output_backend=DIRECT_OUTPUT_BACKEND,
        )
        registry = AnalyzerRegistry(
            plan,
            {STANDARD_ANALYZER_TYPE: lambda _definition: processor},
        )
        registry.setup()
        registry.resolve_projection(
            plan.analyzers[0],
            {
                "min_freq": 80.0,
                "max_freq": 1950.0,
                "noise_floor_dbfs": -120.0,
                "weight_points": legacy_weight_points(),
            },
        )

        class Stream:
            def __init__(self):
                self.start_calls = 0
                self.stop_calls = 0
                self.close_calls = 0

            def start(self): self.start_calls += 1
            def stop(self): self.stop_calls += 1
            def close(self): self.close_calls += 1

        stream = Stream()
        with mock.patch("core.audio.sd.InputStream", return_value=stream) as opened:
            registry.start()
            registry.stop()
        opened.assert_called_once()
        self.assertEqual(
            (stream.start_calls, stream.stop_calls, stream.close_calls),
            (1, 1, 1),
        )

    def test_projection_prepare_rolls_back_and_retires_after_commit(self) -> None:
        audio, frequency, sensitivity = _settings()
        processor = AudioProcessor(audio, frequency, sensitivity, 64)
        plan = compile_standard_analyzer_plan(
            audio_settings=audio,
            frequency_settings=frequency,
            sensitivity_settings=sensitivity,
            num_bins=64,
            output_backend=DIRECT_OUTPUT_BACKEND,
        )
        registry = AnalyzerRegistry(
            plan,
            {STANDARD_ANALYZER_TYPE: lambda _definition: processor},
        )
        low = {
            "min_freq": 80.0, "max_freq": 1950.0, "noise_floor_dbfs": -65.0,
            "weight_points": legacy_weight_points(),
        }
        high = {**low, "min_freq": 2000.0, "max_freq": 6300.0}

        rolled_back = registry.begin_projection_prepare()
        registry.resolve_projection(plan.analyzers[0], low)
        self.assertEqual(len(processor._projection_records), 2)
        rolled_back.rollback()
        self.assertEqual(len(processor._projection_records), 1)

        accepted = registry.begin_projection_prepare()
        registry.resolve_projection(plan.analyzers[0], low)
        accepted.commit()
        self.assertEqual(len(processor._projection_records), 2)

        replacement = registry.begin_projection_prepare()
        registry.resolve_projection(plan.analyzers[0], high)
        replacement.commit()
        self.assertEqual(len(processor._projection_records), 2)

    def test_layer_plan_resolves_registry_once_outside_the_frame_loop(self) -> None:
        source = MAIN_PATH.read_text(encoding="utf-8")
        loop = source[source.index("            while True:"):]
        layer_plan_source = (
            FFT_ROOT / "core" / "layer_plan.py"
        ).read_text(encoding="utf-8")

        self.assertIn("analyzer_registry.resolve(", layer_plan_source)
        self.assertIn("compile_current_bars_layer_plan(", source[: source.index("            while True:")])
        self.assertNotIn("analyzer_registry", loop)
        self.assertIn("frame_plan.prepare_frame()", loop)
        self.assertNotIn("get_fft_magnitudes()", loop)
        self.assertNotIn("release_fft_magnitudes()", loop)


if __name__ == "__main__":
    unittest.main()
