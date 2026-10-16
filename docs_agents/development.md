# Development Workflow

## Environment assumptions

The target is a Raspberry Pi driving a HUB75 panel through the `rpi-rgb-led-matrix` native library, commonly with an Adafruit HAT/Bonnet. The active Python dependency file installs only:

```text
numpy>=1.20.0
sounddevice>=0.4.0
```

`sounddevice` also requires a functioning PortAudio/input-device setup. `rgbmatrix`
must be built and installed separately. Repeatable portable provisioning is deferred;
read [plans/portable-deployment.md](plans/portable-deployment.md) before resuming it.

Imports in the current app are script-oriented: `main.py` adds its directory to `sys.path`, and internal modules use top-level names such as `config`, `core`, and `themes`. Running the file directly works; importing it as a conventional package from the repository root may not.

## Useful commands

List everything the launcher currently recognizes:

```bash
python3 launch_rgbmatrix.py --list
```

Inspect the exact launch command without sudo or hardware access:

```bash
python3 launch_rgbmatrix.py \
  --program my_programs.fft_program.main \
  --no-sudo \
  --dry-run
```

Compile-check tracked Python code without importing hardware/audio modules:

```bash
python3 -m compileall -q launch_rgbmatrix.py matrix_controller tests my_programs/fft_program my_programs/*.py
```

Run the hardware-free contract, supervisor, and loopback HTTP tests:

```bash
python3 -m unittest discover -s tests -v
```

On a configured Pi, the normal launch is:

```bash
python3 launch_rgbmatrix.py --program my_programs.fft_program.main
```

Direct launch is also supported:

```bash
sudo -E python3 my_programs/fft_program/main.py \
  --led-rows=64 \
  --led-cols=64 \
  --led-gpio-mapping=adafruit-hat \
  --led-slowdown-gpio=2
```

`--list-themes` still imports NumPy and `sounddevice`, but exits before matrix
initialization. `--non-interactive` is supported for supervised FFT execution. There
is no supported `--visualizer`, `--list-visualizers`, or `--preset` flag in `main.py`.

For web-service/API commands and boot installation, use
[web-service-operations.md](web-service-operations.md).

## Validation expectations

There is a focused standard-library unit test suite for the new controller, but no CI
configuration and no hardware integration suite. Match validation to the change:

- Pure Python edits: run `compileall` and focused import/function checks where dependencies allow.
- Launcher edits: run `--list` plus representative `--dry-run` commands, including passthrough arguments.
- Registry/API edits: run all `tests/`; maintain strict types, unknown-field rejection,
  fixed argv construction, single-child ownership, and HTTP authorization tests.
- Settings edits: round-trip a temporary JSON file and verify nested dataclasses, tuple conversion, and omitted groups.
- Theme/visualizer edits: use a fake canvas with `Clear()` and `SetPixel()` and deterministic NumPy bar arrays; then test on the actual 64x64 panel.
- Audio changes: test bin coverage at the target device's native sample rate and confirm behavior with silence and clipped input.
- Hardware option changes: verify on the Pi; desktop compilation cannot validate GPIO timing, privilege dropping, panel mapping, or native ABI compatibility.

Do not claim the full application was exercised unless a compatible `rgbmatrix` binding, audio input device, interactive TTY, and panel were actually present.

## Change patterns

When adding a settings field:

1. Add it to the appropriate dataclass.
2. Extend `load_settings()` if the group is not already handled.
3. Decide whether it belongs in the smaller crash-resume state as well.
4. Update relevant runtime controls and nested user docs.

When adding a theme:

1. Implement the `BaseTheme` interface.
2. Register and export it.
3. Check standard, gradient, overflow, and peak colors.
4. If dynamic, consider runtime-state extraction/restoration in `main.py`.

When changing layers:

1. Keep audio layer configs, visualizer layer states, scaler lists, and `draw_order` index-aligned.
2. Remember the keyboard UI only selects/toggles layers 1–3.
3. Test invisible-layer `None` placeholders and changes between single and layered modes.

## Repository hygiene

- Preserve the distinction between `docs_agents/` (agent context and plans),
  `my_programs/fft_program/docs/` (feature/user docs), and `docs_external/` (hardware
  assets and historical host notes).
- Do not commit native build outputs or the ignored upstream driver checkout without an explicit repository-structure decision.
- Treat `runtime_state.json` as local mutable state. If work causes it to appear, do not commit it casually.
- Existing `.DS_Store` and bytecode artifacts are historical; avoid creating more tracked artifacts.
- Keep unrelated legacy scripts untouched unless the requested change targets them.
