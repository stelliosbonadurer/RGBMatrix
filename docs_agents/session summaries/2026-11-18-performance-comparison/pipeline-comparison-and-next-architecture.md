# FFT pipeline comparison and recommended next architecture — 2026-11-18

> **Status: authoritative legacy analysis of the rejected refactor.** The refactor
> was rolled back after this analysis. Use this document to preserve the useful
> contracts, understand the failed hot-path choices, and evaluate a new design—not as
> instructions to restore the removed implementation.

## Decision summary

The refactor achieved useful product and software goals—versioned configurations,
atomic live Apply, analyzer sharing, independent analyzer/render clocks, telemetry,
and an explicit legacy fallback—but the tested implementation is not a viable
performance base for the Pi.

The recommended direction is to return to the pre-refactor runtime as the known-fast
baseline, preserve the configuration/control work that is not on the frame hot path,
and rebuild the data plane incrementally. Modularity should come from narrow typed
interfaces and replaceable stages, not from turning every stage boundary into Python
objects or requiring every feature to use a generic compositor.

For the standard one-analyzer/one-bar-layer mode, the target should be a deliberately
short fast path:

```text
audio block -> vectorized FFT/projection -> latest 64-bin frame
            -> direct bar render -> matrix canvas -> VSync
```

If analyzer/render failure isolation or Bluetooth ownership requires a true process
boundary, use two OS processes with fixed-size shared memory and a latest-value
mailbox. The present implementation does **not** provide CPU isolation: analyzer,
renderer, and audio callback are separate threads inside one Python process and
therefore still contend for the GIL during Python work.

No additional hardware test or benchmark was run while preparing this document. The
analysis below is based on today's stored measurements and code inspection.

## Measured result

All four runs used the same Pi, iMM-6C source, 64×64 panel, matrix flags, 512-sample
audio geometry, and 8192-point FFT. The most useful comparison is legacy versus the
refactor with all application pacing disabled, because it removes the refactor's
deadline wait as an explanation.

| Metric | Legacy | Refactor, no application wait | Difference |
|---|---:|---:|---:|
| Achieved content FPS | 94.04 | 70.22 | Refactor is 25.3% lower; legacy is 1.34× faster |
| Process CPU | 85.82% | 110.36% | Refactor uses about 28.6% more CPU |
| RSS | 56.4–56.6 MiB | 66.0–67.0 MiB | Refactor uses about 10 MiB more |
| Render-to-swap/frame p50 | 4.78 ms | 15.64 ms | Refactor is about 3.27× longer |
| Render-to-swap/frame p95 | 13.99 ms | 23.79 ms | Refactor is about 1.70× longer |
| Analyzer rate | Coupled to loop | 21.59 Hz | Refactor analyzer was starved by rendering |
| Missed analyzer hops | Not applicable | 1,714 | Severe scheduling loss |
| Audio overruns in window | Counter unavailable | 61 | Capture could not remain healthy |
| Thermal throttling | No | No | Not a thermal explanation |

The timing labels are not perfectly symmetrical: legacy measured the entire loop
from its frame start through swap, while the refactor measured its frame work through
publish/swap. They are close enough to locate a large regression, but should not be
treated as a component-level profile.

The pacing experiments establish a second result:

| Refactor mode | Content FPS | Analyzer rate | Audio overruns |
|---|---:|---:|---:|
| 60 FPS target | 36.16 | 46.38 Hz | 0 |
| 240 FPS target | 46.43 | 34.50 Hz | 1 |
| No application wait | 70.22 | 21.59 Hz | 61 |

Removing pacing recovered substantial render throughput, so pacing was a real
problem. It did not close the legacy gap, and increased rendering directly starved
analysis and capture. The bulk `SetImage` path was active in every refactor run, so
failure to select that path is ruled out.

## Pipeline comparison

### Legacy pipeline

```text
PortAudio callback
  -> NumPy channel copy into latest_samples

single main loop
  -> FFT from latest 512-sample block
  -> NumPy magnitude/bin/scaling operations
  -> direct bar drawing into native frame canvas
  -> SwapOnVSync
  -> fixed 5 ms sleep
```

Important characteristics:

- The newest audio block replaces the previous block; there is no large PCM history.
- FFT, projection, temporal DSP, drawing, and publication are serialized.
- Most numerical state stays in NumPy arrays.
- The renderer draws directly to the native canvas and does not first build a
  general-purpose layer result, coverage mask, composed RGB frame, second output
  buffer, and Pillow image.
- The design can recompute an FFT for the same source block and couples analysis
  cadence to rendering, but it has very little coordination or marshalling overhead.

### Refactored pipeline as implemented

```text
PortAudio callback thread
  -> lock and health counters
  -> Python loop over each input sample
  -> write sample + timestamp + generation + discontinuity into PCM ring
  -> wake analyzer scheduler

analyzer scheduler thread (same Python process)
  -> lock and inspect exact sample positions
  -> Python loop copies an exact window into a tuple
  -> NumPy DC removal/window/rFFT/magnitude
  -> convert the full 4,097-coefficient spectrum to Python float tuple
  -> lock and publish immutable SpectrumFrame to latest-frame cache

render thread
  -> lock and fetch newest analysis frame
  -> validate/convert all 4,097 coefficients again
  -> Python bin aggregation, response gain, normalization, smoothing, and peaks
  -> copy 64 bars/peaks into renderer tuples
  -> create PixelSpan objects and scan coverage per pixel
  -> compose spans into RGB + coverage + remaining byte buffers
  -> copy RGB into a second staging buffer
  -> update a Pillow image with frombytes
  -> canvas.SetImage
  -> SwapOnVSync
  -> deadline pacing
```

This architecture decouples logical clocks, but it adds expensive representation
changes at nearly every boundary. It also leaves all Python-heavy stages in one
interpreter, so the renderer can prevent the analyzer and audio callback from
running even though they are nominally independent.

## Why performance was lost

These are ordered by confidence and likely impact. They are evidence-backed theories,
not a substitute for a future component profile.

### 1. Python/GIL contention defeated the intended decoupling — high confidence

The strongest evidence is the no-wait run. Raising render throughput reduced analyzer
rate from 46.38 Hz to 21.59 Hz and caused 61 audio overruns. If analyzer and renderer
had effective execution isolation, an uncapped renderer would not cut analyzer
service by more than half.

NumPy FFT work can release the GIL, but much of this pipeline cannot: ring copying,
tuple conversion, metadata construction, projection, smoothing loops, span creation,
composition, metrics, and buffer packing are Python work. The analyzer thread and
render thread therefore time-slice on one interpreter rather than behaving like
independent workers on separate cores.

### 2. The audio callback became a per-sample Python data-structure writer — high confidence

Legacy capture performs a NumPy channel slice and `.copy()` for each 512-frame audio
block. The refactor loops over every input frame while holding the ring lock and, for
each sample, performs indexing, float conversion, modulo, timestamp arithmetic, and
writes to four arrays (sample, capture time, generation, discontinuity).

This is exactly the kind of work that should not occur in a real-time callback. It
also consumes the GIL at the point where delayed service becomes an audible/capture
overrun. Per-sample timestamps and generation markers are more precise than the
runtime needs; block metadata plus absolute sample positions can reconstruct sample
time.

### 3. Full-spectrum data is repeatedly converted and validated in Python — high confidence

An 8192-point real FFT yields 4,097 coefficients. On every analyzer publication the
refactor converts the NumPy result into a tuple of 4,097 Python floats. When a visual
layer consumes the frame, `project_spectrum` validates and converts all 4,097 values
to another tuple before aggregating 64 output bins in Python.

The legacy path retains NumPy arrays and only produces the 64 values the renderer
needs. The refactor's immutable contract is reasonable, but immutability does not
require boxing thousands of scalars. A read-only NumPy array or fixed binary buffer
can provide the same ownership guarantee.

### 4. The generic renderer/compositor adds more work than it removes — high confidence

For one solid 64-column bar layer, the refactor still:

- allocates span/result/metric objects;
- scans rows to build available runs;
- scans the same spans again to claim pixels;
- updates three composition buffers per claimed pixel;
- validates colors on the hot path;
- copies the composed RGB buffer into another output buffer; and
- copies/decodes that buffer into Pillow before `SetImage`.

The design document correctly said that the compositor should use vectorized NumPy
or a small native loop rather than Python iteration, but the implementation uses
Python iteration. Bulk upload only optimizes the final Python-to-native call; it does
not compensate for the extra work required to construct the full frame.

At 64×64, a specialized direct bar renderer is small enough that the legacy canvas
path can beat a generic bulk path. `SetImage` should remain an option, but it must be
benchmarked as an end-to-end path rather than assumed faster from call count alone.

### 5. Pacing discards usable throughput — proven contributor, not the root cause

The deadline scheduler advances to the next interval whenever a frame is late. With
render cost near a deadline, a small miss can become an additional whole-period wait.
At a nominal 60 FPS this helped produce only 36.16 FPS. A nominal 240 FPS still waits
for a 4.17 ms boundary and reached 46.43 FPS. Removing that wait reached 70.22 FPS.

This explains a meaningful part of the regression but cannot explain why no-wait
refactor still trails legacy at lower CPU efficiency.

### 6. Render work is repeated when analysis has not changed — medium confidence

The no-wait run rendered at 70.22 FPS while producing analysis at only 21.59 Hz.
Most content frames therefore reused old analysis state, yet still paid composition,
packing, Pillow, upload, and VSync costs. Re-rendering may be valid for animated
themes or time-based fades, but static bar frames should not run the full pipeline
without a new analysis frame.

### 7. Generality and telemetry add cumulative hot-path overhead — medium confidence

Locks, identity ledgers, duplicate-history sets, dataclasses, protocol checks,
per-frame dictionaries, object validation, and detailed counters are individually
small. Together they add allocations and Python dispatch to every audio, analyzer,
and render boundary. The telemetry is useful, but should use coarse counters and
sampled timings rather than shape the primary data representation.

### 8. Thermal or panel throttling is not supported as a cause — high confidence

Neither path reported throttling, and the refactor performed worse even at lower
temperatures in some runs. The native panel path still contributes blocking time,
but both implementations used the same hardware and flags. It cannot explain the
relative loss by itself.

## Recommended architecture

### Principle: modular control plane, minimal data plane

Keep these as modular contracts:

- versioned configuration and validation;
- controller-owned persistence;
- atomic Apply at a frame boundary;
- analyzer definitions and canonical sharing keys;
- source/analyzer/layer registries;
- bounded health counters and rollout fallback.

Keep the high-frequency data plane compact and concrete. Avoid mappings, dataclasses,
JSON-shaped structures, boxed tuples, and generic pixel objects once a configuration
has been compiled.

### Recommended two-process topology

```text
controller/web service
  persistent configuration, validation, lifecycle, revisions
             |
       low-rate control IPC
             v

analysis process                          renderer process
----------------                          ----------------
audio callback                            owns RGBMatrix/native canvas
  bulk-copy PCM block                       reads latest presentation frame
  block metadata only        shared         applies render-time effects
           |                memory          direct fast path or compositor
preallocated NumPy ring  ---------------->  SwapOnVSync
           |              latest-value
compiled analyzer          mailbox; no
  FFT + projection         queues/backlog
  normalization/smoothing
  publishes 64-bin presentation frame
```

Why this boundary:

- Separate processes provide actual GIL and failure isolation.
- The renderer cannot starve PortAudio or analyzer Python work.
- Only display-ready data crosses the hot boundary: normally 64 `float32` bars,
  optional peaks, a sequence number, capture timestamp, and flags.
- The matrix binding and privilege transition remain confined to the renderer.
- A user-owned analysis process can later access PipeWire/Bluetooth without putting
  that concern in the matrix process.

A single-process build should remain possible for simplicity and direct comparison.
It should implement the same `AnalysisFrameSource` / `FrameRenderer` contracts, but
use one serialized hot loop or a worker that performs almost all work in vectorized
native code. Threads should not be described as performance isolation.

### Audio capture and ring

- Preallocate a contiguous `float32` ring, ideally shared memory when using two
  processes.
- Bulk-copy each incoming channel/block with NumPy or a buffer copy. Do not loop over
  samples in Python.
- Store metadata per block: absolute start/end sample, capture-end time, source
  generation, and discontinuity/status flags.
- Derive per-sample times from block time and sample rate only when required.
- Use single-producer/single-consumer indices. The consumer may skip to the newest
  complete window; it must never build an unbounded work queue.
- Size the ring from the largest supported window plus measured scheduling margin,
  not a blanket 131,072 samples unless that capacity is justified.

### Analysis and projection

- Precompile the window, FFT plan/backend state where available, and a projection
  operator for each distinct analyzer/layer mapping.
- Keep spectra as NumPy/native contiguous arrays. Mark views read-only instead of
  converting values to Python tuples.
- Apply projection before publication. A bar renderer usually needs 64 bins, not all
  4,097 FFT coefficients.
- Implement projection as indexed reductions, a precomputed sparse/dense matrix, or
  a small native kernel. Avoid validating every coefficient on every frame; validate
  buffer shape/type once at compile/activation time and check only sequence/shape at
  runtime.
- Run normalization, attack/release, and peaks on the analyzer cadence using
  vectorized arrays. Publish presentation-ready `float32` bars/peaks.
- If several layers share a raw analyzer but have different projections, calculate
  the FFT once and execute only the required compiled projections. Share identical
  projections as well as identical FFTs.

### Latest-frame transport

Use a double buffer or small fixed slot ring in shared memory:

```text
header: schema, generation, sequence, capture_time, publish_time, layer count
layer descriptor: type, offset, element count, flags
payload: contiguous float32 arrays
```

The producer writes an inactive slot, then atomically publishes its sequence/slot
index. The renderer reads the newest complete sequence and may skip older ones. No
pickle, JSON, Python tuple conversion, socket stream of magnitudes, or accumulating
queue belongs on this boundary.

### Rendering

Provide two compiled render plans:

1. **Fast path:** one bar layer or another known primitive writes directly to the
   returned native frame canvas, or to exactly one packed RGB buffer if end-to-end
   measurement proves `SetImage` faster.
2. **Composite path:** multiple layers/effects use preallocated NumPy buffers and
   vectorized/native mask operations. Never construct `PixelSpan` or color tuples per
   pixel in steady state.

Select the plan when configuration is applied, not through repeated type checks in
every frame. The fast path is a first-class supported implementation, not a temporary
exception to the modular model.

Eliminate avoidable copies. If the installed binding requires Pillow, determine the
minimum-copy supported representation once. Do not compose into one buffer, copy to
a second buffer, then copy into an image unless a measurement shows the copies are
negligible.

### Scheduling and backpressure

- Analysis cadence comes from sample positions/hop size.
- Render cadence comes from a realistic configured ceiling and panel behavior.
- Audio always has priority over optional redraws.
- Each boundary is latest-value, bounded, and lossy by design: skip stale analysis or
  render frames rather than catch up.
- Render a static visualization only on a new analysis sequence. Use a separate
  modest animation tick only for themes/effects that actually change with wall time.
- Pacing should sleep only the remaining time to the next deadline. When late, render
  the next useful frame without imposing an additional full-period penalty, while
  preventing a busy-loop from starving analysis.
- Put a hard content-FPS ceiling in the qualified hardware profile. Native PWM refresh
  is independent and does not require identical content FPS.

### Telemetry

Retain the useful counters, but measure stage boundaries directly:

```text
callback copy
ring/window acquisition
FFT
projection + temporal DSP
analysis-frame publication
render geometry
composition
frame packing/Pillow
native upload
VSync wait
```

Counters should be fixed-size, timing should be sampled or stored in bounded numeric
arrays, and reporting/percentile calculation should run outside the hot path. Process
CPU/RSS must be reported separately for analyzer and renderer if they become separate
processes.

## Rebuild sequence after returning to baseline

Each step should preserve a working visualization and be accepted or rejected before
the next abstraction is added.

1. **Freeze the evidence.** Preserve today's JSON and Markdown artifacts outside any
   rollback target. Record the exact source revision and Pi deployment state.
2. **Restore the known-fast baseline.** Confirm legacy behavior without importing the
   refactored data plane. Keep the refactor fallback/config work only if it can remain
   dormant and does not affect the loop.
3. **Introduce interfaces without changing execution.** Wrap legacy capture,
   analyzer, and renderer behind narrow contracts while keeping the same arrays,
   call order, and output path. This proves modularity does not itself require a
   performance loss.
4. **Compile configuration off the hot path.** Bring back validated versioned models
   and atomic graph replacement, but compile them into array-oriented runtime plans.
5. **Decouple analysis with the smallest transport.** First try a latest 64-bin
   double buffer. If a thread cannot preserve analyzer/callback service under render
   load, move the same contract to a separate process with shared memory.
6. **Choose the output path empirically.** Compare complete direct-canvas and
   one-buffer bulk paths. Do not compare only `SetPixel` versus `SetImage` call cost.
7. **Add the composite path only when multiple layers require it.** Keep the
   one-layer fast path intact. Use vectorized/native operations from the beginning.
8. **Add advanced analyzers/layers one at a time.** Charge configuration budgets from
   measured costs, not optimistic isolated FFT estimates.

Per the session constraint, no hardware run, long benchmark, or LAN test should be
started automatically. Before each measurement gate, ask for approval with a short
statement of the exact run, expected duration, and decision it will inform.

## Acceptance gates for the rebuilt system

The legacy run is the baseline, not necessarily the final required FPS. A candidate
should not replace it unless all relevant gates pass on the Pi:

- Standard one-analyzer/one-layer mode has no unexplained audio overruns or missed
  hops during the agreed test window.
- Uncapped rendering does not materially reduce analyzer cadence; if it does, the
  process boundary or priority/backpressure design is still wrong.
- Standard-mode content throughput and latency are at least as good as legacy, or a
  deliberate lower content cap has visibly equivalent behavior with clear CPU margin.
- CPU and RSS remain within an agreed budget, measured per process.
- Stage timings identify where p50/p95/p99 time is spent; a single aggregate frame
  timer is insufficient for further optimization decisions.
- Applying configuration cannot partially mutate live state and does not interrupt
  audio capture.
- The one-layer fast path remains active and testable after multi-layer features are
  introduced.
- A sustained run shows no thermal throttling, growing queue, unbounded allocation,
  or increasing audio age.

## Rollback note

The rollback was completed after this analysis. Tracked implementation and test
changes were restored to `f23a34a` (`Docs: Plan FFT Refactor`), and the untracked
refactor implementation/tests were removed. The November 17–18 Markdown, raw JSON
measurements, plan archive, mockups, and qualification record were retained as legacy
evidence. The removed untracked implementation is not recoverable through local Git;
the November 17 Pi archive identified in the session summary is the recovery source
if historical code inspection is ever required.
