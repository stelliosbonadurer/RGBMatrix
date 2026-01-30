"""Theme module for FFT visualizer color schemes."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from themes.base import BaseTheme
from themes.gradients import (
    ClassicTheme,
    WarmTheme,
    FireTheme,
    OceanTheme,
    ForestTheme,
    PurpleTheme,
    WavesTheme,
    MonoGreenTheme,
    MonoAmberTheme,
    BlueFlamTheme,
    FireSpectrumTheme,
    SunsetTheme,
    OverflowTheme,
)
from themes.rainbow import RainbowTheme, SpectrumTheme
from themes.registry import get_theme, register_theme, list_themes

__all__ = [
    'BaseTheme',
    'ClassicTheme',
    'WarmTheme', 
    'FireTheme',
    'OceanTheme',
    'ForestTheme',
    'PurpleTheme',
    'WavesTheme',
    'MonoGreenTheme',
    'MonoAmberTheme',
    'BlueFlamTheme',
    'FireSpectrumTheme',
    'SunsetTheme',
    'OverflowTheme',
    'RainbowTheme',
    'SpectrumTheme',
    'get_theme',
    'register_theme',
    'list_themes',
]
