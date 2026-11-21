"""Opt-in parent/renderer orchestration for the bounded matrix experiment."""

from __future__ import annotations

import multiprocessing
import os
import threading
import time
import traceback
from typing import Any, Optional

from core.analyzer_registry import AnalyzerRegistry
from core.audio import AudioProcessor
from core.layer_plan import (
    ActiveLayerPlan,
    compile_current_bars_graph,
    compile_current_bars_layer_plan,
)
from core.matrix_app import MatrixApp
from core.matrix_process import (
    MATRIX_PROCESS_RESULT_SHAPE,
    SharedAnalyzerCounters,
    SharedAnalyzerView,
    SharedLatestAnalysisTransport,
    StaticAnalyzerResolver,
    drop_privileges_like_matrix,
    receive_lifecycle_message,
    send_lifecycle_message,
)
from core.telemetry import telemetry_from_args
from matrix_controller.runtime_transport import (
    RuntimeControlClient,
    RuntimeControlError,
)
from themes import get_theme
from visualizers import get_visualizer


RENDERER_READY_TIMEOUT_SECONDS = 10.0
RENDERER_JOIN_TIMEOUT_SECONDS = 2.0
RENDERER_FORCE_JOIN_TIMEOUT_SECONDS = 1.0


class MatrixProcessRuntimeError(RuntimeError):
    """The provisional split topology could not complete cleanly."""


def _apply_dynamic_theme_state(theme: Any, dynamic_state: Any) -> None:
    if (
        theme is None
        or not isinstance(dynamic_state, dict)
        or not hasattr(theme, "cycle_speed")
    ):
        return
    start_hue = dynamic_state.get("start_hue")
    if hasattr(theme, "reseed_start_hue"):
        theme.reseed_start_hue(start_hue)
    elif start_hue is not None:
        theme.start_hue = start_hue % 1.0
    for key in (
        "cycle_speed",
        "saturation",
        "value",
        "column_spread",
        "height_spread",
    ):
        value = dynamic_state.get(key)
        if value is not None and hasattr(theme, key):
            setattr(theme, key, value)
    target_frozen = bool(dynamic_state.get("frozen", False))
    if (
        hasattr(theme, "toggle_frozen")
        and bool(getattr(theme, "frozen", False)) != target_frozen
    ):
        theme.toggle_frozen()


def _apply_persisted_visual_state(
    visualizer: Any,
    settings: Any,
    prior_state: Optional[dict[str, Any]],
) -> None:
    if prior_state is None:
        return
    persisted = prior_state.get("visualizer", {})
    if not isinstance(persisted, dict):
        return
    for attribute in (
        "gradient_mode",
        "overflow_mode",
        "bars_enabled",
        "full_mode",
        "debug_mode",
    ):
        if attribute in persisted and hasattr(visualizer, attribute):
            setattr(visualizer, attribute, bool(persisted[attribute]))
    theme_name = persisted.get("single_theme_name")
    if isinstance(theme_name, str) and theme_name:
        theme = get_theme(
            theme_name,
            brightness_boost=settings.color.brightness_boost,
        )
        visualizer.set_theme(theme)
        _apply_dynamic_theme_state(theme, persisted.get("single_dynamic", {}))


def _renderer_process_main(
    args: Any,
    settings: Any,
    gradient_enabled: bool,
    output_path: str,
    graph: Any,
    prior_state: Optional[dict[str, Any]],
    transport: SharedLatestAnalysisTransport,
    counters: SharedAnalyzerCounters,
    lifecycle: Any,
) -> None:
    """Own the native matrix, presentation, backend, swaps, and frame pacing."""

    app: Optional[MatrixApp] = None
    analyzer_view: Optional[SharedAnalyzerView] = None
    telemetry = None
    telemetry_armed = False
    clean_exit = False
    try:
        # Rebuild the recorder in its owner process. Parent-side construction has
        # already validated the same CLI values before spawning.
        telemetry = telemetry_from_args(args, output_mode=output_path)

        app = MatrixApp()
        app.args = args
        if not app.initialize_matrix():
            raise MatrixProcessRuntimeError("renderer could not initialize matrix")
        if app.width != MATRIX_PROCESS_RESULT_SHAPE[0]:
            raise MatrixProcessRuntimeError(
                "matrix-process experiment requires exactly 64 matrix columns"
            )

        visualizer = get_visualizer("bars", app.width, app.height, settings)
        if gradient_enabled:
            visualizer.gradient_mode = True
        theme = get_theme(
            settings.color.theme,
            brightness_boost=settings.color.brightness_boost,
        )
        visualizer.set_theme(theme)
        _apply_persisted_visual_state(visualizer, settings, prior_state)

        analyzer_view = SharedAnalyzerView(
            transport,
            counters,
            block_size=settings.audio.block_size,
        )
        active_plan = ActiveLayerPlan(
            compile_current_bars_layer_plan(
                graph,
                StaticAnalyzerResolver(analyzer_view),
                visualizer=visualizer,
                settings=settings,
                width=app.width,
                height=app.height,
            )
        )
        send_lifecycle_message(
            lifecycle,
            {
                "type": "ready",
                "phase": "renderer",
                "pid": os.getpid(),
                "width": app.width,
                "height": app.height,
            },
        )

        if not lifecycle.poll(RENDERER_READY_TIMEOUT_SECONDS):
            raise MatrixProcessRuntimeError("audio parent did not acknowledge readiness")
        start_message = receive_lifecycle_message(lifecycle)
        if start_message.get("type") == "shutdown":
            clean_exit = True
            return
        if not (
            start_message.get("type") == "ready"
            and start_message.get("phase") == "audio"
        ):
            raise MatrixProcessRuntimeError("unexpected renderer startup message")

        if telemetry is not None:
            telemetry.arm(analyzer_view)
            telemetry_armed = True

        while True:
            # Exactly one nonblocking lifecycle poll per frame boundary. Receiving
            # from a ready pipe also detects EOF without a second liveness query.
            if lifecycle.poll(0):
                message = receive_lifecycle_message(lifecycle)
                if message.get("type") == "shutdown":
                    clean_exit = True
                    break
                raise MatrixProcessRuntimeError("unexpected renderer lifecycle message")

            if telemetry is not None and telemetry.poll(analyzer_view):
                send_lifecycle_message(
                    lifecycle,
                    {"type": "shutdown", "reason": "measurement_complete"},
                )
                # Rendering is complete, but the parent must stop analysis before
                # asking this process to clear and exit.
                if not lifecycle.poll(RENDERER_READY_TIMEOUT_SECONDS):
                    raise MatrixProcessRuntimeError("parent did not finish shutdown")
                message = receive_lifecycle_message(lifecycle)
                if message.get("type") != "shutdown":
                    raise MatrixProcessRuntimeError("unexpected shutdown message")
                clean_exit = True
                break

            frame_plan = active_plan.begin_frame()
            smoothed = frame_plan.prepare_frame()
            if smoothed is None:
                time.sleep(0.001)
                continue

            measure_frame = telemetry is not None and telemetry.begin_frame(analyzer_view)
            if telemetry is not None and telemetry.complete:
                continue
            if measure_frame:
                analysis_sequence = analyzer_view.last_acquired_analysis_sequence
                analysis_publish_time_ns = (
                    analyzer_view.last_acquired_analysis_publish_time_ns
                )
                draw_started_ns = time.perf_counter_ns()

            frame_plan.draw_frame(app.canvas)
            if measure_frame:
                draw_completed_ns = time.perf_counter_ns()
                swap_started_ns = time.perf_counter_ns()
            app.swap_canvas()
            if measure_frame:
                swap_completed_ns = time.perf_counter_ns()
                telemetry.record_frame(
                    audio=analyzer_view,
                    smoothed_bars=smoothed,
                    height=app.height,
                    overflow_multiplier=settings.overflow.multiplier,
                    draw_started_ns=draw_started_ns,
                    draw_completed_ns=draw_completed_ns,
                    swap_started_ns=swap_started_ns,
                    swap_completed_ns=swap_completed_ns,
                    analysis_sequence=analysis_sequence,
                    analysis_publish_time_ns=analysis_publish_time_ns,
                )

            time.sleep(settings.audio.sleep_delay)

    except (EOFError, BrokenPipeError, OSError) as exc:
        raise MatrixProcessRuntimeError("renderer lost its lifecycle parent") from exc
    except BaseException as exc:
        try:
            send_lifecycle_message(
                lifecycle,
                {
                    "type": "error",
                    "message": str(exc),
                    "traceback": traceback.format_exc(limit=8),
                },
            )
        except BaseException:
            pass
        raise
    finally:
        cleanup_error: Optional[BaseException] = None
        try:
            if telemetry is not None and telemetry_armed and analyzer_view is not None:
                if not telemetry.complete:
                    telemetry.finish(
                        analyzer_view,
                        reason="application_exit" if clean_exit else "runtime_error",
                    )
                telemetry.write(analyzer_view)
        except BaseException as exc:
            if clean_exit:
                cleanup_error = exc
        try:
            if app is not None:
                app.clear()
        except BaseException as exc:
            if clean_exit and cleanup_error is None:
                cleanup_error = exc
        finally:
            try:
                lifecycle.close()
            except BaseException as exc:
                if clean_exit and cleanup_error is None:
                    cleanup_error = exc
        if cleanup_error is not None:
            raise MatrixProcessRuntimeError("renderer cleanup failed") from cleanup_error


def _start_unavailable_runtime_controls(
    runtime_client: Optional[RuntimeControlClient],
) -> tuple[threading.Event, Optional[threading.Thread]]:
    stop_event = threading.Event()
    if runtime_client is None:
        return stop_event, None

    def unavailable(_operation: str, _payload: Any) -> Any:
        raise RuntimeControlError(
            "unavailable",
            "runtime controls are unavailable in matrix-process experiment mode",
        )

    def serve() -> None:
        while not stop_event.is_set():
            try:
                runtime_client.serve(unavailable, stop_event=stop_event)
            except Exception as exc:
                if not stop_event.is_set():
                    print(f"Runtime control disconnected: {exc}")
            stop_event.wait(0.25)

    thread = threading.Thread(
        target=serve,
        name="fft-runtime-control-unavailable",
        daemon=True,
    )
    thread.start()
    return stop_event, thread


def _stop_renderer(
    process: Any,
    lifecycle: Any,
    *,
    request_shutdown: bool,
) -> bool:
    """Reap one child and return whether forced cleanup was required."""

    if request_shutdown and process.is_alive():
        try:
            send_lifecycle_message(lifecycle, {"type": "shutdown"})
        except (BrokenPipeError, EOFError, OSError):
            pass
    process.join(RENDERER_JOIN_TIMEOUT_SECONDS)
    forced = process.is_alive()
    if forced:
        process.terminate()
        process.join(RENDERER_FORCE_JOIN_TIMEOUT_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(RENDERER_FORCE_JOIN_TIMEOUT_SECONDS)
    return forced


def run_matrix_process_experiment(
    *,
    args: Any,
    settings: Any,
    gradient_enabled: bool,
    output_path: str,
    prior_state: Optional[dict[str, Any]],
    keyboard_handler_factory: Any,
) -> None:
    """Run the analysis parent and supervise exactly one spawned renderer."""

    runtime_client = RuntimeControlClient.from_environment()
    graph = compile_current_bars_graph(
        audio_settings=settings.audio,
        frequency_settings=settings.frequency,
        sensitivity_settings=settings.sensitivity,
        num_bins=MATRIX_PROCESS_RESULT_SHAPE[0],
        output_backend=output_path,
    )
    context = multiprocessing.get_context("spawn")
    transport = SharedLatestAnalysisTransport(
        context,
        MATRIX_PROCESS_RESULT_SHAPE[0],
    )
    counters = SharedAnalyzerCounters(context)
    parent_lifecycle, child_lifecycle = context.Pipe(duplex=True)
    process = context.Process(
        target=_renderer_process_main,
        name="fft-matrix-renderer",
        args=(
            args,
            settings,
            gradient_enabled,
            output_path,
            graph,
            prior_state,
            transport,
            counters,
            child_lifecycle,
        ),
    )

    registry: Optional[AnalyzerRegistry] = None
    audio: Optional[AudioProcessor] = None
    process_started = False
    runtime_stop = threading.Event()
    runtime_thread: Optional[threading.Thread] = None
    forced_cleanup = False
    primary_error: Optional[BaseException] = None
    try:
        process.start()
        process_started = True
        child_lifecycle.close()
        if not parent_lifecycle.poll(RENDERER_READY_TIMEOUT_SECONDS):
            raise MatrixProcessRuntimeError("renderer did not become ready within 10 seconds")
        ready = receive_lifecycle_message(parent_lifecycle)
        if ready.get("type") == "error":
            raise MatrixProcessRuntimeError(
                f"renderer startup failed: {ready.get('message', 'unknown error')}"
            )
        if not (
            ready.get("type") == "ready"
            and ready.get("phase") == "renderer"
        ):
            raise MatrixProcessRuntimeError("renderer sent an unexpected startup message")
        if ready.get("width") != MATRIX_PROCESS_RESULT_SHAPE[0]:
            raise MatrixProcessRuntimeError("renderer reported an unsupported matrix width")

        if args.drop_privileges:
            drop_privileges_like_matrix()

        registry = AnalyzerRegistry(
            graph,
            {
                graph.analyzers[0].analyzer_type: lambda _definition: AudioProcessor(
                    audio_settings=settings.audio,
                    freq_settings=settings.frequency,
                    sensitivity_settings=settings.sensitivity,
                    num_bins=MATRIX_PROCESS_RESULT_SHAPE[0],
                    analysis_transport=transport,
                    counter_mirror=(
                        counters
                        if getattr(args, "telemetry_out", None) is not None
                        else None
                    ),
                )
            },
        )
        audio = registry.resolve(graph.analyzers[0])
        registry.setup()
        registry.start()
        send_lifecycle_message(
            parent_lifecycle,
            {"type": "ready", "phase": "audio"},
        )

        runtime_stop, runtime_thread = _start_unavailable_runtime_controls(
            runtime_client
        )
        keyboard_enabled = not args.non_interactive
        with keyboard_handler_factory(enabled=keyboard_enabled) as keyboard:
            while True:
                if parent_lifecycle.poll(0.05):
                    message = receive_lifecycle_message(parent_lifecycle)
                    message_type = message.get("type")
                    if (
                        message_type == "shutdown"
                        and message.get("reason") == "measurement_complete"
                    ):
                        break
                    if message_type == "error":
                        raise MatrixProcessRuntimeError(
                            f"renderer failed: {message.get('message', 'unknown error')}"
                        )
                    raise MatrixProcessRuntimeError(
                        f"unexpected renderer lifecycle message {message_type!r}"
                    )
                if not process.is_alive():
                    process.join()
                    raise MatrixProcessRuntimeError(
                        f"renderer exited unexpectedly with status {process.exitcode}"
                    )
                audio.check_health()
                key = keyboard.get_key()
                if key is not None:
                    print(
                        "Runtime controls are unavailable in matrix-process "
                        "experiment mode"
                    )
    except KeyboardInterrupt:
        print("\nExiting...")
    except BaseException as exc:
        primary_error = exc
    finally:
        runtime_stop.set()
        if runtime_thread is not None:
            runtime_thread.join(timeout=1.0)
        if registry is not None:
            try:
                registry.stop()
            except BaseException as exc:
                if primary_error is None:
                    primary_error = exc
        if process_started:
            forced_cleanup = _stop_renderer(
                process,
                parent_lifecycle,
                request_shutdown=True,
            )
        parent_lifecycle.close()
        child_lifecycle.close()
        if (
            process_started
            and process.exitcode not in (0, None)
            and primary_error is None
        ):
            primary_error = MatrixProcessRuntimeError(
                f"renderer exited with status {process.exitcode}"
            )
        if process_started:
            process.close()

    if forced_cleanup:
        raise MatrixProcessRuntimeError("renderer required forced cleanup") from primary_error
    if primary_error is not None:
        raise primary_error


__all__ = [
    "MatrixProcessRuntimeError",
    "RENDERER_JOIN_TIMEOUT_SECONDS",
    "RENDERER_READY_TIMEOUT_SECONDS",
    "run_matrix_process_experiment",
]
