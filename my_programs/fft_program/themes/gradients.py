"""
Gradient-based color themes for FFT visualizer.

Direct color calculation for best performance on LED matrices.
"""
import math
from typing import Tuple
from .base import BaseTheme


class WarmTheme(BaseTheme):
    """Red (low) -> Orange (high) - matches overflow mode first layer."""
    
    name = "warm"
    description = "Red (low) -> Orange (high) - overflow style"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        # Matches overflow mode layer 0: red -> orange
        r = 255
        g = int(165 * height_ratio)  # 0 -> 165 (orange)
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


class SunsetTheme(BaseTheme):
    """Deep purple (low) -> Pink/Orange (mid) -> Yellow/White (high) - sunset/sunrise."""
    
    name = "sunset"
    description = "Sunset gradient: purple -> pink -> orange -> yellow"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio
        
        if ratio < 0.25:
            # Bottom quarter: deep purple/indigo
            intensity = ratio * 4
            r = int(75)# * intensity)
            g = int(0)# * intensity)
            b = int(130)# * intensity)
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
    
    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0, bar_ratio: float = 0.0) -> Tuple[int, int, int]:
        """Sunset overflow: Yellow -> White -> Bright sun core."""
        if layer == 0:
            return self.get_color(height_ratio, column_ratio)
        elif layer == 1:
            # Second layer: yellow/gold -> white-yellow
            r = 255
            g = int(220 + 35 * height_ratio)
            b = int(50 + 180 * height_ratio)
        else:
            # Higher layers: bright white sun
            r = 255
            g = 255
            b = int(230 + 25 * height_ratio)
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


class NeonTheme(BaseTheme):
    """Deep electric blue -> Hot magenta -> Electric cyan - 80s cyberpunk vibes."""
    
    name = "neon"
    description = "Electric blue -> Magenta -> Cyan (cyberpunk)"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        # Three-phase gradient: blue -> magenta -> cyan
        if height_ratio < 0.5:
            # Blue to magenta
            t = height_ratio * 2  # 0 to 1
            r = int(255 * t)          # 0 -> 255
            g = int(50 * (1 - t))     # 50 -> 0
            b = int(150 + 105 * t)    # 150 -> 255
        else:
            # Magenta to cyan
            t = (height_ratio - 0.5) * 2  # 0 to 1
            r = int(255 * (1 - t))    # 255 -> 0
            g = int(255 * t)          # 0 -> 255
            b = 255                   # Stay 255
        return self._apply_brightness(r, g, b)
    
    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0, bar_ratio: float = 0.0) -> Tuple[int, int, int]:
        """Neon overflow: Cyan -> White hot with pink tint."""
        if layer == 0:
            return self.get_color(height_ratio, column_ratio)
        elif layer == 1:
            # Bright cyan -> white with pink tint
            r = int(200 * height_ratio)       # 0 -> 200
            g = 255
            b = 255
        else:
            # White hot with slight pink
            r = 255
            g = int(220 + 35 * height_ratio)  # 220 -> 255
            b = 255
        return self._apply_brightness(r, g, b)


class AuroraTheme(BaseTheme):
    """Deep indigo -> Teal -> Bright green -> Pale white - Northern lights."""
    
    name = "aurora"
    description = "Indigo -> Teal -> Green -> White (northern lights)"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        # Four-phase gradient mimicking aurora
        if height_ratio < 0.33:
            # Deep indigo to teal
            t = height_ratio * 3  # 0 to 1
            r = int(30 * (1 - t))         # 30 -> 0
            g = int(100 * t)              # 0 -> 100
            b = int(80 + 20 * (1 - t))    # 100 -> 80
        elif height_ratio < 0.66:
            # Teal to bright green
            t = (height_ratio - 0.33) * 3  # 0 to 1
            r = 0
            g = int(100 + 155 * t)        # 100 -> 255
            b = int(80 * (1 - t))         # 80 -> 0
        else:
            # Bright green to pale green/white
            t = (height_ratio - 0.66) * 3  # 0 to 1
            r = int(200 * t)              # 0 -> 200
            g = 255
            b = int(200 * t)              # 0 -> 200
        return self._apply_brightness(r, g, b)
    
    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0, bar_ratio: float = 0.0) -> Tuple[int, int, int]:
        """Aurora overflow: White -> soft pink hints (like aurora peaks)."""
        if layer == 0:
            return self.get_color(height_ratio, column_ratio)
        elif layer == 1:
            # Pale green-white to white with pink tint
            r = int(200 + 55 * height_ratio)  # 200 -> 255
            g = 255
            b = int(200 + 30 * height_ratio)  # 200 -> 230
        else:
            # Pink-tinged white (aurora sometimes has pink at peaks)
            r = 255
            g = int(230 + 25 * height_ratio)  # 230 -> 255
            b = int(240 + 15 * height_ratio)  # 240 -> 255
        return self._apply_brightness(r, g, b)


class PlasmaTheme(BaseTheme):
    """Near-black purple -> Electric purple -> Lavender -> White hot."""
    
    name = "plasma"
    description = "Dark purple -> Electric purple -> White (energy)"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        # Three-phase gradient: dark purple -> electric purple -> white
        if height_ratio < 0.4:
            # Dark purple to electric purple
            t = height_ratio / 0.4  # 0 to 1
            r = int(40 + 110 * t)      # 40 -> 150
            g = int(50 * t)            # 0 -> 50
            b = int(60 + 195 * t)      # 60 -> 255
        elif height_ratio < 0.75:
            # Electric purple to bright lavender
            t = (height_ratio - 0.4) / 0.35  # 0 to 1
            r = int(150 + 50 * t)      # 150 -> 200
            g = int(50 + 100 * t)      # 50 -> 150
            b = 255
        else:
            # Lavender to white hot
            t = (height_ratio - 0.75) / 0.25  # 0 to 1
            r = int(200 + 55 * t)      # 200 -> 255
            g = int(150 + 105 * t)     # 150 -> 255
            b = 255
        return self._apply_brightness(r, g, b)
    
    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0, bar_ratio: float = 0.0) -> Tuple[int, int, int]:
        """Plasma overflow: White hot with electric purple edge."""
        if layer == 0:
            return self.get_color(height_ratio, column_ratio)
        elif layer == 1:
            # White hot
            r = 255
            g = int(220 + 35 * height_ratio)  # 220 -> 255
            b = 255
        else:
            # Pure white (maximum energy)
            r = 255
            g = 255
            b = 255
        return self._apply_brightness(r, g, b)