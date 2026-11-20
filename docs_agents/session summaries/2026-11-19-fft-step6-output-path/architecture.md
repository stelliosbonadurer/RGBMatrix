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
