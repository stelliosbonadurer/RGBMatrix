# Runtime Architecture Plan

Status: initial runtime-contract milestone implemented; broader migration deferred

## Question

Is Python code built around the upstream demo programs the right long-term runtime?

## Recommendation

Keep Python for application orchestration, configuration, audio processing, and most
visualization logic for now. The timing-sensitive panel driver is already implemented
by the native `rpi-rgb-led-matrix` library, and NumPy performs the FFT in compiled
code. Rewriting everything before profiling would add cost without identifying the
actual bottleneck.

Do stop treating the upstream `bindings/python/samples` layout and `samplebase.py` as
the application's architecture. Vendor/pin the upstream dependency explicitly and
own a small project runtime API. Use the demos as examples and migration inputs, not
as the place production applications must live.

Move individual hot paths to native code only when target-device profiling shows a
measurable miss that algorithmic fixes cannot solve. The current duplicate FFT in
layered mode is an example to fix before considering a language rewrite.

## Dependencies and boundaries

- [Web control](web-control-service.md) consumes this plan's registry and schema but
  stays out of the render process.
- [Portable deployment](portable-deployment.md) packages the chosen Python/native
  boundary when that deferred work resumes.
- [Network fallback](network-fallback-ap.md) is operationally separate from the
  rendering runtime.

## Target boundaries

```text
CLI adapter ---------\
web supervisor -------> visualization definition + typed parameters
                      -> lifecycle (setup, frame/tick, action, teardown)
                      -> shared matrix/audio services
                      -> rgbmatrix Python binding -> native driver
```

The web service remains outside the render process. A visualization remains runnable
directly from its CLI adapter.

## Common visualization contract

Define a narrow interface rather than a large framework. A visualization should be
able to declare:

- identity and metadata;
- typed launch/runtime parameters;
- required resources (matrix, audio input, fonts/assets);
- `setup`, repeated `render`/`tick`, optional command handling, and `teardown`;
- whether it is finite, continuous, or internally sequenced;
- health/status fields safe to expose; and
- capabilities such as live parameter changes.

The runtime should own common concerns:

- matrix configuration and exactly one canvas/output owner;
- signal handling, clear-on-exit, and meaningful exit codes;
- monotonic timing/frame pacing and optional metrics;
- configuration loading/validation and state paths;
- structured, bounded logging; and
- non-interactive operation.

Do not force every legacy upstream demo to be rewritten before the architecture can
ship. Provide a subprocess/legacy adapter, then convert only the allowlisted programs
that the product intends to support.

## Packaging direction

- Make project modules importable without inserting ad hoc sample directories into
  `sys.path`.
- Use normal package-relative imports and a project-owned entry point.
- Pin `rpi-rgb-led-matrix` separately; do not fork its entire sample tree into the
  application unless a deliberate vendoring decision is made.
- Keep hardware-independent parameter, FFT/scaling, and drawing logic testable with
  fake audio and canvas objects.
- Keep CLI wrappers thin so CLI and web paths call the same validation and runtime.

## Alternatives considered

### Continue directly on `SampleBase`

Useful for quick experiments, but not the long-term default. It couples project
programs to an untracked upstream directory, offers no shared parameter metadata,
and assumes an interactive standalone program lifecycle.

### Rewrite all visualizations in C++

Not justified initially. It increases build complexity and slows iteration. Consider
native extensions or a C++ implementation only for a measured hot loop, a visualizer
that cannot meet its deadline, or a feature unavailable through the binding.

### Run one permanent Python process and load every visualization as a plugin

This could make switching fast, but a leak/crash/native-state bug can affect all
visualizations and legacy adaptation is harder. Begin with supervised subprocesses.
Revisit an in-process plugin host only if measured switch time or memory makes it
necessary.

### Keep each script completely independent

Preserves simplicity but duplicates parsing, service behavior, privilege handling,
and parameter metadata. It also makes full CLI/web parity expensive. Retain direct
entry points while sharing the runtime contract underneath.

## Migration phases

### Phase 1: specify and inventory — implemented

1. Record that existing FFT latency is acceptable; no benchmark gate is required.
2. Inventory program lifecycles, CLI options, prompts, keyboard actions, and external
   assets.
3. Write the minimal runtime and visualization protocols plus parameter schema.
4. Define ownership rules for matrix, audio, persisted state, and shutdown.

### Phase 2: extract shared infrastructure — MVP implemented

1. Create project-owned matrix configuration/initialization compatible with needed
   upstream flags.
2. Add a headless fake canvas and lifecycle tests.
3. Add a thin CLI adapter that consumes the shared parameter schema.
4. Move persistent state out of module/source directories.

### Phase 3: convert representative programs — service adapters implemented

1. Convert the FFT application, preserving its direct CLI and terminal controls.
2. Convert `visualize_sorts.py`, adding non-interactive sort/mode parameters while
   preserving its interactive menu.
3. Verify behavior on hardware; current FFT latency remains the accepted reference.
4. Use the two conversions to simplify the contract before migrating more scripts.

### Phase 4: optimize only if needed — deferred

1. Remove the layered-mode duplicate FFT and profile again.
2. Inspect frame pacing, rendering loops, allocation, audio callbacks, and native
   calls using target-device measurements.
3. Optimize algorithms/data flow first; introduce a native extension only if an
   observed regression or constrained target shows the need and benefit.

## Acceptance criteria

- Converted visualizations run both directly from CLI and under the supervisor using
  the same parameter validation/defaults.
- Project code no longer requires placement inside the upstream samples directory.
- A fake canvas can test setup/render/teardown on a development machine.
- `SIGTERM`, `Ctrl+C`, normal completion, and startup failure release/clear resources
  predictably.
- The converted FFT meets or improves its established frame/latency baseline.
- Adding a simple visualization does not require editing the web service internals.

## Decision triggers for leaving Python

Reconsider a native implementation only when all are true:

1. A named supported Pi misses a quantified frame/latency target.
2. Profiling attributes the miss to project Python code, not GPIO timing, audio I/O,
   duplicated work, thermal throttling, or service load.
3. A small native prototype demonstrates a meaningful improvement.
4. The improvement outweighs cross-architecture build and maintenance costs.

## Open decisions

- Package name and exact visualization/runtime protocols.
- Whether the native driver is fetched during provisioning, a submodule, or a pinned
  build dependency.
- Which upstream demos graduate into supported project visualizations.
- Required switch time between supervised processes.
