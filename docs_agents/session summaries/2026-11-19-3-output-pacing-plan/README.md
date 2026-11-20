# Output pacing and next-architecture planning — 2026-11-19

## Objective

Explain why the step-6 Pillow candidate could not win, and plan the next increments
for display pacing, binding behavior, frame-trigger modes, the process boundary,
and multi-layer output. Target: 1–3 analyzers and 1–5 layers.

## Scope

Planning and read-only investigation only. No source code, Pi state, binding, or
deployment changed. Hardware offload (Pi 5 PIO, microcontroller panel drivers) is out
of scope until this software path is exhausted (D-007).

## Baseline

- HEAD `6d0b6b8de0484ac18b4dad9ef49f8ffa73671154` (step-6 qualification records).
- Accepted output path: direct native-canvas drawing (step-6 D-013).
- Working tree has pre-existing user/qualification files (`audio.py`, `main.py`,
  `qualification_metrics.py`, `AGENTS.md`, `LED_Matrix_Rendering_Design.md`, test
  file). They were not touched.
- Pi: step 6 deployed in direct-default interim state (step-6 D-012).

## Key findings

1. The step-6 loop is limited by the panel refresh. `SwapOnVSync` blocks until the
   refresh thread finishes a full `DumpToMatrix` pass. With 11 PWM bits and 130 ns
   LSB on a 1:32 scan, one pass is ≈9.6 ms (≈104 Hz). Measured swap FPS was
   104.3/104.4. Render time saved shows up as VSync wait, so no output-path change
   could raise swap FPS. See [architecture.md](architecture.md).
2. `SwapOnVSync` holds the GIL while it waits (no `nogil` in `cppinc.pxd`/`core.pyx`).
3. Bar rise/fall smoothing, scale attack/decay and peak hold/fall are per-frame
   constants. Their speed is therefore tied to the display frame rate.
4. The step-6 result applies only to one sparse layer (see D-008).

## Status

Direction recorded from the user. The next increment's ordering awaits a user
decision. Records: [decisions.md](decisions.md), [changes.md](changes.md),
[tests.md](tests.md), [architecture.md](architecture.md), [handoff.md](handoff.md).
