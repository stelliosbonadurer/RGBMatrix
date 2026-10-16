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

- `KeyboardHandler` assumes stdin is a TTY. The program is not designed for service/non-interactive execution even though prior-state prompting itself checks `isatty()`.
- The audio callback stores only one input channel because `InputStream` is opened with `channels=1`; setting `AudioSettings.channel` above zero will index past that array.
- Layer `bins` defaults to 64 independently of actual matrix width. A non-64-column panel can produce layer arrays whose width does not match the canvas.
- Pressing `r`/`R` rebuilds only the single-mode bin set. Layer frequency ranges are independent and remain unchanged.
- Runtime state is written into the source directory every two seconds. This may fail on a read-only deployment, and the file is not covered explicitly by `.gitignore`.
- `main.py` catches initialization errors and exits, but runtime errors are printed and re-raised. The `finally` block still attempts state save and matrix clear.
- In single mode, the peak overlay receives `theme_cycler._get_current_theme()`, which creates a fresh theme object. For dynamic themes, peak colors may not share the renderer theme's exact animation state.

## Repository/deployment traps

- `.gitignore` excludes all of `rpi-rgb-led-matrix/`, and the parent repository tracks none of that directory. Local launcher results can include its samples, but a clean clone will not have them.
- The ignored local driver tree contains compiled `cpython-311-aarch64-linux-gnu.so` files. Their presence says nothing about compatibility with the current host or a different Pi Python version.
- Several standalone programs import `samplebase` by manipulating paths around the upstream samples layout. Moving them or installing only the parent repository can break imports.
- `documentation/cheatsheet.txt` contains machine-specific addresses and paths. Confirm the current host instead of treating them as deployment constants.
- The provisioning document records Python 3.12/Cython/distutils problems but is explicitly a TODO. There is no `provision.sh` yet.

## Testing gaps

There are no tracked tests, mocks, lint configuration, packaging metadata, or CI workflow for the project application. Syntax compilation succeeds without proving that native imports, PortAudio, terminal mode, or GPIO output work. High-value future tests would cover settings round trips, frequency-bin construction, scaler state transitions, runtime-state serialization, launcher resolution, and renderer output against a fake canvas.
