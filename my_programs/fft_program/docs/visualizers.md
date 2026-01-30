# Visualizers Guide

Visualizers determine how the FFT data is rendered to the LED matrix.

## Available Visualizers

| Visualizer | Description |
|------------|-------------|
| `bars` | Classic vertical bar graph, one bar per frequency bin |
| `bars_overflow` | Bars that stack/wrap with different colors when audio is loud |

## Selecting a Visualizer

### Via Command Line

```bash
sudo python main.py --visualizer=bars_overflow ...
```

### Automatic Selection

By default, the visualizer is chosen based on settings:

- `overflow.enabled = True` → `bars_overflow`
- `overflow.enabled = False` → `bars`

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
        BarsVisualizer,
        BarsOverflowVisualizer,
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
- `smoothed_bars`: Array of normalized values
  - Length = matrix width (one per column)
  - Values 0-1 normally, can exceed 1.0 in overflow mode
- `peak_heights`: Optional array of peak positions (0-1)

### Available in self

- `self.width` - Matrix width in pixels
- `self.height` - Matrix height in pixels
- `self.settings` - Full Settings object
- `self.theme` - Active theme instance (use `self.theme.get_color()`)

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
