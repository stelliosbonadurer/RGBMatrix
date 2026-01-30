# Themes Guide

Themes control the colors used to render the visualization.

## Available Themes

| Theme | Description | Best For |

| `classic` | Blue → Green → Red | General use |
| `warm` | Dark red → Orange → Yellow | Acoustic, bluegrass |
| `fire` | Black/red → Orange → Yellow/white | Intense, EDM |
| `ocean` | Deep blue → Cyan → White | Calm, ambient |
| `forest` | Dark green → Bright green → Yellow | Nature vibes |
| `purple` | Deep purple → Magenta → Pink | Vibrant, pop |
| `rainbow` | Full spectrum by column position | Colorful displays |
| `spectrum` | Rainbow + amplitude hue shift | Dynamic, reactive |
| `fire_spectrum` | Fire colors vary by column | Warm variety |
| `blue_flame` | Fire that transitions to blue at top | Unique effect |
| `waves` | Ocean waves with seafoam peaks | Relaxing |
| `mono_green` | Single green color, brightness varies | Retro |
| `mono_amber` | Amber/orange, vintage VU meter look | Classic |

## Selecting a Theme

### Via Command Line

```bash
sudo python main.py --theme=ocean ...
```

### Via Settings File

```json
{
  "color": {
    "theme": "fire",
    "brightness_boost": 1.2
  }
}
```

### Via Code (config/settings.py defaults)

```python
@dataclass
class ColorSettings:
    theme: str = 'fire'  # Change default here
    brightness_boost: float = 1.0
```

## Creating a Custom Theme

### Step 1: Create Theme Class

Add to `themes/gradients.py` (or create a new file):

```python
class MyCustomTheme(BaseTheme):
    """Description of your theme."""

    name = "my_custom"  # Used in --theme=my_custom
    description = "My custom color scheme"

    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        """
        Args:
            height_ratio: 0.0 (bottom) to 1.0 (top) - bar height
            column_ratio: 0.0 (left) to 1.0 (right) - column position

        Returns:
            (r, g, b) tuple with values 0-255
        """
        # Example: simple gradient from purple to pink
        r = int(128 + 127 * height_ratio)
        g = int(50 * height_ratio)
        b = int(200 + 55 * height_ratio)

        return self._apply_brightness(r, g, b)
```

### Step 2: Register the Theme

In `themes/registry.py`, add to `_register_builtin_themes()`:

```python
from .gradients import MyCustomTheme  # Add import

def _register_builtin_themes():
    builtin_themes = [
        # ... existing themes ...
        MyCustomTheme,  # Add here
    ]
```

### Step 3: Update __init__.py (optional)

For cleaner imports, add to `themes/__init__.py`:

```python
from .gradients import MyCustomTheme
```

## Theme Methods

### get_color(height_ratio, column_ratio)

Main method - returns RGB color for a pixel.

- `height_ratio`: 0-1, position within the bar (0=bottom, 1=top)
- `column_ratio`: 0-1, horizontal position (0=left, 1=right)

### get_overflow_color(layer, height_ratio, column_ratio)

Color for overflow layers (when bars exceed display height).

- `layer`: 0=first layer, 1=second overflow, etc.
- Default: red→orange (layer 0), orange→white (layer 1), then theme colors

### get_peak_color(mode, bar_color, column_ratio)

Color for peak indicator dots.

- `mode`: 'white', 'bar', 'contrast', or 'peak'
- `bar_color`: Current bar's RGB color
- Default handles all modes, override for custom behavior

## Tips for Good Themes

1. __Contrast at extremes__ - Make bottom (quiet) and top (loud) clearly different
2. __Smooth gradients__ - Avoid harsh jumps between color bands
3. __Consider brightness__ - Very dark colors are hard to see on LED panels
4. __Test with music__ - Colors look different in motion than static
5. __Use column_ratio__ - Makes wide displays more interesting (rainbow effect)

## Example: Position-Based Theme

```python
class WaveTheme(BaseTheme):
    name = "wave"
    description = "Colors shift across the display"

    def get_color(self, height_ratio, column_ratio=0.0):
        # Hue shifts based on column position
        hue = (column_ratio * 180 + height_ratio * 60) % 360

        # Convert HSV to RGB (simplified)
        # ... HSV conversion code ...

        return self._apply_brightness(r, g, b)
```
