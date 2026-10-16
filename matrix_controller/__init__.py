"""Shared runtime contracts for managed RGB matrix visualizations.

This package intentionally does not import ``rgbmatrix``, NumPy, or audio modules.
The web control process must be able to list and validate visualizations on a laptop
or during early Pi boot without initializing hardware.
"""

from .registry import Registry, build_default_registry
from .schema import ParameterSpec, ValidationError, VisualizationSpec

__all__ = [
    "ParameterSpec",
    "Registry",
    "ValidationError",
    "VisualizationSpec",
    "build_default_registry",
]

