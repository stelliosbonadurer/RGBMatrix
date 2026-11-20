# Decisions

## D-001 — Modularity is the active priority

**Decision:** Focus the remaining refactor on modular analyzers and ordered visual
layers. The current matrix performance is adequate, so pacing refinements and
time-based animation are deferred.

**Rationale:** This creates product flexibility now without optimizing a timing
problem that has not shown itself to be limiting.

## D-002 — Preserve two explicit output backends

**Decision:** Keep both direct canvas drawing and reusable Pillow plus `SetImage`.
Select the backend manually when compiling/applying the runtime plan. Direct remains
the default until later evidence justifies a different default.

**Tradeoff:** The backends may need separate optimized drawing implementations. That
duplication is preferable to forcing both through a generic abstraction that weakens
the direct hot path.

**Deferred:** Automatic selection and the layer-count or complexity threshold at
which SetImage may become preferable.

## D-003 — Compile a modular control model into a concrete hot path

**Decision:** Configuration may be modular and descriptive, but frame execution will
use a precompiled ordered plan with stable arrays, state objects, and drawing
callables. Do not construct mappings, boxed pixels, generic command lists, or other
per-frame interchange objects.

**Rationale:** The earlier failed refactor showed that an object-heavy generic data
plane can erase the performance benefit of the native drawing path.

## D-004 — Share analyzers by stable identity and canonical definition

**Decision:** Each layer references an analyzer ID. Equivalent analyzer definitions
resolve to one runtime analyzer result where safe, so two layers do not automatically
perform two FFTs.

For the first increment, an analyzer is the complete current analysis plan that
produces its compact magnitude array. Sharing intermediate FFTs between different
projection definitions is a later optimization, not a prerequisite.

## D-005 — Layers are ordered modules with independently owned presentation state

**Decision:** A compiled plan contains an ordered collection of layers, interpreted
bottom to top. Each layer has a stable ID, analyzer reference, visibility, immutable
compiled settings, and its required mutable presentation state.

Smoothing, peaks, or similar presentation state must not remain an implicit global
singleton. It belongs to the layer that uses it unless an explicit future definition
requests shared presentation state.

The existing peaks pass may remain part of the current bars layer during the first
migration. It does not need to become a separately reorderable product layer merely
to prove the architecture.

## D-006 — Structural changes use compile then swap

**Decision:** Add, remove, reorder, visibility, and reference changes are validated
and compiled off the frame path. A complete valid plan replaces the active plan at a
safe frame boundary. Invalid combinations leave the prior plan active.

## D-007 — Backend support is checked before activation

**Decision:** A layer definition may provide backend-specific compiled drawing
kernels. The compiler rejects a selected backend/layer combination that is not yet
implemented. Initial work does not require every future layer type to have immediate
feature parity across both backends.

**Invariant:** The current supported scene must remain runnable in both direct and
SetImage modes.

## D-008 — Test a separate matrix process early, but do not build on it first

**Decision:** First establish analyzer and render-plan seams in one process. Then run
a bounded experiment in which a matrix process owns layer presentation state, the
selected output backend, matrix canvases, and swap calls, while an analysis process
owns audio capture and analyzers.

The experiment is a decision gate. It does not automatically promote the two-process
topology.

**Rationale:** Process isolation could avoid GIL interference and give the matrix an
independent cadence, but it also introduces serialization/shared-memory, lifecycle,
failure, and deployment complexity. Clean in-process seams make the experiment
smaller and remain useful if the process split is rejected.

## D-009 — If split, use two hot-path processes and latest-value numeric transport

**Decision:** Do not create a process per analyzer or layer. The candidate topology
has one analysis process and one matrix/render process. Transfer compact fixed-shape
numeric analyzer results through a bounded latest-value mechanism; use a separate
low-rate channel for validated plan/control changes.

The renderer may drop superseded analysis results instead of accumulating a queue.
No unbounded frame or spectrum queue is permitted.

## D-010 — Avoid a general compositor in this phase

**Decision:** Clear once, then execute the already compiled visible layers in order.
Do not add general alpha blending, masks, intermediate full-frame surfaces, or a
universal draw-command protocol until a real layer requires one.

Tests may use small fake layers to prove ordering without building artificial
production scenes.

## D-011 — Preserve the present timing model

**Decision:** The roadmap does not change fixed sleep, VSync behavior, animation
units, or maximum-rate behavior. Timing work can resume later against the modular
runtime if observed behavior gives it priority.
