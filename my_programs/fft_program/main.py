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

Runtime Controls:
    t / T - Cycle themes forward / backward
    m / M - Cycle visualizer modes forward / backward
    Ctrl+C - Quit
"""
import sys
import os
import time
import select
import tty
import termios

# Ensure the fft_program package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Settings, load_settings
from core import MatrixApp, AudioProcessor, ScalingProcessor
from themes import get_theme, list_themes
from visualizers import get_visualizer, list_visualizers


class KeyboardHandler:
    """Non-blocking keyboard input handler for Unix/macOS."""
    
    def __init__(self):
        self.old_settings = None
    
    def __enter__(self):
        """Set terminal to raw mode for single keypress detection."""
        self.old_settings = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())
        return self
    
    def __exit__(self, *args):
        """Restore terminal settings."""
        if self.old_settings:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.old_settings)
    
    def get_key(self) -> str | None:
        """
        Check for keypress without blocking.
        
        Returns:
            Single character if key pressed, None otherwise
        """
        if select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.read(1)
        return None


class ThemeCycler:
    """Manages cycling through available themes."""
    
    def __init__(self, initial_theme: str, brightness_boost: float = 1.0):
        self.themes = list_themes()
        self.brightness_boost = brightness_boost
        try:
            self.current_index = self.themes.index(initial_theme)
        except ValueError:
            self.current_index = 0
    
    @property
    def current_theme_name(self) -> str:
        return self.themes[self.current_index]
    
    def next_theme(self):
        """Cycle to the next theme."""
        self.current_index = (self.current_index + 1) % len(self.themes)
        return self._get_current_theme()
    
    def prev_theme(self):
        """Cycle to the previous theme."""
        self.current_index = (self.current_index - 1) % len(self.themes)
        return self._get_current_theme()
    
    def _get_current_theme(self):
        """Get the current theme instance."""
        return get_theme(self.current_theme_name, brightness_boost=self.brightness_boost)


class VisualizerCycler:
    """Manages cycling through available visualizers."""
    
    def __init__(self, initial_visualizer: str, width: int, height: int, settings):
        self.visualizers = list_visualizers()
        self.width = width
        self.height = height
        self.settings = settings
        try:
            self.current_index = self.visualizers.index(initial_visualizer)
        except ValueError:
            self.current_index = 0
    
    @property
    def current_visualizer_name(self) -> str:
        return self.visualizers[self.current_index]
    
    def next_visualizer(self):
        """Cycle to the next visualizer."""
        self.current_index = (self.current_index + 1) % len(self.visualizers)
        return self._get_current_visualizer()
    
    def prev_visualizer(self):
        """Cycle to the previous visualizer."""
        self.current_index = (self.current_index - 1) % len(self.visualizers)
        return self._get_current_visualizer()
    
    def _get_current_visualizer(self):
        """Get the current visualizer instance."""
        return get_visualizer(
            self.current_visualizer_name,
            self.width,
            self.height,
            self.settings
        )


def print_startup_info(width: int, height: int, theme: str, visualizer: str, shadow: bool, peak: bool):
    """Print startup information and controls."""
    print(f"\n{'='*50}")
    print(f"FFT Visualizer - {width}x{height} matrix")
    print(f"{'='*50}")
    
    print(f"\nCurrent: theme={theme}, mode={visualizer}, shadow={'ON' if shadow else 'OFF'}, peak={'ON' if peak else 'OFF'}")
    
    print(f"\n[t/T] Themes ({len(list_themes())} available):")
    themes = list_themes()
    # Print themes in rows of 4
    for i in range(0, len(themes), 4):
        row = themes[i:i+4]
        print(f"    {', '.join(row)}")
    
    print(f"\n[m/M] Modes ({len(list_visualizers())} available):")
    for viz in list_visualizers():
        print(f"    {viz}")
    
    print(f"\n[s] Toggle shadow mode")
    print(f"[p] Toggle peak mode")
    print(f"\n[Ctrl+C] Quit")
    print(f"{'='*50}\n")


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
    
    # Print startup info with all options
    print_startup_info(app.width, app.height, settings.color.theme, visualizer_name, settings.shadow.enabled, settings.peak.enabled)
    
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
        
        # Theme cycler for runtime switching
        theme_cycler = ThemeCycler(
            settings.color.theme,
            brightness_boost=settings.color.brightness_boost
        )
        
        # Visualizer cycler for runtime switching
        viz_cycler = VisualizerCycler(
            visualizer_name,
            app.width,
            app.height,
            settings
        )
        
    except Exception as e:
        print(f"Initialization error: {e}")
        sys.exit(1)
    
    # Main loop
    try:
        with audio, KeyboardHandler() as keyboard:
            time.sleep(0.5)  # Let audio stream settle
            
            while True:
                # Check for keyboard input
                key = keyboard.get_key()
                if key == 't':
                    # Next theme
                    new_theme = theme_cycler.next_theme()
                    visualizer.set_theme(new_theme)
                    print(f"Theme: {theme_cycler.current_theme_name}")
                elif key == 'T':
                    # Previous theme
                    new_theme = theme_cycler.prev_theme()
                    visualizer.set_theme(new_theme)
                    print(f"Theme: {theme_cycler.current_theme_name}")
                elif key == 'm':
                    # Next visualizer
                    visualizer = viz_cycler.next_visualizer()
                    visualizer.set_theme(theme_cycler._get_current_theme())
                    print(f"Mode: {viz_cycler.current_visualizer_name}")
                elif key == 'M':
                    # Previous visualizer
                    visualizer = viz_cycler.prev_visualizer()
                    visualizer.set_theme(theme_cycler._get_current_theme())
                    print(f"Mode: {viz_cycler.current_visualizer_name}")
                elif key == 's':
                    # Toggle shadow mode
                    settings.shadow.enabled = not settings.shadow.enabled
                    # Re-initialize shadow buffers in visualizer
                    if settings.shadow.enabled:
                        import numpy as np
                        visualizer.shadow_buffer = np.zeros((app.width, app.height), dtype=np.float32)
                        visualizer.shadow_colors = np.zeros((app.width, app.height, 3), dtype=np.uint8)
                    else:
                        visualizer.shadow_buffer = None
                        visualizer.shadow_colors = None
                    print(f"Shadow: {'ON' if settings.shadow.enabled else 'OFF'}")
                elif key == 'p':
                    # Toggle peak mode
                    settings.peak.enabled = not settings.peak.enabled
                    print(f"Peak: {'ON' if settings.peak.enabled else 'OFF'}")
                
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
