"""
Unified bar graph visualizer for FFT display.

Combines standard and overflow modes with gradient toggle.
Press 'g' to toggle gradient mode, 'o' to toggle overflow mode.
"""
from typing import Optional, Tuple
import numpy as np  # type: ignore

from .base import BaseVisualizer


class BarsUnifiedVisualizer(BaseVisualizer):
    """
    Unified bar graph visualization with configurable modes.
    
    Modes:
    - Gradient OFF (uniform): One color per bar based on total height
    - Gradient ON: Per-pixel gradient within each bar
    - Overflow OFF: Bars clamped to display height
    - Overflow ON: Bars can exceed height with layered colors
    """
    
    name = "bars"
    description = "Vertical bars (g=gradient, o=overflow)"
    
    def __init__(self, width: int, height: int, settings):
        super().__init__(width, height, settings)
        # Modes - default from settings
        self.gradient_mode = getattr(settings, 'gradient_enabled', False)
        self.overflow_mode = settings.overflow.enabled
        self.bars_enabled = True  # Can be toggled to show only peaks
    
    def toggle_gradient(self) -> bool:
        """Toggle gradient mode. Returns new state."""
        self.gradient_mode = not self.gradient_mode
        return self.gradient_mode
    
    def toggle_overflow(self) -> bool:
        """Toggle overflow mode. Returns new state."""
        self.overflow_mode = not self.overflow_mode
        return self.overflow_mode
    
    def toggle_bars(self) -> bool:
        """Toggle bar drawing. Returns new state."""
        self.bars_enabled = not self.bars_enabled
        return self.bars_enabled
    
    def draw(
        self,
        canvas,
        smoothed_bars: np.ndarray,
        peak_heights: Optional[np.ndarray] = None
    ) -> None:
        """
        Draw vertical bars with configurable gradient and overflow.
        
        Args:
            canvas: RGB matrix canvas to draw on
            smoothed_bars: Normalized bar values (0-1, can exceed if overflow enabled)
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
            self._draw_shadows(canvas, height)
        
        # Skip bar drawing if disabled (peaks-only mode)
        if not self.bars_enabled:
            return
        
        for i, raw_ratio in enumerate(smoothed_bars):
            # Guard against NaN values
            if np.isnan(raw_ratio):
                raw_ratio = 0.0
            
            column_ratio = i / num_bins
            
            if self.overflow_mode:
                self._draw_bar_overflow(canvas, i, raw_ratio, column_ratio, height, shadow_enabled)
            else:
                self._draw_bar_standard(canvas, i, raw_ratio, column_ratio, height, shadow_enabled)
    
    def _draw_shadows(self, canvas, height: int) -> None:
        """Draw shadow pixels using sparse iteration."""
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
    
    def _draw_bar_standard(
        self,
        canvas,
        col: int,
        bar_value: float,
        column_ratio: float,
        height: int,
        shadow_enabled: bool
    ) -> None:
        """Draw a standard bar (clamped to display height)."""
        # Clamp to 0-1 for standard mode
        bar_value = min(1.0, max(0.0, bar_value))
        bar_height = int(bar_value * height)
        
        if bar_height <= 0:
            return
        
        if self.gradient_mode:
            # Per-pixel gradient
            for j in range(bar_height):
                y = height - 1 - j
                height_ratio = j / height
                r, g, b = self.theme.get_color(height_ratio, column_ratio)
                canvas.SetPixel(col, y, r, g, b)
                
                if shadow_enabled:
                    self.shadow_buffer[col, j] = 1.0
                    self.shadow_colors[col, j] = (r, g, b)
        else:
            # Uniform color based on bar height
            r, g, b = self.theme.get_color(bar_value, column_ratio)
            
            for j in range(bar_height):
                y = height - 1 - j
                canvas.SetPixel(col, y, r, g, b)
                
                if shadow_enabled:
                    self.shadow_buffer[col, j] = 1.0
                    self.shadow_colors[col, j] = (r, g, b)
    
    def _draw_bar_overflow(
        self,
        canvas,
        col: int,
        raw_ratio: float,
        column_ratio: float,
        height: int,
        shadow_enabled: bool
    ) -> None:
        """Draw a bar with overflow stacking."""
        overflow = self.settings.overflow
        total_pixels = int(raw_ratio * height * overflow.multiplier)
        
        if total_pixels <= 0:
            return
        
        if self.gradient_mode:
            # Optimized: draw each pixel exactly once
            # Only at most 2 layers are ever visible on screen
            visible_pixels = min(total_pixels, height)
            
            # Calculate which layers are visible
            top_layer = (total_pixels - 1) // height
            remainder = total_pixels % height
            # boundary = where top layer ends (pixels 0 to boundary-1 are top layer)
            boundary = remainder if remainder > 0 else height
            
            for j in range(visible_pixels):
                y = height - 1 - j
                layer_ratio = j / height
                
                if j < boundary:
                    # This pixel shows the top layer
                    layer = top_layer
                else:
                    # This pixel shows the layer below
                    layer = top_layer - 1
                
                r, g, b = self.theme.get_overflow_color(
                    layer, layer_ratio, column_ratio, self.frame_count, raw_ratio
                )
                canvas.SetPixel(col, y, r, g, b)
                
                if shadow_enabled:
                    self.shadow_buffer[col, j] = 1.0
                    self.shadow_colors[col, j] = (r, g, b)
        else:
            # Uniform color mode: entire bar is one color
            # Color = what the top pixel would be in gradient mode
            # Calculate which layer and position the top pixel is in
            top_layer = (total_pixels - 1) // height
            top_position_in_layer = (total_pixels - 1) % height
            top_ratio = top_position_in_layer / height
            
            # Get the color for the top pixel
            r, g, b = self.theme.get_overflow_color(
                top_layer, top_ratio, column_ratio, self.frame_count, raw_ratio
            )
            
            # Draw entire visible portion with this single color
            visible_pixels = min(total_pixels, height)
            for j in range(visible_pixels):
                y = height - 1 - j
                canvas.SetPixel(col, y, r, g, b)
                
                if shadow_enabled:
                    self.shadow_buffer[col, j] = 1.0
                    self.shadow_colors[col, j] = (r, g, b)
