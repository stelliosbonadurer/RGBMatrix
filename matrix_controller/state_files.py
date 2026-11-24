"""Atomic replacement of the web service's small JSON state files.

Presets (D-025) and the shared matrix settings (web-control-refactor D-035) both
keep one file in the service's state directory. A change is written to a 0600 temp
file in the same directory, flushed, and renamed over the old file, so a reader
sees either the old document or the new one.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_bytes_atomically(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data``, or raise ``OSError`` and leave it unchanged.

    The temp file is removed when anything fails, so a failed write leaves no
    stray file next to the state file.
    """

    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=directory,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            os.chmod(temporary.fileno(), 0o600)
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        _fsync_directory(directory)
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def _fsync_directory(directory: Path) -> None:
    """Persist the rename across power loss where the platform allows it."""

    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
