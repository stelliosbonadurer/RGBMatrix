# Configuration Guide

All settings are defined in `config/settings.py` as dataclasses. You can modify defaults there or override via JSON settings file.

## Command Line Arguments

### Matrix Configuration

| Argument | Description | Default |
|----------|-------------|---------|
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
|----------|-------------|
| `--theme` | Color theme name |
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
noise_floor: float = 0.3       # Subtract from signal (0=sensitive, 0.5=cut noise)
low_freq_weight: float = 0.55  # Bass multiplier (<1=quieter, >1=louder)
high_freq_weight: float = 10.0 # Treble multiplier
silence_threshold: float = 0.0 # Fade to black below this
```

**Tips:**
- Increase `noise_floor` if you see dancing bars with no audio
- Increase `high_freq_weight` if treble is too quiet
- Decrease `low_freq_weight` if bass dominates everything

### ScalingSettings

```python
use_fixed_scale: bool = False       # Fixed sensitivity
use_rolling_rms_scale: bool = True  # Adaptive RMS (recommended)
use_rolling_max_scale: bool = False # Adaptive max

rolling_window_seconds: float = 3   # History for adaptation
headroom_multiplier: float = 2.5    # Higher = punchier peaks
attack_speed: float = 0.1           # Adapt to loud (0.1=slow, 0.5=instant)
decay_speed: float = 0.06           # Adapt to quiet (lower=slower)
min_scale: float = 0.05             # Prevent infinite gain in silence
sensitivity_scalar: float = 1.0     # Manual boost on top
```

**Tips:**
- `use_rolling_rms_scale=True` gives the best results for most music
- Increase `headroom_multiplier` for punchier, more dynamic visuals
- Decrease `attack_speed` if loud sounds cause jarring jumps
- Increase `decay_speed` if quiet sections feel sluggish

### SmoothingSettings

```python
rise: float = 0.8   # Bar rise speed (0.3=smooth, 1.0=instant)
fall: float = 0.20  # Bar fall speed (0.2=slow, 0.8=fast)
```

**Tips:**
- Higher `rise` = snappier response to beats
- Lower `fall` = smoother, more flowing decay
- For EDM: `rise=0.9, fall=0.3`
- For classical: `rise=0.6, fall=0.15`

### OverflowSettings

```python
enabled: bool = True                    # Enable overflow mode
multiplier: float = 1.5                 # Sensitivity (higher=more overflow)
color_2: Tuple[int,int,int] = (0,150,255)   # 3rd layer color
color_3: Tuple[int,int,int] = (255,0,255)   # 4th+ layer color
```

**Tips:**
- Enable for loud electronic music, disable for quieter acoustic
- Increase `multiplier` for more dramatic overflow effects

### PeakSettings

```python
enabled: bool = False       # Show peak indicators
fall_speed: float = 0.08    # Peak descent rate
hold_frames: int = 8        # Frames to hold at peak
color_mode: str = 'contrast' # 'white', 'bar', 'contrast', 'peak'
```

### ColorSettings

```python
theme: str = 'fire'           # Theme name
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
    "headroom_multiplier": 2.5
  }
}
```

Then run with:
```bash
sudo python main.py --settings=my_settings.json --led-rows=64 ...
```

## Using Presets

Built-in presets are available in `config/presets.py`:

- `default` - Balanced settings
- `bluegrass` - Warm colors, acoustic frequency range
- `edm` - Intense fire theme, heavy bass, overflow enabled
- `classical` - Ocean theme, smooth movement, wide dynamics
- `podcast` - Voice frequencies, simple green theme
