"""
Gradient-based color themes for FFT visualizer.

Direct color calculation for best performance on LED matrices.
"""
import math
from typing import Tuple
from .base import BaseTheme


class WarmTheme(BaseTheme):
    """Red (low) -> Orange -> Yellow -> White (high) - warm fire colors."""
    
    name = "warm"
    description = "Red (low) -> Orange -> Yellow -> White (high)"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        # Red (0) -> Orange (0.33) -> Yellow (0.66) -> White (1.0)
        ratio = height_ratio
        
        if ratio < 0.5:
            # Red to Orange
            norm = ratio / 0.5
            r = 255
            g = int(165 * norm)  # 0 -> 165
            b = 0
        else:
            # Orange to Yellow
            norm = (ratio - 0.5) / 0.5
            r = 255
            g = int(165 + 90 * norm)  # 165 -> 255
            b = 0
        
        return self._apply_brightness(r, g, b)
    
    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0, bar_ratio: float = 0.0) -> Tuple[int, int, int]:
        """Warm overflow: Orange -> Yellow -> White hot."""
        if layer == 0:
            return self.get_color(height_ratio, column_ratio)
        elif layer == 1:
            # Second layer: orange -> yellow
            r = 255
            g = int(165 + 90 * height_ratio)  # 165 -> 255
            b = 0
        else:
            # Higher layers: yellow -> white
            r = 255
            g = 255
            b = int(255 * height_ratio)
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
    
    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0, bar_ratio: float = 0.0) -> Tuple[int, int, int]:
        """Ocean overflow: Theme -> Cyan/White -> Bright white (cool tones only)."""
        if layer == 0:
            return self.get_color(height_ratio, column_ratio)
        elif layer == 1:
            # Second layer: cyan -> white
            r = int(255 * height_ratio)
            g = 255
            b = 255
        else:
            # Higher layers: bright white with slight cyan tint
            r = int(230 + 25 * height_ratio)
            g = 255
            b = 255
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
    
    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0, bar_ratio: float = 0.0) -> Tuple[int, int, int]:
        """Forest overflow: Yellow -> Gold -> Sunlight white."""
        if layer == 0:
            return self.get_color(height_ratio, column_ratio)
        elif layer == 1:
            # Second layer: yellow -> gold
            r = 255
            g = int(255 - 45 * height_ratio)  # 255 -> 210 (gold)
            b = int(50 * height_ratio)
        else:
            # Higher layers: gold -> warm white (sunlight through leaves)
            r = 255
            g = int(210 + 45 * height_ratio)
            b = int(50 + 150 * height_ratio)
        return self._apply_brightness(r, g, b)



class RainbowTheme(BaseTheme):
    """Full spectrum based on bar position (column), full brightness throughout."""
    
    name = "rainbow"
    description = "Full rainbow spectrum based on column position"
    
    def _adjusted_hue(self, column_ratio: float) -> float:
        """Apply non-linear mapping to stretch red region and compress green/blue."""
        # Use power curve: lower exponent = more red space, higher = more compressed rest
        # column_ratio^0.6 stretches the red/orange region (left side)
        adjusted = column_ratio ** 0.9
        return adjusted * 300  # 0-300 degrees: red -> yellow -> green -> cyan -> blue -> magenta
    
    def _hue_to_rgb(self, hue: float) -> Tuple[float, float, float]:
        """Convert hue (0-300) to RGB components (0-1 range)."""
        c = 1.0
        x = c * (1 - abs((hue / 60) % 2 - 1))
        
        if hue < 60:
            return c, x, 0
        elif hue < 120:
            return x, c, 0
        elif hue < 180:
            return 0, c, x
        elif hue < 240:
            return 0, x, c
        elif hue < 300:
            return x, 0, c
        else:
            return c, 0, x
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        hue = self._adjusted_hue(column_ratio)
        r, g, b = self._hue_to_rgb(hue)
        r, g, b = int(r * 255), int(g * 255), int(b * 255)
        return self._apply_brightness(r, g, b)
    
    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0, bar_ratio: float = 0.0) -> Tuple[int, int, int]:
        """Rainbow overflow: Continue rainbow with increasing brightness/white."""
        if layer == 0:
            return self.get_color(height_ratio, column_ratio)
        else:
            # Higher layers: rainbow colors washed toward white
            hue = self._adjusted_hue(column_ratio)
            # More white blend as layers increase
            white_blend = min(0.3 + layer * 0.2, 0.8)
            r, g, b = self._hue_to_rgb(hue)
            
            # Blend toward white
            r = int((r * (1 - white_blend) + white_blend) * 255)
            g = int((g * (1 - white_blend) + white_blend) * 255)
            b = int((b * (1 - white_blend) + white_blend) * 255)
            return self._apply_brightness(r, g, b)


class AutumnTheme(BaseTheme):
    """Deep brown (low) -> Burnt orange -> Golden yellow (high) with red accents - autumn foliage."""

    name = "autumn"
    description = "Deep brown (low) -> Burnt orange -> Golden yellow (high) - autumn foliage"

    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio

        if ratio < 0.33:
            # Deep brown -> Burnt red-brown
            norm = ratio / 0.33
            r = int(100 + 55 * norm)   # 100 -> 155
            g = int(40 + 20 * norm)    # 40 -> 60
            b = int(10 + 5 * norm)     # 10 -> 15
        elif ratio < 0.66:
            # Burnt red-brown -> Burnt orange with red touch
            norm = (ratio - 0.33) / 0.33
            r = int(155 + 85 * norm)   # 155 -> 240
            g = int(60 + 60 * norm)    # 60 -> 120
            b = int(15 - 10 * norm)    # 15 -> 5
        else:
            # Burnt orange -> Golden yellow
            norm = (ratio - 0.66) / 0.34
            r = int(240 + 15 * norm)   # 240 -> 255
            g = int(120 + 100 * norm)  # 120 -> 220
            b = int(5 + 15 * norm)     # 5 -> 20

        return self._apply_brightness(r, g, b)

    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0, bar_ratio: float = 0.0) -> Tuple[int, int, int]:
        """Autumn overflow: Crimson red -> Bright gold -> Warm white."""
        if layer == 0:
            return self.get_color(height_ratio, column_ratio)
        elif layer == 1:
            # Second layer: crimson red -> bright gold
            r = 255
            g = int(50 + 170 * height_ratio)   # 50 -> 220
            b = int(20 * height_ratio)          # 0 -> 20
        else:
            # Higher layers: bright gold -> warm white
            r = 255
            g = int(220 + 35 * height_ratio)    # 220 -> 255
            b = int(20 + 180 * height_ratio)    # 20 -> 200
        return self._apply_brightness(r, g, b)


