# Known Issues and Traps

These observations were verified against the code when this guide was written. Re-check them after relevant changes.

## Current behavior mismatches

- Layered mode computes the FFT twice per frame. `main.py` always calls `get_fft_magnitudes()`, then calls `get_layer_magnitudes()` when layers are active; both perform `np.fft.rfft`. Nested architecture docs currently claim it is computed once.
- `load_settings()` only reconstructs `audio`, `frequency`, `overflow`, `peak`, `color`, `sensitivity`, `scaling`, and `smoothing`. JSON `shadow` and `layers` sections are silently left at defaults. `save_settings()` does serialize them, so save/load is not a full round trip.
- Named presets are not exposed by `main.py`. Four non-default preset functions also assign `zoom_min_freq` and `zoom_max_freq`, which are read-only properties; calling those presets raises `AttributeError`. They reference old theme names such as `fire` and `mono_green` that are not registered now.
- `bars_dual` is imported and registered, but its constructor reads `settings.dual`; no `DualSettings` or `Settings.dual` exists. The active app avoids this by hardcoding `bars`.
- User docs under `my_programs/fft_program/docs/` mention removed/nonexistent `--visualizer` and `--list-visualizers` flags and contain several older defaults/theme names. Use `main.py`, `matrix_app.py`, and `settings.py` for truth.
- The root launcher defaults to a 64x64 matrix and GPIO slowdown 2. Direct `main.py` defaults come from `MatrixApp`: 32 rows, 64 columns, and slowdown 1. Results therefore depend on entry point unless flags are explicit.

## Runtime edge cases

- The FFT service path is non-interactive, but most older standalone programs still
  assume a TTY or upstream `SampleBase` lifecycle. Only `system_info`, `fft`, and
  `sorts` are in the managed allowlist.
- The audio callback stores only one input channel because `InputStream` is opened with `channels=1`; setting `AudioSettings.channel` above zero will index past that array.
- Layer `bins` defaults to 64 independently of actual matrix width. A non-64-column panel can produce layer arrays whose width does not match the canvas.
- Pressing `r`/`R` rebuilds only the single-mode bin set. Layer frequency ranges are independent and remain unchanged.
- Runtime state is written into the source directory by default for compatibility.
  Services must set `RGBMATRIX_STATE_DIR`; the supplied systemd template does so.
- `main.py` catches initialization errors and exits, but runtime errors are printed and re-raised. The `finally` block still attempts state save and matrix clear.
- In single mode, the peak overlay receives `theme_cycler._get_current_theme()`, which creates a fresh theme object. For dynamic themes, peak colors may not share the renderer theme's exact animation state.

## Repository/deployment traps

- `.gitignore` excludes all of `rpi-rgb-led-matrix/`, and the parent repository tracks none of that directory. Local launcher results can include its samples, but a clean clone will not have them.
- The ignored local driver tree contains compiled `cpython-311-aarch64-linux-gnu.so` files. Their presence says nothing about compatibility with the current host or a different Pi Python version.
- Several standalone programs import `samplebase` by manipulating paths around the upstream samples layout. Moving them or installing only the parent repository can break imports.
- `docs_external/cheatsheet.txt` contains machine-specific addresses and paths.
  Confirm the current host instead of treating them as deployment constants.
- Portable provisioning remains deferred. There is no `provision.sh` yet.

## Web-service and network-control limits

- The `/` phone UI lists visualizations, reports current state, and quits the active
  process. Starting/switching visualizations and changing parameters still require
  the JSON API. `/setup` remains a separate Wi-Fi provisioning page.
- Parameters are launch-time settings. Calling `run` again stops and restarts the child;
  live FFT frame-boundary IPC is not implemented.
- The systemd unit is installed and tested on the current Pi, but assumes its specific
  checkout/interpreter paths and existing passwordless `sudo -n`; it is not yet a
  portable installer.
- POST actions use a bearer token over plain HTTP. Bind beyond loopback only on a
  trusted LAN, and do not expose port 8080 to the internet.
- Six-digit pairing improves phone usability but not transport security. A device
  capable of sniffing the trusted LAN can observe a pairing exchange or bearer token;
  HTTPS remains necessary before using this on an untrusted network.
- GET status/list endpoints are intentionally unauthenticated in the MVP.
- The fallback profile/controller and physical recovery watcher are installed on the
  current Pi, but an attended LAN-to-AP transition and real button press remain to be
  tested. Captive DNS is intentionally not implemented; use the fixed setup URL.
- The recovery button defaults to BCM GPIO 19 because it is free under
  `adafruit-hat`. Other matrix mappings or added hardware require a fresh pin-conflict
  review before enabling the watcher.
- Registry matrix options cover the common launcher controls, not every obscure
  upstream `SampleBase` flag.
- A missing `iMM-6C` audio device will still make the FFT child fail; the service does
  not substitute an input device.

## Testing gaps

Controller tests now cover schemas, allowlisting, argv construction, process switching,
termination escalation, status reconciliation, HTTP parsing/authentication, network
grace periods, DHCP-less candidate rollback, restart reconciliation, physical hold
detection and local-peer authorization, profile guards, and secret-safe failures with
fake processes/backends. There is still no CI, packaging metadata, or hardware test suite.
Syntax/unit success does not prove native imports, PortAudio, sudo, systemd, terminal
mode, or GPIO output. High-value future tests include settings round trips,
frequency-bin construction, scaler state transitions, runtime-state serialization,
launcher resolution, and renderer output against a fake canvas.
