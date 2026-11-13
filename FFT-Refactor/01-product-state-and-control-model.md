# Product, State, and Control Model

## Product goals

The music page should behave like an editor, not a bank of remote-control switches.
A user should be able to experiment with a complete prospective state, understand
how analyzers and visual layers are connected, and then submit that state once.

The page must support:

- Creating, duplicating, renaming, editing, enabling, and deleting FFT analyzers.
- Creating, duplicating, renaming, editing, hiding, deleting, and reordering visual
  layers.
- Connecting each FFT-driven visual layer to any FFT analyzer.
- Reusing one analyzer from many visual layers.
- Editing the layer's X frequency range and Y gain response graphically or
  numerically.
- Saving individual analyzer favorites and visual-layer templates.
- Saving and loading complete configurations.
- Keeping draft, applied, saved, and boot-default state visibly distinct.
- Showing configuration conflicts and performance constraints before changing the
  matrix.

## Main page structure

### 1. Runtime header

- Running/stopped/error state.
- Active audio source and source health.
- Applied configuration name and revision.
- Analysis/render health summary.
- Start, stop, and reconnect actions.

### 2. Configuration toolbar

- Load configuration.
- Save.
- Save As.
- Duplicate.
- Export/import later.
- Explicit **Make boot default** action.
- Dirty-state label.
- Sticky **Apply** and **Discard** buttons on narrow screens.

### 3. FFT analyzers

An expandable list of analyzer cells. A collapsed cell shows:

- Name and enabled state.
- Favorite name if based on one.
- Window/hop/FFT summary.
- Effective update rate.
- Connected visual-layer names/count.
- Estimated CPU cost and latest measured processing percentile.
- Validation or health badges.

Expanded content exposes advanced FFT fields. Frequency ranges and gain curves do
not belong here unless they change the transform itself; those remain visual-layer
settings so layers can share an analyzer.

### 4. Visual layer stack

Cards appear in visual top-to-bottom order, matching composition priority. Each card
contains:

- Drag handle plus Move Up/Move Down buttons.
- Name, type, visible state, and connected analyzer.
- Collapsed visual summary.
- Expandable response graph and precise controls.
- Save as layer template, duplicate, and delete.

The first release offers one layer type, `FFT Bars`, but the type is explicit from
the start so note, onset, waveform, or particle layers can be added without changing
the configuration envelope.

### 5. Diagnostics

An expandable section shows analyzer timing, missed hops, audio overruns, frame time,
layers culled, content FPS, matrix refresh information, process CPU, and temperature.
It is not mixed into the ordinary creative controls.

## Frequency/gain response editor

The recommended visual-layer graph is:

- X axis: logarithmic Hz, bounded by the connected analyzer's valid spectrum and
  source Nyquist frequency.
- Y axis: gain in dB.
- Two to eight ordered control points.
- Leftmost/rightmost points also establish the active layer frequency range.
- Connected segments interpolate gain in log-frequency space.
- Large touch targets and a magnified/locked editing mode prevent accidental X/Y
  movement on phones.
- Optional X-only or Y-only drag locking.
- Optional snapping to musical octaves, common frequencies, or a configurable grid.
- A synchronized table with frequency and gain inputs for exact editing.
- Add, remove, reset, and flatten actions.
- Validation messages appear at the affected point, not only at page level.

The graph may display a downsampled live spectrum as context, but the live trace is
view-only and must never be required for editing or Apply.

## State entities

All durable entities use immutable UUIDs and separately editable names.

### Audio source selection

```json
{
  "source_id": "usb:0d8c:0147:card3",
  "type": "alsa_input",
  "display_name": "Dayton iMM-6C",
  "channel": 0
}
```

Audio source selection is device/runtime state by default. Loading a color/layer
configuration must not silently make the Pi discoverable or take control of a phone.
A future optional `preferred_source_id` may be stored in a configuration, but source
activation still requires an explicit successful switch.

### FFT analyzer

```json
{
  "id": "analyzer-uuid",
  "name": "Fast full spectrum",
  "enabled": true,
  "window_function": "hann",
  "window_samples": 512,
  "hop_samples": 512,
  "fft_samples": 8192,
  "output": "magnitude",
  "remove_dc": true
}
```

The sample rate is reported from the audio source. A favorite may record a required
or preferred rate, but the effective analyzer state must show the actual rate.

### Visual layer

```json
{
  "id": "layer-uuid",
  "name": "Bass bars",
  "type": "fft_bars",
  "analysis_source_id": "analyzer-uuid",
  "visible": true,
  "frequency_scale": "log",
  "response_points": [
    {"frequency_hz": 60.0, "gain_db": 0.0},
    {"frequency_hz": 250.0, "gain_db": 5.0},
    {"frequency_hz": 1950.0, "gain_db": -8.0}
  ],
  "bins": 64,
  "normalization": {"mode": "rolling_rms", "headroom": 2.5},
  "smoothing": {"attack_ms": 25.0, "release_ms": 180.0},
  "render": {
    "theme": "ocean",
    "gradient": false,
    "overflow": true,
    "peaks": false
  }
}
```

Time-based attack/release replaces frame-count assumptions so behavior remains
stable when content FPS or analyzer hop rate changes.

### Complete music configuration

```json
{
  "schema_version": 1,
  "id": "configuration-uuid",
  "name": "Blue bass and warm mids",
  "analyzers": [],
  "visual_layers": [],
  "layer_order_top_to_bottom": [],
  "output": {"brightness_limit": 1.0, "content_fps": 60}
}
```

The complete candidate document is the unit of validation and application.

## Draft, Apply, Save, and boot behavior

These actions have deliberately different meanings:

- **Edit**: Changes only browser-local draft state.
- **Discard**: Restores the last acknowledged applied state.
- **Apply**: Sends the complete candidate with the applied revision. It does not
  automatically save a named preset.
- **Save Analyzer Favorite**: Stores one analyzer definition under a custom name.
- **Save Layer**: Stores one visual-layer template under a custom name. The saved
  template records the analyzer requirements but does not hard-code another
  configuration's analyzer UUID.
- **Save Configuration**: Stores the complete analyzer and visual stack.
- **Make boot default**: Explicitly selects one saved configuration for future
  launches.

`Default - Current` is a protected analyzer favorite representing the current
512/Hann/8192 baseline. It can be duplicated and edited but not silently overwritten.
Compatibility layer templates preserve the current bass and mid visual appearance.

## Atomic Apply transaction

The proposed request is a complete desired state, not a list of UI gestures:

```json
{
  "run_id": "current-run",
  "expected_revision": 14,
  "operation": "apply_configuration",
  "configuration": {}
}
```

The runtime must:

1. Validate schema version, sizes, types, IDs, and names.
2. Validate every layer's analyzer reference.
3. Validate analyzer geometry and source compatibility.
4. Validate frequency point ordering, bounds, count, and finite values.
5. Canonicalize analyzers and calculate the distinct-analysis compute budget.
6. Compile windows, bin projections, curves, scalers, and renderers outside the
   active state.
7. Reject the entire candidate if compilation or budget validation fails.
8. Swap the compiled state at a safe hop/frame boundary.
9. Return the effective normalized state, new revision, and any non-fatal warnings.

No intermediate analyzer/layer combinations may become visible. A conflict returns
the latest applied state without destroying the user's draft; the UI then offers
Reload or Save Draft As.

## Validation limits for the first release

- Maximum 4 active distinct analyzers.
- Maximum 16 visual layers.
- Maximum 8 response points per FFT visual layer.
- FFT sizes limited to tested powers of two, initially 512 through 32768.
- Window and hop sizes must be positive and within the source ring capacity.
- Hop cannot exceed window unless an explicit sparse-analysis mode is added later.
- Frequency points must be strictly increasing and between the analyzer's minimum
  usable frequency and Nyquist.
- Names are bounded display strings; IDs, paths, module names, or code are never
  accepted from a user-controlled name.
- Complete request and stored-document sizes remain bounded.

Limits should be advertised by the schema so the browser can give immediate feedback,
but the service and runtime remain authoritative.

