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
    lines = [
        f"# pi-check {context['sha'][:7]}",
        "",
        f"- Source: `{context['sha']}` (Pi HEAD, clean tree)",
        f"- Started: {context['stamp']} · settle {context['settle']:g} s · "
        f"duration {context['duration']:g} s · max samples {context['max_samples']}",
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


_STOP_SIGNALS = (signal.SIGHUP, signal.SIGTERM)


def _set_stop_signals(handler: Any) -> dict[int, Any]:
    return {sig: signal.signal(sig, handler) for sig in _STOP_SIGNALS}


def _restore_signals(previous: dict[int, Any]) -> None:
    for sig, handler in previous.items():
        signal.signal(sig, handler)


def _validate_check_args(modes: Sequence[str], settle: float, duration: float,
                         max_samples: int) -> None:
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
              install_signals: bool = True) -> int:
    payload: dict[str, Any] = {"command": "check"}
    try:
        _validate_check_args(modes, settle, duration, max_samples)
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

    child_timeout = settle + duration + CHILD_MARGIN_SECONDS
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
            for label, mode in run_labels(modes):
                if report.stdout_broken:
                    runs.append({"label": label, "mode": mode, "skipped": True,
                                 "error": "skipped: the Mac side disconnected"})
                    continue
                argv = [*launch["argv"],
                        "--telemetry-out", str(run_dir / f"{label}.json"),
                        "--telemetry-label", label,
                        "--telemetry-source-id", target,
                        "--telemetry-settle", f"{settle:g}",
                        "--telemetry-duration", f"{duration:g}",
                        "--telemetry-max-samples", str(max_samples)]
                env = {"RGBMATRIX_FFT_OUTPUT_PATH": mode,
                       "RGBMATRIX_STATE_DIR": str(run_dir / "state")}
                commands[label] = {"argv": argv, "env": env}
                write_commands()
                report.line(f"run       {label} ({mode}), about {settle + duration:g} s")
                record: dict[str, Any] = {"label": label, "mode": mode}
                runs.append(record)
                record.update(host.run_foreground(
                    argv, cwd=Path(launch["cwd"]), env=env, timeout=child_timeout,
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
    for label, mode in run_labels(modes):
        if label not in done:
            runs.append({"label": label, "mode": mode, "skipped": True,
                         "error": "skipped: the check stopped early"})
    order = [label for label, _ in run_labels(modes)]
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
    check.add_argument("--max-samples", type=int, required=True)
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
    return cmd_check(
        host, report, sha=args.sha, modes=[m for m in args.modes.split(",") if m],
        settle=args.settle, duration=args.duration, max_samples=args.max_samples,
    )


if __name__ == "__main__":
    # Under ``python3 -`` argv[0] is "-".
    sys.exit(main(sys.argv[1:]))
