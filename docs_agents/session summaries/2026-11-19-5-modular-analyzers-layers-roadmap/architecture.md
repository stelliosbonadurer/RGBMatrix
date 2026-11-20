# Architecture

## Preserved invariants

- Direct native-canvas drawing remains a first-class optimized path and the default.
- SetImage remains selectable through its reusable Pillow image implementation.
- Output selection is resolved outside the frame loop.
- Analyzer outputs remain compact NumPy arrays with stable, bounded storage.
- The renderer consumes the newest complete analyzer result and need not render every
  intermediate result.
- No per-frame generic scene graph, pixel-object graph, or unbounded queue is added.
- Current animation and pacing behavior remains unchanged.

## Target logical model

```text
RuntimeDefinition
├── analyzers: stable ID -> AnalyzerDefinition
├── layers: [LayerDefinition, ...]       # bottom to top
└── output_backend: direct | setimage    # explicit selection
             │
             ▼ validate and compile off hot path
CompiledRuntimePlan
├── AnalyzerRegistry
│   └── stable ID/canonical key -> latest fixed-shape result
└── CompiledRenderPlan
    ├── selected output backend
    └── ordered layer runtimes
        ├── analyzer-result reference
        ├── backend-specific draw kernel
        └── layer-owned presentation state
```

The definition layer is flexible. The compiled plan is intentionally concrete.
Layer and analyzer lookups must not become repeated string/dictionary work inside
per-pixel loops.

## Analyzer ownership

An analyzer definition initially represents the complete current transformation from
captured audio to the compact magnitudes a layer consumes. It has:

- a stable public ID used by configuration and controls;
- a canonical immutable definition used to share equivalent runtime computation;
- a declared result shape and numeric type;
- bounded latest-value result storage;
- lifecycle and health state outside the numeric hot path.

Multiple layer IDs may hold direct references to the same compiled analyzer result.
The first implementation can still run exactly one analyzer. The registry shape is
introduced so adding another analyzer does not require rewriting the render loop.

Sharing an FFT between analyzers with different projections is intentionally
deferred. It should be added only when multiple real analysis plans show duplicated
work worth eliminating.

## Layer ownership

A layer definition has a stable ID, type, analyzer ID, visibility, and settings. Its
compiler resolves these into a runtime object containing direct references and the
minimum mutable state needed to render.

Presentation state such as smoothing or held peaks is layer-owned by default. This
allows two layers to share one magnitude array while using different visual history.
The initial bars migration can keep its current peak overlay internal, preserving
appearance and avoiding a false dependency between user-visible layers.

The active layer collection is immutable in structure during a frame. Add/remove or
reorder operations create a new validated tuple of compiled layer runtimes and swap
it at a frame boundary.

## Output backend ownership

`direct` and `setimage` are separate executors compiled from the same validated
runtime definition:

- Direct layers draw against the native canvas.
- SetImage layers draw against the reusable RGB Pillow image, which is submitted to
  the native frame canvas.

The backend is selected once per active plan; the frame loop does not test the mode
for every primitive. Backend-specific kernels are allowed when that preserves the
fast path. Unsupported layer/backend combinations fail compilation rather than
failing after activation.

This is not a promise that every future layer appears in both backends immediately.
It is a promise that neither backend is deleted or hidden behind an irreversible
abstraction.

## Candidate process boundary

After the in-process contracts work, test this topology:

```text
analysis process                         matrix/render process
----------------                         ---------------------
audio capture                            compiled ordered layers
analyzer registry       latest arrays    presentation state
latest complete result  ==============>  direct or SetImage executor
health/status             bounded slots  RGBMatrix/canvas/swap ownership

control/config process  ---- low-rate validated plan messages ---->
```

The exact launcher may keep analysis/control in the current main process, yielding
two operating-system processes total. The important ownership rule is that all
native matrix objects are created and used by the matrix process, while analyzer
computation does not execute there.

Use fixed-shape shared numeric slots with a generation/ready protocol, or another
equally bounded latest-value transport. Do not send full Pillow images or per-draw
commands across the boundary. Configuration changes travel on a low-rate control
channel and become complete compiled plans before activation.

The spike must cover startup, shutdown, stale data, analyzer failure, renderer
failure, and the platform's process-start constraints before promotion. Detailed
privilege and pacing design is deliberately left to the spike because it is not
needed to define modular layers.

## Rejected foundations

- One process per analyzer or per layer: excessive lifecycle and transport cost.
- A universal compositor before a layer requires blending: broadens scope and can
  penalize the direct path.
- Per-frame dictionaries or generic render-command objects: unnecessary hot-path
  overhead.
- Making process separation mandatory before extracting ownership seams: it mixes
  architecture cleanup with IPC debugging.
- Treating SetImage as an automatic multi-layer winner: current measurements cover
  one sparse workload and do not support that conclusion.
