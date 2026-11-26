"""Theme module for FFT visualizer color schemes."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from themes.base import BaseTheme
from themes.gradients import (
    WarmTheme,
    OceanTheme,
    ForestTheme,
    RainbowTheme,
    AutumnTheme,
)
from themes.color_stops import ColorStopTheme, color_stop_theme
from themes.registry import get_theme, register_theme, list_themes

__all__ = [
    'BaseTheme',
    'WarmTheme',
    'OceanTheme',
    'ForestTheme',
    'RainbowTheme',
    'AutumnTheme',
    'ColorStopTheme',
    'color_stop_theme',
    'get_theme',
    'register_theme',
    'list_themes',
]
