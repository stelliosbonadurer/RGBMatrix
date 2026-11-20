#!/usr/bin/env python3
"""Reproduce the pooled and pairwise FFT step-6 qualification analysis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

import numpy as np  # type: ignore


RUNS = ("direct-1", "pillow-1", "pillow-2", "direct-2")
DIRECT_RUNS = ("direct-1", "direct-2")
PILLOW_RUNS = ("pillow-1", "pillow-2")


def _percentiles(values: np.ndarray) -> dict[str, float]:
    milliseconds = values.astype(np.float64) / 1_000_000.0
    return {
        "p50_ms": float(np.percentile(milliseconds, 50)),
        "p95_ms": float(np.percentile(milliseconds, 95)),
        "p99_ms": float(np.percentile(milliseconds, 99)),
        "max_ms": float(np.max(milliseconds)),
    }


def _improvement(new: float, baseline: float, *, lower_is_better: bool) -> float:
    numerator = baseline - new if lower_is_better else new - baseline
    return 100.0 * numerator / baseline


def _external_summary(path: Path) -> dict[str, Any]:
    temperatures: list[float] = []
    throttled: list[str] = []
    services: list[str] = []
    cpu: list[float] = []
    rss_kib: list[int] = []
    threads: list[int] = []
    process_samples = 0
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        fields = line.split("\t", 4)
        if len(fields) < 4:
            continue
        match = re.search(r"temp=([0-9.]+)'C", fields[1])
        if match:
            temperatures.append(float(match.group(1)))
        throttled.append(fields[2])
        services.append(fields[3])
        if len(fields) != 5 or not fields[4].strip():
            continue
        process = fields[4].split(maxsplit=7)
        if len(process) < 8:
            continue
        process_samples += 1
        cpu.append(float(process[3]))
        rss_kib.append(int(process[4]))
        threads.append(int(process[5]))
    return {
        "sample_count": len(services),
        "process_sample_count": process_samples,
        "service_all_active": bool(services) and all(item == "active" for item in services),
        "throttled_all_clear": bool(throttled) and all(item == "throttled=0x0" for item in throttled),
        "temperature_c_min": min(temperatures),
        "temperature_c_mean": float(np.mean(temperatures)),
        "temperature_c_max": max(temperatures),
        "cpu_percent_mean": float(np.mean(cpu)),
        "cpu_percent_min": min(cpu),
        "cpu_percent_max": max(cpu),
        "rss_kib_mean": float(np.mean(rss_kib)),
        "rss_kib_max": max(rss_kib),
        "threads": sorted(set(threads)),
    }


def analyze(artifact_dir: Path) -> dict[str, Any]:
    summaries = {
        label: json.loads((artifact_dir / f"{label}.json").read_text(encoding="utf-8"))
        for label in RUNS
    }
    raw = {label: np.load(artifact_dir / f"{label}.npz") for label in RUNS}
    external = {
        label: _external_summary(artifact_dir / f"{label}-external.tsv")
        for label in RUNS
    }

    def pooled(labels: tuple[str, ...]) -> dict[str, Any]:
        duration = sum(summaries[label]["measured_duration_seconds"] for label in labels)
        sample_count = sum(summaries[label]["sample_count"] for label in labels)
        unique_frames = sum(
            summaries[label]["rates"]["unique_content_frames"] for label in labels
        )
        published_frames = sum(
            summaries[label]["rates"]["published_content_frames"] for label in labels
        )
        return {
            "duration_seconds": duration,
            "sample_count": sample_count,
            "swap_fps": sample_count / duration,
            "unique_content_fps": unique_frames / duration,
            "published_content_fps": published_frames / duration,
            "renderer": _percentiles(
                np.concatenate([raw[label]["render_ns"] for label in labels])
            ),
            "swap": _percentiles(
                np.concatenate([raw[label]["swap_ns"] for label in labels])
            ),
            "draw_through_swap": _percentiles(
                np.concatenate([raw[label]["total_ns"] for label in labels])
            ),
            "result_age": _percentiles(
                np.concatenate([raw[label]["result_age_ns"] for label in labels])
            ),
        }

    direct = pooled(DIRECT_RUNS)
    pillow = pooled(PILLOW_RUNS)
    pooled_changes = {
        "swap_fps_percent": _improvement(
            pillow["swap_fps"], direct["swap_fps"], lower_is_better=False
        ),
        "unique_content_fps_percent": _improvement(
            pillow["unique_content_fps"],
            direct["unique_content_fps"],
            lower_is_better=False,
        ),
        "published_content_fps_percent": _improvement(
            pillow["published_content_fps"],
            direct["published_content_fps"],
            lower_is_better=False,
        ),
        "renderer_p50_percent": _improvement(
            pillow["renderer"]["p50_ms"],
            direct["renderer"]["p50_ms"],
            lower_is_better=True,
        ),
        "renderer_p95_percent": _improvement(
            pillow["renderer"]["p95_ms"],
            direct["renderer"]["p95_ms"],
            lower_is_better=True,
        ),
        "swap_p50_percent": _improvement(
            pillow["swap"]["p50_ms"],
            direct["swap"]["p50_ms"],
            lower_is_better=True,
        ),
        "swap_p95_percent": _improvement(
            pillow["swap"]["p95_ms"],
            direct["swap"]["p95_ms"],
            lower_is_better=True,
        ),
        "draw_through_swap_p50_percent": _improvement(
            pillow["draw_through_swap"]["p50_ms"],
            direct["draw_through_swap"]["p50_ms"],
            lower_is_better=True,
        ),
        "draw_through_swap_p95_percent": _improvement(
            pillow["draw_through_swap"]["p95_ms"],
            direct["draw_through_swap"]["p95_ms"],
            lower_is_better=True,
        ),
    }

    def pair(pillow_label: str, direct_label: str) -> dict[str, float | bool]:
        pillow_run = summaries[pillow_label]
        direct_run = summaries[direct_label]
        values = {
            "swap_fps_percent": _improvement(
                pillow_run["rates"]["swap_fps"],
                direct_run["rates"]["swap_fps"],
                lower_is_better=False,
            ),
            "draw_through_swap_p50_percent": _improvement(
                pillow_run["latency"]["draw_through_swap"]["p50_ms"],
                direct_run["latency"]["draw_through_swap"]["p50_ms"],
                lower_is_better=True,
            ),
            "draw_through_swap_p95_percent": _improvement(
                pillow_run["latency"]["draw_through_swap"]["p95_ms"],
                direct_run["latency"]["draw_through_swap"]["p95_ms"],
                lower_is_better=True,
            ),
        }
        values["all_three_favor_pillow"] = all(value > 0 for value in values.values())
        return values

    pairs = {
        "direct_1_to_pillow_1": pair("pillow-1", "direct-1"),
        "pillow_2_to_direct_2": pair("pillow-2", "direct-2"),
    }
    external_ok = all(
        item["service_all_active"] and item["throttled_all_clear"]
        for item in external.values()
    )
    gate = {
        "all_runs_valid": all(summaries[label]["valid"] for label in RUNS),
        "all_runs_completed_requested_duration": all(
            summaries[label]["requested_duration_complete"]
            and summaries[label]["termination_reason"] == "duration_complete"
            for label in RUNS
        ),
        "no_sample_capacity_exhaustion": all(
            not summaries[label]["capacity_exhausted"] for label in RUNS
        ),
        "both_pairs_favor_pillow": all(
            item["all_three_favor_pillow"] for item in pairs.values()
        ),
        "pooled_swap_fps_at_least_10_percent_better": (
            pooled_changes["swap_fps_percent"] >= 10.0
        ),
        "pooled_total_p50_at_least_10_percent_better": (
            pooled_changes["draw_through_swap_p50_percent"] >= 10.0
        ),
        "pooled_total_p95_at_least_10_percent_better": (
            pooled_changes["draw_through_swap_p95_percent"] >= 10.0
        ),
        "unique_content_regression_within_5_percent": (
            pooled_changes["unique_content_fps_percent"] >= -5.0
        ),
        "analysis_fraction_at_least_95_percent": all(
            summaries[label]["analyzed_published_fraction"] >= 0.95
            for label in RUNS
        ),
        "result_age_p95_below_two_audio_blocks": all(
            summaries[label]["latency"]["result_age"]["p95_ms"] < 23.22
            for label in RUNS
        ),
        "no_callback_or_worker_failures": all(
            summaries[label]["audio_counter_deltas"]["callback_status_count"] == 0
            and summaries[label]["audio_counter_deltas"]["worker_failure_count"] == 0
            for label in RUNS
        ),
        "service_and_thermal_context_valid": external_ok,
    }
    gate["pillow_candidate_passes"] = all(gate.values())

    return {
        "runs": summaries,
        "external": external,
        "pooled": {"direct": direct, "pillow_bulk": pillow},
        "pooled_pillow_improvement_percent": pooled_changes,
        "directional_pairs": pairs,
        "gate": gate,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.artifact_dir), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
