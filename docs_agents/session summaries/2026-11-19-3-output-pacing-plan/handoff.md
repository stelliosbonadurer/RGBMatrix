# Handoff

## State

Planning only. Code and the Pi are unchanged from the step-6 handoff: step 6 is
deployed in direct-default interim state, the qualification patch is uncommitted
locally, and the user-owned files are untouched.

## Proposed increments (ordering pending user decision)

1. **7a — time-based animation.** Convert rise/fall, scale attack/decay and peak
   hold/fall to `dt`-aware updates that look identical at the nominal rate. Pure
   Python; can be proven without hardware.
2. **7b — `nogil` SwapOnVSync binding patch.** Native rebuild with pinned
   identities and a recovery binding. Measure against the current binding.
3. **7c — display-clock pacing.** `limit_refresh_rate_hz` plus swap-paced loop with
   no 5 ms sleep, a miss counter, and an optional adaptive divisor.
4. **8 — clock-policy switch** (display/content) plus a smoothing toggle, with an
   attended visual comparison.
5. **9 — analysis/renderer process split** with shared-memory latest-value slots, for
   1–3 analyzers.
6. **10 — composite path** (vectorized top-down mask), then re-run the output-path
   comparison (direct, `SetImage`, native buffer) with multi-layer workloads (D-008).

## Cheap evidence worth collecting first

- One `--led-show-refresh` run to fix the true refresh (and the value for
  `limit_refresh_rate_hz`).
- A histogram of swap-to-swap intervals from the retained step-6 NPZ files. It should
  show a peak at ≈9.6 ms with occasional ≈19 ms misses.

Both need Pi access approval.

## Remaining risks

- The refresh model and GIL-starvation risk are inferences, not measurements.
- Content-clock mode may show the bursty publication pattern (~60 unique of ~84
  published per second) as judder.
