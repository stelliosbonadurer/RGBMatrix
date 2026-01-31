"""
Visualizer registry for FFT display modes.

Provides a centralized way to access visualizers by name.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Dict, Type, List
from visualizers.base import BaseVisualizer
from visualizers.bars_unified import BarsUnifiedVisualizer

# Global visualizer registry
_VISUALIZER_REGISTRY: Dict[str, Type[BaseVisualizer]] = {}


def _register_builtin_visualizers():
    """Register all built-in visualizers."""
    builtin_visualizers = [
        BarsUnifiedVisualizer,
    ]
    for viz_class in builtin_visualizers:
        _VISUALIZER_REGISTRY[viz_class.name] = viz_class


def register_visualizer(visualizer_class: Type[BaseVisualizer]) -> None:
    """
    Register a custom visualizer.
    
    Args:
        visualizer_class: Visualizer class to register (must have a 'name' attribute)
    """
    if not hasattr(visualizer_class, 'name'):
        raise ValueError("Visualizer class must have a 'name' attribute")
    _VISUALIZER_REGISTRY[visualizer_class.name] = visualizer_class


def get_visualizer(
    name: str,
    width: int,
    height: int,
    settings
) -> BaseVisualizer:
    """
    Get a visualizer instance by name.
    
    Args:
        name: Visualizer name (e.g., 'bars', 'bars_overflow')
        width: Matrix width in pixels
        height: Matrix height in pixels
        settings: Application settings
    
    Returns:
        Instantiated visualizer object
    
    Raises:
        KeyError: If visualizer name is not found
    """
    if not _VISUALIZER_REGISTRY:
        _register_builtin_visualizers()
    
    if name not in _VISUALIZER_REGISTRY:
        available = ', '.join(sorted(_VISUALIZER_REGISTRY.keys()))
        raise KeyError(f"Unknown visualizer '{name}'. Available: {available}")
    
    return _VISUALIZER_REGISTRY[name](width, height, settings)


def list_visualizers() -> List[str]:
    """
    Get list of available visualizer names.
    
    Returns:
        Sorted list of visualizer names
    """
    if not _VISUALIZER_REGISTRY:
        _register_builtin_visualizers()
    return sorted(_VISUALIZER_REGISTRY.keys())


def get_visualizer_info() -> Dict[str, str]:
    """
    Get dictionary of visualizer names and descriptions.
    
    Returns:
        Dict mapping visualizer name to description
    """
    if not _VISUALIZER_REGISTRY:
        _register_builtin_visualizers()
    return {name: cls.description for name, cls in _VISUALIZER_REGISTRY.items()}


# Initialize built-in visualizers on module load
_register_builtin_visualizers()
