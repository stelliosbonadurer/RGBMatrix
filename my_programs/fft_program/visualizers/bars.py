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
            
            # Draw shadows first, then bars overwrite
            for i in range(num_bins):
                for j in range(height):
                    shadow_val = self.shadow_buffer[i, j]
                    if shadow_val > 0:
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
            
            if bar_height <= 0:
                continue
            
            # Column ratio for position-based themes
            column_ratio = i / num_bins
            
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
            
            # Draw peak indicator if enabled
            if peak_heights is not None and self.settings.peak.enabled:
                self._draw_peak(canvas, i, peak_heights[i], bar_value, column_ratio)
    
    def _draw_peak(
        self,
        canvas,
        x: int,
        peak_value: float,
        bar_value: float,
        column_ratio: float
    ) -> None:
        """
        Draw peak indicator dot above bar.
        
        Args:
            canvas: RGB matrix canvas
            x: Column position
            peak_value: Peak height (0-1)
            bar_value: Current bar height (0-1)
            column_ratio: Column position ratio for theme
        """
        if peak_value <= 0:
            return
        
        peak_y = int(peak_value * self.height)
        y = self.height - 1 - peak_y
        
        if y < 0 or y >= self.height:
            return
        
        # Get peak color based on mode
        bar_color = self.theme.get_color(bar_value, column_ratio)
        pr, pg, pb = self.theme.get_peak_color(
            self.settings.peak.color_mode,
            bar_color,
            column_ratio
        )
        
        canvas.SetPixel(x, y, pr, pg, pb)
