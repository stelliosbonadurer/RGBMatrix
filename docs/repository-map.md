# Repository Map

## Active entry points

| Path | Role | Notes |
| --- | --- | --- |
| `launch_rgbmatrix.py` | Workspace launcher | Discovers runnable Python files below `my_programs/` and the local upstream sample directory. Runs the selected file with its own directory as the working directory. |
| `my_programs/fft_program/main.py` | Current FFT visualizer | The active application and only code path that assembles the modular audio, scaling, themes, layers, rendering, keyboard controls, and state persistence. |
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

## Other top-level areas

- `my_programs/*.py`: standalone matrix demos and earlier versions. `fft_matrix.py` is an older monolith; `fft_terminal.py` is a terminal FFT experiment. Most matrix demos depend on upstream `samplebase.py`.
- `viz2016version/`: historical experiments retained for reference. Treat as legacy and expect old APIs/dependencies.
- `gameoflife_patterns/jslife/`: about 700 tracked `.lif` data files plus upstream readmes. This is data, not application code.
- `documentation/`: hardware reference assets, a host-specific cheat sheet, and plans organized by status. The provisioning plan is explicitly not implemented.
- `rpi-rgb-led-matrix/`: a present-but-ignored upstream checkout/build tree. It contains native sources, samples, compiled aarch64 Python extensions, and the `rgbmatrix` binding in this workspace, but no files from it are tracked by the parent repository. A fresh clone of this repository will not contain it.

## Generated/local state

- `my_programs/fft_program/runtime_state.json` and its temporary sibling are written by the visualizer next to `main.py`. They are not explicitly ignored at present.
- `__pycache__/`, bytecode, virtual environments, `.DS_Store`, and the whole upstream driver directory are ignored.
- Hostnames, IP addresses, and user-specific paths in `documentation/cheatsheet.txt` are historical local notes, not portable configuration.
