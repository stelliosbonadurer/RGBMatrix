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


class FireTheme(BaseTheme):
    """Black/red (low) -> Orange (mid) -> Yellow/white (high) - intense."""
    
    name = "fire"
    description = "Black/red (low) -> Orange (mid) -> Yellow/white (high)"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio
        if ratio < 0.33:
            r = int(255 * (ratio * 3))
            g = 0
            b = 0
        elif ratio < 0.66:
            r = 255
            g = int(255 * ((ratio - 0.33) * 3))
            b = 0
        else:
            r = 255
            g = 255
            b = int(255 * ((ratio - 0.66) * 3))
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


class WavesTheme(BaseTheme):
    """Ocean waves: deep blue -> light blue -> cyan -> white (seafoam peaks)."""
    
    name = "waves"
    description = "Ocean waves: deep blue -> cyan -> white seafoam"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio
        # X-axis varies the shade slightly (deeper vs lighter blue)
        depth = 1.0 - column_ratio * 0.3  # 1.0 to 0.7
        
        if ratio < 0.33:
            # Bottom third: dark/deep blue
            intensity = ratio * 3
            r = 0
            g = int(30 * intensity * depth)
            b = int((100 + 80 * depth) * intensity)
        elif ratio < 0.66:
            # Middle third: deep blue -> light blue/cyan
            intensity = (ratio - 0.33) * 3
            r = int(30 * intensity)
            g = int(30 * depth + (180 - 30) * intensity)
            b = int(100 + 80 * depth + (255 - (100 + 80 * depth)) * intensity * 0.5)
        else:
            # Top third: cyan -> white (seafoam/whitecaps)
            intensity = (ratio - 0.66) * 3
            r = int(30 + (255 - 30) * intensity)
            g = int(180 + (255 - 180) * intensity)
            b = int(200 + (255 - 200) * intensity)
        
        # Clamp and apply brightness
        r = max(0, min(255, r))
        g = max(0, min(255, g))
        b = max(0, min(255, b))
        return self._apply_brightness(r, g, b)


class MonoGreenTheme(BaseTheme):
    """Single color (green) with brightness based on height - retro."""
    
    name = "mono_green"
    description = "Single color green - retro style"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio
        r = 0
        g = int(255 * ratio)
        b = 0
        return self._apply_brightness(r, g, b)


class MonoAmberTheme(BaseTheme):
    """Single color (amber/orange) with brightness based on height - vintage VU."""
    
    name = "mono_amber"
    description = "Single color amber - vintage VU meter"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio
        r = int(255 * ratio)
        g = int(140 * ratio)
        b = 0
        return self._apply_brightness(r, g, b)


class BlueFlamTheme(BaseTheme):
    """Fire spectrum but peaks fade to blue (like hottest part of flame)."""
    
    name = "blue_flame"
    description = "Fire that transitions to blue at the hottest part"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio
        warmth = column_ratio
        
        if ratio < 0.25:
            # Bottom quarter: black to red/dark-orange
            intensity = ratio * 4
            r = int(255 * intensity)
            g = int(60 * warmth * intensity)
            b = 0
        elif ratio < 0.5:
            # Second quarter: red to orange
            intensity = (ratio - 0.25) * 4
            r = 255
            g = int((120 + 100 * warmth) * intensity)
            b = 0
        elif ratio < 0.75:
            # Third quarter: orange to yellow/white
            intensity = (ratio - 0.5) * 4
            r = 255
            g = int(120 + 100 * warmth + (255 - (120 + 100 * warmth)) * intensity)
            b = int(180 * intensity)
        else:
            # Top quarter: yellow/white transitioning to blue
            intensity = min(1.0, (ratio - 0.75) * 4)
            r = int(255 * max(0, 1 - intensity * 0.8))
            g = int(255 * max(0, 1 - intensity * 0.6))
            b = int(180 + (255 - 180) * intensity)
        
        r = max(0, min(255, r))
        g = max(0, min(255, g))
        b = max(0, min(255, b))
        return self._apply_brightness(r, g, b)


class FireSpectrumTheme(BaseTheme):
    """Fire hues vary by column (deep red to orange), fire gradient on y-axis."""
    
    name = "fire_spectrum"
    description = "Fire hues vary across columns"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio
        warmth = column_ratio
        
        if ratio < 0.33:
            # Bottom third: black to red/dark-orange
            intensity = ratio * 3
            r = int(255 * intensity)
            g = int(60 * warmth * intensity)
            b = 0
        elif ratio < 0.66:
            # Middle third: red to orange/yellow
            intensity = (ratio - 0.33) * 3
            r = 255
            g = int((120 + 135 * warmth) * intensity)
            b = 0
        else:
            # Top third: orange to yellow/white
            intensity = (ratio - 0.66) * 3
            r = 255
            g = int(120 + 135 * warmth + (255 - (120 + 135 * warmth)) * intensity)
            b = int(255 * intensity * warmth)
        
        return self._apply_brightness(r, g, b)
