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
| `graph_traversal` | `my_programs/dsa_visualizations/graph_traversal.py` | Seeded, connected BFS on an acyclic planar graph | HUB75 matrix, `rgbmatrix` |

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
centered, one-pixel input line whose width is the literal configured `n` (up to 32).
Each node at a recursive layer draws `round(n_level^k)` pixels in columns bounded by
`round(n_level)` high: constant work is a point, linear work is a vertical line,
quadratic work is a square, and fractional powers fill a partial final column. Logical
node counts that exceed the available columns are rendered as density. Recurrence
parameters remain managed for eventual phone controls, but no parameter labels appear
on the matrix.

This visualization uses a project-owned native adapter and does not depend on
`samplebase`, NumPy, sounddevice, or the FFT package. It is launch/stop-only in the
first release; changing the recurrence starts a replacement process.

## Graph traversal details

The graph traversal visualization supports BFS, DFS, and Dijkstra. It places up to 32
nodes on the 64x64 panel, builds short straight-line edges without crossings, and
replays the same graph for a reproducible seed. Connected undirected and weakly
connected directed graphs begin with a Euclidean spanning tree. Strong directed graphs
begin with an oriented cycle. Additional short non-crossing edges are added only when
cycles are enabled. Directed edges use a configurable number of orange pixels
immediately before the destination node instead of arrowheads; the default is two.

`e=0` selects a dependent default: an undirected graph uses `n-1` with cycles
disabled and `ceil(1.5n)` with cycles enabled; a directed graph uses `ceil(1.5n)` and
orients acyclic edges according to a seeded topological order. A connected undirected
acyclic graph requires exactly `n-1` edges. Strictly planar graphs have the general
upper bound `3n-6`; strong directed connectivity requires cycles and at least `n`
edges. The application reports invalid combinations before matrix initialization.
Directed graphs default to `ensure_path=true`, which reserves and orients a viable
start-to-target path even when no overall connectivity guarantee is selected. A
strongly connected graph satisfies this automatically. The phone form exposes this
setting only for directed graphs and includes its one-edge minimum in the displayed
edge constraints.

Frames can advance by processed node or examined edge. The untouched opening frame and
completed frame are each held for `holding_period * frame_duration`, then the same
traversal replays. DFS colors every edge in a discovered cycle. Dijkstra maps unvisited
weights 1 through 9 from white to yellow, colors examined edges separately, and
highlights the final shortest path.
During Dijkstra, explored edges and the current predecessor tree use separate colors;
when relaxation replaces a predecessor, the old edge returns to the explored color.
Edges are rendered in state layers so the predecessor tree appears above overlapping
explored edges, with the currently examined edge and final path above the tree.
Edges leading to already-settled nodes are not examined again because they cannot
improve a distance; this avoids a predecessor edge flashing as current a second time.
`component_mode=weak` colors ordinary nodes by weak/undirected component, while
`component_mode=strong` colors strongly connected components in directed graphs.
Start, target, and current-node colors retain precedence over component colors. The
editable component palette contains a distinct color for every possible node.
Ordinary nodes are one pixel while the start and target default to 2x2; both sizes are
configurable. Nodes retain their normal, start, or target color after processing; only
the currently processed node changes color. Rendering colors are editable in
`my_programs/dsa_visualizations/graph_traversal_config.json`.

The phone launch form conditionally shows undirected or directed connectivity controls
and recalculates the explicit `e` range plus the automatic value before launch. The
registry repeats those dependent checks server-side before replacing an active process.

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
