# Managed Visualization Inventory

Last reviewed: 2026-10-16

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
| `fft` | `my_programs/fft_program/main.py` | Ocean theme, overflow on, non-interactive | HUB75 matrix, `rgbmatrix`, NumPy, sounddevice, working audio input |
| `sorts` | `my_programs/visualize_sorts.py` | Continuous seeded random sorts | HUB75 matrix, `rgbmatrix`, upstream `samplebase.py` |

The broader discovery results from `launch_rgbmatrix.py` are developer conveniences,
not reviewed product entries. Do not automatically copy discovered files into the web
registry.

## Shared matrix launch parameters

Both managed definitions currently expose these CLI/API parameters:

- `led_rows`
- `led_cols`
- `led_chain`
- `led_parallel`
- `led_pwm_bits`
- `led_brightness`
- `led_gpio_mapping`
- `led_slowdown_gpio`

The supervisor supplies all defaults explicitly, using the existing developer
launcher's normal 64x64, Adafruit HAT, slowdown-2 profile. Less common upstream flags
remain available during direct CLI use but are not yet web-managed.

## FFT details

Managed application parameters:

- `theme`
- `gradient`
- `overflow`
- `non_interactive` (always enabled by the service default)

Direct CLI execution still supports `--settings` and `--list-themes`. `--settings` is
not web-exposed because accepting an arbitrary server-side path from HTTP would cross
the intended security boundary.

The following features are still runtime keyboard controls, not MVP web parameters:

- next/previous theme;
- zoom preset cycling;
- bars, peaks, shadows, full mode, and debug mode;
- layer selection, visibility, and draw order; and
- dynamic-theme speed, reseed, and freeze.

The future live-control phase should promote these into typed named actions or values
and apply them at frame boundaries. Do not model them as synthetic keypresses: that
would couple the API to terminal implementation details and make validation/state
reporting unreliable.

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

## Supervisor lifecycle

The state machine reports `idle`, `starting`, `running`, `stopping`, or `failed`.
Lifecycle mutations are serialized under one lock. A valid new `run` request performs
a complete process-group stop/reap before starting its child; an invalid switch is
rejected without stopping the current display.

Finite programs that exit with status zero return to `idle`. Unexpected nonzero exit
returns `failed` with a bounded summary. Calling stop is idempotent and acknowledges a
failure back to `idle` while retaining the last exit fields for diagnosis.

## Deliberately deferred

- polished phone UI;
- live parameter/action IPC;
- presets and automatic resume after reboot;
- registration of other project/upstream demos;
- portable installer and other Pi compatibility work;
- fallback access point and captive provisioning;
- container evaluation; and
- TLS or a stronger local pairing transport.

