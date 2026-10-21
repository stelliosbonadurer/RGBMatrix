# Repository Map

## Active entry points

| Path | Role | Notes |
| --- | --- | --- |
| `launch_rgbmatrix.py` | Workspace launcher | Discovers runnable Python files below `my_programs/` and the local upstream sample directory. Runs the selected file with its own directory as the working directory. |
| `python3 -m matrix_controller.web_service` | Managed HTTP API | Creates/reuses the boot code, launches connection info, and controls the explicit visualization allowlist. Does not import matrix or audio libraries. |
| `python3 -m matrix_controller.boot_code` | Pairing code reader | Prints the same six-digit code displayed on the matrix for the current Pi boot. |
| `python3 -m matrix_network.service` | Privileged network helper | Applies known-network/fallback-AP policy through NetworkManager and exposes only a local Unix socket. Normally run by systemd as root. |
| `python3 -m matrix_network.recovery_input` | Physical recovery watcher | Uses `gpiomon` and a five-second GPIO 19 hold to request setup AP through a root-peer-only Unix operation. Normally run by systemd as root. |
| `my_programs/fft_program/main.py` | Current FFT visualizer | The active application and only code path that assembles the modular audio, scaling, themes, layers, rendering, keyboard controls, and state persistence. |
| `my_programs/visualize_sorts.py` | Sorting visualizer | Preserves its terminal menu and also supports non-interactive `single`, `all`, and `random` service modes. |
| `my_programs/system_info.py` | Boot connection display | Cycles the active SSID, controller URL, and boot pairing code; automatically yields through the shared supervisor. |
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
├── controller_config.py strict versioned launch-default loader
├── schema.py          immutable parameter/visualization contracts and validation
├── definitions.py     shared matrix, FFT, and sorting parameters
├── registry.py        explicit product allowlist, including connection info
├── boot_code.py       boot-ID-scoped six-digit code and terminal reader
├── pairing.py         boot-code exchange and temporary control sessions
├── supervisor.py      one-child state machine and process-group teardown
├── static/index.html  responsive visualization control page
├── static/control.*   generated launch forms, pairing, status, and browser behavior
├── static/setup.html  minimal phone Wi-Fi provisioning page
└── web_service.py     authenticated standard-library HTTP API and network proxy
```

`config/matrix-controller.json` supplies the managed service's interpreter,
privilege mode, boot visualization, hardware defaults, and visualization defaults.
It is validated before the HTTP listener or matrix child starts.

`matrix_network/` contains the separate privileged state machine, NetworkManager
backend, D-Bus secret writer, Unix-socket protocol, unprivileged client, and isolated
GPIO recovery watcher. It must not absorb visualization launching or expose a network
listener itself.

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
