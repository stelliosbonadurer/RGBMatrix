"""Hardware-free contract tests for the minimal FFT pipeline interfaces."""

from __future__ import annotations

from dataclasses import replace
import inspect
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest import mock

try:
    import numpy as np
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("FFT pipeline tests require the runtime NumPy dependency") from exc


FFT_ROOT = Path(__file__).resolve().parent.parent / "my_programs" / "fft_program"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))


# AudioProcessor only needs sounddevice when a real stream starts.  Provide the
# import surface here so these contract tests remain independent of PortAudio.
_sounddevice = ModuleType("sounddevice")
_sounddevice.query_devices = lambda *_args, **_kwargs: {
    "name": "test input",
    "default_samplerate": 44_100,
}


class _UnexpectedInputStream:
    def __init__(self, *_args, **_kwargs) -> None:
        raise AssertionError("hardware stream must not start in interface tests")


_sounddevice.InputStream = _UnexpectedInputStream
_prior_sounddevice = sys.modules.get("sounddevice")
_prior_core = sys.modules.get("core")
_prior_core_audio = sys.modules.get("core.audio")
sys.modules["sounddevice"] = _sounddevice
try:
    from config.settings import (  # noqa: E402
        AudioSettings,
        FrequencySettings,
        LayerConfig,
        SensitivitySettings,
    )
    from core.audio import AudioProcessor  # noqa: E402
    from core.interfaces import (  # noqa: E402
        AudioCapture,
        FrameRenderer,
        MagnitudeAnalyzer,
    )
    from visualizers.bars_unified import BarsUnifiedVisualizer  # noqa: E402
finally:
    if _prior_sounddevice is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = _prior_sounddevice
    if _prior_core_audio is None:
        sys.modules.pop("core.audio", None)
    else:
        sys.modules["core.audio"] = _prior_core_audio
    if _prior_core is None:
        sys.modules.pop("core", None)
    else:
        sys.modules["core"] = _prior_core


def _processor(num_bins: int = 64) -> AudioProcessor:
    return AudioProcessor(
        AudioSettings(),
        FrequencySettings(),
        SensitivitySettings(),
        num_bins,
    )


class AudioCaptureBehaviorTests(unittest.TestCase):
    def test_callback_copies_and_replaces_the_latest_array(self) -> None:
        processor = _processor()
        first_input = np.arange(512, dtype=np.float32).reshape(512, 1)

        processor._audio_callback(first_input, 512, None, None)

        first_published = processor.latest_samples
        self.assertIsNotNone(first_published)
        np.testing.assert_array_equal(first_published, first_input[:, 0])
        self.assertEqual(first_published.dtype, np.float32)
        self.assertEqual(first_published.shape, (512,))
        self.assertFalse(np.shares_memory(first_published, first_input))
        self.assertTrue(processor.have_data)

        first_input.fill(-1)
        np.testing.assert_array_equal(
            first_published,
            np.arange(512, dtype=np.float32),
        )

        second_input = np.full((512, 1), 7.5, dtype=np.float32)
        processor._audio_callback(second_input, 512, None, None)

        self.assertIsNot(processor.latest_samples, first_published)
        np.testing.assert_array_equal(processor.latest_samples, second_input[:, 0])
        np.testing.assert_array_equal(
            first_published,
            np.arange(512, dtype=np.float32),
        )


class MagnitudeAnalyzerBehaviorTests(unittest.TestCase):
    def test_fft_matches_the_existing_numpy_projection_exactly(self) -> None:
        processor = _processor()
        self.assertEqual(processor.audio_settings.block_size, 512)
        self.assertEqual(processor.audio_settings.fft_size, 8192)
        self.assertEqual(processor.num_bins, 64)
        processor.setup()
        self.assertEqual(processor.sample_rate, 44_100)

        plan = processor.active_plan
        self.assertIsNotNone(plan)

        positions = np.arange(512, dtype=np.float32)
        samples = (
            0.7 * np.sin(2 * np.pi * positions / 31)
            + 0.2 * np.cos(2 * np.pi * positions / 9)
        ).astype(np.float32)
        processor._audio_callback(samples.reshape(-1, 1), 512, None, None)

        actual = processor.get_fft_magnitudes()

        windowed = samples * plan.window
        magnitudes = np.abs(np.fft.rfft(windowed, n=plan.fft_size))
        expected = np.zeros(processor.num_bins, dtype=np.float32)
        for index, bin_indices in enumerate(plan.bin_indices):
            if len(bin_indices) > 0:
                expected[index] = (
                    np.mean(magnitudes[bin_indices])
                    * plan.bin_weights[index]
                )
        expected = np.maximum(
            0,
            expected - processor.sensitivity_settings.noise_floor,
        )

        self.assertIsNotNone(actual)
        self.assertEqual(actual.shape, (64,))
        self.assertEqual(actual.dtype, np.float32)
        np.testing.assert_array_equal(actual, expected)

    def test_setup_compiles_once_and_frames_keep_the_same_active_plan(self) -> None:
        processor = _processor()
        compile_plan = AudioProcessor.setup.__globals__["compile_standard_analysis_plan"]

        with mock.patch.dict(
            AudioProcessor.setup.__globals__,
            {"compile_standard_analysis_plan": mock.Mock(wraps=compile_plan)},
        ):
            compile_mock = AudioProcessor.setup.__globals__["compile_standard_analysis_plan"]
            processor.setup()
            active_plan = processor.active_plan
            samples = np.ones((512, 1), dtype=np.float32)
            processor._audio_callback(samples, 512, None, None)

            first = processor.get_fft_magnitudes()
            second = processor.get_fft_magnitudes()

        self.assertEqual(compile_mock.call_count, 1)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIs(processor.active_plan, active_plan)

    def test_layered_mode_keeps_its_existing_second_fft(self) -> None:
        processor = _processor()
        processor.setup()
        processor.setup_layers([
            LayerConfig(name="test", freq_range=(100, 6300)),
        ])
        samples = np.ones((512, 1), dtype=np.float32)
        processor._audio_callback(samples, 512, None, None)

        with mock.patch.object(np.fft, "rfft", wraps=np.fft.rfft) as rfft:
            standard = processor.get_fft_magnitudes()
            layered = processor.get_layer_magnitudes()

        self.assertEqual(rfft.call_count, 2)
        self.assertEqual(standard.shape, (64,))
        self.assertEqual(len(layered), 1)
        self.assertEqual(layered[0].shape, (64,))

    def test_failed_recompile_leaves_the_active_plan_unchanged(self) -> None:
        processor = _processor()
        processor.setup()
        active_plan = processor.active_plan
        processor.freq_settings.zoom_presets.append((100, 22_051))
        processor.freq_settings.zoom_preset_index = len(
            processor.freq_settings.zoom_presets
        ) - 1

        with self.assertRaisesRegex(ValueError, "Nyquist"):
            processor.update_frequency_range()

        self.assertIs(processor.active_plan, active_plan)

    def test_candidate_compiles_off_to_the_side_then_activates_once(self) -> None:
        processor = _processor()
        processor.setup()
        original_plan = processor.active_plan
        candidate = replace(
            processor.analysis_candidate,
            low_freq_weight=1.25,
            high_freq_weight=9.5,
        )

        prepared_plan = processor.compile_analysis_candidate(candidate)

        self.assertIs(processor.active_plan, original_plan)
        self.assertIsNot(prepared_plan, original_plan)
        processor.activate_analysis_plan(candidate, prepared_plan)
        self.assertIs(processor.analysis_candidate, candidate)
        self.assertIs(processor.active_plan, prepared_plan)


class PipelineProtocolTests(unittest.TestCase):
    def test_contracts_are_static_protocols_with_narrow_method_sets(self) -> None:
        expected_methods = {
            AudioCapture: {
                "setup": ["self"],
                "start": ["self"],
                "stop": ["self"],
                "__enter__": ["self"],
                "__exit__": ["self", "exc_type", "exc_val", "exc_tb"],
            },
            MagnitudeAnalyzer: {
                "update_frequency_range": ["self"],
                "setup_layers": ["self", "layer_configs"],
                "get_fft_magnitudes": ["self"],
                "get_layer_magnitudes": ["self"],
            },
            FrameRenderer: {
                "draw": [
                    "self",
                    "canvas",
                    "smoothed_bars",
                    "peak_heights",
                    "layer_bars",
                    "layer_peaks",
                ],
            },
        }

        for protocol, methods in expected_methods.items():
            self.assertTrue(protocol._is_protocol)
            self.assertFalse(protocol._is_runtime_protocol)
            for method_name, parameter_names in methods.items():
                signature = inspect.signature(getattr(protocol, method_name))
                self.assertEqual(list(signature.parameters), parameter_names)

    def test_existing_objects_have_compatible_method_order(self) -> None:
        implementations = {
            AudioCapture: (
                AudioProcessor,
                ("setup", "start", "stop", "__enter__", "__exit__"),
            ),
            MagnitudeAnalyzer: (
                AudioProcessor,
                (
                    "update_frequency_range",
                    "setup_layers",
                    "get_fft_magnitudes",
                    "get_layer_magnitudes",
                ),
            ),
            FrameRenderer: (BarsUnifiedVisualizer, ("draw",)),
        }

        for protocol, (implementation, method_names) in implementations.items():
            for method_name in method_names:
                protocol_method = getattr(protocol, method_name)
                protocol_parameters = list(
                    inspect.signature(protocol_method).parameters
                )
                implementation_parameters = list(
                    inspect.signature(getattr(implementation, method_name)).parameters
                )
                self.assertEqual(
                    implementation_parameters,
                    protocol_parameters,
                    f"{implementation.__name__}.{method_name}",
                )


if __name__ == "__main__":
    unittest.main()
