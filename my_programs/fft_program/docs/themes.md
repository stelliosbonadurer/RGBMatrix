# Themes Guide

Themes control the colors used to render the visualization.

## Available Themes

`ocean` is the default.

| Theme | Description | Best For |
| ----- | ----------- | -------- |
| `ocean` | Deep blue → Cyan → White | Calm, ambient; the only theme the `pillow-bulk` backend accepts |
| `warm` | Red → Orange → Yellow | Acoustic, general use |
| `forest` | Dark green → Bright green → Yellow | Nature vibes |
| `autumn` | Deep brown → Burnt orange → Golden yellow | Mellow |
| `rainbow` | Full spectrum by column position | Colorful displays |
| `dynamic` | Animated hue cycling, same color across columns | Shows, ambient |
| `dynamic_lateral_gradient` | Animated hue cycling that drifts across columns | Shows, ambient |
| `vice_pink` | Hot pink, dark → light | One layer of a multi-layer Miami look |
| `vice_orange` | Sunset orange, dark → light | One layer of a multi-layer Miami look |
| `vice_purple` | Violet, dark → light | One layer of a multi-layer Miami look |
| `vice_teal` | Ocean teal, dark → light | One layer of a multi-layer Miami look |
| `vice_city` | Purple → Pink → Orange → Peach by height | Single-layer Miami sunset |
| `vice_skyline` | Teal → Purple → Pink → Orange across columns | Single-layer Miami skyline |

The `vice_*` themes use the GTA VI Miami-sunset palette (approximate hex values in
`themes/palettes.py`). Each single-color `vice_*` theme stays in its own hue, and its
overflow layers brighten toward white, so stacked layers stay distinct. Use one per
layer, for example pink, purple, and teal for three layers, adding orange for a fourth.

## Selecting a Theme

### Per Layer

Each layer's `theme` setting picks its theme: the Theme field on the FFT page, or
`settings.theme` in a `structure.set` document. There is no `--theme` option.

### Via Settings File

```json
{
  "color": {
    "theme": "vice_city",
    "brightness_boost": 1.2
  }
}
```

### Via Code (config/settings.py defaults)

```python
@dataclass
class ColorSettings:
    theme: str = 'ocean'  # Change default here
    brightness_boost: float = 1.0
```

## Creating a Custom Theme

### From color stops (no code)

Most themes are a gradient. Describe one as color stops in `themes/palettes.py` and
add it to `PALETTE_THEMES`:

```python
MyTheme = color_stop_theme(
    "my_theme",
    "Navy (low) -> gold (high)",
    ((0.0, (10, 20, 90)), (0.5, (120, 60, 160)), (1.0, (255, 200, 40))),
    axis="height",              # or "column" to spread colors across the display
    overflow=(                  # optional: one stop list per overflow layer
        ((0.0, (255, 200, 40)), (1.0, (255, 240, 200))),
        ((0.0, (255, 240, 200)), (1.0, (255, 255, 255))),
    ),
)
```

Stops run from position 0 to 1 with RGB values 0-255. Each layer's theme builds a
256-entry lookup table once at launch, so drawing costs one table lookup per pixel.

### From code

#### Step 1: Create Theme Class

Add to `themes/gradients.py` (or create a new file):

```python
class MyCustomTheme(BaseTheme):
    """Description of your theme."""

    name = "my_custom"  # The value of a layer's theme setting
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

#### Step 2: Register the Theme

In `themes/registry.py`, add to `_register_builtin_themes()`:

```python
from .gradients import MyCustomTheme  # Add import

def _register_builtin_themes():
    builtin_themes = [
        # ... existing themes ...
        MyCustomTheme,  # Add here
    ]
```

#### Step 3: Update __init__.py (optional)

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
