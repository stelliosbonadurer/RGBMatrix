"""
Gradient-based color themes for FFT visualizer.

Direct color calculation for best performance on LED matrices.
"""
import math
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
    
    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0) -> Tuple[int, int, int]:
        """Classic overflow: continues naturally from red at top of base layer."""
        if layer == 0:
            # Base layer uses theme colors (blue -> green -> red)
            return self.get_color(height_ratio, column_ratio)
        elif layer == 1:
            # Layer 1: red -> orange (continuing from where layer 0 ends at red)
            r = 255
            g = int(165 * height_ratio)  # 0 -> 165 (orange)
            b = 0
        elif layer == 2:
            # Layer 2: orange -> yellow
            r = 255
            g = int(165 + 90 * height_ratio)  # 165 -> 255
            b = 0
        else:
            # Higher layers: yellow -> white
            r = 255
            g = 255
            b = int(255 * height_ratio)
        return self._apply_brightness(r, g, b)


class ClassicInvertedTheme(BaseTheme):
    """Yellow (low) -> Magenta (mid) -> Cyan (high) - inverted classic."""
    
    name = "classic_inverted"
    description = "Yellow (low) -> Magenta (mid) -> Cyan (high)"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio
        # Inverted classic: 255 - each component
        if ratio < 0.5:
            r = 255
            g = int(255 * (1 - ratio * 2))
            b = int(255 * (ratio * 2))
        else:
            r = int(255 * (1 - (ratio - 0.5) * 2))
            g = int(255 * ((ratio - 0.5) * 2))
            b = 255
        return self._apply_brightness(r, g, b)
    
    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0) -> Tuple[int, int, int]:
        """Inverted overflow: continues naturally from cyan at top of base layer."""
        if layer == 0:
            # Base layer uses theme colors (yellow -> magenta -> cyan)
            return self.get_color(height_ratio, column_ratio)
        elif layer == 1:
            # Layer 1: cyan -> light cyan (adding red gradually)
            r = int(128 * height_ratio)  # 0 -> 128
            g = 255
            b = 255
        elif layer == 2:
            # Layer 2: light cyan -> white
            r = int(128 + 127 * height_ratio)  # 128 -> 255
            g = 255
            b = 255
        else:
            # Higher layers: stay white
            r = 255
            g = 255
            b = 255
        return self._apply_brightness(r, g, b)


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
    
    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0) -> Tuple[int, int, int]:
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


class FireTheme(BaseTheme):
    """Red (low) -> Orange -> Yellow -> White tips (high) - realistic fire."""
    
    name = "fire"
    description = "Fire gradient: red -> orange -> yellow -> white tips"
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        ratio = height_ratio
        
        if ratio < 0.4:
            # Bottom: bright red -> orange
            intensity = ratio / 0.4
            r = 255
            g = int(165 * intensity)
            b = 0
        elif ratio < 0.7:
            # Middle: orange -> yellow
            intensity = (ratio - 0.4) / 0.3
            r = 255
            g = int(165 + (255 - 165) * intensity)
            b = 0
        else:
            # Top: yellow -> white (hot tips)
            intensity = (ratio - 0.7) / 0.3
            r = 255
            g = 255
            b = int(220 * intensity)
        
        return self._apply_brightness(r, g, b)
    
    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0) -> Tuple[int, int, int]:
        """Fire overflow: Column-varied flames (left=red, right=orange)."""
        # Static column variation: left side more red, right side more orange
        hue_shift = column_ratio * 0.3  # 0-0.3 shift toward orange
        
        if layer == 0:
            ratio = height_ratio
            if ratio < 0.4:
                intensity = ratio / 0.4
                r = 255
                g = int((165 + hue_shift * 90) * intensity)
                b = 0
            elif ratio < 0.7:
                intensity = (ratio - 0.4) / 0.3
                r = 255
                g = int(165 + hue_shift * 90 + (255 - 165 - hue_shift * 90) * intensity)
                b = 0
            else:
                intensity = (ratio - 0.7) / 0.3
                r = 255
                g = 255
                b = int(220 * intensity)
            return self._apply_brightness(r, g, b)
        elif layer == 1:
            # Second layer: white hot
            r = 255
            g = 255
            b = int(200 + 55 * height_ratio)
        else:
            # Higher layers: blue-white core
            r = int(255 - 55 * height_ratio)
            g = int(255 - 30 * height_ratio)
            b = 255
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
    
    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0) -> Tuple[int, int, int]:
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
    
    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0) -> Tuple[int, int, int]:
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
    
    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0) -> Tuple[int, int, int]:
        """Purple overflow: Pink -> Hot pink -> White-pink glow."""
        if layer == 0:
            return self.get_color(height_ratio, column_ratio)
        elif layer == 1:
            # Second layer: pink -> hot pink
            r = 255
            g = int(105 + 75 * height_ratio)  # 105 -> 180
            b = int(180 + 40 * height_ratio)  # 180 -> 220
        else:
            # Higher layers: hot pink -> white-pink
            r = 255
            g = int(180 + 75 * height_ratio)
            b = int(220 + 35 * height_ratio)
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
    
    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0) -> Tuple[int, int, int]:
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
    
    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> Tuple[int, int, int]:
        # Red on left, goes through spectrum, ends at magenta on right (no wrap back to red)
        hue = column_ratio * 300  # 0-300 degrees: red -> yellow -> green -> cyan -> blue -> magenta
        
        # HSV to RGB conversion (saturation=1, value=1) - full brightness always
        c = 1.0
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
    
    def get_overflow_color(self, layer: int, height_ratio: float, column_ratio: float = 0.0, frame: int = 0) -> Tuple[int, int, int]:
        """Rainbow overflow: Continue rainbow with increasing brightness/white."""
        if layer == 0:
            return self.get_color(height_ratio, column_ratio)
        else:
            # Higher layers: rainbow colors washed toward white
            hue = column_ratio * 300  # Match base layer
            # More white blend as layers increase
            white_blend = min(0.3 + layer * 0.2, 0.8)
            c = 1.0
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
            
            # Blend toward white
            r = int((r * (1 - white_blend) + white_blend) * 255)
            g = int((g * (1 - white_blend) + white_blend) * 255)
            b = int((b * (1 - white_blend) + white_blend) * 255)
            return self._apply_brightness(r, g, b)