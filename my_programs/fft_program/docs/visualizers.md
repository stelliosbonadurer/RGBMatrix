# Visualizers Guide

The visualizer renders FFT data to the LED matrix. There is one unified visualizer with multiple toggleable modes, including multi-layer support.

## Display Modes

The unified `bars` visualizer supports these toggleable modes:

| Key | Mode | Description |
| --- | ---- | ----------- |
| `g` | Gradient | ON = per-pixel color gradient, OFF = uniform color per bar |
| `o` | Overflow | ON = bars can exceed height with layered colors, OFF = clamped to height |
| `b` | Bars | ON = show bars, OFF = show only peaks |
| `f` | Full | ON = entire screen lit with crossfade at bar height |
| `d` | Debug | ON = static gradient test pattern |
| `l` | Layered | ON = multi-layer mode with independent frequency bands |

## Mode Combinations

| Gradient | Overflow | Result |
| -------- | -------- | ------ |
| OFF | OFF | Uniform color per bar, clamped to height |
| ON | OFF | Per-pixel gradient within bar, clamped to height |
| OFF | ON | Uniform color based on total bar height (color changes as bar grows) |
| ON | ON | Per-pixel gradient with overflow layer colors |

## Multi-Layer Mode

Press `l` to enable layered mode. Each layer has its own:

- **Frequency range** (e.g., bass 80-1950 Hz, mid 2000-4000 Hz)
- **Theme** (ocean, warm, forest, etc.)
- **Settings** (gradient, overflow, peaks, bars, visibility)
- **Scaler** (independent smoothing and normalization)

### Layer Controls

| Key | Action |
| --- | ------ |
| `1` / `2` / `3` | Select layer for editing |
| `!` / `@` / `#` | Toggle layer visibility (Shift+1/2/3) |
| `<` / `>` | Move layer backward/forward in draw order |
| `t` / `T` | Change theme for selected layer |
| `g` | Toggle gradient for selected layer |
| `o` | Toggle overflow for selected layer |
| `p` | Toggle peaks for selected layer |
| `b` | Toggle bars for selected layer |

### Draw Order

Layers are drawn in `draw_order` sequence:
- First layer drawn = background (can be covered by later layers)
- Last layer drawn = foreground (always visible on top)

Use `<` and `>` to reorder which layer appears in front.

### Performance

- Invisible layers (`visible=False`) skip processing entirely
- FFT is computed once and shared by all layers
- Each layer has its own scaler to preserve independent dynamics

## Command Line Options

```bash
# Start with gradient mode enabled
sudo python main.py --gradient ...

# Start with overflow mode enabled  
sudo python main.py --overflow ...

# Both modes
sudo python main.py --gradient --overflow ...
```

## Creating a Custom Visualizer

### Step 1: Create Visualizer Class

Create a new file, e.g., `visualizers/circular.py`:

```python
from typing import Optional
import numpy as np
import math

from .base import BaseVisualizer


class CircularVisualizer(BaseVisualizer):
    """Radial/circular FFT visualization."""

    name = "circular"
    description = "Circular spectrum display"

    def draw(
        self,
        canvas,
        smoothed_bars: np.ndarray,
        peak_heights: Optional[np.ndarray] = None
    ) -> None:
        """
        Draw visualization to canvas.

        Args:
            canvas: RGB matrix canvas with SetPixel(x, y, r, g, b) method
            smoothed_bars: Normalized bar values (0-1 range, or >1 for overflow)
            peak_heights: Optional peak positions (0-1 range)
        """
        if self.theme is None:
            raise RuntimeError("Theme not set")

        canvas.Clear()

        num_bins = len(smoothed_bars)
        center_x = self.width // 2
        center_y = self.height // 2
        max_radius = min(center_x, center_y) - 1

        for i, bar_value in enumerate(smoothed_bars):
            # Calculate angle for this bin
            angle = (i / num_bins) * 2 * math.pi

            # Calculate line from center outward
            length = int(bar_value * max_radius)

            # Get color
            column_ratio = i / num_bins
            r, g, b = self.theme.get_color(bar_value, column_ratio)

            # Draw line
            for dist in range(length):
                x = int(center_x + dist * math.cos(angle))
                y = int(center_y + dist * math.sin(angle))

                if 0 <= x < self.width and 0 <= y < self.height:
                    canvas.SetPixel(x, y, r, g, b)
```

### Step 2: Register the Visualizer

In `visualizers/registry.py`:

```python
from .circular import CircularVisualizer  # Add import

def _register_builtin_visualizers():
    builtin_visualizers = [
        BarsUnifiedVisualizer,
        CircularVisualizer,  # Add here
    ]
```

### Step 3: Update __init__.py

In `visualizers/__init__.py`:

```python
from .circular import CircularVisualizer
# Add to __all__ list
```

## Visualizer Interface

### Required: draw()

```python
def draw(
    self,
    canvas,
    smoothed_bars: np.ndarray,
    peak_heights: Optional[np.ndarray] = None,
    layer_bars: Optional[List[np.ndarray]] = None,
    layer_peaks: Optional[List[np.ndarray]] = None
) -> None:
```

__Parameters:__

- `canvas`: Matrix canvas object with:
  - `canvas.Clear()` - Clear display
  - `canvas.SetPixel(x, y, r, g, b)` - Set pixel color
- `smoothed_bars`: Array of normalized values (single-layer mode)
  - Length = matrix width (one per column)
  - Values 0-1 normally, can exceed 1.0 in overflow mode
- `peak_heights`: Optional array of peak positions (0-1)
- `layer_bars`: List of smoothed bar arrays (one per layer, may contain None for invisible layers)
- `layer_peaks`: List of peak arrays (one per layer, may contain None)

### Available in self

- `self.width` - Matrix width in pixels
- `self.height` - Matrix height in pixels
- `self.settings` - Full Settings object
- `self.theme` - Active theme instance (use `self.theme.get_color()`)
- `self.layers_enabled` - Whether multi-layer mode is active
- `self.layer_states` - List of LayerState objects (when layered)
- `self.draw_order` - List of layer indices for z-ordering

### Optional: on_settings_changed()

Called when settings are updated (for hot-reload support):

```python
def on_settings_changed(self, settings: Settings) -> None:
    self.settings = settings
    # Recalculate anything that depends on settings
```

## Visualizer Ideas

Here are some visualizers you could implement:

### Mirrored Bars

Bars extend from center both up and down:

```python
# Draw bar from center upward
for j in range(bar_height // 2):
    y_up = center_y - j
    y_down = center_y + j
    canvas.SetPixel(i, y_up, r, g, b)
    canvas.SetPixel(i, y_down, r, g, b)
```

### Spectrogram (Scrolling)

Time scrolls horizontally, frequency on Y-axis:

```python
# Store history buffer
# Each frame, shift left and add new column on right
```

### Waveform

Display raw audio waveform instead of FFT:

```python
# Would need to access raw audio samples
# Plot amplitude over time
```

### Particle System

Spawn particles based on audio energy:

```python
# Track particle positions
# Spawn at beat detection
# Update physics each frame
```

### VU Meter

Classic stereo VU meter style:

```python
# Two horizontal bars (left/right channels)
# With peak hold indicators
```

## Tips

1. __Always call canvas.Clear()__ at the start of draw()
2. __Check bounds__ before SetPixel() to avoid crashes
3. __Use self.theme__ for colors - don't hardcode
4. __Handle empty bars__ gracefully (bar_value = 0)
5. __Consider peak_heights__ even if optional - users may enable peaks
6. __Test with different matrix sizes__ - don't assume 64x64
