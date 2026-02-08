# Architecture Overview

This document explains the code structure for developers who want to understand or extend the visualizer.

## Module Structure

```text
fft_program/
├── main.py              # Entry point, orchestrates everything
├── config/              # Configuration layer
│   ├── settings.py      # Dataclass definitions (including LayerConfig)
│   └── presets.py       # Named preset configurations
├── core/                # Core processing
│   ├── audio.py         # Audio capture, FFT, and multi-layer bin extraction
│   ├── scaling.py       # Normalization, smoothing, and peak tracking
│   └── matrix_app.py    # LED matrix interface
├── themes/              # Color themes
│   ├── base.py          # Abstract BaseTheme
│   ├── gradients.py     # All gradient-based themes
│   └── registry.py      # Theme lookup
├── visualizers/         # Display modes
│   ├── base.py          # Abstract BaseVisualizer
│   ├── bars_unified.py  # Unified visualizer with layers, gradient, overflow
│   ├── peaks.py         # Peak indicator drawing
│   └── registry.py      # Visualizer lookup
└── utils/               # Helpers
    └── colors.py        # Color math utilities
```

## Data Flow

### Single-Layer Mode

```text
┌─────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ Audio Input │ ──▶ │ AudioProcessor  │ ──▶ │ ScalingProcessor│
│ (sounddevice)     │ (FFT, binning)  │     │ (normalize,     │
└─────────────┘     └─────────────────┘     │  smooth, peaks) │
                                            └────────┬────────┘
                                                     │
                                                     ▼
┌─────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ LED Matrix  │ ◀── │   Visualizer    │ ◀── │  smoothed_bars  │
│ (rgbmatrix) │     │ + Theme colors  │     │  peak_heights   │
└─────────────┘     └─────────────────┘     └─────────────────┘
```

### Multi-Layer Mode

```text
┌─────────────┐     ┌─────────────────┐
│ Audio Input │ ──▶ │ AudioProcessor  │
│ (sounddevice)     │ (FFT computed   │
└─────────────┘     │  ONCE)          │
                    └────────┬────────┘
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
   ┌────────────┐     ┌────────────┐     ┌────────────┐
   │ Layer 0    │     │ Layer 1    │     │ Layer 2    │
   │ Bass bins  │     │ Mid bins   │     │ Treble bins│
   │ 80-1950Hz  │     │ 2000-4000Hz│     │ 4000-8000Hz│
   └──────┬─────┘     └──────┬─────┘     └──────┬─────┘
          │                  │                  │
          ▼                  ▼                  ▼
   ┌────────────┐     ┌────────────┐     ┌────────────┐
   │ Scaler 0   │     │ Scaler 1   │     │ Scaler 2   │
   │ (smoothed, │     │ (smoothed, │     │ (smoothed, │
   │  peaks)    │     │  peaks)    │     │  peaks)    │
   └──────┬─────┘     └──────┬─────┘     └──────┬─────┘
          │                  │                  │
          └──────────────────┼──────────────────┘
                             ▼
                    ┌─────────────────┐
                    │   Visualizer    │
                    │ (draws layers   │
                    │  in draw_order) │
                    └────────┬────────┘
                             ▼
                    ┌─────────────────┐
                    │   LED Matrix    │
                    └─────────────────┘
```

**Key insight**: The FFT is computed once per frame. Each layer just extracts different frequency ranges from the same FFT result. This makes multi-layer mode nearly free (see [FFT Math Explained](fft_math_explained.md)).

## Component Responsibilities

### main.py

- Parse command line arguments
- Load settings
- Initialize all components
- Handle keyboard input for runtime controls
- Run main loop:
  1. Get FFT data from AudioProcessor
  2. Get layer magnitudes if layered mode enabled
  3. Process through ScalingProcessor (one per layer in layered mode)
  4. Skip processing for invisible layers (performance optimization)
  5. Draw with Visualizer
  6. Swap canvas buffer

### AudioProcessor (core/audio.py)

- Manage audio input stream via sounddevice
- Compute FFT with zero-padding (done ONCE per frame)
- Create logarithmic frequency bins
- Apply frequency weighting
- Apply noise floor
- **Multi-layer support**: Extract different frequency ranges from same FFT

**Key methods:**

- `setup()` - Initialize FFT parameters
- `setup_layers(layer_configs)` - Configure multi-layer bin extraction
- `start()` / `stop()` - Control audio stream
- `get_fft_magnitudes()` - Return current FFT data (single-layer)
- `get_layer_magnitudes()` - Return list of magnitude arrays (one per layer)

### ScalingProcessor (core/scaling.py)

- Normalize bar values (fixed, RMS, or max scaling)
- Apply asymmetric smoothing (fast rise, slow fall)
- Track peak indicators
- Handle silence threshold
- **In layered mode**: One scaler per layer for independent dynamics

**Key methods:**

- `process(bars)` - Returns (normalized, smoothed, peaks)
- `reset()` - Clear state when switching modes

### MatrixApp (core/matrix_app.py)

- Wrap rgbmatrix library
- Parse LED-related command line arguments
- Initialize matrix with options
- Provide canvas for drawing

**Key properties:**

- `width`, `height` - Matrix dimensions
- `canvas` - Current frame buffer

### BaseTheme (themes/base.py)

- Abstract class all themes inherit
- Define color interface

**Key methods:**

- `get_color(height_ratio, column_ratio)` - Main color method
- `get_overflow_color(layer, ...)` - Overflow layer colors
- `get_peak_color(mode, ...)` - Peak indicator colors

### BaseVisualizer (visualizers/base.py)

- Abstract class all visualizers inherit
- Store reference to theme and settings

**Key methods:**

- `draw(canvas, smoothed_bars, peak_heights, layer_bars, layer_peaks)` - Render frame
- `set_theme(theme)` - Set active theme

### BarsUnifiedVisualizer (visualizers/bars_unified.py)

The main visualizer supporting multiple modes:

**Single-layer state:**
- `gradient_mode` - Per-pixel vs uniform color
- `overflow_mode` - Allow bars to exceed height
- `bars_enabled` - Show bars (can be OFF for peaks only)
- `full_mode` - Full screen with crossfade
- `debug_mode` - Static gradient test

**Multi-layer state:**
- `layers_enabled` - Multi-layer mode active
- `active_layer` - Which layer is being edited (0-indexed)
- `layer_states` - List of `LayerState` objects with per-layer settings
- `draw_order` - List of layer indices controlling z-order

**LayerState dataclass:**
```python
@dataclass
class LayerState:
    theme: Any           # Theme instance
    theme_name: str      # Theme name
    bars_enabled: bool   # Draw bars for this layer
    gradient_enabled: bool
    overflow_enabled: bool
    peak_enabled: bool
    visible: bool        # Whether layer is drawn
    boost: float         # Sensitivity multiplier
```

**Key methods:**
- `setup_layers(configs, brightness_boost)` - Initialize layer states
- `toggle_gradient()`, `toggle_overflow()`, etc. - Affect active layer
- `select_layer(index)` - Switch which layer is being edited
- `toggle_layer_visibility(index)` - Show/hide a layer
- `swap_draw_order(direction)` - Move layer forward/backward in z-order

## Registry Pattern

Both themes and visualizers use a registry pattern:

```python
# Registration happens at module load
_REGISTRY = {}

def _register_builtins():
    for cls in [ThemeA, ThemeB, ...]:
        _REGISTRY[cls.name] = cls

# Lookup by name
def get_theme(name):
    return _REGISTRY[name](...)
```

**Benefits:**

- New themes/visualizers auto-register
- String-based lookup for CLI
- Easy to list available options

## Settings Architecture

Settings use Python dataclasses:

```python
@dataclass
class AudioSettings:
    device: str = 'iMM-6C'
    block_size: int = 512
    ...

@dataclass
class LayerConfig:
    name: str                    # e.g., 'bass', 'mid', 'treble'
    freq_range: Tuple[int, int]  # (min_freq, max_freq) in Hz
    theme_name: str = 'ocean'
    bars_enabled: bool = True
    gradient_enabled: bool = False
    overflow_enabled: bool = True
    peak_enabled: bool = False
    visible: bool = True
    boost: float = 1.0           # Sensitivity multiplier
    bins: int = 64

@dataclass
class LayeredVisualizerSettings:
    enabled: bool = False
    layers: List[LayerConfig] = ...  # Default 3 layers
    active_layer: int = 0

@dataclass
class Settings:
    audio: AudioSettings
    frequency: FrequencySettings
    overflow: OverflowSettings
    peak: PeakSettings
    color: ColorSettings
    sensitivity: SensitivitySettings
    scaling: ScalingSettings
    smoothing: SmoothingSettings
    shadow: ShadowSettings
    layers: LayeredVisualizerSettings  # Multi-layer config
```

**Benefits:**

- Type hints for IDE support
- Default values documented in code
- Easy serialization to/from JSON
- Grouped by category

## Adding New Features

### New Setting

1. Add field to appropriate dataclass in `config/settings.py`
2. Update `load_settings()` if needed for JSON support
3. Use in code via `settings.category.field`

### New Layer

1. Add `LayerConfig` to `settings.layers.layers` list
2. Configure frequency range, theme, and display options
3. Layers are drawn in order (index 0 = background)

### New Theme

1. Create class inheriting `BaseTheme` in `themes/`
2. Implement `get_color()` method
3. Add to registry in `themes/registry.py`

### New Visualizer

1. Create class inheriting `BaseVisualizer` in `visualizers/`
2. Implement `draw()` method (accept layer_bars, layer_peaks kwargs)
3. Add to registry in `visualizers/registry.py`

### New Preset

1. Add function to `config/presets.py`
2. Return configured `Settings` object
3. Add to preset dict in `get_preset()`

## Threading Model

```text
Main Thread:
    └── Main loop (draw frames)

Audio Thread (managed by sounddevice):
    └── Audio callback (copies samples to self.latest_samples)
```

The audio callback runs in a separate thread managed by sounddevice.
Communication is via `self.latest_samples` (numpy array copy).

## Performance Considerations

| Component | Typical Time | Notes |
| --------- | ------------ | ----- |
| FFT (numpy) | ~0.5ms | Runs in C, computed ONCE per frame |
| Bin extraction | ~0.1ms | Just array slicing, cheap |
| Scaling/smoothing | ~0.1ms per layer | Simple numpy ops |
| Visualizer draw | ~1-3ms | Per-pixel SetPixel calls |
| Matrix swap | ~5-15ms | Hardware dependent |
| **Total per frame** | **~7-20ms** | Allows 50-140 FPS |

**Key optimizations:**
- FFT is computed once, shared by all layers
- Invisible layers skip scaler processing (main.py checks visibility)
- Layer draw uses layer-by-layer rendering (simpler and faster than single-pass)

**Bottleneck:** Matrix hardware (GPIO timing), not Python code.

See [Optimization Notes](optimization_notes.md) for detailed lessons learned.

## Future Improvements

Potential enhancements:

- Hot-reload settings from file
- MIDI/OSC control
- Beat detection
- Multiple visualizer blending
- Network streaming input
- Recording/playback of FFT data
- More than 3 layers (currently UI supports 1/2/3 keys)
