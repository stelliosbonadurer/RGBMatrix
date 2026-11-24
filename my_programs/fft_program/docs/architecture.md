# Architecture Overview

This document describes the active standard FFT visualization path. The legacy
layered-mode branch has been removed.

## Module Structure

```text
fft_program/
├── main.py                 # Application lifecycle and render loop
├── runtime_control.py      # Run identity, revision, and runtime snapshot
├── structural_control.py   # structure.set validation, compile, and activation
├── config/
│   ├── settings.py         # Typed application settings
│   └── weighting.py        # Per-layer weight-point helpers
├── core/
│   ├── analysis_plan.py   # Immutable FFT/projection plan compilation
│   ├── audio.py            # Capture and bounded analysis worker
│   ├── interfaces.py       # Narrow pipeline contracts
│   ├── scaling.py          # Normalization, smoothing, peak tracking
│   └── matrix_app.py       # Native LED-matrix boundary
├── themes/                  # Theme implementations and registry
└── visualizers/
    ├── bars_unified.py      # Active direct-canvas renderer
    ├── peaks.py             # Peak overlay
    └── registry.py          # Visualizer lookup
```

## Runtime Data Flow

```text
PortAudio callback
    └── copy newest mono block, advance generation, wake analysis worker

analysis worker
    ├── snapshot latest sample generation and one immutable worker state
    ├── Hann window -> zero-padded rFFT -> weighted 64-bin projection
    └── publish newest complete float32 result into a two-slot transport

main/render loop (continuous; never waits for the worker)
    ├── lease latest complete 64-bin result
    ├── adaptive scaling -> rise/fall smoothing -> peak tracking
    ├── release result slot
    ├── direct native-canvas drawing -> optional peak overlay
    └── SwapOnVSync -> configured post-frame sleep
```

The callback does no FFT work. Its transport is latest-value and lossy by design:
multiple callbacks may be coalesced while analysis is busy, so work cannot accumulate.
The worker similarly publishes only its newest complete 64-bin frame. Rendering keeps
firing at its existing cadence and may reuse a stale analysis result; this is required
for the scaler's attack/decay and peak behavior to continue smoothly.

## Fixed Result Transport

`AudioProcessor` owns two preallocated, contiguous `float32` arrays. The worker claims
an inactive and unleased slot, fills it without holding the metadata lock, then
publishes the slot index, sequence, epoch, and monotonic timestamp in one short locked
operation. The render loop leases the newest published slot through scaling and then
explicitly releases it. It never sees a partially written result, and the worker never
rewrites a leased slot.

There is no result queue. A result may be skipped when the producer or consumer falls
behind. Stable buffer identities avoid per-frame transport allocation.

## Compiled Analysis State

`StandardAnalysisCandidate` is the validated control-plane value.
`StandardAnalysisPlan` owns immutable FFT size, Hann window, bin-index arrays, and bin
weights. Compilation occurs only at startup or after a successful relevant Apply.

The worker reads one versioned state containing the plan and noise floor. Applying a
new plan/noise floor replaces that state atomically and advances its epoch. A result
computed from an obsolete epoch is rejected instead of being published. Invalid Apply
requests leave both the active worker state and the published frame unchanged. A frame
already complete before successful Apply remains usable as the stale display value
until the new epoch publishes, preserving continuous render cadence.

## Scaling and Temporal State

`ScalingProcessor.process()` consumes the latest 64-bin magnitudes and returns
normalized bars, stateful smoothed bars, and falling peaks. The default mode tracks a
rolling RMS of each frame's largest bar and moves `current_scale` toward RMS times
headroom. Fixed, rolling-max, and instant-normalization modes also exist.

Scaling, attack/decay smoothing, and peaks deliberately remain in the continuously
running render thread. They are temporal display behavior, not merely FFT projection.

## Rendering

`BarsUnifiedVisualizer` draws the standard path directly into the native matrix canvas.
Its active toggles are:

- uniform bar color or per-pixel gradient;
- clamped or overflow bars;
- bars, shadows, and peaks;
- full-screen crossfade.

Here, an overflow *layer* means one additional vertical matrix-height band of a single
bar. It is only color terminology inside one standard renderer; it is not the removed
legacy multi-analyzer layered mode.

The peak overlay remains separate and is drawn after the bars. The main loop then calls
`SwapOnVSync()` and retains its configured sleep.

## Controls and Configuration

The only runtime configuration path is the declarative layer structure.
`structural_control.py` validates a complete `structure.set` document (or the launch
structure), compiles the candidate plan and its projections off the frame path, and
activates it at a render-frame boundary, so request threads never mutate the worker
or renderer directly. `runtime_control.py` owns only the run identity, the revision,
and the snapshot, and rejects every other command. There are no keyboard controls,
and no state is saved between runs.

Layer settings are declared in `core/layer_plan.py`
(`current_bars_settings_descriptor()` and `normalize_current_bars_settings()`).
Startup settings use grouped Python dataclasses in `config/settings.py`. When adding
a layer setting:

1. Add it to the descriptor, the normalizer, and `CURRENT_BARS_SETTING_NAMES`.
2. Apply it when the layer's presentation objects are compiled.
3. Update the user documentation.

## Lifecycle and Failure Handling

`AudioProcessor` owns the worker and PortAudio stream. Startup creates the worker before
opening the stream and rolls the worker back if stream creation fails. Shutdown stops
and closes PortAudio first, then signals the worker and joins it with a bounded timeout.
A worker exception is surfaced to the render loop so the existing process supervisor
can restart the application; the worker is not restarted internally.

## Future Multiple Views

Future multi-view rendering should create multiple instances of the standard
analyzer/scaler/drawing path described above. It must not restore the removed
special-case layered pipeline. Add one instance at a time, keep each transport bounded,
and require hardware performance evidence before accepting additional instances.

## Validation Boundary

Most pipeline tests use NumPy arrays and fake canvases without matrix hardware. Native
GPIO timing, PortAudio device behavior, and end-to-end panel output still require an
explicitly approved Raspberry Pi qualification run.
