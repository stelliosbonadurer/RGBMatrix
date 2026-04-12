#!/usr/bin/env python3
"""Interactive launcher for RGB Matrix Python programs.

This script discovers runnable matrix programs in this workspace and launches
one with standard matrix parameters. It defaults to using sudo and environment
preservation so programs can access system audio and matrix hardware.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProgramEntry:
    alias: str
    rel_path: str
    abs_path: Path


WORKSPACE_ROOT = Path(__file__).resolve().parent
SEARCH_ROOTS = [
    WORKSPACE_ROOT / "my_programs",
    WORKSPACE_ROOT / "rpi-rgb-led-matrix" / "bindings" / "python" / "samples",
]

EXCLUDED_FILENAMES = {
    "__init__.py",
    "samplebase.py",
    "setup.py",
}

RUN_MARKERS = (
    'if __name__ == "__main__"',
    "if __name__ == '__main__'",
    "SampleBase",
    "MatrixApp(",
)

DEFAULT_PYTHON_CANDIDATES = [
    Path("~/rpi-rgb-led-matrix/bindings/python/samples/fftdemo/.venv/bin/python3").expanduser(),
    WORKSPACE_ROOT / "rpi-rgb-led-matrix" / "bindings" / "python" / "samples" / "fftdemo" / ".venv" / "bin" / "python3",
]


def is_runnable_program(file_path: Path) -> bool:
    if file_path.name in EXCLUDED_FILENAMES:
        return False

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False

    return any(marker in content for marker in RUN_MARKERS)


def make_alias(rel_path: str) -> str:
    no_ext = rel_path[:-3] if rel_path.endswith(".py") else rel_path
    return no_ext.replace("/", ".")


def discover_programs() -> list[ProgramEntry]:
    programs: list[ProgramEntry] = []

    for root in SEARCH_ROOTS:
        if not root.exists():
            continue

        for file_path in sorted(root.rglob("*.py")):
            if "__pycache__" in file_path.parts:
                continue
            if not is_runnable_program(file_path):
                continue

            rel_path = file_path.relative_to(WORKSPACE_ROOT).as_posix()
            programs.append(
                ProgramEntry(
                    alias=make_alias(rel_path),
                    rel_path=rel_path,
                    abs_path=file_path,
                )
            )

    return programs


def print_programs(programs: list[ProgramEntry]) -> None:
    print("\nDiscovered runnable programs:\n")
    for idx, program in enumerate(programs, start=1):
        print(f"{idx:2d}. {program.alias}")
        print(f"    {program.rel_path}")


def choose_program_interactive(programs: list[ProgramEntry]) -> ProgramEntry:
    print_programs(programs)
    print("\nChoose a program by number, alias, or relative path.")

    while True:
        raw = input("Selection: ").strip()
        if not raw:
            continue

        selected = resolve_program(raw, programs)
        if selected:
            return selected

        print("Invalid selection. Try again.")


def resolve_program(token: str, programs: list[ProgramEntry]) -> ProgramEntry | None:
    token = token.strip()
    if not token:
        return None

    if token.isdigit():
        idx = int(token) - 1
        if 0 <= idx < len(programs):
            return programs[idx]

    for program in programs:
        if token == program.alias or token == program.rel_path:
            return program

    matches = [p for p in programs if token in p.alias or token in p.rel_path]
    if len(matches) == 1:
        return matches[0]

    return None


def resolve_python_executable(user_choice: str | None) -> str:
    if user_choice:
        return str(Path(user_choice).expanduser())

    env_choice = os.environ.get("RGBMATRIX_PYTHON")
    if env_choice:
        return str(Path(env_choice).expanduser())

    for candidate in DEFAULT_PYTHON_CANDIDATES:
        if candidate.exists():
            return str(candidate)

    return sys.executable


def build_led_args(args: argparse.Namespace) -> list[str]:
    return [
        f"--led-rows={args.led_rows}",
        f"--led-cols={args.led_cols}",
        f"--led-gpio-mapping={args.led_gpio_mapping}",
        f"--led-slowdown-gpio={args.led_slowdown_gpio}",
    ]


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description="Launch RGB matrix programs with shared matrix options.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--program", help="Program alias, index, or path.")
    parser.add_argument("--list", action="store_true", help="List discovered programs and exit.")
    parser.add_argument("--python", dest="python_executable", help="Python executable for target program.")
    parser.add_argument("--no-sudo", action="store_true", help="Run without sudo -E.")
    parser.add_argument("--dry-run", action="store_true", help="Print command and exit without running.")

    parser.add_argument("--led-rows", type=int, default=64)
    parser.add_argument("--led-cols", type=int, default=64)
    parser.add_argument("--led-gpio-mapping", default="adafruit-hat")
    parser.add_argument("--led-slowdown-gpio", type=int, default=2)

    parser.add_argument(
        "--no-led-defaults",
        action="store_true",
        help="Do not inject default LED args; pass only launcher passthrough args.",
    )

    args, passthrough = parser.parse_known_args()
    return args, passthrough


def main() -> int:
    args, passthrough = parse_args()
    programs = discover_programs()

    if not programs:
        print("No runnable Python RGB matrix programs found.")
        return 1

    if args.list:
        print_programs(programs)
        return 0

    selected = resolve_program(args.program, programs) if args.program else None
    if not selected:
        selected = choose_program_interactive(programs)

    python_exec = resolve_python_executable(args.python_executable)

    command: list[str] = []
    if not args.no_sudo:
        command.extend(["sudo", "-E"])

    command.extend([python_exec, str(selected.abs_path)])

    if not args.no_led_defaults:
        command.extend(build_led_args(args))

    command.extend(passthrough)

    print("\nLaunching:")
    print(shlex.join(command))
    print(f"Working directory: {selected.abs_path.parent}\n")

    if args.dry_run:
        return 0

    try:
        completed = subprocess.run(command, cwd=str(selected.abs_path.parent), check=False)
        return completed.returncode
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
