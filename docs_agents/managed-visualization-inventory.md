# Managed Visualization Inventory

Last reviewed: 2026-11-10

## Performance decision

Current music-visualization FFT latency is acceptable. No baseline measurement or
benchmark gate is required for the current architecture and web-service work. Preserve
the existing data path and investigate performance only if hardware use reveals a
regression or a future constrained Pi cannot render acceptably.

This decision does not claim a measured latency number and does not prove another Pi
model will perform identically.

## Product allowlist

Only the following programs are exposed through `matrix_controller.Registry`:

| ID | Program | Service default | Required resources |
| --- | --- | --- | --- |
| `system_info` | `my_programs/system_info.py` | Active SSID, bare address, and boot pairing code | HUB75 matrix, `rgbmatrix`, controller/network status |
| `fft` | `my_programs/fft_program/main.py` | Ocean theme, overflow on, non-interactive | HUB75 matrix, `rgbmatrix`, NumPy, sounddevice, working audio input |
| `sorts` | `my_programs/visualize_sorts.py` | Continuous seeded random sorts | HUB75 matrix, `rgbmatrix`, upstream `samplebase.py` |
| `master_theorem` | `my_programs/dsa_visualizations/master_theorem.py` | Custom `2T(n/2) + Theta(n)` recurrence | HUB75 matrix, `rgbmatrix` |

The broader discovery results from `launch_rgbmatrix.py` are developer conveniences,
not reviewed product entries. Do not automatically copy discovered files into the web
registry.

## Shared matrix launch parameters

Managed hardware visualizations expose these CLI/API parameters:

- `led_rows`
- `led_cols`
- `led_chain`
- `led_parallel`
- `led_pwm_bits`
- `led_brightness`
- `led_gpio_mapping`
- `led_slowdown_gpio`
- `led_scan_mode`
- `led_pwm_lsb_nanoseconds`
- `led_show_refresh`
- `led_no_hardware_pulse`
- `led_rgb_sequence`
- `led_pixel_mapper`
- `led_row_addr_type`
- `led_multiplexing`
- `led_panel_type`
- `led_no_drop_privs`

The supervisor supplies all defaults explicitly. The checked-in
`config/matrix-controller.json` owns the service's normal 64x64, Adafruit HAT,
slowdown-2 profile and the remaining native-driver defaults. The web UI generates
controls for this contract; it does not maintain a second parameter list.

## FFT details

Managed application parameters:

- `theme`
- `gradient`
- `overflow`
- `non_interactive` (always enabled by the service default)

Direct CLI execution still supports `--settings` and `--list-themes`. `--settings` is
not web-exposed because accepting an arbitrary server-side path from HTTP would cross
the intended security boundary.

The FFT child now exposes these as typed live phone controls while preserving their
keyboard mappings:

- next/previous theme;
- zoom preset cycling;
- bars, peaks, shadows, full mode, and debug mode;
- layer selection, visibility, and draw order; and
- dynamic-theme speed, reseed, and freeze.

The same schema exposes per-layer theme/display/boost values and bounded noise,
weighting, sensitivity, smoothing, overflow, and peak tuning. Weight changes rebuild
derived frequency-bin weights at a frame boundary. Matrix flags and audio stream
geometry remain launch/restart settings.

Service-safety changes already present:

- `--non-interactive` avoids `termios`/TTY access;
- SIGTERM follows the Ctrl+C cleanup path;
- `RGBMATRIX_STATE_DIR` relocates mutable state outside the source tree; and
- CLI theme/gradient/overflow definitions share the controller schema.

The configured default audio device is still `iMM-6C`. The service reports a failed
child if that device or PortAudio setup is unavailable; it does not guess another
input.

## Sorting details

Direct launch with no application mode preserves the numbered terminal menu when stdin
is a TTY. A non-TTY launch defaults to continuous random mode rather than blocking on
`input()`.

Managed parameters:

- `mode`: `single`, `all`, or `random`;
- `sort`: `insertion`, `selection`, `bubble`, or `quick` for single mode;
- `delay`: seconds per rendered sorting frame;
- `seed`: deterministic random-array seed; and
- `max_runs`: completed sorts before exit, with zero meaning continuous in `all` or
  `random` mode.

The visualizer now retains the back buffer returned by every VSync swap and clears the
current buffer during controlled exit. SIGTERM exits even if the interactive random
menu would otherwise catch Ctrl+C and return to its prompt.

This script still depends on the ignored upstream samples checkout for `samplebase.py`
and the native binding. Removing that layout dependency is saved for the portable
runtime/deployment work.

## Master Theorem details

The DSA visualization models `T(n) = aT(n/b) + Theta(n^k)`. It calculates exact
logical levels independently of matrix hardware. The static matrix view starts with a
centered, one-pixel input line capped at a tunable 20–32 pixels. Each recursive layer
then shows its node count and per-node work geometrically: constant work is a point,
linear work is a line, and quadratic work is a square. Logical node counts that exceed
the available columns are rendered as density. Recurrence parameters remain managed
for eventual phone controls, but no parameter labels appear on the matrix.

This visualization uses a project-owned native adapter and does not depend on
`samplebase`, NumPy, sounddevice, or the FFT package. It is launch/stop-only in the
first release; changing the recurrence starts a replacement process.

## Supervisor lifecycle

The state machine reports `idle`, `starting`, `running`, `stopping`, or `failed`.
Lifecycle mutations are serialized under one lock. A valid new `run` request performs
a complete process-group stop/reap before starting its child; an invalid switch is
rejected without stopping the current display.

Finite programs that exit with status zero return to `idle`. Unexpected nonzero exit
returns `failed` with a bounded summary. Calling stop is idempotent and acknowledges a
failure back to `idle` while retaining the last exit fields for diagnosis.

## Deliberately deferred

- named live-control presets and dynamic add/remove/duplicate layer composition;
- automatic resume after reboot;
- registration of other project/upstream demos;
- portable installer and other Pi compatibility work;
- container evaluation; and
- TLS or a stronger local pairing transport.
