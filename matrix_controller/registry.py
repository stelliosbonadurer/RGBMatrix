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
from .schema import ParameterSpec, ProgramFileSpec, ValidationError, VisualizationSpec


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


# Graph Traversal's render config (web-control-refactor D-039, D-045). The editor
# keeps exactly this many component colors: enough for a 32-node graph's largest
# component count, so no color repeats. The program itself accepts any non-empty
# list, so a hand-edited tracked file may differ.
GRAPH_TRAVERSAL_COMPONENT_COLORS = 32
GRAPH_TRAVERSAL_NAMED_COLORS = (
    "background",
    "node",
    "start_node",
    "target_node",
    "current_node",
    "unvisited_edge",
    "traversed_edge",
    "predecessor_edge",
    "current_edge",
    "direction_marker",
    "cycle_edge",
    "shortest_path",
    "dijkstra_low_weight",
    "dijkstra_high_weight",
    "mst_accepted_edge",
    "mst_rejected_edge",
)
# Read only by the page, to draw the editor (D-047). A test pins it to the
# program's loader.
GRAPH_TRAVERSAL_RENDER_FIELDS: tuple[Mapping[str, Any], ...] = (
    {
        "key": "node_size",
        "kind": "int",
        "label": "Node size",
        "minimum": 1,
        "maximum": 2,
        "help": "Pixels per side of each node.",
    },
    {
        "key": "endpoint_node_size",
        "kind": "int",
        "label": "Start and target node size",
        "minimum": 1,
        "maximum": 2,
        "help": "Pixels per side of the start and target nodes.",
    },
    {
        "key": "direction_marker_pixels",
        "kind": "int",
        "label": "Direction marker length",
        "minimum": 1,
        "maximum": 8,
        "help": "Pixels colored at the arrow end of each directed edge.",
    },
    {
        "key": "colors",
        "kind": "color_map",
        "label": "Colors",
        "names": GRAPH_TRAVERSAL_NAMED_COLORS,
    },
    {
        "key": "component_colors",
        "kind": "color_list",
        "label": "Component colors",
        "length": GRAPH_TRAVERSAL_COMPONENT_COLORS,
        "help": "One color per connected component, in the order components are found.",
    },
)


def _validate_graph_traversal_render(document: Any) -> None:
    """The program's own parser (D-047), plus the editor's fixed list (D-045)."""

    from my_programs.dsa_visualizations.graph_rendering import parse_render_config

    try:
        parse_render_config(document)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if len(document["component_colors"]) != GRAPH_TRAVERSAL_COMPONENT_COLORS:
        raise ValidationError(
            f"component_colors must hold exactly {GRAPH_TRAVERSAL_COMPONENT_COLORS} colors"
        )


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


_MATRIX_NAMES = frozenset(parameter.name for parameter in MATRIX_PARAMETERS)


def _program_parameters(
    parameters: tuple[ParameterSpec, ...],
    defaults: Mapping[str, Any],
) -> tuple[ParameterSpec, ...]:
    """Return a program's own options with configured defaults (D-035).

    The shared matrix parameters are carried separately as ``matrix_parameters``.
    """

    own = tuple(parameter for parameter in parameters if parameter.name not in _MATRIX_NAMES)
    return _with_defaults(own, defaults)


def build_default_registry(
    workspace_root: Path | None = None,
    parameter_defaults: Mapping[str, Mapping[str, Any]] | None = None,
) -> Registry:
    """Build the initial product allowlist without importing either application.

    ``parameter_defaults`` holds each program's own configured defaults. Matrix
    values never come from here; a launch supplies them (D-035).
    """

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
                parameters=_program_parameters((), defaults.get("system_info", {})),
                supports_live_control=False,
                matrix_parameters=MATRIX_PARAMETERS,
            ),
            VisualizationSpec(
                visualization_id="fft",
                display_name="Music FFT",
                description="Real-time audio spectrum visualization.",
                script_path=root / "my_programs" / "fft_program" / "main.py",
                working_directory=root / "my_programs" / "fft_program",
                parameters=_program_parameters(FFT_PARAMETERS, defaults.get("fft", {})),
                requires_audio=True,
                supports_live_control=True,
                matrix_parameters=MATRIX_PARAMETERS,
            ),
            VisualizationSpec(
                visualization_id="sorts",
                display_name="Sorting Algorithms",
                description="Animated insertion, selection, bubble, and quick sorts.",
                script_path=root / "my_programs" / "visualize_sorts.py",
                working_directory=root / "my_programs",
                parameters=_program_parameters(SORT_PARAMETERS, defaults.get("sorts", {})),
                supports_live_control=False,
                matrix_parameters=MATRIX_PARAMETERS,
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
                parameters=_program_parameters(
                    GRAPH_TRAVERSAL_PARAMETERS,
                    defaults.get("graph_traversal", {}),
                ),
                supports_live_control=False,
                parameter_validator=_validate_graph_traversal,
                matrix_parameters=MATRIX_PARAMETERS,
                program_files=(
                    ProgramFileSpec(
                        name="render",
                        label="Colors & rendering",
                        default_path=(
                            root
                            / "my_programs"
                            / "dsa_visualizations"
                            / "graph_traversal_config.json"
                        ),
                        validate=_validate_graph_traversal_render,
                        fields=GRAPH_TRAVERSAL_RENDER_FIELDS,
                    ),
                ),
            ),
            VisualizationSpec(
                visualization_id="master_theorem",
                display_name="Master Theorem",
                description="Builds a recurrence tree and highlights where its work is concentrated.",
                script_path=(
                    root / "my_programs" / "dsa_visualizations" / "master_theorem.py"
                ),
                working_directory=root / "my_programs" / "dsa_visualizations",
                parameters=_program_parameters(
                    MASTER_THEOREM_PARAMETERS,
                    defaults.get("master_theorem", {}),
                ),
                supports_live_control=False,
                matrix_parameters=MATRIX_PARAMETERS,
            ),
        )
    )
