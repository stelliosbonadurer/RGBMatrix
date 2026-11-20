"""Temporary, opt-in metrics for the step-6 Pi output-path comparison.

This module is qualification scaffolding, not product telemetry.  It is loaded only
when the output JSON path environment variable is present and is intentionally kept
out of the committed candidate source identity.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import time
from typing import Any, Optional

import numpy as np  # type: ignore


METRICS_PATH_ENVIRONMENT = "RGBMATRIX_FFT_STEP6_METRICS_PATH"
RUN_LABEL_ENVIRONMENT = "RGBMATRIX_FFT_STEP6_RUN_LABEL"
SOURCE_ID_ENVIRONMENT = "RGBMATRIX_FFT_STEP6_SOURCE_ID"
SETTLE_SECONDS_ENVIRONMENT = "RGBMATRIX_FFT_STEP6_SETTLE_SECONDS"
DURATION_SECONDS_ENVIRONMENT = "RGBMATRIX_FFT_STEP6_DURATION_SECONDS"
MAX_SAMPLES_ENVIRONMENT = "RGBMATRIX_FFT_STEP6_MAX_SAMPLES"


def _percentiles(values: np.ndarray) -> dict[str, float]:
    if values.size == 0:
        return {}
    milliseconds = values.astype(np.float64) / 1_000_000.0
    return {
        "p50_ms": float(np.percentile(milliseconds, 50)),
        "p95_ms": float(np.percentile(milliseconds, 95)),
        "p99_ms": float(np.percentile(milliseconds, 99)),
        "max_ms": float(np.max(milliseconds)),
    }


def _audio_counters(audio: Any) -> dict[str, int]:
    return {
        "sample_generation": int(audio.sample_generation),
        "last_analyzed_generation": int(audio.last_analyzed_generation),
        "analysis_sequence": int(audio.analysis_sequence),
        "callback_status_count": int(audio.callback_status_count),
        "skipped_generation_count": int(audio.skipped_generation_count),
        "worker_failure_count": int(audio.worker_failure_count),
        "transport_drop_count": int(audio.transport_drop_count),
    }


class Step6QualificationMetrics:
    """Fixed-capacity exact samples for one bounded qualification run."""

    def __init__(
        self,
        output_path: Path,
        run_label: str,
        source_id: str,
        output_mode: str,
        settle_seconds: float = 60.0,
        duration_seconds: float = 300.0,
        max_samples: int = 100_000,
        density_interval: int = 256,
    ) -> None:
        if not output_path.is_absolute():
            raise ValueError("qualification metrics path must be absolute")
        if output_path.suffix != ".json":
            raise ValueError("qualification metrics path must end in .json")
        if not output_path.parent.is_dir():
            raise ValueError("qualification metrics directory must already exist")
        if output_path.exists() or output_path.with_suffix(".npz").exists():
            raise FileExistsError("qualification metrics output already exists")
        if not run_label or not source_id:
            raise ValueError("qualification run label and source identity are required")
        if not math.isfinite(settle_seconds) or settle_seconds < 0:
            raise ValueError("settle seconds must be finite and non-negative")
        if not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise ValueError("duration seconds must be finite and positive")
        if max_samples <= 0 or density_interval <= 0:
            raise ValueError("sample capacities must be positive")

        self.output_path = output_path
        self.raw_path = output_path.with_suffix(".npz")
        self.run_label = run_label
        self.source_id = source_id
        self.output_mode = output_mode
        self.settle_ns = int(settle_seconds * 1_000_000_000)
        self.duration_ns = int(duration_seconds * 1_000_000_000)
        self.max_samples = max_samples
        self.density_interval = density_interval

        self.render_ns = np.empty(max_samples, dtype=np.uint64)
        self.swap_ns = np.empty(max_samples, dtype=np.uint64)
        self.total_ns = np.empty(max_samples, dtype=np.uint64)
        self.completed_ns = np.empty(max_samples, dtype=np.uint64)
        self.result_age_ns = np.empty(max_samples, dtype=np.uint64)
        self.analysis_sequences = np.empty(max_samples, dtype=np.uint64)
        density_capacity = (max_samples // density_interval) + 2
        self.density_frame_indices = np.empty(density_capacity, dtype=np.uint64)
        self.visible_pixel_samples = np.empty(density_capacity, dtype=np.uint16)

        self.sample_count = 0
        self.density_count = 0
        self.armed_ns: Optional[int] = None
        self.measurement_start_ns: Optional[int] = None
        self.measurement_deadline_ns: Optional[int] = None
        self.measurement_end_ns: Optional[int] = None
        self.start_wall_time: Optional[float] = None
        self.end_wall_time: Optional[float] = None
        self.start_counters: Optional[dict[str, int]] = None
        self.end_counters: Optional[dict[str, int]] = None
        self.capacity_exhausted = False
        self.requested_duration_complete = False
        self.termination_reason: Optional[str] = None

    @classmethod
    def from_environment(
        cls,
        output_mode: str,
    ) -> Optional["Step6QualificationMetrics"]:
        path_text = os.environ.get(METRICS_PATH_ENVIRONMENT)
        if path_text is None:
            return None
        return cls(
            output_path=Path(path_text),
            run_label=os.environ.get(RUN_LABEL_ENVIRONMENT, ""),
            source_id=os.environ.get(SOURCE_ID_ENVIRONMENT, ""),
            output_mode=output_mode,
            settle_seconds=float(os.environ.get(SETTLE_SECONDS_ENVIRONMENT, "60")),
            duration_seconds=float(os.environ.get(DURATION_SECONDS_ENVIRONMENT, "300")),
            max_samples=int(os.environ.get(MAX_SAMPLES_ENVIRONMENT, "100000")),
        )

    def arm(self, audio: Any, now_ns: Optional[int] = None) -> None:
        if self.armed_ns is not None:
            raise RuntimeError("qualification metrics are already armed")
        current_ns = time.perf_counter_ns() if now_ns is None else now_ns
        self.armed_ns = current_ns
        self.measurement_start_ns = current_ns + self.settle_ns
        self.measurement_deadline_ns = self.measurement_start_ns + self.duration_ns
        # Counters are reset at the first measured frame after the settle period.
        del audio

    def poll(self, audio: Any, now_ns: Optional[int] = None) -> bool:
        """Update the absolute watchdog and return whether the run must stop."""

        current_ns = time.perf_counter_ns() if now_ns is None else now_ns
        if self.measurement_start_ns is None or self.measurement_deadline_ns is None:
            raise RuntimeError("qualification metrics must be armed before rendering")
        if current_ns < self.measurement_start_ns:
            return False
        if self.start_counters is None:
            self.start_counters = _audio_counters(audio)
            self.start_wall_time = time.time()
        if current_ns >= self.measurement_deadline_ns:
            self.finish(
                audio,
                reason="duration_complete",
                now_ns=current_ns,
                requested_duration_complete=True,
            )
            return True
        if self.sample_count >= self.max_samples:
            self.capacity_exhausted = True
            self.finish(audio, reason="capacity_exhausted", now_ns=current_ns)
            return True
        return False

    def begin_frame(self, audio: Any, now_ns: Optional[int] = None) -> bool:
        """Return whether this frame belongs to the active measurement window."""

        current_ns = time.perf_counter_ns() if now_ns is None else now_ns
        if self.poll(audio, now_ns=current_ns):
            return False
        if self.measurement_start_ns is None:
            raise RuntimeError("qualification metrics must be armed before rendering")
        return current_ns >= self.measurement_start_ns

    @property
    def complete(self) -> bool:
        return self.termination_reason is not None

    def finish(
        self,
        audio: Any,
        reason: str,
        now_ns: Optional[int] = None,
        requested_duration_complete: bool = False,
    ) -> None:
        """Finalize counters once with an explicit termination reason."""

        if self.complete:
            return
        current_ns = time.perf_counter_ns() if now_ns is None else now_ns
        self.measurement_end_ns = current_ns
        self.end_wall_time = time.time()
        self.end_counters = _audio_counters(audio)
        self.requested_duration_complete = requested_duration_complete
        self.termination_reason = reason

    def record_frame(
        self,
        audio: Any,
        smoothed_bars: np.ndarray,
        height: int,
        overflow_multiplier: float,
        draw_started_ns: int,
        draw_completed_ns: int,
        swap_started_ns: int,
        swap_completed_ns: int,
        analysis_sequence: int,
        analysis_publish_time_ns: int,
    ) -> bool:
        if self.start_counters is None or self.measurement_deadline_ns is None:
            raise RuntimeError("cannot record before the measurement window starts")
        if self.sample_count >= self.max_samples:
            self.capacity_exhausted = True
            self.finish(audio, reason="capacity_exhausted", now_ns=swap_completed_ns)
            return True

        index = self.sample_count
        self.render_ns[index] = draw_completed_ns - draw_started_ns
        self.swap_ns[index] = swap_completed_ns - swap_started_ns
        self.total_ns[index] = swap_completed_ns - draw_started_ns
        self.completed_ns[index] = swap_completed_ns
        self.result_age_ns[index] = max(
            0,
            time.monotonic_ns() - analysis_publish_time_ns,
        )
        self.analysis_sequences[index] = analysis_sequence
        self.sample_count += 1

        if index % self.density_interval == 0:
            visible_pixels = 0
            for value in smoothed_bars:
                if np.isnan(value):
                    continue
                total_pixels = int(value * height * overflow_multiplier)
                visible_pixels += min(max(total_pixels, 0), height)
            density_index = self.density_count
            self.density_frame_indices[density_index] = index
            self.visible_pixel_samples[density_index] = visible_pixels
            self.density_count += 1

        if swap_completed_ns >= self.measurement_deadline_ns:
            self.finish(
                audio,
                reason="duration_complete",
                now_ns=swap_completed_ns,
                requested_duration_complete=True,
            )
            return True
        return False

    def write(self, audio: Any) -> None:
        if self.measurement_start_ns is None:
            raise RuntimeError("qualification metrics were never armed")
        if not self.complete:
            self.finish(audio, reason="application_exit")
        if self.end_counters is None:
            self.end_counters = _audio_counters(audio)
        if self.measurement_end_ns is None:
            self.measurement_end_ns = time.perf_counter_ns()
        if self.end_wall_time is None:
            self.end_wall_time = time.time()

        count = self.sample_count
        sequences = self.analysis_sequences[:count]
        unique_content_frames = (
            1 + int(np.count_nonzero(sequences[1:] != sequences[:-1]))
            if count > 0
            else 0
        )
        elapsed_seconds = max(
            0.0,
            (self.measurement_end_ns - self.measurement_start_ns)
            / 1_000_000_000.0,
        )
        counters_start = self.start_counters or _audio_counters(audio)
        counters_end = self.end_counters
        counter_deltas = {
            key: counters_end[key] - counters_start[key]
            for key in counters_start
        }
        callback_generations = counter_deltas["sample_generation"]
        published_frames = counter_deltas["analysis_sequence"]
        analyzed_fraction = (
            published_frames / callback_generations
            if callback_generations > 0
            else 0.0
        )
        valid = (
            self.requested_duration_complete
            and not self.capacity_exhausted
            and count > 0
            and self.termination_reason == "duration_complete"
        )
        density_values = self.visible_pixel_samples[:self.density_count]

        summary = {
            "schema_version": 1,
            "run_label": self.run_label,
            "source_id": self.source_id,
            "output_mode": self.output_mode,
            "settle_seconds": self.settle_ns / 1_000_000_000.0,
            "requested_duration_seconds": self.duration_ns / 1_000_000_000.0,
            "measured_duration_seconds": elapsed_seconds,
            "max_samples": self.max_samples,
            "sample_count": count,
            "capacity_exhausted": self.capacity_exhausted,
            "requested_duration_complete": self.requested_duration_complete,
            "termination_reason": self.termination_reason,
            "valid": valid,
            "start_wall_time": self.start_wall_time,
            "end_wall_time": self.end_wall_time,
            "rates": {
                "swap_fps": count / elapsed_seconds if elapsed_seconds else 0.0,
                "unique_content_fps": (
                    unique_content_frames / elapsed_seconds
                    if elapsed_seconds
                    else 0.0
                ),
                "published_content_fps": (
                    published_frames / elapsed_seconds
                    if elapsed_seconds
                    else 0.0
                ),
                "unique_content_frames": unique_content_frames,
                "published_content_frames": published_frames,
            },
            "latency": {
                "renderer": _percentiles(self.render_ns[:count]),
                "swap": _percentiles(self.swap_ns[:count]),
                "draw_through_swap": _percentiles(self.total_ns[:count]),
                "result_age": _percentiles(self.result_age_ns[:count]),
            },
            "workload": {
                "density_sample_count": self.density_count,
                "visible_pixels_mean": (
                    float(np.mean(density_values)) if self.density_count else None
                ),
                "visible_pixels_p50": (
                    float(np.percentile(density_values, 50))
                    if self.density_count
                    else None
                ),
                "visible_pixels_p95": (
                    float(np.percentile(density_values, 95))
                    if self.density_count
                    else None
                ),
            },
            "audio_counters_start": counters_start,
            "audio_counters_end": counters_end,
            "audio_counter_deltas": counter_deltas,
            "analyzed_published_fraction": analyzed_fraction,
            "raw_samples": str(self.raw_path),
        }

        raw_temp_path = self.raw_path.with_name(
            f".{self.raw_path.name}.{os.getpid()}.tmp.npz"
        )
        json_temp_path = self.output_path.with_name(
            f".{self.output_path.name}.{os.getpid()}.tmp"
        )
        np.savez_compressed(
            raw_temp_path,
            render_ns=self.render_ns[:count],
            swap_ns=self.swap_ns[:count],
            total_ns=self.total_ns[:count],
            completed_ns=self.completed_ns[:count],
            result_age_ns=self.result_age_ns[:count],
            analysis_sequences=sequences,
            density_frame_indices=self.density_frame_indices[:self.density_count],
            visible_pixel_samples=self.visible_pixel_samples[:self.density_count],
        )
        with json_temp_path.open("x", encoding="utf-8") as output:
            json.dump(summary, output, indent=2, sort_keys=True)
            output.write("\n")
        os.replace(raw_temp_path, self.raw_path)
        os.replace(json_temp_path, self.output_path)


__all__ = [
    "METRICS_PATH_ENVIRONMENT",
    "Step6QualificationMetrics",
]
