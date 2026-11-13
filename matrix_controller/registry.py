"""Explicit allowlist of visualizations managed by the control service."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from .definitions import (
    FFT_PARAMETERS,
    GRAPH_TRAVERSAL_PARAMETERS,
    MASTER_THEOREM_PARAMETERS,
    MATRIX_PARAMETERS,
    SORT_PARAMETERS,
)
from .schema import ParameterSpec, ValidationError, VisualizationSpec


def _validate_graph_traversal(parameters: Mapping[str, Any]) -> None:
    """Apply dependent graph constraints before replacing a running program."""

    from my_programs.dsa_visualizations.graph_model import GraphOptions, validate_options

    try:
        validate_options(
            GraphOptions(
                n=parameters["n"],
                e=parameters["e"],
                traversal=parameters["traverse"],
                seed=parameters["seed"],
                connected=parameters["connected"],
                allow_cycles=parameters["allow_cycles"],
                directed=parameters["directed"],
                ensure_path=parameters["ensure_path"],
                directed_connectivity=parameters["directed_connectivity"],
                component_type=parameters["component_type"],
                step_mode=parameters["step_mode"],
                display_mode=parameters["display_mode"],
                animate_back_edges=parameters["animate_back_edges"],
            )
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


class Registry:
    """Immutable-by-convention lookup for code-reviewed visualization entries."""

    def __init__(self, visualizations: tuple[VisualizationSpec, ...]):
        ids = [item.visualization_id for item in visualizations]
        if len(ids) != len(set(ids)):
            raise ValueError("visualization IDs must be unique")
        self._by_id = {item.visualization_id: item for item in visualizations}

    def list(self) -> tuple[VisualizationSpec, ...]:
        return tuple(self._by_id[key] for key in sorted(self._by_id))

    def get(self, visualization_id: str) -> VisualizationSpec:
        try:
            return self._by_id[visualization_id]
        except KeyError as exc:
            raise ValidationError(f"unknown visualization: {visualization_id}") from exc


def _with_defaults(
    parameters: tuple[ParameterSpec, ...],
    defaults: Mapping[str, Any],
) -> tuple[ParameterSpec, ...]:
    """Copy parameter contracts with administrator-configured default values."""

    known = {parameter.name for parameter in parameters}
    unknown = sorted(set(defaults) - known)
    if unknown:
        raise ValidationError(f"unknown configured parameters: {', '.join(unknown)}")
    return tuple(
        replace(parameter, default=parameter.normalize(defaults[parameter.name]))
        if parameter.name in defaults
        else parameter
        for parameter in parameters
    )


def build_default_registry(
    workspace_root: Path | None = None,
    parameter_defaults: Mapping[str, Mapping[str, Any]] | None = None,
) -> Registry:
    """Build the initial product allowlist without importing either application."""

    root = workspace_root or Path(__file__).resolve().parent.parent
    defaults = parameter_defaults or {}
    return Registry(
        (
            VisualizationSpec(
                visualization_id="system_info",
                display_name="Connection Info",
                description="Shows the Wi-Fi name, controller address, and pairing code.",
                script_path=root / "my_programs" / "system_info.py",
                working_directory=root / "my_programs",
                parameters=_with_defaults(
                    MATRIX_PARAMETERS,
                    defaults.get("system_info", {}),
                ),
                supports_live_control=False,
            ),
            VisualizationSpec(
                visualization_id="fft",
                display_name="Music FFT",
                description="Real-time audio spectrum visualization.",
                script_path=root / "my_programs" / "fft_program" / "main.py",
                working_directory=root / "my_programs" / "fft_program",
                parameters=_with_defaults(FFT_PARAMETERS, defaults.get("fft", {})),
                requires_audio=True,
                supports_live_control=True,
            ),
            VisualizationSpec(
                visualization_id="sorts",
                display_name="Sorting Algorithms",
                description="Animated insertion, selection, bubble, and quick sorts.",
                script_path=root / "my_programs" / "visualize_sorts.py",
                working_directory=root / "my_programs",
                parameters=_with_defaults(SORT_PARAMETERS, defaults.get("sorts", {})),
                supports_live_control=False,
            ),
            VisualizationSpec(
                visualization_id="graph_traversal",
                display_name="Graph Traversal",
                description=(
                    "Animates graph searches, connected components, and minimum "
                    "spanning trees on a reproducible planar graph."
                ),
                script_path=(
                    root / "my_programs" / "dsa_visualizations" / "graph_traversal.py"
                ),
                working_directory=root / "my_programs" / "dsa_visualizations",
                parameters=_with_defaults(
                    GRAPH_TRAVERSAL_PARAMETERS,
                    defaults.get("graph_traversal", {}),
                ),
                supports_live_control=False,
                parameter_validator=_validate_graph_traversal,
            ),
            VisualizationSpec(
                visualization_id="master_theorem",
                display_name="Master Theorem",
                description="Builds a recurrence tree and highlights where its work is concentrated.",
                script_path=(
                    root / "my_programs" / "dsa_visualizations" / "master_theorem.py"
                ),
                working_directory=root / "my_programs" / "dsa_visualizations",
                parameters=_with_defaults(
                    MASTER_THEOREM_PARAMETERS,
                    defaults.get("master_theorem", {}),
                ),
                supports_live_control=False,
            ),
        )
    )
