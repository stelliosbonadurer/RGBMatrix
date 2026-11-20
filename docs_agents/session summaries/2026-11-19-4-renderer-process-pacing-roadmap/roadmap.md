# Deliberate implementation roadmap

Each step is a separate development cycle. Close its tests, records, and stop/go
decision before starting the next step.

## Step 0 — reconcile the baseline

### Work

- Identify the purpose and ownership of each current dirty file.
- Record local and Pi hashes for application files, binding source, and the installed
  extension.
- Record current Pi service and output-path state without replacing it.
- Decide whether the Step 6 metrics remain a reusable harness or are archived.

### Exit gate

The accepted application source, Pillow candidate, installed binding, and Pi state
each have one unambiguous identity. Stop before implementation.

## Step 1 — make temporal animation time-based

### Work

- Add elapsed time to the scaling and presentation update boundary.
- Convert bar rise/fall smoothing, scale attack/decay, peak hold/fall, and applicable
  shadow decay to elapsed-time semantics.
- Choose a reference interval that preserves the current nominal appearance.
- Keep output selection unchanged. Both output paths consume the same temporal state.

### Validation

- At the reference interval, deterministic trajectories match the current equations
  within an approved numerical tolerance.
- Equivalent elapsed time split across different frame intervals produces equivalent
  state within tolerance.
- Tests cover missed frames, long pauses, zero or negative intervals, and reset.
- An attended live-music check confirms the accepted look at nominal cadence.

### Exit gate

Animation speed is defined in real time rather than calls per frame, while the
nominal appearance remains acceptable. Stop before creating the process boundary.

## Step 2 — move rendering and matrix ownership into a child process

Implement this as three bounded substeps, closing each before the next.

### Step 2A — define and test the process contract

- The parent owns audio capture, FFT analysis, web/control integration, and process
  supervision.
- The child owns temporal presentation state, the selected output plan, `RGBMatrix`,
  canvas buffers, and `SwapOnVSync`.
- Publish only the newest fixed-size analysis result through a preallocated shared
  memory double buffer. Include schema/epoch, sequence, publish time, and the 64-bin
  numeric payload; do not build a queue or pickle per frame.
- Send low-rate configuration and lifecycle commands over a separate control channel.
- Define startup readiness, health, error propagation, clean shutdown, forced
  termination, and stale-frame behavior before hardware integration.

### Step 2A exit gate

Hardware-free tests prove latest-value replacement, coherent reads, restart/epoch
handling, bounded shutdown, and child-failure reporting.

### Step 2B — transfer matrix ownership with pacing unchanged

- Start with the existing fixed-sleep cadence so process isolation is the only major
  behavioral change.
- Run the direct path first. The child initializes and exclusively owns the binding;
  the parent never touches matrix objects.
- Forward only the runtime controls needed by the accepted live configuration and
  acknowledge application at a frame boundary.
- Preserve service readiness and cleanup behavior.

### Step 2B exit gate

The direct path renders correctly from the child, audio and analysis continue while
the child waits at VSync, controls remain coherent, and process failure is bounded.

### Step 2C — retain the Pillow option

- Instantiate `pillow-bulk` inside the same child when explicitly selected.
- Run existing capability-rejection and startup tests plus a hardware smoke test.
- Do not add multi-layer parity or crossover benchmarking.

### Step 2C exit gate

Both manual output selections start, render their currently supported scene, and
shut down correctly under the new process ownership. Direct remains the default.

## Step 3 — compare fixed-sleep and VSync-only pacing

### Fixed-sleep reference

Keep the current five-millisecond application sleep after each swap. This isolates
the process change and provides the pacing baseline.

### VSync-only candidate

Remove the application sleep and begin the next frame after `SwapOnVSync` returns.
The swap remains the display clock and prevents tearing. This is the maximum useful
rate exposed by the current safe double-buffered API; it is also what “as fast as we
can” means for this roadmap.

Only consider an explicit refresh cap if VSync-only pacing shows unstable intervals,
excess heat, or a visual reason to prefer a lower fixed cadence.

### Explicitly out of scope

- Writing the live canvas to avoid waiting, because refresh can observe partial
  updates.
- Busy-spinning or repeatedly composing frames that cannot be displayed.
- Adding another output stage merely to make the child loop nonblocking.

### Validation

- Compare fixed-sleep and VSync-only runs with the direct output path and the same
  input and configuration.
- Give Pillow a bounded smoke and non-regression run, not a parity campaign.
- Measure displayed FPS, fresh-content FPS, frame intervals, analysis publication,
  result age, CPU by process, temperature, throttling, and service health.
- Perform an attended visual comparison with representative live music.

### Exit gate

Choose fixed-sleep or VSync-only pacing from measured system behavior and the visual
check. Do not retain both as permanent product modes unless they provide a real
diagnostic or visual benefit.

## Step 4 — hardware qualification and promotion decision

- Use pinned source identities, settle periods, repeated order-balanced runs,
  service and thermal gates, and raw artifacts.
- Separate the process-isolation comparison from the pacing comparison so causality
  remains clear.
- Verify parent analysis health and child display health independently.
- Promote only the approved process and pacing combination; preserve a targeted
  rollback for application files and service configuration.
- Record the findings for the engineering case study and stop.

## Deferred beyond this roadmap

- Automatic output-path selection and a Pillow/direct crossover threshold.
- Equivalent multi-layer implementations and layer-count benchmarks.
- Content-triggered frame clocks.
- A new nonblocking matrix API or live-canvas output.
- The `SwapOnVSync` GIL patch unless measurements show useful Python concurrency
  remains inside the dedicated renderer process.
- External display hardware.
