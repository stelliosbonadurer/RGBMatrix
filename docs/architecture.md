# Architecture

## Startup

`main.py` creates `MatrixApp`, adds application flags (`--theme`, `--list-themes`, `--gradient`, and `--overflow`), and parses the matrix flags owned by `MatrixApp`. It loads defaults or a JSON file, optionally offers to restore `runtime_state.json`, then initializes the native RGB matrix.

After the matrix exists, the program constructs:

- one `AudioProcessor`, with one output bin per matrix column;
- one single-mode `ScalingProcessor`;
- the hardcoded `bars` (`BarsUnifiedVisualizer`) renderer;
- a theme and `ThemeCycler`;
- layer audio bins, visual state, and one scaler per configured layer on demand.

Both the audio stream and terminal keyboard mode are context-managed. The matrix is cleared and runtime state is saved during normal teardown.

## Per-frame path

```text
sounddevice callback
    -> copies the newest mono sample block
main loop
    -> AudioProcessor: Hann window -> zero-padded rFFT -> logarithmic bins
    -> ScalingProcessor: noise-adjusted bars -> adaptive scale -> rise/fall smoothing -> peaks
    -> BarsUnifiedVisualizer: pixels + optional separate peak overlay
    -> RGBMatrix.SwapOnVSync()
```

The callback and render loop communicate through `AudioProcessor.latest_samples`. There is no explicit lock; the callback replaces the array with a copy, and the main thread consumes the latest available block.

The configured audio block is 512 samples, while the FFT is zero-padded to 8192. Zero-padding improves the sampled frequency grid but does not add source information. Frequency bins are logarithmic and weighted from `low_freq_weight` to `high_freq_weight` with a power curve. Empty low-frequency bins are reported at setup.

## Scaling

`ScalingProcessor.process()` returns normalized bars, stateful smoothed bars, and falling peak positions. The default mode tracks a rolling RMS of each frame's largest raw bar and moves `current_scale` toward RMS times headroom. Fixed, rolling-max, and instant-normalization modes also exist.

`sensitivity_scalar` multiplies the divisor. Despite the name/comments elsewhere, a larger value therefore makes displayed bars shorter, not taller.

## Rendering modes

`BarsUnifiedVisualizer` owns the live rendering toggles:

- standard versus overflow bars;
- uniform bar color versus per-pixel gradient;
- bars, shadows, and peaks;
- full-screen FFT crossfade and static debug gradient;
- single mode versus independently configured frequency layers.

In layered mode, `LayerConfig` supplies each layer's frequency range, bin count, theme, boost, and initial flags. The default settings define two 64-bin layers: bass at 80–1950 Hz and mid at 2000–4000 Hz. Each visible layer has an independent scaler. The renderer follows `draw_order`, so later layers overwrite pixels from earlier layers.

Important implementation detail: layered mode currently calls both `get_fft_magnitudes()` and `get_layer_magnitudes()` every frame. Each method computes its own rFFT, so the FFT is performed twice, even though older architecture notes say it is shared once.

## Themes and registries

Themes implement `BaseTheme.get_color()` and may override overflow or peak coloring. Dynamic themes also expose speed, reseed, and freeze methods. Registry lookup creates a new theme instance; this matters because dynamic state is instance-specific.

Adding a theme requires defining the class in `themes/gradients.py` (or another module), importing it in `themes/registry.py`, and including it in `_register_builtin_themes()`. Adding a visualizer similarly requires both registry and package imports, but `main.py` must also stop hardcoding `visualizer_name = "bars"` before a new visualizer is selectable.

## State and configuration

There are three kinds of state:

1. Dataclass defaults in `config/settings.py`.
2. Optional startup JSON supplied with `--settings`.
3. `runtime_state.json`, written atomically about every two seconds and on exit, then offered for interactive restore next launch.

Runtime state captures selected display flags, zoom index, themes, layer visibility/order, and dynamic-theme parameters. It does not serialize the full `Settings` object. In non-interactive stdin, prior state is never restored because the confirmation prompt is skipped.

The terminal controls are defined in the large key-dispatch block in `main.py`; the startup banner is the quickest current inventory. If changing a key or mode, update the banner, handler, persisted state if relevant, and the user docs together.

## Hardware boundary

`MatrixApp.initialize_matrix()` is the only active module that imports `rgbmatrix`. This keeps most math and color code hardware-independent, but the application has no mock-canvas or headless mode. A canvas only needs `Clear()` and `SetPixel()` for renderer-focused tests, so a small fake is practical when adding tests.

The native binding is not a PyPI dependency. It must be built for the target Raspberry Pi/Python combination. The local `.so` files under the ignored upstream checkout are aarch64 binaries and are not usable on a typical development Mac.
