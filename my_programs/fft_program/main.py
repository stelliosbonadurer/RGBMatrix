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
import json
import numpy as np  # type: ignore

# Ensure the fft_program package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Settings, load_settings
from core import MatrixApp, AudioProcessor, ScalingProcessor
from themes import get_theme, list_themes
from visualizers import get_visualizer, draw_peaks


STATE_VERSION = 1
RUNTIME_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime_state.json")


def _extract_dynamic_theme_state(theme) -> dict:
    """Extract dynamic-theme runtime parameters if supported by this theme."""
    if theme is None or not hasattr(theme, 'cycle_speed'):
        return {}

    return {
        'start_hue': getattr(theme, 'start_hue', None),
        'cycle_speed': getattr(theme, 'cycle_speed', None),
        'saturation': getattr(theme, 'saturation', None),
        'value': getattr(theme, 'value', None),
        'column_spread': getattr(theme, 'column_spread', None),
        'height_spread': getattr(theme, 'height_spread', None),
        'frozen': getattr(theme, 'frozen', False),
    }


def _apply_dynamic_theme_state(theme, dynamic_state: dict) -> None:
    """Apply serialized dynamic-theme runtime parameters to a theme instance."""
    if theme is None or not isinstance(dynamic_state, dict) or not hasattr(theme, 'cycle_speed'):
        return

    start_hue = dynamic_state.get('start_hue', None)
    if hasattr(theme, 'reseed_start_hue'):
        theme.reseed_start_hue(start_hue)
    elif start_hue is not None:
        theme.start_hue = start_hue % 1.0

    for key in ('cycle_speed', 'saturation', 'value', 'column_spread', 'height_spread'):
        value = dynamic_state.get(key)
        if value is not None and hasattr(theme, key):
            setattr(theme, key, value)

    target_frozen = bool(dynamic_state.get('frozen', False))
    if hasattr(theme, 'toggle_frozen') and bool(getattr(theme, 'frozen', False)) != target_frozen:
        theme.toggle_frozen()


def load_runtime_state(path: str = RUNTIME_STATE_PATH) -> dict | None:
    """Load persisted runtime state from disk."""
    if not os.path.exists(path):
        return None

    try:
        with open(path, 'r', encoding='utf-8') as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Warning: could not read prior runtime state: {e}")
        return None

    if state.get('version') != STATE_VERSION:
        return None
    return state


def save_runtime_state(state: dict, path: str = RUNTIME_STATE_PATH) -> None:
    """Persist runtime state to disk using atomic replace."""
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, path)
    except OSError as e:
        print(f"Warning: could not save runtime state: {e}")


def should_reload_prior_state(prior_state: dict | None) -> bool:
    """Prompt user to reload prior runtime state if available."""
    if prior_state is None:
        return False

    if not sys.stdin.isatty():
        return False

    try:
        answer = input("Prior runtime state found. Reload it? [y/N]: ").strip().lower()
        return answer in {'y', 'yes'}
    except EOFError:
        return False


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
    
    print(f"\nMain Controls:")
    print(f"[g] Toggle gradient mode (per-pixel vs uniform color)")
    print(f"[o] Toggle overflow mode (bars can exceed height)")
    print(f"[l] Toggle layered mode (multi-layer visualization)")
    print(f"\n[1/2/3] Select layer to edit (when layered mode on)")
    print(f"[!/@/#] Toggle layer 1/2/3 visibility (Shift+1/2/3)")
    print(f"[</>] Move layer back/forward in draw order")
    print(f"\n[b] Toggle bars (OFF = peaks only)")
    print(f"[s] Toggle shadow mode")
    print(f"[p] Toggle peak mode")
    print(f"[P] Cycle peak color: white → bar → contrast → peak")
    print(f"[r/R] Cycle zoom presets (frequency range)")

    print(f"\nDynamic Theme Controls (active theme):")
    print(f"[] / [ ] Dynamic speed -/+ ")
    print(f"[c] Reseed dynamic start color")
    print(f"[x] Freeze/unfreeze dynamic animation")

    print(f"\nAdvanced Visual Test Modes:")
    print(f"[f] Toggle full mode (full screen with crossfade)")
    print(f"[d] Toggle debug mode (static gradient test)")
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

    # Optional crash-resume state restore
    prior_state = load_runtime_state()
    reload_prior_state = should_reload_prior_state(prior_state)
    if reload_prior_state and prior_state is not None:
        persisted_settings = prior_state.get('settings', {})
        persisted_visualizer = prior_state.get('visualizer', {})

        if 'shadow_enabled' in persisted_settings:
            settings.shadow.enabled = bool(persisted_settings['shadow_enabled'])
        if 'peak_enabled' in persisted_settings:
            settings.peak.enabled = bool(persisted_settings['peak_enabled'])
        if 'peak_color_mode' in persisted_settings:
            settings.peak.color_mode = persisted_settings['peak_color_mode']

        zoom_idx = persisted_settings.get('zoom_preset_index')
        if isinstance(zoom_idx, int) and settings.frequency.zoom_presets:
            settings.frequency.zoom_preset_index = zoom_idx % len(settings.frequency.zoom_presets)

        if 'layers_enabled' in persisted_visualizer:
            settings.layers.enabled = bool(persisted_visualizer['layers_enabled'])

        persisted_theme = persisted_visualizer.get('single_theme_name')
        if isinstance(persisted_theme, str) and persisted_theme:
            settings.color.theme = persisted_theme
    
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
        
        layer_scalers = []

        def ensure_layer_pipeline() -> None:
            """Ensure audio/layer visualizer/scalers are initialized for layered mode."""
            nonlocal layer_scalers

            if not audio.layers_enabled:
                audio.setup_layers(settings.layers.layers)

            if not visualizer.layer_states:
                visualizer.setup_layers(
                    settings.layers.layers,
                    brightness_boost=settings.color.brightness_boost
                )

            if len(layer_scalers) != len(settings.layers.layers):
                layer_scalers = []
                for layer_config in settings.layers.layers:
                    layer_scaler = ScalingProcessor(
                        scaling_settings=settings.scaling,
                        sensitivity_settings=settings.sensitivity,
                        smoothing_settings=settings.smoothing,
                        num_bins=layer_config.bins,
                        frame_rate=1.0 / settings.audio.sleep_delay
                    )
                    layer_scalers.append(layer_scaler)

        if settings.layers.enabled:
            ensure_layer_pipeline()

        def apply_persisted_runtime_state(state: dict) -> None:
            """Apply persisted visualizer/runtime state to initialized components."""
            persisted_settings = state.get('settings', {})
            persisted_visualizer = state.get('visualizer', {})

            if 'peak_color_mode' in persisted_settings:
                settings.peak.color_mode = persisted_settings['peak_color_mode']

            # Global/single-mode visualizer flags
            for attr in ('gradient_mode', 'overflow_mode', 'bars_enabled', 'full_mode', 'debug_mode'):
                if attr in persisted_visualizer and hasattr(visualizer, attr):
                    setattr(visualizer, attr, bool(persisted_visualizer[attr]))

            target_layers_enabled = bool(persisted_visualizer.get('layers_enabled', visualizer.layers_enabled))

            if target_layers_enabled:
                ensure_layer_pipeline()
                visualizer.layers_enabled = True

                layer_states_data = persisted_visualizer.get('layer_states', [])
                for idx, state_data in enumerate(layer_states_data):
                    if idx >= len(visualizer.layer_states):
                        break

                    theme_name = state_data.get('theme_name')
                    if isinstance(theme_name, str):
                        visualizer.select_layer(idx)
                        visualizer.set_layer_theme(
                            theme_name,
                            brightness_boost=settings.color.brightness_boost
                        )

                    layer_state = visualizer.layer_states[idx]
                    for attr in ('bars_enabled', 'gradient_enabled', 'overflow_enabled', 'peak_enabled', 'visible'):
                        if attr in state_data:
                            setattr(layer_state, attr, bool(state_data[attr]))

                    if 'boost' in state_data:
                        layer_state.boost = float(state_data['boost'])

                    _apply_dynamic_theme_state(layer_state.theme, state_data.get('dynamic', {}))

                draw_order = persisted_visualizer.get('draw_order')
                if isinstance(draw_order, list) and len(draw_order) == len(visualizer.layer_states):
                    visualizer.draw_order = [int(i) for i in draw_order]

                active_layer = persisted_visualizer.get('active_layer')
                if isinstance(active_layer, int):
                    visualizer.select_layer(active_layer)
            else:
                visualizer.layers_enabled = False

            single_theme_name = persisted_visualizer.get('single_theme_name')
            if isinstance(single_theme_name, str):
                restored_theme = get_theme(
                    single_theme_name,
                    brightness_boost=settings.color.brightness_boost
                )
                visualizer.set_theme(restored_theme)
                _apply_dynamic_theme_state(restored_theme, persisted_visualizer.get('single_dynamic', {}))

                if single_theme_name in theme_cycler.themes:
                    theme_cycler.current_index = theme_cycler.themes.index(single_theme_name)

        def build_runtime_state() -> dict:
            """Capture the current runtime/session state as a serializable dict."""
            layer_states = []
            if visualizer.layer_states:
                for state_obj in visualizer.layer_states:
                    layer_states.append({
                        'theme_name': state_obj.theme_name,
                        'bars_enabled': state_obj.bars_enabled,
                        'gradient_enabled': state_obj.gradient_enabled,
                        'overflow_enabled': state_obj.overflow_enabled,
                        'peak_enabled': state_obj.peak_enabled,
                        'visible': state_obj.visible,
                        'boost': state_obj.boost,
                        'dynamic': _extract_dynamic_theme_state(state_obj.theme),
                    })

            single_theme_name = settings.color.theme
            if visualizer.theme is not None and hasattr(visualizer.theme, 'name'):
                single_theme_name = visualizer.theme.name

            return {
                'version': STATE_VERSION,
                'timestamp': time.time(),
                'settings': {
                    'shadow_enabled': settings.shadow.enabled,
                    'peak_enabled': settings.peak.enabled,
                    'peak_color_mode': settings.peak.color_mode,
                    'zoom_preset_index': settings.frequency.zoom_preset_index,
                },
                'visualizer': {
                    'layers_enabled': visualizer.layers_enabled,
                    'gradient_mode': getattr(visualizer, 'gradient_mode', False),
                    'overflow_mode': getattr(visualizer, 'overflow_mode', False),
                    'bars_enabled': getattr(visualizer, 'bars_enabled', True),
                    'full_mode': getattr(visualizer, 'full_mode', False),
                    'debug_mode': getattr(visualizer, 'debug_mode', False),
                    'active_layer': getattr(visualizer, 'active_layer', 0),
                    'draw_order': list(getattr(visualizer, 'draw_order', [])),
                    'single_theme_name': single_theme_name,
                    'single_dynamic': _extract_dynamic_theme_state(visualizer.theme),
                    'layer_states': layer_states,
                }
            }

        if reload_prior_state and prior_state is not None:
            apply_persisted_runtime_state(prior_state)
            print(f"Reloaded prior runtime state from: {RUNTIME_STATE_PATH}")
        
    except Exception as e:
        print(f"Initialization error: {e}")
        sys.exit(1)
    
    # Main loop
    try:
        with audio, KeyboardHandler() as keyboard:
            time.sleep(0.5)  # Let audio stream settle
            last_state_save_time = 0.0
            state_save_interval = 2.0
            
            while True:
                # Check for keyboard input
                key = keyboard.get_key()
                state_dirty = key is not None

                def get_active_theme_instance():
                    if visualizer.layers_enabled and visualizer.layer_states:
                        return visualizer.layer_states[visualizer.active_layer].theme
                    return visualizer.theme

                def get_active_layer_prefix() -> str:
                    if visualizer.layers_enabled and visualizer.layer_states:
                        return f"Layer {visualizer.active_layer + 1} "
                    return ""

                if key == 't':
                    # Next theme (for active layer if layered mode)
                    new_theme = theme_cycler.next_theme()
                    if visualizer.layers_enabled:
                        visualizer.set_layer_theme(
                            theme_cycler.current_theme_name,
                            brightness_boost=settings.color.brightness_boost
                        )
                        info = visualizer.get_active_layer_info()
                        print(f"Layer {info['index']+1} theme: {theme_cycler.current_theme_name}")
                    else:
                        visualizer.set_theme(new_theme)
                        print(f"Theme: {theme_cycler.current_theme_name}")
                elif key == 'T':
                    # Previous theme (for active layer if layered mode)
                    new_theme = theme_cycler.prev_theme()
                    if visualizer.layers_enabled:
                        visualizer.set_layer_theme(
                            theme_cycler.current_theme_name,
                            brightness_boost=settings.color.brightness_boost
                        )
                        info = visualizer.get_active_layer_info()
                        print(f"Layer {info['index']+1} theme: {theme_cycler.current_theme_name}")
                    else:
                        visualizer.set_theme(new_theme)
                        print(f"Theme: {theme_cycler.current_theme_name}")
                elif key == 'g':
                    # Toggle gradient mode (for active layer if layered mode)
                    gradient_on = visualizer.toggle_gradient()
                    if visualizer.layers_enabled:
                        info = visualizer.get_active_layer_info()
                        print(f"Layer {info['index']+1} gradient: {'ON' if gradient_on else 'OFF'}")
                    else:
                        print(f"Gradient: {'ON (per-pixel)' if gradient_on else 'OFF (uniform)'}")
                elif key == 'o':
                    # Toggle overflow mode (for active layer if layered mode)
                    overflow_on = visualizer.toggle_overflow()
                    if visualizer.layers_enabled:
                        info = visualizer.get_active_layer_info()
                        print(f"Layer {info['index']+1} overflow: {'ON' if overflow_on else 'OFF'}")
                    else:
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
                    # Toggle bars for active layer (to see peaks only)
                    bars_on = visualizer.toggle_bars()
                    if visualizer.layers_enabled:
                        info = visualizer.get_active_layer_info()
                        print(f"Layer {info['index']+1} bars: {'ON' if bars_on else 'OFF'}")
                    else:
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
                elif key == 'l':
                    # Toggle layered mode
                    if hasattr(visualizer, 'toggle_layers'):
                        layers_on = visualizer.toggle_layers()
                        settings.layers.enabled = layers_on
                        print(f"Layered mode: {'ON' if layers_on else 'OFF'}")
                        
                        # Setup layers if not already done
                        if layers_on:
                            ensure_layer_pipeline()
                        
                        if layers_on:
                            info = visualizer.get_active_layer_info()
                            print(f"  Editing layer {info['index']+1}: {info['theme']}")
                elif key == '1':
                    # Select layer 1 (index 0)
                    if visualizer.layers_enabled:
                        visualizer.select_layer(0)
                        info = visualizer.get_active_layer_info()
                        print(f"Editing layer 1: {info['theme']} (g={info['gradient']}, o={info['overflow']})")
                elif key == '2':
                    # Select layer 2 (index 1)
                    if visualizer.layers_enabled:
                        visualizer.select_layer(1)
                        info = visualizer.get_active_layer_info()
                        print(f"Editing layer 2: {info['theme']} (g={info['gradient']}, o={info['overflow']})")
                elif key == '!':
                    # Toggle layer 1 visibility (Shift+1)
                    if visualizer.layers_enabled:
                        visible = visualizer.toggle_layer_visibility(0)
                        print(f"Layer 1 visibility: {'ON' if visible else 'OFF'}")
                elif key == '@':
                    # Toggle layer 2 visibility (Shift+2)
                    if visualizer.layers_enabled:
                        visible = visualizer.toggle_layer_visibility(1)
                        print(f"Layer 2 visibility: {'ON' if visible else 'OFF'}")
                elif key == '3':
                    # Select layer 3 (index 2)
                    if visualizer.layers_enabled:
                        if visualizer.select_layer(2):
                            info = visualizer.get_active_layer_info()
                            print(f"Editing layer 3: {info['theme']} (g={info['gradient']}, o={info['overflow']})")
                        else:
                            print("Layer 3 not available")
                elif key == '#':
                    # Toggle layer 3 visibility (Shift+3)
                    if visualizer.layers_enabled:
                        visible = visualizer.toggle_layer_visibility(2)
                        if visible is not False:
                            print(f"Layer 3 visibility: {'ON' if visible else 'OFF'}")
                        else:
                            print("Layer 3 not available")
                elif key == '<':
                    # Move active layer toward background (drawn earlier)
                    if visualizer.layers_enabled:
                        result = visualizer.swap_draw_order(-1)
                        if result:
                            old_pos, new_pos = result
                            info = visualizer.get_active_layer_info()
                            print(f"Layer '{info['theme']}' moved to z-position {new_pos} (toward background)")
                        else:
                            print("Layer already at background")
                elif key == '>':
                    # Move active layer toward foreground (drawn later)
                    if visualizer.layers_enabled:
                        result = visualizer.swap_draw_order(+1)
                        if result:
                            old_pos, new_pos = result
                            info = visualizer.get_active_layer_info()
                            print(f"Layer '{info['theme']}' moved to z-position {new_pos} (toward foreground)")
                        else:
                            print("Layer already at foreground")
                elif key == 's':
                    # Toggle shadow mode
                    settings.shadow.enabled = not settings.shadow.enabled
                    # Re-initialize shadow buffers in visualizer
                    if settings.shadow.enabled:
                        visualizer.shadow_buffer = np.zeros((app.width, app.height), dtype=np.float32)
                        visualizer.shadow_colors = np.zeros((app.width, app.height, 3), dtype=np.uint8)
                    else:
                        visualizer.shadow_buffer = None
                        visualizer.shadow_colors = None
                    print(f"Shadow: {'ON' if settings.shadow.enabled else 'OFF'}")
                elif key == 'p':
                    # Toggle peak mode (for active layer if layered mode)
                    if visualizer.layers_enabled:
                        peak_on = visualizer.toggle_peak()
                        info = visualizer.get_active_layer_info()
                        print(f"Layer {info['index']+1} peak: {'ON' if peak_on else 'OFF'}")
                    else:
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
                elif key == '[':
                    active_theme = get_active_theme_instance()
                    if hasattr(active_theme, 'adjust_cycle_speed'):
                        new_speed = active_theme.adjust_cycle_speed(-0.01)
                        print(f"{get_active_layer_prefix()}dynamic speed: {new_speed:.3f} cycles/sec")
                    else:
                        print(f"{get_active_layer_prefix()}theme is not dynamic")
                elif key == ']':
                    active_theme = get_active_theme_instance()
                    if hasattr(active_theme, 'adjust_cycle_speed'):
                        new_speed = active_theme.adjust_cycle_speed(0.01)
                        print(f"{get_active_layer_prefix()}dynamic speed: {new_speed:.3f} cycles/sec")
                    else:
                        print(f"{get_active_layer_prefix()}theme is not dynamic")
                elif key == 'c':
                    active_theme = get_active_theme_instance()
                    if hasattr(active_theme, 'reseed_start_hue'):
                        new_hue = active_theme.reseed_start_hue()
                        print(f"{get_active_layer_prefix()}dynamic reseed: start_hue={new_hue:.3f}")
                    else:
                        print(f"{get_active_layer_prefix()}theme is not dynamic")
                elif key == 'x':
                    active_theme = get_active_theme_instance()
                    if hasattr(active_theme, 'toggle_frozen'):
                        is_frozen = active_theme.toggle_frozen()
                        print(f"{get_active_layer_prefix()}dynamic: {'FROZEN' if is_frozen else 'RUNNING'}")
                    else:
                        print(f"{get_active_layer_prefix()}theme is not dynamic")
                
                # Get FFT data
                bars = audio.get_fft_magnitudes()
                layer_bars_raw = None
                
                # Get layer data if enabled
                if audio.layers_enabled and visualizer.layers_enabled:
                    layer_bars_raw = audio.get_layer_magnitudes()
                
                if bars is None and layer_bars_raw is None:
                    now = time.time()
                    if state_dirty or (now - last_state_save_time) >= state_save_interval:
                        save_runtime_state(build_runtime_state())
                        last_state_save_time = now
                    time.sleep(0.001)
                    continue
                
                # Process through scaler (for single-layer mode or fallback)
                normalized, smoothed, peaks = scaler.process(
                    bars if bars is not None else np.zeros(app.width),
                    peak_hold_frames=settings.peak.hold_frames,
                    peak_fall_speed=settings.peak.fall_speed
                )
                
                # Process each layer through its own scaler (skip invisible layers)
                smoothed_layers = None
                layer_peaks = None
                if layer_bars_raw is not None and layer_scalers and visualizer.layers_enabled:
                    smoothed_layers = []
                    layer_peaks = []
                    for i, (raw_bars, layer_scaler) in enumerate(zip(layer_bars_raw, layer_scalers)):
                        # Skip processing if layer is not visible
                        if i < len(visualizer.layer_states) and not visualizer.layer_states[i].visible:
                            # Append None placeholders to maintain index alignment
                            smoothed_layers.append(None)
                            layer_peaks.append(None)
                            continue
                        
                        # Apply boost from layer config
                        boost = settings.layers.layers[i].boost
                        boosted = raw_bars * boost
                        
                        # Process through layer's scaler (also tracks peaks)
                        _, layer_smoothed, layer_peak = layer_scaler.process(
                            boosted,
                            peak_hold_frames=settings.peak.hold_frames,
                            peak_fall_speed=settings.peak.fall_speed
                        )
                        smoothed_layers.append(np.clip(layer_smoothed, 0, 1))
                        layer_peaks.append(layer_peak)
                
                # Draw visualization
                if smoothed_layers is not None and visualizer.layers_enabled:
                    visualizer.draw(
                        app.canvas,
                        smoothed,
                        None,
                        layer_bars=smoothed_layers,
                        layer_peaks=layer_peaks
                    )
                else:
                    visualizer.draw(
                        app.canvas,
                        smoothed,
                        None
                    )
                
                # Draw peaks as independent overlay (only for non-layered mode)
                if settings.peak.enabled and not visualizer.layers_enabled:
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

                now = time.time()
                if state_dirty or (now - last_state_save_time) >= state_save_interval:
                    save_runtime_state(build_runtime_state())
                    last_state_save_time = now
                
    except KeyboardInterrupt:
        print("\nExiting...")
    except Exception as e:
        print(f"Runtime error: {e}")
        raise
    finally:
        try:
            save_runtime_state(build_runtime_state())
        except Exception:
            pass
        app.clear()


if __name__ == "__main__":
    main()
