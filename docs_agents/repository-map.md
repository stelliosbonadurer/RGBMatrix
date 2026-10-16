# Repository Map

## Active entry points

| Path | Role | Notes |
| --- | --- | --- |
| `launch_rgbmatrix.py` | Workspace launcher | Discovers runnable Python files below `my_programs/` and the local upstream sample directory. Runs the selected file with its own directory as the working directory. |
| `python3 -m matrix_controller.web_service` | Managed HTTP API | Lists, validates, starts, switches, stops, and reports the explicit `fft`/`sorts` allowlist. Does not import matrix or audio libraries. |
| `my_programs/fft_program/main.py` | Current FFT visualizer | The active application and only code path that assembles the modular audio, scaling, themes, layers, rendering, keyboard controls, and state persistence. |
| `my_programs/visualize_sorts.py` | Sorting visualizer | Preserves its terminal menu and also supports non-interactive `single`, `all`, and `random` service modes. |
| `my_programs/fft_program/fft_sandbox.py` | Older monolithic FFT experiment | Still discoverable by the launcher; do not mistake it for the modular application. |

The normal launcher invocation is:

```bash
python3 launch_rgbmatrix.py --program my_programs.fft_program.main
```

The launcher defaults to `sudo -E` and injects:

```text
--led-rows=64 --led-cols=64 --led-gpio-mapping=adafruit-hat --led-slowdown-gpio=2
```

Use `--no-sudo`, `--no-led-defaults`, or trailing matrix/application arguments when appropriate. Python selection is, in order: `--python`, `RGBMATRIX_PYTHON`, one of two historical `fftdemo/.venv` paths, then the interpreter running the launcher.

## Current FFT package

```text
my_programs/fft_program/
├── main.py                 orchestration, keyboard controls, runtime state
├── requirements.txt        NumPy and sounddevice only
├── config/
│   ├── settings.py         settings dataclasses and partial JSON loader
│   └── presets.py          named presets (currently not wired to the CLI)
├── core/
│   ├── audio.py            capture, FFT, log bins, frequency weighting
│   ├── scaling.py          normalization, smoothing, peak tracking
│   └── matrix_app.py       matrix CLI options and native binding setup
├── themes/
│   ├── base.py             theme interface and peak colors
│   ├── gradients.py        seven built-in themes
│   └── registry.py         name-to-theme registry
├── visualizers/
│   ├── base.py             shared theme/shadow state
│   ├── bars_unified.py     active renderer: single and layered modes
│   ├── peaks.py            single-mode peak overlay
│   ├── bars_dual.py        registered but currently unusable; see known issues
│   └── registry.py         visualizer registry
├── utils/colors.py         currently unused color helpers
└── docs/                   user/developer notes, some stale
```

Built-in theme names are `autumn`, `dynamic`, `dynamic_lateral_gradient`, `forest`, `ocean`, `rainbow`, and `warm`. `main.py` hardcodes the `bars` visualizer even though the registry also exposes `bars_dual`.

## Managed control package

```text
matrix_controller/
├── schema.py          immutable parameter/visualization contracts and validation
├── definitions.py     shared matrix, FFT, and sorting parameters
├── registry.py        explicit two-program product allowlist
├── supervisor.py      one-child state machine and process-group teardown
└── web_service.py     authenticated standard-library HTTP API
```

The product registry is intentionally separate from `launch_rgbmatrix.py` discovery.
Discovery is convenient for trusted terminal use; it must not become a web execution
surface. Tests under `tests/` inject fake processes and do not initialize hardware.

## Other top-level areas

- `my_programs/*.py`: standalone matrix demos and earlier versions. `fft_matrix.py` is an older monolith; `fft_terminal.py` is a terminal FFT experiment. Most matrix demos depend on upstream `samplebase.py`.
- `viz2016version/`: historical experiments retained for reference. Treat as legacy and expect old APIs/dependencies.
- `gameoflife_patterns/jslife/`: about 700 tracked `.lif` data files plus upstream readmes. This is data, not application code.
- `docs_agents/`: agent context, operations, implementation status, and future plans.
- `docs_external/`: hardware reference assets and host-specific historical notes.
- `deploy/systemd/`: checked-in boot-service templates; installation is still manual.
- `rpi-rgb-led-matrix/`: a present-but-ignored upstream checkout/build tree. It contains native sources, samples, compiled aarch64 Python extensions, and the `rgbmatrix` binding in this workspace, but no files from it are tracked by the parent repository. A fresh clone of this repository will not contain it.

## Generated/local state

- `runtime_state.json` defaults to the FFT source directory for direct compatibility.
  Set `RGBMATRIX_STATE_DIR` for services; the supplied unit uses
  `/var/lib/rgb-matrix-controller`.
- `__pycache__/`, bytecode, virtual environments, `.DS_Store`, and the whole upstream driver directory are ignored.
- Hostnames, IP addresses, and user-specific paths in `docs_external/cheatsheet.txt`
  are historical local notes, not portable configuration.
