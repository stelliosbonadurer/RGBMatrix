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
            # Calculate total pixels to draw (can exceed display height)
            total_pixels = int(raw_ratio * height * overflow.multiplier)
            
            if total_pixels <= 0:
                continue
            
            column_ratio = i / num_bins
            
            # Draw layer by layer
            pixels_drawn = 0
            layer = 0
            
            while pixels_drawn < total_pixels:
                pixels_this_layer = min(height, total_pixels - pixels_drawn)
                
                for j in range(pixels_this_layer):
                    y = height - 1 - j
                    if 0 <= y < height:
                        layer_ratio = j / height
                        
                        # Get color based on layer
                        if layer == 0:
                            r, g, b = self.theme.get_color(layer_ratio, column_ratio)
                        elif layer == 1:
                            r = 255
                            g = int(165 + 90 * layer_ratio)
                            b = int(255 * layer_ratio)
                        elif layer == 2:
                            r, g, b = overflow.color_2
                        else:
                            r, g, b = overflow.color_3
                        
                        canvas.SetPixel(i, y, r, g, b)
                        
                        # Update shadow buffer
                        if shadow_enabled:
                            self.shadow_buffer[i, j] = 1.0
                            self.shadow_colors[i, j] = (r, g, b)
                
                pixels_drawn += pixels_this_layer
                layer += 1
            
            # Draw peak indicator if enabled (not typical for overflow mode)
            if peak_heights is not None and self.settings.peak.enabled:
                self._draw_peak(canvas, i, peak_heights[i], raw_ratio, column_ratio)
    
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
        overflow = self.settings.overflow
        
        if layer == 0:
            # First layer: use theme colors
            return self.theme.get_color(height_ratio, column_ratio)
        elif layer == 1:
            # Second layer: use theme's overflow color (or default orange -> white)
            return self.theme.get_overflow_color(layer, height_ratio, column_ratio)
        elif layer == 2:
            # Third layer: custom color from settings
            r, g, b = overflow.color_2
        else:
            # Fourth+ layer: custom color from settings
            r, g, b = overflow.color_3
        
        return (r, g, b)
    
    def _draw_peak(
        self,
        canvas,
        x: int,
        peak_value: float,
        bar_value: float,
        column_ratio: float
    ) -> None:
        """
        Draw peak indicator dot.
        
        Args:
            canvas: RGB matrix canvas
            x: Column position
            peak_value: Peak height (0-1)
            bar_value: Current bar value
            column_ratio: Column position ratio
        """
        if peak_value <= 0:
            return
        
        # Clamp peak to display height
        peak_y = int(min(peak_value, 1.0) * self.height)
        y = self.height - 1 - peak_y
        
        if y < 0 or y >= self.height:
            return
        
        # Get peak color
        bar_color = self._get_layer_color(0, bar_value, column_ratio)
        pr, pg, pb = self.theme.get_peak_color(
            self.settings.peak.color_mode,
            bar_color,
            column_ratio
        )
        
        canvas.SetPixel(x, y, pr, pg, pb)
