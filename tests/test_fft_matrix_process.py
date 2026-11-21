"""Hardware-free tests for the bounded matrix-process experiment seams."""

from __future__ import annotations

import multiprocessing
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock
import json

import numpy as np


FFT_ROOT = Path(__file__).resolve().parent.parent / "my_programs" / "fft_program"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))

_sounddevice = ModuleType("sounddevice")
_sounddevice.query_devices = lambda *_args, **_kwargs: {
    "name": "test input",
    "default_samplerate": 44_100,
}


class _UnexpectedInputStream:
    def __init__(self, *_args, **_kwargs):
        raise AssertionError("matrix-process primitive tests must not open audio")


_sounddevice.InputStream = _UnexpectedInputStream
_prior_sounddevice = sys.modules.get("sounddevice")
sys.modules["sounddevice"] = _sounddevice
try:
    from core.matrix_process import (  # noqa: E402
        MATRIX_PROCESS_EXPERIMENT_ENVIRONMENT,
        MAX_LIFECYCLE_MESSAGE_BYTES,
        SharedAnalyzerCounters,
        SharedAnalyzerView,
        SharedLatestAnalysisTransport,
        StaticAnalyzerResolver,
        drop_privileges_like_matrix,
        matrix_process_experiment_enabled,
        send_lifecycle_message,
    )
    import core.matrix_process_runtime as matrix_process_runtime  # noqa: E402
    from core.audio import AudioProcessor  # noqa: E402
    from config import Settings  # noqa: E402
finally:
    if _prior_sounddevice is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = _prior_sounddevice


def _read_shared_result_in_spawned_child(transport, connection) -> None:
    result = transport.acquire_latest(transport.active_epoch)
    try:
        connection.send(
            {
                "values": result.tolist() if result is not None else None,
                "sequence": transport.last_acquired_sequence,
                "source_generation": transport.last_acquired_source_generation,
            }
        )
    finally:
        if result is not None:
            transport.release_latest()
        connection.close()


class ExperimentSelectionTests(unittest.TestCase):
    def test_selection_is_explicit_and_rejects_ambiguous_values(self) -> None:
        self.assertFalse(matrix_process_experiment_enabled({}))
        self.assertFalse(
            matrix_process_experiment_enabled(
                {MATRIX_PROCESS_EXPERIMENT_ENVIRONMENT: "0"}
            )
        )
        self.assertTrue(
            matrix_process_experiment_enabled(
                {MATRIX_PROCESS_EXPERIMENT_ENVIRONMENT: "1"}
            )
        )
        for value in ("", "true", "2", " 1"):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError,
                "must be 0 or 1",
            ):
                matrix_process_experiment_enabled(
                    {MATRIX_PROCESS_EXPERIMENT_ENVIRONMENT: value}
                )


class SharedTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = multiprocessing.get_context("spawn")
        self.transport = SharedLatestAnalysisTransport(self.context, 4)
        self.transport.activate_epoch(1)

    def test_fixed_slots_publish_only_complete_payloads(self) -> None:
        slot_ids = tuple(id(slot) for slot in self.transport.slots)
        destination = self.transport.claim_write()
        self.assertIsNotNone(destination)
        destination[:] = (1.0, 2.0, 3.0, 4.0)
        self.assertIsNone(self.transport.acquire_latest(1))

        self.assertTrue(
            self.transport.publish_claimed(1, source_generation=17)
        )
        result = self.transport.acquire_latest(1)
        np.testing.assert_array_equal(result, (1.0, 2.0, 3.0, 4.0))
        self.assertEqual(self.transport.last_acquired_sequence, 1)
        self.assertEqual(self.transport.last_acquired_source_generation, 17)
        self.assertEqual(self.transport.last_acquired_epoch, 1)
        self.transport.release_latest()
        self.assertEqual(tuple(id(slot) for slot in self.transport.slots), slot_ids)

    def test_newest_value_supersedes_without_backlog_and_capacity_drops(self) -> None:
        first = self.transport.claim_write()
        first.fill(1)
        self.assertTrue(self.transport.publish_claimed(1, source_generation=1))
        second = self.transport.claim_write()
        second.fill(2)
        self.assertTrue(self.transport.publish_claimed(1, source_generation=2))

        newest = self.transport.acquire_latest(1)
        np.testing.assert_array_equal(newest, np.full(4, 2, dtype=np.float32))
        third = self.transport.claim_write()
        third.fill(3)
        self.assertTrue(self.transport.publish_claimed(1, source_generation=3))
        self.assertIsNone(self.transport.claim_write())
        self.assertEqual(self.transport.transport_drop_count, 1)
        self.transport.release_latest()
        self.assertIsNotNone(self.transport.claim_write())
        self.transport.abort_claimed()

    def test_old_epoch_cannot_publish_but_prior_complete_result_is_retained(self) -> None:
        destination = self.transport.claim_write()
        destination.fill(4)
        self.assertTrue(self.transport.publish_claimed(1, source_generation=8))
        self.transport.activate_epoch(2)

        retained = self.transport.acquire_latest(2)
        np.testing.assert_array_equal(retained, np.full(4, 4, dtype=np.float32))
        self.assertEqual(self.transport.last_acquired_epoch, 1)
        self.transport.release_latest()

        stale = self.transport.claim_write()
        stale.fill(9)
        self.assertFalse(self.transport.publish_claimed(1, source_generation=9))
        self.assertEqual(self.transport.transport_drop_count, 1)

    def test_spawned_child_reads_same_complete_shared_slot(self) -> None:
        destination = self.transport.claim_write()
        destination[:] = (2.5, 3.5, 4.5, 5.5)
        self.transport.publish_claimed(1, source_generation=23)
        parent, child = self.context.Pipe(duplex=False)
        process = self.context.Process(
            target=_read_shared_result_in_spawned_child,
            args=(self.transport, child),
        )
        process.start()
        child.close()
        actual = parent.recv()
        process.join(timeout=2)
        parent.close()

        self.assertEqual(process.exitcode, 0)
        self.assertEqual(actual["values"], [2.5, 3.5, 4.5, 5.5])
        self.assertEqual(actual["sequence"], 1)
        self.assertEqual(actual["source_generation"], 23)

    def test_audio_processor_analyzes_directly_into_shared_destination(self) -> None:
        settings = Settings()
        counters = SharedAnalyzerCounters(self.context)
        processor = AudioProcessor(
            settings.audio,
            settings.frequency,
            settings.sensitivity,
            4,
            analysis_transport=self.transport,
            counter_mirror=counters,
        )
        processor.setup()
        processor.latest_samples = np.ones(
            settings.audio.block_size,
            dtype=np.float32,
        )
        processor.sample_generation = 1

        self.assertTrue(processor._process_latest_samples_once())
        result = self.transport.acquire_latest(self.transport.active_epoch)
        self.assertIs(result, self.transport.slots[0])
        self.assertEqual(self.transport.last_acquired_source_generation, 1)
        self.assertEqual(counters.last_analyzed_generation, 1)
        self.transport.release_latest()


class SharedTelemetryViewTests(unittest.TestCase):
    def test_view_exposes_existing_telemetry_contract_without_copying_result(self) -> None:
        context = multiprocessing.get_context("spawn")
        transport = SharedLatestAnalysisTransport(context, 4)
        counters = SharedAnalyzerCounters(context)
        transport.activate_epoch(5)
        counters.set_sample_rate(44_100)
        counters.update_callback(12, 1)
        counters.update_analysis(11, 2)
        destination = transport.claim_write()
        destination[:] = (1, 2, 3, 4)
        transport.publish_claimed(5, source_generation=11)
        view = SharedAnalyzerView(transport, counters, block_size=512)

        result = view.get_fft_magnitudes()
        self.assertIs(result, transport.slots[0])
        self.assertEqual(view.sample_generation, 12)
        self.assertEqual(view.last_analyzed_generation, 11)
        self.assertEqual(view.analysis_sequence, 1)
        self.assertEqual(view.callback_status_count, 1)
        self.assertEqual(view.skipped_generation_count, 2)
        self.assertEqual(view.sample_rate, 44_100)
        self.assertEqual(view.audio_settings.block_size, 512)
        view.release_fft_magnitudes()

    def test_static_resolver_accepts_only_canonical_slot_zero(self) -> None:
        context = multiprocessing.get_context("spawn")
        view = SharedAnalyzerView(
            SharedLatestAnalysisTransport(context, 4),
            SharedAnalyzerCounters(context),
            block_size=512,
        )
        resolver = StaticAnalyzerResolver(view)
        self.assertIs(
            resolver.resolve(SimpleNamespace(canonical_slot=0)),
            view,
        )
        with self.assertRaisesRegex(ValueError, "one analyzer slot"):
            resolver.resolve(SimpleNamespace(canonical_slot=1))


class LifecyclePrimitiveTests(unittest.TestCase):
    def test_lifecycle_messages_are_bounded_json(self) -> None:
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        send_lifecycle_message(sender, {"type": "ready", "width": 64})
        self.assertEqual(
            receiver.recv_bytes(),
            b'{"type":"ready","width":64}',
        )
        with self.assertRaisesRegex(ValueError, "too large"):
            send_lifecycle_message(
                sender,
                {"value": "x" * MAX_LIFECYCLE_MESSAGE_BYTES},
            )
        sender.close()
        receiver.close()

    def test_privilege_mirror_is_noop_when_not_root(self) -> None:
        with (
            mock.patch("core.matrix_process.os.geteuid", return_value=501),
            mock.patch("core.matrix_process.grp.getgrnam") as get_group,
            mock.patch("core.matrix_process.pwd.getpwnam") as get_user,
        ):
            drop_privileges_like_matrix()
        get_group.assert_not_called()
        get_user.assert_not_called()

    def test_privilege_mirror_drops_group_before_user(self) -> None:
        calls = []
        with (
            mock.patch("core.matrix_process.os.geteuid", return_value=0),
            mock.patch(
                "core.matrix_process.grp.getgrnam",
                return_value=SimpleNamespace(gr_gid=7),
            ),
            mock.patch(
                "core.matrix_process.pwd.getpwnam",
                return_value=SimpleNamespace(pw_uid=8),
            ),
            mock.patch(
                "core.matrix_process.os.setresgid",
                side_effect=lambda *args: calls.append(("gid", args)),
                create=True,
            ),
            mock.patch(
                "core.matrix_process.os.setresuid",
                side_effect=lambda *args: calls.append(("uid", args)),
                create=True,
            ),
        ):
            drop_privileges_like_matrix()
        self.assertEqual(calls, [("gid", (7, 7, 7)), ("uid", (8, 8, 8))])


class _ScriptedLifecycle:
    def __init__(self, incoming=()):
        self.incoming = [json.dumps(value).encode("utf-8") for value in incoming]
        self.sent = []
        self.poll_timeouts = []
        self.closed = False

    def poll(self, timeout=0):
        self.poll_timeouts.append(timeout)
        return bool(self.incoming)

    def recv_bytes(self, _maximum=None):
        if not self.incoming:
            raise EOFError
        return self.incoming.pop(0)

    def send_bytes(self, payload):
        self.sent.append(json.loads(payload.decode("utf-8")))

    def close(self):
        self.closed = True


class _ParentRuntimeHarness:
    """Hardware-free supervisor harness for success and fail-stop paths."""

    def __init__(self, mode):
        self.mode = mode
        self.trace = []
        self.state = {"alive": True, "exitcode": None}
        self.counter_mirrors = []
        if mode == "startup_error":
            incoming = ({"type": "error", "message": "matrix failed"},)
        elif mode == "startup_timeout":
            incoming = ()
        else:
            incoming = ({
                "type": "ready",
                "phase": "renderer",
                "width": 64,
                "height": 64,
            },)

        harness = self

        class ParentLifecycle(_ScriptedLifecycle):
            def poll(self, timeout=0):
                self.poll_timeouts.append(timeout)
                if harness.mode == "pipe_failure" and timeout == 0.05:
                    raise OSError("pipe failed")
                return bool(self.incoming)

            def send_bytes(self, payload):
                message = json.loads(payload.decode("utf-8"))
                event = message.get("phase", message["type"])
                harness.trace.append(event)
                if event == "audio":
                    if harness.mode in ("success", "success_telemetry"):
                        self.incoming.append(
                            b'{"type":"shutdown","reason":"measurement_complete"}'
                        )
                    elif harness.mode == "renderer_crash":
                        harness.state.update(alive=False, exitcode=-9)
                elif event == "shutdown" and harness.mode != "forced_cleanup":
                    harness.state.update(alive=False, exitcode=0)

        self.parent_lifecycle = ParentLifecycle(incoming)
        self.child_lifecycle = _ScriptedLifecycle()

    def run(self):
        harness = self
        actual_context = multiprocessing.get_context("spawn")

        class FakeProcess:
            @property
            def exitcode(self):
                return harness.state["exitcode"]

            def start(self):
                harness.trace.append("renderer_start")

            def is_alive(self):
                return harness.state["alive"]

            def join(self, _timeout=None):
                harness.trace.append("renderer_join")

            def terminate(self):
                harness.trace.append("renderer_terminate")
                harness.state.update(alive=False, exitcode=-15)

            def kill(self):
                harness.trace.append("renderer_kill")
                harness.state.update(alive=False, exitcode=-9)

            def close(self):
                harness.trace.append("renderer_close")

        self.process = FakeProcess()

        class FakeContext:
            RawArray = staticmethod(actual_context.RawArray)
            Lock = staticmethod(actual_context.Lock)

            def Pipe(self, duplex=True):
                self.duplex = duplex
                return harness.parent_lifecycle, harness.child_lifecycle

            def Process(self, **_kwargs):
                return harness.process

        class FakeAnalyzer:
            def check_health(self):
                harness.trace.append("health")
                if harness.mode == "analyzer_failure":
                    raise RuntimeError("analysis worker failed")
                if harness.mode == "forced_cleanup":
                    raise KeyboardInterrupt

        analyzer = FakeAnalyzer()

        def build_audio(**kwargs):
            harness.counter_mirrors.append(kwargs["counter_mirror"])
            return analyzer

        class FakeRegistry:
            def __init__(self, graph, factories):
                factory = factories[graph.analyzers[0].analyzer_type]
                self.runtime = factory(graph.analyzers[0])

            def resolve(self, _definition):
                return self.runtime

            def setup(self):
                harness.trace.append("audio_setup")

            def start(self):
                harness.trace.append("audio_start")

            def stop(self):
                harness.trace.append("audio_stop")

        graph = SimpleNamespace(
            analyzers=(SimpleNamespace(analyzer_type="standard"),),
        )

        class FakeKeyboard:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def get_key(self):
                return None

        with (
            mock.patch.object(
                matrix_process_runtime.multiprocessing,
                "get_context",
                return_value=FakeContext(),
            ),
            mock.patch.object(
                matrix_process_runtime.RuntimeControlClient,
                "from_environment",
                return_value=None,
            ),
            mock.patch.object(
                matrix_process_runtime,
                "drop_privileges_like_matrix",
                side_effect=lambda: harness.trace.append("privilege_drop"),
            ),
            mock.patch.object(
                matrix_process_runtime,
                "compile_current_bars_graph",
                return_value=graph,
            ),
            mock.patch.object(matrix_process_runtime, "AnalyzerRegistry", FakeRegistry),
            mock.patch.object(
                matrix_process_runtime,
                "AudioProcessor",
                side_effect=build_audio,
            ),
        ):
            return matrix_process_runtime.run_matrix_process_experiment(
                args=SimpleNamespace(
                    drop_privileges=True,
                    non_interactive=True,
                    telemetry_out=(
                        "/tmp/telemetry.json"
                        if harness.mode == "success_telemetry"
                        else None
                    ),
                ),
                settings=SimpleNamespace(
                    audio=object(),
                    frequency=object(),
                    sensitivity=object(),
                ),
                gradient_enabled=False,
                output_path="direct",
                prior_state=None,
                keyboard_handler_factory=lambda **_kwargs: FakeKeyboard(),
            )

    def assert_cleaned(self, test_case):
        test_case.assertTrue(self.parent_lifecycle.closed)
        test_case.assertTrue(self.child_lifecycle.closed)
        test_case.assertIn("renderer_join", self.trace)
        test_case.assertIn("renderer_close", self.trace)


class RendererLifecycleTests(unittest.TestCase):
    def test_renderer_creates_matrix_reports_ready_and_polls_once_before_shutdown(self):
        events = []

        class FakeApp:
            def __init__(self):
                self.args = None
                self.width = 64
                self.height = 64
                self.canvas = object()

            def initialize_matrix(self):
                events.append("matrix")
                return True

            def clear(self):
                events.append("clear")

        class FakeVisualizer:
            gradient_mode = False

            def set_theme(self, _theme):
                events.append("theme")

        lifecycle = _ScriptedLifecycle(
            ({"type": "ready", "phase": "audio"}, {"type": "shutdown"})
        )
        settings = SimpleNamespace(
            audio=SimpleNamespace(block_size=512),
            color=SimpleNamespace(theme="test", brightness_boost=1.0),
            frequency=object(),
            sensitivity=object(),
        )
        with (
            mock.patch.object(matrix_process_runtime, "telemetry_from_args", return_value=None),
            mock.patch.object(matrix_process_runtime, "MatrixApp", FakeApp),
            mock.patch.object(
                matrix_process_runtime,
                "get_visualizer",
                return_value=FakeVisualizer(),
            ),
            mock.patch.object(matrix_process_runtime, "get_theme", return_value=object()),
            mock.patch.object(
                matrix_process_runtime,
                "compile_current_bars_graph",
                return_value=object(),
            ),
            mock.patch.object(
                matrix_process_runtime,
                "compile_current_bars_layer_plan",
                return_value=object(),
            ),
            mock.patch.object(matrix_process_runtime, "ActiveLayerPlan", side_effect=lambda value: value),
        ):
            matrix_process_runtime._renderer_process_main(
                SimpleNamespace(),
                settings,
                False,
                "direct",
                object(),
                None,
                object(),
                object(),
                lifecycle,
            )

        self.assertEqual(events, ["matrix", "theme", "clear"])
        self.assertEqual(lifecycle.sent[0]["type"], "ready")
        self.assertEqual(lifecycle.poll_timeouts, [10.0, 0])
        self.assertTrue(lifecycle.closed)

    def test_renderer_treats_parent_eof_as_failure_and_still_clears(self):
        events = []

        class ParentLossLifecycle(_ScriptedLifecycle):
            def poll(self, timeout=0):
                self.poll_timeouts.append(timeout)
                return True

        class FakeApp:
            def __init__(self):
                self.args = None
                self.width = 64
                self.height = 64
                self.canvas = object()

            def initialize_matrix(self):
                return True

            def clear(self):
                events.append("clear")

        visualizer = SimpleNamespace(set_theme=lambda _theme: None)
        lifecycle = ParentLossLifecycle(({"type": "ready", "phase": "audio"},))
        settings = SimpleNamespace(
            audio=SimpleNamespace(block_size=512),
            color=SimpleNamespace(theme="test", brightness_boost=1.0),
            frequency=object(),
            sensitivity=object(),
        )
        with (
            mock.patch.object(matrix_process_runtime, "telemetry_from_args", return_value=None),
            mock.patch.object(matrix_process_runtime, "MatrixApp", FakeApp),
            mock.patch.object(matrix_process_runtime, "get_visualizer", return_value=visualizer),
            mock.patch.object(matrix_process_runtime, "get_theme", return_value=object()),
            mock.patch.object(matrix_process_runtime, "compile_current_bars_graph", return_value=object()),
            mock.patch.object(matrix_process_runtime, "compile_current_bars_layer_plan", return_value=object()),
            mock.patch.object(matrix_process_runtime, "ActiveLayerPlan", side_effect=lambda value: value),
        ):
            with self.assertRaisesRegex(
                matrix_process_runtime.MatrixProcessRuntimeError,
                "lost its lifecycle parent",
            ):
                matrix_process_runtime._renderer_process_main(
                    SimpleNamespace(),
                    settings,
                    False,
                    "direct",
                    object(),
                    None,
                    object(),
                    object(),
                    lifecycle,
                )

        self.assertEqual(events, ["clear"])
        self.assertTrue(lifecycle.closed)

    def test_forced_renderer_cleanup_is_reported(self):
        lifecycle = _ScriptedLifecycle()

        class StuckProcess:
            def __init__(self):
                self.alive = True
                self.terminated = False
                self.killed = False

            def is_alive(self):
                return self.alive

            def join(self, _timeout=None):
                return None

            def terminate(self):
                self.terminated = True
                self.alive = False

            def kill(self):
                self.killed = True
                self.alive = False

        process = StuckProcess()
        self.assertTrue(
            matrix_process_runtime._stop_renderer(
                process,
                lifecycle,
                request_shutdown=True,
            )
        )
        self.assertTrue(process.terminated)
        self.assertFalse(process.killed)
        self.assertEqual(lifecycle.sent, [{"type": "shutdown"}])

    def test_external_runtime_control_handler_reports_unavailable(self):
        observed = []

        class FakeClient:
            def serve(self, handler, *, stop_event):
                try:
                    handler("snapshot", {})
                except Exception as exc:
                    observed.append(exc)
                stop_event.set()

        stop_event, thread = matrix_process_runtime._start_unavailable_runtime_controls(
            FakeClient()
        )
        thread.join(timeout=1)
        self.assertTrue(stop_event.is_set())
        self.assertEqual(len(observed), 1)
        self.assertIsInstance(observed[0], RuntimeError)
        self.assertEqual(observed[0].code, "unavailable")

    def test_supervisor_success_closes_endpoints_and_disables_counter_mirror(self):
        harness = _ParentRuntimeHarness("success")
        harness.run()
        harness.assert_cleaned(self)
        self.assertEqual(harness.counter_mirrors, [None])
        self.assertLess(
            harness.trace.index("audio_stop"),
            harness.trace.index("shutdown"),
        )

        telemetry_harness = _ParentRuntimeHarness("success_telemetry")
        telemetry_harness.run()
        telemetry_harness.assert_cleaned(self)
        self.assertEqual(len(telemetry_harness.counter_mirrors), 1)
        self.assertIsNotNone(telemetry_harness.counter_mirrors[0])

    def test_supervisor_reaps_startup_timeout_and_startup_error(self):
        for mode, message in (
            ("startup_timeout", "did not become ready"),
            ("startup_error", "renderer startup failed"),
        ):
            with self.subTest(mode=mode):
                harness = _ParentRuntimeHarness(mode)
                with self.assertRaisesRegex(
                    matrix_process_runtime.MatrixProcessRuntimeError,
                    message,
                ):
                    harness.run()
                harness.assert_cleaned(self)
                self.assertNotIn("audio_setup", harness.trace)

    def test_supervisor_reaps_renderer_crash_and_analyzer_failure(self):
        for mode, message in (
            ("renderer_crash", "renderer exited unexpectedly"),
            ("analyzer_failure", "analysis worker failed"),
        ):
            with self.subTest(mode=mode):
                harness = _ParentRuntimeHarness(mode)
                with self.assertRaisesRegex(RuntimeError, message):
                    harness.run()
                harness.assert_cleaned(self)
                self.assertIn("audio_stop", harness.trace)

    def test_supervisor_reaps_lifecycle_pipe_failure(self):
        harness = _ParentRuntimeHarness("pipe_failure")
        with self.assertRaisesRegex(OSError, "pipe failed"):
            harness.run()
        harness.assert_cleaned(self)
        self.assertIn("audio_stop", harness.trace)

    def test_supervisor_rejects_forced_cleanup(self):
        harness = _ParentRuntimeHarness("forced_cleanup")
        with self.assertRaisesRegex(
            matrix_process_runtime.MatrixProcessRuntimeError,
            "required forced cleanup",
        ):
            harness.run()
        harness.assert_cleaned(self)
        self.assertIn("renderer_terminate", harness.trace)

    def test_parent_starts_audio_only_after_ready_and_stops_it_before_shutdown(self):
        trace = []
        actual_context = multiprocessing.get_context("spawn")
        state = {"alive": True}

        class ParentLifecycle(_ScriptedLifecycle):
            def __init__(self):
                super().__init__(({
                    "type": "ready",
                    "phase": "renderer",
                    "width": 64,
                    "height": 64,
                },))

            def send_bytes(self, payload):
                message = json.loads(payload.decode("utf-8"))
                event = message.get("phase", message["type"])
                trace.append(event)
                if event == "audio":
                    self.incoming.append(
                        b'{"type":"shutdown","reason":"measurement_complete"}'
                    )
                elif message["type"] == "shutdown":
                    state["alive"] = False

        parent_lifecycle = ParentLifecycle()
        child_lifecycle = _ScriptedLifecycle()

        class FakeProcess:
            exitcode = 0

            def start(self):
                trace.append("renderer_start")

            def is_alive(self):
                return state["alive"]

            def join(self, _timeout=None):
                trace.append("renderer_join")

            def terminate(self):
                raise AssertionError("clean renderer must not be terminated")

            def kill(self):
                raise AssertionError("clean renderer must not be killed")

            def close(self):
                trace.append("renderer_close")

        class FakeContext:
            RawArray = staticmethod(actual_context.RawArray)
            Lock = staticmethod(actual_context.Lock)

            def Pipe(self, duplex=True):
                self.duplex = duplex
                return parent_lifecycle, child_lifecycle

            def Process(self, **_kwargs):
                return FakeProcess()

        analyzer = SimpleNamespace(check_health=lambda: trace.append("health"))

        class FakeRegistry:
            def __init__(self, _graph, _factories):
                pass

            def resolve(self, _definition):
                return analyzer

            def setup(self):
                trace.append("audio_setup")

            def start(self):
                trace.append("audio_start")

            def stop(self):
                trace.append("audio_stop")

        graph = SimpleNamespace(
            analyzers=(SimpleNamespace(analyzer_type="standard"),),
        )

        class FakeKeyboard:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def get_key(self):
                return None

        with (
            mock.patch.object(
                matrix_process_runtime.multiprocessing,
                "get_context",
                return_value=FakeContext(),
            ),
            mock.patch.object(
                matrix_process_runtime.RuntimeControlClient,
                "from_environment",
                return_value=None,
            ),
            mock.patch.object(
                matrix_process_runtime,
                "drop_privileges_like_matrix",
                side_effect=lambda: trace.append("privilege_drop"),
            ),
            mock.patch.object(
                matrix_process_runtime,
                "compile_current_bars_graph",
                return_value=graph,
            ),
            mock.patch.object(matrix_process_runtime, "AnalyzerRegistry", FakeRegistry),
        ):
            matrix_process_runtime.run_matrix_process_experiment(
                args=SimpleNamespace(drop_privileges=True, non_interactive=True),
                settings=SimpleNamespace(
                    audio=object(),
                    frequency=object(),
                    sensitivity=object(),
                ),
                gradient_enabled=False,
                output_path="direct",
                prior_state=None,
                keyboard_handler_factory=lambda **_kwargs: FakeKeyboard(),
            )

        self.assertLess(trace.index("renderer_start"), trace.index("privilege_drop"))
        self.assertLess(trace.index("privilege_drop"), trace.index("audio_setup"))
        self.assertLess(trace.index("audio_start"), trace.index("audio"))
        self.assertLess(trace.index("audio_stop"), trace.index("shutdown"))
        self.assertNotIn("health", trace)


if __name__ == "__main__":
    unittest.main()
