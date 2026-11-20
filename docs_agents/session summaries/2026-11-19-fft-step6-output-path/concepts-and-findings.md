# Output-path concurrency, buffering, and performance findings

> **State:** pre-measurement structure. Sections labeled “Hypothesis” describe what
> the implementation suggests; they are not hardware findings. This record will be
> completed after the approved four-run Pi comparison.

## Case-study question

For a 64x64 HUB75 audio visualizer, does replacing thousands of Python-to-Cython
`SetPixel` calls with one reusable Pillow image and one `SetImage` call materially
increase displayed and fresh-content frame rates without weakening service quality?

## Concepts under test

### Python call count is not the complete cost

The direct path has many Python/Cython calls but writes only visible pixels. The
Pillow path makes one upload call, but first clears and populates a complete RGB image
and then traverses all 4,096 pixels inside the binding. A lower Python call count is
therefore a hypothesis about speed, not evidence of a faster complete frame.

### Buffer reuse is not automatically zero-copy

The candidate reuses one Pillow-owned image and creates no NumPy RGB staging frame.
This removes an application-level frame copy and stabilizes ownership, but the
binding still reads Pillow storage and translates every pixel into native canvas
writes. Stable identity and zero-copy are different claims.

### Separate threads allow overlap but do not guarantee it

The analyzer worker and render loop are separate OS threads. NumPy native work can
overlap with rendering when it releases the GIL. In the inspected local binding,
`SetImage` traverses the image without a `with nogil` block and `SwapOnVSync` is not
declared `nogil`; those sections can delay Python work in the analyzer. The exact Pi
binding must be verified before treating this as measured target behavior.

### Display FPS and fresh-content FPS answer different questions

- **Swap/display FPS** measures how quickly complete frames reach the VSync boundary,
  including repeated rendering of the latest analysis result.
- **Unique-content FPS** counts distinct acquired analysis sequences actually swapped
  and detects whether rendering load starves fresh FFT results.

The speed decision requires both: swap FPS is primary, while unique-content FPS may
not regress by more than five percent.

### Counterbalancing limits environmental bias

Live music, temperature, and background load vary over time. The approved
`direct -> Pillow -> Pillow -> direct` order creates one pair in each direction. It
does not make the input deterministic, so workload density, temperature, throttling,
and per-run results must still be reported rather than hidden in a pooled average.

## Hypotheses before measurement

1. Pillow may win when Python call overhead dominates dense direct drawing.
2. Direct drawing may remain faster for sparse frames because it avoids a full-frame
   Pillow clear/upload traversal.
3. Longer GIL-holding sections in `SetImage` or VSync may limit analyzer/render
   overlap even if raw swap FPS improves.
4. CPU use may increase without invalidating the candidate, provided there is no
   throttling, thermal instability, or service-quality regression.

## Measurement design

Pending final source identity and hardware approval. The approved design uses four
five-minute representative-live-music windows, 60-second settles, exact preallocated
timing samples, swap and unique-content rates, stage latencies, workload context, and
the existing analysis/service gates.

## Measured results

Not measured yet.

## Interpretation and future-work implications

To be completed from retained raw artifacts after measurement. This section will
separate observed facts, derived comparisons, architectural inference, limitations,
and recommendations for a possible native-buffer binding experiment.
