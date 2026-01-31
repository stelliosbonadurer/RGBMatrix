# Architecture Overview

This document explains the code structure for developers who want to understand or extend the visualizer.

## Module Structure

```text
fft_program/
├── main.py              # Entry point, orchestrates everything
├── config/              # Configuration layer
│   ├── settings.py      # Dataclass definitions
│   └── presets.py       # Named preset configurations
├── core/                # Core processing
│   ├── audio.py         # Audio capture and FFT
│   ├── scaling.py       # Normalization and smoothing
│   └── matrix_app.py    # LED matrix interface
├── themes/              # Color themes
│   ├── base.py          # Abstract BaseTheme
│   ├── gradients.py     # All gradient-based themes
│   └── registry.py      # Theme lookup
├── visualizers/         # Display modes
│   ├── base.py          # Abstract BaseVisualizer
│   ├── bars_unified.py  # Unified visualizer with gradient/overflow toggles
│   ├── peaks.py         # Peak indicator drawing
│   └── registry.py      # Visualizer lookup
└── utils/               # Helpers
    └── colors.py        # Color math utilities
```

## Data Flow

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

## Component Responsibilities

### main.py

- Parse command line arguments
- Load settings
- Initialize all components
- Run main loop:
  1. Get FFT data from AudioProcessor
  2. Process through ScalingProcessor
  3. Draw with Visualizer
  4. Swap canvas buffer

### AudioProcessor (core/audio.py)

- Manage audio input stream via sounddevice
- Compute FFT with zero-padding
- Create logarithmic frequency bins
- Apply frequency weighting
- Apply noise floor

**Key methods:**

- `setup()` - Initialize FFT parameters
- `start()` / `stop()` - Control audio stream
- `get_fft_magnitudes()` - Return current FFT data

### ScalingProcessor (core/scaling.py)

- Normalize bar values (fixed, RMS, or max scaling)
- Apply asymmetric smoothing (fast rise, slow fall)
- Track peak indicators
- Handle silence threshold

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

- `draw(canvas, smoothed_bars, peak_heights)` - Render frame
- `set_theme(theme)` - Set active theme

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
class Settings:
    audio: AudioSettings = field(default_factory=AudioSettings)
    frequency: FrequencySettings = field(default_factory=FrequencySettings)
    ...
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

### New Theme

1. Create class inheriting `BaseTheme` in `themes/`
2. Implement `get_color()` method
3. Add to registry in `themes/registry.py`

### New Visualizer

1. Create class inheriting `BaseVisualizer` in `visualizers/`
2. Implement `draw()` method
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
| FFT (numpy) | ~0.5ms | Runs in C, very fast |
| Scaling/smoothing | ~0.1ms | Simple numpy ops |
| Visualizer draw | ~1-3ms | Per-pixel SetPixel calls |
| Matrix swap | ~5-15ms | Hardware dependent |
| **Total per frame** | **~7-20ms** | Allows 50-140 FPS |

**Bottleneck:** Matrix hardware (GPIO timing), not Python code.

## Future Improvements

Potential enhancements:

- Hot-reload settings from file
- MIDI/OSC control
- Beat detection
- Multiple visualizer blending
- Network streaming input
- Recording/playback of FFT data
