# Deferred FFT considerations

This is the single parking place for useful FFT ideas that were discussed but not
implemented or qualified. It condenses the former
`2026-11-19-3-output-pacing-plan` and
`2026-11-19-4-renderer-process-pacing-roadmap` folders.

These are **not active requirements or approved implementation steps**. The active
plan is [the modular analyzers and layers roadmap](../2026-11-19-5-modular-analyzers-layers-roadmap/README.md).
Agents should consult this folder only when current evidence makes one of these
topics relevant.

## Evidence boundary

The following observations informed the ideas below:

- Step 6 measured about 104.3 FPS for direct output and 101.9 FPS for SetImage under
  one sparse layer. That hardware result remains in the
  [Step 6 record](../2026-11-19-2-fft-step6-output-path/README.md).
- Local source inspection found that `SwapOnVSync` blocks for a refresh boundary and
  the current Python binding does not release the GIL around that call.
- The current smoothing, attack/decay, and peak behavior uses per-update constants,
  so its apparent speed depends on update cadence.
- A limited Apple M4 microbenchmark found a NumPy top-down occupancy-mask prototype
  faster than a pure-Python per-pixel prototype. The retained
  [artifact](artifacts/bench_topdown-output.txt) is not Raspberry Pi or matrix-driver
  evidence.

Everything else in this file is an unimplemented consideration unless a newer
session record says otherwise.

## Timing and animation

Possible future work:

- Convert temporal presentation effects from per-frame constants to elapsed-time
  updates. One candidate conversion is
  `alpha_dt = 1 - (1 - alpha_ref) ** (dt / dt_ref)`, with explicit long-stall and
  reset behavior.
- If display timing is the clock, derive `dt` from completed swap intervals rather
  than render-start timestamps.
- Compare fixed sleep, VSync-only, and late-acquire/deadline pacing only if freshness
  or render headroom becomes a measured problem. Result age at display is more
  informative than FPS when every candidate is refresh-bound.
- A content-triggered clock could update only when new analysis arrives, but it may
  expose bursty analysis publication as visible judder.
- With the current safe double-buffered API, “as fast as possible” still waits for
  `SwapOnVSync`. A true no-wait mode requires a different native/output design and
  risks tearing or wasted work.

These topics were deliberately removed from the active refactor because current
matrix behavior is acceptable.

## GIL and native binding

The binding could potentially declare `SwapOnVSync` as `nogil` and call it inside a
`with nogil` block. This matters mainly when analysis and rendering remain Python
threads in one process. It is less valuable when the matrix has a dedicated process.

Any future binding experiment must pin source and binary identities, preserve a
recovery copy, rebuild reproducibly, and compare the complete workload on hardware.
The patch has not been built or tested.

## Matrix-process details

The active roadmap contains a bounded process feasibility spike. If that spike is
reached, these details remain worth evaluating:

- Use two hot-path processes, not one per analyzer or layer: analysis owns audio and
  analyzers; the matrix process owns presentation state, selected output backend,
  native matrix objects, canvases, and swaps.
- Start the matrix child with a safe process-start method before audio/native worker
  threads exist. Construct matrix objects only in their owning process.
- Define the privilege model before Pi integration so audio, analysis, and controls
  do not remain root merely because matrix initialization requires privilege.
- Transfer compact fixed-shape latest-value arrays through bounded slots, not queued
  Pillow images, draw commands, or pickled per-frame objects.
- Use an explicit coherence mechanism for publication. Candidate approaches include
  a small lock around slot publication/capture or a correctly specified seqlock.
  Stress tests support the choice but do not replace a correctness argument.
- Carry schema/epoch, analyzer ID, shape, sequence, and publish time. Size the
  protocol so it can evolve beyond one analyzer, while implementing only what the
  current step needs.
- Keep validated configuration, lifecycle, readiness, health, error, and shutdown
  messages on a separate low-rate control channel.

The process split should be retained only if measured isolation or operational value
justifies IPC, lifecycle, privilege, and recovery complexity. Improved FPS is not the
only possible benefit, but presumed GIL or latency improvements are not evidence.

## Output and composition

- Preserve both manual output paths: direct native-canvas drawing and reusable
  Pillow plus `SetImage`. The current sparse-layer result does not establish which
  scales better for dense or overlapping layers.
- Do not add automatic selection until representative multi-layer workloads exist.
- Avoid live-canvas top-down writes: refresh may observe a partially updated frame,
  and Python must still visit every pixel.
- If a real scene needs general composition, a preallocated NumPy occupancy/mask
  approach or a native contiguous-buffer path is more plausible than Python
  per-pixel dispatch. Re-measure on the Pi with the actual driver before adopting it.
- A generic compositor, alpha blending, masks, and equivalent multi-layer benchmark
  scenes remain outside the current refactor.

## Evidence worth collecting only when needed

- Actual refresh rate from the driver under the relevant panel configuration.
- Swap-interval and result-age distributions, not just mean FPS.
- Analysis publication rate versus unique displayed-result rate.
- CPU by process, memory, temperature, throttling, stale results, and missed refreshes.
- Representative live-music visual checks after deterministic tests.

External panel hardware, a nonblocking matrix API, automatic output crossover, and
shared intermediate FFTs remain future topics without an active implementation plan.
