"""
Base theme class for FFT visualizer color schemes.

Uses direct color calculation (not LUT) for best performance
on small displays like 64x64 LED matrices.
"""
from abc import ABC, abstractmethod
from typing import Tuple


class BaseTheme(ABC):
    """Abstract base class for color themes."""
    
    name: str = "base"
    description: str = "Base theme"
    
    def __init__(self, brightness_boost: float = 1.0):
        """
        Initialize theme.
        
        Args:
            brightness_boost: Overall brightness multiplier (1.0 = normal)
        """
        self.brightness_boost = brightness_boost
    
    @abstractmethod
    def get_color(
        self,
        height_ratio: float,
        column_ratio: float = 0.0
    ) -> Tuple[int, int, int]:
        """
        Get RGB color for a pixel based on position.
        
        Args:
            height_ratio: 0-1 value representing bar height (0=bottom, 1=top)
            column_ratio: 0-1 value representing column position (0=left, 1=right)
        
        Returns:
            Tuple of (r, g, b) values 0-255
        """
        pass
    
    def get_overflow_color(
        self,
        layer: int,
        height_ratio: float,
        column_ratio: float = 0.0,
        frame: int = 0,
        bar_ratio: float = 0.0
    ) -> Tuple[int, int, int]:
        """
        Get RGB color for overflow layers.
        
        Default implementation - themes should override for custom colors.
        
        Args:
            layer: Overflow layer number (0 = first/base layer, 1 = second, etc.)
            height_ratio: 0-1 value within the current layer
            column_ratio: 0-1 value representing column position
            frame: Current frame number for animation effects
            bar_ratio: Total bar height ratio (0-1+) for uniform column coloring
        
        Returns:
            Tuple of (r, g, b) values 0-255
        """
        if layer == 0:
            # First layer: use theme's main gradient
            return self.get_color(height_ratio, column_ratio)
        elif layer == 1:
            # Second layer: default red -> orange
            r = 255
            g = int(165 * height_ratio)  # 0 -> 165 (orange)
            b = 0
        elif layer == 2:
            # Third layer: orange -> white
            r = 255
            g = int(165 + (255 - 165) * height_ratio)  # 165 -> 255
            b = int(255 * height_ratio)  # 0 -> 255
        else:
            # Higher layers: white
            return self._apply_brightness(255, 255, 255)
        
        return self._apply_brightness(r, g, b)
    
    def get_peak_color(
        self,
        mode: str,
        bar_color: Tuple[int, int, int],
        column_ratio: float = 0.0
    ) -> Tuple[int, int, int]:
        """
        Get RGB color for peak indicator.
        
        Args:
            mode: Peak color mode ('white', 'bar', 'contrast', 'peak')
            bar_color: Current bar color for reference
            column_ratio: Column position for position-based themes
        
        Returns:
            Tuple of (r, g, b) values 0-255
        """
        if mode == 'white':
            return (255, 255, 255)
        elif mode == 'bar':
            return bar_color
        elif mode == 'contrast':
            return (255 - bar_color[0], 255 - bar_color[1], 255 - bar_color[2])
        elif mode == 'peak':
            # Use the color for max height (ratio = 1.0)
            return self.get_color(1.0, column_ratio)
        else:
            return (255, 255, 255)
    
    def _apply_brightness(self, r: int, g: int, b: int) -> Tuple[int, int, int]:
        """Apply brightness boost and clamp to valid range."""
        return (
            min(255, int(r * self.brightness_boost)),
            min(255, int(g * self.brightness_boost)),
            min(255, int(b * self.brightness_boost))
        )
