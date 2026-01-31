# FFT Visualizer for RGB LED Matrix

A real-time audio spectrum visualizer for RGB LED matrix panels on Raspberry Pi.

## Quick Start

```bash
# Basic usage
sudo python main.py --led-rows=64 --led-cols=64 --led-gpio-mapping=adafruit-hat --led-slowdown-gpio=2

# With specific theme
sudo python main.py --led-rows=64 --led-cols=64 --led-gpio-mapping=adafruit-hat --led-slowdown-gpio=2 --theme=ocean

# List available themes
python main.py --list-themes
```

## Runtime Controls

| Key | Action |
| --- | ------ |
| `t` / `T` | Cycle themes forward / backward |
| `g` | Toggle gradient mode (per-pixel vs uniform color) |
| `o` | Toggle overflow mode (bars can exceed height) |
| `b` | Toggle bars (OFF = peaks only) |
| `s` | Toggle shadow mode |
| `p` | Toggle peak mode |
| `P` | Cycle peak color: white → bar → contrast → peak |
| `Ctrl+C` | Quit |

## Documentation

- [Configuration Guide](configuration.md) - All settings explained
- [Themes Guide](themes.md) - Available themes and how to create custom ones
- [Visualizers Guide](visualizers.md) - Display modes and creating new ones
- [Architecture Overview](architecture.md) - Code structure for developers
- [Troubleshooting](troubleshooting.md) - Common issues and solutions

## Features

- **Real-time FFT** - Audio spectrum analysis with configurable frequency ranges
- **7 Color Themes** - warm, fire, ocean, forest, purple, sunset, rainbow
- **Overflow Mode** - Bars stack with different colors when audio is loud
- **Gradient Toggle** - Per-pixel gradients or uniform bar colors
- **Peak Indicators** - Floating dots that show recent peak levels
- **Shadow Mode** - Trailing fade effect on bars
- **Adaptive Scaling** - Automatic gain control for consistent visuals

## Requirements

- Raspberry Pi (2, 3, 4, or Zero)
- RGB LED Matrix panel (tested with 64x64)
- Adafruit HAT/Bonnet or direct wiring
- USB audio input device (microphone)
- Python 3.7+
- numpy, sounddevice libraries

## Project Structure

```text
fft_program/
├── main.py              # Entry point
├── config/              # Settings and presets
├── core/                # Audio processing, scaling, matrix control
├── themes/              # Color themes
├── visualizers/         # Display modes
├── utils/               # Helper functions
└── docs/                # Documentation
```
