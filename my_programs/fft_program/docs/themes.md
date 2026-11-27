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

### Saved on the Pi (no code)

Custom themes are FFT's program config file, saved by the web service on the Pi
(`<state directory>/program-files/fft.themes.json`). Edit them in the FFT page's
**Custom themes** section: add a theme, set its name, description, color direction,
colors, and overflow colors (each list previews as a gradient), and press **Save
themes**. The section saves on its own; Launch and Send update never save it. The
API is `GET`/`PUT /api/v1/programs/fft/files/themes`.

Each FFT launch receives the saved file in `RGB_MATRIX_PROGRAM_CONFIG` and registers
its themes before building any layer, so a saved change applies from the next launch.
Saved themes join every layer's Theme list at once; while FFT runs, a layer that uses
a custom theme after the themes were saved makes Send update a relaunch. A run without
that variable reads the tracked default `config/custom_themes.json` (no custom themes).

If the saved file can't be used (it was edited by hand, or a later version no longer
accepts it), the section says why and offers **Remove the saved file**; until then
launches use no custom themes.

**A missing theme.** A preset or layer can name a custom theme that is no longer
saved (deleted, or saved on another Pi). Loading it on the page sets that layer to
`ocean` and says so. A launch structure naming an unregistered `my_*` theme runs that
layer with `ocean` and reports it in the structure snapshot's `theme_fallbacks`
(`path`, `layer_id`, `theme`), which the page shows; the other layers are unchanged.
A live `structure.set` with an unregistered theme, and any non-`my_*` name that isn't
registered, are still refused.

```json
{
  "themes": [
    {
      "name": "my_sunset",
      "description": "Purple to gold",
      "axis": "height",
      "stops": [
        {"position": 0.0, "color": [60, 20, 140]},
        {"position": 1.0, "color": [255, 200, 80]}
      ],
      "overflow": [
        [{"position": 0.0, "color": [255, 200, 80]}, {"position": 1.0, "color": [255, 255, 255]}]
      ]
    }
  ]
}
```

- Names are `my_` plus 1-29 lowercase letters, digits, or underscores, so they never
  collide with a built-in theme. Names are unique within the file.
- Every theme has exactly these five fields. `axis` is `height` or `column`;
  `description` is at most 80 characters.
- `stops` (and each `overflow` list) holds 2-16 stops whose positions run from 0 to 1
  without decreasing; colors are `[r, g, b]` integers 0-255. Overflow layer n uses
  list n and later layers reuse the last list; with `[]` overflow repeats the main
  gradient. At most 4 overflow lists and 32 themes, and the whole file at most
  48 KiB as compact JSON (a few themes of 3-5 stops is about 1-2 KB).
- The web service refuses a file that breaks these rules. If FFT is ever handed one,
  it prints one line and runs with the built-in themes only.

These are color-stop themes, drawn exactly like the built-in ones below.

### From color stops (built in)

Most built-in themes are a gradient. Describe one as color stops in
`themes/palettes.py` and add it to `PALETTE_THEMES`:

```python
NavyGoldTheme = color_stop_theme(
    "navy_gold",
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
class PurplePinkTheme(BaseTheme):
    """Description of your theme."""

    name = "purple_pink"  # The value of a layer's theme setting; built-ins never start with my_
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
from .gradients import PurplePinkTheme  # Add import

def _register_builtin_themes():
    builtin_themes = [
        # ... existing themes ...
        PurplePinkTheme,  # Add here
    ]
```

#### Step 3: Update __init__.py (optional)

For cleaner imports, add to `themes/__init__.py`:

```python
from .gradients import PurplePinkTheme
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
