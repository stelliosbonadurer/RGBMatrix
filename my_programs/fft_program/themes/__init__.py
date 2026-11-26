"""Theme module for FFT visualizer color schemes.

Imports are relative so the web service can import ``themes.custom`` (as
``my_programs.fft_program.themes.custom``) without changing its own import path
(fft-themes D-007).
"""
from .base import BaseTheme
from .gradients import (
    WarmTheme,
    OceanTheme,
    ForestTheme,
    RainbowTheme,
    AutumnTheme,
)
from .color_stops import ColorStopTheme, color_stop_theme
from .registry import get_theme, register_theme, list_themes

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
