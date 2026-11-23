"""Hardware-free contract tests for the minimal FFT pipeline interfaces."""

from __future__ import annotations

from dataclasses import replace
import inspect
from pathlib import Path
import sys
import threading
import time
from types import ModuleType
import unittest
from unittest import mock

try:
    import numpy as np
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("FFT pipeline tests require the runtime NumPy dependency") from exc


FFT_ROOT = Path(__file__).resolve().parent.parent / "my_programs" / "fft_program"
MAIN_PATH = FFT_ROOT / "main.py"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))


# AudioProcessor only needs sounddevice when a real stream starts.  Provide the
# import surface here so these contract tests remain independent of PortAudio.
_sounddevice = ModuleType("sounddevice")
_sounddevice.query_devices = lambda *_args, **_kwargs: {
    "name": "test input",
    "default_samplerate": 44_100,
}


class _UnexpectedInputStream:
    def __init__(self, *_args, **_kwargs) -> None:
        raise AssertionError("hardware stream must not start in interface tests")


_sounddevice.InputStream = _UnexpectedInputStream
_prior_sounddevice = sys.modules.get("sounddevice")
_prior_core = sys.modules.get("core")
_prior_core_audio = sys.modules.get("core.audio")
sys.modules["sounddevice"] = _sounddevice
try:
    from config.settings import (  # noqa: E402
        AudioSettings,
        FrequencySettings,
        SensitivitySettings,
        Settings,
    )
    from core.audio import AudioProcessor, _LatestAnalysisTransport  # noqa: E402
    from core.interfaces import (  # noqa: E402
        AudioCapture,
        FrameRenderer,
        MagnitudeAnalyzer,
    )
    from visualizers.bars_unified import BarsUnifiedVisualizer  # noqa: E402
    from themes import get_theme  # noqa: E402
finally:
    if _prior_sounddevice is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = _prior_sounddevice
    if _prior_core_audio is None:
        sys.modules.pop("core.audio", None)
    else:
        sys.modules["core.audio"] = _prior_core_audio
    if _prior_core is None:
        sys.modules.pop("core", None)
    else:
        sys.modules["core"] = _prior_core


def _processor(num_bins: int = 64) -> AudioProcessor:
    return AudioProcessor(
        AudioSettings(),
        FrequencySettings(),
        SensitivitySettings(),
        num_bins,
    )


class AudioCaptureBehaviorTests(unittest.TestCase):
    def test_callback_copies_and_replaces_the_latest_array(self) -> None:
        processor = _processor()
        first_input = np.arange(512, dtype=np.float32).reshape(512, 1)

        processor._audio_callback(first_input, 512, None, None)

        first_published = processor.latest_samples
        self.assertIsNotNone(first_published)
        np.testing.assert_array_equal(first_published, first_input[:, 0])
        self.assertEqual(first_published.dtype, np.float32)
        self.assertEqual(first_published.shape, (512,))
        self.assertFalse(np.shares_memory(first_published, first_input))
        self.assertTrue(processor.have_data)
        self.assertEqual(processor.sample_generation, 1)
        self.assertEqual(processor.callback_status_count, 0)

        first_input.fill(-1)
        np.testing.assert_array_equal(
            first_published,
            np.arange(512, dtype=np.float32),
        )

        second_input = np.full((512, 1), 7.5, dtype=np.float32)
        processor._audio_callback(second_input, 512, None, None)

        self.assertIsNot(processor.latest_samples, first_published)
        self.assertEqual(processor.sample_generation, 2)
        np.testing.assert_array_equal(processor.latest_samples, second_input[:, 0])
        np.testing.assert_array_equal(
            first_published,
            np.arange(512, dtype=np.float32),
        )

    def test_callback_does_no_analysis_or_result_transport_work(self) -> None:
        processor = _processor()
        samples = np.ones((512, 1), dtype=np.float32)

        with (
            mock.patch.object(
                AudioProcessor,
                "_analyze_into",
                side_effect=AssertionError("FFT must not run in callback"),
            ),
            mock.patch.object(
                processor._analysis_transport,
                "claim_write",
                side_effect=AssertionError("result transport must not run in callback"),
            ),
        ):
            processor._audio_callback(samples, 512, None, object())

        self.assertEqual(processor.sample_generation, 1)
        self.assertEqual(processor.callback_status_count, 1)
        np.testing.assert_array_equal(processor.latest_samples, samples[:, 0])


class LatestAnalysisTransportTests(unittest.TestCase):
    def test_slots_are_fixed_contiguous_and_publish_only_complete_payloads(self) -> None:
        transport = _LatestAnalysisTransport(64)
        slot_ids = tuple(id(slot) for slot in transport.slots)
        transport.activate_epoch(1)

        destination = transport.claim_write()
        self.assertIsNotNone(destination)
        destination[:32] = 1
        self.assertIsNone(transport.acquire_latest(1))
        destination[32:] = 2
        self.assertTrue(transport.publish_claimed(1))

        published = transport.acquire_latest(1)
        self.assertIs(published, destination)
        np.testing.assert_array_equal(published[:32], np.ones(32, dtype=np.float32))
        np.testing.assert_array_equal(published[32:], np.full(32, 2, dtype=np.float32))
        transport.release_latest()
        self.assertIs(transport.acquire_latest(1), published)
        self.assertEqual(transport.last_acquired_sequence, 1)
        transport.release_latest()

        self.assertEqual(tuple(id(slot) for slot in transport.slots), slot_ids)
        for slot in transport.slots:
            self.assertEqual(slot.shape, (64,))
            self.assertEqual(slot.dtype, np.float32)
            self.assertTrue(slot.flags.c_contiguous)

    def test_consumer_gets_newest_sequence_and_producer_never_builds_backlog(self) -> None:
        transport = _LatestAnalysisTransport(64)
        transport.activate_epoch(7)

        first = transport.claim_write()
        first.fill(1)
        self.assertTrue(transport.publish_claimed(7))
        second = transport.claim_write()
        second.fill(2)
        self.assertTrue(transport.publish_claimed(7))

        newest = transport.acquire_latest(7)
        self.assertIs(newest, second)
        self.assertEqual(transport.last_acquired_sequence, 2)
        np.testing.assert_array_equal(newest, np.full(64, 2, dtype=np.float32))

        third = transport.claim_write()
        self.assertIs(third, first)
        third.fill(3)
        self.assertTrue(transport.publish_claimed(7))
        self.assertIsNone(transport.claim_write())
        self.assertEqual(transport.transport_drop_count, 1)
        transport.release_latest()

        reusable = transport.claim_write()
        self.assertIs(reusable, second)
        transport.abort_claimed()

    def test_old_epoch_cannot_publish_after_replacement(self) -> None:
        transport = _LatestAnalysisTransport(64)
        transport.activate_epoch(1)
        destination = transport.claim_write()
        destination.fill(4)

        transport.activate_epoch(2)

        self.assertFalse(transport.publish_claimed(1))
        self.assertIsNone(transport.acquire_latest(2))
        self.assertEqual(transport.published_sequence, 0)
        self.assertEqual(transport.transport_drop_count, 1)

    def test_last_complete_frame_remains_readable_during_epoch_handoff(self) -> None:
        transport = _LatestAnalysisTransport(64)
        transport.activate_epoch(1)
        destination = transport.claim_write()
        destination.fill(6)
        self.assertTrue(transport.publish_claimed(1))

        transport.activate_epoch(2)

        retained = transport.acquire_latest(2)
        self.assertIs(retained, destination)
        np.testing.assert_array_equal(retained, np.full(64, 6, dtype=np.float32))
        transport.release_latest()

        replacement = transport.claim_write()
        replacement.fill(9)
        self.assertTrue(transport.publish_claimed(2))
        newest = transport.acquire_latest(2)
        self.assertIs(newest, replacement)
        np.testing.assert_array_equal(newest, np.full(64, 9, dtype=np.float32))
        transport.release_latest()


class AnalysisWorkerLifecycleTests(unittest.TestCase):
    class RecordingInputStream:
        instances = []

        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.started = False
            self.stopped = False
            self.closed = False
            self.__class__.instances.append(self)

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

        def close(self) -> None:
            self.closed = True

    class FailingInputStream(RecordingInputStream):
        def start(self) -> None:
            self.started = True
            raise RuntimeError("injected stream start failure")

    def test_worker_waits_for_callback_and_stops_with_capture(self) -> None:
        processor = _processor()
        processor.setup()
        self.RecordingInputStream.instances.clear()
        sd_module = AudioProcessor.start.__globals__["sd"]

        with mock.patch.object(sd_module, "InputStream", self.RecordingInputStream):
            processor.start()
            worker = processor._analysis_thread
            self.assertIsNotNone(worker)
            self.assertTrue(worker.is_alive())
            processor.stop()

        stream = self.RecordingInputStream.instances[0]
        self.assertTrue(stream.started)
        self.assertTrue(stream.stopped)
        self.assertTrue(stream.closed)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(processor._analysis_thread)

    def test_stream_start_failure_closes_stream_and_joins_worker(self) -> None:
        processor = _processor()
        processor.setup()
        self.FailingInputStream.instances.clear()
        sd_module = AudioProcessor.start.__globals__["sd"]

        with (
            mock.patch.object(sd_module, "InputStream", self.FailingInputStream),
            self.assertRaisesRegex(RuntimeError, "stream start failure"),
        ):
            processor.start()

        stream = self.FailingInputStream.instances[0]
        self.assertTrue(stream.started)
        self.assertTrue(stream.closed)
        self.assertIsNone(processor._analysis_thread)
        self.assertIsNone(processor._stream)

    def test_worker_thread_start_failure_releases_lifecycle_ownership(self) -> None:
        processor = _processor()

        with (
            mock.patch.object(
                threading.Thread,
                "start",
                side_effect=RuntimeError("injected worker start failure"),
            ),
            self.assertRaisesRegex(RuntimeError, "worker start failure"),
        ):
            processor._start_analysis_worker()

        self.assertIsNone(processor._analysis_thread)
        self.assertIsNone(processor._stream)

    def test_worker_failure_surfaces_to_render_side_and_shutdown_stays_bounded(self) -> None:
        processor = _processor()
        processor.setup()
        entered = threading.Event()

        def fail_analysis(*_args) -> None:
            entered.set()
            raise ValueError("injected analysis failure")

        with mock.patch.object(AudioProcessor, "_analyze_into", side_effect=fail_analysis):
            processor._start_analysis_worker()
            processor._audio_callback(
                np.ones((512, 1), dtype=np.float32),
                512,
                None,
                None,
            )
            self.assertTrue(entered.wait(1.0))
            processor._analysis_thread.join(timeout=1.0)

        self.assertEqual(processor.worker_failure_count, 1)
        with self.assertRaisesRegex(RuntimeError, "analysis worker failed"):
            processor.get_fft_magnitudes()

        started = time.monotonic()
        processor._stop_analysis_worker()
        self.assertLess(time.monotonic() - started, 1.0)

    def test_worker_coalesces_callbacks_and_analyzes_the_newest_generation(self) -> None:
        processor = _processor()
        processor.setup()
        first_analysis_started = threading.Event()
        allow_first_analysis = threading.Event()
        second_publication_complete = threading.Event()
        analyze_calls = 0
        original_analyze = processor._analyze_into
        original_publish = processor._analysis_transport.publish_claimed

        def controlled_analysis(*args) -> None:
            nonlocal analyze_calls
            analyze_calls += 1
            if analyze_calls == 1:
                first_analysis_started.set()
                self.assertTrue(allow_first_analysis.wait(1.0))
            original_analyze(*args)

        def record_publication(epoch: int) -> bool:
            published = original_publish(epoch)
            if processor.analysis_sequence >= 2:
                second_publication_complete.set()
            return published

        with (
            mock.patch.object(
                processor,
                "_analyze_into",
                side_effect=controlled_analysis,
            ),
            mock.patch.object(
                processor._analysis_transport,
                "publish_claimed",
                side_effect=record_publication,
            ),
        ):
            processor._start_analysis_worker()
            try:
                processor._audio_callback(
                    np.full((512, 1), 1, dtype=np.float32),
                    512,
                    None,
                    None,
                )
                self.assertTrue(first_analysis_started.wait(1.0))
                processor._audio_callback(
                    np.full((512, 1), 2, dtype=np.float32),
                    512,
                    None,
                    None,
                )
                processor._audio_callback(
                    np.full((512, 1), 3, dtype=np.float32),
                    512,
                    None,
                    None,
                )
                allow_first_analysis.set()
                self.assertTrue(second_publication_complete.wait(1.0))
            finally:
                allow_first_analysis.set()
                processor._stop_analysis_worker()

        self.assertEqual(analyze_calls, 2)
        self.assertEqual(processor.sample_generation, 3)
        self.assertEqual(processor.last_analyzed_generation, 3)
        self.assertEqual(processor.skipped_generation_count, 1)
        self.assertEqual(processor.analysis_sequence, 2)

    def test_join_timeout_is_fixed_and_reported(self) -> None:
        class StuckWorker:
            def __init__(self) -> None:
                self.join_timeout = None

            def join(self, timeout=None) -> None:
                self.join_timeout = timeout

            def is_alive(self) -> bool:
                return True

        processor = _processor()
        worker = StuckWorker()
        processor._analysis_thread = worker

        with self.assertRaisesRegex(RuntimeError, "within 1.0 seconds"):
            processor._stop_analysis_worker()

        self.assertEqual(worker.join_timeout, 1.0)


class MagnitudeAnalyzerBehaviorTests(unittest.TestCase):
    def test_fft_matches_the_existing_numpy_projection_exactly(self) -> None:
        processor = _processor()
        self.assertEqual(processor.audio_settings.block_size, 512)
        self.assertEqual(processor.audio_settings.fft_size, 8192)
        self.assertEqual(processor.num_bins, 64)
        processor.setup()
        self.assertEqual(processor.sample_rate, 44_100)

        plan = processor.active_plan
        self.assertIsNotNone(plan)

        positions = np.arange(512, dtype=np.float32)
        samples = (
            0.7 * np.sin(2 * np.pi * positions / 31)
            + 0.2 * np.cos(2 * np.pi * positions / 9)
        ).astype(np.float32)
        processor._audio_callback(samples.reshape(-1, 1), 512, None, None)

        self.assertTrue(processor._process_latest_samples_once())
        actual = processor.get_fft_magnitudes()

        windowed = samples * plan.window
        magnitudes = np.abs(np.fft.rfft(windowed, n=plan.fft_size))
        expected = np.zeros(processor.num_bins, dtype=np.float32)
        for index, bin_indices in enumerate(plan.bin_indices):
            if len(bin_indices) > 0:
                expected[index] = (
                    np.mean(magnitudes[bin_indices])
                    * plan.bin_weights[index]
                )
        expected = np.maximum(
            0,
            expected - processor.sensitivity_settings.noise_floor,
        )

        self.assertIsNotNone(actual)
        self.assertEqual(actual.shape, (64,))
        self.assertEqual(actual.dtype, np.float32)
        np.testing.assert_array_equal(actual, expected)
        processor.release_fft_magnitudes()

    def test_setup_compiles_once_and_frames_keep_the_same_active_plan(self) -> None:
        processor = _processor()
        compile_plan = AudioProcessor.setup.__globals__["compile_standard_analysis_plan"]

        with mock.patch.dict(
            AudioProcessor.setup.__globals__,
            {"compile_standard_analysis_plan": mock.Mock(wraps=compile_plan)},
        ):
            compile_mock = AudioProcessor.setup.__globals__["compile_standard_analysis_plan"]
            processor.setup()
            active_plan = processor.active_plan
            samples = np.ones((512, 1), dtype=np.float32)
            processor._audio_callback(samples, 512, None, None)

            first_processed = processor._process_latest_samples_once()
            second_processed = processor._process_latest_samples_once()
            first = processor.get_fft_magnitudes()

        self.assertEqual(compile_mock.call_count, 1)
        self.assertTrue(first_processed)
        self.assertFalse(second_processed)
        self.assertIsNotNone(first)
        processor.release_fft_magnitudes()
        self.assertIs(processor.active_plan, active_plan)
        self.assertEqual(processor.last_analyzed_generation, 1)
        self.assertEqual(processor._analysis_transport.published_sequence, 1)

    def test_analysis_skips_stale_sample_generations_without_catching_up(self) -> None:
        processor = _processor()
        processor.setup()
        for value in (1, 2, 3):
            processor._audio_callback(
                np.full((512, 1), value, dtype=np.float32),
                512,
                None,
                None,
            )

        self.assertTrue(processor._process_latest_samples_once())

        self.assertEqual(processor.sample_generation, 3)
        self.assertEqual(processor.last_analyzed_generation, 3)
        self.assertEqual(processor.skipped_generation_count, 2)
        self.assertEqual(processor.analysis_sequence, 1)
        self.assertFalse(processor._process_latest_samples_once())

    def test_no_free_result_slot_counts_the_unanalyzed_generation(self) -> None:
        processor = _processor()
        processor.setup()
        processor._audio_callback(
            np.full((512, 1), 1, dtype=np.float32),
            512,
            None,
            None,
        )
        self.assertTrue(processor._process_latest_samples_once())
        leased = processor.get_fft_magnitudes()
        self.assertIsNotNone(leased)

        processor._audio_callback(
            np.full((512, 1), 2, dtype=np.float32),
            512,
            None,
            None,
        )
        self.assertTrue(processor._process_latest_samples_once())
        processor._audio_callback(
            np.full((512, 1), 3, dtype=np.float32),
            512,
            None,
            None,
        )
        self.assertFalse(processor._process_latest_samples_once())

        self.assertEqual(processor.sample_generation, 3)
        self.assertEqual(processor.last_analyzed_generation, 2)
        self.assertEqual(processor.skipped_generation_count, 1)
        self.assertEqual(processor.analysis_sequence, 2)
        self.assertEqual(processor.transport_drop_count, 1)
        processor.release_fft_magnitudes()

        processor._audio_callback(
            np.full((512, 1), 4, dtype=np.float32),
            512,
            None,
            None,
        )
        self.assertTrue(processor._process_latest_samples_once())
        self.assertEqual(processor.last_analyzed_generation, 4)
        self.assertEqual(processor.skipped_generation_count, 1)

    def test_failed_recompile_leaves_the_active_plan_unchanged(self) -> None:
        processor = _processor()
        processor.setup()
        active_plan = processor.active_plan
        worker_state = processor._worker_state
        processor._audio_callback(
            np.ones((512, 1), dtype=np.float32),
            512,
            None,
            None,
        )
        processor._process_latest_samples_once()
        published_sequence = processor._analysis_transport.published_sequence
        published = processor.get_fft_magnitudes()
        processor.release_fft_magnitudes()
        processor.freq_settings.zoom_presets.append((100, 22_051))
        processor.freq_settings.zoom_preset_index = len(
            processor.freq_settings.zoom_presets
        ) - 1

        with self.assertRaisesRegex(ValueError, "Nyquist"):
            processor.update_frequency_range()

        self.assertIs(processor.active_plan, active_plan)
        self.assertIs(processor._worker_state, worker_state)
        self.assertEqual(
            processor._analysis_transport.published_sequence,
            published_sequence,
        )
        self.assertIs(processor.get_fft_magnitudes(), published)
        processor.release_fft_magnitudes()

    def test_candidate_compiles_off_to_the_side_then_activates_once(self) -> None:
        processor = _processor()
        processor.setup()
        original_plan = processor.active_plan
        original_worker_state = processor._worker_state
        candidate = replace(
            processor.analysis_candidate,
            low_freq_weight=1.25,
            high_freq_weight=9.5,
        )

        prepared_plan = processor.compile_analysis_candidate(candidate)

        self.assertIs(processor.active_plan, original_plan)
        self.assertIsNot(prepared_plan, original_plan)
        processor.activate_analysis_plan(candidate, prepared_plan)
        self.assertIs(processor.analysis_candidate, candidate)
        self.assertIs(processor.active_plan, prepared_plan)
        self.assertIs(processor._worker_state.plan, prepared_plan)
        self.assertEqual(
            processor._worker_state.epoch,
            original_worker_state.epoch + 1,
        )

    def test_inflight_old_plan_result_cannot_publish_after_apply(self) -> None:
        processor = _processor()
        processor.setup()
        samples = np.linspace(-1, 1, 512, dtype=np.float32)
        old_state = processor._worker_state
        destination = processor._analysis_transport.claim_write()
        processor._analyze_into(
            samples,
            old_state.plan,
            old_state.noise_floor,
            destination,
        )

        candidate = replace(
            processor.analysis_candidate,
            min_freq=80,
            max_freq=1950,
        )
        new_plan = processor.compile_analysis_candidate(candidate)
        processor.activate_analysis_plan(candidate, new_plan)

        self.assertFalse(
            processor._analysis_transport.publish_claimed(old_state.epoch)
        )
        self.assertIsNone(processor.get_fft_magnitudes())

        processor._audio_callback(samples.reshape(-1, 1), 512, None, None)
        self.assertTrue(processor._process_latest_samples_once())
        actual = processor.get_fft_magnitudes()
        expected = np.empty(64, dtype=np.float32)
        processor._analyze_into(
            samples,
            new_plan,
            processor.sensitivity_settings.noise_floor,
            expected,
        )
        np.testing.assert_array_equal(actual, expected)
        processor.release_fft_magnitudes()


class PipelineProtocolTests(unittest.TestCase):
    def test_contracts_are_static_protocols_with_narrow_method_sets(self) -> None:
        expected_methods = {
            AudioCapture: {
                "setup": ["self"],
                "start": ["self"],
                "stop": ["self"],
                "__enter__": ["self"],
                "__exit__": ["self", "exc_type", "exc_val", "exc_tb"],
            },
            MagnitudeAnalyzer: {
                "update_frequency_range": ["self"],
                "get_fft_magnitudes": ["self"],
                "release_fft_magnitudes": ["self"],
            },
            FrameRenderer: {
                "draw": [
                    "self",
                    "canvas",
                    "smoothed_bars",
                    "peak_heights",
                ],
            },
        }

        for protocol, methods in expected_methods.items():
            self.assertTrue(protocol._is_protocol)
            self.assertFalse(protocol._is_runtime_protocol)
            for method_name, parameter_names in methods.items():
                signature = inspect.signature(getattr(protocol, method_name))
                self.assertEqual(list(signature.parameters), parameter_names)

    def test_existing_objects_have_compatible_method_order(self) -> None:
        implementations = {
            AudioCapture: (
                AudioProcessor,
                ("setup", "start", "stop", "__enter__", "__exit__"),
            ),
            MagnitudeAnalyzer: (
                AudioProcessor,
                (
                    "update_frequency_range",
                    "get_fft_magnitudes",
                    "release_fft_magnitudes",
                ),
            ),
            FrameRenderer: (BarsUnifiedVisualizer, ("draw",)),
        }

        for protocol, (implementation, method_names) in implementations.items():
            for method_name in method_names:
                protocol_method = getattr(protocol, method_name)
                protocol_parameters = list(
                    inspect.signature(protocol_method).parameters
                )
                implementation_parameters = list(
                    inspect.signature(getattr(implementation, method_name)).parameters
                )
                self.assertEqual(
                    implementation_parameters,
                    protocol_parameters,
                    f"{implementation.__name__}.{method_name}",
                )


class StandardRenderSourceTests(unittest.TestCase):
    class RecordingCanvas:
        def __init__(self) -> None:
            self.calls = []

        def Clear(self) -> None:
            self.calls.append(("clear",))

        def SetPixel(self, x, y, r, g, b) -> None:
            self.calls.append(("pixel", x, y, r, g, b))

    def test_ordered_plan_preserves_prepare_draw_swap_and_sleep_order(self) -> None:
        source = MAIN_PATH.read_text(encoding="utf-8")
        loop = source[source.index("            while True:"):]
        layer_plan_source = (
            FFT_ROOT / "core" / "layer_plan.py"
        ).read_text(encoding="utf-8")

        ordered_markers = (
            "frame_plan = active_layer_plan.begin_frame()",
            "smoothed = frame_plan.prepare_frame()",
            "frame_plan.draw_frame(app.canvas)",
            "app.swap_canvas()",
            "time.sleep(settings.audio.sleep_delay)",
        )
        positions = [loop.index(marker) for marker in ordered_markers]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("SetImage", loop)
        self.assertIn("result = group.analyzer.get_fft_magnitudes()", layer_plan_source)
        self.assertIn("group.analyzer.release_fft_magnitudes()", layer_plan_source)
        self.assertIn("self.scaler.process(", layer_plan_source)
        self.assertIn("draw_peaks(", layer_plan_source)

    def test_standard_overflow_bands_still_draw_directly_to_canvas(self) -> None:
        settings = Settings()
        settings.shadow.enabled = False
        settings.overflow.enabled = True
        visualizer = BarsUnifiedVisualizer(2, 8, settings)
        visualizer.set_theme(get_theme("ocean"))
        canvas = self.RecordingCanvas()
        bars = np.array([0.25, 1.5], dtype=np.float32)

        visualizer.draw(canvas, bars, None)

        self.assertEqual(canvas.calls[0], ("clear",))
        pixels = [call for call in canvas.calls if call[0] == "pixel"]
        self.assertTrue(pixels)
        self.assertEqual({call[1] for call in pixels}, {0, 1})
        self.assertTrue(all(0 <= call[2] < 8 for call in pixels))

    def test_accepted_post_frame_sleep_default_remains_five_milliseconds(self) -> None:
        processor = _processor()
        self.assertEqual(processor.audio_settings.sleep_delay, 0.005)

    def test_analysis_hot_path_has_no_queue_or_configuration_construction(self) -> None:
        audio_source = (FFT_ROOT / "core" / "audio.py").read_text(encoding="utf-8")
        process_source = inspect.getsource(
            AudioProcessor._process_latest_samples_once
        )

        self.assertNotIn("queue.Queue", audio_source)
        for forbidden in (
            "StandardAnalysisCandidate(",
            "compile_standard_analysis_plan(",
            "dict(",
            "tuple(",
        ):
            self.assertNotIn(forbidden, process_source)


if __name__ == "__main__":
    unittest.main()
