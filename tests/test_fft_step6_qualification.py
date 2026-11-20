"""Hardware-free checks for the temporary step-6 qualification recorder."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

import numpy as np


FFT_ROOT = Path(__file__).resolve().parent.parent / "my_programs" / "fft_program"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))

from qualification_metrics import Step6QualificationMetrics  # noqa: E402


def _audio(**overrides):
    values = {
        "sample_generation": 100,
        "last_analyzed_generation": 100,
        "analysis_sequence": 95,
        "callback_status_count": 0,
        "skipped_generation_count": 5,
        "worker_failure_count": 0,
        "transport_drop_count": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class Step6QualificationMetricsTests(unittest.TestCase):
    def test_records_exact_rates_latencies_density_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "run.json"
            metrics = Step6QualificationMetrics(
                output_path=output_path,
                run_label="direct-1",
                source_id="candidate+metrics",
                output_mode="direct",
                settle_seconds=1.0,
                duration_seconds=2.0,
                max_samples=8,
                density_interval=1,
            )
            audio = _audio()
            metrics.arm(audio, now_ns=1_000_000_000)
            self.assertFalse(metrics.begin_frame(audio, now_ns=1_500_000_000))
            self.assertTrue(metrics.begin_frame(audio, now_ns=2_000_000_000))

            bars = np.full(64, 0.5, dtype=np.float32)
            self.assertFalse(metrics.record_frame(
                audio, bars, 64, 1.5,
                2_000_000_000, 2_002_000_000, 2_003_000_000, 2_004_000_000,
                10, time.monotonic_ns(),
            ))
            self.assertTrue(metrics.begin_frame(audio, now_ns=3_000_000_000))
            audio.sample_generation = 200
            audio.last_analyzed_generation = 200
            audio.analysis_sequence = 190
            self.assertTrue(metrics.record_frame(
                audio, bars, 64, 1.5,
                3_000_000_000, 3_003_000_000, 3_004_000_000, 4_000_000_000,
                11, time.monotonic_ns(),
            ))

            metrics.write(audio)
            summary = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["sample_count"], 2)
            self.assertTrue(summary["valid"])
            self.assertTrue(summary["requested_duration_complete"])
            self.assertEqual(summary["termination_reason"], "duration_complete")
            self.assertEqual(summary["rates"]["unique_content_frames"], 2)
            self.assertAlmostEqual(summary["rates"]["swap_fps"], 1.0)
            self.assertEqual(summary["workload"]["visible_pixels_mean"], 3072.0)
            self.assertEqual(summary["audio_counter_deltas"]["analysis_sequence"], 95)
            self.assertTrue(output_path.with_suffix(".npz").exists())

    def test_capacity_exhaustion_is_explicit_and_existing_outputs_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "run.json"
            metrics = Step6QualificationMetrics(
                output_path=output_path,
                run_label="pillow-1",
                source_id="candidate+metrics",
                output_mode="pillow-bulk",
                settle_seconds=0,
                duration_seconds=10,
                max_samples=1,
            )
            audio = _audio()
            metrics.arm(audio, now_ns=0)
            self.assertTrue(metrics.begin_frame(audio, now_ns=1))
            bars = np.zeros(64, dtype=np.float32)
            self.assertFalse(metrics.record_frame(
                audio, bars, 64, 1.5, 1, 2, 2, 3, 1,
                time.monotonic_ns(),
            ))
            self.assertFalse(metrics.begin_frame(audio, now_ns=4))
            self.assertTrue(metrics.capacity_exhausted)
            metrics.write(audio)
            summary = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertFalse(summary["valid"])
            self.assertEqual(summary["termination_reason"], "capacity_exhausted")

            with self.assertRaises(FileExistsError):
                Step6QualificationMetrics(
                    output_path=output_path,
                    run_label="duplicate",
                    source_id="candidate+metrics",
                    output_mode="direct",
                )

    def test_absolute_watchdog_writes_an_explicit_invalid_zero_frame_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "no-frames.json"
            metrics = Step6QualificationMetrics(
                output_path=output_path,
                run_label="direct-no-frames",
                source_id="candidate+metrics",
                output_mode="direct",
                settle_seconds=1,
                duration_seconds=2,
                max_samples=8,
            )
            audio = _audio()
            metrics.arm(audio, now_ns=1_000_000_000)

            self.assertFalse(metrics.poll(audio, now_ns=1_500_000_000))
            self.assertFalse(metrics.poll(audio, now_ns=2_000_000_000))
            self.assertTrue(metrics.poll(audio, now_ns=4_000_000_000))
            metrics.write(audio)

            summary = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertFalse(summary["valid"])
            self.assertTrue(summary["requested_duration_complete"])
            self.assertEqual(summary["termination_reason"], "duration_complete")
            self.assertEqual(summary["sample_count"], 0)

    def test_interruption_during_settle_is_invalid_without_negative_rates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "interrupted.json"
            metrics = Step6QualificationMetrics(
                output_path=output_path,
                run_label="pillow-interrupted",
                source_id="candidate+metrics",
                output_mode="pillow-bulk",
                settle_seconds=60,
                duration_seconds=300,
                max_samples=100_000,
            )
            audio = _audio()
            metrics.arm(audio, now_ns=1_000_000_000)
            metrics.finish(
                audio,
                reason="keyboard_interrupt",
                now_ns=2_000_000_000,
            )
            metrics.write(audio)

            summary = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertFalse(summary["valid"])
            self.assertFalse(summary["requested_duration_complete"])
            self.assertEqual(summary["measured_duration_seconds"], 0.0)
            self.assertEqual(summary["rates"]["swap_fps"], 0.0)


if __name__ == "__main__":
    unittest.main()
