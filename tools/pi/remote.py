"""Pi side of the scripted Pi cycle.

``pi_cycle.py`` on the Mac sends this file to ``python3 -`` over SSH stdin, so nothing
is installed on the Pi and the gates always come from the tool's version, not from
the candidate commit being checked. It uses the standard library only and runs under
the Pi's system Python.

Every Pi-state change (service stop/start, checkout, ``/run`` output, evidence) and
every gate verdict is made here. Progress lines go to stdout. The final line is
``RESULT_MARKER`` followed by one JSON document, which the Mac parses.

Exit codes: 0 success, 1 a gate failed (or the check was aborted), 2 refused
before anything changed, 3 an operation failed after Pi state changed (for example
the service is unhealthy after restart).
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Optional, Sequence
import urllib.error
import urllib.request


RESULT_MARKER = "@@PI_CYCLE_RESULT@@ "
EXIT_OK = 0
EXIT_GATE_FAILED = 1
EXIT_REFUSED = 2
EXIT_OPERATION_FAILED = 3

TELEMETRY_SCHEMA = "rgbmatrix-fft-telemetry"
TELEMETRY_SCHEMA_VERSION = 3
OUTPUT_MODES = ("direct", "pillow-bulk")
AUDIO_SOURCES = ("usb-mic", "bluetooth")
MIN_ANALYZED_PUBLISHED_FRACTION = 0.95
RESULT_AGE_LIMIT_BLOCKS = 2
# The matrix library drops the FFT child to this user after GPIO setup, and the
# child writes its telemetry after the drop, so the /run directory must be its own.
DROP_USER = "daemon"
RUN_DIR_PREFIX = "/run/rgb-matrix-check-"
EVIDENCE_ROOT = "~/rgb-matrix-evidence"
HEALTHZ_URL = "http://127.0.0.1:8080/healthz"
HEALTHZ_WAIT_SECONDS = 30.0
# Startup, audio open, and the telemetry write on top of settle plus duration.
CHILD_MARGIN_SECONDS = 90.0
# Throttle bits 0-3 are current conditions; bits 16-19 are sticky since boot.
THROTTLE_CURRENT_MASK = 0xF
THROTTLE_STICKY_MASK = 0xF0000

# Step 4 matrix-process measurement, v2 report-only (measurement-plan-v2.md; the
# unchanged parts of measurement-plan.md, A-1 to A-3 and A-6 to A-8).
EXPERIMENT_ENV = "RGBMATRIX_FFT_MATRIX_PROCESS_EXPERIMENT"
OUTPUT_PATH_ENV = "RGBMATRIX_FFT_OUTPUT_PATH"
MEASURE_EVIDENCE_PREFIX = "fft-step4-matrix-process-v2-"
# (label, topology, backend), in the approved symmetric order.
MEASURE_SEQUENCE = (
    ("baseline-direct-1", "baseline", "direct"),
    ("candidate-direct-1", "candidate", "direct"),
    ("candidate-pillow-1", "candidate", "pillow-bulk"),
    ("baseline-pillow-1", "baseline", "pillow-bulk"),
    ("baseline-pillow-2", "baseline", "pillow-bulk"),
    ("candidate-pillow-2", "candidate", "pillow-bulk"),
    ("candidate-direct-2", "candidate", "direct"),
    ("baseline-direct-2", "baseline", "direct"),
)
MAX_SKIPPED_FRACTION = 0.05
SAMPLER_INTERVAL_SECONDS = 1.0
SAMPLER_MARGIN_SECONDS = 30.0
SAMPLER_EXIT_GRACE_SECONDS = 10.0
# The resource tracker exits only after it reads EOF from its departed parent.
RESIDUAL_GRACE_SECONDS = 5.0
# Gates that judge the harness rather than the candidate: a run that fails one
# measured the wrong thing, so the sequence stops and is incomplete (repeat it).
HARNESS_GATES = ("sampler", "exact_commit", "topology_env")
# v2 (D-011a): only these stop the sequence. Every other failed gate is a flag on
# the run's row, and the sequence continues; there is no fixed accept/reject rule.
STOP_GATES = (*HARNESS_GATES, "no_residual_process")
# At least this fraction of the expected one-per-second samples inside the window.
MIN_SAMPLER_COVERAGE = 0.9
APP_ROLES = ("parent", "renderer", "resource_tracker")
# Report-only summaries (measurement-plan-v2.md "Report").
WINDOW_SECONDS = 60.0
DENSITY_NOTE_DIFFERENCE = 0.15

# Run as root (one ``sudo -n`` per run) under the Pi's system Python. Arguments:
# root PID, interval seconds, deadline seconds. Writes one JSON header line and then
# one JSON line per sample to stdout; exits when the root PID is gone or a zombie.
SAMPLER_SNIPPET = r"""
import json, os, signal, sys, time
root = int(sys.argv[1]); interval = float(sys.argv[2])
deadline = time.monotonic() + float(sys.argv[3])
stopping = []
signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
signal.signal(signal.SIGINT, lambda *_: stopping.append(1))
def emit(value):
    sys.stdout.write(json.dumps(value, sort_keys=True) + "\n"); sys.stdout.flush()
def stat(pid):
    with open("/proc/%d/stat" % pid, "rb") as f:
        data = f.read().decode("utf-8", "replace")
    fields = data[data.rindex(")") + 2:].split()
    return {"state": fields[0], "ppid": int(fields[1]), "pgrp": int(fields[2]),
            "session": int(fields[3]), "utime": int(fields[11]),
            "stime": int(fields[12]), "start": int(fields[19])}
def kib(path, keys):
    found = {}
    try:
        with open(path, "rb") as f:
            for line in f.read().decode("utf-8", "replace").splitlines():
                name, _, rest = line.partition(":")
                if name in keys:
                    found[keys[name]] = int(rest.split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return found
emit({"header": {"clk_tck": os.sysconf("SC_CLK_TCK"), "root_pid": root,
                 "interval": interval, "sampler_pid": os.getpid(),
                 "euid": os.geteuid()}})
while not stopping:
    began = time.monotonic()
    table = {}
    for name in os.listdir("/proc"):
        if name.isdigit():
            try:
                table[int(name)] = stat(int(name))
            except (OSError, ValueError, IndexError):
                pass
    children = {}
    for pid, info in table.items():
        children.setdefault(info["ppid"], []).append(pid)
    tree, stack = [], ([root] if root in table else [])
    while stack:
        pid = stack.pop(); tree.append(pid); stack.extend(children.get(pid, ()))
    rows = []
    for pid in sorted(tree):
        row = dict(table[pid], pid=pid)
        try:
            with open("/proc/%d/cmdline" % pid, "rb") as f:
                row["cmd"] = [a.decode("utf-8", "replace")[:512]
                              for a in f.read(8192).split(b"\0") if a]
        except OSError:
            row["cmd"] = []
        row.update(kib("/proc/%d/status" % pid, {"VmRSS": "rss_kb", "VmHWM": "hwm_kb"}))
        row.update(kib("/proc/%d/smaps_rollup" % pid, {"Pss": "pss_kb"}))
        rows.append(row)
    emit({"t": time.time(), "m": time.monotonic(), "procs": rows})
    if root not in table or table[root]["state"] in ("Z", "X"):
        break
    if time.monotonic() >= deadline:
        emit({"deadline": True}); break
    time.sleep(max(0.0, interval - (time.monotonic() - began)))
"""

# Printed as JSON by the checkout's own controller code, run with the service's
# interpreter, so the check launches exactly what the web service would (D-003).
FFT_ARGV_SNIPPET = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, ".")
from matrix_controller.controller_config import load_controller_config
from matrix_controller.registry import build_default_registry
config = load_controller_config(Path("config/matrix-controller.json"))
prefix = ("sudo", "-n", "-E") if config.use_sudo else ()
python = config.python_executable or sys.executable
spec = build_default_registry(
    parameter_defaults={"fft": config.defaults_for("fft")}
).get("fft")
print(json.dumps({
    "argv": spec.build_argv(python, {}, command_prefix=prefix),
    "cwd": str(spec.working_directory),
    "python": python,
}))
"""

VERSIONS_SNIPPET = r"""
import json
versions = {}
for module, name in (("numpy", "numpy"), ("PIL", "Pillow")):
    try:
        versions[name] = __import__(module).__version__
    except Exception as exc:
        versions[name] = None
print(json.dumps(versions))
"""

class Refused(Exception):
    """A precondition failed before anything on the Pi changed."""


class CheckAborted(Exception):
    """SIGHUP or SIGTERM arrived during a check."""


class Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class Reporter:
    """Progress to stdout and, once set, a log file. Survives a dropped SSH session."""

    def __init__(self, stream: Any = None) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.log_path: Optional[Path] = None
        self.stdout_broken = False

    def line(self, message: str) -> None:
        if self.log_path is not None:
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(message + "\n")
        if self.stdout_broken:
            return
        try:
            self.stream.write(message + "\n")
            self.stream.flush()
        except (OSError, ValueError):
            # The Mac side went away (Ctrl+C or a dropped connection). The check
            # stops starting new runs, but still restarts the service (D-008).
            self.stdout_broken = True

    def result(self, payload: dict[str, Any]) -> None:
        self.line(RESULT_MARKER + json.dumps(payload, sort_keys=True))


class PiHost:
    """The only seam between this script and the Pi. Tests replace it."""

    # Wait after SIGINT, SIGTERM, and SIGKILL to an overrunning FFT child's group.
    STOP_GRACE_SECONDS = (15.0, 10.0, 10.0)

    def __init__(self, checkout: Path) -> None:
        self.checkout = checkout
        self.user = getpass.getuser()
        self.unit = f"rgb-matrix-web@{self.user}.service"

    # Generic commands -------------------------------------------------------------

    def run(self, argv: Sequence[str], *, cwd: Optional[Path] = None,
            timeout: float = 60.0) -> Result:
        try:
            completed = subprocess.run(
                list(argv), cwd=cwd, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=timeout,
            )
        except FileNotFoundError as exc:
            return Result(127, "", str(exc))
        except subprocess.TimeoutExpired:
            return Result(124, "", f"timed out after {timeout:g} s")
        return Result(completed.returncode, completed.stdout, completed.stderr)

    def git(self, *args: str) -> Result:
        return self.run(["git", "-C", str(self.checkout), *args])

    def healthz(self) -> tuple[bool, str]:
        try:
            with urllib.request.urlopen(HEALTHZ_URL, timeout=3) as response:
                body = response.read(512).decode("utf-8", "replace").strip()
                return response.status == 200 and '"ok"' in body, body
        except (urllib.error.URLError, OSError) as exc:
            return False, str(exc)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def clock(self) -> float:
        return time.monotonic()

    def utc_stamp(self) -> str:
        return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    def evidence_root(self) -> Path:
        return Path(EVIDENCE_ROOT).expanduser()

    # State changes ----------------------------------------------------------------

    def stop_service(self) -> Result:
        return self.run(["sudo", "-n", "systemctl", "stop", self.unit], timeout=60)

    def start_service(self) -> Result:
        return self.run(["sudo", "-n", "systemctl", "start", self.unit], timeout=60)

    def make_run_dir(self, run_dir: Path) -> Result:
        return self.run([
            "sudo", "-n", "install", "-d", "-m", "0755",
            "-o", DROP_USER, "-g", DROP_USER, str(run_dir), str(run_dir / "state"),
        ])

    def collect_run_dir(self, run_dir: Path, evidence_dir: Path) -> Result:
        """Copy the run's .json and .npz files into the evidence directory."""

        names = [
            entry.name for entry in sorted(run_dir.iterdir())
            if entry.is_file() and entry.suffix in (".json", ".npz")
        ] if run_dir.is_dir() else []
        if not names:
            return Result(0)
        copied = self.run([
            "sudo", "-n", "cp", "-p", "--",
            *[str(run_dir / name) for name in names], str(evidence_dir),
        ])
        if copied.returncode != 0:
            return copied
        return self.run([
            "sudo", "-n", "chown", f"{self.user}:",
            *[str(evidence_dir / name) for name in names],
        ])

    def remove_run_dir(self, run_dir: Path) -> Result:
        if not str(run_dir).startswith(RUN_DIR_PREFIX):
            raise ValueError(f"refusing to remove {run_dir}")
        return self.run(["sudo", "-n", "rm", "-rf", "--", str(run_dir)])

    def run_foreground(self, argv: Sequence[str], *, cwd: Path, env: dict[str, str],
                       timeout: float, log_path: Path) -> dict[str, Any]:
        """Run one bounded FFT child, stopping it on timeout or abort."""

        child_env = dict(os.environ)
        for key in tuple(child_env):
            if key.startswith("RGBMATRIX_FFT_STEP6_"):
                del child_env[key]
        child_env.update(env)
        with log_path.open("w", encoding="utf-8") as log:
            # Own process group, as the supervisor does, so a stop reaches sudo and
            # the FFT process behind it together.
            child = subprocess.Popen(
                list(argv), cwd=cwd, env=child_env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
            timed_out = False
            escalated = False
            try:
                child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                if child.poll() is None:
                    # SIGINT first: the FFT process clears the panel and writes what
                    # it has. The whole group is signalled, never sudo alone.
                    escalated = self._stop_child_group(child)
            return {
                "returncode": child.returncode,
                "timed_out": timed_out,
                "kill_escalated": escalated,
            }


    def _signal_group(self, pgid: int, sig: signal.Signals) -> None:
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Members that already dropped privileges may refuse the user's signal.
            self.run(["sudo", "-n", "kill", "-s", sig.name[3:], "--", f"-{pgid}"],
                     timeout=10)

    def _stop_child_group(self, child: subprocess.Popen) -> bool:
        """SIGINT, then SIGTERM, then SIGKILL to the child's group. True if killed."""

        pgid = child.pid  # start_new_session makes the child its group leader
        for sig, grace in zip((signal.SIGINT, signal.SIGTERM, signal.SIGKILL),
                              self.STOP_GRACE_SECONDS):
            self._signal_group(pgid, sig)
            try:
                child.wait(timeout=grace)
                return sig == signal.SIGKILL
            except subprocess.TimeoutExpired:
                continue
        return True

    def fft_processes(self) -> list[str]:
        """FFT processes still alive. The service must not start while one is."""

        found = self.run(["pgrep", "-af", "fft_program/main.py"])
        return found.stdout.splitlines() if found.returncode == 0 else []

    # Matrix-process measurement (Step 4) -------------------------------------------

    def run_measured(self, argv: Sequence[str], *, cwd: Path, env: dict[str, str],
                     timeout: float, log_path: Path, samples_path: Path,
                     sampler_log_path: Path) -> dict[str, Any]:
        """Run one bounded FFT child with the root ``/proc`` sampler beside it.

        The sampler is one ``sudo -n`` process per run (A-3). It follows the
        descendant tree of the launched ``sudo`` PID (A-2) and exits by itself when
        that PID is gone; it is stopped explicitly only if it outlives the child.
        """

        child_env = measured_environment(os.environ, env)
        with log_path.open("w", encoding="utf-8") as log, \
                samples_path.open("w", encoding="utf-8") as samples, \
                sampler_log_path.open("w", encoding="utf-8") as sampler_log:
            child: Optional[subprocess.Popen] = None
            sampler: Optional[subprocess.Popen] = None
            timed_out = False
            escalated = False
            try:
                child = subprocess.Popen(
                    list(argv), cwd=cwd, env=child_env, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                )
                sampler = subprocess.Popen(
                    sampler_argv(child.pid, timeout + SAMPLER_MARGIN_SECONDS),
                    stdin=subprocess.DEVNULL, stdout=samples, stderr=sampler_log,
                )
                child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                if child is not None and child.poll() is None:
                    escalated = self._stop_child_group(child)
                sampler_rc = self._finish_sampler(sampler)
            return {
                "returncode": None if child is None else child.returncode,
                "timed_out": timed_out,
                "kill_escalated": escalated,
                "sampler_returncode": sampler_rc,
            }

    def _finish_sampler(self, sampler: Optional[subprocess.Popen]) -> Optional[int]:
        if sampler is None:
            return None
        for sig in (None, "TERM", "KILL"):
            if sig is not None:
                # The sampler runs as root behind sudo; sudo relays the signal.
                self.run(["sudo", "-n", "kill", "-s", sig, "--", str(sampler.pid)],
                         timeout=10)
            try:
                return sampler.wait(timeout=SAMPLER_EXIT_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                continue
        return sampler.poll()

    def alive_processes(self, processes: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
        """Processes from a run's tree that still exist (same PID and start time)."""

        alive = []
        for pid, start in processes:
            try:
                info = parse_proc_stat(Path(f"/proc/{pid}/stat").read_text())
            except (OSError, ValueError, IndexError):
                continue
            if info["start"] == start and info["state"] not in ("Z", "X"):
                alive.append((pid, start))
        return alive

    def surviving(self, processes: Sequence[tuple[int, int]]) -> list[str]:
        return [f"pid {pid}" for pid, _ in self.alive_processes(processes)]

    def stop_survivors(self, processes: Sequence[tuple[int, int]]) -> list[str]:
        """Allow a short exit grace, then INT/TERM/KILL any survivor by verified PID.

        Returns what was still alive after the grace (a lifecycle failure even if
        the signals then remove it). The caller re-checks before starting the
        service.
        """

        deadline = self.clock() + RESIDUAL_GRACE_SECONDS
        while self.alive_processes(processes) and self.clock() < deadline:
            self.sleep(0.25)
        lingering = self.surviving(processes)
        for sig in ("INT", "TERM", "KILL"):
            alive = self.alive_processes(processes)
            if not alive:
                break
            for pid, _ in alive:
                self.run(["sudo", "-n", "kill", "-s", sig, "--", str(pid)], timeout=10)
            stop_by = self.clock() + RESIDUAL_GRACE_SECONDS
            while self.alive_processes(processes) and self.clock() < stop_by:
                self.sleep(0.25)
        return lingering

    def multiprocessing_processes(self, python: Optional[str]) -> list[str]:
        """FFT-venv multiprocessing helpers that ``fft_processes`` cannot see (A-1)."""

        found = self.run(["pgrep", "-af", "multiprocessing"])
        if found.returncode != 0:
            return []
        return [
            line for line in found.stdout.splitlines()
            if python and python in line
        ]


# Status ---------------------------------------------------------------------------


def _parse_pins(text: str) -> dict[str, str]:
    pins = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, version = line.split("==", 1)
            pins[name.strip()] = version.strip()
    return pins


def _parse_throttled(text: str) -> Optional[int]:
    # "throttled=0x50000"
    value = text.strip().rpartition("=")[2]
    try:
        return int(value, 16)
    except ValueError:
        return None


def _config_python(checkout: Path) -> Optional[str]:
    try:
        config = json.loads((checkout / "config" / "matrix-controller.json").read_text())
        python = config.get("runtime", {}).get("python_executable")
    except (OSError, ValueError, AttributeError):
        return None
    return str(Path(python).expanduser()) if isinstance(python, str) and python else None


def collect_status(host: PiHost) -> dict[str, Any]:
    """Read-only snapshot: no sudo, no writes. tests/test_pi_cycle.py pins the commands."""

    head = host.git("rev-parse", "HEAD")
    symbolic = host.git("symbolic-ref", "-q", "HEAD")
    porcelain = host.git("status", "--porcelain")
    active = host.run(["systemctl", "is-active", host.unit])
    temp = host.run(["vcgencmd", "measure_temp"])
    throttled = host.run(["vcgencmd", "get_throttled"])
    system_info = host.run(["pgrep", "-af", "system_info.py"])
    healthy, health_body = host.healthz()

    pins_path = host.checkout / "requirements-test.txt"
    try:
        pins = _parse_pins(pins_path.read_text(encoding="utf-8"))
    except OSError:
        pins = {}
    python = _config_python(host.checkout)
    installed: dict[str, Optional[str]] = {}
    if python is not None:
        probe = host.run([python, "-c", VERSIONS_SNIPPET])
        if probe.returncode == 0:
            try:
                installed = json.loads(probe.stdout)
            except ValueError:
                installed = {}
    drift = sorted(
        name for name, version in pins.items() if installed.get(name) != version
    )

    return {
        "unit": host.unit,
        "service_active": active.stdout.strip() or "unknown",
        "head": head.stdout.strip() if head.returncode == 0 else None,
        "detached": symbolic.returncode != 0,
        "branch": symbolic.stdout.strip() or None,
        "porcelain": porcelain.stdout.splitlines() if porcelain.returncode == 0 else None,
        "temperature": temp.stdout.strip() or None,
        "throttled": _parse_throttled(throttled.stdout),
        "healthz": {"ok": healthy, "body": health_body},
        "system_info_processes": system_info.stdout.splitlines(),
        "fft_python": python,
        "packages": {"pinned": pins, "installed": installed, "drift": drift},
    }


def _status_lines(status: dict[str, Any]) -> list[str]:
    throttled = status["throttled"]
    packages = status["packages"]
    porcelain = status["porcelain"]
    return [
        f"service   {status['unit']}: {status['service_active']}",
        f"healthz   {'ok' if status['healthz']['ok'] else 'FAIL'} ({status['healthz']['body']})",
        f"checkout  {status['head']} "
        + ("(detached)" if status["detached"] else f"(on {status['branch']})"),
        "tree      " + ("clean" if porcelain == [] else f"DIRTY: {porcelain}"),
        f"temp      {status['temperature']}",
        "throttled " + ("unknown" if throttled is None else hex(throttled)),
        f"system_info {len(status['system_info_processes'])} process(es)",
        "packages  " + (
            "match pins" if not packages["drift"]
            else "DRIFT " + ", ".join(
                f"{name} pinned {packages['pinned'][name]} installed "
                f"{packages['installed'].get(name)}"
                for name in packages["drift"]
            )
        ),
    ]


def cmd_status(host: PiHost, report: Reporter) -> int:
    status = collect_status(host)
    for line in _status_lines(status):
        report.line(line)
    report.result({"command": "status", "status": status})
    return EXIT_OK


# Checkout (deploy and restore) -----------------------------------------------------


def _resolve(host: PiHost, ref: str) -> str:
    resolved = host.git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if resolved.returncode != 0 or not resolved.stdout.strip():
        raise Refused(f"{ref!r} is not a commit in the Pi checkout")
    return resolved.stdout.strip()


def wait_for_healthz(host: PiHost, wait_seconds: float = HEALTHZ_WAIT_SECONDS) -> tuple[bool, str]:
    deadline = host.clock() + wait_seconds
    while True:
        healthy, body = host.healthz()
        if healthy or host.clock() >= deadline:
            return healthy, body
        host.sleep(1.0)


def cmd_checkout(host: PiHost, report: Reporter, ref: str,
                 expect_sha: Optional[str]) -> int:
    payload: dict[str, Any] = {"command": "checkout", "ref": ref}
    try:
        before = collect_status(host)
        payload["status_before"] = before
        if before["porcelain"] != []:
            raise Refused(f"Pi checkout is not clean: {before['porcelain']}")
        target = _resolve(host, ref)
        if expect_sha is not None and target != expect_sha:
            raise Refused(f"{ref!r} resolves to {target} on the Pi, expected {expect_sha}")
    except Refused as exc:
        payload["refused"] = str(exc)
        report.line(f"refused: {exc}")
        report.result(payload)
        return EXIT_REFUSED

    previous = before["head"]
    payload.update({"previous": previous, "target": target, "changed": False})
    report.line(f"previous  {previous}")
    report.line(f"target    {target}")

    errors: list[str] = []
    at_target = previous == target and before["detached"]
    if at_target and before["service_active"] == "active":
        report.line("already at target and running; not restarting")
    else:
        # Stop, checkout, and start can't be interrupted by SIGHUP/SIGTERM (a dropped
        # session), so the service is never left stopped between them.
        shielded = _set_stop_signals(signal.SIG_IGN)
        try:
            stopped = host.stop_service()
            if stopped.returncode != 0:
                errors.append(f"service stop failed: {stopped.stderr.strip()}")
            elif not at_target:
                checked_out = host.git("checkout", "--detach", "--quiet", target)
                if checked_out.returncode != 0:
                    errors.append(f"checkout failed: {checked_out.stderr.strip()}")
                else:
                    payload["changed"] = True
            # Start whenever a stop was attempted: a stop that timed out may still
            # have taken the service down.
            started = host.start_service()
            if started.returncode != 0:
                errors.append(f"service start failed: {started.stderr.strip()}")
        finally:
            _restore_signals(shielded)

    healthy, body = wait_for_healthz(host)
    after = collect_status(host)
    payload.update({
        "status_after": after,
        "current": after["head"],
        "healthy": healthy,
        "healthz_body": body,
        "errors": errors,
    })
    ok = healthy and not errors and after["head"] == target and after["porcelain"] == []
    report.line(f"current   {after['head']}")
    report.line(f"healthz   {'ok' if healthy else 'FAIL'} ({body})")
    for error in errors:
        report.line(f"error: {error}")
    if after["porcelain"] != []:
        report.line(f"error: tree not clean after checkout: {after['porcelain']}")
    report.result(payload)
    return EXIT_OK if ok else EXIT_OPERATION_FAILED


# Check ----------------------------------------------------------------------------


def run_labels(modes: Sequence[str]) -> list[tuple[str, str]]:
    """``direct,pillow-bulk,direct`` -> direct-1, pillow-bulk-1, direct-2."""

    counts: dict[str, int] = {}
    labels = []
    for mode in modes:
        counts[mode] = counts.get(mode, 0) + 1
        labels.append((f"{mode}-{counts[mode]}", mode))
    return labels


def planned_runs(
    modes: Sequence[str],
    duration: float,
    run_plan: Optional[Sequence[tuple[str, str, float]]],
) -> list[tuple[str, str, Optional[str], float]]:
    """Return unique label, mode, optional source override, and duration."""

    if run_plan is None:
        return [(label, mode, None, duration) for label, mode in run_labels(modes)]
    counts: dict[str, int] = {}
    runs = []
    for source, mode, run_duration in run_plan:
        base = f"{source}-{mode}"
        counts[base] = counts.get(base, 0) + 1
        suffix = f"-{counts[base]}" if counts[base] > 1 else ""
        runs.append((f"{base}{suffix}", mode, source, run_duration))
    return runs


def _throttle_gate(value: Optional[int], baseline: Optional[int]) -> tuple[bool, str]:
    if value is None:
        return False, "throttle state unknown"
    if value & THROTTLE_CURRENT_MASK:
        return False, f"throttled now ({hex(value)})"
    new_sticky = (value & THROTTLE_STICKY_MASK) & ~((baseline or 0) & THROTTLE_STICKY_MASK)
    if new_sticky:
        return False, f"throttled during the check ({hex(value)})"
    return True, hex(value)


def evaluate_run(run: dict[str, Any], summary: Optional[dict[str, Any]],
                 throttle_baseline: Optional[int]) -> dict[str, Any]:
    """Apply the Step 3 gates to one run. Never raises on a bad summary."""

    gates: dict[str, bool] = {}
    notes: list[str] = []
    gates["exit_zero"] = (
        run.get("returncode") == 0
        and not run.get("timed_out")
        and not run.get("kill_escalated")
    )
    if run.get("timed_out"):
        notes.append("timed out and was stopped")
    if run.get("kill_escalated"):
        notes.append("needed SIGKILL to stop")

    def get(path: str, default: Any = None) -> Any:
        node: Any = summary
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def number(path: str) -> Optional[float]:
        value = get(path)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) if math.isfinite(value) else None

    if not isinstance(summary, dict):
        notes.append("summary missing or unreadable")
    gates["schema"] = (
        get("schema") == TELEMETRY_SCHEMA and get("schema_version") == TELEMETRY_SCHEMA_VERSION
    )
    gates["full_duration"] = (
        get("requested_duration_complete") is True
        and get("termination_reason") == "duration_complete"
        and get("valid") is True
    )
    frames = number("sample_count")
    gates["frames"] = frames is not None and frames > 0
    sequences = number("audio_counter_deltas.analysis_sequence")
    gates["analyzer_sequences"] = sequences is not None and sequences > 0
    fraction = number("analyzed_published_fraction")
    gates["analyzed_published"] = (
        fraction is not None and fraction >= MIN_ANALYZED_PUBLISHED_FRACTION
    )
    sample_rate = number("audio.sample_rate")
    block_size = number("audio.block_size")
    limit_ms = (
        RESULT_AGE_LIMIT_BLOCKS * block_size / sample_rate * 1000.0
        if sample_rate and block_size else None
    )
    p95 = number("latency.result_age.p95_ms")
    gates["result_age_p95"] = limit_ms is not None and p95 is not None and p95 < limit_ms
    callback = number("audio_counter_deltas.callback_status_count")
    worker = number("audio_counter_deltas.worker_failure_count")
    gates["no_callback_failures"] = callback == 0
    gates["no_worker_failures"] = worker == 0
    gates["capacity_intact"] = get("capacity_exhausted") is False
    throttle_ok, throttle_text = _throttle_gate(run.get("throttled"), throttle_baseline)
    gates["no_throttling"] = throttle_ok

    return {
        "label": run["label"],
        "mode": run["mode"],
        "source": run.get("source"),
        "duration": run.get("duration"),
        "returncode": run.get("returncode"),
        "skipped": run.get("skipped", False),
        "valid": get("valid"),
        "frames": frames,
        "swap_fps": number("rates.swap_fps"),
        "unique_fps": number("rates.unique_content_fps"),
        "analyzed_published_fraction": fraction,
        "result_age_p95_ms": p95,
        "result_age_limit_ms": limit_ms,
        "callback_failures": callback,
        "worker_failures": worker,
        "throttled": throttle_text,
        "gates": gates,
        "failed_gates": [name for name, passed in gates.items() if not passed],
        "notes": notes + ([run["error"]] if run.get("error") else []),
    }


def _fmt(value: Any, spec: str = "", suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and spec:
        return format(value, spec) + suffix
    return f"{value}{suffix}"


def results_markdown(context: dict[str, Any], evaluations: list[dict[str, Any]]) -> str:
    duration_detail = context.get("duration_detail", f"{context['duration']:g} s each")
    lines = [
        f"# pi-check {context['sha'][:7]}",
        "",
        f"- Source: `{context['sha']}` (Pi HEAD, clean tree)",
        f"- Started: {context['stamp']} · settle {context['settle']:g} s · "
        f"duration {duration_detail} · max samples {context['max_samples']}",
        f"- Evidence: `{context['evidence_dir']}`",
        f"- Throttle before: {_fmt(None if context['throttle_before'] is None else hex(context['throttle_before']))}"
        " (sticky bits already set can't show a repeat event)",
        f"- Service after: {'healthy' if context['healthy'] else 'UNHEALTHY'}",
        "",
        "| Run | Mode | Valid | Frames | Swap FPS | Unique FPS | Analyzed/published "
        "| Result-age p95 (limit) | Callback/worker failures | Throttled | Failed gates |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for item in evaluations:
        frames = item["frames"]
        lines.append(
            "| {label} | {mode} | {valid} | {frames} | {swap} | {unique} | {fraction} "
            "| {p95} ({limit}) | {callback} / {worker} | {throttled} | {failed} |".format(
                label=item["label"],
                mode=item["mode"],
                valid="skipped" if item["skipped"] else ("yes" if item["valid"] else "no"),
                frames=_fmt(int(frames) if frames is not None else None),
                swap=_fmt(item["swap_fps"], ".3f"),
                unique=_fmt(item["unique_fps"], ".3f"),
                fraction=_fmt(
                    None if item["analyzed_published_fraction"] is None
                    else item["analyzed_published_fraction"] * 100, ".2f", "%",
                ),
                p95=_fmt(item["result_age_p95_ms"], ".3f", " ms"),
                limit=_fmt(item["result_age_limit_ms"], ".2f", " ms"),
                callback=_fmt(None if item["callback_failures"] is None
                              else int(item["callback_failures"])),
                worker=_fmt(None if item["worker_failures"] is None
                            else int(item["worker_failures"])),
                throttled=item["throttled"],
                failed=", ".join(item["failed_gates"]) or "none",
            )
        )
    notes = [
        f"- {item['label']}: {note}" for item in evaluations for note in item["notes"]
    ] + [f"- {error}" for error in context["errors"]]
    if notes:
        lines += ["", "Notes:", *notes]
    verdict = "PASS" if context["passed"] else "FAIL"
    lines += ["", f"Result: **{verdict}**", ""]
    return "\n".join(lines)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_manifest(evidence_dir: Path) -> None:
    lines = []
    for path in sorted(evidence_dir.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            lines.append(f"{digest}  {path.name}")
    (evidence_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _raise_aborted(signum: int, _frame: Any) -> None:
    raise CheckAborted(f"signal {signal.Signals(signum).name}")


def _raise_aborted_once(signum: int, _frame: Any) -> None:
    """Measurement handler: later stop signals can't interrupt the cleanup."""

    _set_stop_signals(signal.SIG_IGN)
    raise CheckAborted(f"signal {signal.Signals(signum).name}")


class _Deferred:
    """Hold SIGHUP/SIGTERM across a step that must not be cut short (a checkout)."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.pending: list[int] = []
        self.previous: dict[int, Any] = {}

    def __enter__(self) -> "_Deferred":
        if self.enabled:
            self.previous = _set_stop_signals(lambda signum, _f: self.pending.append(signum))
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.enabled:
            _restore_signals(self.previous)
            if self.pending and exc[0] is None:
                _raise_aborted_once(self.pending[0], None)


_STOP_SIGNALS = (signal.SIGHUP, signal.SIGTERM)


def _set_stop_signals(handler: Any) -> dict[int, Any]:
    return {sig: signal.signal(sig, handler) for sig in _STOP_SIGNALS}


def _restore_signals(previous: dict[int, Any]) -> None:
    for sig, handler in previous.items():
        signal.signal(sig, handler)


def parse_run_plan(text: str) -> list[tuple[str, str, float]]:
    runs = []
    for entry in (item for item in text.split(",") if item):
        parts = entry.split(":")
        if len(parts) != 3:
            raise argparse.ArgumentTypeError(
                "run plan entries must be source:mode:seconds"
            )
        source, mode, duration_text = parts
        try:
            run_duration = float(duration_text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("invalid run plan duration") from exc
        runs.append((source, mode, run_duration))
    return runs


def _validate_check_args(modes: Sequence[str], settle: float, duration: float,
                         max_samples: int,
                         run_plan: Optional[Sequence[tuple[str, str, float]]] = None) -> None:
    if not modes:
        raise Refused("no modes given")
    unknown = sorted(set(modes) - set(OUTPUT_MODES))
    if unknown:
        raise Refused(f"unknown modes: {', '.join(unknown)}")
    if not (math.isfinite(settle) and settle >= 0):
        raise Refused("settle must be finite and >= 0")
    if not (math.isfinite(duration) and duration > 0):
        raise Refused("duration must be finite and > 0")
    if max_samples <= 0:
        raise Refused("max samples must be > 0")
    if run_plan is not None:
        if not run_plan:
            raise Refused("run plan must contain at least one run")
        for source, mode, run_duration in run_plan:
            if source not in AUDIO_SOURCES:
                raise Refused(f"unknown audio source: {source}")
            if mode not in OUTPUT_MODES:
                raise Refused(f"unknown mode: {mode}")
            if not (math.isfinite(run_duration) and run_duration > 0):
                raise Refused("run duration must be finite and > 0")


def _with_audio_source(argv: Sequence[str], source: str) -> list[str]:
    """Replace the controller-generated public source value without a shell."""

    updated = list(argv)
    try:
        index = updated.index("--audio-source")
        updated[index + 1] = source
    except (ValueError, IndexError) as exc:
        raise Refused("controller FFT argv has no --audio-source value") from exc
    return updated


def fft_launch(host: PiHost) -> dict[str, Any]:
    built = host.run(["/usr/bin/python3", "-c", FFT_ARGV_SNIPPET], cwd=host.checkout)
    if built.returncode != 0:
        raise Refused(f"controller could not build the FFT argv: {built.stderr.strip()}")
    try:
        launch = json.loads(built.stdout)
        argv = [str(item) for item in launch["argv"]]
        cwd = str(launch["cwd"])
    except (ValueError, KeyError, TypeError) as exc:
        raise Refused(f"unexpected FFT argv output: {exc}") from exc
    return {"argv": argv, "cwd": cwd, "python": launch.get("python")}


def cmd_check(host: PiHost, report: Reporter, *, sha: str, modes: Sequence[str],
              settle: float, duration: float, max_samples: int,
              run_plan: Optional[Sequence[tuple[str, str, float]]] = None,
              install_signals: bool = True) -> int:
    payload: dict[str, Any] = {"command": "check"}
    try:
        _validate_check_args(modes, settle, duration, max_samples, run_plan)
        before = collect_status(host)
        payload["status_before"] = before
        if before["porcelain"] != []:
            raise Refused(f"Pi checkout is not clean: {before['porcelain']}")
        target = _resolve(host, sha)
        if not before["detached"] or before["head"] != target:
            raise Refused(
                f"Pi is at {before['head']} ({'detached' if before['detached'] else 'on a branch'}),"
                f" not detached at {target}; run deploy first"
            )
        launch = fft_launch(host)
        runs_to_execute = planned_runs(modes, duration, run_plan)
        if run_plan is not None:
            _with_audio_source(launch["argv"], run_plan[0][0])
    except Refused as exc:
        payload["refused"] = str(exc)
        report.line(f"refused: {exc}")
        report.result(payload)
        return EXIT_REFUSED

    stamp = host.utc_stamp()
    run_dir = Path(f"{RUN_DIR_PREFIX}{stamp}")
    evidence_dir = host.evidence_root() / f"{stamp}-{target[:7]}"
    try:
        evidence_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        payload["refused"] = f"{evidence_dir} already exists; retry in a second"
        report.line(f"refused: {payload['refused']}")
        report.result(payload)
        return EXIT_REFUSED
    report.log_path = evidence_dir / "check.log"
    report.line(f"source    {target}")
    report.line(f"evidence  {evidence_dir}")

    commands: dict[str, dict[str, Any]] = {}

    def write_commands() -> None:
        _write_json(evidence_dir / "argv.json", {
            "controller": launch,
            "runs": {label: item["argv"] for label, item in commands.items()},
        })
        _write_json(evidence_dir / "env.json",
                    {label: item["env"] for label, item in commands.items()})

    write_commands()
    _write_json(evidence_dir / "status-before.json", before)

    runs: list[dict[str, Any]] = []
    errors: list[str] = []
    previous_handlers = _set_stop_signals(_raise_aborted) if install_signals else {}
    try:
        try:
            made = host.make_run_dir(run_dir)
            if made.returncode != 0:
                raise RuntimeError(f"could not create {run_dir}: {made.stderr.strip()}")
            stopped = host.stop_service()
            if stopped.returncode != 0:
                raise RuntimeError(f"service stop failed: {stopped.stderr.strip()}")
            report.line(f"stopped   {host.unit}")
            for label, mode, source, run_duration in runs_to_execute:
                if report.stdout_broken:
                    runs.append({"label": label, "mode": mode, "source": source,
                                 "duration": run_duration, "skipped": True,
                                 "error": "skipped: the Mac side disconnected"})
                    continue
                base_argv = (
                    launch["argv"]
                    if source is None
                    else _with_audio_source(launch["argv"], source)
                )
                argv = [*base_argv,
                        "--telemetry-out", str(run_dir / f"{label}.json"),
                        "--telemetry-label", label,
                        "--telemetry-source-id", target,
                        "--telemetry-settle", f"{settle:g}",
                        "--telemetry-duration", f"{run_duration:g}",
                        "--telemetry-max-samples", str(max_samples)]
                env = {"RGBMATRIX_FFT_OUTPUT_PATH": mode,
                       "RGBMATRIX_STATE_DIR": str(run_dir / "state")}
                commands[label] = {"argv": argv, "env": env}
                write_commands()
                report.line(
                    f"run       {label} ({mode}), about {settle + run_duration:g} s"
                )
                record: dict[str, Any] = {
                    "label": label,
                    "mode": mode,
                    "source": source,
                    "duration": run_duration,
                }
                runs.append(record)
                record.update(host.run_foreground(
                    argv, cwd=Path(launch["cwd"]), env=env,
                    timeout=settle + run_duration + CHILD_MARGIN_SECONDS,
                    log_path=evidence_dir / f"{label}.log",
                ))
                record["throttled"] = _parse_throttled(
                    host.run(["vcgencmd", "get_throttled"]).stdout
                )
                report.line(f"done      {label}: exit {record['returncode']}")
        except (Exception, KeyboardInterrupt) as exc:
            errors.append(f"check stopped early: {type(exc).__name__}: {exc}")
        finally:
            # A second signal must not skip the restart.
            if install_signals:
                _set_stop_signals(signal.SIG_IGN)
            leftovers = host.fft_processes()
            if leftovers:
                errors.append(
                    "service not started: FFT process still running: "
                    + "; ".join(leftovers)
                )
            else:
                started = host.start_service()
                if started.returncode != 0:
                    errors.append(f"service start failed: {started.stderr.strip()}")
            healthy, body = wait_for_healthz(host)
            report.line(f"healthz   {'ok' if healthy else 'FAIL'} ({body})")
            collected = host.collect_run_dir(run_dir, evidence_dir)
            if collected.returncode != 0:
                errors.append(f"evidence copy failed: {collected.stderr.strip()}")
            else:
                removed = host.remove_run_dir(run_dir)
                if removed.returncode != 0:
                    errors.append(f"could not remove {run_dir}: {removed.stderr.strip()}")
    finally:
        _restore_signals(previous_handlers)

    after = collect_status(host)
    _write_json(evidence_dir / "status-after.json", after)
    done = {record["label"] for record in runs}
    for label, mode, source, run_duration in runs_to_execute:
        if label not in done:
            runs.append({"label": label, "mode": mode, "source": source,
                         "duration": run_duration, "skipped": True,
                         "error": "skipped: the check stopped early"})
    order = [label for label, _mode, _source, _duration in runs_to_execute]
    runs.sort(key=lambda record: order.index(record["label"]))

    evaluations = []
    for record in runs:
        summary = None
        if not record.get("skipped"):
            try:
                summary = json.loads(
                    (evidence_dir / f"{record['label']}.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                summary = None
        evaluations.append(evaluate_run(record, summary, before["throttled"]))

    passed = healthy and not errors and all(not item["failed_gates"] for item in evaluations)
    context = {
        "sha": target, "stamp": stamp, "settle": settle, "duration": duration,
        "duration_detail": (
            ", ".join(f"{item[0]}={item[3]:g}s" for item in runs_to_execute)
            if run_plan is not None else f"{duration:g} s each"
        ),
        "max_samples": max_samples, "evidence_dir": str(evidence_dir),
        "healthy": healthy, "errors": errors, "passed": passed,
        "throttle_before": before["throttled"],
    }
    markdown = results_markdown(context, evaluations)
    (evidence_dir / "results.md").write_text(markdown, encoding="utf-8")
    payload.update({
        "sha": target, "evidence_dir": str(evidence_dir), "healthy": healthy,
        "errors": errors, "passed": passed, "runs": evaluations,
        "results_markdown": markdown,
    })
    report.result(payload)
    # The manifest goes last so it covers the finished check.log.
    report.log_path = None
    _write_manifest(evidence_dir)
    if not healthy or any(error.startswith("service ") for error in errors):
        return EXIT_OPERATION_FAILED
    return EXIT_OK if passed else EXIT_GATE_FAILED


# Matrix-process measurement (Step 4) -----------------------------------------------


def measured_environment(inherited: dict[str, str], overrides: dict[str, str]) -> dict[str, str]:
    """The child's environment: topology and backend come only from ``overrides``."""

    child_env = {
        key: value for key, value in inherited.items()
        if not key.startswith("RGBMATRIX_FFT_STEP6_")
        and key not in (EXPERIMENT_ENV, OUTPUT_PATH_ENV)
    }
    child_env.update(overrides)
    return child_env


def sampler_argv(root_pid: int, deadline_seconds: float) -> list[str]:
    return ["sudo", "-n", "/usr/bin/python3", "-c", SAMPLER_SNIPPET, str(root_pid),
            f"{SAMPLER_INTERVAL_SECONDS:g}", f"{deadline_seconds:g}"]


def measure_env(topology: str, mode: str, run_dir: Path) -> dict[str, str]:
    env = {OUTPUT_PATH_ENV: mode, "RGBMATRIX_STATE_DIR": str(run_dir / "state")}
    if topology == "candidate":
        env[EXPERIMENT_ENV] = "1"
    return env


def parse_proc_stat(text: str) -> dict[str, Any]:
    fields = text[text.rindex(")") + 2:].split()
    return {"state": fields[0], "ppid": int(fields[1]), "utime": int(fields[11]),
            "stime": int(fields[12]), "start": int(fields[19])}


def classify_role(cmd: Sequence[str]) -> str:
    """A-2 roles. ``sudo`` is checked first because its argv names main.py too."""

    if not cmd:
        return "unknown"
    if os.path.basename(cmd[0]) == "sudo":
        return "sudo"
    if any("multiprocessing.resource_tracker" in arg for arg in cmd):
        return "resource_tracker"
    if any("multiprocessing.spawn" in arg for arg in cmd):
        return "renderer"
    if any(arg.endswith("fft_program/main.py") for arg in cmd):
        return "parent"
    return "unknown"


def read_npz_uint64(path: Path, name: str) -> list[int]:
    """Read one 1-D little-endian uint64 array from an .npz without NumPy."""

    import array
    import ast
    import zipfile

    with zipfile.ZipFile(path) as archive:
        data = archive.read(f"{name}.npy")
    if data[:6] != b"\x93NUMPY":
        raise ValueError(f"{name}: not an .npy member")
    major = data[6]
    if major == 1:
        length, start = int.from_bytes(data[8:10], "little"), 10
    elif major in (2, 3):
        length, start = int.from_bytes(data[8:12], "little"), 12
    else:
        raise ValueError(f"{name}: unsupported .npy version {major}")
    header = ast.literal_eval(data[start:start + length].decode("latin1"))
    shape = header.get("shape")
    if (header.get("descr") != "<u8" or header.get("fortran_order")
            or not isinstance(shape, tuple) or len(shape) != 1):
        raise ValueError(f"{name}: expected 1-D <u8, got {header}")
    values = array.array("Q")
    if values.itemsize != 8:
        raise ValueError("platform array('Q') is not 8 bytes")
    body = data[start + length:start + length + 8 * shape[0]]
    if len(body) != 8 * shape[0]:
        raise ValueError(f"{name}: truncated")
    values.frombytes(body)
    if sys.byteorder != "little":
        values.byteswap()
    return list(values)


def superseded_count(sequences: Sequence[int]) -> int:
    """A-5: sum of max(0, delta - 1) over consecutive rendered sequences."""

    return sum(max(0, b - a - 1) for a, b in zip(sequences, sequences[1:]))


def window_count(span_seconds: float) -> int:
    """v2: about one window per WINDOW_SECONDS, at least one."""

    return max(1, round(span_seconds / WINDOW_SECONDS))


def frame_windows(completed_ns: Sequence[int], result_age_ns: Sequence[int],
                  sequences: Sequence[int]) -> list[dict[str, Optional[float]]]:
    """v2: equal windows from the first to the last frame completion.

    Swap FPS is frames over the window width; published FPS is the rendered
    sequence advance over the time between the window's first and last frames.
    """

    count = len(completed_ns)
    if count < 2 or len(result_age_ns) != count or len(sequences) != count:
        return []
    start, end = completed_ns[0], completed_ns[-1]
    if end <= start:
        return []
    windows = window_count((end - start) / 1e9)
    width_ns = (end - start) / windows
    buckets: list[list[int]] = [[] for _ in range(windows)]
    for index, stamp in enumerate(completed_ns):
        buckets[min(windows - 1, max(0, int((stamp - start) / width_ns)))].append(index)
    result: list[dict[str, Optional[float]]] = []
    for indices in buckets:
        if not indices:
            result.append({"swap_fps": 0.0, "published_fps": None, "age_p95_ms": None})
            continue
        first, last = indices[0], indices[-1]
        elapsed = (completed_ns[last] - completed_ns[first]) / 1e9
        result.append({
            "swap_fps": len(indices) / (width_ns / 1e9),
            "published_fps": ((sequences[last] - sequences[first]) / elapsed
                              if elapsed > 0 else None),
            "age_p95_ms": _percentile([result_age_ns[i] / 1e6 for i in indices], 0.95),
        })
    return result


def cpu_windows(header: Optional[dict[str, Any]], samples: list[dict[str, Any]],
                role_pids: dict[str, list[int]],
                window: tuple[Optional[float], Optional[float]],
                windows: int) -> list[Optional[float]]:
    """v2: app-role CPU cores in equal windows of the telemetry wall-clock span."""

    start, end = window
    clk = float((header or {}).get("clk_tck") or 0)
    if start is None or end is None or end <= start or windows < 1 or clk <= 0:
        return []
    app = {pid for role in APP_ROLES for pid in role_pids.get(role, [])}
    width = (end - start) / windows
    points: list[dict[int, list[tuple[float, int]]]] = [{} for _ in range(windows)]
    for sample in samples:
        t = float(sample["t"])
        if not start <= t <= end:
            continue
        slot = points[min(windows - 1, int((t - start) / width))]
        for row in sample["procs"]:
            pid = int(row["pid"])
            if pid in app:
                slot.setdefault(pid, []).append((t, int(row["utime"]) + int(row["stime"])))
    result: list[Optional[float]] = []
    for per_pid in points:
        total: Optional[float] = None
        for series in per_pid.values():
            if len(series) >= 2 and series[-1][0] > series[0][0]:
                cores = (series[-1][1] - series[0][1]) / clk / (series[-1][0] - series[0][0])
                total = (total or 0.0) + cores
        result.append(total)
    return result


def spread(values: Sequence[Optional[float]]) -> dict[str, Optional[float]]:
    """min/median/max over the values that exist."""

    present = sorted(v for v in values if isinstance(v, (int, float)))
    return {"n": len(present), "min": present[0] if present else None,
            "median": _percentile(present, 0.5), "max": present[-1] if present else None}


def load_samples(path: Path) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
    header: Optional[dict[str, Any]] = None
    samples: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and "header" in item:
            header = item["header"]
        elif isinstance(item, dict) and "procs" in item:
            samples.append(item)
    return header, samples


def _percentile(values: Sequence[float], fraction: float) -> Optional[float]:
    """Linear interpolation, as numpy.percentile's default."""

    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize_processes(header: Optional[dict[str, Any]], samples: list[dict[str, Any]],
                        window: tuple[Optional[float], Optional[float]]) -> dict[str, Any]:
    """Roles over the whole run; CPU and memory over the telemetry window."""

    start, end = window
    last_cmd: dict[int, list[str]] = {}
    starts: dict[int, int] = {}
    in_window_pids: set[int] = set()
    for sample in samples:
        inside = start is not None and end is not None and start <= sample["t"] <= end
        for row in sample["procs"]:
            pid = int(row["pid"])
            starts[pid] = int(row["start"])
            if inside:
                in_window_pids.add(pid)
            if row.get("cmd"):
                last_cmd[pid] = list(row["cmd"])
    roles = {pid: classify_role(last_cmd.get(pid, [])) for pid in starts}
    # A-8: an unclassified descendant never seen inside the telemetry window (for
    # example sounddevice's import-time ``ldconfig -p``) is recorded, not failed.
    # Survivors are caught separately by the residual check.
    transients = {
        pid: last_cmd.get(pid, []) for pid, role in roles.items()
        if role == "unknown" and pid not in in_window_pids
    }
    for pid in transients:
        roles[pid] = "transient"
    role_pids: dict[str, list[int]] = {}
    for pid, role in sorted(roles.items()):
        role_pids.setdefault(role, []).append(pid)

    in_window = [
        sample for sample in samples
        if start is not None and end is not None and start <= sample["t"] <= end
    ]
    clk = float((header or {}).get("clk_tck") or 0)
    per_pid: dict[int, list[tuple[float, int]]] = {}
    totals_rss: list[float] = []
    totals_pss: list[float] = []
    pss_available = bool(in_window)
    hwm: dict[int, int] = {}
    role_rss: dict[str, list[float]] = {}
    role_pss: dict[str, list[float]] = {}
    for sample in in_window:
        rss = 0.0
        pss = 0.0
        sample_role_rss: dict[str, float] = {}
        sample_role_pss: dict[str, float] = {}
        for row in sample["procs"]:
            pid = int(row["pid"])
            role = roles.get(pid)
            if role not in APP_ROLES:
                continue
            sample_role_rss[role] = sample_role_rss.get(role, 0.0) + float(row.get("rss_kb") or 0)
            if row.get("pss_kb") is not None:
                sample_role_pss[role] = sample_role_pss.get(role, 0.0) + float(row["pss_kb"])
            per_pid.setdefault(pid, []).append(
                (float(sample["t"]), int(row["utime"]) + int(row["stime"]))
            )
            rss += float(row.get("rss_kb") or 0)
            if row.get("pss_kb") is None:
                pss_available = False
            else:
                pss += float(row["pss_kb"])
            if row.get("hwm_kb") is not None:
                hwm[pid] = max(hwm.get(pid, 0), int(row["hwm_kb"]))
        totals_rss.append(rss)
        totals_pss.append(pss)
        for role, value in sample_role_rss.items():
            role_rss.setdefault(role, []).append(value)
        for role, value in sample_role_pss.items():
            role_pss.setdefault(role, []).append(value)

    cpu_by_role: dict[str, float] = {}
    for pid, points in per_pid.items():
        if len(points) < 2 or clk <= 0 or points[-1][0] <= points[0][0]:
            continue
        cores = (points[-1][1] - points[0][1]) / clk / (points[-1][0] - points[0][0])
        cpu_by_role[roles[pid]] = cpu_by_role.get(roles[pid], 0.0) + cores
    cpu_total = sum(cpu_by_role.values()) if cpu_by_role else None

    def stats(values: list[float]) -> dict[str, Optional[float]]:
        return {"p50": _percentile(values, 0.50), "p95": _percentile(values, 0.95),
                "max": max(values) if values else None}

    duration = (end - start) if start is not None and end is not None else None
    interval = float((header or {}).get("interval") or SAMPLER_INTERVAL_SECONDS)
    return {
        "role_pids": role_pids,
        "transients": {str(pid): cmd for pid, cmd in sorted(transients.items())},
        "processes": sorted(starts.items()),
        "window_samples": len(in_window),
        "expected_window_samples": (duration / interval) if duration else None,
        "sampler_euid": (header or {}).get("euid"),
        "cpu_by_role": cpu_by_role,
        "cpu_total": cpu_total,
        "rss_total_kb": stats(totals_rss),
        "pss_total_kb": stats(totals_pss) if pss_available else stats([]),
        "pss_available": pss_available,
        "hwm_peak_total_kb": sum(hwm.values()) if hwm else None,
        "rss_by_role_kb": {role: stats(values) for role, values in sorted(role_rss.items())},
        "pss_by_role_kb": {role: stats(values) for role, values in sorted(role_pss.items())},
    }


def evaluate_measured_run(run: dict[str, Any], summary: Optional[dict[str, Any]],
                          sequences: Optional[list[int]],
                          processes: dict[str, Any],
                          survivors: Sequence[str],
                          throttle_baseline: Optional[int],
                          windows: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Step 3 gates plus the Step 4 gates. v2: only STOP_GATES stop the sequence;
    every other failed gate is a flag (D-011a)."""

    base = evaluate_run(run, summary, throttle_baseline)
    gates = dict(base["gates"])
    summary = summary if isinstance(summary, dict) else {}
    deltas = summary.get("audio_counter_deltas") or {}
    rates = summary.get("rates") or {}
    age = (summary.get("latency") or {}).get("result_age") or {}

    gates["exact_commit"] = run.get("head") == run.get("sha") and run.get("clean") is True
    expected_experiment = "1" if run.get("topology") == "candidate" else None
    gates["topology_env"] = (run.get("env") or {}).get(EXPERIMENT_ENV) == expected_experiment
    # v2: transport drops are reported as a fraction, not gated (measurement-plan-v2).
    drops = deltas.get("transport_drop_count")
    generations = deltas.get("sample_generation")
    skipped = deltas.get("skipped_generation_count")
    skipped_fraction = (
        skipped / generations
        if isinstance(skipped, int) and isinstance(generations, int) and generations > 0
        else None
    )
    gates["skipped_fraction"] = (
        skipped_fraction is not None and skipped_fraction <= MAX_SKIPPED_FRACTION
    )
    published = rates.get("published_content_frames")
    gates["raw_sequences"] = (
        sequences is not None and len(sequences) == summary.get("sample_count")
    )
    superseded = superseded_count(sequences) if sequences else None
    superseded_fraction = (
        superseded / published
        if superseded is not None and isinstance(published, int) and published > 0
        else None
    )
    drop_fraction = (
        drops / published
        if isinstance(drops, int) and isinstance(published, int) and published > 0
        else None
    )
    density = (summary.get("workload") or {}).get("visible_pixels_mean")
    gates["density"] = isinstance(density, (int, float))

    role_pids = processes.get("role_pids", {})
    renderers = len(role_pids.get("renderer", []))
    gates["roles"] = (
        len(role_pids.get("parent", [])) == 1
        and renderers == (1 if run.get("topology") == "candidate" else 0)
        and len(role_pids.get("resource_tracker", [])) <= 1
        and 1 <= len(role_pids.get("sudo", [])) <= 2
        and not role_pids.get("unknown")
    )
    expected = processes.get("expected_window_samples")
    # v2: a run with no telemetry window (no summary, or it stopped during settle,
    # as when the renderer misses readiness) is flagged through schema, duration,
    # and frames. The sampler is a harness fault only on its own exit or euid, or on
    # poor coverage of a window that exists.
    gates["sampler"] = (
        run.get("sampler_returncode") == 0
        and processes.get("sampler_euid") == 0
        and (expected is None or (
            processes.get("window_samples", 0) >= MIN_SAMPLER_COVERAGE * expected
            and processes.get("cpu_total") is not None
        ))
    )
    gates["no_residual_process"] = not survivors

    notes = list(base["notes"])
    if survivors:
        notes.append("residual processes: " + "; ".join(survivors))
    if role_pids.get("unknown"):
        notes.append(f"unknown processes: {role_pids['unknown']}")

    base.update({
        "topology": run.get("topology"),
        "sha": run.get("sha"),
        "published_fps": rates.get("published_content_fps"),
        "result_age_ms": {key: age.get(key) for key in ("p50_ms", "p95_ms", "p99_ms", "max_ms")},
        "callback_generations": generations,
        "skipped_generations": skipped,
        "published_frames": published,
        "unique_frames": rates.get("unique_content_frames"),
        "temperature": run.get("temperature"),
        "skipped_fraction": skipped_fraction,
        "superseded": superseded,
        "superseded_fraction": superseded_fraction,
        "transport_drops": drops,
        "drop_fraction": drop_fraction,
        "density_mean": density,
        "processes": {k: v for k, v in processes.items() if k != "processes"},
        "windows": windows or {"frames": [], "cpu": []},
        "gates": gates,
        "flags": [name for name, passed in gates.items() if not passed],
        "notes": notes,
    })
    base.pop("failed_gates", None)
    base["stops"] = [name for name in base["flags"] if name in STOP_GATES]
    return base


def _ratio(candidate: Optional[float], baseline: Optional[float]) -> Optional[float]:
    if candidate is None or baseline is None or baseline == 0:
        return None
    return candidate / baseline


def _mean(values: Sequence[Any]) -> Optional[float]:
    present = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return sum(present) / len(present) if present else None


ARM_METRICS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "swap_fps": lambda e: e.get("swap_fps"),
    "published_fps": lambda e: e.get("published_fps"),
    "unique_fps": lambda e: e.get("unique_fps"),
    "analyzed_fraction": lambda e: e.get("analyzed_published_fraction"),
    "result_age_p50_ms": lambda e: (e.get("result_age_ms") or {}).get("p50_ms"),
    "result_age_p95_ms": lambda e: e.get("result_age_p95_ms"),
    "result_age_p99_ms": lambda e: (e.get("result_age_ms") or {}).get("p99_ms"),
    "skipped_fraction": lambda e: e.get("skipped_fraction"),
    "superseded_fraction": lambda e: e.get("superseded_fraction"),
    "drop_fraction": lambda e: e.get("drop_fraction"),
    "cpu_total": lambda e: (e.get("processes") or {}).get("cpu_total"),
    "density_mean": lambda e: e.get("density_mean"),
}
WINDOW_METRICS = ("swap_fps", "published_fps", "age_p95_ms", "cpu")


def summarize_measurement(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    """v2 report-only summary (D-011a). Arm means, ratios, and window spreads.

    There is no accept/reject rule: the user decides from the report. The outcome
    is ``incomplete`` when the sequence stopped early or a run failed a harness
    gate, and ``report-only`` otherwise, whatever the flags.
    """

    measured = [item for item in evaluations if not item.get("skipped")]
    harness = [item["label"] for item in measured
               if any(flag in HARNESS_GATES for flag in item.get("flags", []))]
    flagged = {item["label"]: item["flags"] for item in measured if item.get("flags")}
    usable = [item for item in measured if item["label"] not in harness]

    backends: dict[str, Any] = {}
    for mode in OUTPUT_MODES:
        arms = {
            topology: [e for e in usable if e["mode"] == mode and e["topology"] == topology]
            for topology in ("baseline", "candidate")
        }
        runs = arms["baseline"] + arms["candidate"]
        use_pss = bool(runs) and all((e.get("processes") or {}).get("pss_available")
                                     for e in runs)
        memory_key = "pss_total_kb" if use_pss else "rss_total_kb"
        metrics = dict(ARM_METRICS)
        metrics["memory_p95_kb"] = (
            lambda e, key=memory_key: ((e.get("processes") or {}).get(key) or {}).get("p95"))
        summary: dict[str, Any] = {"memory_metric": memory_key, "notes": []}
        for topology, items in arms.items():
            pooled: dict[str, list[Optional[float]]] = {name: [] for name in WINDOW_METRICS}
            for item in items:
                windows = item.get("windows") or {}
                for window in windows.get("frames") or []:
                    for name in ("swap_fps", "published_fps", "age_p95_ms"):
                        pooled[name].append(window.get(name))
                pooled["cpu"].extend(windows.get("cpu") or [])
            summary[topology] = {
                "runs": [item["label"] for item in items],
                "mean": {name: _mean([getter(item) for item in items])
                         for name, getter in metrics.items()},
                "windows": {name: spread(values) for name, values in pooled.items()},
            }
        summary["ratios"] = {
            name: _ratio(summary["candidate"]["mean"][name], summary["baseline"]["mean"][name])
            for name in metrics
        }
        # Differences read better than ratios for fractions with a zero baseline.
        summary["differences"] = {
            name: (summary["candidate"]["mean"][name] - summary["baseline"]["mean"][name]
                   if None not in (summary["candidate"]["mean"][name],
                                   summary["baseline"]["mean"][name]) else None)
            for name in metrics
        }
        density = summary["ratios"].get("density_mean")
        if density is not None and abs(density - 1) > DENSITY_NOTE_DIFFERENCE:
            summary["notes"].append(
                f"workload density differs by {abs(density - 1) * 100:.1f}% between arms"
                " (informational)")
        backends[mode] = summary

    if len(measured) < len(MEASURE_SEQUENCE) or harness:
        reason = (f"{len(measured)} of {len(MEASURE_SEQUENCE)} runs measured"
                  + (f"; harness gates failed: {', '.join(harness)}" if harness else "")
                  + ". A repeat is the complete sequence under a new evidence directory.")
        return {"decision": "incomplete", "reason": reason, "flagged": flagged,
                "backends": backends}
    reason = (f"{len(measured)} of {len(MEASURE_SEQUENCE)} runs measured; no fixed rule,"
              " the user decides from this report (D-011a)")
    if flagged:
        reason += "; flagged: " + "; ".join(
            f"{label} ({', '.join(flags)})" for label, flags in flagged.items())
    return {"decision": "report-only", "reason": reason, "flagged": flagged,
            "backends": backends}


def measure_markdown(context: dict[str, Any], evaluations: list[dict[str, Any]],
                     decision: dict[str, Any]) -> str:
    lines = [
        "# Step 4 matrix-process measurement (v2, report-only)",
        "",
        f"- Baseline `{context['baseline']}` (in-process) · candidate "
        f"`{context['candidate']}` ({EXPERIMENT_ENV}=1)",
        f"- Tool commit `{context['tool_commit']}` · remote.py sha256 `{context['tool_sha256']}`",
        f"- Settle {context['settle']:g} s · duration {context['duration']:g} s · "
        f"max samples {context['max_samples']} · started {context['stamp']}",
        f"- Audio: {context['audio_note']}",
        f"- Evidence: `{context['evidence_dir']}`",
        f"- Restored to `{context['restored']}` · service "
        f"{'healthy' if context['healthy'] else 'UNHEALTHY'}",
        "",
        "| Run | Swap FPS | Published FPS | Age p95 ms | Skipped | Superseded "
        "| Drops | Density | CPU cores | Mem p95 MiB | Roles | Flags |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for item in evaluations:
        procs = item.get("processes") or {}
        memory = procs.get("pss_total_kb") if procs.get("pss_available") else procs.get("rss_total_kb")
        roles = ",".join(f"{role}:{len(pids)}" for role, pids in
                         sorted((procs.get("role_pids") or {}).items()))
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            item["label"] + (" (skipped)" if item.get("skipped") else ""),
            _fmt(item.get("swap_fps"), ".3f"), _fmt(item.get("published_fps"), ".3f"),
            _fmt(item.get("result_age_p95_ms"), ".3f"),
            _fmt(None if item.get("skipped_fraction") is None
                 else item["skipped_fraction"] * 100, ".2f", "%"),
            _fmt(None if item.get("superseded_fraction") is None
                 else item["superseded_fraction"] * 100, ".2f", "%"),
            _fmt(item.get("transport_drops")), _fmt(item.get("density_mean"), ".1f"),
            _fmt(procs.get("cpu_total"), ".3f"),
            _fmt(None if not memory or memory.get("p95") is None else memory["p95"] / 1024,
                 ".1f"),
            roles or "—", ", ".join(item.get("flags") or []) or "none",
        ))
    for mode, item in decision.get("backends", {}).items():
        lines += ["", f"## {mode}", "",
                  f"Runs: baseline {', '.join(item['baseline']['runs']) or '—'}; "
                  f"candidate {', '.join(item['candidate']['runs']) or '—'}. "
                  f"Memory metric: {item['memory_metric']}.",
                  "",
                  "| Metric | Baseline mean | Candidate mean | Candidate/baseline "
                  "| Candidate − baseline |",
                  "|---|---:|---:|---:|---:|"]
        for name, value in item["baseline"]["mean"].items():
            lines.append(f"| {name} | {_fmt(value, '.4f')} | "
                         f"{_fmt(item['candidate']['mean'][name], '.4f')} | "
                         f"{_fmt(item['ratios'][name], '.4f')} | "
                         f"{_fmt(item['differences'][name], '.4f')} |")
        lines += ["", f"| {WINDOW_SECONDS:g} s windows | Baseline min / median / max (n) "
                      "| Candidate min / median / max (n) |", "|---|---:|---:|"]
        for name in WINDOW_METRICS:
            cells = []
            for topology in ("baseline", "candidate"):
                s = item[topology]["windows"][name]
                cells.append(f"{_fmt(s['min'], '.3f')} / {_fmt(s['median'], '.3f')} / "
                             f"{_fmt(s['max'], '.3f')} ({s['n']})")
            lines.append(f"| {name} | {cells[0]} | {cells[1]} |")
        lines += [f"- {mode}: {note}" for note in item["notes"]]
    notes = [f"- {item['label']}: {note}" for item in evaluations for note in item.get("notes", [])]
    notes += [f"- {error}" for error in context["errors"]]
    if notes:
        lines += ["", "Notes:", *notes]
    lines += ["", f"Result: **{decision['decision'].upper()}** — {decision['reason']}"]
    exit_code = context.get("exit_code", EXIT_OK)
    if exit_code != EXIT_OK:
        lines.append(f"Operation: exit {exit_code} (restored `{context['restored']}`, service "
                     f"{'healthy' if context['healthy'] else 'UNHEALTHY'}); errors are"
                     " listed under Notes.")
    lines.append("")
    return "\n".join(lines)


MEASURE_INTERPRETATIONS = {
    "decision": "report-only (D-011a): no fixed accept/reject rule; the user decides",
    "stops": "only " + ", ".join(STOP_GATES) + " (and interruptions or pre-run"
             " refusals) stop the sequence; every other failed gate is a flag",
    "arm_value": "mean of the arm's runs that report the metric; runs with a harness"
                 " flag are excluded",
    "cpu": "per process (utime+stime) from first to last in-window sample divided by"
           " that span, summed over parent/renderer/resource_tracker",
    "memory": "per run p95 of the per-sample summed app-role PSS; RSS for a backend if"
              " any of its runs lacks PSS",
    "skipped_fraction": "skipped_generation_count / sample_generation (deltas)",
    "superseded_fraction": "sum(max(0, delta_seq - 1)) / published_content_frames",
    "drop_fraction": "transport_drop_count / published_content_frames (not gated)",
    "frame_windows": "round(span / 60 s) equal windows from first to last frame"
                     " completion; swap FPS = frames / width; published FPS = rendered"
                     " sequence advance / first-to-last frame time; age p95 per window",
    "cpu_windows": "the same count of equal windows over the telemetry wall-clock span",
    "density": "an arm difference above 15% is noted, not invalidating",
    "harness_gates": "sampler (exit 0, euid 0, and >=90% coverage of the telemetry window"
                     " when one exists), exact_commit, topology_env: failure stops the"
                     " sequence as incomplete",
    "residual": "a process alive after its run's 5 s grace stops the sequence (exit 1);"
                " one still alive at cleanup keeps the service stopped (exit 3)",
    "throttling": "each run's baseline is read just before its launch; a sticky bit"
                  " already set cannot reveal a repeat event",
    "outcome_and_exit": "the outcome describes the measurement; exit code and"
                        " operational errors are recorded beside it in decision.json",
    "transient_descendants": "A-8: unclassified descendants never seen inside the"
                             " telemetry window are recorded, not flagged",
}


def ignored_tracked_conflicts(host: PiHost, shas: Sequence[str]) -> list[str]:
    """Ignored untracked Pi files at paths a measured or restored commit tracks."""

    ignored = host.git("ls-files", "--others", "--ignored", "--exclude-standard",
                       "--directory")
    if ignored.returncode != 0:
        raise Refused(f"could not list ignored files: {ignored.stderr.strip()}")
    entries = [line for line in ignored.stdout.splitlines() if line]
    files = {entry for entry in entries if not entry.endswith("/")}
    directories = tuple(entry for entry in entries if entry.endswith("/"))
    conflicts: set[str] = set()
    for sha in shas:
        tracked = host.git("ls-tree", "-r", "--name-only", sha)
        if tracked.returncode != 0:
            raise Refused(f"could not list {sha}: {tracked.stderr.strip()}")
        for path in tracked.stdout.splitlines():
            if path in files or (directories and path.startswith(directories)):
                conflicts.add(path)
    return sorted(conflicts)


def cmd_measure(host: PiHost, report: Reporter, *, baseline: str, candidate: str,
                expect_start: str, settle: float, duration: float, max_samples: int,
                tool_commit: str, tool_sha256: str, audio_note: str,
                install_signals: bool = True) -> int:
    payload: dict[str, Any] = {"command": "measure-split"}
    try:
        _validate_check_args(["direct"], settle, duration, max_samples)
        before = collect_status(host)
        payload["status_before"] = before
        if before["porcelain"] != []:
            raise Refused(f"Pi checkout is not clean: {before['porcelain']}")
        start_sha = _resolve(host, expect_start)
        if not before["detached"] or before["head"] != start_sha:
            raise Refused(f"Pi is at {before['head']}, expected detached {start_sha}")
        if before["service_active"] != "active" or not before["healthz"]["ok"]:
            raise Refused("service is not active and healthy before the measurement")
        throttled = before["throttled"]
        if throttled is None or throttled & THROTTLE_CURRENT_MASK:
            raise Refused(f"throttle state not clean before the measurement: {throttled}")
        # Read-only: may this user run the root /proc sampler (A-3)? Refuse now rather
        # than fail the first run's sampler gate.
        if host.run(["sudo", "-n", "-l", "/usr/bin/python3"]).returncode != 0:
            raise Refused("sudo -n does not allow /usr/bin/python3 for the /proc sampler")
        shas = {"baseline": _resolve(host, baseline), "candidate": _resolve(host, candidate)}
        for name, sha in shas.items():
            if sha != (baseline if name == "baseline" else candidate):
                raise Refused(f"{name} must be a full commit id present on the Pi")
        if shas["baseline"] == shas["candidate"]:
            raise Refused("baseline and candidate are the same commit")
        conflicts = ignored_tracked_conflicts(host, [*shas.values(), start_sha])
        if conflicts:
            raise Refused("ignored Pi files would be overwritten or removed by a"
                          f" checkout: {conflicts[:10]}")
    except Refused as exc:
        payload["refused"] = str(exc)
        report.line(f"refused: {exc}")
        report.result(payload)
        return EXIT_REFUSED

    stamp = host.utc_stamp()
    run_dir = Path(f"{RUN_DIR_PREFIX}{stamp}")
    evidence_dir = host.evidence_root() / f"{MEASURE_EVIDENCE_PREFIX}{stamp}"
    try:
        evidence_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        payload["refused"] = f"{evidence_dir} already exists; retry in a second"
        report.line(f"refused: {payload['refused']}")
        report.result(payload)
        return EXIT_REFUSED
    report.log_path = evidence_dir / "measure.log"
    report.line(f"evidence  {evidence_dir}")
    report.line(f"start     {start_sha} (restored unconditionally at the end)")
    _write_json(evidence_dir / "status-before.json", before)
    _write_json(evidence_dir / "plan.json", {
        "baseline": shas["baseline"], "candidate": shas["candidate"],
        "expect_start": start_sha, "settle": settle, "duration": duration,
        "max_samples": max_samples, "sequence": MEASURE_SEQUENCE,
        "tool_commit": tool_commit, "tool_sha256": tool_sha256,
        "audio_note": audio_note, "sampler_interval": SAMPLER_INTERVAL_SECONDS,
        "interpretations": MEASURE_INTERPRETATIONS,
        "inherited_rgbmatrix_env": {
            key: value for key, value in sorted(os.environ.items())
            if key.startswith("RGBMATRIX_")
        },
    })

    commands: dict[str, dict[str, Any]] = {}
    first_launch: Optional[dict[str, Any]] = None

    def write_commands() -> None:
        _write_json(evidence_dir / "argv.json", {
            "controller": first_launch,
            "runs": {label: item["argv"] for label, item in commands.items()},
        })
        _write_json(evidence_dir / "env.json",
                    {label: item["env"] for label, item in commands.items()})

    child_timeout = settle + duration + CHILD_MARGIN_SECONDS
    runs: list[dict[str, Any]] = []
    evaluations: list[dict[str, Any]] = []
    errors: list[str] = []
    all_processes: list[tuple[int, int]] = []
    python: Optional[str] = None
    restored: Optional[str] = None
    healthy = False
    previous_handlers = _set_stop_signals(_raise_aborted_once) if install_signals else {}
    previous_sigint = None
    try:
        try:
            made = host.make_run_dir(run_dir)
            if made.returncode != 0:
                raise RuntimeError(f"could not create {run_dir}: {made.stderr.strip()}")
            stopped = host.stop_service()
            if stopped.returncode != 0:
                raise RuntimeError(f"service stop failed: {stopped.stderr.strip()}")
            report.line(f"stopped   {host.unit} for the whole sequence")
            for label, topology, mode in MEASURE_SEQUENCE:
                if report.stdout_broken:
                    raise RuntimeError("the Mac side disconnected")
                present = host.fft_processes() + host.multiprocessing_processes(python)
                if present:
                    raise RuntimeError(f"FFT process present before {label}: {present}")
                sha = shas[topology]
                with _Deferred(install_signals):
                    porcelain = host.git("status", "--porcelain")
                    if porcelain.returncode != 0 or porcelain.stdout.strip():
                        raise RuntimeError(
                            f"Pi tree not clean before {label}: {porcelain.stdout!r}")
                    checked_out = host.git("checkout", "--detach", "--quiet", sha)
                    if checked_out.returncode != 0:
                        raise RuntimeError(
                            f"checkout {sha} failed: {checked_out.stderr.strip()}")
                    head = host.git("rev-parse", "HEAD").stdout.strip()
                    clean = host.git("status", "--porcelain")
                is_clean = clean.returncode == 0 and not clean.stdout.strip()
                if head != sha or not is_clean:
                    raise RuntimeError(f"Pi is at {head} (clean={is_clean}) before {label},"
                                       f" expected clean {sha}")
                launch = fft_launch(host)
                if first_launch is None:
                    first_launch = launch
                    python = launch.get("python")
                elif launch != first_launch:
                    raise RuntimeError(f"controller argv differs at {label}: {launch}")
                argv = [*launch["argv"],
                        "--telemetry-out", str(run_dir / f"{label}.json"),
                        "--telemetry-label", label,
                        "--telemetry-source-id", sha,
                        "--telemetry-settle", f"{settle:g}",
                        "--telemetry-duration", f"{duration:g}",
                        "--telemetry-max-samples", str(max_samples)]
                env = measure_env(topology, mode, run_dir)
                commands[label] = {"argv": argv, "env": env}
                write_commands()
                record: dict[str, Any] = {
                    "label": label, "mode": mode, "topology": topology, "sha": sha,
                    "head": head, "env": env, "clean": is_clean,
                }
                runs.append(record)
                report.line(f"run       {label} ({topology} {sha[:7]}, {mode}), "
                            f"about {settle + duration:g} s")
                # v2: each run's own throttle baseline, so a sticky bit set in one run
                # flags only that run. A bit already set can't reveal a repeat event.
                record["throttled_before"] = _parse_throttled(
                    host.run(["vcgencmd", "get_throttled"]).stdout
                )
                record.update(host.run_measured(
                    argv, cwd=Path(launch["cwd"]), env=env, timeout=child_timeout,
                    log_path=evidence_dir / f"{label}.log",
                    samples_path=evidence_dir / f"{label}.proc.jsonl",
                    sampler_log_path=evidence_dir / f"{label}.sampler.log",
                ))
                record["throttled"] = _parse_throttled(
                    host.run(["vcgencmd", "get_throttled"]).stdout
                )
                record["temperature"] = (
                    host.run(["vcgencmd", "measure_temp"]).stdout.strip() or None
                )
                collected = host.collect_run_dir(run_dir, evidence_dir)
                if collected.returncode != 0:
                    errors.append(f"evidence copy failed after {label}: "
                                  f"{collected.stderr.strip()}")
                throttle_baseline = (record["throttled_before"]
                                     if record["throttled_before"] is not None
                                     else before["throttled"])
                evaluation = _evaluate_measured(host, record, evidence_dir, python,
                                                all_processes, throttle_baseline)
                evaluations.append(evaluation)
                report.line(f"done      {label}: exit {record['returncode']}, "
                            f"flags: {', '.join(evaluation['flags']) or 'none'}")
                # v2 (D-011a): only a survivor or a harness fault stops the sequence.
                if evaluation["stops"]:
                    raise RuntimeError(f"{label} failed {', '.join(evaluation['stops'])};"
                                       " stopping the sequence")
        except (Exception, KeyboardInterrupt) as exc:
            errors.append(f"measurement stopped early: {type(exc).__name__}: {exc}")
        finally:
            if install_signals:
                _set_stop_signals(signal.SIG_IGN)
                previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
            # A run that never reached evaluation still left a sampled process tree.
            evaluated = {item["label"] for item in evaluations}
            for record in runs:
                if record["label"] not in evaluated:
                    try:
                        _, samples = load_samples(
                            evidence_dir / f"{record['label']}.proc.jsonl")
                        all_processes.extend(
                            (int(row["pid"]), int(row["start"]))
                            for sample in samples for row in sample["procs"])
                    except Exception as exc:  # cleanup must still restore
                        errors.append(f"could not read {record['label']} samples: {exc!r}")
            lingering = host.stop_survivors(all_processes)
            if lingering:
                errors.append("processes outlived their run: " + "; ".join(lingering))
            leftovers = (host.fft_processes() + host.multiprocessing_processes(python)
                         + host.surviving(all_processes))
            porcelain = host.git("status", "--porcelain")
            if porcelain.returncode != 0 or porcelain.stdout.strip():
                errors.append(f"restore skipped: Pi tree not clean: {porcelain.stdout!r}")
            else:
                restored_out = host.git("checkout", "--detach", "--quiet", start_sha)
                head_now = host.git("rev-parse", "HEAD").stdout.strip()
                clean_now = host.git("status", "--porcelain")
                if restored_out.returncode != 0:
                    errors.append(f"restore checkout failed: {restored_out.stderr.strip()}")
                elif head_now != start_sha or clean_now.returncode != 0 \
                        or clean_now.stdout.strip():
                    errors.append(f"restore checkout left {head_now} {clean_now.stdout!r}")
                else:
                    restored = start_sha
            if leftovers:
                errors.append("service not started: FFT process still running: "
                              + "; ".join(leftovers))
            elif restored != start_sha:
                # Never start the service on the candidate or a broken tree (B1).
                errors.append(f"service not started: Pi not restored to {start_sha}")
            else:
                started = host.start_service()
                if started.returncode != 0:
                    errors.append(f"service start failed: {started.stderr.strip()}")
            healthy, body = wait_for_healthz(host)
            report.line(f"healthz   {'ok' if healthy else 'FAIL'} ({body})")
            collected = host.collect_run_dir(run_dir, evidence_dir)
            if collected.returncode != 0:
                errors.append(f"evidence copy failed: {collected.stderr.strip()}")
            else:
                removed = host.remove_run_dir(run_dir)
                if removed.returncode != 0:
                    errors.append(f"could not remove {run_dir}: {removed.stderr.strip()}")
    finally:
        _restore_signals(previous_handlers)
        if previous_sigint is not None:
            signal.signal(signal.SIGINT, previous_sigint)

    after = collect_status(host)
    _write_json(evidence_dir / "status-after.json", after)
    if after["head"] != start_sha or after["porcelain"] != []:
        errors.append(f"service restore: Pi is at {after['head']} {after['porcelain']},"
                      f" expected clean {start_sha}")
        restored = after["head"]
    done = {item["label"] for item in evaluations}
    for label, topology, mode in MEASURE_SEQUENCE:
        if label not in done:
            evaluations.append({"label": label, "mode": mode, "topology": topology,
                                "skipped": True, "flags": ["skipped"], "stops": [],
                                "notes": []})
    order = [label for label, _, _ in MEASURE_SEQUENCE]
    evaluations.sort(key=lambda item: order.index(item["label"]))
    _write_json(evidence_dir / "runs.json", evaluations)
    try:
        decision = summarize_measurement(evaluations)
    except Exception as exc:  # keep the evidence; never lose a sequence to a bug here
        decision = {"decision": "incomplete", "reason": f"summary error: {exc!r}",
                    "flagged": {}, "backends": {}}
    # The outcome describes the measurement; the exit code describes Pi operations.
    # Both are recorded together so results.md and decision.json can't disagree
    # with what the tool returned.
    if not healthy or restored != start_sha or any(
            error.startswith(("service ", "restore ", "processes outlived"))
            for error in errors):
        exit_code = EXIT_OPERATION_FAILED
    elif errors or decision["decision"] == "incomplete":
        exit_code = EXIT_GATE_FAILED
    else:
        exit_code = EXIT_OK
    decision["operation"] = {"exit_code": exit_code, "restored": restored,
                             "healthy": healthy, "errors": list(errors)}
    context = {
        "baseline": shas["baseline"], "candidate": shas["candidate"],
        "tool_commit": tool_commit, "tool_sha256": tool_sha256, "settle": settle,
        "duration": duration, "max_samples": max_samples, "stamp": stamp,
        "audio_note": audio_note, "evidence_dir": str(evidence_dir),
        "restored": restored, "healthy": healthy, "errors": errors,
        "exit_code": exit_code,
    }
    try:
        markdown = measure_markdown(context, evaluations, decision)
    except Exception as exc:  # never lose the manifest or result line to a report bug
        markdown = (f"# Step 4 matrix-process measurement (v2, report-only)\n\n"
                    f"Report rendering failed: {exc!r}. See runs.json and decision.json.\n\n"
                    f"Result: **{decision['decision'].upper()}** — {decision['reason']}\n"
                    f"Operation: exit {exit_code}\n")
    (evidence_dir / "results.md").write_text(markdown, encoding="utf-8")
    _write_json(evidence_dir / "decision.json", decision)
    payload.update({
        "evidence_dir": str(evidence_dir), "healthy": healthy, "restored": restored,
        "errors": errors, "decision": decision["decision"],
        "decision_reason": decision["reason"], "results_markdown": markdown,
    })
    report.result(payload)
    report.log_path = None
    _write_manifest(evidence_dir)
    manifest_sha = hashlib.sha256((evidence_dir / "SHA256SUMS").read_bytes()).hexdigest()
    report.line(f"manifest  {evidence_dir / 'SHA256SUMS'} sha256 {manifest_sha}")
    # v2: flags never change the exit code (computed above, before the report).
    return exit_code


def _evaluate_measured(host: PiHost, record: dict[str, Any], evidence_dir: Path,
                       python: Optional[str], all_processes: list[tuple[int, int]],
                       throttle_baseline: Optional[int]) -> dict[str, Any]:
    label = record["label"]
    try:
        summary = json.loads((evidence_dir / f"{label}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        summary = None
    try:
        sequences: Optional[list[int]] = read_npz_uint64(
            evidence_dir / f"{label}.npz", "analysis_sequences"
        )
    except Exception as exc:  # v2: an unreadable file is a flag, never a stop
        sequences = None
        record["error"] = f"raw sequences unreadable: {exc!r}"
    header, samples = load_samples(evidence_dir / f"{label}.proc.jsonl")
    window = (
        summary.get("start_wall_time") if isinstance(summary, dict) else None,
        summary.get("end_wall_time") if isinstance(summary, dict) else None,
    )
    processes = summarize_processes(header, samples, window)
    all_processes.extend(processes["processes"])
    survivors = (host.stop_survivors(processes["processes"]) + host.fft_processes()
                 + host.multiprocessing_processes(python))
    # v2 one-minute windows. Any failure here only empties the windows (and is
    # noted); it never stops the sequence.
    windows: dict[str, Any] = {"frames": [], "cpu": []}
    try:
        if sequences is not None:
            windows["frames"] = frame_windows(
                read_npz_uint64(evidence_dir / f"{label}.npz", "completed_ns"),
                read_npz_uint64(evidence_dir / f"{label}.npz", "result_age_ns"),
                sequences,
            )
            if not windows["frames"] and not record.get("error"):
                record["error"] = "no frame windows (fewer than 2 frames or mismatched arrays)"
        span = (window[1] - window[0]) if None not in window else 0.0
        count = len(windows["frames"]) or (window_count(span) if span > 0 else 0)
        windows["cpu"] = cpu_windows(header, samples, processes["role_pids"], window, count)
    except Exception as exc:
        if not record.get("error"):
            record["error"] = f"window computation failed: {exc!r}"
    return evaluate_measured_run(record, summary, sequences, processes, survivors,
                                 throttle_baseline, windows)


# Entry point ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="remote.py", description=__doc__)
    parser.add_argument("--checkout", required=True,
                        help="Pi checkout path; ~ is expanded on the Pi.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    checkout = sub.add_parser("checkout")
    checkout.add_argument("ref")
    checkout.add_argument("--expect-sha")
    check = sub.add_parser("check")
    check.add_argument("sha")
    check.add_argument("--modes", required=True)
    check.add_argument("--settle", type=float, required=True)
    check.add_argument("--duration", type=float, required=True)
    check.add_argument("--run-plan", type=parse_run_plan)
    check.add_argument("--max-samples", type=int, required=True)
    measure = sub.add_parser("measure-split")
    measure.add_argument("baseline")
    measure.add_argument("candidate")
    measure.add_argument("--expect-start", required=True)
    measure.add_argument("--settle", type=float, required=True)
    measure.add_argument("--duration", type=float, required=True)
    measure.add_argument("--max-samples", type=int, required=True)
    measure.add_argument("--tool-commit", required=True)
    measure.add_argument("--tool-sha256", required=True)
    measure.add_argument("--audio-note", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None,
         host_factory: Callable[[Path], PiHost] = PiHost,
         report: Optional[Reporter] = None) -> int:
    args = build_parser().parse_args(argv)
    host = host_factory(Path(args.checkout).expanduser())
    report = report or Reporter()
    if args.command == "status":
        return cmd_status(host, report)
    if args.command == "checkout":
        return cmd_checkout(host, report, args.ref, args.expect_sha)
    if args.command == "measure-split":
        return cmd_measure(
            host, report, baseline=args.baseline, candidate=args.candidate,
            expect_start=args.expect_start, settle=args.settle, duration=args.duration,
            max_samples=args.max_samples, tool_commit=args.tool_commit,
            tool_sha256=args.tool_sha256, audio_note=args.audio_note,
        )
    return cmd_check(
        host, report, sha=args.sha, modes=[m for m in args.modes.split(",") if m],
        settle=args.settle, duration=args.duration, max_samples=args.max_samples,
        run_plan=args.run_plan,
    )


if __name__ == "__main__":
    # Under ``python3 -`` argv[0] is "-".
    sys.exit(main(sys.argv[1:]))
