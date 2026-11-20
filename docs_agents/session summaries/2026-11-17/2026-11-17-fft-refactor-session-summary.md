# FFT refactor session summary — 2026-11-17

> **Status: failed experiment; retained as a legacy reference.** The implementation
> described here passed its functional checks but failed the end-to-end performance
> comparison on 2026-11-18. It was removed from the working runtime and the repository
> implementation was returned to commit `f23a34a`. Do not treat this document as the
> current architecture or as a design to reimplement unchanged. See
> [`2026-11-18-performance-comparison/README.md`](2026-11-18-performance-comparison/README.md)
> and
> [`pipeline-comparison-and-next-architecture.md`](2026-11-18-performance-comparison/pipeline-comparison-and-next-architecture.md)
> for the measured failure and replacement direction.

## Outcome

This section records the outcome as it appeared at the end of November 17. That
conclusion was superseded by the controlled legacy comparison on November 18.

The FFT visualizer was refactored from a single legacy loop into a versioned,
runtime-configurable music-visualization pipeline. The implementation was deployed
to the Raspberry Pi at `10.8.184.98`, exercised with the real iMM-6C USB capture
device and 64×64 HUB75 panel, and left running under the managed controller.

The controller now owns durable music configurations, materializes a validated boot
snapshot for each FFT launch, and exposes a mobile-oriented editor. The visualization
can accept a complete candidate configuration at a frame boundary without restarting
the matrix process. The legacy FFT implementation remains available as an explicit
rollout fallback through `RGBMATRIX_FFT_LEGACY=1`.

## Runtime architecture

The following is a record of the discarded implementation. Its component names are
useful for tracing the experiment and backup artifacts; those modules are no longer
present in the rolled-back working tree.

The new implementation is under `my_programs/fft_program/music_runtime/` and divides
the pipeline into explicit responsibilities:

- `models.py` defines the versioned configuration, analyzer, visual-layer, output,
  spectrum-frame, projection, and telemetry contracts. Validation is strict and
  rejects malformed or out-of-budget candidates before activation.
- `audio.py` provides the USB audio source, bounded PCM ring, shared analyzer
  registry, NumPy FFT backend, latest-frame cache, and sample-clock scheduler.
  Identical analyzer definitions share one transform worker.
- `projection.py` compiles logarithmic or linear visual-bin projections, response
  curves, coefficient coverage, and undersampling metadata.
- `runtime.py` compiles a complete candidate graph and swaps it atomically. Failed
  validation, compilation, or activation preserves the previous graph.
- `control.py` implements revision-checked complete-configuration applies and
  rejects stale run IDs or revisions.
- `bar_renderer.py` turns presentation-ready bars and peaks into sparse vertical
  spans. It supports solid bars, gradients, overflow, peaks, and shadows.
- `compositor.py` performs top-to-bottom opaque composition with separate RGB and
  coverage buffers. Hidden and fully occluded layers are culled without corrupting
  temporal-state policy.
- `output.py` uses one reusable RGB buffer, a Pillow image, one bulk `SetImage`, and
  one `SwapOnVSync` per content frame. A per-pixel compatibility path remains
  available through `RGBMATRIX_FFT_PIXEL_OUTPUT=1`.

`refactored_main.py` integrates the audio, analyzer, graph, renderer, compositor,
output, telemetry, and runtime-control clocks. Rendering consumes only the latest
published analyzer frames; it does not schedule transforms itself.

## Controller, persistence, and editor

`matrix_controller/music_library.py` adds a controller-owned, revisioned library for:

- analyzer favorites;
- visual-layer templates;
- complete music configurations; and
- an explicitly selected boot-default configuration.

The store validates and atomically commits bounded JSON documents. A protected
baseline is always available, while user configurations can be created, updated,
selected as the boot default, and deleted when they are not protected or boot-active.

The web service now exposes music-library and live-configuration routes, including:

- `GET/POST /api/v1/music/configurations`;
- `GET/PUT/DELETE /api/v1/music/configurations/{id}`;
- `POST /api/v1/music/configurations/{id}/make-boot-default`;
- corresponding analyzer-favorite and layer-template routes; and
- `PUT /api/v1/music/runtime/configuration` for one complete revisioned live apply.

The editor at `/config/fft` works from a complete draft configuration, reports
projection and compute-budget information, and sends one atomic candidate rather
than a sequence of partially applied field mutations. The controller page and editor
use the existing authenticated browser session and same-origin mutation checks.

Before launching FFT, the supervisor materializes the selected boot configuration as
`/var/lib/rgb-matrix-controller/music-boot-configuration.json` and supplies its path
through `RGBMATRIX_MUSIC_CONFIGURATION_PATH`. The FFT child reads this snapshot only;
it does not write durable application state.

## Legacy migration and compatibility

The migration layer maps current settings into schema version 1, including analyzer
window/FFT settings, frequency range, response gain curve, normalization, smoothing,
theme, layer priority, overflow, peaks, shadows, brightness, and content FPS. It also
records fields that were mapped or deliberately retired.

`my_programs/fft_program/main.py` defaults to the refactored runtime. Existing matrix
CLI parameters remain owned by `MatrixApp`, and the old implementation is still
reachable only through the explicit legacy environment flag.

## Pi deployment discoveries and fixes

Two failures appeared only on the real RGB-matrix privilege boundary:

1. The first deployment imported `refactored_main.py` after `RGBMatrix`
   initialization. The native library had already dropped privileges, and the
   resulting account could not traverse the checkout below the user's home
   directory. The refactored module is now imported before matrix initialization.
2. The boot configuration was initially opened after the same privilege drop. The
   controller-owned snapshot is mode `0600`, so the child silently fell back to
   migrated legacy settings. The JSON payload is now bounded and preloaded before
   matrix initialization, then schema-validated after the audio device establishes
   its sample rate.

`tests/test_fft_entrypoint.py` guards both ordering requirements.

## Qualified Pi configuration

The previous legacy setting used a 5 ms sleep, which migrated to a nominal 200 FPS
content target. The Pi and panel actually sustained roughly 40–50 FPS under that
saturated workload, causing render drops, control starvation, audio overruns, and
missed analyzer hops. A temporary 60 FPS target remained above the measured hardware
frame cost.

The durable boot profile is now named `Pi 64x64 Qualified 30 FPS` and uses:

- 30 content frames per second;
- one enabled analyzer;
- a 2048-sample Hann window and 2048-sample hop;
- an 8192-point magnitude FFT with DC removal;
- 64 logarithmic visual bins from 100 Hz through 6300 Hz;
- the ocean theme with overflow enabled; and
- a brightness limit of 1.0.

The 2048-sample hop provides a 46.44 ms analysis deadline at 44.1 kHz. This is safely
above the panel's blocking frame-publication path while retaining about 21.5 new
audio-analysis frames per second. The panel's native PWM refresh remains independent
of the 30 FPS content rate.

## Historical deployment state

At the November 17 handoff, before the performance failure was established:

- `rgb-matrix-web@stelliosbonadurer.service` was active;
- FFT run 7 was running with its runtime-control channel connected;
- the durable boot snapshot contained the qualified 30 FPS/2048-sample settings;
- the live configuration was at revision 2 and restored to 100% brightness;
- `/healthz`, the main controller, the FFT editor, and its static assets responded;
- no post-launch traceback, runtime error, rejected boot configuration, or unexpected
  child exit appeared in the journal; and
- the unrelated dirty Pi file
  `my_programs/dsa_visualizations/graph_traversal_config.json` was preserved.

The browser editor was then available at
`http://10.8.184.98:8080/config/fft`. This is historical deployment information, not
a statement about the Pi's current state.

## Recovery artifacts

Before deployment, a recoverable repository/state backup was created at:

```text
/home/stelliosbonadurer/rgb_Matrix.pre-fft-refactor-20261117T2120Z.tar.gz
```

Its SHA-256 digest is:

```text
a420b1b392921f2df5c67dc9dd05de2db976ca148d9f9af6a0151c7c97c479f0
```

The Pi benchmark report is retained at:

```text
/home/stelliosbonadurer/fft-refactor-benchmark-20261117T2120Z.json
```
