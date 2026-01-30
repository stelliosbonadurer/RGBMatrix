#!/usr/bin/env python
"""
FFT Visualizer for RGB LED Matrix

Main entry point that orchestrates audio processing, visualization, and display.

Usage:
    sudo python main.py --led-rows=64 --led-cols=64 --led-gpio-mapping=adafruit-hat --led-slowdown-gpio=2

Settings can be customized via:
    - Command line arguments for matrix configuration
    - JSON settings file via --settings flag
    - Editing config/settings.py defaults
"""
import sys
import os
import time

# Ensure the fft_program package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Settings, load_settings
from core import MatrixApp, AudioProcessor, ScalingProcessor
from themes import get_theme, list_themes
from visualizers import get_visualizer, list_visualizers


def main():
    """Main entry point for FFT visualizer."""
    # Initialize matrix application (handles argument parsing)
    app = MatrixApp()
    
    # Add custom arguments
    app.add_argument(
        "--visualizer",
        help=f"Visualizer type. Available: {', '.join(list_visualizers())}",
        default=None,
        type=str
    )
    app.add_argument(
        "--theme",
        help=f"Color theme. Available: {', '.join(list_themes())}",
        default=None,
        type=str
    )
    app.add_argument(
        "--list-themes",
        action="store_true",
        help="List available themes and exit"
    )
    app.add_argument(
        "--list-visualizers",
        action="store_true",
        help="List available visualizers and exit"
    )
    
    # Parse arguments
    args = app.process_args()
    
    # Handle list commands
    if args.list_themes:
        print("Available themes:")
        for theme_name in list_themes():
            print(f"  - {theme_name}")
        sys.exit(0)
    
    if args.list_visualizers:
        print("Available visualizers:")
        for viz_name in list_visualizers():
            print(f"  - {viz_name}")
        sys.exit(0)
    
    # Load settings from file or use defaults
    settings_path = getattr(args, 'settings', None)
    settings = load_settings(settings_path)
    
    # Override settings from command line if provided
    if args.theme:
        settings.color.theme = args.theme
    
    # Determine visualizer based on settings
    if args.visualizer:
        visualizer_name = args.visualizer
    elif settings.overflow.enabled:
        visualizer_name = "bars_overflow"
    else:
        visualizer_name = "bars"
    
    # Initialize matrix
    if not app.initialize_matrix():
        app.print_help()
        sys.exit(1)
    
    print(f"Matrix size: {app.width}x{app.height}")
    print(f"Theme: {settings.color.theme}")
    print(f"Visualizer: {visualizer_name}")
    
    # Initialize components
    try:
        # Audio processor
        audio = AudioProcessor(
            audio_settings=settings.audio,
            freq_settings=settings.frequency,
            sensitivity_settings=settings.sensitivity,
            num_bins=app.width
        )
        
        # Scaling processor
        scaler = ScalingProcessor(
            scaling_settings=settings.scaling,
            sensitivity_settings=settings.sensitivity,
            smoothing_settings=settings.smoothing,
            num_bins=app.width,
            frame_rate=1.0 / settings.audio.sleep_delay
        )
        
        # Visualizer
        visualizer = get_visualizer(
            visualizer_name,
            app.width,
            app.height,
            settings
        )
        
        # Theme
        theme = get_theme(
            settings.color.theme,
            brightness_boost=settings.color.brightness_boost
        )
        visualizer.set_theme(theme)
        
    except Exception as e:
        print(f"Initialization error: {e}")
        sys.exit(1)
    
    # Main loop
    try:
        with audio:
            time.sleep(0.5)  # Let audio stream settle
            
            while True:
                # Get FFT data
                bars = audio.get_fft_magnitudes()
                
                if bars is None:
                    time.sleep(0.001)
                    continue
                
                # Process through scaler
                normalized, smoothed, peaks = scaler.process(
                    bars,
                    peak_hold_frames=settings.peak.hold_frames,
                    peak_fall_speed=settings.peak.fall_speed
                )
                
                # Draw visualization
                visualizer.draw(
                    app.canvas,
                    smoothed,
                    peaks if settings.peak.enabled else None
                )
                
                # Swap buffers
                app.swap_canvas()
                
                # Frame delay
                time.sleep(settings.audio.sleep_delay)
                
    except KeyboardInterrupt:
        print("\nExiting...")
    except Exception as e:
        print(f"Runtime error: {e}")
        raise
    finally:
        app.clear()


if __name__ == "__main__":
    main()
