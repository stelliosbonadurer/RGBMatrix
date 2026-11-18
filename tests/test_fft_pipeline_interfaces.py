"""Hardware-free contract tests for the minimal FFT pipeline interfaces."""

from __future__ import annotations

import inspect
from pathlib import Path
import sys
from types import ModuleType
import unittest

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
    "default_samplerate": 48_000,
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
        processor.setup()

        positions = np.arange(512, dtype=np.float32)
        samples = (
            0.7 * np.sin(2 * np.pi * positions / 31)
            + 0.2 * np.cos(2 * np.pi * positions / 9)
        ).astype(np.float32)
        processor._audio_callback(samples.reshape(-1, 1), 512, None, None)

        actual = processor.get_fft_magnitudes()

        windowed = samples * processor.window
        magnitudes = np.abs(
            np.fft.rfft(windowed, n=processor.audio_settings.fft_size)
        )
        expected = np.zeros(processor.num_bins, dtype=np.float32)
        for index, bin_indices in enumerate(processor.bin_indices):
            if len(bin_indices) > 0:
                expected[index] = (
                    np.mean(magnitudes[bin_indices])
                    * processor.bin_weights[index]
                )
        expected = np.maximum(
            0,
            expected - processor.sensitivity_settings.noise_floor,
        )

        self.assertIsNotNone(actual)
        self.assertEqual(actual.shape, (64,))
        self.assertEqual(actual.dtype, np.float32)
        np.testing.assert_array_equal(actual, expected)


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
