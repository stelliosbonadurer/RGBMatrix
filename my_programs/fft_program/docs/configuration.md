# Configuration Guide

All settings are defined in `config/settings.py` as dataclasses. You can modify defaults there or override via JSON settings file.

## Command Line Arguments

### Matrix Configuration

| Argument | Description | Default |
| `--led-rows` | Panel height in pixels | 32 |
| `--led-cols` | Panel width in pixels | 32 |
| `--led-chain` | Number of daisy-chained panels | 1 |
| `--led-parallel` | Parallel chains (Pi 2/3/4: 1-3) | 1 |
| `--led-gpio-mapping` | Hardware mapping (`adafruit-hat`, `regular`) | regular |
| `--led-slowdown-gpio` | GPIO timing (0-4, higher = slower) | 1 |
| `--led-brightness` | Brightness 1-100% | 100 |
| `--led-pwm-bits` | PWM bits 1-11 (color depth) | 11 |
| `--led-row-addr-type` | Row addressing mode (0-4) | 0 |
| `--led-multiplexing` | Multiplexing type (0-8) | 0 |

### Application Arguments

| Argument | Description |
| `--audio-source` | Audio input: `usb-mic` (default) or `bluetooth` |
| `--visualizer` | Visualizer type (`bars`, `bars_overflow`) |
| `--settings` | Path to JSON settings file |
| `--list-themes` | Show available themes and exit |
| `--list-visualizers` | Show available visualizers and exit |

## Settings Categories

### AudioSettings

```python
device: str = 'iMM-6C'      # Audio device name
block_size: int = 512       # Samples per callback (~23ms at 44kHz)
fft_size: int = 8192        # Zero-pad size (higher = better freq resolution)
channel: int = 0            # Audio channel to use
sleep_delay: float = 0.004  # Frame delay in seconds
```

**Tips:**

- Find your device name with `python -c "import sounddevice; print(sounddevice.query_devices())"`
- Increase `fft_size` for larger matrices (more frequency bins)
- Lower `sleep_delay` for smoother animation (more CPU)

### FrequencySettings

```python
min_freq: int = 60          # Full range minimum Hz
max_freq: int = 14000       # Full range maximum Hz
zoom_mode: bool = True      # Use zoom range instead
zoom_min_freq: int = 100    # Zoom minimum Hz
zoom_max_freq: int = 6300   # Zoom maximum Hz
```

**Tips:**

- `zoom_mode=True` focuses on a narrower, more interesting range
- For bass-heavy music: lower `zoom_min_freq` to 40-60 Hz
- For acoustic/voice: 80-8000 Hz works well
- For electronic: 40-12000 Hz captures full range

### SensitivitySettings

```python
noise_floor_dbfs: float = -65.0  # Subtracted before weighting, in dBFS
aggregation: str = 'mean'        # How a bar's FFT bins combine: mean, band_energy, max
weight_points: list = [    # Frequency weighting curve
    {"position": 0.0, "weight": 0.45},
    {"position": 0.1, "weight": 0.752},
    {"position": 0.3, "weight": 2.019},
    {"position": 0.6, "weight": 4.888},
    {"position": 1.0, "weight": 10.0},
]
```

These are the startup values for the default layer and the analyzer. At runtime each
layer carries its own `noise_floor_dbfs`, `aggregation`, and `weight_points` in its
structure settings. These pick the layer's projection: layers that differ in any of
them (or in range) each cost one projection of the shared spectrum.

Bars are measured in full-scale units, where a full-scale sine reads 1.0 (0 dBFS), so
they don't change with `block_size` or `fft_size`. `noise_floor_dbfs` (−120 to −20)
is subtracted from each bar's level before the weighting curve multiplies it, so one
floor cuts quiet bass and quiet treble alike. A bar below the floor is dark. The
default −65 dBFS was kept after an in-room check on the Pi (not a silence
measurement); it keeps white noise at −60 dBFS RMS dark at block 512.

`aggregation` sets how the FFT bins inside one bar combine into its level. Each method
has its own full-scale constant, computed when the layer is compiled, so a full-scale
sine still reads 1.0 and the floor keeps its dBFS meaning:

| Method | Bar level | Full-scale constant | Reads a full-scale tone as 1.0 |
| --- | --- | --- | --- |
| `mean` (default) | average of the bins' magnitudes | `2 / Σ window` | in a bar no wider than the tone's peak |
| `band_energy` | `sqrt(Σ magnitude²)` | `2 / sqrt(fft_size × Σ window²)` | in a bar wide enough to hold the tone's whole main lobe |
| `max` | the loudest bin | `2 / Σ window` | in any bar holding the tone's centre bin |

So with the same weighting curve, `mean` reads a tone lower in wide treble bars and
`band_energy` reads it lower in narrow bass bars (about −11 dB at the default
100–6300 Hz range, block 512), because the Hann window spreads a tone over several
narrow bars. None of the three changes with `fft_size`. At the default −65 dBFS
floor, white noise at −60 dBFS RMS stays dark with every method. A settings file
with another value fails to load.

The retired `noise_floor` (raw units, subtracted after weighting) has no exact dBFS
equivalent, so a settings file that still has it fails to load with a message naming
`noise_floor_dbfs`. A saved preset that has it loads with −65 dBFS, and the FFT page's
note names the dropped key.

`weight_points` has 1–16 points. `position` runs from 0 (the layer's `min_freq`) to
1 (its `max_freq`) on the same log scale as the bars, and `weight` (0–100) multiplies
the bars there. Between points the weight is linear in position; beyond the first
and last point it stays flat. Points are sorted by position, and two points can't
share a position. Each bar takes the curve's value at its center, and the weights are
computed once when a layer is compiled, so they cost nothing per frame.

The default approximates the retired `low_freq_weight=0.45`/`high_freq_weight=10.0`
curve (`low + (high - low) * position ** 1.5`) within 9% at every bar. A settings file
that still has `low_freq_weight` or `high_freq_weight` is converted the same way, by
sampling that curve at positions 0, 0.1, 0.3, 0.6, and 1, with a printed note. If
the file also has `weight_points`, those win and the old keys are ignored. The removed
`silence_threshold` setting is dropped with a note.

**Tips:**

- Raise `noise_floor_dbfs` (toward −20) if you see dancing bars with no audio
- Raise the weight of the last points if treble is too quiet
- Lower the weight of the first points if bass dominates everything

### ScalingSettings

```python
auto_level: bool = True       # Off: divide by a fixed reference (0.3) instead
headroom: float = 2.5         # Scale = level x headroom; higher = shorter bars
attack_seconds: float = 5.5   # Time constant for adapting to louder audio
release_seconds: float = 2.75 # Time constant for adapting to quieter audio
gain: float = 1.5             # Multiplies bar height (2 = twice as tall)
max_boost_db: float = 68.0    # Most auto level may raise quiet input, in dB
response_curve: float = 1.0   # Height = level ** curve after gain; 1 is linear
```

`response_curve` (0.25–4) raises each bar's height, after auto level and gain, to that
power. Below 1 lifts quiet bars toward loud ones (0.5 turns a quarter-height bar into
half height); above 1 makes loud bars stand out more (2 turns half height into a
quarter). Gain and the curve combine, so gain `g` then curve `γ` scales a bar by
`g^γ`. At exactly 1 the curve is skipped.

Auto level divides each bar by `sqrt(envelope) × headroom` but never by less than
`10^(−max_boost_db/20)` (in full-scale units), so it lifts quiet music by at most
`max_boost_db` dB. The default rounds the retired `min_scale` of 0.05 (68.1 dB at block 512).
With auto level off the divisor is the retired 0.3 converted to full-scale units at
block 512 (about −52.6 dBFS), so bars look as they did at the default block size.

These, `SmoothingSettings`, and the peak timing are the startup values for the
default layer. Each layer carries its own copies in its structure settings
(`auto_level`, `auto_level_headroom`, `auto_level_attack_seconds`,
`auto_level_release_seconds`, `auto_level_max_boost_db`, `gain`, `response_curve`,
`smoothing_rise_seconds`, `smoothing_fall_seconds`, `spread_radius`, `spread_falloff`,
`peak_hold_seconds`, `peak_fall_rate`), and the FFT page can copy any one of them to
every layer. None of them splits a projection.

The defaults approximate the retired rolling-RMS scaler at about 86 calls per
second. The retired keys `use_fixed_scale`, `use_rolling_rms_scale`,
`use_rolling_max_scale`, `fixed_scale_max`, `rolling_window_seconds`,
`headroom_multiplier`, `attack_speed`, `decay_speed`, `min_scale`, and
`initial_scale` make a settings file fail to load, because their per-call meanings
have no exact seconds equivalent. Remove them, or use the new keys.

`gain` is the startup value for the default layer; each layer carries its own `gain`
(0.1–10) in its structure settings. It replaces the retired `sensitivity_scalar`
(which divided, so higher was shorter) and `overflow.multiplier` (which applied only
to overflow drawing). A settings file that still has either key fails to load;
replace them with `gain = multiplier / sensitivity_scalar` for overflow layers, or
`1 / sensitivity_scalar` otherwise.

**Tips:**

- Lower `attack_seconds` (for example 1) if a sudden loud passage stays pegged too long
- Lower `release_seconds` if bars stay short too long after a loud passage
- Lower `headroom` for taller bars relative to the recent level, raise it for shorter
- Raise `gain` for taller bars (and more overflow), lower it for shorter ones
- Turn `auto_level` off for a fixed reference: quiet music stays short, loud music tall

### SmoothingSettings

```python
rise_seconds: float = 0.023  # Time constant toward a louder value (0 = instant)
fall_seconds: float = 0.11   # Time constant toward a quieter value (0 = instant)
spread_radius: int = 0       # Columns to each side a bar lifts (0 = off)
spread_falloff: float = 0.5  # Fraction of height kept per column of distance
```

Column spread runs after the response curve and before rise/fall smoothing. Each bar
lifts its neighbors within `spread_radius` columns (0–16) to at least its own height
times `spread_falloff` (0–1) per column of distance, so with radius 2 and falloff 0.5
a lone full-height bar shows as ¼, ½, 1, ½, ¼. It takes the larger value, so the
tallest bar and the level auto level reads never change, and it stops at the panel's
edges. Peak markers follow the spread bars.

The retired `rise` and `fall` were per-call fractions; a settings file with them
fails to load.

**Tips:**

- Lower `rise_seconds` = snappier response to beats
- Higher `fall_seconds` = smoother, more flowing decay
- Raise `spread_radius` (2–4) with `spread_falloff` around 0.5 for a fuller, rounder look

### OverflowSettings

```python
enabled: bool = True                    # Enable overflow mode
color_2: Tuple[int,int,int] = (0,150,255)   # 3rd layer color
color_3: Tuple[int,int,int] = (255,0,255)   # 4th+ layer color
```

**Tips:**

- Enable for loud electronic music, disable for quieter acoustic
- Raise the layer's `gain` for more dramatic overflow effects

### PeakSettings

```python
enabled: bool = False       # Show peak indicators
hold_seconds: float = 0.09  # Seconds to hold at peak
fall_rate: float = 7.0      # Fall after the hold, in panel heights per second
color_mode: str = 'contrast' # 'white', 'bar', 'contrast', 'peak'
```

The retired `fall_speed` and `hold_frames` were per-call values; a settings file with
them fails to load.

### ColorSettings

```python
theme: str = 'ocean'          # Theme name
brightness_boost: float = 1.0 # Overall brightness multiplier
```

## Using a JSON Settings File

Create a JSON file with your settings:

```json
{
  "audio": {
    "device": "iMM-6C",
    "sleep_delay": 0.004
  },
  "frequency": {
    "zoom_mode": true,
    "zoom_min_freq": 80,
    "zoom_max_freq": 8000
  },
  "color": {
    "theme": "warm",
    "brightness_boost": 1.0
  },
  "scaling": {
    "headroom": 2.5
  }
}
```

Then run with:

```bash
sudo python main.py --settings=my_settings.json --led-rows=64 ...
```

## Saved Presets

There are no built-in presets. The web control service stores named FFT presets on
the Pi, each holding launch parameters and a full layer structure, and launches one
through the normal `POST /api/v1/run`. See the controller's web-service operations
guide for the `/api/v1/presets` contract.

## Multiple Standard Views

The legacy `LayeredVisualizerSettings` and `LayerConfig` configuration has been
removed. Older JSON files may still contain a `layers` group; it is ignored rather
than activating a compatibility path.

Future multi-view support will configure multiple instances of the standard
analyzer/scaler/drawing path. No multi-view configuration schema is accepted yet.
