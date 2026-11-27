"""
Per-layer level and motion for the FFT visualizer (fft-level-controls M2).

Auto level is one asymmetric envelope on the frame peak's square: it rises with
``attack_seconds`` and falls with ``release_seconds``, and the scale is
``sqrt(envelope) × headroom`` (D-014). With auto level off the divisor is the
constant ``FIXED_SCALE`` (D-015). Gain multiplies afterwards (D-001).

Every time constant is in seconds. Each call measures the interval since the
previous call (clamped to ``MAX_STEP_SECONDS`` after stalls) and turns each
constant into ``1 − exp(−dt/τ)``, so the response is the same at any call rate
(D-008, D-026). All arrays are preallocated; ``process`` allocates none.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # type: ignore

from config.settings import PeakSettings, ScalingSettings, SmoothingSettings


# Auto level off divides by this constant reference (D-015).
FIXED_SCALE = 0.3
# Auto level never divides by less than this: today's min_scale, a max auto boost
# of 20. It becomes a per-layer setting in M3 with normalized units (D-024).
AUTO_LEVEL_MIN_SCALE = 0.05
# The longest interval one call advances state by; a longer stall counts as this.
MAX_STEP_SECONDS = 0.25
# A fresh processor's first call advances this much: one call at the assumed
# ~86 calls/s, so a new layer's first frame matches the retired per-call constants.
FIRST_STEP_SECONDS = 1.0 / 86.0


def step_fraction(dt: float, seconds: float) -> float:
    """Fraction of the remaining distance covered in ``dt`` with time constant ``seconds``."""
    if seconds <= 0.0:
        return 1.0
    return 1.0 - math.exp(-dt / seconds)


class ScalingProcessor:
    """One layer's auto level, gain, rise/fall smoothing, and peak markers."""

    def __init__(
        self,
        scaling_settings: ScalingSettings,
        smoothing_settings: SmoothingSettings,
        peak_settings: PeakSettings,
        num_bins: int,
    ):
        self.scaling = scaling_settings
        self.smoothing = smoothing_settings
        self.peak = peak_settings
        self.num_bins = num_bins

        # State carried across structure updates (adopt_state, D-007).
        self.envelope = 0.0  # smoothed square of the frame peak
        self.started = False  # the envelope has seen a first frame
        self.last_time: float | None = None
        self.smoothed_bars = np.zeros(num_bins, dtype=np.float32)
        self.peak_heights = np.zeros(num_bins, dtype=np.float32)
        self.peak_hold_remaining = np.zeros(num_bins, dtype=np.float32)

        # Per-call scratch, reused so process() allocates nothing.
        self._normalized = np.zeros(num_bins, dtype=np.float32)
        self._delta = np.zeros(num_bins, dtype=np.float32)
        self._rates = np.zeros(num_bins, dtype=np.float32)
        self._mask = np.zeros(num_bins, dtype=bool)
        self._mask_2 = np.zeros(num_bins, dtype=bool)

    @property
    def scale(self) -> float:
        """The divisor the next frame uses before gain."""
        if not self.scaling.auto_level:
            return FIXED_SCALE
        return max(
            math.sqrt(self.envelope) * self.scaling.headroom,
            AUTO_LEVEL_MIN_SCALE,
        )

    def process(self, bars: np.ndarray, now: float) -> tuple:
        """Advance one call at monotonic time ``now`` (seconds).

        Returns ``(normalized, smoothed_bars, peak_heights)``; all three are this
        processor's own arrays, the same objects on every call.
        """
        if self.last_time is None:
            dt = FIRST_STEP_SECONDS
        else:
            dt = min(max(now - self.last_time, 0.0), MAX_STEP_SECONDS)
        self.last_time = now

        # The envelope always tracks the input, even with auto level off (D-027).
        level = float(np.max(bars)) ** 2
        if not self.started:
            self.envelope = level
            self.started = True
        else:
            seconds = (
                self.scaling.attack_seconds
                if level > self.envelope
                else self.scaling.release_seconds
            )
            self.envelope += (level - self.envelope) * step_fraction(dt, seconds)

        # Normalize, then apply gain: higher is taller (D-001).
        normalized = self._normalized
        np.multiply(bars, self.scaling.gain / self.scale, out=normalized)

        # Asymmetric smoothing toward the new value.
        rise = step_fraction(dt, self.smoothing.rise_seconds)
        fall = step_fraction(dt, self.smoothing.fall_seconds)
        delta = self._delta
        np.subtract(normalized, self.smoothed_bars, out=delta)
        np.greater(delta, 0.0, out=self._mask)
        self._rates.fill(fall)
        np.copyto(self._rates, np.float32(rise), where=self._mask)
        np.multiply(delta, self._rates, out=delta)
        np.add(self.smoothed_bars, delta, out=self.smoothed_bars)

        self._update_peaks(dt)
        return normalized, self.smoothed_bars, self.peak_heights

    def _update_peaks(self, dt: float) -> None:
        """Hold each peak for ``hold_seconds``, then fall at ``fall_rate`` heights/s."""
        heights = self.peak_heights
        hold = self.peak_hold_remaining
        new_peaks = self._mask
        np.greater_equal(self.smoothed_bars, heights, out=new_peaks)

        # Columns with no new peak: spend hold time, then fall by the leftover.
        # The time past the end of a hold falls in the same call, so a column falls
        # the same distance whatever the call rate.
        np.subtract(hold, dt, out=self._delta)  # remaining hold after this call
        falling = self._mask_2
        np.less(self._delta, 0.0, out=falling)
        np.maximum(self._delta, 0.0, out=hold)
        # Fall distance = rate × time past the hold (−remaining where it went below 0).
        np.multiply(self._delta, self.peak.fall_rate, out=self._delta)
        np.add(heights, self._delta, out=self._rates)
        np.maximum(self._rates, 0.0, out=self._rates)
        np.copyto(heights, self._rates, where=falling)

        # New peaks reset their height and hold.
        np.copyto(heights, self.smoothed_bars, where=new_peaks)
        np.copyto(hold, np.float32(self.peak.hold_seconds), where=new_peaks)

    def adopt_state(self, other: "ScalingProcessor") -> None:
        """Take another processor's level, bars, and peaks (D-007, D-028)."""
        if other.num_bins != self.num_bins:
            return
        self.envelope = other.envelope
        self.started = other.started
        self.last_time = other.last_time
        np.copyto(self.smoothed_bars, other.smoothed_bars)
        np.copyto(self.peak_heights, other.peak_heights)
        np.copyto(self.peak_hold_remaining, other.peak_hold_remaining)

    def reset(self) -> None:
        """Forget all state; the next call starts like a fresh launch."""
        self.envelope = 0.0
        self.started = False
        self.last_time = None
        self.smoothed_bars.fill(0)
        self.peak_heights.fill(0)
        self.peak_hold_remaining.fill(0)
