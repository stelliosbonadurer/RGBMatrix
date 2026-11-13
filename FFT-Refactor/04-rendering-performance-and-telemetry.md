# Rendering, Performance, and Telemetry

## Rendering decision

The first compositor supports opaque topmost-wins layers. Alpha, opacity, additive
blend, and generalized blending are explicitly deferred.

The user-visible visual layer list is stored top to bottom. The compositor iterates
in that same order and claims pixels once. This is equivalent to the current
bottom-to-top overwrite result for opaque non-black pixels, while avoiding work on
lower pixels that cannot be seen.

## Coverage-mask contract

Every layer renderer produces or exposes:

- RGB values for candidate pixels.
- A boolean coverage mask saying which pixels the layer owns.

Coverage is separate from RGB. A visible black pixel may intentionally cover a lower
layer, while a transparent/absent pixel must allow the lower layer through.

The preallocated compositor owns:

- `frame_rgb[height, width, 3]`.
- `occupied[height, width]`.

Conceptual algorithm:

```text
clear frame_rgb to black
clear occupied to false

for layer in visible_layers_top_to_bottom:
    if occupied is completely true:
        record remaining layers as fully occluded
        break

    remaining = not occupied
    layer_rgb, layer_coverage = layer.render(remaining)
    claim = layer_coverage and remaining
    frame_rgb[claim] = layer_rgb[claim]
    occupied[claim] = true

bulk publish frame_rgb
```

The real implementation should use vectorized NumPy operations or a small native
loop rather than Python iteration over individual pixels.

## What the optimization does and does not save

If every layer first renders a complete private 64x64 buffer, top-first composition
only saves redundant composition writes. To save meaningful layer-render work, the
layer API accepts the remaining/unclaimed mask and may generate only possible output.

Optimization levels:

1. **Hidden-layer cull**: a user-hidden layer creates no pixels.
2. **Full-frame early exit**: once all pixels are claimed, lower layers create no
   pixels.
3. **Remaining-mask render**: compatible renderers skip claimed pixels.
4. **Geometry fast path**: bar renderers calculate claims by column/span without
   constructing a full intermediate image.
5. **Compiled compositor**: only if vectorized Python still misses the frame budget.

Visible but occluded layers continue their inexpensive temporal analysis state by
default. They skip only pixel generation, so later revealing them does not expose
stale smoothing or peaks.

## FFT-bar fast path

Vertical bars have contiguous or small-span coverage. For each column, the renderer
can derive one or more Y intervals plus colors. The compositor intersects those
intervals with the column's unclaimed mask.

For the common solid-bar case:

- Calculate bar height once per column.
- Calculate theme color once per bar when gradient is off.
- Fill only unoccupied rows in the bar interval.
- Stop visiting a column when its occupancy is complete.

Gradient, overflow, peaks, and shadows can still expose coverage spans. A renderer
may fall back to a full layer buffer when its effect cannot be cheaply expressed as
spans. Correctness takes priority over the optimization.

## Shadows and persistence

Current shadows are stateful. A top-first compositor must define whether a shadow
pixel covers lower layers. For the first release:

- A nonzero shadow pixel is opaque coverage.
- Shadow history remains per visual layer.
- Hidden layers may clear or freeze shadow state according to the same resume policy
  as their temporal spectrum state.
- Fully occluded visible layers update shadow decay but may skip RGB generation.

This preserves existing opaque behavior without adding alpha.

## Bulk frame publication

The native driver already owns continuous panel refresh and supports offscreen
frames. The refactor should:

1. Compose into a reusable RGB `uint8` buffer.
2. Expose it to a reusable Pillow RGB image or a direct binding-supported buffer.
3. Call `FrameCanvas.SetImage()` once.
4. Call `SwapOnVSync()` once.
5. Reuse returned frame canvas and memory on the next frame.

Upstream documents high overhead for repeated Python `SetPixel()` calls and
recommends bulk image transfer. Benchmark the installed binding because it is
unversioned relative to upstream and its `SetImage()` implementation may differ.

## Frame pacing

Content rendering and panel refresh are different rates:

- The native refresh thread continually scans the HUB75 panel.
- The application publishes content frames at a target such as 30 or 60 FPS.
- FFT analyzers run at their configured hop cadence.
- A content frame uses the latest compatible analysis frame and does not trigger a
  transform.

Use monotonic deadlines rather than `sleep_delay` as an assumed frame rate. Drop a
late content frame instead of attempting an unbounded catch-up. Express smoothing,
theme speed, peaks, and shadows in elapsed time or analysis-hop time rather than
assumed frame counts wherever practical.

## Initial performance budgets

These are starting gates to validate on hardware, not unsupported promises:

| Metric | Initial target |
| --- | --- |
| Content publish rate | stable 60 FPS preferred; 30 FPS supported floor |
| 60 FPS render/composite/upload p99 | below 16.7 ms |
| Analyzer p99 | below 80% of its hop interval |
| Audio callback/ring overruns | zero during steady-state test |
| Duplicate analyzer executions | zero |
| Unexplained missed hops | zero |
| Configuration swap | no partial frame/state; bounded interruption |
| Temperature/throttling | no throttling during 30-minute load test |

Native refresh frequency must also be observed under load. It is not the same as
content FPS and is currently unavailable because `led_show_refresh` is disabled.

## Telemetry model

Metrics use fixed-size rolling windows/counters and bounded labels. They must not
allocate or log on every audio callback or pixel.

### Audio-source metrics

- Source generation and health.
- Actual sample rate/channels/format.
- PCM frames received.
- Ring occupancy/high-water mark.
- Discontinuities and dropped samples.
- Callback status overflow/underflow counts.
- Callback interval and callback CPU/load when supported.
- Source-to-ring age.

### Analyzer metrics

Per distinct compiled computation:

- User analyzer cells sharing the worker.
- Runs and published frames.
- Last source/sample sequence.
- Duplicate-suppression violations.
- Missed/skipped hops and reason.
- Window age at analysis start.
- Processing p50/p95/p99/max.
- Deadline utilization: processing time / hop interval.
- Estimated versus measured CPU cost.

### Visual-layer and compositor metrics

- New analysis frames consumed per layer.
- Hidden layers culled.
- Fully occluded layers culled.
- Pixels offered, claimed, and rejected as already occupied.
- Layer projection/DSP time.
- Pixel-generation time.
- Composition time.
- Bulk upload time and VSync wait.
- Content frames rendered/dropped and p50/p95/p99 frame time.

### System and native-output metrics

- Per-thread/process CPU and RSS at a modest sampling interval.
- Temperature and throttle state.
- Native matrix refresh rate/minimum where the driver exposes it.
- Runtime queue depth/timeouts.
- Apply validation, staging, and swap time.

The web UI exposes summaries and a short recent history. Detailed metrics remain
available to logs/diagnostics without streaming raw audio or full spectra by default.

## Benchmark matrix

All comparisons use the same audio fixture or repeatable physical stimulus and the
same panel flags.

1. Current per-pixel path versus bulk `SetImage()`.
2. One analyzer/one layer.
3. One analyzer/eight visual layers.
4. Four distinct analyzers/eight visual layers.
5. Top layer covering 0%, 25%, 50%, 75%, and 100% of lower layers.
6. Solid bars versus gradients, peaks, overflow, and shadows.
7. USB microphone versus Bluetooth input.
8. Web polling/apply activity during rendering.
9. 30-minute sustained worst-case configuration.

Record raw environment and versions with every result. A visible hardware test and
an audio-to-photon measurement remain required; microbenchmarks alone cannot prove
user-perceived latency or flicker.

## Native escalation gate

Move composition/frame packing to C++ or Cython only when:

1. Scheduler and duplicate-work fixes are complete.
2. Bulk frame publication is in use.
3. Hardware profiling attributes a p95/p99 miss to Python layer/compositor work.
4. A bounded native prototype demonstrates a material improvement.
5. The improvement justifies packaging and cross-architecture maintenance.

A full C++ rewrite is not the next escalation step.

