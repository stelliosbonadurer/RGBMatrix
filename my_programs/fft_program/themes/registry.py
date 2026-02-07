"""
Theme registry for FFT visualizer.

Provides a centralized way to access themes by name.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Dict, Type, Optional, List
from themes.base import BaseTheme
from themes.gradients import (
    WarmTheme,
    OceanTheme,
    ForestTheme,
    RainbowTheme,
)

# Global theme registry
_THEME_REGISTRY: Dict[str, Type[BaseTheme]] = {}


def _register_builtin_themes():
    """Register all built-in themes."""
    builtin_themes = [
        WarmTheme,
        OceanTheme,
        ForestTheme,
        RainbowTheme,
    ]
    for theme_class in builtin_themes:
        _THEME_REGISTRY[theme_class.name] = theme_class


def register_theme(theme_class: Type[BaseTheme]) -> None:
    """
    Register a custom theme.
    
    Args:
        theme_class: Theme class to register (must have a 'name' attribute)
    """
    if not hasattr(theme_class, 'name'):
        raise ValueError("Theme class must have a 'name' attribute")
    _THEME_REGISTRY[theme_class.name] = theme_class


def get_theme(name: str, brightness_boost: float = 1.0) -> BaseTheme:
    """
    Get a theme instance by name.
    
    Args:
        name: Theme name (e.g., 'fire', 'ocean', 'rainbow')
        brightness_boost: Brightness multiplier to apply
    
    Returns:
        Instantiated theme object
    
    Raises:
        KeyError: If theme name is not found
    """
    if not _THEME_REGISTRY:
        _register_builtin_themes()
    
    if name not in _THEME_REGISTRY:
        available = ', '.join(sorted(_THEME_REGISTRY.keys()))
        raise KeyError(f"Unknown theme '{name}'. Available themes: {available}")
    
    return _THEME_REGISTRY[name](brightness_boost=brightness_boost)


def list_themes() -> List[str]:
    """
    Get list of available theme names.
    
    Returns:
        Sorted list of theme names
    """
    if not _THEME_REGISTRY:
        _register_builtin_themes()
    return sorted(_THEME_REGISTRY.keys())


def get_theme_info() -> Dict[str, str]:
    """
    Get dictionary of theme names and descriptions.
    
    Returns:
        Dict mapping theme name to description
    """
    if not _THEME_REGISTRY:
        _register_builtin_themes()
    return {name: cls.description for name, cls in _THEME_REGISTRY.items()}


# Initialize built-in themes on module load
_register_builtin_themes()
