"""Hardware-free tests for tools/pi (the scripted Pi cycle).

SSH, sudo, systemd, vcgencmd, and the FFT child are faked. The Pi-side transaction
(stop, runs, restart), the gates, and the evidence layout run for real against a
temporary directory.
"""

from __future__ import annotations

import getpass
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
import unittest.mock


REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL_DIR = REPO_ROOT / "tools" / "pi"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve annotations through sys.modules, so register first.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


remote = _load("pi_cycle_remote_under_test", TOOL_DIR / "remote.py")
pi_cycle = _load("pi_cycle_under_test", TOOL_DIR / "pi_cycle.py")

HEAD = "a" * 40
OTHER = "b" * 40
PYTHON = "/venv/bin/python3"
BASE_ARGV = ["sudo", "-n", "-E", PYTHON, "/checkout/my_programs/fft_program/main.py",
             "--led-rows", "64", "--non-interactive"]


def passing_summary(**overrides):
    summary = {
        "schema": "rgbmatrix-fft-telemetry",
        "schema_version": 3,
        "requested_duration_complete": True,
        "termination_reason": "duration_complete",
        "valid": True,
        "sample_count": 3119,
        "capacity_exhausted": False,
        "rates": {"swap_fps": 103.955, "unique_content_fps": 60.86},
        "latency": {"result_age": {"p95_ms": 22.974}},
        "audio": {"sample_rate": 44100, "block_size": 512},
        "audio_counter_deltas": {
            "analysis_sequence": 1400,
            "callback_status_count": 0,
            "worker_failure_count": 0,
        },
        "analyzed_published_fraction": 0.9888,
    }
    for path, value in overrides.items():
        node = summary
        keys = path.split(".")
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return summary


class FakeHost(remote.PiHost):
    """In-memory Pi. Every call is recorded in ``calls`` in order."""

    def __init__(self, root: Path, **state) -> None:
        self.root = root
        self.checkout = root / "checkout"
        self.checkout.mkdir()
        (self.checkout / "requirements-test.txt").write_text(
            "# pins\nnumpy==1.24.2\nPillow==9.4.0\n", encoding="utf-8"
        )
        (self.checkout / "config").mkdir()
        (self.checkout / "config" / "matrix-controller.json").write_text(
            json.dumps({"runtime": {"python_executable": PYTHON}}), encoding="utf-8"
        )
        self.user = "piuser"
        self.unit = "rgb-matrix-web@piuser.service"
        self.head = state.get("head", HEAD)
        self.detached = state.get("detached", True)
        self.porcelain = state.get("porcelain", "")
        self.commits = state.get("commits", {HEAD: HEAD, HEAD[:7]: HEAD, OTHER: OTHER})
        self.throttled = state.get("throttled", "throttled=0x0")
        self.installed = state.get("installed", {"numpy": "1.24.2", "Pillow": "9.4.0"})
        self.healthy = state.get("healthy", True)
        self.argv_result = state.get("argv_result")
        self.checkout_rc = state.get("checkout_rc", 0)
        # label -> dict(returncode, timed_out, summary, raise)
        self.behaviour = state.get("behaviour", {})
        self.leftovers = state.get("leftovers", [])
        self.stop_rc = state.get("stop_rc", 0)
        self.signal_on_start = state.get("signal_on_start")
        self.service_running = state.get("service_running", True)
        self.calls: list[tuple] = []
        self.run_files: dict[str, str] = {}
        self.term_handler_at_stop = None

    # Generic commands
    def run(self, argv, *, cwd=None, timeout=60.0):
        argv = list(argv)
        self.calls.append(("run", tuple(argv)))
        if argv[:3] == ["git", "-C", str(self.checkout)]:
            git = argv[3:]
            if git == ["rev-parse", "HEAD"]:
                return remote.Result(0, self.head + "\n")
            if git == ["symbolic-ref", "-q", "HEAD"]:
                return (remote.Result(1) if self.detached
                        else remote.Result(0, "refs/heads/main\n"))
            if git == ["status", "--porcelain"]:
                return remote.Result(0, self.porcelain)
            if git[:3] == ["rev-parse", "--verify", "--quiet"]:
                ref = git[3][: -len("^{commit}")]
                sha = self.commits.get(ref)
                return remote.Result(0, sha + "\n") if sha else remote.Result(1)
            if git[:3] == ["checkout", "--detach", "--quiet"]:
                if self.checkout_rc == 0:
                    self.head = git[3]
                    self.detached = True
                return remote.Result(self.checkout_rc, "", "checkout error")
        if argv[:2] == ["systemctl", "is-active"]:
            return remote.Result(0, "active\n" if self.service_running else "inactive\n")
        if argv == ["vcgencmd", "measure_temp"]:
            return remote.Result(0, "temp=44.3'C\n")
        if argv == ["vcgencmd", "get_throttled"]:
            value = self.throttled
            if isinstance(value, list):
                value = value.pop(0) if len(value) > 1 else value[0]
            return remote.Result(0, value + "\n")
        if argv[:2] == ["pgrep", "-af"]:
            return remote.Result(0, "123 python3 system_info.py\n")
        if argv[:2] == [PYTHON, "-c"]:
            return remote.Result(0, json.dumps(self.installed))
        if argv[:2] == ["/usr/bin/python3", "-c"]:
            if self.argv_result is not None:
                return self.argv_result
            return remote.Result(0, json.dumps({
                "argv": BASE_ARGV, "cwd": "/checkout/my_programs/fft_program",
                "python": PYTHON,
            }))
        raise AssertionError(f"unexpected command {argv}")

    def healthz(self):
        self.calls.append(("healthz",))
        ok = self.healthy and self.service_running
        return ok, '{"status":"ok"}' if ok else "connection refused"

    def sleep(self, seconds):
        self.calls.append(("sleep", seconds))
        self._clock = getattr(self, "_clock", 0.0) + seconds

    def clock(self):
        return getattr(self, "_clock", 0.0)

    def utc_stamp(self):
        return "20261120T120000Z"

    def evidence_root(self):
        return self.root / "evidence"

    # State changes
    def stop_service(self):
        self.calls.append(("stop",))
        self.term_handler_at_stop = signal.getsignal(signal.SIGTERM)
        if self.stop_rc == 0:
            self.service_running = False
        return remote.Result(self.stop_rc, "", "stop error" if self.stop_rc else "")

    def start_service(self):
        self.calls.append(("start",))
        if self.signal_on_start is not None:
            os.kill(os.getpid(), self.signal_on_start)
        self.service_running = True
        return remote.Result(0)

    def fft_processes(self):
        self.calls.append(("fft_processes",))
        return list(self.leftovers)

    def make_run_dir(self, run_dir):
        self.calls.append(("make_run_dir", str(run_dir)))
        return remote.Result(0)

    def collect_run_dir(self, run_dir, evidence_dir):
        self.calls.append(("collect", str(run_dir), str(evidence_dir)))
        for path, text in self.run_files.items():
            if path.startswith(str(run_dir) + "/"):
                (evidence_dir / Path(path).name).write_text(text, encoding="utf-8")
        return remote.Result(0)

    def remove_run_dir(self, run_dir):
        self.calls.append(("remove_run_dir", str(run_dir)))
        return remote.Result(0)

    def run_foreground(self, argv, *, cwd, env, timeout, log_path):
        label = argv[argv.index("--telemetry-label") + 1]
        self.calls.append(("foreground", label, tuple(argv), dict(env), timeout))
        assert not self.service_running, "foreground run while the service is up"
        log_path.write_text(f"log for {label}\n", encoding="utf-8")
        behaviour = self.behaviour.get(label, {})
        if "raise" in behaviour:
            raise behaviour["raise"]
        if "signal" in behaviour:
            os.kill(os.getpid(), behaviour["signal"])
        summary = behaviour.get("summary", passing_summary())
        out = argv[argv.index("--telemetry-out") + 1]
        if summary is not None:
            self.run_files[out] = (summary if isinstance(summary, str)
                                   else json.dumps(summary))
        return {
            "returncode": behaviour.get("returncode", 0),
            "timed_out": behaviour.get("timed_out", False),
            "kill_escalated": behaviour.get("kill_escalated", False),
        }

    def names(self):
        return [call[0] if call[0] != "run" else " ".join(call[1][:2]) for call in self.calls]


def _run(host, *args, stream=None):
    report = remote.Reporter(stream or io.StringIO())
    code = remote.main(["--checkout", str(host.checkout), *args],
                       host_factory=lambda _path: host, report=report)
    output = report.stream.getvalue() if isinstance(report.stream, io.StringIO) else ""
    result_lines = [line for line in output.splitlines()
                    if line.startswith(remote.RESULT_MARKER)]
    result = json.loads(result_lines[-1][len(remote.RESULT_MARKER):]) if result_lines else None
    return code, result, output


def _check(host, *extra, stream=None):
    return _run(host, "check", HEAD[:7], "--modes", "direct,pillow-bulk,direct",
                "--settle", "5", "--duration", "30", "--max-samples", "10000", *extra,
                stream=stream)


class TempHostCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def host(self, **state):
        return FakeHost(self.root, **state)


# Pi side: status ------------------------------------------------------------------


class StatusTests(TempHostCase):
    def test_status_runs_only_read_only_commands(self):
        host = self.host()
        code, result, _ = _run(host, "status")
        self.assertEqual(code, 0)
        allowed_git = {"rev-parse", "symbolic-ref", "status"}
        for call in host.calls:
            self.assertIn(call[0], {"run", "healthz"}, call)
            if call[0] != "run":
                continue
            argv = call[1]
            self.assertNotIn("sudo", argv)
            if argv[0] == "git":
                self.assertIn(argv[3], allowed_git, argv)
            elif argv[0] == "systemctl":
                self.assertEqual(argv[1], "is-active")
            elif argv[0] == "vcgencmd":
                self.assertIn(argv[1], {"measure_temp", "get_throttled"})
            elif argv[0] == "pgrep":
                self.assertEqual(argv[1:], ("-af", "system_info.py"))
            else:
                self.assertEqual(argv[:2], (PYTHON, "-c"))
        status = result["status"]
        self.assertEqual(status["head"], HEAD)
        self.assertTrue(status["detached"])
        self.assertEqual(status["porcelain"], [])
        self.assertEqual(status["throttled"], 0)
        self.assertEqual(status["packages"]["drift"], [])

    def test_package_drift_is_reported_but_not_fatal(self):
        host = self.host(installed={"numpy": "1.26.4", "Pillow": "9.4.0"})
        code, result, output = _run(host, "status")
        self.assertEqual(code, 0)
        self.assertEqual(result["status"]["packages"]["drift"], ["numpy"])
        self.assertIn("DRIFT numpy pinned 1.24.2 installed 1.26.4", output)


# Pi side: checkout (deploy and restore) -------------------------------------------


class CheckoutTests(TempHostCase):
    def test_dirty_tree_is_refused_before_anything_changes(self):
        host = self.host(head=OTHER, porcelain=" M main.py\n")
        code, result, _ = _run(host, "checkout", HEAD)
        self.assertEqual(code, remote.EXIT_REFUSED)
        self.assertIn("not clean", result["refused"])
        self.assertNotIn("stop", host.names())
        self.assertNotIn("start", host.names())

    def test_unknown_ref_and_expected_sha_mismatch_are_refused(self):
        host = self.host(head=OTHER)
        self.assertEqual(_run(host, "checkout", "nope")[0], remote.EXIT_REFUSED)
        code, result, _ = _run(host, "checkout", HEAD, "--expect-sha", OTHER)
        self.assertEqual(code, remote.EXIT_REFUSED)
        self.assertIn("expected", result["refused"])
        self.assertNotIn("stop", host.names())

    def test_checkout_stops_checks_out_starts_and_reports_previous(self):
        host = self.host(head=OTHER)
        code, result, output = _run(host, "checkout", HEAD, "--expect-sha", HEAD)
        self.assertEqual(code, remote.EXIT_OK)
        names = host.names()
        stop = names.index("stop")
        checkout = next(i for i, call in enumerate(host.calls)
                        if call[0] == "run" and "checkout" in call[1])
        self.assertLess(stop, checkout)
        self.assertLess(checkout, names.index("start"))
        self.assertEqual(result["previous"], OTHER)
        self.assertEqual(result["current"], HEAD)
        self.assertTrue(result["changed"])
        self.assertIn(f"previous  {OTHER}", output)

    def test_already_at_target_does_not_restart(self):
        host = self.host()
        code, result, _ = _run(host, "checkout", HEAD)
        self.assertEqual(code, remote.EXIT_OK)
        self.assertFalse(result["changed"])
        self.assertNotIn("stop", host.names())

    def test_at_target_with_service_down_starts_it_without_checkout(self):
        host = self.host(service_running=False)
        code, result, _ = _run(host, "checkout", HEAD)
        self.assertEqual(code, remote.EXIT_OK)
        self.assertIn("start", host.names())
        self.assertTrue(host.service_running)
        self.assertFalse(result["changed"])
        self.assertFalse(any(call[0] == "run" and "checkout" in call[1] for call in host.calls))

    def test_failed_stop_still_attempts_a_start(self):
        host = self.host(head=OTHER, stop_rc=124)
        code, result, _ = _run(host, "checkout", HEAD)
        self.assertEqual(code, remote.EXIT_OPERATION_FAILED)
        self.assertIn("start", host.names())
        self.assertFalse(any(call[0] == "run" and "checkout" in call[1] for call in host.calls))

    def test_stop_checkout_start_ignores_hangup_and_term(self):
        host = self.host(head=OTHER)
        previous = signal.getsignal(signal.SIGTERM)
        _run(host, "checkout", HEAD)
        self.assertEqual(host.term_handler_at_stop, signal.SIG_IGN)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_failed_checkout_still_restarts_the_service(self):
        host = self.host(head=OTHER, checkout_rc=1)
        code, result, _ = _run(host, "checkout", HEAD)
        self.assertEqual(code, remote.EXIT_OPERATION_FAILED)
        self.assertIn("start", host.names())
        self.assertTrue(host.service_running)
        self.assertTrue(any("checkout failed" in e for e in result["errors"]))

    def test_unhealthy_after_start_exits_3_without_rollback(self):
        host = self.host(head=OTHER, healthy=False)
        code, result, _ = _run(host, "checkout", HEAD)
        self.assertEqual(code, remote.EXIT_OPERATION_FAILED)
        checkouts = [call for call in host.calls if call[0] == "run" and "checkout" in call[1]]
        self.assertEqual(len(checkouts), 1, "no automatic rollback (D-009)")
        self.assertEqual(result["previous"], OTHER)
        self.assertIn(("sleep", 1.0), host.calls, "healthz is retried within the bound")


# Pi side: check ------------------------------------------------------------------


class CheckRefusalTests(TempHostCase):
    def assert_refused_without_changes(self, host, text):
        code, result, _ = _check(host)
        self.assertEqual(code, remote.EXIT_REFUSED)
        self.assertIn(text, result["refused"])
        for name in ("stop", "start", "make_run_dir", "foreground"):
            self.assertNotIn(name, host.names())
        self.assertFalse((self.root / "evidence").exists())

    def test_dirty_tree(self):
        self.assert_refused_without_changes(self.host(porcelain="?? x\n"), "not clean")

    def test_head_mismatch(self):
        self.assert_refused_without_changes(self.host(head=OTHER), "run deploy first")

    def test_not_detached(self):
        self.assert_refused_without_changes(self.host(detached=False), "on a branch")

    def test_controller_argv_failure(self):
        host = self.host(argv_result=remote.Result(1, "", "ImportError: boom"))
        self.assert_refused_without_changes(host, "ImportError: boom")

    def test_bad_arguments(self):
        host = self.host()
        code, result, _ = _run(host, "check", HEAD, "--modes", "direct,bogus",
                               "--settle", "5", "--duration", "30", "--max-samples", "10")
        self.assertEqual(code, remote.EXIT_REFUSED)
        self.assertIn("bogus", result["refused"])
        code, _, _ = _run(host, "check", HEAD, "--modes", "direct",
                          "--settle", "5", "--duration", "nan", "--max-samples", "10")
        self.assertEqual(code, remote.EXIT_REFUSED)


class CheckRunTests(TempHostCase):
    def test_passing_check_runs_modes_in_order_and_keeps_evidence(self):
        host = self.host()
        code, result, _ = _check(host)
        self.assertEqual(code, remote.EXIT_OK, result.get("errors"))
        self.assertTrue(result["passed"])

        runs = [call for call in host.calls if call[0] == "foreground"]
        self.assertEqual([call[1] for call in runs], ["direct-1", "pillow-bulk-1", "direct-2"])
        self.assertEqual([call[3]["RGBMATRIX_FFT_OUTPUT_PATH"] for call in runs],
                         ["direct", "pillow-bulk", "direct"])
        argv = list(runs[0][2])
        self.assertEqual(argv[:len(BASE_ARGV)], BASE_ARGV)
        flags = dict(zip(argv[len(BASE_ARGV)::2], argv[len(BASE_ARGV) + 1::2]))
        run_dir = "/run/rgb-matrix-check-20261120T120000Z"
        self.assertEqual(flags, {
            "--telemetry-out": f"{run_dir}/direct-1.json",
            "--telemetry-label": "direct-1",
            "--telemetry-source-id": HEAD,
            "--telemetry-settle": "5",
            "--telemetry-duration": "30",
            "--telemetry-max-samples": "10000",
        })
        self.assertEqual(runs[0][3]["RGBMATRIX_STATE_DIR"], f"{run_dir}/state")
        self.assertEqual(runs[0][4], 5 + 30 + remote.CHILD_MARGIN_SECONDS)

        names = host.names()
        self.assertLess(names.index("make_run_dir"), names.index("stop"))
        self.assertLess(max(i for i, n in enumerate(names) if n == "foreground"),
                        names.index("start"))
        self.assertLess(names.index("collect"), names.index("remove_run_dir"))

        evidence = self.root / "evidence" / f"20261120T120000Z-{HEAD[:7]}"
        self.assertEqual(result["evidence_dir"], str(evidence))
        expected = {"argv.json", "env.json", "status-before.json", "status-after.json",
                    "results.md", "SHA256SUMS", "check.log",
                    "direct-1.json", "pillow-bulk-1.json", "direct-2.json",
                    "direct-1.log", "pillow-bulk-1.log", "direct-2.log"}
        self.assertEqual({p.name for p in evidence.iterdir()}, expected)
        for line in (evidence / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ")
            self.assertEqual(hashlib.sha256((evidence / name).read_bytes()).hexdigest(),
                             digest)
        recorded = json.loads((evidence / "argv.json").read_text())
        self.assertEqual(recorded["controller"]["argv"], BASE_ARGV)
        self.assertEqual(recorded["runs"]["direct-1"], argv, "the exact per-run command")
        envs = json.loads((evidence / "env.json").read_text())
        self.assertEqual(envs["pillow-bulk-1"]["RGBMATRIX_FFT_OUTPUT_PATH"], "pillow-bulk")

        markdown = (evidence / "results.md").read_text()
        self.assertEqual(markdown, result["results_markdown"])
        self.assertIn("| direct-1 | direct | yes | 3119 | 103.955 | 60.860 | 98.88% "
                      "| 22.974 ms (23.22 ms) | 0 / 0 | 0x0 | none |", markdown)
        self.assertIn("Result: **PASS**", markdown)
        self.assertIn("- Throttle before: 0x0", markdown)

    def test_gate_failure_exits_1_and_still_restarts(self):
        low = passing_summary(analyzed_published_fraction=0.90)
        host = self.host(behaviour={"pillow-bulk-1": {"summary": low}})
        code, result, _ = _check(host)
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        failed = {run["label"]: run["failed_gates"] for run in result["runs"]}
        self.assertEqual(failed, {"direct-1": [], "pillow-bulk-1": ["analyzed_published"],
                                  "direct-2": []})
        self.assertTrue(host.service_running)
        self.assertIn("Result: **FAIL**", result["results_markdown"])

    def test_child_timeout_fails_exit_gate(self):
        host = self.host(behaviour={"direct-1": {"returncode": -2, "timed_out": True}})
        code, result, _ = _check(host)
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assertEqual(result["runs"][0]["failed_gates"], ["exit_zero"])
        self.assertTrue(host.service_running)

    def test_missing_or_garbage_summary_fails_without_crashing(self):
        host = self.host(behaviour={"direct-1": {"summary": None},
                                    "direct-2": {"summary": "{not json"}})
        code, result, _ = _check(host)
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        for index in (0, 2):
            self.assertIn("schema", result["runs"][index]["failed_gates"])
            self.assertIn("summary missing or unreadable", result["runs"][index]["notes"])

    def test_exception_mid_check_restarts_and_skips_the_rest(self):
        host = self.host(behaviour={"pillow-bulk-1": {"raise": RuntimeError("audio gone")}})
        code, result, _ = _check(host)
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assertTrue(host.service_running)
        self.assertEqual(host.names().count("foreground"), 2)
        self.assertTrue(result["runs"][2]["skipped"])
        self.assertTrue(any("audio gone" in error for error in result["errors"]))
        self.assertIn("remove_run_dir", host.names())

    def test_sigterm_during_a_run_restarts_the_service(self):
        host = self.host(behaviour={"direct-1": {"signal": signal.SIGTERM}})
        previous = signal.getsignal(signal.SIGTERM)
        code, result, _ = _check(host)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous, "handler restored")
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assertTrue(host.service_running)
        self.assertTrue(any("CheckAborted" in error for error in result["errors"]))
        self.assertTrue(all(run["skipped"] for run in result["runs"][1:]))

    def test_dropped_mac_session_skips_remaining_runs_but_restarts(self):
        class Breaks(io.StringIO):
            writes = 0

            def write(self, text):
                Breaks.writes += 1
                if Breaks.writes > 4:
                    raise BrokenPipeError
                return super().write(text)

        host = self.host()
        code = remote.main(
            ["--checkout", str(host.checkout), "check", HEAD, "--modes", "direct,direct",
             "--settle", "1", "--duration", "1", "--max-samples", "10"],
            host_factory=lambda _path: host, report=remote.Reporter(Breaks()),
        )
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assertEqual(host.names().count("foreground"), 1)
        self.assertTrue(host.service_running)
        evidence = self.root / "evidence" / f"20261120T120000Z-{HEAD[:7]}"
        markdown = (evidence / "results.md").read_text()
        self.assertIn("the Mac side disconnected", markdown)
        self.assertIn(remote.RESULT_MARKER, (evidence / "check.log").read_text())

    def test_sighup_during_a_run_restarts_the_service(self):
        host = self.host(behaviour={"direct-1": {"signal": signal.SIGHUP}})
        code, result, _ = _check(host)
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assertTrue(host.service_running)
        self.assertTrue(any("SIGHUP" in error for error in result["errors"]))

    def test_second_signal_during_restart_cannot_skip_it(self):
        host = self.host(behaviour={"direct-1": {"signal": signal.SIGTERM}},
                         signal_on_start=signal.SIGTERM)
        code, result, _ = _check(host)
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assertTrue(host.service_running)
        self.assertIn("remove_run_dir", host.names())
        self.assertIsNotNone(result, "the result is still reported")

    def test_leftover_fft_process_blocks_the_restart(self):
        host = self.host(leftovers=["999 python3 my_programs/fft_program/main.py"])
        code, result, _ = _check(host)
        self.assertEqual(code, remote.EXIT_OPERATION_FAILED)
        self.assertNotIn("start", host.names())
        self.assertTrue(any("FFT process still running" in e for e in result["errors"]))

    def test_escalated_kill_fails_the_exit_gate(self):
        host = self.host(behaviour={"direct-2": {"returncode": -9, "timed_out": True,
                                                 "kill_escalated": True}})
        code, result, _ = _check(host)
        self.assertEqual(code, remote.EXIT_GATE_FAILED)
        self.assertEqual(result["runs"][2]["failed_gates"], ["exit_zero"])
        self.assertIn("needed SIGKILL to stop", result["runs"][2]["notes"])

    def test_evidence_dir_collision_is_refused_before_changes(self):
        host = self.host()
        (self.root / "evidence" / f"20261120T120000Z-{HEAD[:7]}").mkdir(parents=True)
        code, result, _ = _check(host)
        self.assertEqual(code, remote.EXIT_REFUSED)
        self.assertIn("already exists", result["refused"])
        self.assertNotIn("stop", host.names())

    def test_unhealthy_after_restart_exits_3(self):
        host = self.host(healthy=False)
        code, result, _ = _check(host)
        self.assertEqual(code, remote.EXIT_OPERATION_FAILED)
        self.assertFalse(result["healthy"])
        self.assertIn("UNHEALTHY", result["results_markdown"])


# Gates ----------------------------------------------------------------------------


class GateTests(unittest.TestCase):
    RUN = {"label": "direct-1", "mode": "direct", "returncode": 0, "throttled": 0}

    def failed(self, summary, run=None, baseline=0):
        return remote.evaluate_run(dict(self.RUN, **(run or {})), summary, baseline)["failed_gates"]

    def test_passing_summary_passes_every_gate(self):
        self.assertEqual(self.failed(passing_summary()), [])

    def test_each_gate_fails_on_its_own(self):
        cases = {
            "exit_zero": ({}, {"returncode": 1}),
            "schema": ({"schema_version": 2}, {}),
            "full_duration": ({"termination_reason": "capacity_exhausted"}, {}),
            "frames": ({"sample_count": 0}, {}),
            "analyzer_sequences": ({"audio_counter_deltas.analysis_sequence": 0}, {}),
            "analyzed_published": ({"analyzed_published_fraction": 0.9499}, {}),
            "result_age_p95": ({"latency.result_age.p95_ms": 23.3}, {}),
            "no_callback_failures": ({"audio_counter_deltas.callback_status_count": 1}, {}),
            "no_worker_failures": ({"audio_counter_deltas.worker_failure_count": 2}, {}),
            "capacity_intact": ({"capacity_exhausted": True}, {}),
            "no_throttling": ({}, {"throttled": 0x4}),
        }
        self.assertEqual(set(cases), set(remote.evaluate_run(self.RUN, passing_summary(), 0)["gates"]))
        for gate, (overrides, run) in cases.items():
            with self.subTest(gate=gate):
                self.assertEqual(self.failed(passing_summary(**overrides), run), [gate])

    def test_result_age_limit_follows_the_recorded_audio_format(self):
        at_48k = passing_summary(**{"audio.sample_rate": 48000, "latency.result_age.p95_ms": 22.0})
        evaluation = remote.evaluate_run(self.RUN, at_48k, 0)
        self.assertAlmostEqual(evaluation["result_age_limit_ms"], 2 * 512 / 48000 * 1000)
        self.assertEqual(evaluation["failed_gates"], ["result_age_p95"])
        unknown = passing_summary(**{"audio.sample_rate": None})
        self.assertEqual(self.failed(unknown), ["result_age_p95"])

    def test_no_summary_fails_every_summary_gate(self):
        failed = self.failed(None)
        self.assertEqual(set(failed), set(remote.evaluate_run(self.RUN, passing_summary(), 0)["gates"])
                         - {"exit_zero", "no_throttling"})

    def test_throttle_gate_ignores_pre_existing_sticky_bits_only(self):
        gate = remote._throttle_gate
        self.assertTrue(gate(0x50000, 0x50000)[0])
        self.assertFalse(gate(0x50005, 0x50000)[0])
        self.assertFalse(gate(0x50000, 0x0)[0])
        self.assertFalse(gate(None, 0)[0])
        self.assertEqual(remote._parse_throttled("throttled=0x50000"), 0x50000)
        self.assertIsNone(remote._parse_throttled("garbage"))

    def test_run_labels(self):
        self.assertEqual(remote.run_labels(["direct", "pillow-bulk", "direct"]),
                         [("direct-1", "direct"), ("pillow-bulk-1", "pillow-bulk"),
                          ("direct-2", "direct")])

    def test_gate_schema_matches_the_recorder(self):
        source = (REPO_ROOT / "my_programs/fft_program/core/telemetry.py").read_text()
        self.assertIn(f'TELEMETRY_SCHEMA = "{remote.TELEMETRY_SCHEMA}"', source)
        version = re.search(r"^TELEMETRY_SCHEMA_VERSION = (\d+)$", source, re.M).group(1)
        self.assertEqual(int(version), remote.TELEMETRY_SCHEMA_VERSION)


# Real PiHost pieces that are safe on a development machine ------------------------


class RealHostTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_state_change_command_lines(self):
        host = remote.PiHost(self.root)
        seen = []
        host.run = lambda argv, **kwargs: seen.append(list(argv)) or remote.Result(0)
        host.stop_service()
        host.start_service()
        host.make_run_dir(Path("/run/rgb-matrix-check-X"))
        host.remove_run_dir(Path("/run/rgb-matrix-check-X"))
        unit = f"rgb-matrix-web@{getpass.getuser()}.service"
        self.assertEqual(seen[0], ["sudo", "-n", "systemctl", "stop", unit])
        self.assertEqual(seen[1], ["sudo", "-n", "systemctl", "start", unit])
        self.assertEqual(seen[2], ["sudo", "-n", "install", "-d", "-m", "0755", "-o", "daemon",
                                   "-g", "daemon", "/run/rgb-matrix-check-X",
                                   "/run/rgb-matrix-check-X/state"])
        self.assertEqual(seen[3], ["sudo", "-n", "rm", "-rf", "--", "/run/rgb-matrix-check-X"])
        with self.assertRaises(ValueError):
            host.remove_run_dir(Path("/home/user"))

    def test_run_foreground_stops_a_child_that_overruns(self):
        host = remote.PiHost(self.root)
        script = ("import os, time; print(os.environ['RGBMATRIX_FFT_OUTPUT_PATH'], "
                  "os.environ.get('RGBMATRIX_FFT_STEP6_X')); time.sleep(30)")
        os.environ["RGBMATRIX_FFT_STEP6_X"] = "1"
        try:
            outcome = host.run_foreground(
                [sys.executable, "-c", script], cwd=self.root,
                env={"RGBMATRIX_FFT_OUTPUT_PATH": "direct"}, timeout=1.0,
                log_path=self.root / "child.log",
            )
        finally:
            del os.environ["RGBMATRIX_FFT_STEP6_X"]
        self.assertTrue(outcome["timed_out"])
        self.assertFalse(outcome["kill_escalated"], "SIGINT was enough")
        self.assertNotEqual(outcome["returncode"], 0)
        self.assertIn("direct None", (self.root / "child.log").read_text())

    def test_group_stop_reaches_a_grandchild_that_ignores_signals(self):
        host = remote.PiHost(self.root)
        host.STOP_GRACE_SECONDS = (0.3, 0.3, 5.0)
        stubborn = ("import signal, subprocess, sys, time;"
                    "signal.signal(signal.SIGINT, signal.SIG_IGN);"
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                    "child = subprocess.Popen([sys.executable, '-c', "
                    "'import signal, time; signal.signal(2, signal.SIG_IGN);"
                    " signal.signal(15, signal.SIG_IGN); time.sleep(60)']);"
                    "print(child.pid, flush=True); time.sleep(60)")
        outcome = host.run_foreground(
            [sys.executable, "-c", stubborn], cwd=self.root, env={}, timeout=1.0,
            log_path=self.root / "child.log",
        )
        self.assertTrue(outcome["timed_out"])
        self.assertTrue(outcome["kill_escalated"])
        grandchild = int((self.root / "child.log").read_text().split()[0])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            os.kill(grandchild, signal.SIGKILL)
            self.fail("grandchild survived the group stop")

    def test_fft_argv_snippet_runs_against_this_checkout(self):
        built = subprocess.run([sys.executable, "-c", remote.FFT_ARGV_SNIPPET],
                               cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(built.returncode, 0, built.stderr)
        launch = json.loads(built.stdout)
        config = json.loads((REPO_ROOT / "config/matrix-controller.json").read_text())
        if config["runtime"].get("use_sudo"):
            self.assertEqual(launch["argv"][:3], ["sudo", "-n", "-E"])
        self.assertTrue(launch["argv"][4 if config["runtime"].get("use_sudo") else 1]
                        .endswith("my_programs/fft_program/main.py"))
        self.assertIn("--non-interactive", launch["argv"])
        self.assertTrue(launch["cwd"].endswith("my_programs/fft_program"))


# Mac side -------------------------------------------------------------------------


def _git_stub(remote_url=None, commits=None):
    commits = commits if commits is not None else {HEAD[:7]: HEAD, HEAD: HEAD}

    def git(*args):
        if args[:2] == ("remote", "get-url"):
            if remote_url is None:
                return SimpleNamespace(returncode=2, stdout="", stderr="no remote")
            return SimpleNamespace(returncode=0, stdout=remote_url + "\n", stderr="")
        if args[:3] == ("rev-parse", "--verify", "--quiet"):
            sha = commits.get(args[3][: -len("^{commit}")])
            return SimpleNamespace(returncode=0 if sha else 1, stdout=(sha or "") + "\n")
        raise AssertionError(args)

    return git


class FakeProcess:
    def __init__(self, lines, returncode):
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: None
        self.stdout = [line.encode() + b"\n" for line in lines]
        self.returncode = returncode

    def wait(self):
        return self.returncode


class FakePopen:
    def __init__(self, result=None, returncode=0, progress=("working",)):
        self.calls = []
        self.result = result
        self.returncode = returncode
        self.progress = list(progress)

    def __call__(self, argv, **kwargs):
        lines = list(self.progress)
        if self.result is not None:
            lines.append(remote.RESULT_MARKER + json.dumps(self.result))
        process = FakeProcess(lines, self.returncode)
        self.calls.append((argv, process))
        return process


class HostResolutionTests(unittest.TestCase):
    def test_precedence_flag_then_env_then_remote(self):
        git = _git_stub("ssh://me@remote.example/~/rgb_Matrix")
        env = {pi_cycle.HOST_ENV: "envhost"}
        self.assertEqual(pi_cycle.resolve_target("flaghost", None, env, git).destination,
                         "flaghost")
        target = pi_cycle.resolve_target(None, None, env, git)
        self.assertEqual((target.destination, target.checkout, target.push_url),
                         ("envhost", "~/rgb_Matrix", "ssh://envhost/~/rgb_Matrix"))
        target = pi_cycle.resolve_target(None, None, {}, git)
        self.assertEqual((target.destination, target.checkout, target.push_url),
                         ("me@remote.example", "~/rgb_Matrix",
                          "ssh://me@remote.example/~/rgb_Matrix"))

    def test_push_url_always_matches_the_checkout(self):
        git = _git_stub("ssh://me@h:2222/~/rgb_Matrix")
        target = pi_cycle.resolve_target(None, "/srv/other", {}, git)
        self.assertEqual((target.checkout, target.push_url),
                         ("/srv/other", "ssh://me@h:2222/srv/other"))
        target = pi_cycle.resolve_target(None, None, {pi_cycle.CHECKOUT_ENV: "alt"}, git)
        self.assertEqual((target.checkout, target.push_url), ("~/alt", "ssh://me@h:2222/~/alt"))
        target = pi_cycle.resolve_target("pihost", "rgb_Matrix", {}, git)
        self.assertEqual((target.checkout, target.push_url),
                         ("~/rgb_Matrix", "ssh://pihost/~/rgb_Matrix"))

    def test_remote_url_forms(self):
        parse = pi_cycle.parse_remote_url
        target = parse("ssh://me@h:2222/srv/rgb")
        self.assertEqual((target.destination, target.port, target.checkout), ("me@h", 2222, "/srv/rgb"))
        self.assertEqual(target.ssh_argv()[-3:], ["-p", "2222", "me@h"])
        self.assertEqual(parse("me@h:rgb_Matrix").checkout, "~/rgb_Matrix")
        self.assertEqual(parse("alias:/abs/path").checkout, "/abs/path")
        with self.assertRaises(pi_cycle.UsageError):
            parse("https://github.com/x/y")

    def test_no_target_exits_2_before_any_ssh(self):
        popen = FakePopen()
        out = io.StringIO()
        code = pi_cycle.main(["status"], environ={}, git=_git_stub(None), popen=popen, out=out)
        self.assertEqual(code, pi_cycle.EXIT_REFUSED)
        self.assertEqual(popen.calls, [])
        self.assertIn(pi_cycle.HOST_ENV, out.getvalue())

    def test_no_hostname_or_user_is_committed(self):
        forbidden = ["raspberry" + "pi.local", getpass.getuser()]
        for path in [*TOOL_DIR.glob("*.py"), Path(__file__)]:
            text = path.read_text(encoding="utf-8")
            for word in forbidden:
                self.assertNotIn(word, text, f"{word!r} in {path.name}")


class MacCommandTests(unittest.TestCase):
    def main(self, argv, popen, run=None):
        out = io.StringIO()
        code = pi_cycle.main(argv, environ={pi_cycle.HOST_ENV: "pihost"}, git=_git_stub(),
                             popen=popen, run=run or (lambda *a, **k: SimpleNamespace(returncode=0)),
                             out=out)
        return code, out.getvalue()

    def test_remote_command_sends_the_script_on_stdin_with_quoted_args(self):
        popen = FakePopen(result={"command": "status"})
        code, output = self.main(["--checkout", "~/my dir", "status"], popen)
        self.assertEqual(code, 0)
        argv, process = popen.calls[0]
        self.assertEqual(argv[:5], ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"])
        self.assertEqual(argv[5], "pihost")
        self.assertEqual(argv[6], "python3 - --checkout '~/my dir' status")
        self.assertEqual(process.stdin.getvalue(), (TOOL_DIR / "remote.py").read_bytes())
        self.assertIn("  pi | working", output)
        self.assertNotIn(remote.RESULT_MARKER, output)

    def test_deploy_pushes_then_checks_out_with_expected_sha(self):
        pushes = []
        popen = FakePopen(result={"command": "checkout", "previous": OTHER,
                                  "current": HEAD, "changed": True})
        code, output = self.main(["deploy", HEAD[:7]], popen,
                                 run=lambda argv, **k: pushes.append(argv) or SimpleNamespace(returncode=0))
        self.assertEqual(code, 0)
        self.assertEqual(pushes[0][-2:], ["ssh://pihost/~/rgb_Matrix", f"+{HEAD}:refs/heads/deploy"])
        self.assertTrue(popen.calls[0][0][-1].endswith(f"checkout {HEAD} --expect-sha {HEAD}"))
        self.assertIn(f"Roll back with: python3 tools/pi/pi_cycle.py restore {OTHER}", output)

    def test_deploy_refuses_unknown_commit_and_failed_push(self):
        popen = FakePopen()
        self.assertEqual(self.main(["deploy", "nope"], popen)[0], pi_cycle.EXIT_REFUSED)
        failed = lambda *a, **k: SimpleNamespace(returncode=1)
        self.assertEqual(self.main(["deploy", HEAD], popen, run=failed)[0], pi_cycle.EXIT_REFUSED)
        self.assertEqual(popen.calls, [])

    def test_unhealthy_deploy_suggests_restore_without_rolling_back(self):
        popen = FakePopen(result={"previous": OTHER, "current": HEAD, "changed": True},
                          returncode=3)
        code, output = self.main(["deploy", HEAD], popen)
        self.assertEqual(code, 3)
        self.assertEqual(len(popen.calls), 1)
        self.assertIn("No rollback was attempted", output)
        self.assertIn(f"restore {OTHER}", output)

    def test_restore_does_not_push(self):
        pushes = []
        popen = FakePopen(result={"previous": HEAD, "current": OTHER, "changed": True})
        self.main(["restore", "archive/pi-snapshot"], popen, run=lambda *a, **k: pushes.append(a))
        self.assertEqual(pushes, [])
        self.assertTrue(popen.calls[0][0][-1].endswith("checkout archive/pi-snapshot"))

    def test_check_forwards_validated_options_and_prints_the_table(self):
        popen = FakePopen(result={"results_markdown": "| table |\n",
                                  "evidence_dir": "/home/u/rgb-matrix-evidence/x"},
                          returncode=1)
        code, output = self.main(["check", HEAD[:7], "--duration", "10"], popen)
        self.assertEqual(code, 1)
        self.assertTrue(popen.calls[0][0][-1].endswith(
            f"check {HEAD} --modes direct,pillow-bulk,direct --settle 5 "
            "--duration 10 --max-samples 100000"))
        self.assertIn("| table |", output)
        self.assertIn("Evidence (on the Pi): /home/u/rgb-matrix-evidence/x", output)

    def test_check_sha_is_resolved_on_the_mac(self):
        popen = FakePopen()
        self.assertEqual(self.main(["check", "deploy"], popen)[0], pi_cycle.EXIT_REFUSED)
        self.assertEqual(popen.calls, [])

    def test_ctrl_c_on_the_mac_exits_130_with_guidance(self):
        def interrupted(*args, **kwargs):
            raise KeyboardInterrupt

        code, output = self.main(["status"], interrupted)
        self.assertEqual(code, 130)
        self.assertIn("restarts the service", output)

    def test_check_rejects_bad_options_before_ssh(self):
        popen = FakePopen()
        for bad in (["--modes", "direct,warp"], ["--duration", "0"], ["--duration", "nan"],
                    ["--settle", "-1"], ["--max-samples", "0"]):
            with self.subTest(bad=bad), self.assertRaises(SystemExit) as raised:
                with unittest.mock.patch("sys.stderr", io.StringIO()):
                    self.main(["check", HEAD, *bad], popen)
            self.assertEqual(raised.exception.code, 2)
        self.assertEqual(popen.calls, [])

    def test_missing_result_document_is_an_operation_failure(self):
        popen = FakePopen(result=None, returncode=0)
        self.assertEqual(self.main(["status"], popen)[0], pi_cycle.EXIT_OPERATION_FAILED)
        refused = FakePopen(result=None, returncode=255)
        self.assertEqual(self.main(["status"], refused)[0], 255)


if __name__ == "__main__":
    unittest.main()
