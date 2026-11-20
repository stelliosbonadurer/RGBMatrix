# Architecture and invariants

The production architecture is unchanged. The accepted step-5 worker and two-slot
latest-value transport still publish complete 64-bin results; the main loop still
scales, releases the lease, draws, optionally overlays peaks, swaps on VSync, and
sleeps five milliseconds.

The candidate is a startup-selected renderer implementation, not a new composition
layer. Direct mode retains the original `BarsUnifiedVisualizer` object. Candidate
mode substitutes one immutable, qualification-only output plan at the same draw call.
Selection, validation, Pillow import, geometry, colors, and image allocation all
remain outside the frame loop.

The current Python binding's `SetImage` still traverses all 4,096 pixels and calls
native `SetPixel`; it is not a raw zero-copy upload. A deferred native binding method
could accept a stable contiguous RGB buffer and move traversal into C/C++, but would
require pinned binding source/build flags, explicit ownership/stride/GIL semantics,
a native deployment artifact, and separate recovery and hardware qualification.

Preserved invariants: direct is default, no analysis lock during output/VSync, no
unbounded queue, no per-frame output-plan compilation, no silent fallback, and no
production promotion from an experiment.

## Measured architectural conclusion

The candidate moved cost rather than removing it. Pooled Pillow swap latency improved
21.28% at p50 and 16.91% at p95, but renderer latency regressed 30.66%/30.69%. The
combined interval improved only 1.70%/0.27%, and the fixed five-millisecond sleep plus
the slower complete frame reduced swap FPS by 2.31%.

This also clarifies the thread question. The analyzer and renderer did make useful
parallel progress: all runs published about 84 analysis results per second and kept
result-age p95 below two audio blocks. Separate threads did not make output work free,
however. Pillow raised observed process CPU by about five percentage points and the
binding's GIL-holding/full-frame work still competed inside one process. Parallel
structure permits overlap where native code releases the GIL; it does not guarantee
simultaneous Python execution or a faster critical path.

Direct rendering remains the accepted output architecture. A future bulk experiment
must eliminate or materially reduce the full-frame Pillow/binding traversal rather
than merely hiding 4,096 native writes behind one Python call.
