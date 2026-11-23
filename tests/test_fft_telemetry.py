"""Hardware-free checks for the opt-in FFT frame telemetry recorder and its CLI."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

try:
    import numpy as np
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("FFT telemetry tests require NumPy") from exc


FFT_ROOT = Path(__file__).resolve().parent.parent / "my_programs" / "fft_program"
MAIN_PATH = FFT_ROOT / "main.py"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))


# Loading core.telemetry executes core/__init__.py, which imports the audio
# processor and therefore sounddevice. Stub it while importing the recorder and the
# entry point, then restore the prior module table.
_sounddevice = ModuleType("sounddevice")
_prior_sounddevice = sys.modules.get("sounddevice")
_prior_core_modules = {
    name: module
    for name, module in sys.modules.items()
    if name == "core" or name.startswith("core.")
}
sys.modules["sounddevice"] = _sounddevice
try:
    import core.telemetry as telemetry_module  # noqa: E402
    from core.telemetry import (  # noqa: E402
        FrameTelemetry,
        TelemetryConfigurationError,
        add_telemetry_arguments,
        check_retired_environment,
        telemetry_from_args,
    )

    _main_spec = importlib.util.spec_from_file_location(
        "fft_main_under_telemetry_test", MAIN_PATH
    )
    fft_main = importlib.util.module_from_spec(_main_spec)
    _main_spec.loader.exec_module(fft_main)
finally:
    if _prior_sounddevice is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = _prior_sounddevice
    for _module_name in tuple(sys.modules):
        if _module_name == "core" or _module_name.startswith("core."):
            sys.modules.pop(_module_name)
    sys.modules.update(_prior_core_modules)


# The e9c0442 recorder's key tree plus "schema" (D-005) and, in version 3, "audio". Percentile dictionaries are
# populated only when the run has samples.
_PERCENTILE_KEYS = {"p50_ms", "p95_ms", "p99_ms", "max_ms"}
_COUNTER_KEYS = {
    "sample_generation",
    "last_analyzed_generation",
    "analysis_sequence",
    "callback_status_count",
    "skipped_generation_count",
    "worker_failure_count",
    "transport_drop_count",
}
EXPECTED_KEY_TREE = {
    "schema": None,
    "schema_version": None,
    "run_label": None,
    "source_id": None,
    "output_mode": None,
    "settle_seconds": None,
    "requested_duration_seconds": None,
    "measured_duration_seconds": None,
    "max_samples": None,
    "sample_count": None,
    "capacity_exhausted": None,
    "requested_duration_complete": None,
    "termination_reason": None,
    "valid": None,
    "start_wall_time": None,
    "end_wall_time": None,
    "rates": {
        "swap_fps": None,
        "unique_content_fps": None,
        "published_content_fps": None,
        "unique_content_frames": None,
        "published_content_frames": None,
    },
    "latency": {
        "renderer": {key: None for key in _PERCENTILE_KEYS},
        "swap": {key: None for key in _PERCENTILE_KEYS},
        "draw_through_swap": {key: None for key in _PERCENTILE_KEYS},
        "result_age": {key: None for key in _PERCENTILE_KEYS},
    },
    "workload": {
        "density_sample_count": None,
        "visible_pixels_mean": None,
        "visible_pixels_p50": None,
        "visible_pixels_p95": None,
    },
    "audio": {"sample_rate": None, "block_size": None},
    "audio_counters_start": {key: None for key in _COUNTER_KEYS},
    "audio_counters_end": {key: None for key in _COUNTER_KEYS},
    "audio_counter_deltas": {key: None for key in _COUNTER_KEYS},
    "analyzed_published_fraction": None,
    "raw_samples": None,
}
EXPECTED_NPZ_ARRAYS = {
    "render_ns",
    "swap_ns",
    "total_ns",
    "completed_ns",
    "result_age_ns",
    "analysis_sequences",
    "density_frame_indices",
    "visible_pixel_samples",
}


def _key_tree(value):
    if isinstance(value, dict):
        return {key: _key_tree(item) for key, item in value.items()}
    return None


def _audio(**overrides):
    values = {
        "sample_generation": 100,
        "last_analyzed_generation": 100,
        "analysis_sequence": 95,
        "callback_status_count": 0,
        "skipped_generation_count": 5,
        "worker_failure_count": 0,
        "transport_drop_count": 0,
        "sample_rate": 44100,
        "audio_settings": SimpleNamespace(block_size=512),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _two_frame_run(output_path: Path) -> tuple[FrameTelemetry, SimpleNamespace]:
    """Settle 1 s, measure 2 s, and record two frames ending at the deadline."""

    telemetry = FrameTelemetry(
        output_path=output_path,
        run_label="direct-1",
        source_id="candidate",
        output_mode="direct",
        settle_seconds=1.0,
        duration_seconds=2.0,
        max_samples=8,
        density_interval=1,
    )
    audio = _audio()
    telemetry.arm(audio, now_ns=1_000_000_000)
    bars = np.full(64, 0.5, dtype=np.float32)
    telemetry.begin_frame(audio, now_ns=2_000_000_000)
    telemetry.record_frame(
        audio, bars, 64, 1.5,
        2_000_000_000, 2_002_000_000, 2_003_000_000, 2_004_000_000,
        10, time.monotonic_ns(),
    )
    telemetry.begin_frame(audio, now_ns=3_000_000_000)
    audio.sample_generation = 200
    audio.last_analyzed_generation = 200
    audio.analysis_sequence = 190
    telemetry.record_frame(
        audio, bars, 64, 1.5,
        3_000_000_000, 3_003_000_000, 3_004_000_000, 4_000_000_000,
        11, time.monotonic_ns(),
    )
    return telemetry, audio


class FrameTelemetryRecorderTests(unittest.TestCase):
    def test_records_exact_rates_latencies_density_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "run.json"
            telemetry = FrameTelemetry(
                output_path=output_path,
                run_label="direct-1",
                source_id="candidate",
                output_mode="direct",
                settle_seconds=1.0,
                duration_seconds=2.0,
                max_samples=8,
                density_interval=1,
            )
            audio = _audio()
            telemetry.arm(audio, now_ns=1_000_000_000)
            self.assertFalse(telemetry.begin_frame(audio, now_ns=1_500_000_000))
            self.assertTrue(telemetry.begin_frame(audio, now_ns=2_000_000_000))

            bars = np.full(64, 0.5, dtype=np.float32)
            self.assertFalse(telemetry.record_frame(
                audio, bars, 64, 1.5,
                2_000_000_000, 2_002_000_000, 2_003_000_000, 2_004_000_000,
                10, time.monotonic_ns(),
            ))
            self.assertTrue(telemetry.begin_frame(audio, now_ns=3_000_000_000))
            audio.sample_generation = 200
            audio.last_analyzed_generation = 200
            audio.analysis_sequence = 190
            self.assertTrue(telemetry.record_frame(
                audio, bars, 64, 1.5,
                3_000_000_000, 3_003_000_000, 3_004_000_000, 4_000_000_000,
                11, time.monotonic_ns(),
            ))

            telemetry.write(audio)
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
            telemetry = FrameTelemetry(
                output_path=output_path,
                run_label="pillow-1",
                source_id="candidate",
                output_mode="pillow-bulk",
                settle_seconds=0,
                duration_seconds=10,
                max_samples=1,
            )
            audio = _audio()
            telemetry.arm(audio, now_ns=0)
            self.assertTrue(telemetry.begin_frame(audio, now_ns=1))
            bars = np.zeros(64, dtype=np.float32)
            self.assertFalse(telemetry.record_frame(
                audio, bars, 64, 1.5, 1, 2, 2, 3, 1,
                time.monotonic_ns(),
            ))
            self.assertFalse(telemetry.begin_frame(audio, now_ns=4))
            self.assertTrue(telemetry.capacity_exhausted)
            telemetry.write(audio)
            summary = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertFalse(summary["valid"])
            self.assertEqual(summary["termination_reason"], "capacity_exhausted")

            with self.assertRaises(FileExistsError):
                FrameTelemetry(
                    output_path=output_path,
                    run_label="duplicate",
                    source_id="candidate",
                    output_mode="direct",
                )

    def test_absolute_watchdog_writes_an_explicit_invalid_zero_frame_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "no-frames.json"
            telemetry = FrameTelemetry(
                output_path=output_path,
                run_label="direct-no-frames",
                source_id="candidate",
                output_mode="direct",
                settle_seconds=1,
                duration_seconds=2,
                max_samples=8,
            )
            audio = _audio()
            telemetry.arm(audio, now_ns=1_000_000_000)

            self.assertFalse(telemetry.poll(audio, now_ns=1_500_000_000))
            self.assertFalse(telemetry.poll(audio, now_ns=2_000_000_000))
            self.assertTrue(telemetry.poll(audio, now_ns=4_000_000_000))
            telemetry.write(audio)

            summary = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertFalse(summary["valid"])
            self.assertTrue(summary["requested_duration_complete"])
            self.assertEqual(summary["termination_reason"], "duration_complete")
            self.assertEqual(summary["sample_count"], 0)

    def test_interruption_during_settle_is_invalid_without_negative_rates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "interrupted.json"
            telemetry = FrameTelemetry(
                output_path=output_path,
                run_label="pillow-interrupted",
                source_id="candidate",
                output_mode="pillow-bulk",
                settle_seconds=60,
                duration_seconds=300,
                max_samples=100_000,
            )
            audio = _audio()
            telemetry.arm(audio, now_ns=1_000_000_000)
            telemetry.finish(
                audio,
                reason="keyboard_interrupt",
                now_ns=2_000_000_000,
            )
            telemetry.write(audio)

            summary = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertFalse(summary["valid"])
            self.assertFalse(summary["requested_duration_complete"])
            self.assertEqual(summary["measured_duration_seconds"], 0.0)
            self.assertEqual(summary["rates"]["swap_fps"], 0.0)


class TelemetrySchemaTests(unittest.TestCase):
    def test_summary_key_tree_and_sidecar_arrays_are_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "run.json"
            telemetry, audio = _two_frame_run(output_path)
            telemetry.write(audio)

            summary = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["schema"], "rgbmatrix-fft-telemetry")
            self.assertEqual(summary["schema_version"], 3)
            self.assertEqual(_key_tree(summary), EXPECTED_KEY_TREE)
            self.assertEqual(summary["audio"], {"sample_rate": 44100, "block_size": 512})
            self.assertEqual(summary["raw_samples"], str(output_path.with_suffix(".npz")))

            with np.load(output_path.with_suffix(".npz")) as raw:
                self.assertEqual(set(raw.files), EXPECTED_NPZ_ARRAYS)
                self.assertEqual(raw["render_ns"].tolist(), [2_000_000, 3_000_000])
                self.assertEqual(raw["analysis_sequences"].tolist(), [10, 11])

    def test_zero_sample_run_keeps_the_key_tree_with_empty_percentiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "empty.json"
            telemetry = FrameTelemetry(
                output_path=output_path,
                run_label="empty",
                source_id="candidate",
                output_mode="direct",
                settle_seconds=0,
                duration_seconds=1,
                max_samples=4,
            )
            audio = _audio()
            telemetry.arm(audio, now_ns=0)
            telemetry.poll(audio, now_ns=2_000_000_000)
            telemetry.write(audio)

            summary = json.loads(output_path.read_text(encoding="utf-8"))
            expected = dict(EXPECTED_KEY_TREE)
            expected["latency"] = {name: {} for name in EXPECTED_KEY_TREE["latency"]}
            self.assertEqual(_key_tree(summary), expected)
            self.assertIsNone(summary["workload"]["visible_pixels_mean"])

    def test_sample_rate_is_null_when_the_stream_was_never_set_up(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "unstarted.json"
            telemetry = FrameTelemetry(
                output_path=output_path,
                run_label="unstarted",
                source_id="candidate",
                output_mode="direct",
                settle_seconds=0,
                duration_seconds=1,
                max_samples=4,
            )
            audio = _audio(sample_rate=0)
            telemetry.arm(audio, now_ns=0)
            telemetry.write(audio)

            summary = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["audio"], {"sample_rate": None, "block_size": 512})


class PiCheckGateCompatibilityTests(unittest.TestCase):
    def test_pi_check_gates_find_every_field_in_a_real_summary(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "pi_cycle_remote_for_schema_test",
            FFT_ROOT.parent.parent / "tools" / "pi" / "remote.py",
        )
        remote = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = remote
        try:
            spec.loader.exec_module(remote)
        finally:
            sys.modules.pop(spec.name, None)
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "run.json"
            telemetry, audio = _two_frame_run(output_path)
            telemetry.write(audio)
            summary = json.loads(output_path.read_text(encoding="utf-8"))

        run = {"label": "direct-1", "mode": "direct", "returncode": 0, "throttled": 0}
        evaluation = remote.evaluate_run(run, summary, 0)
        self.assertTrue(evaluation["gates"]["schema"])
        for field in ("frames", "swap_fps", "unique_fps", "analyzed_published_fraction",
                      "result_age_p95_ms", "result_age_limit_ms", "callback_failures",
                      "worker_failures"):
            self.assertIsNotNone(evaluation[field], field)
        self.assertAlmostEqual(evaluation["result_age_limit_ms"], 2 * 512 / 44100 * 1000)


class TelemetryWriteFailureTests(unittest.TestCase):
    def test_summary_write_failure_raises_and_leaves_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "run.json"
            telemetry, audio = _two_frame_run(output_path)
            with mock.patch.object(
                telemetry_module.json, "dump", side_effect=OSError("disk full")
            ):
                with self.assertRaises(OSError):
                    telemetry.write(audio)
            self.assertEqual(os.listdir(directory), [])

    def test_final_replace_failure_removes_the_already_published_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "run.json"
            telemetry, audio = _two_frame_run(output_path)
            real_replace = os.replace
            calls = []

            def failing_second_replace(source, destination):
                calls.append(destination)
                if len(calls) == 2:
                    raise OSError("replace failed")
                real_replace(source, destination)

            with mock.patch.object(
                telemetry_module.os, "replace", side_effect=failing_second_replace
            ):
                with self.assertRaises(OSError):
                    telemetry.write(audio)
            self.assertEqual(len(calls), 2)
            self.assertEqual(os.listdir(directory), [])

    def test_missing_directory_at_write_time_raises(self) -> None:
        directory = tempfile.mkdtemp()
        output_path = Path(directory) / "run.json"
        telemetry, audio = _two_frame_run(output_path)
        os.rmdir(directory)
        with self.assertRaises(OSError):
            telemetry.write(audio)
        self.assertFalse(Path(directory).exists())


class TelemetryOptionTests(unittest.TestCase):
    def _parse(self, *argv: str) -> argparse.Namespace:
        parser = argparse.ArgumentParser()
        add_telemetry_arguments(parser)
        return parser.parse_args(list(argv))

    def test_off_by_default_without_allocation(self) -> None:
        with mock.patch.object(telemetry_module, "FrameTelemetry") as constructor:
            self.assertIsNone(telemetry_from_args(self._parse(), output_mode="direct"))
        constructor.assert_not_called()

    def test_defaults_and_overrides_reach_the_recorder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "run.json"
            telemetry = telemetry_from_args(
                self._parse(
                    "--telemetry-out", str(output_path),
                    "--telemetry-label", "direct-1",
                    "--telemetry-source-id", "abc1234",
                ),
                output_mode="pillow-bulk",
            )
            self.assertIsInstance(telemetry, FrameTelemetry)
            self.assertEqual(telemetry.output_path, output_path)
            self.assertEqual(telemetry.run_label, "direct-1")
            self.assertEqual(telemetry.source_id, "abc1234")
            self.assertEqual(telemetry.output_mode, "pillow-bulk")
            self.assertEqual(telemetry.settle_ns, 60_000_000_000)
            self.assertEqual(telemetry.duration_ns, 300_000_000_000)
            self.assertEqual(telemetry.max_samples, 100_000)

            overridden = telemetry_from_args(
                self._parse(
                    "--telemetry-out", str(Path(directory) / "short.json"),
                    "--telemetry-label", "direct-2",
                    "--telemetry-source-id", "abc1234",
                    "--telemetry-settle", "0",
                    "--telemetry-duration", "30",
                    "--telemetry-max-samples", "10000",
                ),
                output_mode="direct",
            )
            self.assertEqual(overridden.settle_ns, 0)
            self.assertEqual(overridden.duration_ns, 30_000_000_000)
            self.assertEqual(overridden.max_samples, 10_000)
            self.assertEqual(overridden.render_ns.shape, (10_000,))

    def test_dependent_flags_without_output_are_rejected(self) -> None:
        for argv in (
            ("--telemetry-label", "x"),
            ("--telemetry-source-id", "x"),
            ("--telemetry-settle", "5"),
            ("--telemetry-duration", "30"),
            ("--telemetry-max-samples", "10"),
        ):
            with self.subTest(argv=argv):
                with self.assertRaisesRegex(
                    TelemetryConfigurationError,
                    rf"{argv[0]} require --telemetry-out",
                ):
                    telemetry_from_args(self._parse(*argv), output_mode="direct")

    def test_output_requires_label_and_source_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "run.json")
            cases = {
                "--telemetry-label and --telemetry-source-id": (),
                "--telemetry-source-id": ("--telemetry-label", "x"),
                "--telemetry-label": ("--telemetry-source-id", "x"),
            }
            for missing, extra in cases.items():
                with self.subTest(missing=missing):
                    with self.assertRaisesRegex(
                        TelemetryConfigurationError,
                        f"requires {missing}$",
                    ):
                        telemetry_from_args(
                            self._parse("--telemetry-out", output, *extra),
                            output_mode="direct",
                        )
            self.assertEqual(os.listdir(directory), [])

    def test_invalid_paths_and_values_are_configuration_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            existing_json = Path(directory) / "exists.json"
            existing_json.write_text("{}", encoding="utf-8")
            (Path(directory) / "sidecar.npz").write_bytes(b"")
            cases = {
                "relative": ("--telemetry-out", "run.json"),
                "suffix": ("--telemetry-out", str(Path(directory) / "run.txt")),
                "missing directory": (
                    "--telemetry-out", str(Path(directory) / "absent" / "run.json"),
                ),
                "existing json": ("--telemetry-out", str(existing_json)),
                "existing npz": (
                    "--telemetry-out", str(Path(directory) / "sidecar.json"),
                ),
                "nan settle": (
                    "--telemetry-out", str(Path(directory) / "a.json"),
                    "--telemetry-settle", "nan",
                ),
                "negative settle": (
                    "--telemetry-out", str(Path(directory) / "b.json"),
                    "--telemetry-settle", "-1",
                ),
                "zero duration": (
                    "--telemetry-out", str(Path(directory) / "c.json"),
                    "--telemetry-duration", "0",
                ),
                "infinite duration": (
                    "--telemetry-out", str(Path(directory) / "d.json"),
                    "--telemetry-duration", "inf",
                ),
                "zero samples": (
                    "--telemetry-out", str(Path(directory) / "e.json"),
                    "--telemetry-max-samples", "0",
                ),
            }
            for name, argv in cases.items():
                with self.subTest(case=name):
                    with self.assertRaisesRegex(
                        TelemetryConfigurationError, r"^telemetry: "
                    ):
                        telemetry_from_args(
                            self._parse(
                                *argv,
                                "--telemetry-label", "x",
                                "--telemetry-source-id", "y",
                            ),
                            output_mode="direct",
                        )

    def test_retired_step6_variables_are_refused_by_name(self) -> None:
        check_retired_environment({"RGBMATRIX_FFT_OUTPUT_PATH": "direct", "HOME": "/"})
        with self.assertRaises(TelemetryConfigurationError) as caught:
            check_retired_environment({
                "RGBMATRIX_FFT_STEP6_SETTLE_SECONDS": "5",
                "RGBMATRIX_FFT_STEP6_METRICS_PATH": "/run/x.json",
            })
        message = str(caught.exception)
        self.assertIn(
            "RGBMATRIX_FFT_STEP6_METRICS_PATH, RGBMATRIX_FFT_STEP6_SETTLE_SECONDS",
            message,
        )
        self.assertIn("--telemetry-out", message)


class MainTelemetryWiringTests(unittest.TestCase):
    """Drive main() up to the matrix boundary without hardware."""

    def _run_main(self, argv, environment=None, source_error=None):
        clean_environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("RGBMATRIX_FFT_STEP6_")
        }
        clean_environment.update(environment or {})
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, clean_environment, clear=True))
            stack.enter_context(mock.patch.object(sys, "argv", ["main.py", *argv]))
            load_state = stack.enter_context(
                mock.patch.object(fft_main, "load_runtime_state", return_value=None)
            )
            initialize = stack.enter_context(
                mock.patch.object(
                    fft_main.MatrixApp, "initialize_matrix", return_value=False
                )
            )
            if source_error is None:
                resolve_source = stack.enter_context(
                    mock.patch.object(
                        fft_main,
                        "resolve_audio_source",
                        side_effect=lambda _source_id, settings: settings,
                    )
                )
            else:
                resolve_source = stack.enter_context(
                    mock.patch.object(
                        fft_main,
                        "resolve_audio_source",
                        side_effect=source_error,
                    )
                )
            real_telemetry_from_args = fft_main.telemetry_from_args
            built_values = []

            def capture_telemetry(*args, **kwargs):
                value = real_telemetry_from_args(*args, **kwargs)
                built_values.append(value)
                return value

            built = stack.enter_context(
                mock.patch.object(
                    fft_main, "telemetry_from_args", side_effect=capture_telemetry
                )
            )
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            with self.assertRaises(SystemExit) as exited:
                fft_main.main()
        return SimpleNamespace(
            code=exited.exception.code,
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
            load_state=load_state,
            initialize=initialize,
            resolve_source=resolve_source,
            built=built,
            built_values=built_values,
        )

    def test_help_lists_every_telemetry_flag(self) -> None:
        result = self._run_main(["--help"])
        self.assertEqual(result.code, 0)
        for flag in (
            "--telemetry-out",
            "--telemetry-label",
            "--telemetry-source-id",
            "--telemetry-settle",
            "--telemetry-duration",
            "--telemetry-max-samples",
        ):
            self.assertIn(flag, result.stdout)
        help_text = " ".join(result.stdout.split())
        for default in ("(default 60)", "(default 300)", "(default 100000)"):
            self.assertIn(default, help_text)
        result.initialize.assert_not_called()

    def test_configuration_errors_exit_2_before_state_or_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            # argparse's usage line lists every telemetry flag, so match the
            # specific error line rather than any mention of telemetry.
            cases = {
                "orphan flag": (
                    ["--telemetry-duration", "30"],
                    "error: --telemetry-duration require --telemetry-out",
                ),
                "missing label": (
                    [
                        "--telemetry-out", str(Path(directory) / "run.json"),
                        "--telemetry-source-id", "abc",
                    ],
                    "error: --telemetry-out requires --telemetry-label",
                ),
                "relative path": (
                    [
                        "--telemetry-out", "run.json",
                        "--telemetry-label", "x",
                        "--telemetry-source-id", "abc",
                    ],
                    "error: telemetry: output path must be absolute",
                ),
            }
            for name, (argv, message) in cases.items():
                with self.subTest(case=name):
                    result = self._run_main(argv)
                    self.assertEqual(result.code, 2)
                    self.assertIn(message, result.stderr)
                    result.load_state.assert_not_called()
                    result.initialize.assert_not_called()
            self.assertEqual(os.listdir(directory), [])

    def test_retired_step6_variable_exits_2_before_state_or_matrix(self) -> None:
        result = self._run_main(
            [], environment={"RGBMATRIX_FFT_STEP6_METRICS_PATH": "/run/x.json"}
        )
        self.assertEqual(result.code, 2)
        self.assertIn(
            "error: RGBMATRIX_FFT_STEP6_METRICS_PATH no longer configure telemetry",
            result.stderr,
        )
        self.assertIn("use --telemetry-out, --telemetry-label", result.stderr)
        result.built.assert_not_called()
        result.load_state.assert_not_called()
        result.initialize.assert_not_called()

    def test_valid_options_build_telemetry_with_the_selected_output_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "run.json"
            result = self._run_main(
                [
                    "--telemetry-out", str(output_path),
                    "--telemetry-label", "pillow-1",
                    "--telemetry-source-id", "abc1234",
                    "--telemetry-duration", "30",
                ],
                environment={"RGBMATRIX_FFT_OUTPUT_PATH": "pillow-bulk"},
            )
            # initialize_matrix() is stubbed to fail, so main() stops at the matrix.
            self.assertEqual(result.code, 1)
            result.initialize.assert_called_once()
            self.assertEqual(len(result.built_values), 1)
            telemetry = result.built_values[0]
            self.assertIsInstance(telemetry, FrameTelemetry)
            self.assertEqual(telemetry.output_mode, "pillow-bulk")
            self.assertEqual(telemetry.duration_ns, 30_000_000_000)
            self.assertEqual(os.listdir(directory), [])

    def test_unavailable_bluetooth_exits_before_state_or_matrix(self) -> None:
        result = self._run_main(
            ["--audio-source", "bluetooth"],
            source_error=fft_main.AudioSourceResolutionError(
                "bluetooth audio input is unavailable or ambiguous"
            ),
        )

        self.assertEqual(result.code, 2)
        self.assertIn(
            "error: bluetooth audio input is unavailable or ambiguous",
            result.stderr,
        )
        result.resolve_source.assert_called_once()
        self.assertEqual(result.resolve_source.call_args.args[0], "bluetooth")
        result.load_state.assert_not_called()
        result.initialize.assert_not_called()

    def test_no_telemetry_flags_leave_telemetry_off(self) -> None:
        result = self._run_main([])
        self.assertEqual(result.code, 1)
        result.initialize.assert_called_once()
        self.assertEqual(result.built_values, [None])


if __name__ == "__main__":
    unittest.main()
