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
import signal
import threading
from typing import cast
import numpy as np  # type: ignore

# Ensure the fft_program package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Direct script execution normally places only fft_program/ on sys.path. Add the
# repository root so this legacy entry point can consume the same parameter contract
# as the web supervisor. A future packaging pass should replace both path adjustments
# with an installed package and module entry point.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import Settings, load_settings
from core import (
    ActiveLayerPlan,
    AnalyzerRegistry,
    AudioCapture,
    AudioProcessor,
    DIRECT_OUTPUT_PATH,
    MatrixApp,
    OUTPUT_PATH_ENVIRONMENT,
    compile_current_bars_graph,
    compile_current_bars_layer_plan,
)
from themes import get_theme, list_themes
from visualizers import get_visualizer
from matrix_controller.definitions import application_parameters
from matrix_controller.runtime_transport import (
    FrameBoundaryRuntimeBridge,
    RuntimeControlClient,
)
from runtime_control import FftRuntimeController
from qualification_metrics import Step6QualificationMetrics


STATE_VERSION = 1
RUNTIME_STATE_DIRECTORY = os.environ.get(
    "RGBMATRIX_STATE_DIR",
    os.path.dirname(os.path.abspath(__file__)),
)
RUNTIME_STATE_PATH = os.path.join(RUNTIME_STATE_DIRECTORY, "runtime_state.json")


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
        # Services should set RGBMATRIX_STATE_DIR to a writable state directory such
        # as /var/lib/rgb-matrix-controller. Keeping directory creation here makes a
        # first boot deterministic while atomic replace protects against power loss.
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
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
    """Optional non-blocking keyboard input handler for Unix/macOS.

    A systemd child has no controlling terminal. Treating keyboard input as an
    optional adapter keeps direct CLI controls intact without making the render loop
    depend on a TTY.
    """
    
    def __init__(self, enabled: bool = True):
        self.enabled = enabled and sys.stdin.isatty()
        self.old_settings = None
    
    def __enter__(self):
        """Set terminal to raw mode for single keypress detection."""
        if self.enabled:
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
        if self.enabled and select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.read(1)
        return None


class ShutdownSignalHandler:
    """Translate service termination into normal Python cleanup.

    The default SIGTERM action exits immediately and can bypass the application's
    ``finally`` block. The supervisor uses SIGTERM for normal switching, so raising
    ``KeyboardInterrupt`` here deliberately follows the same tested cleanup path as
    Ctrl+C: audio closes, runtime state is saved, and the matrix is cleared.
    """

    def __init__(self):
        self.previous_sigterm = None

    def __enter__(self):
        self.previous_sigterm = signal.getsignal(signal.SIGTERM)

        def request_shutdown(_signum, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, request_shutdown)
        return self

    def __exit__(self, *_args):
        if self.previous_sigterm is not None:
            signal.signal(signal.SIGTERM, self.previous_sigterm)


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
    
    # Application options come from the shared controller contract. Direct execution
    # uses None for visual settings so a supplied JSON settings file remains the
    # source of truth unless the user explicitly passes an override.
    direct_defaults = {
        "theme": None,
        "gradient": None,
        "overflow": None,
        "non_interactive": False,
    }
    for parameter in application_parameters("fft"):
        parameter.add_to_argparse(
            app.parser,
            argument_default=direct_defaults[parameter.name],
        )
    app.add_argument(
        "--list-themes",
        action="store_true",
        help="List available themes and exit"
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
    gradient_enabled = bool(args.gradient) if args.gradient is not None else False
    if args.overflow is not None:
        settings.overflow.enabled = bool(args.overflow)

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

        numeric_restore = {
            'noise_floor': (settings.sensitivity, 'noise_floor', 0.0, 100.0),
            'low_freq_weight': (settings.sensitivity, 'low_freq_weight', 0.0, 100.0),
            'high_freq_weight': (settings.sensitivity, 'high_freq_weight', 0.0, 100.0),
            'silence_threshold': (settings.sensitivity, 'silence_threshold', 0.0, 100.0),
            'sensitivity_scalar': (settings.scaling, 'sensitivity_scalar', 0.01, 20.0),
            'smoothing_rise': (settings.smoothing, 'rise', 0.0, 1.0),
            'smoothing_fall': (settings.smoothing, 'fall', 0.0, 1.0),
            'overflow_multiplier': (settings.overflow, 'multiplier', 0.1, 10.0),
            'peak_fall_speed': (settings.peak, 'fall_speed', 0.0, 1.0),
            'peak_hold_frames': (settings.peak, 'hold_frames', 0, 600),
        }
        for name, (target, attribute, minimum, maximum) in numeric_restore.items():
            value = persisted_settings.get(name)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and np.isfinite(value)
                and minimum <= value <= maximum
            ):
                if name == 'peak_hold_frames' and not isinstance(value, int):
                    continue
                setattr(target, attribute, value)

        zoom_idx = persisted_settings.get('zoom_preset_index')
        if isinstance(zoom_idx, int) and settings.frequency.zoom_presets:
            settings.frequency.zoom_preset_index = zoom_idx % len(settings.frequency.zoom_presets)

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
        output_path = os.environ.get(OUTPUT_PATH_ENVIRONMENT, DIRECT_OUTPUT_PATH)

        compiled_graph = compile_current_bars_graph(
            audio_settings=settings.audio,
            frequency_settings=settings.frequency,
            sensitivity_settings=settings.sensitivity,
            num_bins=app.width,
            output_backend=output_path,
        )
        analyzer_registry = AnalyzerRegistry(
            compiled_graph,
            {
                compiled_graph.analyzers[0].analyzer_type: lambda _definition: AudioProcessor(
                    audio_settings=settings.audio,
                    freq_settings=settings.frequency,
                    sensitivity_settings=settings.sensitivity,
                    num_bins=app.width,
                )
            },
        )
        capture: AudioCapture = analyzer_registry
        
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

        # Assigned after optional persisted-state restoration. The closure keeps
        # runtime-state saves on the same authoritative controller used by keys and
        # IPC.
        controller = None

        def build_runtime_state() -> dict:
            """Capture current state through the in-process runtime controller."""
            if controller is None:
                raise RuntimeError("FFT runtime controller is not initialized")
            return controller.runtime_state()

        if reload_prior_state and prior_state is not None:
            apply_persisted_runtime_state(prior_state)
            print(f"Reloaded prior runtime state from: {RUNTIME_STATE_PATH}")

        # Compile the complete current bars layer and selected output executor once.
        # The layer owns scaling, peaks, visual state, and backend execution; the
        # frame loop retains only direct plan calls and the existing NumPy lease.
        initial_layer_plan = compile_current_bars_layer_plan(
            compiled_graph,
            analyzer_registry,
            visualizer=visualizer,
            settings=settings,
            width=app.width,
            height=app.height,
        )
        active_layer_plan = ActiveLayerPlan(initial_layer_plan)
        audio = cast(AudioProcessor, initial_layer_plan.primary_analyzer)
        unavailable_command_paths = (
            initial_layer_plan.incompatible_runtime_paths
        )

        qualification_metrics = Step6QualificationMetrics.from_environment(
            output_mode=output_path,
        )

        controller = FftRuntimeController(
            settings=settings,
            visualizer=visualizer,
            audio=audio,
            theme_cycler=theme_cycler,
            width=app.width,
            height=app.height,
            run_id=os.environ.get("RGB_MATRIX_RUN_ID", "local"),
            unavailable_command_paths=unavailable_command_paths,
        )
        runtime_client = RuntimeControlClient.from_environment()
        runtime_bridge = FrameBoundaryRuntimeBridge(
            max_pending=32,
            response_timeout=0.75,
        )
        runtime_stop_event = threading.Event()
        runtime_thread = None
        
    except Exception as e:
        print(f"Initialization error: {e}")
        sys.exit(1)
    
    # Main loop
    try:
        keyboard_enabled = not args.non_interactive
        with ShutdownSignalHandler(), capture, KeyboardHandler(enabled=keyboard_enabled) as keyboard:
            time.sleep(0.5)  # Let audio stream settle
            if qualification_metrics is not None:
                qualification_metrics.arm(audio)
            if runtime_client is not None:
                def serve_runtime_controls() -> None:
                    """Keep control-channel failures isolated from rendering."""
                    while not runtime_stop_event.is_set():
                        try:
                            runtime_client.serve(
                                runtime_bridge.handle,
                                stop_event=runtime_stop_event,
                            )
                        except Exception as exc:
                            # The panel and audio remain useful even if the optional
                            # phone-control connection disappears. Retry because the
                            # supervisor keeps the per-run listener alive.
                            if not runtime_stop_event.is_set():
                                print(f"Runtime control disconnected: {exc}")
                        runtime_stop_event.wait(0.25)

                runtime_thread = threading.Thread(
                    target=serve_runtime_controls,
                    name="fft-runtime-control",
                    daemon=True,
                )
                runtime_thread.start()
            last_state_save_time = 0.0
            state_save_interval = 2.0
            while True:
                if (
                    qualification_metrics is not None
                    and qualification_metrics.poll(audio)
                ):
                    break
                # IPC listeners only enqueue validated request envelopes.  Drain a
                # small bounded batch here so all visualizer/audio mutations happen
                # between frames on this render thread.
                runtime_bridge.process_pending(
                    controller.handle_runtime_operation,
                    max_items=4,
                )
                # Check for keyboard input
                key = keyboard.get_key()
                state_dirty = key is not None

                # Keyboard input is an adapter for the same semantic controller
                # used by the child IPC listener at frame boundaries.
                if key is not None:
                    message = controller.handle_key(key)
                    if message:
                        print(message)
                # Activate only a complete staged plan at this frame boundary, then
                # acquire each physical analyzer result once. Every visible layer
                # updates its own presentation state before the direct lease is
                # released; drawing uses the precompiled backend executor tuple.
                frame_plan = active_layer_plan.begin_frame()
                smoothed = frame_plan.prepare_frame()

                if smoothed is None:
                    now = time.time()
                    if (
                        qualification_metrics is None
                        and (
                            state_dirty
                            or (now - last_state_save_time) >= state_save_interval
                        )
                    ):
                        save_runtime_state(build_runtime_state())
                        last_state_save_time = now
                    time.sleep(0.001)
                    continue

                measure_frame = (
                    qualification_metrics is not None
                    and qualification_metrics.begin_frame(audio)
                )
                if qualification_metrics is not None and qualification_metrics.complete:
                    break
                if measure_frame:
                    analysis_sequence = audio.last_acquired_analysis_sequence
                    analysis_publish_time_ns = (
                        audio.last_acquired_analysis_publish_time_ns
                    )
                    draw_started_ns = time.perf_counter_ns()

                # Draw the exact precompiled visible order. Direct clears the native
                # canvas once; pillow-bulk clears its reusable image and uploads once.
                frame_plan.draw_frame(app.canvas)
                if measure_frame:
                    draw_completed_ns = time.perf_counter_ns()
                
                # Swap buffers
                if measure_frame:
                    swap_started_ns = time.perf_counter_ns()
                app.swap_canvas()
                if measure_frame:
                    swap_completed_ns = time.perf_counter_ns()
                    if qualification_metrics.record_frame(
                        audio=audio,
                        smoothed_bars=smoothed,
                        height=app.height,
                        overflow_multiplier=settings.overflow.multiplier,
                        draw_started_ns=draw_started_ns,
                        draw_completed_ns=draw_completed_ns,
                        swap_started_ns=swap_started_ns,
                        swap_completed_ns=swap_completed_ns,
                        analysis_sequence=analysis_sequence,
                        analysis_publish_time_ns=analysis_publish_time_ns,
                    ):
                        break
                
                # Frame delay
                time.sleep(settings.audio.sleep_delay)

                now = time.time()
                if (
                    qualification_metrics is None
                    and (
                        state_dirty
                        or (now - last_state_save_time) >= state_save_interval
                    )
                ):
                    save_runtime_state(build_runtime_state())
                    last_state_save_time = now
                
    except KeyboardInterrupt:
        if qualification_metrics is not None:
            qualification_metrics.finish(audio, reason="keyboard_interrupt")
        print("\nExiting...")
    except Exception as e:
        if qualification_metrics is not None:
            qualification_metrics.finish(audio, reason="runtime_error")
        print(f"Runtime error: {e}")
        raise
    finally:
        runtime_stop_event.set()
        runtime_bridge.close()
        if runtime_thread is not None:
            runtime_thread.join(timeout=1.0)
        if qualification_metrics is None:
            try:
                save_runtime_state(build_runtime_state())
            except Exception:
                pass
        qualification_write_failure = None
        if qualification_metrics is not None:
            try:
                qualification_metrics.write(audio)
            except Exception as exc:
                print(f"Qualification metrics write failed: {exc}")
                qualification_write_failure = exc
        app.clear()
        if qualification_write_failure is not None and sys.exc_info()[0] is None:
            raise RuntimeError("qualification metrics write failed") from qualification_write_failure


if __name__ == "__main__":
    main()
