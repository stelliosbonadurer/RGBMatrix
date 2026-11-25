#!/usr/bin/env python
"""
FFT Visualizer for RGB LED Matrix

Main entry point that orchestrates audio processing, visualization, and display.

Usage:
    sudo python main.py --led-rows=64 --led-cols=64 --led-gpio-mapping=adafruit-hat --led-slowdown-gpio=2

Settings can be customized via:
    - Command line arguments for matrix configuration and the initial layer
    - JSON settings file via --settings flag
    - Editing config/settings.py defaults

The layer structure changes only through the runtime protocol's ``structure.set``
action (the web controller or tools/fft_layers.py), or at launch through
``RGB_MATRIX_LAUNCH_STRUCTURE``. There are no keyboard controls. Ctrl+C quits.
"""
import sys
import os
import time
import signal
import threading
from typing import cast

# Ensure the fft_program package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Direct script execution normally places only fft_program/ on sys.path. Add the
# repository root so this legacy entry point can consume the same parameter contract
# as the web supervisor. A future packaging pass should replace both path adjustments
# with an installed package and module entry point.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import load_settings
from core import (
    ActiveLayerPlan,
    AnalyzerRegistry,
    AudioCapture,
    AudioProcessor,
    AudioSourceResolutionError,
    DIRECT_OUTPUT_PATH,
    MatrixApp,
    OUTPUT_PATH_ENVIRONMENT,
    compile_current_bars_layer_plan,
    compile_runtime_graph,
    current_bars_runtime_capabilities,
    current_bars_runtime_definition,
    current_bars_settings_from_runtime,
    resolve_audio_source,
)
from themes import list_themes
from matrix_controller.definitions import application_parameters
from matrix_controller.runtime_transport import (
    LAUNCH_STRUCTURE_ENVIRONMENT,
    FrameBoundaryRuntimeBridge,
    RuntimeControlClient,
)
from runtime_control import FftRuntimeController
from structural_control import PreparedStructuralPlan, StructuralRuntimeCoordinator
from core.telemetry import (
    TelemetryConfigurationError,
    add_telemetry_arguments,
    check_retired_environment,
    telemetry_from_args,
)


class ShutdownSignalHandler:
    """Translate service termination into normal Python cleanup.

    The default SIGTERM action exits immediately and can bypass the application's
    ``finally`` block. The supervisor uses SIGTERM for normal switching, so raising
    ``KeyboardInterrupt`` here deliberately follows the same tested cleanup path as
    Ctrl+C: audio closes and the matrix is cleared.
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


def print_startup_info(width: int, height: int, layer_settings: dict) -> None:
    """Print the matrix size and the initial layer's main settings."""
    print(f"\n{'='*50}")
    print(f"FFT Visualizer - {width}x{height} matrix")
    print(f"{'='*50}")
    print(
        f"Initial layer: theme={layer_settings['theme']}, "
        f"gradient={'ON' if layer_settings['gradient'] else 'OFF'}, "
        f"overflow={'ON' if layer_settings['overflow'] else 'OFF'}"
    )
    print("Layers change through the runtime structure API. [Ctrl+C] Quit")
    print(f"{'='*50}\n")


def main():
    """Main entry point for FFT visualizer."""
    # Initialize matrix application (handles argument parsing)
    app = MatrixApp()
    
    # Application options come from the shared controller contract. The audio
    # source is the only one (web-control-refactor D-050): the default layer's look
    # comes from config/settings.py or a --settings file.
    direct_defaults = {"audio_source": "usb-mic"}
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
    add_telemetry_arguments(app.parser)
    
    # Parse arguments
    args = app.process_args()
    
    # Handle list commands
    if args.list_themes:
        print("Available themes:")
        for theme_name in list_themes():
            print(f"  - {theme_name}")
        sys.exit(0)

    # Validate telemetry before touching audio or the matrix. Telemetry is
    # None unless --telemetry-out is given; configuration errors exit with code 2.
    output_path = os.environ.get(OUTPUT_PATH_ENVIRONMENT, DIRECT_OUTPUT_PATH)
    try:
        check_retired_environment(os.environ)
        telemetry = telemetry_from_args(args, output_mode=output_path)
    except TelemetryConfigurationError as exc:
        app.parser.error(str(exc))

    # Load settings from file or use defaults
    settings_path = getattr(args, 'settings', None)
    settings = load_settings(settings_path)

    # Resolve and validate the selected endpoint before the child initializes the
    # matrix. A bad Bluetooth selection therefore cannot take display ownership or
    # silently fall back to the microphone.
    try:
        settings.audio = resolve_audio_source(args.audio_source, settings.audio)
    except AudioSourceResolutionError as exc:
        app.parser.error(str(exc))

    # Initialize matrix
    if not app.initialize_matrix():
        app.print_help()
        sys.exit(1)

    initial_layer_settings = current_bars_settings_from_runtime(settings)
    print_startup_info(app.width, app.height, initial_layer_settings)

    # Initialize components
    try:
        initial_definition = current_bars_runtime_definition(
            audio_settings=settings.audio,
            frequency_settings=settings.frequency,
            sensitivity_settings=settings.sensitivity,
            num_bins=app.width,
            output_backend=output_path,
            layer_settings=initial_layer_settings,
        )
        capabilities = current_bars_runtime_capabilities()
        compiled_graph = compile_runtime_graph(initial_definition, capabilities)
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

        # Compile the default single layer and selected output executor once.
        # Each layer owns scaling, peaks, visual state, and backend execution; the
        # frame loop retains only direct plan calls and the existing NumPy lease.
        initial_layer_plan = compile_current_bars_layer_plan(
            compiled_graph,
            analyzer_registry,
            settings=settings,
            width=app.width,
            height=app.height,
        )
        active_layer_plan = ActiveLayerPlan(initial_layer_plan)
        # The default layer uses the primary projection, so this is the one
        # AudioProcessor that owns capture and the FFT (and that telemetry reads).
        audio = cast(AudioProcessor, initial_layer_plan.primary_analyzer)

        controller = FftRuntimeController(
            run_id=os.environ.get("RGB_MATRIX_RUN_ID", "local"),
        )
        runtime_client = RuntimeControlClient.from_environment()
        runtime_bridge = FrameBoundaryRuntimeBridge(
            max_pending=32,
            response_timeout=0.75,
        )
        def compile_structural_definition(definition):
            graph = compile_runtime_graph(definition, capabilities)
            prepared = analyzer_registry.begin_projection_prepare()
            try:
                plan = compile_current_bars_layer_plan(
                    graph,
                    analyzer_registry,
                    settings=settings,
                    width=app.width,
                    height=app.height,
                )
            except BaseException:
                prepared.rollback()
                raise
            return PreparedStructuralPlan(
                plan=plan,
                commit=prepared.commit,
                rollback=prepared.rollback,
            )

        structural_controller = StructuralRuntimeCoordinator(
            controller=controller,
            active_plan=active_layer_plan,
            initial_definition=initial_definition,
            capabilities=capabilities,
            compile_definition=compile_structural_definition,
            bridge=runtime_bridge,
        )
        runtime_stop_event = threading.Event()
        runtime_thread = None
        
    except Exception as e:
        print(f"Initialization error: {e}")
        sys.exit(1)
    
    # Main loop
    try:
        with ShutdownSignalHandler(), capture:
            time.sleep(0.5)  # Let audio stream settle
            # The launch structure needs the sample rate (Nyquist), so apply it
            # after setup but before the first frame and before the control
            # listener can start a transaction (D-016).
            launch_structure = os.environ.get(LAUNCH_STRUCTURE_ENVIRONMENT)
            if launch_structure is not None:
                if structural_controller.apply_launch_structure(launch_structure):
                    print("Applied the launch layer structure")
                else:
                    error = structural_controller.structure_snapshot()["launch_error"]
                    print(
                        "Launch structure rejected; running the default layer: "
                        f"{error['path']}: {error['message']}"
                    )
            if telemetry is not None:
                telemetry.arm(audio)
            if runtime_client is not None:
                def serve_runtime_controls() -> None:
                    """Keep control-channel failures isolated from rendering."""
                    while not runtime_stop_event.is_set():
                        try:
                            runtime_client.serve(
                                structural_controller.handle_runtime_operation,
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
            while True:
                if (
                    telemetry is not None
                    and telemetry.poll(audio)
                ):
                    break
                # IPC listeners only enqueue validated request envelopes.  Drain a
                # small bounded batch here so all visualizer/audio mutations happen
                # between frames on this render thread.
                structural_controller.process_pending(max_items=4)
                # Activate only a complete staged plan at this frame boundary, then
                # acquire each physical analyzer result once. Every visible layer
                # updates its own presentation state before the direct lease is
                # released; drawing uses the precompiled backend executor tuple.
                frame_plan = active_layer_plan.begin_frame()
                smoothed = frame_plan.prepare_frame()

                if smoothed is None:
                    time.sleep(0.001)
                    continue

                measure_frame = (
                    telemetry is not None
                    and telemetry.begin_frame(audio)
                )
                if telemetry is not None and telemetry.complete:
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
                    if telemetry.record_frame(
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


    except KeyboardInterrupt:
        if telemetry is not None:
            telemetry.finish(audio, reason="keyboard_interrupt")
        print("\nExiting...")
    except Exception as e:
        if telemetry is not None:
            telemetry.finish(audio, reason="runtime_error")
        print(f"Runtime error: {e}")
        raise
    finally:
        runtime_stop_event.set()
        structural_controller.close()
        if runtime_thread is not None:
            runtime_thread.join(timeout=1.0)
        telemetry_write_failure = None
        if telemetry is not None:
            try:
                telemetry.write(audio)
            except Exception as exc:
                print(f"Telemetry write failed: {exc}")
                telemetry_write_failure = exc
        app.clear()
        if telemetry_write_failure is not None and sys.exc_info()[0] is None:
            raise RuntimeError("telemetry write failed") from telemetry_write_failure


if __name__ == "__main__":
    main()
