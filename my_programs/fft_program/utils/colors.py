"""
Color utility functions for FFT visualizer.
"""
from typing import Tuple


def hsv_to_rgb(hue: float, saturation: float, value: float) -> Tuple[int, int, int]:
    """
    Convert HSV color to RGB.
    
    Args:
        hue: Hue value (0-360 degrees)
        saturation: Saturation (0-1)
        value: Value/brightness (0-1)
    
    Returns:
        Tuple of (r, g, b) values 0-255
    """
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
    
    return (
        int((r + m) * 255),
        int((g + m) * 255),
        int((b + m) * 255)
    )


def clamp_color(r: int, g: int, b: int) -> Tuple[int, int, int]:
    """
    Clamp RGB values to valid 0-255 range.
    
    Args:
        r, g, b: RGB values (may be outside valid range)
    
    Returns:
        Tuple of clamped (r, g, b) values 0-255
    """
    return (
        max(0, min(255, int(r))),
        max(0, min(255, int(g))),
        max(0, min(255, int(b)))
    )


def apply_brightness(r: int, g: int, b: int, boost: float) -> Tuple[int, int, int]:
    """
    Apply brightness boost to RGB color.
    
    Args:
        r, g, b: RGB values 0-255
        boost: Brightness multiplier (1.0 = no change)
    
    Returns:
        Tuple of boosted (r, g, b) values 0-255
    """
    return clamp_color(
        int(r * boost),
        int(g * boost),
        int(b * boost)
    )


def interpolate_color(
    color1: Tuple[int, int, int],
    color2: Tuple[int, int, int],
    t: float
) -> Tuple[int, int, int]:
    """
    Linearly interpolate between two colors.
    
    Args:
        color1: Starting RGB color
        color2: Ending RGB color
        t: Interpolation factor (0 = color1, 1 = color2)
    
    Returns:
        Interpolated RGB color tuple
    """
    t = max(0, min(1, t))
    return (
        int(color1[0] + (color2[0] - color1[0]) * t),
        int(color1[1] + (color2[1] - color1[1]) * t),
        int(color1[2] + (color2[2] - color1[2]) * t)
    )


def invert_color(r: int, g: int, b: int) -> Tuple[int, int, int]:
    """
    Invert an RGB color.
    
    Args:
        r, g, b: RGB values 0-255
    
    Returns:
        Inverted RGB color tuple
    """
    return (255 - r, 255 - g, 255 - b)
