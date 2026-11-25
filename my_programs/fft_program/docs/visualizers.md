# Visualizers Guide

The visualizer renders the standard 64-bin FFT result directly to the LED matrix.

## Display Modes

The unified `bars` visualizer supports these modes. Each is a per-layer setting in the
layer structure (set with `structure.set`); there are no keyboard toggles.

| Layer setting | Mode | Description |
| ------------- | ---- | ----------- |
| `gradient` | Gradient | ON = per-pixel color gradient, OFF = uniform color per bar |
| `overflow` | Overflow | ON = bars can exceed height with layered colors, OFF = clamped to height |
| `bars` | Bars | ON = show bars, OFF = show only peaks |
| `full` | Full | ON = entire screen lit with crossfade at bar height |

## Mode Combinations

| Gradient | Overflow | Result |
| -------- | -------- | ------ |
| OFF | OFF | Uniform color per bar, clamped to height |
| ON | OFF | Per-pixel gradient within bar, clamped to height |
| OFF | ON | Uniform color based on total bar height (color changes as bar grows) |
| ON | ON | Per-pixel gradient with overflow layer colors |

The word *layer* in overflow theme APIs means a vertical matrix-height color band
when a single bar exceeds the panel height. It does not mean an independent analyzer
or renderer.

## Gradient and Overflow

`gradient` and `overflow` are settings of each layer, set on the FFT page or in a
`structure.set` document; there are no `--gradient` or `--overflow` options. The
default layer starts with gradient off and overflow from the settings file
(`overflow.enabled`, on by default).

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
    peak_heights: Optional[np.ndarray] = None
) -> None:
```

__Parameters:__

- `canvas`: Matrix canvas object with:
  - `canvas.Clear()` - Clear display
  - `canvas.SetPixel(x, y, r, g, b)` - Set pixel color
- `smoothed_bars`: Array of normalized values for the standard path
  - Length = matrix width (one per column)
  - Values 0-1 normally, can exceed 1.0 in overflow mode
- `peak_heights`: Optional array of peak positions (0-1)

### Available in self

- `self.width` - Matrix width in pixels
- `self.height` - Matrix height in pixels
- `self.settings` - Full Settings object
- `self.theme` - Active theme instance (use `self.theme.get_color()`)

The removed legacy layered-mode fields are not part of the visualizer interface.
Future multiple views will use multiple standard visualizer instances rather than
adding per-layer arguments back to `draw()`.

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
