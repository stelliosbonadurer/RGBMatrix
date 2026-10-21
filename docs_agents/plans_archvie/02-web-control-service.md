# 02 — Web Control Service Plan

Status: archived — supervised API, boot service, and network proxy milestone completed

The visualization phone UI milestone is archived in
[04-phone-visualization-controls.md](04-phone-visualization-controls.md). Live-control
IPC remains deferred.

## Goal

Provide a mobile-first web interface on the Pi that can:

- list supported visualizations;
- launch exactly one visualization at a time;
- stop, restart, and report the active visualization;
- expose every declared visualization parameter available through its command-line
  interface; and
- start automatically at boot, ready before anyone opens the page.

Existing CLI commands must remain first-class and must not require the web service.
This plan covers all managed visualizations, not only the FFT application.

## Dependencies and boundaries

- [Runtime architecture](01-runtime-architecture.md) supplies the shared registry,
  parameter schema, and service-safe lifecycle.
- [Portable deployment](../plans/portable-deployment.md) supplies the supported environment
  and installs the service unit.
- [Network fallback](03-network-fallback-ap.md) makes this service reachable on either a
  normal LAN or setup AP, but does not own visualization processes.

## Current constraints

- `launch_rgbmatrix.py` discovers scripts heuristically and launches arbitrary paths.
  That is convenient for a terminal but is not a safe public API.
- The scripts do not share one application interface. The FFT program has its own
  parser and runtime keyboard controls; several other programs inherit the upstream
  `samplebase.py`; `visualize_sorts.py` waits for interactive stdin.
- Each process expects exclusive ownership of the panel. Two visualizations running
  together would contend for GPIO/matrix output.
- Some FFT options exist only as settings or keyboard actions, not as CLI flags.
- The FFT loop currently assumes an interactive terminal, so it is not yet safe to
  run unchanged as a background service.

## Proposed architecture

Use a control-plane/data-plane split:

```text
phone browser
    -> HTTP API + static mobile UI (unprivileged control service)
    -> validated supervisor commands
    -> one visualization subprocess
    -> rgbmatrix native binding -> HUB75 panel
```

The server should supervise subprocesses, not import visualization modules into its
own process. This isolates crashes and native state, supports legacy scripts during
migration, permits clean stop/restart behavior, and keeps web request work out of the
render loop.

Use `systemd` to start and restart the control service at boot. Let the service own
the single active child process. Do not create one independently enabled systemd unit
per visualization; that makes exclusivity and switching harder to guarantee.

## Visualization manifest and parameter contract

Replace web-facing auto-discovery with an explicit allowlisted registry. Each entry
should define:

- stable ID, display name, description, and implementation path;
- execution adapter (`native-app`, `samplebase`, or temporary legacy adapter);
- interpreter/working directory requirements;
- matrix compatibility and required peripherals such as an audio input;
- typed parameter definitions, defaults, ranges/enums, and CLI spelling;
- whether a parameter applies at launch, at a frame boundary, or requires restart;
- supported actions such as stop, next sort, next theme, or load preset; and
- a health/readiness definition.

One schema must drive CLI validation, API validation, and UI generation. Avoid a
separate hand-maintained list of web-only parameters. Matrix-wide arguments such as
rows, columns, mapping, brightness, and slowdown belong in device configuration;
visualization-specific arguments belong in the selected program schema.

“All command-line functionality” means all safe, declared options for a registered
visualization. It must not mean accepting a raw command, path, environment variable,
or arbitrary argument list from HTTP.

## CLI compatibility

- Preserve direct execution for every converted program.
- Keep `launch_rgbmatrix.py` as the human-friendly terminal launcher, but teach it to
  consume the same registry when that exists.
- Make non-interactive behavior explicit (`--non-interactive` or an equivalent
  runtime mode) rather than detecting the web service.
- Convert stdin menus into declared launch parameters/actions. For example,
  `visualize_sorts.py` needs flags for a specific sort, run-all, or random continuous
  mode while retaining its current menu when launched interactively.
- Return meaningful exit codes and handle `SIGTERM` by clearing/releasing the matrix.

For live FFT tuning, use a small versioned local IPC contract with validated updates
applied at frame boundaries. A Unix-domain socket is the preferred first prototype;
a restart with new CLI/settings is acceptable for launch-only values. Do not send FFT
bins or frames to the browser.

## Minimal API

Exact paths can change, but the contract needs equivalents of:

- `GET /api/v1/visualizations`
- `GET /api/v1/status`
- `POST /api/v1/run` with a visualization ID and validated parameter object
- `POST /api/v1/stop`
- `PATCH /api/v1/runtime-parameters` for supported live changes
- `GET`/`PUT /api/v1/device-config` for persistent matrix defaults

Commands need serialized execution, idempotent stop behavior, timeouts, and a small
state machine: `idle`, `starting`, `running`, `stopping`, `failed`. Status should
include the selected ID, effective parameters, start time, PID, and a bounded error
summary—not secrets or full unbounded logs.

## Security and privilege boundary

- Bind to LAN/setup interfaces only by default; never expose this service to the
  internet automatically.
- Run the web server without root privileges.
- Do not pass user input through a shell. Construct an argument vector from the
  allowlisted schema.
- Give the child only the privileges/device access required by `rpi-rgb-led-matrix`.
  Determine the precise systemd policy during hardware testing because matrix timing
  and privilege dropping are native-library concerns.
- Protect state-changing endpoints with a device-local credential or pairing token,
  even on the LAN. Use a different credential for fallback-AP provisioning.
- Redact Wi-Fi credentials and sensitive environment data from logs.

## Implementation phases

The completed MVP boundary is documented in
[web-service-operations.md](../web-service-operations.md). It includes the registry,
typed validation, exclusive subprocess supervisor, HTTP API, tests, and a systemd
template. A parameter change restarts the active visualization in this milestone.

### Phase 1: inventory and contract — implemented

1. Enumerate supported visualizations rather than exposing every discoverable demo.
2. Capture each current CLI option, keyboard action, stdin prompt, dependency, and
   peripheral requirement.
3. Define the versioned manifest and parameter/action schema.
4. Decide which live actions are MVP and which initially restart the child.

### Phase 2: service-safe applications — initial allowlist implemented

1. Make the FFT program run without a TTY or restore prompt.
2. Add signal-aware teardown to converted programs.
3. Add non-interactive arguments to `visualize_sorts.py`.
4. Introduce a shared program/adapter contract from the runtime architecture plan.
5. Add fake-canvas tests for lifecycle and parsing without Pi hardware.

### Phase 3: supervisor and API — implemented

1. Implement the allowlisted registry loader and strict validation.
2. Implement exclusive subprocess ownership and process-group termination.
3. Persist desired device defaults outside the source tree with atomic writes.
4. Add bounded logs, status, failure recovery, and API contract tests.

### Phase 4: phone UI and full boot integration — partially implemented/deferred

1. Build a responsive UI generated from parameter metadata where practical. Deferred.
2. Add visualization selection, parameter editing, start/stop, status, and errors.
3. The supplied systemd template is installed and enabled on the current Pi. Portable
   installation is deferred; fallback-AP integration and its focused setup page are
   implemented.
4. Advertise a stable local hostname with mDNS, while retaining an IP fallback. Deferred.

### Phase 5: live control and presets — deferred

1. Add low-rate/coalesced runtime updates for applications that support them.
2. Store presets with schema and visualization versions.
3. Benchmark frame time while idle, under API load, and during parameter changes.

## Acceptance criteria

- A phone can select, start, stop, and switch every registered visualization.
- Starting B reliably stops A; no two processes own the matrix at once.
- Every declared CLI option has the same validation/default through the API, and
  direct CLI launching still works with the service stopped.
- Restarting or crashing the web service does not orphan multiple visualization
  processes; the documented recovery policy is verified.
- Malformed IDs/parameters cannot execute arbitrary commands or paths.
- The service becomes reachable after boot on both a normal LAN and the fallback AP.
- FFT retains the currently accepted latency; measure only if hardware testing reveals
  a regression.

## Open decisions

- Web framework after measuring startup/memory on the lowest supported Pi.
- Whether a visualization resumes automatically after reboot, and how repeated crash
  loops disable that resume.
- Unix socket versus another local IPC mechanism for live controls.
- Authentication/pairing UX for trusted home networks.
- Initial allowlist: project programs only, or selected upstream demos too.
