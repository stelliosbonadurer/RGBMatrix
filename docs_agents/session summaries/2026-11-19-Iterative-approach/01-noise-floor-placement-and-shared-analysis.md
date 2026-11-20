# FFT rebuild design notes — noise-floor placement and shared analysis

> **Status: design notes only; no implementation change is approved here.**
> Keep the committed step-5 behavior unchanged through its Raspberry Pi
> qualification so it remains directly comparable with accepted step 4.

## Current context

The locally verified step-5 implementation is commit
`5e018a4a3ec36ea81fb985153b2897fd9a3d184a`. Its standard analysis worker publishes
the accepted weighted, noise-subtracted 64-bin result:

```text
512-sample input block
  -> Hann window
  -> 8192-point rFFT
  -> 64-bin projection
  -> frequency weighting
  -> subtract noise_floor
  -> clamp to zero
  -> publish latest complete 64-bin frame
```

This placement was retained in step 5 to preserve the existing numerical contract
while changing only execution and transport. It does not establish that noise
subtraction permanently belongs to the FFT/analyzer boundary.

## Unresolved render-timing question

The existing fixed 5 ms sleep after each frame is not assumed to be the permanent
timing model. A later design checkpoint must determine how to provide smooth,
consistent rise/fall behavior while degrading gracefully when rendering slows down.
Possible concerns include elapsed-time-based temporal DSP, render deadlines, missed
deadline handling, and protection against pathological time gaps. This note does not
choose among them, and step 5 retains the accepted 5 ms pacing unchanged.

## Noise subtraction is post-projection DSP

Noise subtraction is not intrinsically an FFT operation. Its current position matters
because it occurs before automatic normalization, smoothing, and peaks:

```text
weighted magnitudes
  -> noise subtraction
  -> normalization
  -> attack/decay smoothing
  -> peak tracking
  -> drawing
```

Removing low-level background energy before normalization prevents the automatic
scaler from treating microphone or room noise as useful signal and amplifying it.
Moving the same operation after normalization or smoothing would change behavior and
could cause unstable gain or abrupt visual cutoffs.

The pixel renderer should not own this processing. If it moves out of the shared
analyzer, it belongs in a per-view DSP/preprocessing stage immediately before that
view's normalization.

## Two distinct controls

Future multi-view work may need two explicitly different concepts.

### Source calibration floor

```text
projected analyzer bins
  -> source_noise_floor
  -> shared analyzer publication
```

This represents physical input conditions such as microphone self-noise, interface
noise, or a stable room baseline. It is applied once and shared by every consumer of
that analyzer result.

If source-floor configuration differs, the analyzer contract differs. The value must
therefore be part of the analyzer's canonical sharing key or worker state; consumers
requesting different values cannot silently share the same post-floor publication.

### Per-view input control

```text
shared analyzer result
  -> view_input_gate or view_input_floor
  -> that view's normalization
  -> that view's smoothing and peaks
  -> that view's drawing path
```

This is a visualization control rather than source calibration. Separate views may
use different values while sharing the same analyzer output. For example, one view may
show quiet bass movement while another suppresses low-level treble activity.

Each view already needs independent normalization, attack/decay, smoothing, peaks,
theme, geometry, and drawing state. A 64-element per-view preprocessing operation fits
naturally at the start of that temporal DSP path.

## Analyzer-owned bin projection

Bin projection must be an explicit parameter of each analyzer, not an implicit global
setting or a choice made by the drawing path. An analyzer configuration defines the
projection from FFT coefficients into its output bins, including the output-bin count
and the compiled bin-index and aggregation coefficients needed by that projection.

The projection is compiled when the analyzer is created or when its accepted
configuration is applied. The worker then reads one immutable compiled plan; it does
not rebuild projection state, validate configuration, or perform registry lookups in
the hot path. The analyzer's latest-value transport has a fixed payload shape for the
lifetime of that compiled plan.

Projection configuration is part of the analyzer's canonical sharing identity. Views
may share one analyzer only when they request the same FFT and projection contract.
A view requiring a different bin count, frequency mapping, or aggregation uses a
separately configured analyzer rather than re-projecting all FFT coefficients inside
the visualization path. Any weighting intrinsic to the projection must be distinguished
from the presentation response curve described below.

## Per-visualization frequency-response curve

Each visualization layer must be able to boost or lower frequency regions with a
small tunable response curve of approximately four control points. This is separate
from selecting the analyzer's bin projection. Multiple visualization layers sharing
one analyzer may use different response curves.

The accepted legacy/current implementation does not yet provide that contract. It has
only two global settings, `low_freq_weight` and `high_freq_weight`, connected by the
fixed curve `low + (high - low) * normalized_position**1.5`. That curve is compiled
into `StandardAnalysisPlan.bin_weights` alongside the projection indices and applied
inside `AudioProcessor._analyze_into()` before the 64-bin result is published. It is
therefore global analyzer-side processing in the accepted code, not per-visualization
processing.

The rejected large refactor modeled arbitrary `response_points` on each visual layer.
That ownership matches the new per-layer requirement, but its implementation and
broader architecture are not being restored automatically. A future modular checkpoint
must decide how a layer compiles its few control points into a fixed per-bin gain array
and applies it before that layer's normalization and temporal processing, without
per-frame configuration work or allocation.

The current design direction is that the artistic response curve belongs only to the
visualization layer. It must not be an analyzer parameter: placing it there would
either force every consumer to use the same visual tuning or require duplicate
analyzers solely because their layers use different curves, defeating analyzer
sharing.

An analyzer-level source-calibration response curve for microphone, room, or input-
system correction is deferred until measurements demonstrate a need. If later added,
it must be separately named, normally neutral, shared by every analyzer consumer, and
included in the analyzer contract and sharing identity. It must not be confused with,
or reuse the name of, the artistic per-visualization curve.

## Subtraction versus gating

These behaviors should not be conflated:

- **Subtractive floor:** `max(0, magnitude - floor)` removes a fixed baseline and
  reduces every surviving value. This is the current behavior.
- **Threshold gate:** values below a threshold become zero, while surviving values
  remain unchanged. This is a different visual control.

If both are eventually exposed, use distinct names and document their domains. Do not
provide two ambiguous controls both called `noise_floor`.

Suggested terminology:

- `source_noise_floor` for physical input calibration;
- `view_input_floor` for per-view subtraction; and
- `view_input_gate` for a non-subtractive threshold.

## Sharing and transport implications

When several views use the same compiled frequency range, bin mapping, aggregation,
FFT configuration, and source calibration, the analyzer can publish one immutable
64-bin frame for all of them. The render orchestrator should lease that frame once,
run each view's response curve, preprocessing, and scaler sequentially, release the
analyzer slot, and then draw from each view's independent smoothed arrays.

```text
lease shared 64-bin analyzer frame once
  -> preprocess/scale view A
  -> preprocess/scale view B
  -> preprocess/scale view C
release analyzer frame once
  -> draw A, B, C
```

No lock is held during preprocessing, scaling, drawing, composition, or VSync. The
initial implementation should use the direct shared lease. If hardware measurements
show that several view scalers hold the lease too long, the smallest fallback is one
copy into a preallocated 64-element render scratch array per new analysis sequence,
not one copy per view and not a per-frame allocation.

A view needing a different configured projection is a distinct analyzer instance with
its own bounded latest-value transport. Do not publish all 4,097 FFT coefficients
merely to support arbitrary downstream projection.

## Recommended future direction

After the current threaded step-5 candidate is qualified on the Pi:

1. Consider making the shared analyzer publication projected but free of the artistic
   response weighting and noise subtraction.
2. Compile each visualization's control points into its own fixed per-bin response
   array and apply it before that visualization's normalization and temporal DSP.
3. Move the existing configurable subtraction into each view's preprocessing stage,
   immediately before normalization.
4. Preserve the current subtract-and-clamp calculation initially so the architectural
   move can be compared independently from a behavior change.
5. Add a separate source calibration floor only if input measurements show a real
   need for one.
6. Add a threshold gate only as a separately named, separately tested visual control.

This direction maximizes analyzer sharing while keeping display behavior independently
configurable. It is intentionally deferred until after step-5 Pi qualification; no
code, configuration schema, runtime protocol, or hot-path behavior changes as a result
of these notes.
