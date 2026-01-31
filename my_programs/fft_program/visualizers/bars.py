"""
Standard bar graph visualizer for FFT display.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Optional
import numpy as np  # type: ignore

from visualizers.base import BaseVisualizer


class BarsVisualizer(BaseVisualizer):
    """
    Classic vertical bar graph visualization.
    
    Each frequency bin is displayed as a vertical bar,
    with height proportional to magnitude and color
    determined by the active theme.
    """
    
    name = "bars"
    description = "Classic vertical bar graph"
    
    def draw(
        self,
        canvas,
        smoothed_bars: np.ndarray,
        peak_heights: Optional[np.ndarray] = None
    ) -> None:
        """
        Draw vertical bars for each frequency bin.
        
        Args:
            canvas: RGB matrix canvas to draw on
            smoothed_bars: Normalized bar values (0-1)
            peak_heights: Optional peak indicator positions (0-1)
        """
        if self.theme is None:
            raise RuntimeError("Theme not set. Call set_theme() before draw().")
        
        canvas.Clear()
        
        num_bins = len(smoothed_bars)
        height = self.height
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
        
        for i, bar_value in enumerate(smoothed_bars):
            # Clamp to 0-1 for standard bars mode
            bar_value = min(1.0, max(0.0, bar_value))
            bar_height = int(bar_value * height)
            
            # Column ratio for position-based themes
            column_ratio = i / num_bins
            
            if bar_height > 0:
                # Get color once per bar
                r, g, b = self.theme.get_color(bar_value, column_ratio)
                
                # Draw bar from bottom up
                for j in range(bar_height):
                    y = height - 1 - j
                    canvas.SetPixel(i, y, r, g, b)
                    
                    # Update shadow buffer: set to 1.0 and store color
                    if shadow_enabled:
                        self.shadow_buffer[i, j] = 1.0
                        self.shadow_colors[i, j] = (r, g, b)
