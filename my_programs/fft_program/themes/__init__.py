"""Theme module for FFT visualizer color schemes."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from themes.base import BaseTheme
from themes.gradients import (
    WarmTheme,
    OceanTheme,
    ForestTheme,
    PurpleTheme,
    SunsetTheme,
    RainbowTheme,
    NeonTheme,
    AuroraTheme,
    PlasmaTheme,
)
from themes.registry import get_theme, register_theme, list_themes

__all__ = [
    'BaseTheme',
    'WarmTheme',
    'OceanTheme',
    'ForestTheme',
    'PurpleTheme',
    'SunsetTheme',
    'RainbowTheme',
    'NeonTheme',
    'AuroraTheme',
    'PlasmaTheme',
    'get_theme',
    'register_theme',
    'list_themes',
]
