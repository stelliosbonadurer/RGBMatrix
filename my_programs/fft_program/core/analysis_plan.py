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


STANDARD_ANALYSIS_SCHEMA_VERSION = 1


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
    low_freq_weight: float
    high_freq_weight: float

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
        _require_finite_number(
            "low_freq_weight", self.low_freq_weight, minimum=0.0, maximum=100.0
        )
        _require_finite_number(
            "high_freq_weight", self.high_freq_weight, minimum=0.0, maximum=100.0
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
            low_freq_weight=sensitivity_settings.low_freq_weight,
            high_freq_weight=sensitivity_settings.high_freq_weight,
        )


@dataclass(frozen=True, slots=True)
class StandardAnalysisPlan:
    """Array-oriented runtime inputs used by the standard FFT hot path."""

    fft_size: int
    window: np.ndarray
    bin_indices: tuple[np.ndarray, ...]
    bin_weights: np.ndarray


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

    frequencies = np.fft.rfftfreq(candidate.fft_size, 1 / sample_rate)
    edges = np.logspace(
        np.log10(candidate.min_freq),
        np.log10(candidate.max_freq),
        candidate.num_bins + 1,
    )
    indices: list[np.ndarray] = []
    weights: list[float] = []

    for index in range(candidate.num_bins):
        mask = (frequencies >= edges[index]) & (frequencies < edges[index + 1])
        bin_indices = np.where(mask)[0]
        bin_indices.flags.writeable = False
        indices.append(bin_indices)

        center_freq = (edges[index] + edges[index + 1]) / 2
        normalized_position = np.log10(
            center_freq / candidate.min_freq
        ) / np.log10(candidate.max_freq / candidate.min_freq)
        weight = candidate.low_freq_weight + (
            candidate.high_freq_weight - candidate.low_freq_weight
        ) * (normalized_position ** 1.5)
        weights.append(weight)

    window = np.hanning(candidate.block_size)
    bin_weights = np.array(weights)
    window.flags.writeable = False
    bin_weights.flags.writeable = False

    return StandardAnalysisPlan(
        fft_size=candidate.fft_size,
        window=window,
        bin_indices=tuple(indices),
        bin_weights=bin_weights,
    )
