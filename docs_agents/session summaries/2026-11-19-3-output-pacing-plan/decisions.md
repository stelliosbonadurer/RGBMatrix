# Decisions

## D-001 — keep PWM depth and brightness; target ≈100 Hz

- **Evidence:** in this driver, lowering `pwm_bits` removes only the shortest bit
  planes, so refresh barely changes (estimated 104 → ~108 Hz at 8 bits). The cost
  is dark tones: 8 bits gives 12 distinct levels for inputs 0–63, and inputs 0–8
  turn off. Faster LSB timing or dither raises refresh but dims the display.
- **User decision:** keep `pwm_bits=11`, keep the current LSB timing (no dither),
  no dimming. About 100 Hz is the overall display target; it looks good and is faster
  than the analyzer (~86 Hz).

## D-002 — FFT parameters fixed

- **User decision:** keep 44.1 kHz, 512-sample block, 8192-point FFT, 64 bins.
  The analyzer content ceiling therefore stays ≈86 Hz per analyzer.

## D-003 — replace the fixed 5 ms sleep with display-clock pacing

- **User decision:** adopt fixed-cadence pacing; the user dislikes the fixed sleep.
- **Recommendation:** pace with `limit_refresh_rate_hz` (set at or a little below the
  lowest refresh measured under load) plus `SwapOnVSync` with fraction 1. Make
  animation time-based so a missed refresh costs smoothness, not speed. Add a miss
  counter, and optionally an adaptive divisor with hysteresis. Details are in
  [architecture.md](architecture.md).
- **Dependency:** removing the sleep without D-004 (or the process split) may starve
  the analyzer thread, because the render thread would hold the GIL almost
  continuously.

## D-004 — release the GIL during SwapOnVSync

- **User direction:** do it if it makes sense.
- **Recommendation:** yes. The C++ wait touches no Python objects. The change is two
  lines (`nogil` declaration plus `with nogil:`), but it rebuilds the native
  extension, so it needs pinned source/.so hashes, a recovery copy of the current
  binding, and hardware approval. It is worth less once the renderer is a separate
  single-threaded process; that is an ordering question.

## D-005 — switchable frame-trigger modes for visual comparison

- **User direction:** "swap only on new content" must not be the only mode, because
  attack/decay animation must keep its speed. The user wants a visual comparison of:
  - display-clock mode (fixed cadence, animation every refresh);
  - content-clock mode (frame only on a new analysis sequence);
  - attack/decay on/off.
  Keep both modes only if they look different and neither is strictly better.
- **Recommendation:** one frame pipeline with a small clock policy selected at
  startup/Apply that decides when a frame runs and supplies `dt`. This requires
  time-based animation (the D-003 prerequisite). With several analyzers, content
  mode needs a primary analyzer.
- **Status:** direction accepted; design not yet approved.

## D-006 — process/shared-memory boundary is in scope

- **User decision:** include the analysis/renderer process split (the two-process
  topology in `2026-11-18-1-performance-comparison/pipeline-comparison-and-next-architecture.md`)
  in this development process. It is motivated by 1–3 analyzers, not by step-5
  failure.
- **Open point:** that design runs smoothing on the analyzer cadence. D-005 needs
  display-rate animation in the renderer. Split them: per-analysis normalization in
  the analyzer, rise/fall and peaks in the renderer.

## D-007 — external hardware deferred

- **User decision:** do not involve other hardware now (Pi 5 Piomatter, Pico/ESP32
  PIO/DMA drivers, receiver cards). Revisit after this software path is exhausted.

## D-008 — step-6 caveat: single sparse layer only

- **User decision:** the step-6 rejection carries a big asterisk. It measured one
  sparse bar layer (~400–450 lit pixels). Direct drawing cost grows with lit pixels
  across all layers, including overlap; a full-frame upload costs a fixed 4,096
  pixels. `SetImage` or a native buffer upload may win with multiple layers.
  Re-run the output-path comparison when the composite path exists.

## D-009 — draw top-down per pixel directly on the live canvas (evaluated)

- **User proposal:** skip double buffering; for each pixel, test layers from the top
  and write the first one that draws.
- **Recommendation:** keep the top-down ordering but not the live-canvas or pure-Python
  per-pixel form.
  - Writing the live canvas shows half-updated frames, because the refresh pass
    crosses the update. Each `SetPixel` also updates 11 bit planes non-atomically.
  - With no `Clear`, every one of the 4,096 pixels must be written each frame. Python
    calls at ~1–3 µs each on the Pi make that ~4–12 ms (estimate), against a ~10 ms
    budget.
  - The same top-down rule as a NumPy occupancy mask cost 0.11 ms versus 0.55 ms for
    the Python loop on an M4 laptop (5 layers). It then feeds one double-buffered
    upload.
  - Its one real advantage is no VSync wait and no snapping to refresh periods. A
    released-GIL swap and a pipelined output thread keep most of that.
- **User answer:** pending.
