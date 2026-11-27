"""Hardware-free tests for the compiled standard FFT analysis plan."""

from __future__ import annotations

import json
import os
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
        DEFAULT_WEIGHT_POINTS,
        STANDARD_ANALYSIS_SCHEMA_VERSION,
        StandardAnalysisCandidate,
        WeightPointsError,
        canonical_weight_points,
        compile_standard_analysis_plan,
        legacy_weight_points,
        projection_layout,
        weight_points_document,
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
        "weight_points": DEFAULT_WEIGHT_POINTS,
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
            {"weight_points": ((0.0, np.inf),)},
            {"weight_points": ((0.0, True),)},
            {"weight_points": [{"position": 0.0, "weight": 1.0}]},
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
            {"weight_points": ((0.0, -0.01),)},
            {"weight_points": ((0.0, 100.01),)},
            {"weight_points": ((1.01, 1.0),)},
            {"weight_points": ()},
            {"weight_points": ((1.0, 1.0), (0.0, 1.0))},
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
    def test_standard_plan_matches_the_projection_formula(self) -> None:
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
        centers = (edges[:-1] + edges[1:]) / 2
        positions = np.log10(centers / 100) / np.log10(6300 / 100)
        expected_weights = np.interp(
            positions,
            [position for position, _weight in DEFAULT_WEIGHT_POINTS],
            [weight for _position, weight in DEFAULT_WEIGHT_POINTS],
        )

        self.assertEqual(plan.fft_size, 8192)
        self.assertEqual(len(plan.bin_indices), 64)
        np.testing.assert_array_equal(plan.window, expected_window)
        for actual, expected in zip(plan.bin_indices, expected_indices):
            np.testing.assert_array_equal(actual, expected)
        # The weights carry the full-scale normalization (D-031).
        scale = 2.0 / float(np.sum(expected_window))
        self.assertEqual(plan.spectrum_scale, scale)
        np.testing.assert_array_equal(
            plan.bin_weights, (expected_weights * scale).astype(np.float32)
        )

    def test_default_points_stay_close_to_the_retired_weight_curve(self) -> None:
        plan = compile_standard_analysis_plan(_candidate(), sample_rate=44_100)
        edges = np.logspace(np.log10(100), np.log10(6300), 65)
        centers = (edges[:-1] + edges[1:]) / 2
        positions = np.log10(centers / 100) / np.log10(6300 / 100)
        retired = 0.45 + (10.0 - 0.45) * positions ** 1.5

        weights = plan.bin_weights / plan.spectrum_scale
        relative_error = np.abs(weights - retired) / retired
        self.assertLess(float(relative_error.max()), 0.09)

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


GOLDEN_PATH = Path(__file__).resolve().parent / "fixtures" / "projection_layout_vectors.json"
# Inputs for the golden vectors the M5 bin-graph JavaScript must reproduce.
GOLDEN_CASES = (
    {
        "name": "default",
        "min_freq": 100.0,
        "max_freq": 6300.0,
        "weight_points": legacy_weight_points(),
        "sample_rate": 44100,
        "fft_size": 8192,
        "num_bars": 64,
    },
    {
        "name": "narrow-range-with-empty-bars",
        "min_freq": 40.0,
        "max_freq": 200.0,
        "weight_points": [
            {"position": 0.0, "weight": 1.0},
            {"position": 1.0, "weight": 2.0},
        ],
        "sample_rate": 44100,
        "fft_size": 8192,
        "num_bars": 64,
    },
    {
        "name": "unsorted-interior-points-hold-flat-at-the-ends",
        "min_freq": 200.0,
        "max_freq": 8000.0,
        "weight_points": [
            {"position": 0.8, "weight": 3.0},
            {"position": 0.2, "weight": 0.5},
            {"position": 0.5, "weight": 1.5},
        ],
        "sample_rate": 48000,
        "fft_size": 8192,
        "num_bars": 64,
    },
    {
        "name": "single-point-is-flat",
        "min_freq": 100.0,
        "max_freq": 6300.0,
        "weight_points": [{"position": 0.5, "weight": 2.0}],
        "sample_rate": 44100,
        "fft_size": 8192,
        "num_bars": 64,
    },
)


def _golden_vectors() -> dict[str, object]:
    cases = []
    for case in GOLDEN_CASES:
        layout = projection_layout(
            case["min_freq"],
            case["max_freq"],
            case["weight_points"],
            case["sample_rate"],
            case["fft_size"],
            case["num_bars"],
        )
        cases.append({
            "input": dict(case),
            "canonical_weight_points": weight_points_document(case["weight_points"]),
            "edges": [float(edge) for edge in layout.edges],
            "bin_counts": list(layout.bin_counts),
            "weights": [float(weight) for weight in layout.weights],
        })
    return {
        "description": (
            "Golden vectors for core.analysis_plan.projection_layout "
            "(web-control-refactor D-023). Regenerate with "
            "RGB_MATRIX_REGENERATE_GOLDEN=1 python -m unittest "
            "tests.test_fft_analysis_plan."
        ),
        "cases": cases,
    }


class WeightPointTests(unittest.TestCase):
    def test_points_are_sorted_and_returned_in_document_form(self) -> None:
        document = weight_points_document([
            {"position": 1, "weight": 2},
            {"position": 0.25, "weight": 0},
        ])

        self.assertEqual(
            document,
            [
                {"position": 0.25, "weight": 0.0},
                {"position": 1.0, "weight": 2.0},
            ],
        )
        self.assertIs(type(document[0]["position"]), float)

    def test_negative_zero_is_canonicalized(self) -> None:
        document = weight_points_document([{"position": -0.0, "weight": -0.0}])

        self.assertEqual(repr(document), repr([{"position": 0.0, "weight": 0.0}]))

    def test_invalid_points_name_the_offending_field(self) -> None:
        cases = (
            ("not a list", "weight_points"),
            ([], "weight_points"),
            ([{"position": index / 16, "weight": 1} for index in range(17)], "weight_points"),
            ([{"position": 0.0}], "weight_points[0]"),
            ([{"position": 0.0, "weight": 1, "hz": 9}], "weight_points[0]"),
            ([[0.0, 1.0]], "weight_points[0]"),
            ([{"position": 0.0, "weight": 1}, {"position": -0.1, "weight": 1}], "weight_points[1].position"),
            ([{"position": 0.5, "weight": 100.5}], "weight_points[0].weight"),
            ([{"position": 0.5, "weight": False}], "weight_points[0].weight"),
            ([{"position": "0.5", "weight": 1}], "weight_points[0].position"),
            ([{"position": 0.5, "weight": float("nan")}], "weight_points[0].weight"),
            ([{"position": 0.5, "weight": 10 ** 400}], "weight_points[0].weight"),
            (
                [
                    {"position": 0.5, "weight": 1},
                    {"position": 0.1, "weight": 1},
                    {"position": 0.5, "weight": 2},
                ],
                "weight_points[2].position",
            ),
        )
        for value, field in cases:
            with self.subTest(value=value), self.assertRaises(WeightPointsError) as caught:
                canonical_weight_points(value)
            self.assertEqual(caught.exception.field, field)

    def test_sixteen_points_and_the_bounds_are_accepted(self) -> None:
        points = [{"position": index / 15, "weight": 100 * (index % 2)} for index in range(16)]

        self.assertEqual(len(canonical_weight_points(points)), 16)

    def test_legacy_pair_samples_the_retired_curve(self) -> None:
        points = legacy_weight_points(0.45, 10.0)

        self.assertEqual(
            [point["position"] for point in points], [0.0, 0.1, 0.3, 0.6, 1.0]
        )
        self.assertEqual(points[0]["weight"], 0.45)
        self.assertEqual(points[-1]["weight"], 10.0)
        self.assertEqual(points[2]["weight"], round(0.45 + 9.55 * 0.3 ** 1.5, 3))
        self.assertEqual(canonical_weight_points(points), DEFAULT_WEIGHT_POINTS)
        with self.assertRaises(WeightPointsError):
            legacy_weight_points(-1.0, 10.0)


class ProjectionLayoutTests(unittest.TestCase):
    def test_layout_is_linear_between_points_and_flat_beyond_them(self) -> None:
        layout = projection_layout(
            200.0,
            8000.0,
            [
                {"position": 0.75, "weight": 3.0},
                {"position": 0.25, "weight": 1.0},
            ],
            48000,
            8192,
            64,
        )
        centers = (layout.edges[:-1] + layout.edges[1:]) / 2
        positions = np.log10(centers / 200.0) / np.log10(8000.0 / 200.0)

        for position, weight in zip(positions, layout.weights):
            if position <= 0.25:
                self.assertEqual(weight, 1.0)
            elif position >= 0.75:
                self.assertEqual(weight, 3.0)
            else:
                self.assertAlmostEqual(weight, 1.0 + 4.0 * (position - 0.25))
        self.assertEqual(len(layout.edges), 65)
        self.assertFalse(layout.edges.flags.writeable)
        self.assertFalse(layout.weights.flags.writeable)

    def test_empty_bars_have_no_fft_bins(self) -> None:
        layout = projection_layout(
            40.0, 200.0, legacy_weight_points(), 44100, 8192, 64
        )

        self.assertIn(0, layout.bin_counts)
        self.assertEqual(sum(layout.bin_counts), sum(len(i) for i in layout.bin_indices))

    def test_plan_uses_the_layout(self) -> None:
        points = ((0.2, 0.5), (0.9, 4.0))
        plan = compile_standard_analysis_plan(
            _candidate(weight_points=points), sample_rate=44_100
        )
        layout = projection_layout(100, 6300, points, 44_100, 8192, 64)

        np.testing.assert_array_equal(
            plan.bin_weights,
            (layout.weights * plan.spectrum_scale).astype(np.float32),
        )
        for actual, expected in zip(plan.bin_indices, layout.bin_indices):
            np.testing.assert_array_equal(actual, expected)

    def test_golden_vectors_match_the_fixture(self) -> None:
        expected = _golden_vectors()
        if os.environ.get("RGB_MATRIX_REGENERATE_GOLDEN") == "1":
            GOLDEN_PATH.parent.mkdir(exist_ok=True)
            GOLDEN_PATH.write_text(json.dumps(expected, indent=1) + "\n")
        fixture = json.loads(GOLDEN_PATH.read_text())

        self.assertEqual(
            [case["input"]["name"] for case in fixture["cases"]],
            [case["name"] for case in GOLDEN_CASES],
        )
        for actual, stored in zip(expected["cases"], fixture["cases"]):
            with self.subTest(case=stored["input"]["name"]):
                self.assertEqual(actual["input"], stored["input"])
                self.assertEqual(
                    actual["canonical_weight_points"],
                    stored["canonical_weight_points"],
                )
                self.assertEqual(actual["bin_counts"], stored["bin_counts"])
                np.testing.assert_allclose(actual["edges"], stored["edges"], rtol=1e-12)
                np.testing.assert_allclose(actual["weights"], stored["weights"], rtol=1e-12)
        empty_case = fixture["cases"][1]
        self.assertIn(0, empty_case["bin_counts"])


if __name__ == "__main__":
    unittest.main()
