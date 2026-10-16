"""Explicit allowlist of visualizations managed by the control service."""

from __future__ import annotations

from pathlib import Path

from .definitions import FFT_PARAMETERS, SORT_PARAMETERS
from .schema import ValidationError, VisualizationSpec


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


def build_default_registry(workspace_root: Path | None = None) -> Registry:
    """Build the initial product allowlist without importing either application."""

    root = workspace_root or Path(__file__).resolve().parent.parent
    return Registry(
        (
            VisualizationSpec(
                visualization_id="fft",
                display_name="Music FFT",
                description="Real-time audio spectrum visualization.",
                script_path=root / "my_programs" / "fft_program" / "main.py",
                working_directory=root / "my_programs" / "fft_program",
                parameters=FFT_PARAMETERS,
                requires_audio=True,
            ),
            VisualizationSpec(
                visualization_id="sorts",
                display_name="Sorting Algorithms",
                description="Animated insertion, selection, bubble, and quick sorts.",
                script_path=root / "my_programs" / "visualize_sorts.py",
                working_directory=root / "my_programs",
                parameters=SORT_PARAMETERS,
            ),
        )
    )

