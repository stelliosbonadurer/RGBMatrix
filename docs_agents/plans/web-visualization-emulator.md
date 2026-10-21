# Web Visualization Emulator Plan

Status: proposed

## Goal

Provide a browser-based preview of the matrix visualizations for people who cannot
see the physical panel. The preview must execute the repository's Python
visualization code and display its frames in a browser; it must not reimplement an
effect in JavaScript or maintain a parallel browser-only renderer.

The first supported preview targets are the managed `fft` and `sorts`
visualizations. The connection-info screen is not a public demo: it contains a real
SSID, controller address, and pairing code and should remain excluded unless a
future sanitized preview is explicitly designed.

## Recommended architecture

Build a project-owned software matrix adapter and a small preview runtime. Existing
Python visualization code renders into a common canvas-shaped interface. On a Pi,
the adapter forwards completed frames to `rgbmatrix`; in preview mode, it retains an
RGB framebuffer and publishes completed frames to the web service.

```text
existing Python visualization logic
              |
      canvas/matrix interface
        /             \
native RGB adapter   software framebuffer
        |                    |
physical matrix       preview worker -> frame stream -> browser canvas
```

The browser is only a viewer and control surface. It scales each 64x64 frame with
nearest-neighbor rendering so the preview resembles individual matrix pixels. It
does not know how FFT bars, themes, or sorts are drawn.

Use the existing `matrix_controller` registry and parameter schema for discovery,
names, defaults, and validation. Add preview-specific metadata to the same
`VisualizationSpec` only where necessary, such as whether an entry is previewable
and which safe input source it supports. Do not introduce a second preview catalog.

Run each preview in an isolated worker process. This contains visualization crashes,
prevents one slow client from blocking the HTTP server, and provides a clear place
to enforce one active preview, frame-rate limits, and cleanup. The worker imports
the visualization's shared Python runner directly; it does not scrape source text,
shell out to a separate web implementation, or launch the hardware-only CLI entry
point.

The preview service can initially be a separately launched development/public-demo
process while sharing packages and static assets with `matrix_controller`. Once the
runtime and security boundary are proven, mount its routes in the existing control
service for Pi-local use. Keeping the worker boundary makes either deployment shape
possible without changing visualization code.

## Required refactoring boundary

The current code is close to reusable but its top-level applications still assemble
hardware, inputs, and render loops themselves.

- Define small `Canvas` and `MatrixOutput` protocols around the operations actually
  used today: dimensions, `Clear()`, `SetPixel()`, `CreateFrameCanvas()`, and
  `SwapOnVSync()`.
- Inject frame timing/sleep behavior. Sorting currently sleeps inside `draw_array()`,
  while a preview needs a cancellable clock and may need to pace or skip frames
  independently of the physical-panel delay.
- Implement a software framebuffer with the same observable operations. A swap must
  publish an immutable completed frame and return a writable back buffer, matching
  the native double-buffer contract.
- Refactor `fft_program/main.py` so its reusable runner accepts an audio source,
  output adapter, settings, and shutdown signal. Keep the current CLI as a thin
  adapter that constructs `sounddevice` input and the native matrix output.
- Refactor `visualize_sorts.py` so the automatic runner can accept an output adapter
  without importing upstream `samplebase`. Keep its interactive CLI behavior in the
  hardware entry point. Continue importing and executing the algorithms in
  `sorts.py` and the drawing function in `visualize_sorts.py`.
- Keep theme and renderer imports unchanged. In particular, previewed FFT frames
  must still come from `BarsUnifiedVisualizer.draw()` and the existing theme
  classes.

This is a focused implementation of the project-owned lifecycle and fake-canvas
work already called for by `runtime-modernization.md`; it should satisfy that part
of the modernization plan rather than create another abstraction beside it.

## Preview inputs

Sorting previews can be deterministic immediately by using the existing seed,
mode, sort, and delay parameters.

FFT preview cannot depend on the server's microphone for a public demonstration.
Create an `AudioSource` boundary and support these sources in order:

1. A checked-in or explicitly configured short, redistributable audio fixture for a
   deterministic public demo.
2. A synthetic spectrum/sample generator for tests and an always-available fallback.
3. Optional browser microphone input only as a later feature. This requires user
   permission, HTTPS in most browsers, and a bounded audio-upload protocol; it must
   feed the existing Python FFT pipeline rather than calculate FFT data in JavaScript.

Do not silently use a microphone attached to the server. A preview must clearly
identify its input source so viewers understand whether it reacts to their audio.

## Delivery phases

### 1. Establish the shared rendering contract

- Add typed protocols for canvas, matrix output, audio input, and cancellation.
- Implement and unit-test the software double buffer, including pixel bounds,
  clearing, frame immutability, and swap semantics.
- Add golden or checksum-based frame tests that run existing sorting and FFT
  renderers against the software canvas.
- Refactor one finite sorting run first. Verify that the hardware CLI still builds
  the same argument parser and follows the same draw path.

Exit condition: one seeded sort produces frames without `rgbmatrix`, and both the
hardware command and preview test call the same drawing function.

### 2. Add the preview worker and transport

- Add a process-owned preview session with explicit start, stop, status, and
  unexpected-exit states. Never run an unbounded visualization loop in an HTTP
  request thread.
- Use a bounded latest-frame queue so a slow browser drops stale frames instead of
  applying backpressure to rendering or accumulating memory.
- Encode lossless frames efficiently. For 64x64 displays, start with binary RGB or
  PNG frames over a WebSocket; benchmark complexity before adding delta encoding.
- Cap output frame rate and concurrent sessions. Stop orphaned workers after client
  disconnect plus a short grace period.
- Treat visualization IDs and parameters as the existing allowlisted typed schema;
  never accept module paths, filenames, or arbitrary Python arguments from clients.

Exit condition: a test client can start a seeded sorting preview, receive ordered
frames, reconnect without spawning a duplicate worker, and stop it cleanly.

### 3. Build the browser demo

- Add a preview action to each previewable visualization in the generated catalog.
- Render frames into a fixed 64x64 canvas, enlarged with nearest-neighbor scaling and
  a subtle pixel grid. Respect reduced-motion preferences by offering pause and
  single-frame controls.
- Generate parameter controls from the existing public registry schema, shared with
  the phone-control UI work. Clearly separate `Preview` from `Run on matrix` so a
  remote viewer cannot mistake one for the other.
- Show loading, active, disconnected, crashed, and unsupported-input states.
- Keep the preview page usable as a standalone public demo route even when physical
  matrix controls are disabled.

Exit condition: a browser can select a visualization, adjust supported parameters,
preview it, pause/stop it, and receive useful errors without requiring matrix
hardware.

### 4. Bring FFT into the shared runner

- Extract the FFT application's initialization and per-frame loop behind the shared
  output and audio-source interfaces without duplicating scaling, theme, layer, or
  renderer code.
- Feed the deterministic audio fixture through the existing `AudioProcessor`,
  `ScalingProcessor`, and `BarsUnifiedVisualizer` pipeline.
- Preserve native `sounddevice` operation, keyboard controls, runtime-state saving,
  SIGTERM cleanup, and accepted current FFT latency on the Pi.
- Avoid importing `sounddevice` or `rgbmatrix` in the web process; only the worker
  and selected adapters should load optional platform dependencies.

Exit condition: the same fixed audio input produces equivalent framebuffer output
through the headless test and preview worker, while the Pi CLI remains operational.

### 5. Integrate and harden

- Decide deployment mode with configuration: Pi-local integrated routes, standalone
  public demo service, or both. Reuse the implementation in every mode.
- Default the public preview to a laptop/build host or dedicated server, not the Pi
  driving the panel. Preview CPU and memory use must not disturb physical FFT
  rendering; Pi-local preview is an explicit, concurrency-limited mode.
- For a public service, disable hardware-control and network-administration routes,
  enforce origin and request-size checks, rate-limit session creation, and sandbox
  workers with CPU, memory, and lifetime limits.
- Add health/status observability that distinguishes HTTP health from worker health.
- Document local startup, public deployment, supported visualizations, fixture
  licensing, and how a new visualization becomes previewable.

Exit condition: the documented deployment can survive refreshes, disconnects,
invalid parameters, worker crashes, and multiple viewers without affecting the
physical matrix supervisor.

## Testing strategy

- Unit tests for the software canvas and frame queue must not require native matrix
  bindings, audio hardware, a browser, or a Raspberry Pi.
- Renderer tests should use fixed seeds and fixed audio blocks. Prefer compact frame
  hashes plus a few explicit pixel assertions over large checked-in screenshots.
- Contract tests must run the same registry parameter cases against hardware-command
  construction and preview-session construction.
- Browser tests should verify canvas sizing, nearest-neighbor scaling, generated
  controls, stop/reconnect behavior, and error states.
- Pi regression checks remain focused: the direct CLI and supervised launch still
  initialize the native adapter, render, stop, clear the panel, and preserve current
  acceptable FFT latency.

## Risks and decisions to preserve

- A browser cannot directly run the current native Python stack. "Read the files
  directly" therefore means importing the same renderer/algorithm modules in a
  server-side Python worker, not sending their source to JavaScript or Pyodide.
- Monkey-patching `rgbmatrix` or `samplebase` imports would make a quick prototype,
  but it would preserve the hardware coupling and be fragile. Use explicit adapters
  and thin CLI entry points instead.
- Frame streaming adds a public resource-exhaustion surface. Bounded queues, process
  isolation, concurrency limits, timeouts, and an allowlisted registry are baseline
  requirements, not later polish.
- The public demo must not expose the boot pairing code, network controls, local
  paths, environment values, or arbitrary file/audio inputs.
- Exact timing will differ between the panel and a browser because of scheduling and
  network jitter. Pixel output and state transitions should be shared; wall-clock
  fidelity is best effort.

## Acceptance criteria

- `fft` and `sorts` previews execute repository-owned Python visualization modules;
  there is no parallel JavaScript implementation of either effect.
- Hardware and preview modes share rendering, themes, algorithms, parameter
  validation, and as much loop orchestration as practical.
- The preview runs on a development laptop without `rgbmatrix` or physical audio
  hardware.
- A slow or disconnected viewer cannot block rendering or leak an unlimited queue or
  worker process.
- The existing CLI and exclusive physical-matrix supervisor continue to work.
- Adding a future managed visualization requires one renderer/runner and one registry
  entry, not separate matrix and browser versions.

## Explicit non-goals for the first release

- Perfect simulation of panel brightness, scan artifacts, camera exposure, or
  electrical timing.
- Running arbitrary files discovered by `launch_rgbmatrix.py` in a web session.
- Browser-side FFT or renderer rewrites.
- Multi-user collaborative control of one preview session.
- Public exposure of Pi administration, Wi-Fi provisioning, or the connection-info
  visualization.
