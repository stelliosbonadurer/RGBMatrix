#!/usr/bin/env python3
"""Scripted Pi cycle: status, deploy, check, and restore, run from the Mac.

    python3 tools/pi/pi_cycle.py status
    python3 tools/pi/pi_cycle.py deploy <sha>
    python3 tools/pi/pi_cycle.py check <sha> [--modes direct,pillow-bulk,direct]
                                              [--duration 30] [--settle 5]
                                              [--max-samples 100000]
    python3 tools/pi/pi_cycle.py restore <ref>
    python3 tools/pi/pi_cycle.py measure-split <baseline> <candidate> \
        --expect-start <sha> --audio-note "..."

The Pi is ``--host``, else ``RGBMATRIX_PI_HOST``, else the ``pi`` git remote's URL.
No hostname is committed. Each command sends ``remote.py`` (beside this file) to the
Pi's ``python3 -`` over SSH, so nothing is installed there and the gates come from
this tool's version. Evidence stays on the Pi. Only progress and the Markdown
results table are printed here, and nothing is written on the Mac.

Exit codes: 0 success, 1 a gate failed, 2 refused (bad arguments, no host, dirty
or mismatched Pi checkout), 3 an operation failed after Pi state changed, 130
interrupted on the Mac, 255 SSH itself failed. ``deploy`` and ``check`` never roll
back on their own. They print the previous SHA.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Any, Callable, Optional, Sequence


TOOL_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOL_DIR.parent.parent
REMOTE_SCRIPT = TOOL_DIR / "remote.py"
HOST_ENV = "RGBMATRIX_PI_HOST"
CHECKOUT_ENV = "RGBMATRIX_PI_CHECKOUT"
DEFAULT_CHECKOUT = "~/rgb_Matrix"
PI_REMOTE = "pi"
DEPLOY_REF = "refs/heads/deploy"
RESULT_MARKER = "@@PI_CYCLE_RESULT@@ "
OUTPUT_MODES = ("direct", "pillow-bulk")
AUDIO_SOURCES = ("usb-mic", "bluetooth")
# Fixed by the Step 4 v2 measurement plan (D-011b); not command-line options.
# 250,000 samples leave room for about 275 FPS over 900 s.
MEASURE_SETTLE_SECONDS = 60.0
MEASURE_DURATION_SECONDS = 900.0
MEASURE_MAX_SAMPLES = 250000
# Only the long measure-split call: probe every 30 s, give up after 5 silent minutes.
SSH_KEEPALIVE_OPTIONS = ("-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=10")

EXIT_OK = 0
EXIT_GATE_FAILED = 1
EXIT_REFUSED = 2
EXIT_OPERATION_FAILED = 3


class UsageError(Exception):
    """Refuse before contacting the Pi (exit 2)."""


@dataclass(frozen=True)
class Target:
    """Where the Pi is: an SSH destination, an optional port, and the checkout path."""

    destination: str
    checkout: str
    port: Optional[int] = None
    push_url: str = ""
    source: str = ""

    def ssh_argv(self, *, keepalive: bool = False) -> list[str]:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        if keepalive:
            argv += SSH_KEEPALIVE_OPTIONS
        if self.port is not None:
            argv += ["-p", str(self.port)]
        return argv + [self.destination]


_SSH_URL = re.compile(r"^ssh://(?P<dest>[^/:]+(?:@[^/:]+)?)(?::(?P<port>\d+))?(?P<path>/.*)$")
_SCP_URL = re.compile(r"^(?P<dest>[^/:]+@[^/:]+|[^/:@]+):(?P<path>.+)$")


def parse_remote_url(url: str) -> Target:
    """Parse ``ssh://user@host[:port]/path`` or scp-style ``user@host:path``."""

    url = url.strip()
    match = _SSH_URL.match(url)
    if match:
        path = match.group("path")
        # ssh://host/~/x is home-relative; ssh://host/abs is absolute.
        checkout = path[1:] if path.startswith("/~") else path
        port = int(match.group("port")) if match.group("port") else None
        return Target(match.group("dest"), checkout, port, "", f"git remote {PI_REMOTE!r}")
    match = None if "://" in url else _SCP_URL.match(url)
    if match:
        path = match.group("path")
        checkout = path if path.startswith(("/", "~")) else f"~/{path}"
        return Target(match.group("dest"), checkout, None, "", f"git remote {PI_REMOTE!r}")
    raise UsageError(f"git remote {PI_REMOTE!r} is not an SSH URL: {url}")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True,
    )


def _home_relative(path: str) -> str:
    """Relative paths are relative to the SSH user's home, as in scp syntax."""

    return path if path.startswith(("/", "~")) else f"~/{path}"


def push_url(destination: str, port: Optional[int], checkout: str) -> str:
    """The ``ssh://`` URL of exactly the checkout that SSH commands act on."""

    netloc = destination + (f":{port}" if port is not None else "")
    return f"ssh://{netloc}{checkout if checkout.startswith('/') else '/' + checkout}"


def resolve_target(host: Optional[str], checkout: Optional[str],
                   environ: Optional[dict[str, str]] = None,
                   git: Callable[..., Any] = _git) -> Target:
    """``--host`` > ``RGBMATRIX_PI_HOST`` > the ``pi`` git remote (D-002).

    The checkout is ``--checkout`` > ``RGBMATRIX_PI_CHECKOUT`` > the remote's path >
    ``~/rgb_Matrix``. The push URL is always built from the final destination and
    checkout, so the pushed repository is the one that is checked out.
    """

    environ = os.environ if environ is None else environ
    path_override = checkout or environ.get(CHECKOUT_ENV)
    if host or environ.get(HOST_ENV):
        destination, port = host or environ[HOST_ENV], None
        path = path_override or DEFAULT_CHECKOUT
        source = "--host" if host else HOST_ENV
    else:
        remote = git("remote", "get-url", PI_REMOTE)
        if remote.returncode != 0 or not remote.stdout.strip():
            raise UsageError(
                f"no Pi target: pass --host, set {HOST_ENV}, or add a git remote named"
                f" {PI_REMOTE!r}"
            )
        parsed = parse_remote_url(remote.stdout)
        destination, port, source = parsed.destination, parsed.port, parsed.source
        path = path_override or parsed.checkout
    path = _home_relative(path)
    return Target(destination, path, port, push_url(destination, port, path), source)


def resolve_local_commit(ref: str, git: Callable[..., Any] = _git) -> str:
    resolved = git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if resolved.returncode != 0 or not resolved.stdout.strip():
        raise UsageError(f"{ref!r} is not a commit in {REPO_ROOT}")
    return resolved.stdout.strip()


def remote_command(target: Target, args: Sequence[str], *,
                   keepalive: bool = False) -> list[str]:
    """The SSH argv. The remote shell sees one quoted ``python3 - ...`` command."""

    remote_args = ["--checkout", target.checkout, *args]
    return [*target.ssh_argv(keepalive=keepalive),
            "python3 - " + " ".join(shlex.quote(a) for a in remote_args)]


def run_remote(target: Target, args: Sequence[str], *,
               popen: Callable[..., Any] = subprocess.Popen,
               out: Any = None, keepalive: bool = False) -> tuple[int, Optional[dict[str, Any]]]:
    """Stream progress lines, return the remote exit code and its result document."""

    out = out or sys.stdout
    script = REMOTE_SCRIPT.read_bytes()
    process = popen(
        remote_command(target, args, keepalive=keepalive),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=None, text=False,
    )
    process.stdin.write(script)
    process.stdin.close()
    result: Optional[dict[str, Any]] = None
    for raw in process.stdout:
        line = raw.decode("utf-8", "replace").rstrip("\n")
        if line.startswith(RESULT_MARKER):
            try:
                result = json.loads(line[len(RESULT_MARKER):])
            except ValueError:
                out.write("could not parse the Pi's result line\n")
            continue
        out.write(f"  pi | {line}\n")
        out.flush()
    returncode = process.wait()
    if result is None and returncode in (EXIT_OK, EXIT_GATE_FAILED):
        # SSH or the remote interpreter failed without a result document.
        returncode = EXIT_OPERATION_FAILED
    return returncode, result


def _positive_float(text: str) -> float:
    value = float(text)
    if not (math.isfinite(value) and value > 0):
        raise argparse.ArgumentTypeError("must be a finite number > 0")
    return value


def _nonnegative_float(text: str) -> float:
    value = float(text)
    if not (math.isfinite(value) and value >= 0):
        raise argparse.ArgumentTypeError("must be a finite number >= 0")
    return value


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return value


def _modes(text: str) -> list[str]:
    modes = [mode.strip() for mode in text.split(",") if mode.strip()]
    unknown = sorted(set(modes) - set(OUTPUT_MODES))
    if not modes or unknown:
        raise argparse.ArgumentTypeError(
            f"modes must be a comma list of {', '.join(OUTPUT_MODES)}"
        )
    return modes


def _run_plan(text: str) -> list[tuple[str, str, float]]:
    """Parse ``source:mode:seconds`` entries for one mixed-source check."""

    runs = []
    for entry in (item.strip() for item in text.split(",")):
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 3:
            raise argparse.ArgumentTypeError(
                "run plan entries must be source:mode:seconds"
            )
        source, mode, duration_text = parts
        if source not in AUDIO_SOURCES:
            raise argparse.ArgumentTypeError(
                f"run plan source must be one of: {', '.join(AUDIO_SOURCES)}"
            )
        if mode not in OUTPUT_MODES:
            raise argparse.ArgumentTypeError(
                f"run plan mode must be one of: {', '.join(OUTPUT_MODES)}"
            )
        try:
            duration = _positive_float(duration_text)
        except (ValueError, argparse.ArgumentTypeError) as exc:
            raise argparse.ArgumentTypeError(
                "run plan duration must be a finite number > 0"
            ) from exc
        runs.append((source, mode, duration))
    if not runs:
        raise argparse.ArgumentTypeError("run plan must contain at least one run")
    return runs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pi_cycle.py",
        description="Scripted Pi cycle: status, deploy, check, restore.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", help=f"SSH destination. Default: ${HOST_ENV}, then"
                        f" the {PI_REMOTE!r} git remote.")
    parser.add_argument("--checkout", help=f"Pi checkout path. Default: ${CHECKOUT_ENV},"
                        f" the remote's path, or {DEFAULT_CHECKOUT}.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    sub.add_parser("status", help="Read-only: service, checkout, temperature, throttling.")
    deploy = sub.add_parser("deploy", help="Push a commit and check it out on the Pi.")
    deploy.add_argument("sha")
    check = sub.add_parser(
        "check", help="Bounded telemetry runs on the deployed commit, with gates.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    check.add_argument("sha", help="Must equal the Pi's detached HEAD.")
    check.add_argument("--modes", type=_modes, default="direct,pillow-bulk,direct",
                       help="Comma list of RGBMATRIX_FFT_OUTPUT_PATH values, run in order.")
    check.add_argument("--duration", type=_positive_float, default=30.0,
                       help="Measured seconds per run.")
    check.add_argument(
        "--run-plan",
        type=_run_plan,
        help="Mixed-source sequence as source:mode:seconds entries; overrides"
             " --modes and --duration.",
    )
    check.add_argument("--settle", type=_nonnegative_float, default=5.0,
                       help="Unmeasured warm-up seconds per run.")
    check.add_argument("--max-samples", type=_positive_int, default=100000,
                       help="Telemetry frame capacity per run.")
    restore = sub.add_parser("restore", help="Check out a commit already on the Pi.")
    restore.add_argument("ref", help="Resolved on the Pi, so Pi-only branches work.")
    measure = sub.add_parser(
        "measure-split",
        help="Step 4 v2: the approved eight-run baseline/candidate matrix-process"
             " sequence (report-only).",
    )
    measure.add_argument("baseline", help="In-process baseline commit.")
    measure.add_argument("candidate", help="Split candidate commit; must descend from"
                         " the baseline, and is the only commit pushed.")
    measure.add_argument("--expect-start", required=True,
                         help="Commit the Pi must be at before, and is restored to after.")
    measure.add_argument("--audio-note", required=True,
                         help="Description of the fixed audio source and level.")
    return parser


def _print(out: Any, text: str = "") -> None:
    out.write(text + "\n")
    out.flush()


def main(argv: Optional[Sequence[str]] = None, *,
         environ: Optional[dict[str, str]] = None,
         git: Callable[..., Any] = _git,
         popen: Callable[..., Any] = subprocess.Popen,
         run: Callable[..., Any] = subprocess.run,
         out: Any = None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        target = resolve_target(args.host, args.checkout, environ, git)
        _print(out, f"Pi: {target.destination}:{target.checkout} (from {target.source})")

        if args.command == "status":
            code, _ = run_remote(target, ["status"], popen=popen, out=out)
            return code

        if args.command == "deploy":
            sha = resolve_local_commit(args.sha, git)
            _print(out, f"pushing {sha} to {DEPLOY_REF}")
            pushed = run(["git", "-C", str(REPO_ROOT), "push", target.push_url,
                          f"+{sha}:{DEPLOY_REF}"])
            if pushed.returncode != 0:
                _print(out, "push failed; the Pi was not changed")
                return EXIT_REFUSED
            code, result = run_remote(target, ["checkout", sha, "--expect-sha", sha],
                                      popen=popen, out=out)
            return _report_checkout(out, code, result)

        if args.command == "measure-split":
            return _measure_split(args, target, git, popen, run, out)

        if args.command == "restore":
            code, result = run_remote(target, ["checkout", args.ref], popen=popen, out=out)
            return _report_checkout(out, code, result)

        # Resolved here, not on the Pi, so "HEAD" or a branch name means the Mac's
        # commit and can't trivially match whatever is deployed (D-005).
        sha = resolve_local_commit(args.sha, git)
        check_args = [
            "check", sha,
            "--modes", ",".join(args.modes),
            "--settle", f"{args.settle:g}",
            "--duration", f"{args.duration:g}",
            "--max-samples", str(args.max_samples),
        ]
        if args.run_plan is not None:
            check_args.extend([
                "--run-plan",
                ",".join(
                    f"{source}:{mode}:{duration:g}"
                    for source, mode, duration in args.run_plan
                ),
            ])
        code, result = run_remote(target, check_args, popen=popen, out=out)
        if result and result.get("results_markdown"):
            _print(out)
            out.write(result["results_markdown"])
            _print(out, f"Evidence (on the Pi): {result['evidence_dir']}")
        return code
    except UsageError as exc:
        _print(out, f"refused: {exc}")
        return EXIT_REFUSED
    except KeyboardInterrupt:
        if args.command == "measure-split":
            _print(out, "\ninterrupted. The Pi side keeps going: it finishes the run in"
                   " progress, stops the sequence, restores the preflight commit, restarts"
                   " the service unless a process survives, and keeps its evidence. Run"
                   " `status` to see the Pi.")
        else:
            _print(out, "\ninterrupted. The Pi side keeps going: a check finishes the run"
                   " in progress, skips the rest, restarts the service, and keeps its"
                   " evidence. Run `status` to see the Pi.")
        return 130


def _measure_split(args: Any, target: Target, git: Callable[..., Any],
                   popen: Callable[..., Any], run: Callable[..., Any], out: Any) -> int:
    """Read-only preflight, one push of the candidate, then the Pi-side sequence."""

    baseline = resolve_local_commit(args.baseline, git)
    candidate = resolve_local_commit(args.candidate, git)
    expect_start = resolve_local_commit(args.expect_start, git)
    if baseline == candidate:
        raise UsageError("baseline and candidate are the same commit")
    if git("merge-base", "--is-ancestor", baseline, candidate).returncode != 0:
        raise UsageError("the baseline must be an ancestor of the candidate (A-6)")
    tools_dirty = git("status", "--porcelain", "--", "tools/pi")
    if tools_dirty.returncode != 0 or tools_dirty.stdout.strip():
        raise UsageError("tools/pi has uncommitted changes; commit the tool first (A-7)")
    tool_commit = resolve_local_commit("HEAD", git)
    tool_sha256 = hashlib.sha256(REMOTE_SCRIPT.read_bytes()).hexdigest()

    code, result = run_remote(target, ["status"], popen=popen, out=out)
    status = (result or {}).get("status") or {}
    if code != EXIT_OK or status.get("head") != expect_start or not status.get("detached") \
            or status.get("porcelain") != []:
        _print(out, f"refused: the Pi is not clean and detached at {expect_start};"
               " nothing was pushed")
        return EXIT_REFUSED

    _print(out, f"pushing {candidate} to {DEPLOY_REF}")
    pushed = run(["git", "-C", str(REPO_ROOT), "push", target.push_url,
                  f"+{candidate}:{DEPLOY_REF}"])
    if pushed.returncode != 0:
        _print(out, "push failed; the Pi was not changed")
        return EXIT_REFUSED
    code, result = run_remote(target, [
        "measure-split", baseline, candidate,
        "--expect-start", expect_start,
        "--settle", f"{MEASURE_SETTLE_SECONDS:g}",
        "--duration", f"{MEASURE_DURATION_SECONDS:g}",
        "--max-samples", str(MEASURE_MAX_SAMPLES),
        "--tool-commit", tool_commit,
        "--tool-sha256", tool_sha256,
        "--audio-note", args.audio_note,
    ], popen=popen, out=out, keepalive=True)
    if result and result.get("results_markdown"):
        _print(out)
        out.write(result["results_markdown"])
        _print(out, f"Evidence (on the Pi): {result['evidence_dir']}")
    return code


def _report_checkout(out: Any, code: int, result: Optional[dict[str, Any]]) -> int:
    if not result or result.get("refused"):
        return code
    previous = result.get("previous")
    if result.get("changed"):
        _print(out, f"Deployed {result.get('current')}. Previous: {previous}")
    if code != EXIT_OK:
        _print(out, "The Pi is not healthy at the target. No rollback was attempted.")
    if previous and previous != result.get("current"):
        _print(out, f"Roll back with: python3 tools/pi/pi_cycle.py restore {previous}")
    return code


if __name__ == "__main__":
    sys.exit(main())
