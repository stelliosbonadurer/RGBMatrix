"""fft-level-controls M2: per-layer auto level and seconds-based timing.

Covers the frame-rate independence the design depends on (D-008, D-026), the
defaults' fit to the retired scaler (D-014, D-025), auto level off (D-015), and the
allocation-free hot path.
"""
from __future__ import annotations

import math
from pathlib import Path
import sys
from types import ModuleType
import unittest

try:
    import numpy as np
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("FFT scaling tests require NumPy") from exc

FFT_ROOT = Path(__file__).resolve().parent.parent / "my_programs" / "fft_program"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))

# Importing core pulls in the audio module; the scaler needs no audio device.
_sounddevice = ModuleType("sounddevice")
_prior_sounddevice = sys.modules.get("sounddevice")
sys.modules["sounddevice"] = _sounddevice
try:
    from config.settings import PeakSettings, ScalingSettings, SmoothingSettings  # noqa: E402
    from core.scaling import (  # noqa: E402
        AUTO_LEVEL_MIN_SCALE,
        FIRST_STEP_SECONDS,
        FIXED_SCALE,
        MAX_STEP_SECONDS,
        ScalingProcessor,
    )
finally:
    if _prior_sounddevice is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = _prior_sounddevice

WIDTH = 16
SHAPE = np.linspace(0.2, 1.0, WIDTH, dtype=np.float32)


def _scaler(**scaling) -> ScalingProcessor:
    return ScalingProcessor(
        ScalingSettings(**scaling), SmoothingSettings(), PeakSettings(), WIDTH
    )


def _amplitude(t: float) -> float:
    """A level that changes once a second: quiet, loud, very quiet, medium."""
    return (0.2, 0.2, 0.9, 0.9, 0.9, 0.05, 0.05, 0.05, 0.05, 0.4)[int(t) % 10]


def _run(rate: float, seconds: float, scaler: ScalingProcessor, amplitude_of=None):
    """Drive at ``rate`` calls/s; return {step index on a 10/s grid: state}."""
    amplitude_of = amplitude_of or _amplitude
    samples = {}
    steps = int(round(seconds * rate))
    stride = int(round(rate / 10))
    for index in range(1, steps + 1):
        t = index / rate
        # Input is constant over each 10/s interval, sampled at its start, so
        # every rate sees the same signal.
        amplitude = amplitude_of(math.floor((t - 1e-9) * 10) / 10)
        scaler.process(SHAPE * amplitude, t)
        if index % stride == 0:
            samples[index // stride] = (
                scaler.scale,
                scaler.smoothed_bars.copy(),
                scaler.peak_heights.copy(),
            )
    return samples


class TwoRateTests(unittest.TestCase):
    """D-008: the same input at two call rates gives the same response."""

    def test_scale_and_bars_match_at_40_and_160_calls_per_second(self) -> None:
        slow = _run(40.0, 20.0, _scaler())
        fast = _run(160.0, 20.0, _scaler())
        self.assertEqual(set(slow), set(fast))
        # The first call advances one nominal step at either rate; skip its
        # first second, then compare on the shared 10/s grid.
        worst_scale = worst_bars = 0.0
        for key in sorted(slow)[10:]:
            slow_scale, slow_bars, _slow_peaks = slow[key]
            fast_scale, fast_bars, _fast_peaks = fast[key]
            worst_scale = max(worst_scale, abs(math.log(slow_scale / fast_scale)))
            worst_bars = max(worst_bars, float(np.max(np.abs(slow_bars - fast_bars))))
        # The envelope sees piecewise-constant input, so it matches to rounding.
        self.assertLess(worst_scale, 1e-4)
        # Bars also follow the scale, which moves within each interval; a few
        # percent of the panel height is the discretization difference.
        self.assertLess(worst_bars, 0.05)

    def test_peak_hold_and_fall_match_at_40_and_160_calls_per_second(self) -> None:
        # Bursts and silences: each peak holds, then falls. (While a bar glides
        # down slowly, a marker re-catches it only on a call, so its hold-and-fall
        # cycle has a per-call phase; that is not compared here.)
        def bursts(t: float) -> float:
            return 0.9 if int(t * 2) % 2 == 0 else 0.0

        def run(rate):
            scaler = _scaler(auto_level=False)
            return _run(rate, 6.0, scaler, bursts)

        slow, fast = run(40.0), run(160.0)
        keys = sorted(slow)[5:]
        # The tallest column's marker stays above its bar all through each
        # silence, so it is pure hold-then-fall: equal to rounding.
        tallest = max(abs(float(slow[key][2][-1] - fast[key][2][-1])) for key in keys)
        self.assertLess(tallest, 1e-3)
        # Shorter columns' markers meet their falling bar; the meeting is seen
        # on the next call, so they differ by at most fall rate × call interval.
        bound = PeakSettings().fall_rate / 40.0
        worst = max(float(np.max(np.abs(slow[key][2] - fast[key][2]))) for key in keys)
        self.assertLess(worst, bound)

    def test_per_call_constants_would_fail_the_same_comparison(self) -> None:
        # Sensitivity check: the retired per-call rise of 0.4 at 40 vs 160 calls/s
        # differs far beyond the tolerance above.
        def per_call(rate: float) -> float:
            value = 0.0
            for _ in range(int(rate * 0.05)):  # 50 ms toward a target of 1
                value += (1.0 - value) * 0.4
            return value

        self.assertGreater(abs(per_call(160.0) - per_call(40.0)), 0.05)


def _retired_scale(peaks: np.ndarray) -> np.ndarray:
    """The pre-M2 rolling-RMS scaler at its defaults, called once per peak.

    Kept here as the reference the new defaults approximate (D-014, D-025):
    a 600-sample RMS window (3 s × the assumed 200 calls/s), headroom 2.5,
    then a per-call follower (attack 0.1, decay 0.06), floor 0.05, start 0.25.
    """
    buffer = np.zeros(600)
    index = count = 0
    total = 0.0
    current = 0.25
    out = np.empty(len(peaks))
    for i, peak in enumerate(peaks):
        if count >= len(buffer):
            total -= buffer[index]
        total += peak * peak
        buffer[index] = peak * peak
        index = (index + 1) % len(buffer)
        count = min(count + 1, len(buffer))
        target = max(math.sqrt(max(total, 0.0) / count) * 2.5, 0.05)
        current += (target - current) * (0.1 if target > current else 0.06)
        out[i] = current
    return out


class DefaultsTests(unittest.TestCase):
    def test_default_auto_level_tracks_the_retired_scaler_within_half(self) -> None:
        rate = 86.0
        rng = np.random.default_rng(0)
        levels = ((0.2, 20), (0.8, 15), (0.2, 15), (0.05, 10), (0.4, 10))
        peaks = np.concatenate([
            level * (0.6 + 0.4 * rng.random(int(seconds * rate)))
            for level, seconds in levels
        ])
        reference = _retired_scale(peaks)
        scaler = _scaler()
        actual = np.empty(len(peaks))
        frame = np.zeros(WIDTH, dtype=np.float32)
        for i, peak in enumerate(peaks):
            frame[0] = peak
            scaler.process(frame, (i + 1) / rate)
            actual[i] = scaler.scale
        # After a 10 s warm-up (launch behavior differs by design, D-015).
        warm = int(10 * rate)
        worst = float(np.max(np.abs(np.log(actual[warm:] / reference[warm:]))))
        self.assertLess(worst, math.log(1.5))

    def test_envelope_starts_at_the_first_frames_level(self) -> None:
        scaler = _scaler()
        scaler.process(SHAPE * 0.5, 100.0)
        self.assertAlmostEqual(scaler.envelope, 0.25, places=6)
        self.assertAlmostEqual(scaler.scale, 0.5 * 2.5, places=6)

    def test_first_call_is_one_nominal_step(self) -> None:
        scaler = _scaler(auto_level=False, gain=1.0)
        scaler.process(SHAPE, 100.0)
        rise = 1.0 - math.exp(-FIRST_STEP_SECONDS / SmoothingSettings().rise_seconds)
        np.testing.assert_allclose(
            scaler.smoothed_bars, SHAPE / FIXED_SCALE * rise, rtol=1e-5
        )
        # The retired per-call rise was 0.4; the default converts it (D-025).
        self.assertAlmostEqual(rise, 0.4, delta=0.01)


class AutoLevelTests(unittest.TestCase):
    def test_auto_level_off_divides_by_the_fixed_reference(self) -> None:
        scaler = _scaler(auto_level=False, gain=2.0)
        for step in range(5):
            normalized, _smoothed, _peaks = scaler.process(SHAPE * (step + 1), step * 0.01)
            np.testing.assert_array_equal(
                normalized, (SHAPE * (step + 1)) * np.float32(2.0 / FIXED_SCALE)
            )

    def test_envelope_tracks_while_auto_level_is_off(self) -> None:
        scaler = _scaler(auto_level=False)
        scaler.process(SHAPE * 0.1, 0.0)
        for step in range(1, 200):
            scaler.process(SHAPE * 0.8, step * 0.1)
        self.assertGreater(scaler.envelope, 0.5)  # rose toward 0.64
        self.assertEqual(scaler.scale, FIXED_SCALE)
        scaler.scaling.auto_level = True
        self.assertAlmostEqual(scaler.scale, math.sqrt(scaler.envelope) * 2.5)

    def test_scale_never_drops_below_the_floor(self) -> None:
        scaler = _scaler()
        scaler.process(np.zeros(WIDTH, dtype=np.float32), 0.0)
        self.assertEqual(scaler.scale, AUTO_LEVEL_MIN_SCALE)

    def test_attack_and_release_are_the_envelopes_time_constants(self) -> None:
        scaler = _scaler(attack_seconds=2.0, release_seconds=0.5)
        scaler.process(SHAPE * 0.0, 0.0)
        # A step up to level 1: after one attack time constant, 1 − 1/e of the way.
        t = 0.0
        while t < 2.0 - 1e-9:
            t += 0.01
            scaler.process(SHAPE, t)
        self.assertAlmostEqual(scaler.envelope, 1 - math.exp(-1), places=3)
        start = scaler.envelope
        while t < 2.5 - 1e-9:
            t += 0.01
            scaler.process(SHAPE * 0.0, t)
        self.assertAlmostEqual(scaler.envelope, start * math.exp(-1), places=3)


class TimingTests(unittest.TestCase):
    def test_a_stall_advances_state_by_one_clamped_step(self) -> None:
        stalled = _scaler(auto_level=False)
        clamped = _scaler(auto_level=False)
        for scaler in (stalled, clamped):
            scaler.process(SHAPE, 0.0)
        stalled.process(SHAPE * 0.0, 5.0)
        clamped.process(SHAPE * 0.0, MAX_STEP_SECONDS)
        np.testing.assert_array_equal(stalled.smoothed_bars, clamped.smoothed_bars)
        self.assertEqual(stalled.envelope, clamped.envelope)

    def test_a_clock_going_backwards_moves_nothing(self) -> None:
        scaler = _scaler(auto_level=False)
        scaler.process(SHAPE, 10.0)
        before = scaler.smoothed_bars.copy()
        scaler.process(SHAPE * 3, 9.0)
        np.testing.assert_array_equal(scaler.smoothed_bars, before)

    def test_peaks_hold_then_fall_at_their_rate(self) -> None:
        scaler = ScalingProcessor(
            ScalingSettings(auto_level=False, gain=FIXED_SCALE),
            SmoothingSettings(rise_seconds=0.0, fall_seconds=0.0),
            PeakSettings(hold_seconds=0.2, fall_rate=2.0),
            WIDTH,
        )
        full = np.ones(WIDTH, dtype=np.float32)
        scaler.process(full, 0.0)  # instant rise: bars and peaks at 1
        np.testing.assert_allclose(scaler.peak_heights, 1.0)
        zero = np.zeros(WIDTH, dtype=np.float32)
        scaler.process(zero, 0.1)
        np.testing.assert_allclose(scaler.peak_heights, 1.0)  # holding
        # 0.2 s later: 0.1 s of hold left, then 0.1 s falling at 2 heights/s.
        scaler.process(zero, 0.3)
        np.testing.assert_allclose(scaler.peak_heights, 0.8, rtol=1e-5)
        scaler.process(zero, 0.5)
        np.testing.assert_allclose(scaler.peak_heights, 0.4, rtol=1e-4)
        scaler.process(zero, 0.7)
        np.testing.assert_array_equal(scaler.peak_heights, 0.0)  # floored


class HotPathTests(unittest.TestCase):
    def test_process_returns_the_same_arrays_every_call(self) -> None:
        scaler = _scaler()
        identities = None
        for step in range(5):
            result = scaler.process(SHAPE * (step + 1), step / 86)
            ids = tuple(id(array) for array in result)
            identities = identities or ids
            self.assertEqual(ids, identities)
        self.assertIs(result[1], scaler.smoothed_bars)
        self.assertIs(result[2], scaler.peak_heights)

    def test_adopt_state_copies_into_the_existing_arrays(self) -> None:
        old = _scaler()
        for step in range(10):
            old.process(SHAPE * (step + 1) * 0.1, step / 86)
        new = _scaler(headroom=4.0)
        arrays = (new.smoothed_bars, new.peak_heights, new.peak_hold_remaining)
        new.adopt_state(old)
        self.assertEqual(
            (new.envelope, new.started, new.last_time),
            (old.envelope, old.started, old.last_time),
        )
        np.testing.assert_array_equal(new.smoothed_bars, old.smoothed_bars)
        np.testing.assert_array_equal(new.peak_heights, old.peak_heights)
        np.testing.assert_array_equal(new.peak_hold_remaining, old.peak_hold_remaining)
        self.assertEqual(
            tuple(map(id, arrays)),
            tuple(map(id, (new.smoothed_bars, new.peak_heights, new.peak_hold_remaining))),
        )
        # The new layer's own settings apply to the carried level.
        self.assertAlmostEqual(new.scale, math.sqrt(old.envelope) * 4.0)


if __name__ == "__main__":
    unittest.main()
