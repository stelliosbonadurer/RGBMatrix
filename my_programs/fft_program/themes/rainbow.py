"""
Rainbow and spectrum-based color themes for FFT visualizer.

These themes use column position to determine hue.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Tuple
from themes.base import BaseTheme
from utils.colors import hsv_to_rgb


class RainbowTheme(BaseTheme):
    """Full spectrum based on bar position (column), brightness varies with height."""
    
    name = "rainbow"
    description = "Full rainbow spectrum based on column position"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio
        hue = column_ratio * 360
        
        # HSV to RGB conversion (saturation=1, value=ratio)
        c = ratio
        x = c * (1 - abs((hue / 60) % 2 - 1))
        
        if hue < 60:
            r, g, b = c, x, 0
        elif hue < 120:
            r, g, b = x, c, 0
        elif hue < 180:
            r, g, b = 0, c, x
        elif hue < 240:
            r, g, b = 0, x, c
        elif hue < 300:
            r, g, b = x, 0, c
        else:
            r, g, b = c, 0, x
        
        r, g, b = int(r * 255), int(g * 255), int(b * 255)
        return self._apply_brightness(r, g, b)


class SpectrumTheme(BaseTheme):
    """Rainbow hue by column + hue shifts with amplitude (low=blue-ish, high=red-ish)."""
    
    name = "spectrum"
    description = "Rainbow with amplitude-based hue shift"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio
        
        # Base hue from column (0-360), then shift based on amplitude
        base_hue = column_ratio * 270  # Use 270 degrees for variety
        amplitude_shift = (ratio - 0.5) * 90  # -45 to +45 degree shift
        hue = (base_hue + amplitude_shift) % 360
        
        # Full saturation, value based on amplitude (minimum brightness for quiet bins)
        saturation = 1.0
        value = 0.3 + ratio * 0.7  # Range from 0.3 to 1.0
        
        # HSV to RGB conversion
        c = value * saturation
        x = c * (1 - abs((hue / 60) % 2 - 1))
        m = value - c
        
        if hue < 60:
            r, g, b = c, x, 0
        elif hue < 120:
            r, g, b = x, c, 0
        elif hue < 180:
            r, g, b = 0, c, x
        elif hue < 240:
            r, g, b = 0, x, c
        elif hue < 300:
            r, g, b = x, 0, c
        else:
            r, g, b = c, 0, x
        
        r, g, b = int((r + m) * 255), int((g + m) * 255), int((b + m) * 255)
        return self._apply_brightness(r, g, b)
