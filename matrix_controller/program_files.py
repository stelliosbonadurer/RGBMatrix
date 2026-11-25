"""Program config files saved on the Pi (web-control-refactor D-039, D-043–D-047).

A program may register one config file (``ProgramFileSpec``). Its tracked file is
the default and is never written. The pages save an override as one versioned JSON
file per program in the service's state directory, and every launch of that
program receives the override in ``RGB_MATRIX_PROGRAM_CONFIG``.

The program's own parser is the only check (D-047). A saved file that can't be
read, or that the parser no longer accepts, is reported and never rewritten:
launches use the tracked default, a save is refused, and ``remove_unusable``
(``DELETE``) is the one way to clear it. Restoring the default is an ordinary
save of the default document, so revisions only rise while a file exists.
"""

from __future__ import annotations

import json
import os
import threading
from http import HTTPStatus
from pathlib import Path
from typing import Any, Mapping

from .boot_code import DEFAULT_STATE_DIRECTORY
from .registry import Registry
from .schema import ProgramFileSpec, ValidationError
from .state_files import write_bytes_atomically


PROGRAM_FILE_VERSION = 1
DEFAULT_PROGRAM_FILES_DIRECTORY = DEFAULT_STATE_DIRECTORY / "program-files"
_FILE_FIELDS = frozenset({"version", "revision", "document"})


class ProgramFileError(Exception):
    """A program-file failure with a stable HTTP status and ``code``."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: HTTPStatus,
        current_revision: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status = status
        self.current_revision = current_revision

    def as_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": str(self), "code": self.code}
        if self.current_revision is not None:
            body["current_revision"] = self.current_revision
        return body


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def _unavailable(message: str) -> ProgramFileError:
    return ProgramFileError(
        "program_file_unavailable", message, status=HTTPStatus.SERVICE_UNAVAILABLE
    )


class ProgramFileStore:
    """Thread-safe read, whole-document replace, and recovery of program files."""

    def __init__(
        self,
        registry: Registry,
        directory: str | os.PathLike[str] = DEFAULT_PROGRAM_FILES_DIRECTORY,
    ):
        self.registry = registry
        self.directory = Path(directory)
        self._lock = threading.Lock()

    def snapshot(self, program_id: str, name: str) -> dict[str, Any]:
        """Return the document the program page renders (``GET``)."""

        spec = self._spec(program_id, name)
        default = self._tracked_default(spec)
        with self._lock:
            revision, stored, error = self._read_locked(program_id, spec)
        return self._snapshot(program_id, spec, revision, stored, error, default)

    def replace(self, program_id: str, name: str, payload: Any) -> dict[str, Any]:
        """Save a whole document at ``expected_revision`` (``PUT``)."""

        spec = self._spec(program_id, name)
        default = self._tracked_default(spec)
        with self._lock:
            revision, _stored, error = self._read_locked(program_id, spec)
            if error is not None:
                raise _unavailable(
                    f"{error}; launches use the default until it is removed"
                )
            if not isinstance(payload, Mapping) or set(payload) != {
                "expected_revision",
                "document",
            }:
                raise self._invalid("request must contain expected_revision and document only")
            expected = payload["expected_revision"]
            if type(expected) is not int or expected < 0:
                raise self._invalid("expected_revision must be a non-negative integer")
            if expected != revision:
                raise ProgramFileError(
                    "revision_conflict",
                    f"{spec.label} changed since it was loaded",
                    status=HTTPStatus.CONFLICT,
                    current_revision=revision,
                )
            document = payload["document"]
            try:
                spec.validate(document)
            except ValidationError as exc:
                raise self._invalid(str(exc)) from exc
            new_revision = revision + 1
            try:
                encoded = json.dumps(
                    {
                        "version": PROGRAM_FILE_VERSION,
                        "revision": new_revision,
                        "document": document,
                    },
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8") + b"\n"
            except (TypeError, ValueError) as exc:
                raise self._invalid("document must be plain JSON") from exc
            try:
                write_bytes_atomically(self._path(program_id, spec), encoded)
            except OSError as exc:
                raise _unavailable(f"{spec.label} can't be written") from exc
            return self._snapshot(program_id, spec, new_revision, document, None, default)

    def remove_unusable(self, program_id: str, name: str) -> dict[str, Any]:
        """Delete a saved file that can't be used (``DELETE``, D-047).

        A usable file is never deleted: the page restores the default by saving
        it, so the revision keeps rising. After a removal the revision restarts
        at 0, the same accepted risk as a manual reset of the matrix settings.
        """

        spec = self._spec(program_id, name)
        default = self._tracked_default(spec)
        with self._lock:
            _revision, _stored, error = self._read_locked(program_id, spec)
            if error is None:
                raise ProgramFileError(
                    "program_file_usable",
                    f"{spec.label} is usable; save the default document to restore it",
                    status=HTTPStatus.CONFLICT,
                )
            try:
                self._path(program_id, spec).unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise _unavailable(f"{spec.label} can't be removed") from exc
            return self._snapshot(program_id, spec, 0, None, None, default)

    def launch_files(self, program_id: str) -> dict[str, tuple[int | None, Any]]:
        """Return ``{name: (revision, document)}`` for one launch.

        Each pair comes from one locked read (critic C-5), so the revision the
        supervisor reports is the one the child received. ``document`` is None
        when launches use the tracked default: revision 0 with no saved file,
        ``None`` when the saved file is unusable.
        """

        spec = self.registry.get(program_id)
        files: dict[str, tuple[int | None, Any]] = {}
        with self._lock:
            for program_file in spec.program_files:
                revision, stored, _error = self._read_locked(program_id, program_file)
                files[program_file.name] = (revision, stored)
        return files

    def _spec(self, program_id: str, name: str) -> ProgramFileSpec:
        try:
            return self.registry.get(program_id).program_file(name)
        except (ValidationError, KeyError) as exc:
            raise ProgramFileError(
                "not_found", "program file not found", status=HTTPStatus.NOT_FOUND
            ) from exc

    @staticmethod
    def _invalid(message: str) -> ProgramFileError:
        return ProgramFileError("invalid_program_file", message, status=HTTPStatus.BAD_REQUEST)

    def _path(self, program_id: str, spec: ProgramFileSpec) -> Path:
        # Both parts are code-owned registry names; request text only selects them.
        return self.directory / f"{program_id}.{spec.name}.json"

    @staticmethod
    def _tracked_default(spec: ProgramFileSpec) -> Any:
        try:
            return json.loads(
                spec.default_path.read_text(encoding="utf-8"), parse_constant=_reject_constant
            )
        except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise _unavailable(f"the default {spec.label.lower()} file can't be read") from exc

    def _snapshot(
        self,
        program_id: str,
        spec: ProgramFileSpec,
        revision: int | None,
        stored: Any,
        error: str | None,
        default: Any,
    ) -> dict[str, Any]:
        return {
            "program_id": program_id,
            "name": spec.name,
            "label": spec.label,
            "revision": revision,
            "source": "stored" if stored is not None else "default",
            "document": stored if stored is not None else default,
            "default": default,
            "error": error,
            "fields": [dict(field) for field in spec.fields],
        }

    def _read_locked(
        self, program_id: str, spec: ProgramFileSpec
    ) -> tuple[int | None, Any, str | None]:
        """Return ``(revision, document, error)``; a missing file is revision 0.

        An unusable file has no revision, so nothing can be saved over it.
        """

        label = spec.label.lower()
        try:
            raw = self._path(program_id, spec).read_bytes()
        except FileNotFoundError:
            return 0, None, None
        except OSError:
            return None, None, f"the saved {label} file can't be read"
        try:
            document = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError, RecursionError):
            # RecursionError: deeply nested input; not a ValueError (review finding 3).
            return None, None, f"the saved {label} file is not valid JSON"
        if not isinstance(document, dict) or set(document) != _FILE_FIELDS:
            return None, None, f"the saved {label} file has an unknown layout"
        version = document["version"]
        if type(version) is not int or version != PROGRAM_FILE_VERSION:
            return None, None, f"the saved {label} file has an unsupported version"
        revision = document["revision"]
        if type(revision) is not int or revision < 1:
            return None, None, f"the saved {label} file has an invalid revision"
        try:
            spec.validate(document["document"])
        except ValidationError as exc:
            return None, None, f"the saved {label} file is no longer valid: {exc}"
        return revision, document["document"], None
