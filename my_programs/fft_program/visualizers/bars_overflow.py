"""
Overflow bar graph visualizer for FFT display.

Bars can exceed display height and wrap with different colors.
"""
from typing import Optional, Tuple
import numpy as np  # type: ignore

from .base import BaseVisualizer


class BarsOverflowVisualizer(BaseVisualizer):
    """
    Bar graph visualization with overflow/stacking.
    
    When bar values exceed 1.0, they wrap around and
    stack with progressively different colors, creating
    a layered effect for loud audio.
    """
    
    name = "bars_overflow"
    description = "Vertical bars with overflow stacking"
    
    def draw(
        self,
        canvas,
        smoothed_bars: np.ndarray,
        peak_heights: Optional[np.ndarray] = None
    ) -> None:
        """
        Draw vertical bars with overflow stacking.
        
        Args:
            canvas: RGB matrix canvas to draw on
            smoothed_bars: Normalized bar values (can exceed 1.0)
            peak_heights: Optional peak indicator positions (0-1)
        """
        if self.theme is None:
            raise RuntimeError("Theme not set. Call set_theme() before draw().")
        
        canvas.Clear()
        
        num_bins = len(smoothed_bars)
        height = self.height
        overflow = self.settings.overflow
        shadow_enabled = self.settings.shadow.enabled and self.shadow_buffer is not None
        
        # Decay shadow buffer once per frame (vectorized)
        if shadow_enabled:
            self.decay_shadow()
            
            # Draw shadows using sparse iteration (only non-zero pixels)
            shadow_i, shadow_j = np.nonzero(self.shadow_buffer)
            for idx in range(len(shadow_i)):
                i, j = shadow_i[idx], shadow_j[idx]
                shadow_val = self.shadow_buffer[i, j]
                y = height - 1 - j
                sc = self.shadow_colors[i, j]
                sr = int(sc[0] * shadow_val)
                sg = int(sc[1] * shadow_val)
                sb = int(sc[2] * shadow_val)
                canvas.SetPixel(i, y, sr, sg, sb)
        
        for i, raw_ratio in enumerate(smoothed_bars):
            # Guard against NaN values
            if np.isnan(raw_ratio):
                raw_ratio = 0.0
            
            # Calculate total pixels to draw (can exceed display height)
            total_pixels = int(raw_ratio * height * overflow.multiplier)
            column_ratio = i / num_bins
            
            if total_pixels > 0:
                # Draw layer by layer
                pixels_drawn = 0
                layer = 0
                
                while pixels_drawn < total_pixels:
                    pixels_this_layer = min(height, total_pixels - pixels_drawn)
                    
                    for j in range(pixels_this_layer):
                        y = height - 1 - j
                        if 0 <= y < height:
                            layer_ratio = j / height
                            
                            # Get color from theme (each theme defines its own overflow colors)
                            r, g, b = self.theme.get_overflow_color(layer, layer_ratio, column_ratio, self.frame_count)
                            
                            canvas.SetPixel(i, y, r, g, b)
                            
                            # Update shadow buffer
                            if shadow_enabled:
                                self.shadow_buffer[i, j] = 1.0
                                self.shadow_colors[i, j] = (r, g, b)
                    
                    pixels_drawn += pixels_this_layer
                    layer += 1
    
    def _get_layer_color(
        self,
        layer: int,
        height_ratio: float,
        column_ratio: float
    ) -> Tuple[int, int, int]:
        """
        Get color for a specific overflow layer.
        
        Args:
            layer: Layer number (0 = base, 1 = first overflow, etc.)
            height_ratio: Position within layer (0-1)
            column_ratio: Column position (0-1)
        
        Returns:
            RGB color tuple
        """
        # All layers now use theme's overflow color method
        return self.theme.get_overflow_color(layer, height_ratio, column_ratio, self.frame_count)
