"""Validated standard-analysis configuration and compiled runtime plan.

The candidate is a control-plane value.  Validation and NumPy array construction
happen only when a candidate is compiled; steady-state FFT analysis consumes the
compact plan directly.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np  # type: ignore

from config.weighting import (
    DEFAULT_HIGH_FREQ_WEIGHT,
    DEFAULT_LOW_FREQ_WEIGHT,
    DEFAULT_WEIGHT_POINTS,
    MAX_WEIGHT_POINTS,
    MIN_WEIGHT_POINTS,
    WEIGHT_MAX,
    WEIGHT_MIN,
    WEIGHT_POSITION_MAX,
    WEIGHT_POSITION_MIN,
    WeightPoints,
    WeightPointsError,
    canonical_weight_points,
    legacy_weight_points,
    weight_points_document,
)


STANDARD_ANALYSIS_SCHEMA_VERSION = 1

# How the FFT bins inside one bar combine into its reading (D-012). Each method has
# its own full-scale constant, so a full-scale sine reads 1.0 (D-017, D-037, D-038).
AGGREGATION_MEAN = "mean"
AGGREGATION_BAND_ENERGY = "band_energy"
AGGREGATION_MAX = "max"
AGGREGATIONS = (AGGREGATION_MEAN, AGGREGATION_BAND_ENERGY, AGGREGATION_MAX)
AGGREGATION_DEFAULT = AGGREGATION_MEAN


class AnalysisPlanValidationError(ValueError):
    """Raised before an invalid standard-analysis candidate can be activated."""


def _require_integer(name: str, value: Any, *, minimum: int = 1) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise AnalysisPlanValidationError(
            f"{name} must be an integer greater than or equal to {minimum}"
        )


def _require_finite_number(
    name: str,
    value: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise AnalysisPlanValidationError(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise AnalysisPlanValidationError(
            f"{name} must be greater than or equal to {minimum:g}"
        )
    if maximum is not None and value > maximum:
        raise AnalysisPlanValidationError(
            f"{name} must be less than or equal to {maximum:g}"
        )


@dataclass(frozen=True, slots=True)
class StandardAnalysisCandidate:
    """Versioned inputs that determine the standard one-analyzer projection."""

    schema_version: int
    block_size: int
    fft_size: int
    num_bins: int
    min_freq: float
    max_freq: float
    weight_points: WeightPoints
    aggregation: str = AGGREGATION_DEFAULT

    def __post_init__(self) -> None:
        _require_integer("schema_version", self.schema_version)
        if self.schema_version != STANDARD_ANALYSIS_SCHEMA_VERSION:
            raise AnalysisPlanValidationError(
                f"unsupported standard-analysis schema version: {self.schema_version!r}"
            )
        _require_integer("block_size", self.block_size)
        _require_integer("fft_size", self.fft_size)
        _require_integer("num_bins", self.num_bins)
        if self.fft_size < self.block_size:
            raise AnalysisPlanValidationError(
                "fft_size must be greater than or equal to block_size"
            )
        _require_finite_number("min_freq", self.min_freq, minimum=1.0)
        _require_finite_number("max_freq", self.max_freq, minimum=1.0)
        if self.max_freq <= self.min_freq:
            raise AnalysisPlanValidationError("max_freq must be greater than min_freq")
        try:
            canonical = canonical_weight_points(self.weight_points)
        except WeightPointsError as exc:
            raise AnalysisPlanValidationError(str(exc)) from exc
        if canonical != self.weight_points:
            raise AnalysisPlanValidationError(
                "weight_points must be canonical (position, weight) tuples "
                "sorted by position"
            )
        if not isinstance(self.aggregation, str) or self.aggregation not in AGGREGATIONS:
            raise AnalysisPlanValidationError(
                f"aggregation must be one of {', '.join(AGGREGATIONS)}"
            )

    @classmethod
    def from_settings(
        cls,
        audio_settings: Any,
        frequency_settings: Any,
        sensitivity_settings: Any,
        num_bins: int,
    ) -> "StandardAnalysisCandidate":
        """Capture the current projection inputs without retaining mutable settings."""
        return cls(
            schema_version=STANDARD_ANALYSIS_SCHEMA_VERSION,
            block_size=audio_settings.block_size,
            fft_size=audio_settings.fft_size,
            num_bins=num_bins,
            min_freq=frequency_settings.active_min_freq,
            max_freq=frequency_settings.active_max_freq,
            weight_points=canonical_weight_points(sensitivity_settings.weight_points),
            aggregation=sensitivity_settings.aggregation,
        )


@dataclass(frozen=True, slots=True)
class StandardAnalysisPlan:
    """Array-oriented runtime inputs used by the standard FFT hot path.

    ``bin_weights`` is the weighting curve times ``spectrum_scale``, so one multiply
    both weights and normalizes a bar (D-017, D-031). ``spectrum_scale`` turns the
    ``aggregation`` method's raw reading of a bar into full-scale units, where a
    full-scale sine reads 1.0 (:func:`aggregation_scale`).
    """

    fft_size: int
    window: np.ndarray
    bin_indices: tuple[np.ndarray, ...]
    bin_weights: np.ndarray
    spectrum_scale: float = 1.0
    aggregation: str = AGGREGATION_DEFAULT


# The noise floor's bounds and default, in dBFS (D-017, D-030, D-033). The default
# is provisional until a silence run on the Pi.
NOISE_FLOOR_DBFS_MIN = -120.0
NOISE_FLOOR_DBFS_MAX = -20.0
NOISE_FLOOR_DBFS_DEFAULT = -65.0


def spectrum_scale(window: np.ndarray) -> float:
    """What a raw magnitude is multiplied by so a full-scale sine reads 1.0 (D-031).

    A sine of amplitude A centred on an FFT bin has magnitude ``A × Σwindow / 2``
    there, whatever the zero padding.
    """
    return 2.0 / float(np.sum(window))


def band_energy_scale(window: np.ndarray, fft_size: int) -> float:
    """What a bar's raw ``sqrt(Σ|X|²)`` is multiplied by so a full-scale sine reads 1.0.

    By Parseval, a sine of amplitude A puts ``fft_size × A² × Σwindow² / 4`` of
    energy into the positive-frequency bins, whatever the zero padding, so a bar
    that holds all of it reads 1.0 (D-038). A narrower bar holds a share and reads
    less.
    """
    return 2.0 / math.sqrt(float(fft_size) * float(np.sum(window * window)))


def aggregation_scale(aggregation: str, window: np.ndarray, fft_size: int) -> float:
    """The full-scale constant for one aggregation method (D-017, D-037, D-038).

    Mean and max share :func:`spectrum_scale`: for max, a full-scale sine centred on
    any bin in a bar reads 1.0; for mean, in a bar that bin fills.
    """
    if aggregation == AGGREGATION_BAND_ENERGY:
        return band_energy_scale(window, fft_size)
    return spectrum_scale(window)


def raw_noise_floor(noise_floor_dbfs: float, scale: float) -> float:
    """The dBFS floor in raw magnitude units, subtracted before weighting (D-005)."""
    return 10.0 ** (float(noise_floor_dbfs) / 20.0) / scale


@dataclass(frozen=True, slots=True)
class ProjectionLayout:
    """One layer's bar layout: the single formula the compiler and the page share.

    ``edges`` has ``num_bars + 1`` log-spaced frequencies. Bar ``i`` covers the
    FFT bins with frequency in ``[edges[i], edges[i + 1])``; a bar with no bins is
    empty and always reads 0. ``weights[i]`` is the weighting curve evaluated at
    the bar's center: the arithmetic midpoint of its edges, mapped to log
    position.
    """

    edges: np.ndarray
    bin_indices: tuple[np.ndarray, ...]
    weights: np.ndarray

    @property
    def bin_counts(self) -> tuple[int, ...]:
        return tuple(len(indices) for indices in self.bin_indices)


def weight_curve(
    positions: np.ndarray,
    weight_points: object,
) -> np.ndarray:
    """Evaluate the piecewise-linear weighting curve at log positions (D-020)."""

    points = canonical_weight_points(weight_points)
    point_positions = np.array([position for position, _weight in points])
    point_weights = np.array([weight for _position, weight in points])
    # np.interp is linear between points and holds the end values beyond them.
    return np.interp(positions, point_positions, point_weights)


def projection_layout(
    min_freq: float,
    max_freq: float,
    weight_points: object,
    sample_rate: int,
    fft_size: int,
    num_bars: int,
) -> ProjectionLayout:
    """Compute bar edges, FFT-bin membership, and weights for one projection.

    This is the reference formula for ``structure.bin_layout`` (D-023). Bounds
    are the caller's responsibility; :func:`compile_standard_analysis_plan`
    validates them first.
    """

    frequencies = np.fft.rfftfreq(fft_size, 1 / sample_rate)
    edges = np.logspace(np.log10(min_freq), np.log10(max_freq), num_bars + 1)
    indices: list[np.ndarray] = []
    for index in range(num_bars):
        mask = (frequencies >= edges[index]) & (frequencies < edges[index + 1])
        bin_indices = np.where(mask)[0]
        bin_indices.flags.writeable = False
        indices.append(bin_indices)
    centers = (edges[:-1] + edges[1:]) / 2
    positions = np.log10(centers / min_freq) / np.log10(max_freq / min_freq)
    weights = weight_curve(positions, weight_points)
    edges.flags.writeable = False
    weights.flags.writeable = False
    return ProjectionLayout(
        edges=edges,
        bin_indices=tuple(indices),
        weights=weights,
    )


def compile_standard_analysis_plan(
    candidate: StandardAnalysisCandidate,
    sample_rate: int,
) -> StandardAnalysisPlan:
    """Validate hardware-dependent bounds and compile one complete plan."""
    _require_integer("sample_rate", sample_rate)
    nyquist = sample_rate / 2.0
    if candidate.max_freq > nyquist:
        raise AnalysisPlanValidationError(
            f"max_freq must not exceed the {nyquist:g} Hz Nyquist frequency"
        )

    layout = projection_layout(
        candidate.min_freq,
        candidate.max_freq,
        candidate.weight_points,
        sample_rate,
        candidate.fft_size,
        candidate.num_bins,
    )
    window = np.hanning(candidate.block_size)
    window.flags.writeable = False
    scale = aggregation_scale(candidate.aggregation, window, candidate.fft_size)
    # Normalization folds into the weights: no per-frame cost (D-017). float32
    # matches the transport slots, so the hot-path multiply needs no cast.
    weights = (layout.weights * scale).astype(np.float32)
    weights.flags.writeable = False

    return StandardAnalysisPlan(
        fft_size=candidate.fft_size,
        window=window,
        bin_indices=layout.bin_indices,
        bin_weights=weights,
        spectrum_scale=scale,
        aggregation=candidate.aggregation,
    )


__all__ = [
    # Re-exported from config.weighting so FFT code has one import point.
    "DEFAULT_HIGH_FREQ_WEIGHT",
    "DEFAULT_LOW_FREQ_WEIGHT",
    "DEFAULT_WEIGHT_POINTS",
    "MAX_WEIGHT_POINTS",
    "MIN_WEIGHT_POINTS",
    "WEIGHT_MAX",
    "WEIGHT_MIN",
    "WEIGHT_POSITION_MAX",
    "WEIGHT_POSITION_MIN",
    "WeightPoints",
    "WeightPointsError",
    "canonical_weight_points",
    "legacy_weight_points",
    "weight_points_document",
    # Defined here.
    "AGGREGATIONS",
    "AGGREGATION_BAND_ENERGY",
    "AGGREGATION_DEFAULT",
    "AGGREGATION_MAX",
    "AGGREGATION_MEAN",
    "AnalysisPlanValidationError",
    "NOISE_FLOOR_DBFS_DEFAULT",
    "NOISE_FLOOR_DBFS_MAX",
    "NOISE_FLOOR_DBFS_MIN",
    "ProjectionLayout",
    "STANDARD_ANALYSIS_SCHEMA_VERSION",
    "StandardAnalysisCandidate",
    "StandardAnalysisPlan",
    "aggregation_scale",
    "band_energy_scale",
    "compile_standard_analysis_plan",
    "projection_layout",
    "raw_noise_floor",
    "spectrum_scale",
    "weight_curve",
]
