"""Configuration module for FFT visualizer."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import (
    AudioSettings,
    FrequencySettings,
    OverflowSettings,
    PeakSettings,
    ColorSettings,
    SensitivitySettings,
    ScalingSettings,
    SmoothingSettings,
    Settings,
    load_settings,
    get_default_settings,
)

__all__ = [
    'AudioSettings',
    'FrequencySettings',
    'OverflowSettings',
    'PeakSettings',
    'ColorSettings',
    'SensitivitySettings',
    'ScalingSettings',
    'SmoothingSettings',
    'Settings',
    'load_settings',
    'get_default_settings',
]
