"""Hardware-free tests for the compiled standard FFT analysis plan."""

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType
import unittest

try:
    import numpy as np
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("FFT analysis-plan tests require NumPy") from exc


FFT_ROOT = Path(__file__).resolve().parent.parent / "my_programs" / "fft_program"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))


# Importing the core package also exposes AudioProcessor. Keep these plan-only tests
# independent of the optional PortAudio binding.
_sounddevice = ModuleType("sounddevice")
_prior_sounddevice = sys.modules.get("sounddevice")
_prior_core_modules = {
    name: module
    for name, module in sys.modules.items()
    if name == "core" or name.startswith("core.")
}
sys.modules["sounddevice"] = _sounddevice
try:
    from core.analysis_plan import (  # noqa: E402
        AnalysisPlanValidationError,
        STANDARD_ANALYSIS_SCHEMA_VERSION,
        StandardAnalysisCandidate,
        compile_standard_analysis_plan,
    )
finally:
    if _prior_sounddevice is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = _prior_sounddevice
    for name in tuple(sys.modules):
        if name == "core" or name.startswith("core."):
            sys.modules.pop(name)
    sys.modules.update(_prior_core_modules)


def _candidate(**overrides) -> StandardAnalysisCandidate:
    values = {
        "schema_version": STANDARD_ANALYSIS_SCHEMA_VERSION,
        "block_size": 512,
        "fft_size": 8192,
        "num_bins": 64,
        "min_freq": 100,
        "max_freq": 6300,
        "low_freq_weight": 0.45,
        "high_freq_weight": 10.0,
    }
    values.update(overrides)
    return StandardAnalysisCandidate(**values)


class StandardAnalysisCandidateTests(unittest.TestCase):
    def test_rejects_unsupported_version_and_invalid_types(self) -> None:
        invalid_cases = (
            {"schema_version": 2},
            {"schema_version": "1"},
            {"schema_version": 1.0},
            {"schema_version": True},
            {"block_size": True},
            {"fft_size": 8192.0},
            {"num_bins": "64"},
            {"min_freq": "100"},
            {"high_freq_weight": np.inf},
        )

        for overrides in invalid_cases:
            with self.subTest(overrides=overrides), self.assertRaises(
                AnalysisPlanValidationError
            ):
                _candidate(**overrides)

    def test_rejects_invalid_ranges(self) -> None:
        invalid_cases = (
            {"block_size": 0},
            {"fft_size": 511},
            {"num_bins": 0},
            {"min_freq": 0},
            {"max_freq": 100},
            {"low_freq_weight": -0.01},
            {"high_freq_weight": 100.01},
        )

        for overrides in invalid_cases:
            with self.subTest(overrides=overrides), self.assertRaises(
                AnalysisPlanValidationError
            ):
                _candidate(**overrides)

    def test_compile_rejects_maximum_frequency_above_nyquist(self) -> None:
        candidate = _candidate(max_freq=22_051)

        with self.assertRaisesRegex(AnalysisPlanValidationError, "Nyquist"):
            compile_standard_analysis_plan(candidate, sample_rate=44_100)


class StandardAnalysisCompilationTests(unittest.TestCase):
    def test_standard_plan_matches_the_prior_numpy_projection_arrays(self) -> None:
        candidate = _candidate()
        plan = compile_standard_analysis_plan(candidate, sample_rate=44_100)

        expected_window = np.hanning(512)
        frequencies = np.fft.rfftfreq(8192, 1 / 44_100)
        edges = np.logspace(np.log10(100), np.log10(6300), 65)
        expected_indices = tuple(
            np.where(
                (frequencies >= edges[index])
                & (frequencies < edges[index + 1])
            )[0]
            for index in range(64)
        )
        expected_weights = []
        for index in range(64):
            center_frequency = (edges[index] + edges[index + 1]) / 2
            normalized_position = np.log10(center_frequency / 100) / np.log10(
                6300 / 100
            )
            expected_weights.append(
                0.45 + (10.0 - 0.45) * (normalized_position ** 1.5)
            )

        self.assertEqual(plan.fft_size, 8192)
        self.assertEqual(len(plan.bin_indices), 64)
        np.testing.assert_array_equal(plan.window, expected_window)
        for actual, expected in zip(plan.bin_indices, expected_indices):
            np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(plan.bin_weights, np.array(expected_weights))

    def test_compiled_arrays_are_read_only(self) -> None:
        plan = compile_standard_analysis_plan(_candidate(), sample_rate=44_100)

        self.assertFalse(plan.window.flags.writeable)
        self.assertFalse(plan.bin_weights.flags.writeable)
        self.assertTrue(
            all(not indices.flags.writeable for indices in plan.bin_indices)
        )
        with self.assertRaises(ValueError):
            plan.window[0] = 1.0
        with self.assertRaises(ValueError):
            plan.bin_weights[0] = 1.0
        populated_indices = next(indices for indices in plan.bin_indices if len(indices))
        with self.assertRaises(ValueError):
            populated_indices[0] = 1


if __name__ == "__main__":
    unittest.main()
