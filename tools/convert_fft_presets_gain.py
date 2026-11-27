#!/usr/bin/env python3
"""Convert saved FFT presets to the gain setting, once (fft-level-controls D-020).

Before fft-level-controls M1 a ``current-bars`` layer had ``sensitivity_scalar``
(bars were divided by it) and ``overflow_multiplier`` (used only when overflow was
on). Both are now one ``gain`` multiplier, so each layer gets

    gain = (overflow_multiplier if overflow else 1) / sensitivity_scalar

using the old defaults (1.5 and 1.0) for a missing key, clamped to 0.1-10
(D-022). Both old keys are removed and each changed preset's revision goes up by
one, so a page holding the old copy gets ``revision_conflict`` instead of saving
over it. Everything else in the file is left as it was.

The original file is kept beside it as ``<name>.pre-gain-<UTC time>.bak``. The new
file is written atomically, mode 0600, with the original owner, so running under
sudo leaves it readable by the web service. A file with nothing to convert is not
touched, so a second run changes nothing. Stop the web service first, or at least
don't save presets while it runs: a save between this script's read and write
would be lost, and the script refuses to write if the file changed meanwhile.

    sudo python3 tools/convert_fft_presets_gain.py --dry-run
    sudo python3 tools/convert_fft_presets_gain.py

This script is temporary: delete it once the Pi's presets are converted.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any


# Mirrors matrix_controller.presets.DEFAULT_PRESET_FILE without importing it, and
# core.layer_plan's gain bounds without importing FFT code (tests check both).
DEFAULT_PRESET_FILE = Path(
    os.environ.get("RGBMATRIX_STATE_DIR", "/var/lib/rgb-matrix-controller")
) / "fft-presets.json"
GAIN_MIN = 0.1
GAIN_MAX = 10.0
GAIN_DEFAULT = 1.5
OLD_SCALAR_DEFAULT = 1.0
OLD_MULTIPLIER_DEFAULT = 1.5
OLD_KEYS = ("sensitivity_scalar", "overflow_multiplier")
LAYER_TYPE = "current-bars"


class ConversionError(Exception):
    """The file can't be converted; nothing was written."""


def _positive(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number > 0 else None


def convert_layer_settings(settings: dict[str, Any]) -> str | None:
    """Convert one layer's settings in place; return a note, or None if unchanged."""

    if not any(key in settings for key in OLD_KEYS):
        return None
    old = {key: settings.pop(key) for key in OLD_KEYS if key in settings}
    if "gain" in settings:
        return f"kept gain {settings['gain']!r}, removed {', '.join(sorted(old))}"
    scalar = _positive(old.get("sensitivity_scalar", OLD_SCALAR_DEFAULT))
    multiplier = _positive(old.get("overflow_multiplier", OLD_MULTIPLIER_DEFAULT))
    if scalar is None or multiplier is None:
        settings["gain"] = GAIN_DEFAULT
        return f"old values {old!r} are not positive numbers; gain reset to {GAIN_DEFAULT:g}"
    gain = (multiplier if settings.get("overflow", True) is True else 1.0) / scalar
    clamped = min(GAIN_MAX, max(GAIN_MIN, gain))
    settings["gain"] = clamped
    if clamped != gain:
        return f"gain {gain:.6g} limited to {clamped:g}"
    return f"gain {gain:.6g}"


def convert_document(document: Any) -> list[str]:
    """Convert a parsed preset file in place; return one line per change."""

    if not isinstance(document, dict) or not isinstance(document.get("presets"), list):
        raise ConversionError("not a preset file: expected {version, presets: [...]}")
    report: list[str] = []
    for preset in document["presets"]:
        if not isinstance(preset, dict):
            raise ConversionError("the preset list holds something that isn't a preset")
        structure = preset.get("structure")
        layers = structure.get("layers") if isinstance(structure, dict) else None
        changed = False
        for index, layer in enumerate(layers if isinstance(layers, list) else []):
            if not isinstance(layer, dict) or layer.get("layer_type") != LAYER_TYPE:
                continue
            settings = layer.get("settings")
            if not isinstance(settings, dict):
                continue
            note = convert_layer_settings(settings)
            if note is not None:
                changed = True
                name = layer.get("layer_id", f"layer {index}")
                report.append(f"{preset.get('name')!s} / {name!s}: {note}")
        if changed:
            revision = preset.get("revision")
            if type(revision) is not int:
                raise ConversionError(f"preset {preset.get('name')!r} has no integer revision")
            preset["revision"] = revision + 1
    return report


def _write_like(path: Path, data: bytes, original: os.stat_result) -> None:
    """Atomically replace ``path`` with ``data``, mode 0600, the original owner."""

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temporary:
            temporary_name = temporary.name
            os.chmod(temporary.fileno(), 0o600)
            if (original.st_uid, original.st_gid) != (os.getuid(), os.getgid()):
                os.chown(temporary.fileno(), original.st_uid, original.st_gid)
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def run(path: Path, *, dry_run: bool = False, now: float | None = None) -> int:
    try:
        raw = path.read_bytes()
        stat = path.stat()
    except FileNotFoundError:
        print(f"{path}: no preset file, nothing to convert")
        return 0
    try:
        document = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        report = convert_document(document)
    except (UnicodeDecodeError, ValueError, ConversionError) as exc:
        print(f"{path}: not converted: {exc}", file=sys.stderr)
        return 1
    if not report:
        print(f"{path}: nothing to convert")
        return 0
    for line in report:
        print(line)
    if dry_run:
        print(f"{path}: dry run, {len(report)} layer(s) would change; nothing written")
        return 0
    encoded = json.dumps(document, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    backup = path.with_name(f"{path.name}.pre-gain-{stamp}.bak")
    if backup.exists():
        print(f"{backup}: already exists; not converted", file=sys.stderr)
        return 1
    _write_like(backup, raw, stat)
    now_stat = path.stat()
    if (
        (now_stat.st_size, now_stat.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns)
        or path.read_bytes() != raw
    ):
        print(f"{path}: changed while converting; not converted (backup kept)", file=sys.stderr)
        return 1
    _write_like(path, encoded, stat)
    print(f"{path}: converted {len(report)} layer(s); original kept as {backup.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, default=DEFAULT_PRESET_FILE,
                        help=f"preset file (default {DEFAULT_PRESET_FILE})")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would change without writing")
    args = parser.parse_args(argv)
    return run(args.file, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
