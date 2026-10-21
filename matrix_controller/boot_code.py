"""Create and retrieve the six-digit pairing code for the current Pi boot.

The code is stored beside the service's other runtime state and tagged with Linux's
boot ID. Restarting only the web service therefore preserves the code already shown
on the matrix, while a real reboot atomically replaces it before the UI starts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Callable


DEFAULT_STATE_DIRECTORY = Path(
    os.environ.get("RGBMATRIX_STATE_DIR", "/var/lib/rgb-matrix-controller")
)
DEFAULT_CODE_FILE = DEFAULT_STATE_DIRECTORY / "boot-pairing.json"
BOOT_ID_FILE = Path("/proc/sys/kernel/random/boot_id")


def current_boot_id(path: Path = BOOT_ID_FILE) -> str:
    """Read the kernel boot identifier used to scope the displayed code."""

    boot_id = path.read_text(encoding="ascii").strip()
    if not boot_id:
        raise RuntimeError("kernel boot ID is empty")
    return boot_id


def ensure_boot_code(
    path: Path = DEFAULT_CODE_FILE,
    *,
    boot_id: str | None = None,
    code_factory: Callable[[], str] | None = None,
) -> str:
    """Return this boot's code, creating it with an atomic file replace if needed."""

    expected_boot_id = boot_id or current_boot_id()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        payload = {}

    stored_code = payload.get("code")
    if (
        payload.get("boot_id") == expected_boot_id
        and type(stored_code) is str
        and re.fullmatch(r"\d{6}", stored_code)
    ):
        return stored_code

    factory = code_factory or (lambda: f"{secrets.randbelow(1_000_000):06d}")
    code = factory()
    if re.fullmatch(r"\d{6}", code) is None:
        raise RuntimeError("boot pairing code generator returned an invalid code")

    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        {"boot_id": expected_boot_id, "code": code},
        separators=(",", ":"),
    ) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            os.chmod(temporary.fileno(), 0o600)
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass
    return code


def read_boot_code(path: Path = DEFAULT_CODE_FILE) -> str:
    """Read the current boot code without creating or rotating it."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"could not read boot pairing code from {path}") from exc
    code = payload.get("code")
    if type(code) is not str or re.fullmatch(r"\d{6}", code) is None:
        raise RuntimeError(f"boot pairing code in {path} is invalid")
    return code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print this boot's RGB matrix pairing code")
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_CODE_FILE,
        help="Boot pairing state file",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    # Reading only is intentional: the boot service owns code creation. A typo or a
    # command run before service startup must never silently rotate what is displayed.
    print(read_boot_code(args.path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
