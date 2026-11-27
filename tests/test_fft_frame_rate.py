"""Live frame counters and the runtime ``stats`` operation (fft-live-fps M1)."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
from types import ModuleType
import unittest


FFT_ROOT = Path(__file__).resolve().parent.parent / "my_programs" / "fft_program"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))

# Importing core pulls in core.audio, which imports sounddevice. Match the stub
# the other FFT suites use so this file passes alone and in any order.
_sounddevice = ModuleType("sounddevice")
_sounddevice.query_devices = lambda *_args, **_kwargs: {
    "name": "test input",
    "default_samplerate": 44_100,
}
_sounddevice.InputStream = object
_prior_sounddevice = sys.modules.get("sounddevice")
sys.modules["sounddevice"] = _sounddevice
try:
    from core.frame_rate import FrameRateCounter  # noqa: E402
    from matrix_controller.runtime_transport import RuntimeControlError  # noqa: E402
    from runtime_control import FftRuntimeController  # noqa: E402
finally:
    if _prior_sounddevice is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = _prior_sounddevice


class _Clock:
    """A settable monotonic clock; tests set ``now`` before each call."""

    def __init__(self) -> None:
        self.now = 0

    def __call__(self) -> int:
        return self.now


def _swap(counter: FrameRateCounter, clock: _Clock, at_ns: int, sequence: int) -> None:
    clock.now = at_ns
    counter.record_swap(sequence)


class FrameRateCounterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.counter = FrameRateCounter(clock=self.clock)

    def test_new_counter_reports_zero_and_no_window(self) -> None:
        self.clock.now = 5
        self.assertEqual(
            self.counter.sample(),
            {
                "monotonic_ns": 5,
                "swap_frames": 0,
                "unique_content_frames": 0,
                "last_swap_ns": None,
                "slowest_frame_window": None,
                "slowest_frame_interval_ns": None,
            },
        )

    def test_unique_content_matches_telemetry_sequence_changes(self) -> None:
        # Telemetry counts 1 + the number of sequence changes between frames.
        sequences = [0, 0, 1, 1, 1, 2, 4, 4]
        for index, sequence in enumerate(sequences):
            _swap(self.counter, self.clock, (index + 1) * 10, sequence)
        sample = self.counter.sample()
        self.assertEqual(sample["swap_frames"], len(sequences))
        self.assertEqual(sample["unique_content_frames"], 4)
        self.assertEqual(sample["last_swap_ns"], 80)

    def test_slowest_interval_is_reported_for_the_window_the_last_sample_closed(self) -> None:
        _swap(self.counter, self.clock, 100, 1)
        _swap(self.counter, self.clock, 110, 1)
        _swap(self.counter, self.clock, 140, 2)   # 30 ns, window 0's slowest
        _swap(self.counter, self.clock, 150, 2)
        # Window 0 is still open, so nothing has been published yet.
        first = self.counter.sample()
        self.assertIsNone(first["slowest_frame_window"])
        self.assertIsNone(first["slowest_frame_interval_ns"])

        # The next swap closes window 0. Its own 50 ns interval ends after the
        # sample, so it belongs to window 1, not window 0.
        _swap(self.counter, self.clock, 200, 3)
        _swap(self.counter, self.clock, 205, 3)
        second = self.counter.sample()
        self.assertEqual(second["slowest_frame_window"], 0)
        self.assertEqual(second["slowest_frame_interval_ns"], 30)

        _swap(self.counter, self.clock, 210, 3)
        third = self.counter.sample()
        self.assertEqual(third["slowest_frame_window"], 1)
        self.assertEqual(third["slowest_frame_interval_ns"], 50)

    def test_a_stall_repeats_the_same_window_instead_of_inventing_one(self) -> None:
        _swap(self.counter, self.clock, 0, 1)
        self.counter.sample()
        _swap(self.counter, self.clock, 10, 1)
        before = self.counter.sample()
        # No swaps between samples: the same closed window comes back, so a
        # sampler can tell nothing new finished.
        again = self.counter.sample()
        self.assertEqual(again["slowest_frame_window"], before["slowest_frame_window"])
        self.assertEqual(again["swap_frames"], before["swap_frames"])
        self.assertEqual(again["last_swap_ns"], 10)

    def test_window_without_an_interval_reports_none(self) -> None:
        _swap(self.counter, self.clock, 0, 1)      # first swap: no interval yet
        self.counter.sample()
        _swap(self.counter, self.clock, 10, 1)     # closes window 0 (empty)
        sample = self.counter.sample()
        self.assertEqual(sample["slowest_frame_window"], 0)
        self.assertIsNone(sample["slowest_frame_interval_ns"])

    def test_record_swap_keeps_object_identities_and_allocates_no_containers(self) -> None:
        _swap(self.counter, self.clock, 0, 1)
        self.counter.sample()
        _swap(self.counter, self.clock, 10, 1)
        closed = self.counter._closed_window
        for index in range(2, 50):
            _swap(self.counter, self.clock, index * 10, index)
        # Without a sample request, the published tuple is never replaced.
        self.assertIs(self.counter._closed_window, closed)
        self.assertFalse(hasattr(self.counter, "__dict__"))


class RecordSwapSourceTests(unittest.TestCase):
    def test_record_swap_has_no_lock_or_container_construction(self) -> None:
        source = (FFT_ROOT / "core" / "frame_rate.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        method = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "record_swap"
        )
        names = {node.id for node in ast.walk(method) if isinstance(node, ast.Name)}
        attributes = {
            node.attr for node in ast.walk(method) if isinstance(node, ast.Attribute)
        }
        self.assertFalse({"Lock", "RLock", "acquire", "dict", "list"} & (names | attributes))
        # The only container built is the closed-window tuple, once per window.
        tuples = [node for node in ast.walk(method) if isinstance(node, ast.Tuple)]
        self.assertEqual(len(tuples), 1)
        self.assertFalse(
            [node for node in ast.walk(method) if isinstance(node, (ast.List, ast.Dict, ast.Set))]
        )


class ControllerStatsTests(unittest.TestCase):
    def test_stats_adds_run_identity_and_revision_and_is_json_safe(self) -> None:
        controller = FftRuntimeController(run_id="run-7")
        controller.revision = 3
        controller.bind_stats(lambda: {"swap_frames": 12, "published_content_frames": 9})
        stats = controller.stats()
        self.assertEqual(json.loads(json.dumps(stats)), stats)
        self.assertEqual(
            stats,
            {
                "protocol_version": 1,
                "run_id": "run-7",
                "visualization_id": "fft",
                "revision": 3,
                "swap_frames": 12,
                "published_content_frames": 9,
            },
        )

    def test_stats_without_a_provider_is_an_unsupported_operation(self) -> None:
        controller = FftRuntimeController()
        with self.assertRaises(RuntimeControlError) as caught:
            controller.stats()
        self.assertEqual(caught.exception.code, "invalid_operation")

    def test_bind_stats_requires_a_callable(self) -> None:
        with self.assertRaises(TypeError):
            FftRuntimeController().bind_stats(None)  # type: ignore[arg-type]

    def test_controller_dispatch_still_rejects_stats_and_unknown_operations(self) -> None:
        # Only the coordinator answers stats; the bridged path is unchanged.
        controller = FftRuntimeController()
        controller.bind_stats(lambda: {})
        for operation in ("stats", "nonsense"):
            with self.assertRaises(RuntimeControlError) as caught:
                controller.handle_runtime_operation(operation, {})
            self.assertEqual(caught.exception.code, "invalid_operation")


if __name__ == "__main__":
    unittest.main()
