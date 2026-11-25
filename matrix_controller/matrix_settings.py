"""Shared matrix settings saved on the Pi (web-control-refactor D-035).

The 18 ``led_*`` parameters describe the panel, not a program, so every launch
uses one set of values. They live in one versioned JSON file in the controller's
state directory, edited from the home page. With no file, the values come from the
config file's ``matrix`` block over the code defaults. A file that can't be read
as this version is reported and never rewritten (as D-025); launches then use the
config values, so a bad file can't keep the boot display dark.
"""

from __future__ import annotations

import json
import os
import threading
from http import HTTPStatus
from pathlib import Path
from typing import Any, Mapping

from .boot_code import DEFAULT_STATE_DIRECTORY
from .definitions import MATRIX_PARAMETERS
from .schema import ValidationError
from .state_files import write_bytes_atomically


MATRIX_SETTINGS_FILE_VERSION = 1
DEFAULT_MATRIX_SETTINGS_FILE = DEFAULT_STATE_DIRECTORY / "matrix-settings.json"
# The often-tuned settings, shown first on the home page; the rest go under
# Advanced in definition order (D-035 layout).
COMMON_MATRIX_SETTINGS = (
    "led_brightness",
    "led_rows",
    "led_cols",
    "led_chain",
    "led_parallel",
    "led_gpio_mapping",
    "led_slowdown_gpio",
    "led_pwm_bits",
    "led_pwm_lsb_nanoseconds",
)
_FILE_FIELDS = frozenset({"version", "revision", "values"})
_PARAMETERS_BY_NAME = {parameter.name: parameter for parameter in MATRIX_PARAMETERS}


class MatrixSettingsError(Exception):
    """A matrix-settings failure with a stable HTTP status and ``code``."""

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


def _invalid(message: str) -> MatrixSettingsError:
    return MatrixSettingsError(
        "invalid_matrix_settings", message, status=HTTPStatus.BAD_REQUEST
    )


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def normalize_matrix_values(values: Any) -> dict[str, Any]:
    """Return all 18 values normalized, or raise ``ValidationError``.

    The set must be complete: a stored or submitted document is the whole panel
    description, never a partial overlay.
    """

    if not isinstance(values, Mapping):
        raise ValidationError("matrix values must be a JSON object")
    unknown = sorted(set(values) - set(_PARAMETERS_BY_NAME))
    if unknown:
        raise ValidationError(f"unknown matrix settings: {', '.join(unknown)}")
    missing = [name for name in _PARAMETERS_BY_NAME if name not in values]
    if missing:
        raise ValidationError(f"missing matrix settings: {', '.join(missing)}")
    return {
        name: parameter.normalize(values[name])
        for name, parameter in _PARAMETERS_BY_NAME.items()
    }


def config_matrix_values(matrix_defaults: Mapping[str, Any]) -> dict[str, Any]:
    """Overlay the config file's ``matrix`` block on the code defaults."""

    values = {parameter.name: parameter.default for parameter in MATRIX_PARAMETERS}
    values.update(matrix_defaults)
    return normalize_matrix_values(values)


class MatrixSettingsStore:
    """Thread-safe read and whole-document replace of the matrix settings file."""

    def __init__(
        self,
        path: str | os.PathLike[str] = DEFAULT_MATRIX_SETTINGS_FILE,
        *,
        config_values: Mapping[str, Any],
    ):
        self.path = Path(path)
        self._config_values = normalize_matrix_values(config_values)
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        """Return the document the home page renders (``GET``)."""

        with self._lock:
            revision, stored, error = self._read_locked()
        return self._snapshot(revision, stored, error)

    def effective_values(self) -> dict[str, Any]:
        """Return the values the next launch uses: stored, else the config's."""

        with self._lock:
            _revision, stored, _error = self._read_locked()
        return dict(stored if stored is not None else self._config_values)

    def replace(self, payload: Any) -> dict[str, Any]:
        """Replace every value at ``expected_revision`` (``PUT``)."""

        with self._lock:
            revision, _stored, error = self._read_locked()
            if error is not None:
                raise MatrixSettingsError(
                    "matrix_settings_unavailable",
                    f"{error}; launches use the Pi config values until it is fixed or removed",
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
            if not isinstance(payload, Mapping) or set(payload) != {
                "expected_revision",
                "values",
            }:
                raise _invalid("request must contain expected_revision and values only")
            expected = payload["expected_revision"]
            if type(expected) is not int or expected < 0:
                raise _invalid("expected_revision must be a non-negative integer")
            if expected != revision:
                raise MatrixSettingsError(
                    "revision_conflict",
                    "matrix settings changed since they were loaded",
                    status=HTTPStatus.CONFLICT,
                    current_revision=revision,
                )
            try:
                values = normalize_matrix_values(payload["values"])
            except ValidationError as exc:
                raise _invalid(str(exc)) from exc
            new_revision = revision + 1
            encoded = json.dumps(
                {
                    "version": MATRIX_SETTINGS_FILE_VERSION,
                    "revision": new_revision,
                    "values": values,
                },
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8") + b"\n"
            try:
                write_bytes_atomically(self.path, encoded)
            except OSError as exc:
                raise MatrixSettingsError(
                    "matrix_settings_unavailable",
                    "matrix settings file can't be written",
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                ) from exc
            return self._snapshot(new_revision, values, None)

    def _snapshot(
        self,
        revision: int | None,
        stored: dict[str, Any] | None,
        error: str | None,
    ) -> dict[str, Any]:
        return {
            "revision": revision,
            "source": "stored" if stored is not None else "config",
            "values": dict(stored if stored is not None else self._config_values),
            "config_values": dict(self._config_values),
            "error": error,
            "parameters": [parameter.as_public_dict() for parameter in MATRIX_PARAMETERS],
            "common": list(COMMON_MATRIX_SETTINGS),
        }

    def _read_locked(self) -> tuple[int | None, dict[str, Any] | None, str | None]:
        """Return ``(revision, values, error)``; a missing file is revision 0.

        An unreadable file has no revision, so nothing can be written over it.
        """

        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return 0, None, None
        except OSError:
            return None, None, "matrix settings file can't be read"
        try:
            document = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError):
            return None, None, "matrix settings file is not valid JSON"
        if not isinstance(document, dict) or set(document) != _FILE_FIELDS:
            return None, None, "matrix settings file has an unknown layout"
        version = document["version"]
        if type(version) is not int or version != MATRIX_SETTINGS_FILE_VERSION:
            return None, None, "matrix settings file has an unsupported version"
        revision = document["revision"]
        if type(revision) is not int or revision < 1:
            return None, None, "matrix settings file has an invalid revision"
        try:
            values = normalize_matrix_values(document["values"])
        except ValidationError:
            return None, None, "matrix settings file holds invalid values"
        return revision, values, None
