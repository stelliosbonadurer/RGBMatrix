# Architecture notes

No architecture change was made in this session. These are findings and the proposed
direction.

## Why the step-6 loop is refresh-bound

The driver refresh thread (`lib/led-matrix.cc` `Run`) draws a full frame with
`DumpToMatrix`, then honours a pending swap and signals. `SwapOnVSync` blocks on that
signal. In each row pair, per-bit-plane flash time is `lsb × 2^b`. The data clock-in
overlaps the previous flash, so short planes cost about the clock-in time (~3 µs,
inferred).

```text
11 bits, 130 ns: 266 µs on-time + ~34 µs overhead per row pair × 32 ≈ 9.6 ms ≈ 104 Hz
step-6 loop:     5 ms sleep + ~0.5 ms scaler + draw (1.5–2.0) + swap wait (2.5–2.0) ≈ 9.6 ms
```

Draw plus swap stayed ≈4.0 ms for both paths. Work moved between the renderer and the
VSync wait without changing the total. The interval model for other PWM settings is a
rough calibrated estimate; `--led-show-refresh` is the authority.

## Proposed display-clock pacing (D-003)

```text
loop:
  latest = acquire newest analysis (non-blocking, may repeat)
  animate(dt)        # time-based rise/fall, scale attack/decay, peak hold/fall
  compose/draw       # one-layer fast path, later composite path
  SwapOnVSync        # GIL released (D-004); paces at the refresh grid
```

- Budget per frame becomes the whole refresh period (≈10 ms), not ≈4 ms after the
  5 ms sleep.
- **Miss:** that frame is shown for two refreshes. The next frame aligns again on its
  own. Latest-value transport means no backlog builds up.
- **Time-based animation:** `α_dt = 1 − (1 − α)^(dt / dt_ref)` with
  `dt_ref ≈ 1/104 s`. This keeps today's look exactly at the nominal rate. Peak
  `hold_frames` becomes hold seconds; `fall_speed` becomes per second.
- **Sustained overload:** a fixed rate of 50 Hz looks better than alternating 100/50
  judder. Optionally switch the swap divisor from 1 to 2 when the miss rate passes a
  threshold, and return with hysteresis. Count misses as bounded telemetry.
- **Later option:** pipeline composition with an output thread (adds one frame of
  latency). It only helps once the GIL is released during the swap.

## Proposed clock policy (D-005)

One pipeline. A clock policy chosen at startup or Apply selects one of:

- display clock: run every refresh, `dt` = elapsed refresh time;
- content clock: run when the primary analyzer sequence changes, then swap at the
  next refresh; `dt` = time since the last frame.

Turning attack/decay off is an existing parameter case (rise = fall = 1 passes
normalized bars through). Expected visual difference: content mode updates
unevenly (step 6 showed ~60 unique results per second against ~84 published).

## Process split (D-006)

Follow the 2026-11-18 two-process topology. Move only display-rate temporal effects
into the renderer.

## Rejected or deferred

- Lowering `pwm_bits` or dimming (D-001).
- Live-canvas per-pixel top-down drawing (D-009, pending confirmation).
- External hardware (D-007).
