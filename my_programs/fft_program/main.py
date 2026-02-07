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
    g     - Toggle gradient mode (per-pixel vs uniform color)
    o     - Toggle overflow mode (bars can exceed height)
    s     - Toggle shadow mode
    p     - Toggle peak mode
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
from visualizers import get_visualizer, draw_peaks


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


def print_startup_info(width: int, height: int, theme: str, visualizer: str, shadow: bool, peak: bool, gradient: bool, overflow: bool):
    """Print startup information and controls."""
    print(f"\n{'='*50}")
    print(f"FFT Visualizer - {width}x{height} matrix")
    print(f"{'='*50}")
    
    print(f"\nCurrent: theme={theme}, shadow={'ON' if shadow else 'OFF'}, peak={'ON' if peak else 'OFF'}")
    print(f"         gradient={'ON' if gradient else 'OFF'}, overflow={'ON' if overflow else 'OFF'}")
    
    print(f"\n[t/T] Themes ({len(list_themes())} available):")
    themes = list_themes()
    # Print themes in rows of 4
    for i in range(0, len(themes), 4):
        row = themes[i:i+4]
        print(f"    {', '.join(row)}")
    
    print(f"\n[g] Toggle gradient mode (per-pixel vs uniform color)")
    print(f"[o] Toggle overflow mode (bars can exceed height)")
    print(f"[b] Toggle bars (OFF = peaks only)")
    print(f"[s] Toggle shadow mode")
    print(f"[p] Toggle peak mode")
    print(f"[P] Cycle peak color: white → bar → contrast → peak")
    print(f"\n[Ctrl+C] Quit")
    print(f"{'='*50}\n")


def main():
    """Main entry point for FFT visualizer."""
    # Initialize matrix application (handles argument parsing)
    app = MatrixApp()
    
    # Add custom arguments
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
        "--gradient",
        action="store_true",
        help="Start with gradient mode enabled (per-pixel colors)"
    )
    app.add_argument(
        "--overflow",
        action="store_true",
        help="Start with overflow mode enabled (bars can exceed height)"
    )
    
    # Parse arguments
    args = app.process_args()
    
    # Handle list commands
    if args.list_themes:
        print("Available themes:")
        for theme_name in list_themes():
            print(f"  - {theme_name}")
        sys.exit(0)
    
    # Load settings from file or use defaults
    settings_path = getattr(args, 'settings', None)
    settings = load_settings(settings_path)
    
    # Override settings from command line if provided
    if args.theme:
        settings.color.theme = args.theme
    
    # Override mode settings from command line
    gradient_enabled = getattr(args, 'gradient', False)
    if args.overflow:
        settings.overflow.enabled = True
    
    # Always use the unified visualizer
    visualizer_name = "bars"
    
    # Initialize matrix
    if not app.initialize_matrix():
        app.print_help()
        sys.exit(1)
    
    # Print startup info with all options
    print_startup_info(
        app.width, app.height, settings.color.theme, visualizer_name,
        settings.shadow.enabled, settings.peak.enabled,
        gradient_enabled, settings.overflow.enabled
    )
    
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
        
        # Apply command line mode overrides
        if gradient_enabled:
            visualizer.gradient_mode = True
        
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
                elif key == 'g':
                    # Toggle gradient mode
                    gradient_on = visualizer.toggle_gradient()
                    print(f"Gradient: {'ON (per-pixel)' if gradient_on else 'OFF (uniform)'}")
                elif key == 'o':
                    # Toggle overflow mode
                    overflow_on = visualizer.toggle_overflow()
                    print(f"Overflow: {'ON' if overflow_on else 'OFF'}")
                elif key == 'r':
                    # Next zoom preset
                    preset = settings.frequency.next_zoom_preset()
                    audio.update_frequency_range()
                    print(f"Zoom preset: {preset[0]} - {preset[1]} Hz")
                elif key == 'R':
                    # Previous zoom preset
                    preset = settings.frequency.prev_zoom_preset()
                    audio.update_frequency_range()
                    print(f"Zoom preset: {preset[0]} - {preset[1]} Hz")
                elif key == 'b':
                    # Toggle bars (to see peaks only)
                    bars_on = visualizer.toggle_bars()
                    print(f"Bars: {'ON' if bars_on else 'OFF (peaks only)'}")
                elif key == 'f':
                    # Toggle full mode (if visualizer supports it)
                    if hasattr(visualizer, 'toggle_full'):
                        full_on = visualizer.toggle_full()
                        print(f"Full: {'ON (gradient scaled by FFT)' if full_on else 'OFF'}")
                elif key == 'd':
                    # Toggle debug mode (if visualizer supports it)
                    if hasattr(visualizer, 'toggle_debug'):
                        debug_on = visualizer.toggle_debug()
                        print(f"Debug: {'ON (static gradient)' if debug_on else 'OFF'}")
                elif key == 's':
                    # Toggle shadow mode
                    settings.shadow.enabled = not settings.shadow.enabled
                    # Re-initialize shadow buffers in visualizer
                    if settings.shadow.enabled:
                        import numpy as np # type: ignore
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
                elif key == 'P':
                    # Cycle peak color mode
                    peak_modes = ['white', 'bar', 'contrast', 'peak']
                    current_idx = peak_modes.index(settings.peak.color_mode)
                    new_idx = (current_idx + 1) % len(peak_modes)
                    settings.peak.color_mode = peak_modes[new_idx]
                    mode_descriptions = {
                        'white': 'white (always white)',
                        'bar': 'bar (matches bar color)',
                        'contrast': 'contrast (inverted bar color)',
                        'peak': 'peak (color at max height)'
                    }
                    print(f"Peak color: {mode_descriptions[settings.peak.color_mode]}")
                
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
                    None  # Peaks are drawn separately now
                )
                
                # Draw peaks as independent overlay
                if settings.peak.enabled:
                    draw_peaks(
                        app.canvas,
                        peaks,
                        theme_cycler._get_current_theme(),
                        settings,
                        app.height
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
