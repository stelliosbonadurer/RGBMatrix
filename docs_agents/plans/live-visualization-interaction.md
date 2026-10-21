# Live Visualization Interaction Plan

Status: proposed next implementation

## Goal

Let an authenticated phone display the active visualization's real current state and
change supported settings while it is running. Begin with the FFT music visualizer,
whose themes, layers, draw order, peaks, gradients, overflow, zoom, and dynamic-theme
controls are already useful interactively from the keyboard.

The design must remain useful as visualizations gain new controls, users save custom
presets, layers become more configurable, and the browser preview is added. The
existing command-line launch and keyboard workflow must continue to work.

Current FFT latency is acceptable. This work does not require a performance baseline
or language rewrite; it must simply avoid introducing visible regressions, audio
dropouts, or unbounded work in the render loop.

## Core architecture decision

Create one typed runtime-control layer inside each interactive visualization. The
keyboard and web service become adapters to that layer; neither interface owns a
second copy of visualization state.

```text
keyboard input --------------------+
                                    v
web UI -> HTTP API -> private IPC -> runtime controller -> renderer/audio/settings
                                    |
                                    +-> versioned current-state snapshot
```

Do not send synthetic keypresses to the child process. The current keyboard cases in
`my_programs/fft_program/main.py` directly mutate several objects and would quickly
drift from a separate web implementation. Instead, extract those mutations into an
`FftRuntimeController`; map keys and web commands to the same named operations.

The visualization process remains authoritative because it owns the renderer, audio
pipeline, theme instances, adaptive scalers, and derived FFT-bin state. The web
service may cache connectivity metadata, but it must always obtain displayed values
from a child snapshot rather than assuming that launch defaults are still current.

Apply commands on the render thread between frames. A socket reader may parse and
queue requests, but it must not mutate visualizer or audio objects directly. This
keeps state transitions ordered and prevents a request thread from changing a layer
while it is being rendered.

## Separate launch configuration from live state

The existing `ParameterSpec` schema describes process-launch arguments. Keep it
intact and add a distinct runtime-control schema. A control should declare at least:

- stable ID/path, label, description, and group;
- value type, choices or numeric bounds, and step size;
- scope (`global`, `layer`, or visualization-specific target);
- apply mode (`live`, `rebuild`, or `restart`);
- availability conditions, such as layered or dynamic-theme mode;
- whether the value may be included in a future saved preset.

The UI should generate controls from this schema and state snapshot. Avoid writing a
one-off HTML form for every new FFT option.

Apply modes mean:

- `live`: safe to change at a frame boundary;
- `rebuild`: can remain in-process but requires a controlled rebuild of derived
  state such as frequency bins;
- `restart`: must be supplied to a new launch, not changed in the active process.

Matrix dimensions, GPIO mapping/slowdown, PWM options, Python/runtime selection,
audio device, block size, and FFT size are restart-only for the first release.
Frequency zoom already rebuilds bins and is the first supported `rebuild` example.
A request mixing live and restart-only changes should be rejected atomically unless
a later API explicitly requests a controlled restart.

## Runtime state and command contract

Every launch receives a unique `run_id`. Every successful state mutation increments
a monotonically increasing `revision`. A state response should have this shape:

```json
{
  "protocol_version": 1,
  "run_id": "one-launch-identifier",
  "visualization_id": "fft",
  "revision": 42,
  "connected": true,
  "state": {},
  "controls": []
}
```

Use stable layer IDs in the public contract rather than array positions. Positions
can change when layers are reordered and future customization may add, remove, or
duplicate layers. An initial ID may be a validated stable name such as `bass`, but it
must not silently change with draw order.

Prefer idempotent assignments such as “set bass visibility to false” over toggles or
“next” commands. Reserve actions for genuinely event-like operations such as reseed
dynamic theme or move a layer. Support an atomic list of changes so related settings
do not appear in partially updated combinations.

Every mutation includes its `run_id` and optional `expected_revision`. This prevents
an old browser tab from controlling a replacement process and allows two phones to
detect stale edits rather than silently overwriting each other. A successful response
returns the resulting snapshot; errors use bounded machine-readable codes such as
`stale_run`, `revision_conflict`, `invalid_value`, `unavailable`, and `busy`.

## Process transport

Use a private Unix-domain socket per visualization run. The supervisor creates it in
a private runtime directory, gives the child the endpoint plus a one-run capability,
and removes it after the process is reaped. Use mode `0600`; never expose this socket
over TCP or let the browser connect to it directly.

Newline-delimited, size-bounded JSON is sufficient. Messages include protocol
version, request ID, run ID, operation, and payload. Bound message size, pending
request count, command-queue depth, and response timeout. Reject excess work as
`busy` so rapid UI input cannot starve rendering.

The child listener validates and queues a request. The render loop drains a bounded
number of commands at the next frame boundary, applies each transaction, and returns
the new revision/snapshot. A broken control channel must be reported to the browser
but must not terminate an otherwise healthy visualization.

The HTTP service remains the public security boundary and applies the existing
pairing-token rules. It must validate paths against code-owned schemas and never
accept Python expressions, arbitrary object attributes, module/file paths, shell
arguments, or serialized objects.

## HTTP and browser contract

Add authenticated endpoints along these lines:

- `GET /api/v1/runtime` returns support, connection, run identity, current state,
  and the live-control schema for the active visualization.
- `PATCH /api/v1/runtime` applies one atomic set of typed value changes.
- `POST /api/v1/runtime/actions` performs allowlisted semantic actions.

For the MVP, poll current runtime state every one or two seconds and refresh
immediately after an acknowledged command. This also reflects local keyboard changes
without requiring WebSockets. Add server-sent events or WebSockets later only if
polling is visibly inadequate or the preview frame transport makes an existing push
channel economical.

Show a `Live controls` panel only when the active process advertises support. The
panel should:

- show values from the runtime snapshot, not launch-form defaults;
- group global, layer, theme, and diagnostic controls;
- clearly label or disable controls that currently require rebuild/restart;
- hide or disable controls whose availability predicate is false;
- retain the last acknowledged value while a change is pending;
- recover by refetching after timeout, conflict, reconnect, or a changed `run_id`;
- remain usable on a narrow phone screen and with native form controls.

Do not persist every slider movement to the main controller config. Current state is
ephemeral and tied to a run. Continue using the existing bounded runtime-state save
for crash/clean-exit recovery. Add explicit versioned `Save preset` and `Load preset`
operations later so users can choose which experiments become durable.

## FFT control inventory and release scope

The first useful release should expose the current keyboard behavior through the
shared controller:

- theme selection;
- gradient, overflow, bars, peaks, shadows, full, and debug modes;
- peak color mode;
- zoom preset;
- layered mode and active-layer selection;
- per-layer visibility, theme, bars, gradient, overflow, peaks, and boost;
- stable layer draw order;
- dynamic-theme speed, freeze, and reseed.

The existing keyboard mappings (`t`/`T`, `g`, `o`, `r`/`R`, `b`, `f`, `d`, `l`,
number and shifted-number keys, `<`/`>`, `s`, `p`, `P`, `[`/`]`, `c`, and `x`)
must call the same semantic controller methods. Keyboard-only execution must not
require the web service or an IPC endpoint.

After that foundation is stable, add validated live numeric tuning for noise floor,
sensitivity/weighting, rise/fall smoothing, overflow multiplier, peak timing, layer
boost, and frequency ranges. Settings that require reinitializing the audio stream
remain restart-only until a dedicated restart transaction exists.

## Implementation phases

### 1. Extract the in-process control model

1. Define generic runtime control/state/command types without importing HTTP code.
2. Add `FftRuntimeController.snapshot()` and transactional command application.
3. Move every keyboard mutation behind semantic controller methods.
4. Centralize theme replacement, layer/audio/scaler alignment, shadow allocation,
   zoom-bin rebuilding, and runtime-state serialization in tested helpers.
5. Fix restore gaps uncovered by the extraction: saved shadow/layer state currently
   is not fully restored, and theme cycler/runtime theme instances can diverge.

Exit condition: the keyboard behaves as before, commands and keys produce the same
state, snapshots contain only JSON-safe values, and no web process is needed.

### 2. Add private child IPC and supervisor support

1. Create the per-run endpoint and capability during supervisor launch.
2. Add the bounded child listener and frame-boundary command queue.
3. Add supervisor `runtime_snapshot` and `runtime_command` methods with short,
   explicit timeouts.
4. Cleanly handle process replacement, exit during a request, stale run IDs, malformed
   replies, service restart, and endpoint cleanup.

Exit condition: tests can launch a fake interactive worker, read its state, apply an
ordered command, reject a stale command, and survive a control-channel failure.

### 3. Expose the authenticated HTTP API

1. Serialize the runtime schema and snapshot without exposing raw argv, paths,
   environment values, or the socket endpoint.
2. Validate all mutations at the HTTP boundary and again in the child.
3. Return consistent responses for no process, unsupported visualization, stale run,
   conflict, timeout, invalid input, and child exit.
4. Add request-size, batch-size, queue, and rate bounds.

Exit condition: paired API clients can read and change FFT state while unauthorized,
invalid, stale, and excessive requests are rejected predictably.

### 4. Build the phone live-control MVP

1. Add an active-visualization panel above or beside the launch catalog.
2. Render controls from the runtime schema and group layer controls by stable ID.
3. Implement polling, acknowledged updates, disabled/pending states, conflict
   recovery, and changed-run recovery.
4. Cover all controls already available from the keyboard before adding new DSP
   customization.

Exit condition: a phone can launch FFT, see its true current settings, change every
existing interactive setting without restarting it, observe keyboard-originated
changes, and stop or replace the visualization normally.

### 5. Add extensible customization

1. Add high-value numeric DSP controls only with explicit bounds and focused state
   rebuild helpers.
2. Add versioned named presets with explicit save/load/delete actions and migration.
3. Support adding, duplicating, removing, and reordering layers using stable IDs and
   atomic validation.
4. Let other visualizations opt into the same minimal runtime interface; unsupported
   entries remain launch/stop-only.
5. Reuse the same schema/controller for the software-matrix preview so preview and
   hardware control do not become parallel feature sets.

Exit condition: a new visualization or setting can join the live UI by declaring a
typed control and implementing the shared runtime interface, without editing generic
HTTP routing or creating bespoke browser state logic.

## Testing strategy

- Unit-test every existing key mapping against the equivalent semantic command.
- Validate themes, booleans, numeric bounds, stable layer IDs, draw order, and
  availability conditions; rejected atomic batches must make no partial change.
- Verify zoom rebuilds bins exactly once, shadows allocate/release safely, and layer
  mode keeps renderer, audio, and scaler collections aligned.
- Verify dynamic-theme commands affect the intended active theme and return updated
  speed/frozen/hue state.
- Ensure snapshots contain no NumPy values or other unserializable objects and that
  revisions increment only after successful mutations.
- Test queue ordering, queue saturation, socket/message limits, timeouts, stale run
  IDs, revision conflicts, child exit, supervisor restart, and endpoint cleanup.
- Exercise HTTP authentication and ensure runtime details never expose secrets or
  private process configuration.
- Run browser tests for schema-generated fields, phone layout, polling, keyboard
  reconciliation, conflicts, disconnection, and replacement launches.
- On the Pi, smoke-test audio continuity, rendering, keyboard input, phone input,
  stop/clear behavior, and runtime-state recovery. Current acceptable latency is the
  baseline; investigate only visible or logged regressions.

## Acceptance criteria

- The active FFT settings shown on a phone match the child process's current state.
- Every existing FFT keyboard control goes through the same command/state layer used
  by web control and still works without the web service.
- Live-safe settings change without restarting FFT; restart-only settings cannot be
  mutated accidentally.
- Commands apply only to the intended run and stale concurrent changes are detected.
- Invalid or partial commands cannot corrupt layer/audio/scaler alignment.
- A slow, disconnected, or malicious client cannot block the render loop or submit
  arbitrary code, paths, process arguments, or unbounded work.
- Current state, launch config, and explicitly saved presets remain separate concepts.
- Future visualizations and the web preview can reuse the protocol without copying
  FFT-specific HTTP or JavaScript logic.

## Explicit non-goals for the first release

- Replacing Python or rewriting FFT/rendering in the browser.
- Real-time frame streaming; that belongs to the web visualization emulator plan.
- Live mutation of matrix hardware flags or audio stream geometry.
- Automatically saving every experimental adjustment as a boot default.
- Multi-user collaborative editing beyond revision-conflict detection.
