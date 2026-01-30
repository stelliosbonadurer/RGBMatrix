# FFT Visualizer for RGB LED Matrix

A real-time audio spectrum visualizer for RGB LED matrix panels on Raspberry Pi.

## Quick Start

```bash
# Basic usage
sudo python main.py --led-rows=64 --led-cols=64 --led-gpio-mapping=adafruit-hat --led-slowdown-gpio=2

# With specific theme
sudo python main.py --led-rows=64 --led-cols=64 --led-gpio-mapping=adafruit-hat --led-slowdown-gpio=2 --theme=ocean

# List available options
python main.py --list-themes
python main.py --list-visualizers
```

## Documentation

- [Configuration Guide](configuration.md) - All settings explained
- [Themes Guide](themes.md) - Available themes and how to create custom ones
- [Visualizers Guide](visualizers.md) - Display modes and creating new ones
- [Architecture Overview](architecture.md) - Code structure for developers
- [Troubleshooting](troubleshooting.md) - Common issues and solutions

## Features

- **Real-time FFT** - Audio spectrum analysis with configurable frequency ranges
- **Multiple Themes** - 13 built-in color themes (fire, ocean, rainbow, etc.)
- **Overflow Mode** - Bars stack with different colors when audio is loud
- **Peak Indicators** - Floating dots that show recent peak levels
- **Adaptive Scaling** - Automatic gain control for consistent visuals
- **Presets** - Quick configurations for different music genres

## Requirements

- Raspberry Pi (2, 3, 4, or Zero)
- RGB LED Matrix panel (tested with 64x64)
- Adafruit HAT/Bonnet or direct wiring
- USB audio input device (microphone)
- Python 3.7+
- numpy, sounddevice libraries

## Project Structure

```
fft_program/
├── main.py              # Entry point
├── config/              # Settings and presets
├── core/                # Audio processing, scaling, matrix control
├── themes/              # Color themes
├── visualizers/         # Display modes
├── utils/               # Helper functions
└── docs/                # Documentation
```
