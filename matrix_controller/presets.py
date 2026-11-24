"""FFT presets saved on the Pi (web-control-refactor D-008, D-024, D-025).

A preset is a name plus the two things ``POST /api/v1/run`` accepts for FFT: launch
``parameters`` and a layer ``structure``. The store is one versioned JSON file in
the controller's state directory. Every operation re-reads the file under one lock,
and every change replaces the file atomically. A file that can't be read as this
version is reported and never rewritten, so a downgrade or a hand edit can't lose
presets silently.

The store doesn't import FFT code. The caller supplies the two validators: the FFT
spec's ``validate_parameters`` and the web layer's launch-structure bounds. Layer
contents are checked only at launch, by the child (D-016 ``launch_error``).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
import unicodedata
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable, Mapping

from .boot_code import DEFAULT_STATE_DIRECTORY


PRESET_FILE_VERSION = 1
MAX_PRESETS = 32
MAX_PRESET_NAME_LENGTH = 64
DEFAULT_PRESET_FILE = DEFAULT_STATE_DIRECTORY / "fft-presets.json"
_PRESET_ID_PATTERN = re.compile(r"[0-9a-f]{16}")
_PRESET_FIELDS = frozenset({"preset_id", "name", "revision", "parameters", "structure"})

ParameterValidator = Callable[[Mapping[str, Any]], Mapping[str, Any]]
StructureValidator = Callable[[Any], Mapping[str, Any]]


class PresetError(Exception):
    """A preset operation failure with a stable HTTP status and ``code``."""

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


def _invalid(message: str) -> PresetError:
    return PresetError("invalid_preset", message, status=HTTPStatus.BAD_REQUEST)


def _unavailable(message: str) -> PresetError:
    return PresetError(
        "preset_store_unavailable", message, status=HTTPStatus.SERVICE_UNAVAILABLE
    )


def _not_found() -> PresetError:
    return PresetError("preset_not_found", "preset not found", status=HTTPStatus.NOT_FOUND)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def normalize_preset_name(value: Any) -> str:
    """Return the trimmed name, or raise ``invalid_preset``."""

    if type(value) is not str:
        raise _invalid("name must be a string")
    name = value.strip()
    if not name:
        raise _invalid("name must not be empty")
    if len(name) > MAX_PRESET_NAME_LENGTH:
        raise _invalid(f"name must be at most {MAX_PRESET_NAME_LENGTH} characters")
    if any(unicodedata.category(character) == "Cc" for character in name):
        raise _invalid("name must not contain control characters")
    return name


def _revision(value: Any, *, label: str) -> int:
    if type(value) is not int or value < 1:
        raise _invalid(f"{label} must be a positive integer")
    return value


class PresetStore:
    """Thread-safe create, read, replace, and delete over one preset file."""

    def __init__(
        self,
        path: str | os.PathLike[str] = DEFAULT_PRESET_FILE,
        *,
        validate_parameters: ParameterValidator,
        validate_structure: StructureValidator,
        id_factory: Callable[[], str] | None = None,
    ):
        self.path = Path(path)
        self._validate_parameters = validate_parameters
        self._validate_structure = validate_structure
        self._id_factory = id_factory or (lambda: secrets.token_hex(8))
        self._lock = threading.Lock()

    @staticmethod
    def limits() -> dict[str, int]:
        return {"max_presets": MAX_PRESETS, "max_name_length": MAX_PRESET_NAME_LENGTH}

    def list(self) -> dict[str, Any]:
        with self._lock:
            presets = self._load_locked()
        return {
            "presets": [
                {
                    "preset_id": preset["preset_id"],
                    "name": preset["name"],
                    "revision": preset["revision"],
                }
                for preset in presets
            ],
            "limits": self.limits(),
        }

    def get(self, preset_id: str) -> dict[str, Any]:
        with self._lock:
            presets = self._load_locked()
            return dict(presets[self._index_locked(presets, preset_id)])

    # Each change reads the file before validating the request, so an unreadable
    # store answers 503 whatever the body holds (criterion 4).

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            presets = self._load_locked()
            name, parameters, structure = self._content(payload, allowed=set())
            self._require_unique_name(presets, name, exclude=None)
            if len(presets) >= MAX_PRESETS:
                raise PresetError(
                    "limit_exceeded",
                    f"at most {MAX_PRESETS} presets can be saved",
                    status=HTTPStatus.BAD_REQUEST,
                )
            preset_id = self._new_id_locked(presets)
            preset = {
                "preset_id": preset_id,
                "name": name,
                "revision": 1,
                "parameters": parameters,
                "structure": structure,
            }
            self._write_locked([*presets, preset])
            return dict(preset)

    def replace(self, preset_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            presets = self._load_locked()
            name, parameters, structure = self._content(
                payload, allowed={"expected_revision"}
            )
            expected = _revision(payload.get("expected_revision"), label="expected_revision")
            index = self._index_locked(presets, preset_id)
            self._require_revision(presets[index], expected)
            self._require_unique_name(presets, name, exclude=preset_id)
            preset = {
                "preset_id": preset_id,
                "name": name,
                "revision": presets[index]["revision"] + 1,
                "parameters": parameters,
                "structure": structure,
            }
            updated = list(presets)
            updated[index] = preset
            self._write_locked(updated)
            return dict(preset)

    def delete(self, preset_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            presets = self._load_locked()
            if not isinstance(payload, Mapping) or set(payload) != {"expected_revision"}:
                raise _invalid("request must contain expected_revision only")
            expected = _revision(payload["expected_revision"], label="expected_revision")
            index = self._index_locked(presets, preset_id)
            self._require_revision(presets[index], expected)
            self._write_locked(presets[:index] + presets[index + 1 :])
        return {"deleted": True, "preset_id": preset_id}

    def _content(
        self, payload: Mapping[str, Any], *, allowed: set[str]
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Validate the client-supplied content of a create or replace."""

        if not isinstance(payload, Mapping):
            raise _invalid("request body must be a JSON object")
        unknown = set(payload) - {"name", "parameters", "structure", *allowed}
        if unknown:
            raise _invalid(f"unknown preset fields: {', '.join(sorted(unknown))}")
        missing = {"name", "structure", *allowed} - set(payload)
        if missing:
            raise _invalid(f"missing preset fields: {', '.join(sorted(missing))}")
        name = normalize_preset_name(payload["name"])
        supplied = payload.get("parameters", {})
        if not isinstance(supplied, Mapping):
            raise _invalid("parameters must be a JSON object")
        try:
            normalized = self._validate_parameters(supplied)
            structure = self._validate_structure(payload["structure"])
        except ValueError as exc:  # ValidationError is a ValueError
            raise _invalid(str(exc)) from exc
        # D-025: validate the full launch set, keep only what the user chose, so a
        # preset doesn't pin today's defaults.
        parameters = {key: normalized[key] for key in supplied}
        return name, parameters, dict(structure)

    @staticmethod
    def _index_locked(presets: list[dict[str, Any]], preset_id: Any) -> int:
        for index, preset in enumerate(presets):
            if preset["preset_id"] == preset_id:
                return index
        raise _not_found()

    @staticmethod
    def _require_revision(preset: Mapping[str, Any], expected: int) -> None:
        if preset["revision"] != expected:
            raise PresetError(
                "revision_conflict",
                "preset changed since it was loaded",
                status=HTTPStatus.CONFLICT,
                current_revision=preset["revision"],
            )

    @staticmethod
    def _require_unique_name(
        presets: list[dict[str, Any]], name: str, *, exclude: str | None
    ) -> None:
        folded = name.casefold()
        for preset in presets:
            if preset["preset_id"] != exclude and preset["name"].casefold() == folded:
                raise PresetError(
                    "name_conflict",
                    "a preset with this name already exists",
                    status=HTTPStatus.CONFLICT,
                )

    def _new_id_locked(self, presets: list[dict[str, Any]]) -> str:
        existing = {preset["preset_id"] for preset in presets}
        for _ in range(8):
            candidate = self._id_factory()
            if _PRESET_ID_PATTERN.fullmatch(candidate) and candidate not in existing:
                return candidate
        raise RuntimeError("could not generate a unique preset ID")

    def _load_locked(self) -> list[dict[str, Any]]:
        """Read and shape-check the file; a missing file is an empty store."""

        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise _unavailable("preset file can't be read") from exc
        try:
            # NaN and Infinity aren't JSON; Python accepts them unless told not to.
            document = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError) as exc:
            raise _unavailable("preset file is not valid JSON") from exc
        if not isinstance(document, dict) or set(document) != {"version", "presets"}:
            raise _unavailable("preset file has an unknown layout")
        if document["version"] != PRESET_FILE_VERSION or type(document["version"]) is not int:
            raise _unavailable("preset file has an unsupported version")
        presets = document["presets"]
        if not isinstance(presets, list) or len(presets) > MAX_PRESETS:
            raise _unavailable("preset file has an invalid preset list")
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        for preset in presets:
            if not self._stored_preset_is_valid(preset):
                raise _unavailable("preset file contains an invalid preset")
            folded = preset["name"].casefold()
            if preset["preset_id"] in seen_ids or folded in seen_names:
                raise _unavailable("preset file contains duplicate presets")
            seen_ids.add(preset["preset_id"])
            seen_names.add(folded)
        return presets

    @staticmethod
    def _stored_preset_is_valid(preset: Any) -> bool:
        if not isinstance(preset, dict) or set(preset) != _PRESET_FIELDS:
            return False
        preset_id = preset["preset_id"]
        revision = preset["revision"]
        if type(preset_id) is not str or not _PRESET_ID_PATTERN.fullmatch(preset_id):
            return False
        if type(revision) is not int or revision < 1:
            return False
        if not isinstance(preset["parameters"], dict) or not isinstance(
            preset["structure"], dict
        ):
            return False
        try:
            return normalize_preset_name(preset["name"]) == preset["name"]
        except PresetError:
            return False

    def _write_locked(self, presets: list[dict[str, Any]]) -> None:
        """Replace the file atomically: same-directory temp file, fsync, rename."""

        try:
            encoded = json.dumps(
                {"version": PRESET_FILE_VERSION, "presets": presets},
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8") + b"\n"
        except ValueError:
            # The stored presets load without NaN or Infinity, so this one came from
            # the request; nothing has been written yet.
            raise _invalid("preset values must be finite numbers") from None
        directory = self.path.parent
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise _unavailable("preset directory can't be created") from exc
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=directory,
                prefix=f".{self.path.name}.",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                os.chmod(temporary.fileno(), 0o600)
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self.path)
            temporary_name = None
            self._fsync_directory(directory)
        except OSError as exc:
            raise _unavailable("preset file can't be written") from exc
        finally:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink()
                except FileNotFoundError:
                    pass

    @staticmethod
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
