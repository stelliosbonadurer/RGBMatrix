"""Hardware-free tests for the Step 4 ``measure-split`` command in tools/pi.

SSH, sudo, systemd, vcgencmd, ``/proc``, the root sampler, and the FFT child are
faked. The Pi-side sequence (stop once, per-run checkout, runs, unconditional
restore and restart), the gates, role discovery, metric aggregation, the fixed
decision rule, and the evidence layout run for real against a temporary directory.
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


def npz_bytes(sequences) -> bytes:
    buffer = io.BytesIO()
    np.savez_compressed(buffer, analysis_sequences=np.asarray(sequences, dtype=np.uint64),
                        render_ns=np.zeros(len(sequences), dtype=np.uint64))
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
        self.run_files[out] = json.dumps(run_summary(frames=len(sequences), **spec))
        self.run_files[out[:-len(".json")] + ".npz"] = npz_bytes(sequences)
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


class MeasureSequenceTests(MeasureCase):
    def test_even_results_run_the_fixed_order_restore_and_reject(self):
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
        self.assertEqual(result["decision"], "reject")
        self.assertIn("no material benefit", result["decision_reason"])
        self.assertEqual(result["restored"], START)

        evidence = Path(result["evidence_dir"])
        self.assertEqual(evidence.name, "fft-step4-matrix-process-20261120T120000Z")
        for name in ("plan.json", "argv.json", "env.json", "status-before.json",
                     "status-after.json", "runs.json", "decision.json", "results.md",
                     "measure.log", "SHA256SUMS", "baseline-direct-1.proc.jsonl",
                     "candidate-pillow-2.npz", "candidate-pillow-2.sampler.log"):
            self.assertTrue((evidence / name).is_file(), name)
        plan = json.loads((evidence / "plan.json").read_text())
        self.assertEqual((plan["tool_commit"], plan["audio_note"]), ("f" * 40, "mic, steady speaker"))
        manifest = (evidence / "SHA256SUMS").read_text()
        self.assertIn("results.md", manifest)
        self.assertIn("measure.log", manifest)
        runs = json.loads((evidence / "runs.json").read_text())
        self.assertTrue(all(not run["failed_gates"] for run in runs))
        candidate = next(run for run in runs if run["label"] == "candidate-direct-1")
        self.assertEqual(candidate["processes"]["role_pids"],
                         {"parent": [101], "renderer": [102], "resource_tracker": [103],
                          "sudo": [100]})
        self.assertAlmostEqual(candidate["processes"]["cpu_total"], 0.9)
        self.assertIn("Decision (fixed rule): **REJECT**", result["results_markdown"])

    def test_consistent_swap_benefit_in_both_backends_accepts(self):
        host = self.host(measure=_both("candidate", swap_fps=116.0))
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_OK)
        self.assertEqual(result["decision"], "accept")
        self.assertIn("swap_fps_plus_10pct", result["decision_reason"])

    def test_age_benefit_accepts_but_one_reversed_run_rejects(self):
        host = self.host(measure=_both("candidate", age_p95=16.0))
        self.assertEqual(_measure(host)[1]["decision"], "accept")
        # Pillow median (12 + 21.5) / 2 = 16.75 meets -15%, but 21.5 > 105% of 20.
        spec = _both("candidate", age_p95=12.0)
        spec["candidate-pillow-2"] = {"age_p95": 21.5}
        root = self.root / "reversed"
        root.mkdir()
        result = _measure(MeasureHost(root, measure=spec))[1]
        self.assertEqual(result["decision"], "reject")
        self.assertIn("no material benefit", result["decision_reason"])

    def test_benefit_in_only_one_backend_rejects(self):
        host = self.host(measure=_both("candidate-direct", swap_fps=116.0))
        result = _measure(host)[1]
        self.assertEqual(result["decision"], "reject")
        decision = json.loads((Path(result["evidence_dir"]) / "decision.json").read_text())
        self.assertTrue(decision["backends"]["direct"]["benefits"]["swap_fps_plus_10pct"])
        self.assertFalse(decision["backends"]["pillow-bulk"]["benefits"]["swap_fps_plus_10pct"])

    def test_cpu_and_memory_limits_reject_despite_a_benefit(self):
        heavy = {"parent": 0.6, "renderer": 0.5, "resource_tracker": 0.0}  # 1.1 > 1.25*0.8
        host = self.host(measure=_both("candidate", swap_fps=116.0, cpu=heavy))
        result = _measure(host)[1]
        self.assertEqual(result["decision"], "reject")
        self.assertIn("cpu_ceiling", result["decision_reason"])

        root = self.root / "memory"
        root.mkdir()
        # 50 + 50 + 5 = 105 MiB: within 150% of 60 MiB, but above 60 + 32 MiB.
        bigger = {"parent": 50_000, "renderer": 50_000, "resource_tracker": 5_000}
        host = MeasureHost(root, measure=_both("candidate", swap_fps=116.0, pss_kb=bigger))
        result = _measure(host)[1]
        self.assertEqual(result["decision"], "reject")
        self.assertIn("memory_ceiling", result["decision_reason"])

    def test_rss_is_used_when_pss_is_unavailable(self):
        host = self.host(measure={"baseline-direct-1": {"pss": False}})
        result = _measure(host)[1]
        decision = json.loads((Path(result["evidence_dir"]) / "decision.json").read_text())
        self.assertEqual(decision["backends"]["direct"]["memory_metric"], "rss_total_kb")
        self.assertEqual(decision["backends"]["pillow-bulk"]["memory_metric"], "pss_total_kb")

    def test_workload_density_mismatch_is_invalid(self):
        host = self.host(measure=_both("candidate-pillow", density=1200.0))
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assertEqual(result["decision"], "invalid")
        self.assertTrue(host.service_running)
        self.assertEqual(host.head, START)

    def test_superseded_fraction_comes_from_raw_sequence_gaps(self):
        gaps = [0, 1, 4, 4, 5, 9]  # gaps of 2 and 3 superseded results -> 5
        host = self.host(measure={"candidate-direct-1": {"sequences": gaps}})
        result = _measure(host)[1]
        runs = json.loads((Path(result["evidence_dir"]) / "runs.json").read_text())
        run = next(item for item in runs if item["label"] == "candidate-direct-1")
        self.assertEqual(run["superseded"], 5)
        self.assertAlmostEqual(run["superseded_fraction"], 5 / 845)


class MeasureFailureTests(MeasureCase):
    def assert_restored(self, host, *, started=True):
        self.assertEqual(host.head, START)
        self.assertEqual(host.service_running, started)

    def _stops_after(self, label, spec, gate, decision="reject"):
        host = self.host(measure={label: spec})
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assertEqual(host.measured()[-1][1], label)
        self.assertEqual(result["decision"], decision)
        runs = json.loads((Path(result["evidence_dir"]) / "runs.json").read_text())
        failed = next(item for item in runs if item["label"] == label)
        self.assertIn(gate, failed["failed_gates"])
        later = [item for item in runs if item.get("skipped")]
        self.assertEqual(len(later) + len(host.measured()), len(remote.MEASURE_SEQUENCE))
        self.assert_restored(host)
        return host, result

    def test_candidate_without_renderer_fails_roles_and_stops(self):
        self._stops_after("candidate-direct-1", {"renderer": False}, "roles")

    def test_baseline_with_a_renderer_fails_roles(self):
        self._stops_after("baseline-direct-1", {"renderer": True}, "roles")

    def test_unknown_descendant_fails_roles(self):
        extra = [(150, 101, ["/bin/sh", "-c", "true"], "other")]
        self._stops_after("candidate-pillow-1", {"extra": extra}, "roles")

    def test_transport_drop_and_skipped_fraction_gates(self):
        self._stops_after("baseline-direct-1", {"drops": 1}, "transport_drops_zero")
        root = self.root / "skip"
        root.mkdir()
        self.root = root
        self._stops_after("baseline-direct-1", {"skipped": 50, "generations": 860},
                          "skipped_fraction")

    def test_sampler_failure_is_a_harness_fault_not_a_reject(self):
        _, result = self._stops_after("baseline-direct-1", {"sampler_rc": 1}, "sampler",
                                      decision="incomplete")
        self.assertIn("harness gates failed: baseline-direct-1", result["decision_reason"])

    def test_startup_only_transient_is_recorded_not_failed(self):
        ldconfig = [(140, 101, ["/sbin/ldconfig", "-p"], "transient")]
        host = self.host(measure=_both("candidate", startup_only=ldconfig))
        code, result, _ = _measure(host)
        self.assertEqual(code, remote.EXIT_OK)
        runs = json.loads((Path(result["evidence_dir"]) / "runs.json").read_text())
        run = next(item for item in runs if item["label"] == "candidate-direct-1")
        self.assertEqual(run["processes"]["transients"], {"140": ["/sbin/ldconfig", "-p"]})
        self.assertEqual(run["processes"]["role_pids"]["transient"], [140])

    def test_nonzero_exit_fails_the_run(self):
        self._stops_after("candidate-direct-1", {"returncode": 1}, "exit_zero")

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
        runs = json.loads((Path(result["evidence_dir"]) / "runs.json").read_text())
        self.assertIn("no_residual_process", runs[1]["failed_gates"])

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
        command = popen.calls[1][0][-1]
        sha256 = __import__("hashlib").sha256((base.TOOL_DIR / "remote.py").read_bytes()).hexdigest()
        self.assertIn(f"measure-split {BASELINE} {CANDIDATE} --expect-start {START} "
                      "--settle 60 --duration 300 --max-samples 100000 "
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
