# Runtime and Multi-Analyzer Architecture

## Required invariants

The implementation is not considered correct unless all of these hold:

1. Audio is captured once per active source.
2. Each PCM write has a monotonic sequence/sample position and capture timestamp.
3. An analyzer runs at most once for a given due hop/sample position.
4. An unchanged microphone block is never reanalyzed.
5. A display frame never triggers an FFT merely because it is being rendered.
6. A visual layer never computes its own FFT.
7. Many visual layers may consume the same immutable analysis frame.
8. Identical effective analyzer configurations may share one internal computation.
9. Every configuration is validated and compiled before it replaces active state.
10. Apply, analyzer rebuild, and source switching cannot partially mutate the active
    analyzer/layer graph.

## Process boundaries

The target has three responsibilities:

```text
controller/web service (normal user)
  authentication, draft Apply API, persistent libraries, lifecycle
                     |
                     v
audio/analysis service (normal user)
  ALSA/PipeWire access, PCM ring, analyzer scheduler, analysis frames
                     |
              bounded local transport
                     v
matrix renderer (initializes as root, drops to daemon)
  layer processors, compositor, bulk frame publication, native refresh owner
```

The first implementation may keep analyzer and renderer classes in one process for
the USB source, but their interfaces and tests must respect this boundary. Bluetooth
requires the user-owned audio side because the per-user PipeWire graph is not
available to the dropped `daemon` process.

Configuration transport carries typed semantic state. Analysis transport should use
fixed binary metadata plus a bounded shared-memory/ring representation rather than
JSON arrays of magnitudes.

## Audio source contract

An `AudioSource` provides:

- Stable source identity and display metadata.
- Actual sample rate, channel layout, and sample format.
- Monotonic sample positions and capture timestamps.
- Bounded health counters: frames, discontinuities, overruns, reconnects.
- Start, stop, and readiness lifecycle.
- PCM writes into a preallocated single-producer ring.

The real-time callback must not perform FFTs, network operations, file writes,
logging bursts, or configuration parsing. It copies or exposes samples through a
preallocated ring, records bounded counters, and returns.

## PCM ring and sample identity

The ring is addressed by an ever-increasing absolute sample position. An analyzer's
next due position is independent of the renderer and other analyzers.

Each contiguous view carries:

```text
source_generation
start_sample
end_sample
capture_time_monotonic
sample_rate
channel_count
discontinuity flag
```

Changing source or format increments `source_generation`. An analyzer must not join
samples from different generations into one window. On overflow, the scheduler
advances to the newest complete usable window, records missed hops, and never tries
to catch up with an unbounded burst of stale FFT work.

## Analyzer definition and compiled analyzer

The user definition is durable configuration. Applying it produces a compiled
analyzer containing:

- Precomputed window coefficients.
- Next due absolute sample position.
- FFT work buffers where the chosen backend allows reuse.
- Frequency axis and normalization metadata.
- Canonical computation key.
- Timing counters and rolling percentiles.

The canonical key includes every input that changes the numerical spectrum:

```text
source generation / channel selection
window function and window length
hop length
FFT length
DC-removal and spectrum-output mode
any genuinely pre-transform filter
```

Name, favorite ID, UI order, consumers, and visual frequency ranges are excluded.
Analyzer cells with the same canonical key can point to the same compiled worker and
cached frame. The runtime still returns both user-visible cells and reports that they
are sharing computation.

## Scheduler

The analyzer scheduler wakes for new PCM availability, not on every render loop.

For each compiled worker:

1. Compare the ring's latest sample position with the worker's `next_due_end`.
2. If a complete window is unavailable, do nothing.
3. If several hops were missed and catching up would violate the time budget, skip
   to the newest aligned complete hop and increment `missed_hops`.
4. Read the exact window ending at `next_due_end`.
5. Apply DC removal/windowing and run one real FFT.
6. Build one immutable `SpectrumFrame`.
7. Publish it to the per-analyzer cache.
8. Advance `next_due_end` by the hop size.

The scheduler attaches `(source_generation, analyzer_compute_id, end_sample)` to
every output. The cache rejects a duplicate identity. This makes accidental
same-block reanalysis observable and testable.

## Analysis frame

```text
analyzer_compute_id
source_generation
sequence
window_start_sample
window_end_sample
capture_start_time
capture_end_time
analysis_complete_time
sample_rate
window_samples
hop_samples
fft_samples
frequency_axis
magnitude/power values
source discontinuity state
```

Frames are immutable and reference-counted or copied through bounded buffers. A
slow renderer consumes the newest compatible frame; it cannot block audio capture or
make the scheduler accumulate unbounded history.

## Visual-layer processing

A visual layer owns presentation-specific processing:

- Analyzer reference.
- Frequency range and log/linear projection.
- Gain curve interpolation.
- Bin aggregation.
- Noise gate or calibrated floor if presentation-specific.
- Normalization/headroom.
- Time-based attack/release.
- Peak state.
- Renderer state and theme.

The runtime precomputes a projection table from analyzer bins to display bins plus
interpolated gain coefficients whenever the connected analyzer or response curve
changes. Per-frame work should then be bounded array operations rather than repeated
mask discovery.

Each layer keeps its last consumed analysis-frame sequence. It updates temporal DSP
only once per new analysis frame, even if the same result is drawn several times.
Dynamic visual themes may advance on render time, but spectrum smoothing advances on
analysis time.

## Hidden and occluded layer behavior

Three states must not be conflated:

- **Disabled analyzer**: no computation and no frames.
- **Hidden visual layer**: excluded by user choice.
- **Occluded pixels**: a visible lower layer whose pixels are covered by higher
  layers in the current frame.

Default policies:

- Hidden layers do not generate pixels. Their analysis projection/smoothing may be
  suspended to save CPU. When made visible, they reset or warm from the latest frame
  according to a documented `resume_policy`.
- Visible but fully occluded layers continue consuming analysis frames and updating
  temporal state, so revealing them does not produce a stale jump. Pixel generation
  is skipped when the compositor proves no unclaimed target pixels remain.
- Partially occluded layers generate only potentially visible pixels when their
  renderer supports a remaining-coverage mask.

This separation preserves predictable animation while avoiding unnecessary drawing.

## Future note and feature analyzers

The reusable invariant is one shared PCM capture and reusable feature frames, not one
FFT forever. A note detector may need:

- A longer observation window.
- Overlapped multi-resolution FFTs.
- Harmonic product spectrum, autocorrelation, YIN, or constant-Q analysis.
- Onset/chroma frames on another cadence.

Future feature analyzers join the same registry/scheduler and publish typed immutable
frames. A `note_visualization` layer references a compatible note-frame source just
as `fft_bars` references a spectrum source. Common transforms can still be
canonicalized and shared.

## Apply/rebuild lifecycle

The runtime uses double-buffered configuration state:

1. Parse and validate a candidate into immutable definitions.
2. Resolve analyzer/layer references.
3. Canonicalize distinct computations and estimate cost.
4. Compile new and changed analyzers, projections, layer state, and renderers in a
   staging graph.
5. Reuse compatible active objects only through explicit identity rules.
6. Optionally warm expensive new analyzers for a bounded number of hops.
7. Swap `active_graph` at a safe analyzer/render boundary.
8. Retire old state after current readers release it.

If any step fails, active state and revision remain unchanged. No caller mutates live
objects one field at a time.

## CPU-budget validation

Budgeting operates on distinct compiled computations, not analyzer-cell or
visual-layer count.

The candidate estimate includes:

- FFT benchmark cost by FFT size.
- Requested analyses per second: `sample_rate / hop_samples`.
- Window/preprocessing and result-copy estimates.
- Layer projection/scaling cost.
- Estimated render/composition cost by layer type and coverage.
- Reserved margin for the web service, OS, audio callback, and native refresh.

Initial hard gates on the Pi 4:

- No more than four distinct active FFT computations.
- Estimated analysis work no greater than 30% of one application core.
- Estimated p99 analyzer completion less than 80% of its hop interval.
- Total configured content rate no greater than the tested renderer budget.
- Ring capacity sufficient for the largest window plus scheduling jitter.

The estimate is seeded with boot-time or provisioned microbenchmarks and corrected
by observed EWMA/p95/p99 metrics. A candidate beyond a hard limit is rejected with a
per-analyzer cost explanation. A near-limit candidate may be accepted with a warning
only if the product explicitly supports warnings; it must never silently degrade to
unbounded missed work.

## Failure behavior

- Audio discontinuity: mark frames discontinuous, reset affected temporal state,
  retain the last display frame or fade safely.
- Analyzer deadline miss: skip stale hops, record the count, publish newest complete
  result.
- Renderer slow: drop intermediate display frames; never block capture.
- Analysis service disconnect: renderer fades/holds according to policy and reports
  offline status.
- Invalid Apply: preserve old state and return structured field errors.
- Source switch failure: keep or restore the previous source and active graph.

