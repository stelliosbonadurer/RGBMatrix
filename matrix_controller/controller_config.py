"""Strict administrator-owned defaults for managed visualization launches.

The file described here is intentionally not a generic command template. It can
select only code-owned runtime switches and values already described by
``ParameterSpec``. Visualization paths, command prefixes, and arbitrary argv remain
owned by the reviewed registry and supervisor.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .definitions import FFT_PARAMETERS, MATRIX_PARAMETERS, SORT_PARAMETERS
from .schema import ParameterSpec, ValidationError


class ControllerConfigError(ValueError):
    """A safe, actionable error in the administrator configuration file."""


@dataclass(frozen=True)
class ControllerConfig:
    """Normalized controller configuration with built-in-compatible defaults."""

    python_executable: str | None = None
    use_sudo: bool = False
    stop_timeout_seconds: float = 5.0
    boot_visualization_id: str | None = "system_info"
    matrix_defaults: Mapping[str, Any] = field(default_factory=dict)
    visualization_defaults: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def defaults_for(self, visualization_id: str) -> dict[str, Any]:
        """Merge shared matrix defaults with one visualization's defaults."""

        return {
            **self.matrix_defaults,
            **self.visualization_defaults.get(visualization_id, {}),
        }


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ControllerConfigError(f"{label} must be a JSON object")
    return value


def _reject_unknown(data: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ControllerConfigError(f"unknown {label} fields: {', '.join(unknown)}")


def _normalize_parameter_defaults(
    values: Mapping[str, Any],
    parameters: tuple[ParameterSpec, ...],
    label: str,
) -> dict[str, Any]:
    by_name = {parameter.name: parameter for parameter in parameters}
    _reject_unknown(values, set(by_name), label)
    normalized: dict[str, Any] = {}
    for name, value in values.items():
        try:
            normalized[name] = by_name[name].normalize(value)
        except ValidationError as exc:
            raise ControllerConfigError(f"invalid {label} value: {exc}") from exc
    return normalized


def load_controller_config(path: Path | None) -> ControllerConfig:
    """Load a version-one JSON file or return backward-compatible defaults.

    A missing path means the caller did not request configuration. A supplied path
    must exist and validate completely; silently falling back after an operator typo
    could initialize the panel with unsafe dimensions or timing.
    """

    if path is None:
        return ControllerConfig()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ControllerConfigError(f"could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ControllerConfigError(
            f"invalid JSON in {path} at line {exc.lineno}, column {exc.colno}"
        ) from exc

    root = _require_object(raw, "controller config")
    _reject_unknown(
        root,
        {"version", "runtime", "boot", "matrix", "visualizations"},
        "top-level",
    )
    if root.get("version") != 1:
        raise ControllerConfigError("controller config version must be 1")

    runtime = _require_object(root.get("runtime", {}), "runtime")
    _reject_unknown(
        runtime,
        {"python_executable", "use_sudo", "stop_timeout_seconds"},
        "runtime",
    )
    python_executable = runtime.get("python_executable")
    if python_executable is not None:
        if type(python_executable) is not str or not python_executable.strip():
            raise ControllerConfigError("runtime.python_executable must be a non-empty string")
        if len(python_executable) > 4096:
            raise ControllerConfigError("runtime.python_executable is too long")
        python_executable = str(Path(python_executable).expanduser())

    use_sudo = runtime.get("use_sudo", False)
    if type(use_sudo) is not bool:
        raise ControllerConfigError("runtime.use_sudo must be a boolean")

    stop_timeout = runtime.get("stop_timeout_seconds", 5.0)
    if isinstance(stop_timeout, bool) or not isinstance(stop_timeout, (int, float)):
        raise ControllerConfigError("runtime.stop_timeout_seconds must be a number")
    stop_timeout = float(stop_timeout)
    if not math.isfinite(stop_timeout) or not 0.1 <= stop_timeout <= 60.0:
        raise ControllerConfigError(
            "runtime.stop_timeout_seconds must be between 0.1 and 60"
        )

    boot = _require_object(root.get("boot", {}), "boot")
    _reject_unknown(boot, {"visualization_id"}, "boot")
    boot_visualization = boot.get("visualization_id", "system_info")
    allowed_visualizations = {"system_info", "fft", "sorts"}
    if boot_visualization is not None and boot_visualization not in allowed_visualizations:
        raise ControllerConfigError(
            "boot.visualization_id must be system_info, fft, sorts, or null"
        )

    matrix = _require_object(root.get("matrix", {}), "matrix")
    matrix_defaults = _normalize_parameter_defaults(matrix, MATRIX_PARAMETERS, "matrix")

    visualizations = _require_object(root.get("visualizations", {}), "visualizations")
    _reject_unknown(visualizations, allowed_visualizations, "visualizations")
    matrix_names = {parameter.name for parameter in MATRIX_PARAMETERS}
    parameters_by_visualization = {
        "system_info": (),
        "fft": tuple(p for p in FFT_PARAMETERS if p.name not in matrix_names),
        "sorts": tuple(p for p in SORT_PARAMETERS if p.name not in matrix_names),
    }
    visualization_defaults: dict[str, dict[str, Any]] = {}
    for visualization_id, values in visualizations.items():
        section = _require_object(values, f"visualizations.{visualization_id}")
        visualization_defaults[visualization_id] = _normalize_parameter_defaults(
            section,
            parameters_by_visualization[visualization_id],
            f"visualizations.{visualization_id}",
        )

    return ControllerConfig(
        python_executable=python_executable,
        use_sudo=use_sudo,
        stop_timeout_seconds=stop_timeout,
        boot_visualization_id=boot_visualization,
        matrix_defaults=matrix_defaults,
        visualization_defaults=visualization_defaults,
    )
