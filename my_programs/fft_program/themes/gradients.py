"""
Gradient-based color themes for FFT visualizer.
"""
from typing import Tuple
from .base import BaseTheme


class ClassicTheme(BaseTheme):
    """Blue (low) -> Green (mid) -> Red (high) - original theme."""
    
    name = "classic"
    description = "Blue (low) -> Green (mid) -> Red (high)"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio
        if ratio < 0.5:
            r = 0
            g = int(255 * (ratio * 2))
            b = int(255 * (1 - ratio * 2))
        else:
            r = int(255 * ((ratio - 0.5) * 2))
            g = int(255 * (1 - (ratio - 0.5) * 2))
            b = 0
        return self._apply_brightness(r, g, b)


class WarmTheme(BaseTheme):
    """Dark red (low) -> Orange (mid) -> Yellow (high) - great for bluegrass."""
    
    name = "warm"
    description = "Dark red (low) -> Orange (mid) -> Yellow (high)"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio
        r = int(255 * min(1, ratio * 2 + 0.3))
        g = int(255 * (ratio ** 1.2))
        b = 0
        return self._apply_brightness(r, g, b)


class OceanTheme(BaseTheme):
    """Deep blue (low) -> Cyan (mid) -> White (high) - cool/calm."""
    
    name = "ocean"
    description = "Deep blue (low) -> Cyan (mid) -> White (high)"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio
        r = int(255 * (ratio ** 2))
        g = int(255 * ratio)
        b = int(255 * (0.5 + ratio * 0.5))
        return self._apply_brightness(r, g, b)


class ForestTheme(BaseTheme):
    """Dark green (low) -> Bright green (mid) -> Yellow (high) - nature."""
    
    name = "forest"
    description = "Dark green (low) -> Bright green (mid) -> Yellow (high)"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio
        r = int(255 * (ratio ** 1.5))
        g = int(255 * (0.3 + ratio * 0.7))
        b = 0
        return self._apply_brightness(r, g, b)


class PurpleTheme(BaseTheme):
    """Deep purple (low) -> Magenta (mid) -> Pink (high) - vibrant."""
    
    name = "purple"
    description = "Deep purple (low) -> Magenta (mid) -> Pink (high)"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio
        r = int(255 * (0.4 + ratio * 0.6))
        g = int(255 * (ratio ** 2))
        b = int(255 * (0.6 + ratio * 0.4))
        return self._apply_brightness(r, g, b)


class SunsetTheme(BaseTheme):
    """Deep purple (low) -> Pink/Orange (mid) -> Yellow/White (high) - sunset/sunrise."""
    
    name = "sunset"
    description = "Sunset gradient: purple -> pink -> orange -> yellow"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio
        
        if ratio < 0.25:
            # Bottom quarter: deep purple/indigo
            intensity = ratio * 4
            r = int(75 * intensity)
            g = int(0 * intensity)
            b = int(130 * intensity)
        elif ratio < 0.5:
            # Second quarter: purple -> hot pink/magenta
            intensity = (ratio - 0.25) * 4
            r = int(75 + (255 - 75) * intensity)
            g = int(0 + (20 - 0) * intensity)
            b = int(130 + (147 - 130) * intensity)
        elif ratio < 0.75:
            # Third quarter: pink -> orange
            intensity = (ratio - 0.5) * 4
            r = 255
            g = int(20 + (140 - 20) * intensity)
            b = int(147 - 147 * intensity)
        else:
            # Top quarter: orange -> bright yellow/gold
            intensity = (ratio - 0.75) * 4
            r = 255
            g = int(140 + (220 - 140) * intensity)
            b = int(0 + (50 * intensity))  # Slight warmth at peak
        
        return self._apply_brightness(r, g, b)

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