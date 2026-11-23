"""Hardware-free tests for the Step 4 v2 ``measure-split`` command in tools/pi.

SSH, sudo, systemd, vcgencmd, ``/proc``, the root sampler, and the FFT child are
faked. The Pi-side sequence (stop once, per-run checkout, runs, unconditional
restore and restart), the stop gates and flags, role discovery, metric aggregation,
the report-only summary with one-minute windows, and the evidence layout run for
real against a temporary directory.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace
import unittest

import numpy as np


TESTS_DIR = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = _load("pi_cycle_tests_shared", TESTS_DIR / "test_pi_cycle.py")
remote = base.remote
pi_cycle = base.pi_cycle

START = "c" * 40
BASELINE = "d" * 40
CANDIDATE = "e" * 40
PYTHON = base.PYTHON
MAIN = "/checkout/my_programs/fft_program/main.py"
RENDERER_CMD = [PYTHON, "-c", "from multiprocessing.spawn import spawn_main; "
                "spawn_main(tracker_fd=5, pipe_handle=7)", "--multiprocessing-fork"]
TRACKER_CMD = [PYTHON, "-c", "from multiprocessing.resource_tracker import main;main(4)"]
SETTLE = 1.0
DURATION = 10.0
WINDOW = (1000.0, 1000.0 + DURATION)


def npz_bytes(sequences, *, swap_fps=104.0, age_p95=20.0) -> bytes:
    """Raw arrays as telemetry writes them: one frame every 1/swap_fps seconds."""

    count = len(sequences)
    completed = 5_000_000_000 + np.arange(count, dtype=np.uint64) * np.uint64(
        round(1e9 / swap_fps))
    buffer = io.BytesIO()
    np.savez_compressed(buffer, analysis_sequences=np.asarray(sequences, dtype=np.uint64),
                        render_ns=np.zeros(count, dtype=np.uint64),
                        completed_ns=completed,
                        result_age_ns=np.full(count, int(age_p95 * 1e6), dtype=np.uint64))
    return buffer.getvalue()


def run_summary(*, swap_fps=104.0, published_fps=84.5, age_p95=20.0, density=900.0,
                drops=0, skipped=10, generations=860, frames=1040, **_):
    return base.passing_summary() | {
        "sample_count": frames,
        "start_wall_time": WINDOW[0],
        "end_wall_time": WINDOW[1],
        "rates": {"swap_fps": swap_fps, "unique_content_fps": 80.0,
                  "published_content_fps": published_fps,
                  "published_content_frames": 845},
        "latency": {"result_age": {"p50_ms": 9.0, "p95_ms": age_p95,
                                   "p99_ms": age_p95 + 1, "max_ms": age_p95 + 3}},
        "workload": {"visible_pixels_mean": density},
        "audio_counter_deltas": {
            "analysis_sequence": 845, "callback_status_count": 0,
            "worker_failure_count": 0, "transport_drop_count": drops,
            "sample_generation": generations, "skipped_generation_count": skipped,
        },
    }


def samples_lines(topology, *, cpu=None, pss_kb=None, extra=(), renderer=None,
                  tracker=True, pss=True, startup_only=()) -> str:
    """One header and one sample per second across (and slightly around) the window.

    Defaults: baseline 0.8 cores / 60 MiB PSS; candidate 0.9 cores / 65 MiB PSS, so
    the candidate is within every limit and shows no benefit.
    """

    cpu = cpu or ({"parent": 0.8} if topology == "baseline"
                  else {"parent": 0.4, "renderer": 0.5, "resource_tracker": 0.0})
    pss_kb = pss_kb or ({"parent": 60_000} if topology == "baseline"
                        else {"parent": 30_000, "renderer": 30_000,
                              "resource_tracker": 5_000})
    renderer = (topology == "candidate") if renderer is None else renderer
    procs = [(100, 1, ["sudo", "-n", "-E", PYTHON, MAIN], "sudo"),
             (101, 100, [PYTHON, MAIN, "--non-interactive"], "parent")]
    if renderer:
        procs.append((102, 101, RENDERER_CMD, "renderer"))
    if topology == "candidate" and tracker:
        procs.append((103, 101, TRACKER_CMD, "resource_tracker"))
    procs.extend(extra)
    lines = [json.dumps({"header": {"clk_tck": 100, "root_pid": 100, "interval": 1.0,
                                    "sampler_pid": 999, "euid": 0}})]
    for step in range(-2, int(DURATION) + 3):
        t = WINDOW[0] + step
        rows = []
        for pid, ppid, cmd, role in [*procs, *(startup_only if step == -2 else ())]:
            ticks = int(round(cpu.get(role, 0.0) * 100 * (step + 2)))
            row = {"pid": pid, "ppid": ppid, "start": 5000 + pid, "state": "S",
                   "cmd": cmd, "utime": ticks, "stime": 0, "rss_kb": 40_000,
                   "hwm_kb": 41_000}
            if pss:
                row["pss_kb"] = pss_kb.get(role, 1_000)
            rows.append(row)
        lines.append(json.dumps({"t": t, "m": t, "procs": rows}))
    return "\n".join(lines) + "\n"


class MeasureHost(base.FakeHost):
    def __init__(self, root: Path, **state) -> None:
        super().__init__(root, head=state.pop("head", START),
                         commits=state.pop("commits", {
                             START: START, BASELINE: BASELINE, CANDIDATE: CANDIDATE,
                         }), **state)
        # label -> overrides for run_summary/samples_lines and run behaviour
        self.measure = state.get("measure", {})
        self.survivors = state.get("survivors", {})  # label -> list[str]
        self.mp_leftovers = state.get("mp_leftovers", [])
        self.current_label = None
        self.git_status_dirty_after = state.get("dirty_after")
        self.sudo_python_rc = state.get("sudo_python_rc", 0)
        self.ignored = state.get("ignored", ".venv/\nmy_programs/fft_program/__pycache__/\n")
        self.tracked = state.get("tracked", "AGENTS.md\nmy_programs/fft_program/main.py\n")
        self.signal_on_checkout = state.get("signal_on_checkout")  # (sha, signal)

    def run(self, argv, *, cwd=None, timeout=60.0):
        argv = list(argv)
        if argv == ["sudo", "-n", "-l", "/usr/bin/python3"]:
            self.calls.append(("run", tuple(argv)))
            return remote.Result(self.sudo_python_rc)
        if argv[:3] == ["sudo", "-n", "kill"]:
            self.calls.append(("kill", argv[4], argv[-1]))
            return remote.Result(0)
        git = argv[3:] if argv[:3] == ["git", "-C", str(self.checkout)] else None
        if git and git[:1] == ["ls-files"]:
            self.calls.append(("run", tuple(argv)))
            return remote.Result(0, self.ignored)
        if git and git[:1] == ["ls-tree"]:
            self.calls.append(("run", tuple(argv)))
            return remote.Result(0, self.tracked)
        if (git and git[:3] == ["checkout", "--detach", "--quiet"]
                and self.signal_on_checkout and git[3] == self.signal_on_checkout[0]):
            result = super().run(argv, cwd=cwd, timeout=timeout)
            os.kill(os.getpid(), self.signal_on_checkout[1])
            self.calls.append(("checkout_done", git[3]))
            return result
        if (argv[:4] == ["git", "-C", str(self.checkout), "status"]
                and self.git_status_dirty_after is not None
                and self.current_label == self.git_status_dirty_after):
            self.calls.append(("run", tuple(argv)))
            return remote.Result(0, " M stray.txt\n")
        return super().run(argv, cwd=cwd, timeout=timeout)

    def run_measured(self, argv, *, cwd, env, timeout, log_path, samples_path,
                     sampler_log_path):
        label = argv[argv.index("--telemetry-label") + 1]
        self.current_label = label
        self.calls.append(("measured", label, self.head, dict(env), timeout))
        assert not self.service_running, "measured run while the service is up"
        log_path.write_text(f"log for {label}\n", encoding="utf-8")
        sampler_log_path.write_text("", encoding="utf-8")
        spec = self.measure.get(label, {})
        if "raise" in spec:
            raise spec["raise"]
        if "signal" in spec:
            os.kill(os.getpid(), spec["signal"])
        topology = "candidate" if label.startswith("candidate") else "baseline"
        out = argv[argv.index("--telemetry-out") + 1]
        sequences = spec.get("sequences", list(range(1040)))
        if not spec.get("no_telemetry"):  # e.g. the renderer missed readiness
            self.run_files[out] = json.dumps(run_summary(frames=len(sequences), **spec))
            self.run_files[out[:-len(".json")] + ".npz"] = npz_bytes(
                sequences, **{k: spec[k] for k in ("swap_fps", "age_p95") if k in spec})
        if "throttle_after" in spec:
            self.throttled = spec["throttle_after"]
        samples_path.write_text(samples_lines(
            topology, **{k: spec[k] for k in ("cpu", "pss_kb", "extra", "renderer", "pss",
                                              "startup_only")
                         if k in spec}), encoding="utf-8")
        return {"returncode": spec.get("returncode", 0), "timed_out": False,
                "kill_escalated": False, "sampler_returncode": spec.get("sampler_rc", 0)}

    def collect_run_dir(self, run_dir, evidence_dir):
        self.calls.append(("collect", str(run_dir), str(evidence_dir)))
        for path, data in self.run_files.items():
            if path.startswith(str(run_dir) + "/"):
                target = evidence_dir / Path(path).name
                if isinstance(data, bytes):
                    target.write_bytes(data)
                else:
                    target.write_text(data, encoding="utf-8")
        return remote.Result(0)

    def alive_processes(self, processes):
        return [item for item in self.survivors.get(self.current_label, [])
                if tuple(item) in {tuple(p) for p in processes}]

    def multiprocessing_processes(self, python):
        self.calls.append(("mp_processes", python))
        # Appears only once a run has launched (the pre-run check sees nothing).
        return list(self.mp_leftovers) if self.current_label else []

    def measured(self):
        return [call for call in self.calls if call[0] == "measured"]


def _measure(host, *args):
    return base._run(host, "measure-split", BASELINE, CANDIDATE, "--expect-start", START,
                     "--settle", f"{SETTLE:g}", "--duration", f"{DURATION:g}",
                     "--max-samples", "10000", "--tool-commit", "f" * 40,
                     "--tool-sha256", "0" * 64, "--audio-note", "mic, steady speaker",
                     *args)


def _both(prefix_or_labels, **spec):
    labels = [label for label, _, _ in remote.MEASURE_SEQUENCE
              if label.startswith(prefix_or_labels)]
    return {label: dict(spec) for label in labels}


class MeasureCase(base.TempHostCase):
    def host(self, **state):
        return MeasureHost(self.root, **state)


def _decision(result):
    return json.loads((Path(result["evidence_dir"]) / "decision.json").read_text())


def _runs(result):
    return json.loads((Path(result["evidence_dir"]) / "runs.json").read_text())


class MeasureSequenceTests(MeasureCase):
    def test_even_results_run_the_fixed_order_restore_and_report(self):
        host = self.host()
        code, result, output = _measure(host)
        self.assertEqual(code, remote.EXIT_OK, output)
        measured = host.measured()
        self.assertEqual([call[1] for call in measured],
                         [label for label, _, _ in remote.MEASURE_SEQUENCE])
        for (label, topology, mode), call in zip(remote.MEASURE_SEQUENCE, measured):
            expected_sha = BASELINE if topology == "baseline" else CANDIDATE
            self.assertEqual(call[2], expected_sha, label)
            self.assertEqual(call[3][remote.OUTPUT_PATH_ENV], mode)
            self.assertEqual(call[3].get(remote.EXPERIMENT_ENV),
                             "1" if topology == "candidate" else None, label)
            self.assertEqual(call[4], SETTLE + DURATION + remote.CHILD_MARGIN_SECONDS)
        names = host.names()
        self.assertEqual(names.count("stop"), 1)
        self.assertEqual(names.count("start"), 1)
        self.assertLess(names.index("stop"), names.index("measured"))
        self.assertGreater(names.index("start"), len(names) - 1 - names[::-1].index("measured"))
        self.assertEqual(host.head, START)
        self.assertTrue(host.service_running)
        self.assertEqual(result["decision"], "report-only")
        self.assertIn("no fixed rule", result["decision_reason"])
        self.assertNotIn("flagged", result["decision_reason"])
        self.assertEqual(result["restored"], START)

        evidence = Path(result["evidence_dir"])
        self.assertEqual(evidence.name, "fft-step4-matrix-process-v2-20261120T120000Z")
        for name in ("plan.json", "argv.json", "env.json", "status-before.json",
                     "status-after.json", "runs.json", "decision.json", "results.md",
                     "measure.log", "SHA256SUMS", "baseline-direct-1.proc.jsonl",
                     "candidate-pillow-2.npz", "candidate-pillow-2.sampler.log"):
            self.assertTrue((evidence / name).is_file(), name)
        plan = json.loads((evidence / "plan.json").read_text())
        self.assertEqual((plan["tool_commit"], plan["audio_note"]), ("f" * 40, "mic, steady speaker"))
        self.assertIn("report-only", plan["interpretations"]["decision"])
        manifest = (evidence / "SHA256SUMS").read_text()
        self.assertIn("results.md", manifest)
        self.assertIn("measure.log", manifest)
        runs = _runs(result)
        self.assertTrue(all(not run["flags"] and not run["stops"] for run in runs))
        candidate = next(run for run in runs if run["label"] == "candidate-direct-1")
        self.assertEqual(candidate["processes"]["role_pids"],
                         {"parent": [101], "renderer": [102], "resource_tracker": [103],
                          "sudo": [100]})
        self.assertAlmostEqual(candidate["processes"]["cpu_total"], 0.9)
        decision = _decision(result)
        self.assertEqual(decision["backends"]["direct"]["baseline"]["runs"],
                         ["baseline-direct-1", "baseline-direct-2"])
        self.assertEqual(decision["backends"]["pillow-bulk"]["candidate"]["runs"],
                         ["candidate-pillow-1", "candidate-pillow-2"])
        markdown = result["results_markdown"]
        self.assertIn("Result: **REPORT-ONLY**", markdown)
        self.assertIn("| 60 s windows |", markdown)
        self.assertNotIn("Operation: exit", markdown)
        self.assertEqual(decision["operation"], {"exit_code": 0, "restored": START,
                                                 "healthy": True, "errors": []})

    def test_arm_means_ratios_and_window_spreads(self):
        spec = _both("candidate", swap_fps=116.0, age_p95=15.0)
        spec["candidate-direct-2"] = {"swap_fps": 120.0, "age_p95": 15.0}
        result = _measure(self.host(measure=spec))[1]
        direct = _decision(result)["backends"]["direct"]
        self.assertAlmostEqual(direct["candidate"]["mean"]["swap_fps"], 118.0)
        self.assertAlmostEqual(direct["baseline"]["mean"]["swap_fps"], 104.0)
        self.assertAlmostEqual(direct["ratios"]["swap_fps"], 118.0 / 104.0)
        self.assertAlmostEqual(direct["ratios"]["result_age_p95_ms"], 15.0 / 20.0)
        self.assertAlmostEqual(direct["ratios"]["cpu_total"], 0.9 / 0.8)
        self.assertAlmostEqual(direct["ratios"]["memory_p95_kb"], 65_000 / 60_000)
        # A 10 s fake run is one window, so each arm pools two windows.
        windows = direct["candidate"]["windows"]
        self.assertEqual(windows["swap_fps"]["n"], 2)
        self.assertAlmostEqual(windows["swap_fps"]["min"], 116.0, delta=0.2)
        self.assertAlmostEqual(windows["swap_fps"]["max"], 120.0, delta=0.2)
        self.assertAlmostEqual(windows["age_p95_ms"]["median"], 15.0)
        self.assertAlmostEqual(windows["cpu"]["median"], 0.9)
        self.assertAlmostEqual(direct["baseline"]["windows"]["cpu"]["median"], 0.8)

    def test_rss_is_used_when_pss_is_unavailable(self):
        host = self.host(measure={"baseline-direct-1": {"pss": False}})
        decision = _decision(_measure(host)[1])
        self.assertEqual(decision["backends"]["direct"]["memory_metric"], "rss_total_kb")
        self.assertEqual(decision["backends"]["pillow-bulk"]["memory_metric"], "pss_total_kb")

    def test_workload_density_mismatch_is_noted_not_invalid(self):
        host = self.host(measure=_both("candidate-pillow", density=1200.0))
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_OK)
        self.assertEqual(result["decision"], "report-only")
        notes = _decision(result)["backends"]["pillow-bulk"]["notes"]
        self.assertTrue(any("density differs by 33.3%" in note for note in notes), notes)
        self.assertEqual(_decision(result)["backends"]["direct"]["notes"], [])
        self.assertTrue(host.service_running)
        self.assertEqual(host.head, START)

    def test_transport_drops_are_a_fraction_not_a_flag(self):
        host = self.host(measure={"baseline-direct-1": {"drops": 3}})
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_OK)
        self.assertEqual(len(host.measured()), len(remote.MEASURE_SEQUENCE))
        run = _runs(result)[0]
        self.assertEqual(run["transport_drops"], 3)
        self.assertAlmostEqual(run["drop_fraction"], 3 / 845)
        self.assertEqual(run["flags"], [])
        self.assertNotIn("transport_drops_zero", run["gates"])

    def test_superseded_fraction_comes_from_raw_sequence_gaps(self):
        gaps = [0, 1, 4, 4, 5, 9]  # gaps of 2 and 3 superseded results -> 5
        host = self.host(measure={"candidate-direct-1": {"sequences": gaps}})
        result = _measure(host)[1]
        run = next(item for item in _runs(result) if item["label"] == "candidate-direct-1")
        self.assertEqual(run["superseded"], 5)
        self.assertAlmostEqual(run["superseded_fraction"], 5 / 845)


class MeasureFailureTests(MeasureCase):
    def assert_restored(self, host, *, started=True):
        self.assertEqual(host.head, START)
        self.assertEqual(host.service_running, started)

    def _stops_after(self, label, spec, gate):
        host = self.host(measure={label: spec})
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assertEqual(host.measured()[-1][1], label)
        self.assertEqual(result["decision"], "incomplete")
        runs = _runs(result)
        failed = next(item for item in runs if item["label"] == label)
        self.assertIn(gate, failed["flags"])
        self.assertIn(gate, failed["stops"])
        later = [item for item in runs if item.get("skipped")]
        self.assertEqual(len(later) + len(host.measured()), len(remote.MEASURE_SEQUENCE))
        self.assert_restored(host)
        return host, result

    def _flags_and_continues(self, label, spec, flag):
        """v2 (D-011a): a non-stop gate is a flag; every run still happens."""

        host = self.host(measure={label: spec})
        code, result, output = _measure(host)
        self.assertEqual(code, remote.EXIT_OK, output)
        self.assertEqual(len(host.measured()), len(remote.MEASURE_SEQUENCE))
        self.assertEqual(result["decision"], "report-only")
        self.assertIn(f"flagged: {label} (", result["decision_reason"])
        run = next(item for item in _runs(result) if item["label"] == label)
        self.assertIn(flag, run["flags"])
        self.assertEqual(run["stops"], [])
        self.assertIn(flag, _decision(result)["flagged"][label])
        row = next(line for line in result["results_markdown"].splitlines()
                   if line.startswith(f"| {label} |"))
        self.assertIn(flag, row.rsplit("|", 2)[-2])
        self.assert_restored(host)
        return host, result

    def test_candidate_without_renderer_is_flagged_and_the_sequence_continues(self):
        self._flags_and_continues("candidate-direct-1", {"renderer": False}, "roles")

    def test_baseline_with_a_renderer_is_flagged(self):
        self._flags_and_continues("baseline-direct-1", {"renderer": True}, "roles")

    def test_unknown_descendant_in_the_window_is_flagged(self):
        extra = [(150, 101, ["/bin/sh", "-c", "true"], "other")]
        self._flags_and_continues("candidate-pillow-1", {"extra": extra}, "roles")

    def test_skipped_fraction_and_age_limit_are_flags(self):
        self._flags_and_continues("baseline-direct-1", {"skipped": 50, "generations": 860},
                                  "skipped_fraction")
        root = self.root / "age"
        root.mkdir()
        self.root = root
        self._flags_and_continues("baseline-direct-1", {"age_p95": 23.5}, "result_age_p95")

    def test_flagged_runs_still_count_toward_the_arm_means(self):
        result = self._flags_and_continues(
            "candidate-direct-1", {"returncode": 1, "swap_fps": 90.0}, "exit_zero")[1]
        direct = _decision(result)["backends"]["direct"]
        self.assertIn("candidate-direct-1", direct["candidate"]["runs"])
        self.assertAlmostEqual(direct["candidate"]["mean"]["swap_fps"], (90.0 + 104.0) / 2)

    def test_run_without_telemetry_is_flagged_not_a_harness_fault(self):
        host, result = self._flags_and_continues(
            "candidate-direct-1", {"no_telemetry": True, "returncode": 1}, "exit_zero")
        run = next(item for item in _runs(result) if item["label"] == "candidate-direct-1")
        self.assertIn("schema", run["flags"])
        self.assertNotIn("sampler", run["flags"])
        self.assertEqual(run["windows"], {"frames": [], "cpu": []})
        direct = _decision(result)["backends"]["direct"]
        self.assertAlmostEqual(direct["candidate"]["mean"]["swap_fps"], 104.0)

    def test_sticky_throttle_flags_only_the_run_that_set_it(self):
        host, result = self._flags_and_continues(
            "candidate-pillow-1", {"throttle_after": "throttled=0x80000"}, "no_throttling")
        flagged = [item["label"] for item in _runs(result) if "no_throttling" in item["flags"]]
        self.assertEqual(flagged, ["candidate-pillow-1"])

    def test_operational_error_is_recorded_beside_a_complete_report(self):
        host = self.host()
        host.remove_run_dir = lambda run_dir: remote.Result(1, "", "busy")
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assertEqual(result["decision"], "report-only")
        operation = _decision(result)["operation"]
        self.assertEqual(operation["exit_code"], remote.EXIT_GATE_FAILED)
        self.assertTrue(any("could not remove" in e for e in operation["errors"]))
        self.assertIn("Operation: exit 1", result["results_markdown"])

    def test_survivor_killed_after_its_grace_stops_the_sequence_and_restarts(self):
        host = self.host()
        killed = []
        original_run = host.run

        def run(argv, **kwargs):
            if list(argv[:3]) == ["sudo", "-n", "kill"] and argv[4] == "KILL":
                killed.append(argv[-1])
            return original_run(argv, **kwargs)

        def alive(processes):
            if host.current_label == "candidate-direct-1" and not killed \
                    and (102, 5102) in processes:
                return [(102, 5102)]
            return []

        host.run = run
        host.alive_processes = alive
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assertEqual(killed, ["102"])
        self.assertEqual(host.measured()[-1][1], "candidate-direct-1")
        self.assertIn("no_residual_process", _runs(result)[1]["stops"])
        self.assertEqual(result["decision"], "incomplete")
        self.assert_restored(host)  # nothing remains, so the service restarts

    def test_sampler_failure_stops_as_incomplete_and_leaves_the_run_out(self):
        _, result = self._stops_after("baseline-direct-1", {"sampler_rc": 1}, "sampler")
        self.assertIn("harness gates failed: baseline-direct-1", result["decision_reason"])
        self.assertEqual(_decision(result)["backends"]["direct"]["baseline"]["runs"], [])

    def test_startup_only_transient_is_recorded_not_failed(self):
        ldconfig = [(140, 101, ["/sbin/ldconfig", "-p"], "transient")]
        host = self.host(measure=_both("candidate", startup_only=ldconfig))
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_OK)
        runs = json.loads((Path(result["evidence_dir"]) / "runs.json").read_text())
        run = next(item for item in runs if item["label"] == "candidate-direct-1")
        self.assertEqual(run["processes"]["transients"], {"140": ["/sbin/ldconfig", "-p"]})
        self.assertEqual(run["processes"]["role_pids"]["transient"], [140])

    def test_nonzero_exit_is_flagged_and_the_sequence_continues(self):
        self._flags_and_continues("candidate-direct-1", {"returncode": 1}, "exit_zero")

    def test_residual_process_is_signalled_keeps_service_stopped_restores_checkout(self):
        host = self.host(survivors={"candidate-direct-1": [(102, 5102)]})
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_OPERATION_FAILED)
        self.assertEqual(host.measured()[-1][1], "candidate-direct-1")
        kills = [call for call in host.calls if call[0] == "kill"]
        self.assertEqual([call[1] for call in kills[:3]], ["INT", "TERM", "KILL"])
        self.assertEqual({call[2] for call in kills}, {"102"})
        self.assertNotIn("start", host.names())
        self.assert_restored(host, started=False)
        self.assertTrue(any("FFT process still running" in e for e in result["errors"]))
        runs = _runs(result)
        self.assertIn("no_residual_process", runs[1]["stops"])
        self.assertEqual(result["decision"], "incomplete")
        self.assertEqual(_decision(result)["operation"]["exit_code"],
                         remote.EXIT_OPERATION_FAILED)
        self.assertIn("Operation: exit 3", result["results_markdown"])

    def test_resource_tracker_exiting_within_grace_is_not_residual(self):
        host = self.host()
        polls = []

        def alive(processes):
            polls.append(host.clock())
            return [(103, 5103)] if len(polls) < 3 and (103, 5103) in processes else []

        host.alive_processes = alive
        code, _, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_OK)
        self.assertNotIn("kill", host.names())

    def test_multiprocessing_leftover_blocks_the_restart(self):
        host = self.host(mp_leftovers=[f"777 {PYTHON} -c from multiprocessing.spawn ..."])
        code, _, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_OPERATION_FAILED)
        self.assertEqual(len(host.measured()), 1)
        self.assert_restored(host, started=False)

    def test_fft_process_present_before_the_first_run_is_never_joined(self):
        host = self.host(leftovers=["555 python3 my_programs/fft_program/main.py"])
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_OPERATION_FAILED)
        self.assertEqual(host.measured(), [])
        self.assert_restored(host, started=False)
        self.assertTrue(any("present before baseline-direct-1" in e for e in result["errors"]))

    def test_exception_mid_sequence_restores_and_restarts(self):
        host = self.host(measure={"candidate-pillow-1": {"raise": RuntimeError("boom")}})
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assert_restored(host)
        self.assertTrue(any("boom" in error for error in result["errors"]))

    def test_sigterm_during_a_run_restores_and_restarts(self):
        host = self.host(measure={"baseline-pillow-1": {"signal": signal.SIGTERM}})
        code, _, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assertEqual(len(host.measured()), 4)
        self.assert_restored(host)

    def test_dirty_tree_mid_sequence_skips_restore_and_never_starts_the_service(self):
        host = self.host(dirty_after="candidate-direct-1")
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_OPERATION_FAILED)
        self.assertEqual(len(host.measured()), 2)
        self.assertEqual(host.head, CANDIDATE)
        self.assertFalse(host.service_running)
        self.assertNotIn("start", host.names())
        self.assertTrue(any(e.startswith("restore skipped") for e in result["errors"]))
        self.assertTrue(any("not restored" in e for e in result["errors"]))

    def test_failed_checkout_refuses_before_launch_and_keeps_service_stopped(self):
        host = self.host(checkout_rc=1)
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_OPERATION_FAILED)
        self.assertEqual(host.measured(), [])
        self.assertFalse(host.service_running)
        self.assertEqual(result["decision"], "incomplete")

    def test_signal_during_checkout_is_deferred_until_it_completes(self):
        host = self.host(signal_on_checkout=(CANDIDATE, signal.SIGTERM))
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assertIn(("checkout_done", CANDIDATE), host.calls)
        self.assertEqual([call[1] for call in host.measured()], ["baseline-direct-1"])
        self.assertTrue(any("CheckAborted" in e for e in result["errors"]))
        self.assert_restored(host)

    def test_second_signal_during_cleanup_cannot_skip_the_restart(self):
        host = self.host(measure={"candidate-direct-1": {"signal": signal.SIGTERM}},
                         signal_on_start=signal.SIGTERM)
        code, _, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assert_restored(host)

    def test_mac_disconnect_stops_after_the_current_run_and_restores(self):
        class Breaks(io.StringIO):
            def write(self, text):
                if "done      baseline-direct-1" in text:
                    raise OSError("broken pipe")
                return super().write(text)

        host = self.host()
        code, _, _ = base._run(
            host, "measure-split", BASELINE, CANDIDATE, "--expect-start", START,
            "--settle", "1", "--duration", "10", "--max-samples", "10000",
            "--tool-commit", "f", "--tool-sha256", "0", "--audio-note", "a",
            stream=Breaks())
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assertEqual(len(host.measured()), 1)
        self.assert_restored(host)
        evidence = next((self.root / "evidence").iterdir())
        decision = json.loads((evidence / "decision.json").read_text())
        self.assertEqual(decision["decision"], "incomplete")

    def test_exception_while_evaluating_still_restores(self):
        original = remote.summarize_processes

        def broken(*args):
            raise ValueError("bad samples")

        remote.summarize_processes = broken
        try:
            host = self.host()
            code, result, _ = _measure(host)
        finally:
            remote.summarize_processes = original
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assert_restored(host)
        self.assertEqual(result["decision"], "incomplete")

    def test_controller_argv_change_between_commits_stops(self):
        host = self.host()
        original = host.run
        launches = []

        def run(argv, **kwargs):
            if list(argv[:2]) == ["/usr/bin/python3", "-c"]:
                launches.append(1)
                argv_out = base.BASE_ARGV + (["--extra"] if len(launches) > 1 else [])
                return remote.Result(0, json.dumps({"argv": argv_out, "cwd": "/c",
                                                    "python": PYTHON}))
            return original(argv, **kwargs)

        host.run = run
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assertEqual(len(host.measured()), 1)
        self.assertTrue(any("controller argv differs" in e for e in result["errors"]))
        self.assert_restored(host)


class MeasureRefusalTests(MeasureCase):
    def assert_refused(self, host):
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_REFUSED)
        self.assertIn("refused", result)
        self.assertNotIn("stop", host.names())
        self.assertEqual(host.measured(), [])

    def test_wrong_start_commit(self):
        self.assert_refused(self.host(head=BASELINE))

    def test_dirty_or_branch_checkout(self):
        self.assert_refused(self.host(porcelain=" M x\n"))
        root = self.root / "branch"
        root.mkdir()
        self.assert_refused(MeasureHost(root, detached=False))

    def test_service_down_or_unhealthy(self):
        self.assert_refused(self.host(service_running=False))
        root = self.root / "unhealthy"
        root.mkdir()
        self.assert_refused(MeasureHost(root, healthy=False))

    def test_current_throttling(self):
        self.assert_refused(self.host(throttled="throttled=0x4"))

    def test_sampler_sudo_not_allowed(self):
        self.assert_refused(self.host(sudo_python_rc=1))

    def test_ignored_pi_file_that_a_checkout_would_replace(self):
        host = self.host(ignored=".venv/\nAGENTS.md\n")
        self.assert_refused(host)
        root = self.root / "dir"
        root.mkdir()
        self.assert_refused(MeasureHost(root, ignored="docs/\n",
                                        tracked="docs/x.md\nmain.py\n"))

    def test_abbreviated_or_missing_commits(self):
        commits = {START: START, BASELINE[:7]: BASELINE, BASELINE: BASELINE,
                   CANDIDATE: CANDIDATE}
        host = self.host(commits=commits)
        code, result, _ = base._run(
            host, "measure-split", BASELINE[:7], CANDIDATE, "--expect-start", START,
            "--settle", "1", "--duration", "10", "--max-samples", "10",
            "--tool-commit", "f", "--tool-sha256", "0", "--audio-note", "a")
        self.assertEqual(code, remote.EXIT_REFUSED)
        self.assertIn("full commit", result["refused"])
        root = self.root / "missing"
        root.mkdir()
        self.assert_refused(MeasureHost(root, commits={START: START, BASELINE: BASELINE}))


class MeasureHelperTests(unittest.TestCase):
    def test_classify_role(self):
        self.assertEqual(remote.classify_role(["sudo", "-n", "-E", PYTHON, MAIN]), "sudo")
        self.assertEqual(remote.classify_role(["/usr/bin/sudo", PYTHON, MAIN]), "sudo")
        self.assertEqual(remote.classify_role([PYTHON, MAIN, "--x"]), "parent")
        self.assertEqual(remote.classify_role(RENDERER_CMD), "renderer")
        self.assertEqual(remote.classify_role(TRACKER_CMD), "resource_tracker")
        self.assertEqual(remote.classify_role(["/bin/sh"]), "unknown")
        self.assertEqual(remote.classify_role([]), "unknown")

    def test_last_cmdline_wins_for_a_pid_caught_before_exec(self):
        lines = samples_lines("candidate").splitlines()
        first = json.loads(lines[1])
        for row in first["procs"]:
            if row["pid"] == 102:
                row["cmd"] = [PYTHON, MAIN, "--non-interactive"]  # fork, before exec
        lines[1] = json.dumps(first)
        header, samples = (json.loads(lines[0])["header"],
                           [json.loads(line) for line in lines[1:]])
        summary = remote.summarize_processes(header, samples, WINDOW)
        self.assertEqual(summary["role_pids"]["parent"], [101])
        self.assertEqual(summary["role_pids"]["renderer"], [102])

    def test_cpu_and_memory_use_only_the_window_and_app_roles(self):
        header = {"clk_tck": 100, "interval": 1.0, "euid": 0}
        samples = [json.loads(line) for line in samples_lines(
            "candidate", cpu={"sudo": 5.0, "parent": 0.25, "renderer": 0.5})
            .splitlines()[1:]]
        summary = remote.summarize_processes(header, samples, WINDOW)
        self.assertEqual(summary["window_samples"], int(DURATION) + 1)
        self.assertAlmostEqual(summary["cpu_total"], 0.75)
        self.assertNotIn("sudo", summary["cpu_by_role"])
        self.assertEqual(summary["pss_total_kb"]["p95"], 65_000)
        self.assertEqual(summary["rss_total_kb"]["max"], 120_000)
        self.assertEqual(summary["hwm_peak_total_kb"], 123_000)
        self.assertTrue(summary["pss_available"])

    def test_parse_proc_stat_handles_spaces_and_parens_in_comm(self):
        fields = ["S", "1", "100", "100", "0", "-1", "4194560", "10", "0", "0", "0",
                  "250", "40", "0", "0", "20", "0", "4", "0", "123456"]
        info = remote.parse_proc_stat("4242 (python3 (x) y) " + " ".join(fields) + " 0 0\n")
        self.assertEqual(info, {"state": "S", "ppid": 1, "utime": 250, "stime": 40,
                                "start": 123456})

    def test_read_npz_matches_numpy(self):
        import tempfile

        values = [0, 1, 2, 2**40, 2**63 + 5]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.npz"
            path.write_bytes(npz_bytes(values))
            self.assertEqual(remote.read_npz_uint64(path, "analysis_sequences"), values)
            with self.assertRaises(KeyError):
                remote.read_npz_uint64(path, "missing")
            np.savez_compressed(path, analysis_sequences=np.zeros(3, dtype=np.int32))
            with self.assertRaises(ValueError):
                remote.read_npz_uint64(path, "analysis_sequences")

    def test_superseded_count(self):
        self.assertEqual(remote.superseded_count([]), 0)
        self.assertEqual(remote.superseded_count([3, 3, 4, 7, 6, 10]), 2 + 3)

    def test_window_count(self):
        self.assertEqual(remote.window_count(900.0), 15)
        self.assertEqual(remote.window_count(899.99), 15)
        self.assertEqual(remote.window_count(10.0), 1)
        self.assertEqual(remote.window_count(0.0), 1)

    def test_frame_windows_split_the_span_and_measure_each_window(self):
        # 60 s at 100 FPS, 60 s at 50 FPS, 60 s at 100 FPS; results published at 80/s.
        times = ([i * 0.01 for i in range(6000)] + [60 + i * 0.02 for i in range(3000)]
                 + [120 + i * 0.01 for i in range(6000)])
        completed = [int(round(t * 1e9)) for t in times]
        ages = [int((20 if 60 <= t < 120 else 10) * 1e6) for t in times]
        sequences = [int(t * 80) for t in times]
        windows = remote.frame_windows(completed, ages, sequences)
        self.assertEqual(len(windows), 3)
        for window, fps, age in zip(windows, (100, 50, 100), (10, 20, 10)):
            self.assertAlmostEqual(window["swap_fps"], fps, delta=0.1)
            self.assertAlmostEqual(window["published_fps"], 80, delta=0.1)
            self.assertAlmostEqual(window["age_p95_ms"], age)
        self.assertEqual(remote.frame_windows([1], [1], [1]), [])
        self.assertEqual(remote.frame_windows([1, 2], [1], [1, 2]), [])

    def test_cpu_windows_follow_app_roles_only(self):
        # The parent uses 0.5 cores for 60 s and then 1.0; sudo's ticks are ignored.
        samples = []
        for t in range(121):
            parent = 50 * t if t <= 60 else 3000 + 100 * (t - 60)
            samples.append({"t": float(t), "procs": [
                {"pid": 101, "utime": parent, "stime": 0},
                {"pid": 100, "utime": 1000 * t, "stime": 0}]})
        roles = {"parent": [101], "sudo": [100]}
        cores = remote.cpu_windows({"clk_tck": 100}, samples, roles, (0.0, 120.0), 2)
        self.assertEqual(len(cores), 2)
        self.assertAlmostEqual(cores[0], 0.5)
        self.assertAlmostEqual(cores[1], 1.0)
        self.assertEqual(remote.cpu_windows({"clk_tck": 100}, samples, roles,
                                            (None, None), 2), [])

    def test_spread(self):
        self.assertEqual(remote.spread([3.0, None, 1.0, 2.0]),
                         {"n": 3, "min": 1.0, "median": 2.0, "max": 3.0})
        self.assertEqual(remote.spread([]), {"n": 0, "min": None, "median": None, "max": None})

    def test_measured_environment_controls_topology_and_backend(self):
        inherited = {"PATH": "/bin", remote.EXPERIMENT_ENV: "1",
                     remote.OUTPUT_PATH_ENV: "pillow-bulk", "RGBMATRIX_FFT_STEP6_X": "1"}
        baseline = remote.measured_environment(
            inherited, remote.measure_env("baseline", "direct", Path("/run/x")))
        self.assertNotIn(remote.EXPERIMENT_ENV, baseline)
        self.assertNotIn("RGBMATRIX_FFT_STEP6_X", baseline)
        self.assertEqual(baseline[remote.OUTPUT_PATH_ENV], "direct")
        candidate = remote.measured_environment(
            {}, remote.measure_env("candidate", "pillow-bulk", Path("/run/x")))
        self.assertEqual(candidate[remote.EXPERIMENT_ENV], "1")
        self.assertEqual(candidate["RGBMATRIX_STATE_DIR"], "/run/x/state")

    def test_sequence_is_the_approved_symmetric_order(self):
        self.assertEqual([label for label, _, _ in remote.MEASURE_SEQUENCE], [
            "baseline-direct-1", "candidate-direct-1", "candidate-pillow-1",
            "baseline-pillow-1", "baseline-pillow-2", "candidate-pillow-2",
            "candidate-direct-2", "baseline-direct-2"])
        for label, topology, mode in remote.MEASURE_SEQUENCE:
            self.assertTrue(label.startswith(topology))
            self.assertIn(mode, remote.OUTPUT_MODES)

    def test_sampler_runs_once_under_sudo_with_the_system_python(self):
        argv = remote.sampler_argv(4321, 400.0)
        self.assertEqual(argv[:4], ["sudo", "-n", "/usr/bin/python3", "-c"])
        self.assertEqual(argv[5:], ["4321", "1", "400"])
        compile(remote.SAMPLER_SNIPPET, "<sampler>", "exec")


class RealRunMeasuredTests(base.TempHostCase):
    """PiHost.run_measured with real processes; the sudo sampler is stubbed."""

    def setUp(self):
        super().setUp()
        self._argv = remote.sampler_argv
        remote.sampler_argv = lambda pid, deadline: [
            sys.executable, "-c",
            "import json, sys; print(json.dumps({'header': {'root_pid': int(sys.argv[1])}}))",
            str(pid)]

    def tearDown(self):
        remote.sampler_argv = self._argv
        super().tearDown()

    def run_child(self, code, timeout):
        host = remote.PiHost(self.root)
        host.STOP_GRACE_SECONDS = (2.0, 2.0, 2.0)
        result = host.run_measured(
            [sys.executable, "-c", code], cwd=self.root, env={"X_ENV": "1"},
            timeout=timeout, log_path=self.root / "run.log",
            samples_path=self.root / "proc.jsonl", sampler_log_path=self.root / "s.log")
        return result

    def test_child_and_sampler_complete(self):
        result = self.run_child("import os; print(os.environ['X_ENV'])", 20)
        self.assertEqual(result, {"returncode": 0, "timed_out": False,
                                  "kill_escalated": False, "sampler_returncode": 0})
        self.assertEqual((self.root / "run.log").read_text().strip(), "1")
        header = json.loads((self.root / "proc.jsonl").read_text())
        self.assertIn("root_pid", header["header"])

    def test_overrun_is_stopped_with_sigint(self):
        result = self.run_child("import time; time.sleep(30)", 0.5)
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["kill_escalated"])
        self.assertNotEqual(result["returncode"], 0)
        self.assertEqual(result["sampler_returncode"], 0)


@unittest.skipUnless(Path("/proc/self/stat").exists(), "needs Linux /proc")
class RealProcTests(unittest.TestCase):
    """Exercised on Linux only; the Mac has no /proc (first real use is on the Pi)."""

    def test_sampler_snippet_follows_a_real_tree(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
        try:
            sampled = subprocess.run(
                [sys.executable, "-c", remote.SAMPLER_SNIPPET, str(child.pid), "0.5", "10"],
                capture_output=True, text=True, timeout=20,
            )
        finally:
            child.wait()
        lines = [json.loads(line) for line in sampled.stdout.splitlines()]
        self.assertIn("header", lines[0])
        pids = {row["pid"] for item in lines[1:] for row in item.get("procs", [])}
        self.assertIn(child.pid, pids)

    def test_surviving_matches_pid_and_start_time(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            start = remote.parse_proc_stat(Path(f"/proc/{child.pid}/stat").read_text())["start"]
            host = remote.PiHost(Path("."))
            self.assertEqual(len(host.surviving([(child.pid, start)])), 1)
            self.assertEqual(host.surviving([(child.pid, start + 1)]), [])
        finally:
            child.kill()
            child.wait()
        self.assertEqual(host.surviving([(child.pid, start)]), [])


class MeasureMacTests(unittest.TestCase):
    def git(self, *, ancestor=True, tools_dirty=""):
        commits = {START: START, BASELINE: BASELINE, CANDIDATE: CANDIDATE,
                   "HEAD": "f" * 40, CANDIDATE[:7]: CANDIDATE}
        resolve = base._git_stub(commits=commits)

        def git(*args):
            if args[:2] == ("merge-base", "--is-ancestor"):
                return SimpleNamespace(returncode=0 if ancestor else 1, stdout="")
            if args[:2] == ("status", "--porcelain"):
                return SimpleNamespace(returncode=0, stdout=tools_dirty)
            return resolve(*args)

        return git

    def main(self, popen, *, git=None, pushes=None):
        out = io.StringIO()
        pushes = [] if pushes is None else pushes
        code = pi_cycle.main(
            ["measure-split", BASELINE, CANDIDATE[:7], "--expect-start", START,
             "--audio-note", "mic, steady speaker"],
            environ={pi_cycle.HOST_ENV: "pihost"}, git=git or self.git(), popen=popen,
            run=lambda argv, **k: pushes.append(argv) or SimpleNamespace(returncode=0),
            out=out)
        return code, out.getvalue()

    class SequencedPopen(base.FakePopen):
        def __init__(self, results):
            super().__init__()
            self.results = list(results)

        def __call__(self, argv, **kwargs):
            result, code = self.results.pop(0)
            lines = ["working"] + ([remote.RESULT_MARKER + json.dumps(result)] if result else [])
            process = base.FakeProcess(lines, code)
            self.calls.append((argv, process))
            return process

    def status(self, **overrides):
        status = {"head": START, "detached": True, "porcelain": []} | overrides
        return ({"command": "status", "status": status}, 0)

    def test_preflight_push_then_fixed_sequence(self):
        pushes = []
        popen = self.SequencedPopen([
            self.status(),
            ({"results_markdown": "| t |\n", "evidence_dir": "/e"}, 0),
        ])
        code, output = self.main(popen, pushes=pushes)
        self.assertEqual(code, 0)
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0][-1], f"+{CANDIDATE}:refs/heads/deploy")
        self.assertTrue(popen.calls[0][0][-1].endswith(" status"))
        # Only the long measure-split call carries the SSH keepalive.
        self.assertNotIn("ServerAliveInterval=30", popen.calls[0][0])
        self.assertIn("ServerAliveInterval=30", popen.calls[1][0])
        self.assertIn("ServerAliveCountMax=10", popen.calls[1][0])
        command = popen.calls[1][0][-1]
        sha256 = __import__("hashlib").sha256((base.TOOL_DIR / "remote.py").read_bytes()).hexdigest()
        self.assertIn(f"measure-split {BASELINE} {CANDIDATE} --expect-start {START} "
                      "--settle 60 --duration 900 --max-samples 250000 "
                      f"--tool-commit {'f' * 40} --tool-sha256 {sha256} "
                      "--audio-note 'mic, steady speaker'", command)
        self.assertIn("Evidence (on the Pi): /e", output)

    def test_preflight_mismatch_refuses_before_any_push(self):
        for status in (self.status(head=BASELINE), self.status(porcelain=[" M x"]),
                       self.status(detached=False)):
            pushes = []
            popen = self.SequencedPopen([status])
            code, _ = self.main(popen, pushes=pushes)
            self.assertEqual(code, pi_cycle.EXIT_REFUSED)
            self.assertEqual(pushes, [])
            self.assertEqual(len(popen.calls), 1)

    def test_local_refusals_before_ssh(self):
        for git in (self.git(ancestor=False), self.git(tools_dirty=" M tools/pi/remote.py\n")):
            popen = self.SequencedPopen([])
            code, _ = self.main(popen, git=git)
            self.assertEqual(code, pi_cycle.EXIT_REFUSED)
            self.assertEqual(popen.calls, [])


if __name__ == "__main__":
    unittest.main()
