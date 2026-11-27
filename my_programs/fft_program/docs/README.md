# FFT Visualizer for RGB LED Matrix

A real-time audio spectrum visualizer for RGB LED matrix panels on Raspberry Pi.

## Quick Start

```bash
# Basic usage
sudo python main.py --led-rows=64 --led-cols=64 --led-gpio-mapping=adafruit-hat --led-slowdown-gpio=2

# With a settings file for the default layer
sudo python main.py --led-rows=64 --led-cols=64 --led-gpio-mapping=adafruit-hat --led-slowdown-gpio=2 --settings my_settings.json

# List available themes
python main.py --list-themes
```

## Runtime Controls

The layer structure is the only runtime configuration. There are no keyboard
controls and no per-setting live controls, and nothing is saved between runs.

The managed service publishes the current `structure` in its runtime snapshot:
the output backend, the ordered layers with all of their settings, the analyzer's
launch settings, and a capability catalog of every setting's type, bounds, and
default. To change anything, an authenticated client sends the whole desired
structure with the revision-checked `structure.set` action:

```json
{"output_backend": "direct",
 "layers": [{"layer_id": "bass", "layer_type": "current-bars",
             "analyzer_id": "main-audio", "visible": true,
             "settings": {"theme": "ocean", "min_freq": 40.0, "...": "..."}}]}
```

The child validates the whole document. It then activates it between two frames
or rejects it whole, with a path such as `structure.layers[2].settings.max_freq`.
Up to eight layers are allowed. A layer whose `layer_id` survives a submit keeps
its auto-level state, smoothed bars, and peaks, even when its settings change; a new
or renamed layer starts fresh. A launch through `POST /api/v1/run` may carry the same document as
`structure`. If that document is invalid, the program runs the default single
`current-bars` layer and reports the reason as `structure.launch_error` in the
snapshot. Without a launch structure, startup is one `current-bars` layer using
`direct`, built from the settings file (or the `config/settings.py` defaults). Of the
options shared with the web controller, only `--audio-source` remains; each layer's
theme, gradient, and overflow are settings in the structure.

For a reusable client of that same web-facing contract, run from the repository
root:

```bash
python3 tools/fft_layers.py --layers 4 --dry-run
python3 tools/fft_layers.py --layers 8 --backend pillow-bulk
```

See the tool's `--help` for controller URL and token-file options. Press `Ctrl+C`
to quit a direct run.

## Documentation

- [Configuration Guide](configuration.md) - All settings explained
- [Themes Guide](themes.md) - Available themes and how to create custom ones
- [Visualizers Guide](visualizers.md) - Display modes and creating new ones
- [Architecture Overview](architecture.md) - Code structure for developers
- [Web Control Panel Plan](web_control_panel_plan.md) - Standalone performance-first plan for future GUI controls
- [Mobile Onboarding Strategy](mobile_onboarding_strategy.md) - Phone-friendly control UX and phone-only customer setup plan
- [FFT Math Explained](fft_math_explained.md) - Mathematical foundations
- [Optimization Notes](optimization_notes.md) - Performance lessons learned
- [Troubleshooting](troubleshooting.md) - Common issues and solutions
- [Bluetooth Audio](bluetooth_audio.md) - Install, pair, operate, verify, and remove the phone-input bridge

## Features

- **Real-time FFT** - Audio spectrum analysis with configurable frequency ranges
- **13 Color Themes** - ocean (default), warm, forest, autumn, rainbow, two dynamic themes, and six `vice_*` Miami themes ([themes.md](themes.md))
- **Overflow Mode** - Bars stack with different colors when audio is loud
- **Gradient Toggle** - Per-pixel gradients or uniform bar colors
- **Peak Indicators** - Floating dots that show recent peak levels
- **Shadow Mode** - Trailing fade effect on bars
- **Adaptive Scaling** - Automatic gain control for consistent visuals
- **Bounded Analysis Worker** - Publishes only the newest complete 64-bin result

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
├── main.py              # Entry point, launch structure, and render loop
├── config/              # Settings and weight-point helpers
│   └── settings.py      # Typed application settings
├── core/                # Audio processing, scaling, matrix control
│   ├── audio.py         # Capture and bounded FFT-analysis worker
│   ├── analysis_plan.py # Compiled FFT/projection configuration
│   └── scaling.py       # Per-layer auto level, gain, smoothing, peaks (seconds)
├── themes/              # Color themes
├── visualizers/         # Display modes
│   └── bars_unified.py  # Direct-canvas bars visualizer
├── utils/               # Helper functions
└── docs/                # Documentation
```

The modular runtime supports multiple ordered instances of the standard bars layer.
Every layer's complete settings include `min_freq`, `max_freq`, `noise_floor_dbfs`, and
`weight_points` (see [configuration](configuration.md#sensitivitysettings)); bars are
in full-scale units, with the floor in dBFS. Level settings, including max auto boost,
are per layer (see [configuration](configuration.md#scalingsettings)).
Equivalent settings share one projected
result; different ranges are projected from the same microphone block and the same
full FFT instead of opening another capture stream. Every layer retains independent
presentation state. Layer structure is runtime-only; restarting the visualization
rebuilds the one-layer direct-output default.
