# Deliberate implementation roadmap

Each step is a stop/go boundary. Do not automatically continue into the next step.
Keep direct output as the default and preserve the current runtime until a candidate
passes its step's acceptance criteria.

## Step 1 — Define the compiled runtime graph

Create the smallest configuration and compiled-plan types needed to describe:

- analyzer definitions with stable IDs;
- an ordered list of layer definitions with stable IDs, analyzer references, and
  visibility;
- explicit `direct` or `setimage` output selection;
- validation errors for duplicate IDs, missing references, invalid order, and
  unsupported layer/backend combinations.

Keep this step off the running hot path. It should compile definitions and prove
identity/order semantics without changing matrix output.

Acceptance criteria:

- Deterministic compilation produces stable ordered plans.
- Invalid definitions are rejected without modifying an active plan.
- Equivalent analyzer definitions have a documented canonical key.
- Tests prove stable ID lookup and bottom-to-top order.
- No frame timing, UI, process, or native-matrix behavior changes.

Stop and review the model before it owns execution.

## Step 2 — Introduce the in-process analyzer registry

Wrap the current `AudioProcessor`/analysis plan behind an analyzer registry while
retaining its existing fixed NumPy result transport. Begin with one real analyzer.
Allow two test layer references to resolve to the same analyzer runtime and result
buffer.

Do not add a second physical audio reader, general DSP graph, or cross-analyzer FFT
sharing in this step.

Acceptance criteria:

- Current analyzer numerical behavior and update semantics are preserved.
- Equivalent definitions do not create duplicate analyzer execution.
- Result buffers remain bounded, fixed-shape, and allocation-stable in steady state.
- Analyzer startup, shutdown, and failure are explicit and tested.
- The current renderer can still consume the result without generic per-frame
  serialization.

Stop and compare allocation, CPU, and current-scene behavior with the baseline.

## Step 3 — Introduce the in-process ordered layer plan

Migrate the current visualizer as the first real layer type. Keep its existing peaks
behavior internal initially. Move implicit global presentation state into that layer
runtime and compile its analyzer reference once.

Execute an immutable ordered tuple of visible layer runtimes. Use fake/instrumented
layers in tests to prove add/remove/hide/reorder behavior; do not invent a complex
production scene solely for qualification.

Compile backend-specific execution plans:

- preserve the existing direct drawing implementation;
- preserve the reusable Pillow/SetImage implementation for the current supported
  scene;
- select the executor when the plan is activated, not within primitive loops.

Acceptance criteria:

- The current visual output and controls remain behaviorally equivalent.
- The active plan can be replaced atomically between frames.
- Tests prove exact layer order, visibility, add/remove, and invalid-plan rollback.
- Two layers can reference one analyzer result while retaining separate layer state.
- Both output modes run the current supported scene; direct remains the default.
- No general alpha compositor or per-frame command graph is introduced.

Stop for a hot-path and ownership review before adding multiprocessing.

## Step 4 — Run a bounded matrix-process experiment

Use the established seams to move matrix/render ownership into one child process.
Keep audio capture and the analyzer registry together outside that process. Transfer
only compact latest-value analyzer arrays and low-rate control/plan updates.

Start with the existing current scene and one analyzer. Exercise both direct and
SetImage modes, but do not add new layer features during this experiment.

The experiment plan must define before Pi testing:

- exact baseline and candidate commits;
- process start method and matrix/native-object creation point;
- fixed result shape, slot count, and completeness protocol;
- startup, shutdown, crash, stale-data, and reconnect behavior;
- CPU, memory, analysis throughput, matrix FPS, result age, and dropped/superseded
  update metrics;
- run order, duration, quality gates, and rollback procedure.

Promotion gate:

- Keep the split only if it measurably improves isolation or sustained behavior, or
  provides a clear operational benefit that justifies its IPC and lifecycle cost.
- Reject it if it adds instability, unacceptable result age, material frame-rate
  loss, or disproportionate complexity.
- Whether accepted or rejected, retain the clean in-process analyzer/layer contracts.

Hardware deployment and benchmarking require a separate explicit approval. Testing
the candidate is not approval to promote it.

## Step 5 — Add minimal structural controls

After deciding the process topology, connect the smallest control operations needed
to demonstrate the modular architecture:

- add a predefined layer;
- remove a layer;
- hide/show a layer;
- reorder layers;
- update a layer's analyzer reference;
- select direct or SetImage by replacing the compiled plan.

Address controls by stable IDs, not array positions or singleton-specific paths. If
the matrix process is retained, send complete validated plan versions over the
low-rate channel; do not mutate live render objects piecemeal.

Acceptance criteria:

- Invalid operations leave the last valid plan active.
- Structural changes become visible only at frame boundaries.
- Existing settings continue to target the intended current layer.
- Control traffic cannot block the pixel/draw hot path indefinitely.

Stop before expanding the layer or analyzer catalog.

## Step 6 — Qualify and close this refactor

Run local tests, allocation/copy checks, current-scene regression tests, and the
approved Raspberry Pi qualification for the selected topology. Document both output
backends without attempting to determine an automatic crossover threshold.

Exit criteria:

- Analyzer reuse and layer ordering are proven by tests.
- The current scene remains correct in both output modes.
- Direct-path performance remains within the predeclared regression gate.
- Process topology is explicitly accepted or rejected with evidence.
- Remaining pacing, time-based animation, compositor, and multi-analyzer optimization
  ideas are recorded as future work rather than silently included.

## Deferred follow-on topics

- time-based animation;
- VSync/GIL binding changes and display-clock/deadline pacing;
- no-wait maximum-rate mode;
- automatic direct/SetImage selection;
- representative multi-layer crossover measurements;
- shared intermediate FFTs for different analyzer projections;
- alpha blending, masks, and general off-screen composition;
- richer layer/analyzer editors.
