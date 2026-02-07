"""
Unified bar graph visualizer for FFT display.

Combines standard and overflow modes with gradient toggle.
Press 'g' to toggle gradient mode, 'o' to toggle overflow mode, 'f' for full mode, 'd' for debug mode.
"""
from typing import Optional, Tuple, List
import numpy as np  # type: ignore

from .base import BaseVisualizer
from themes import get_theme, list_themes


class BarsUnifiedVisualizer(BaseVisualizer):
    """
    Unified bar graph visualization with configurable modes.
    
    Modes:
    - Gradient OFF (uniform): One color per bar based on total height
    - Gradient ON: Per-pixel gradient within each bar
    - Overflow OFF: Bars clamped to display height
    - Overflow ON: Bars can exceed height with layered colors
    - Full: Full screen with gradient scaled by FFT data
    - Debug: Full screen showing static color gradient for each column
    """
    
    name = "bars"
    description = "Vertical bars (g=gradient, o=overflow, f=full, d=debug, l=dual)"
    
    def __init__(self, width: int, height: int, settings):
        super().__init__(width, height, settings)
        # Modes - default from settings
        self.gradient_mode = getattr(settings, 'gradient_enabled', False)
        self.overflow_mode = settings.overflow.enabled
        self.bars_enabled = True  # Can be toggled to show only peaks
        self.full_mode = False  # Full mode: entire screen lit, gradient scaled by FFT
        self.debug_mode = False  # Debug mode shows full screen gradient
        
        # Dual mode settings
        self.dual_enabled = False
        self.top_color_options = ['overflow'] + list_themes()
        self.top_color_index = 0
        self.top_theme = None  # None = use overflow colors
    
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
    
    def toggle_full(self) -> bool:
        """Toggle full mode. Returns new state."""
        self.full_mode = not self.full_mode
        return self.full_mode
    
    def toggle_debug(self) -> bool:
        """Toggle debug mode. Returns new state."""
        self.debug_mode = not self.debug_mode
        return self.debug_mode
    
    def toggle_dual(self) -> bool:
        """Toggle dual mode. Returns new state."""
        self.dual_enabled = not self.dual_enabled
        return self.dual_enabled
    
    def cycle_top_color(self, forward: bool = True) -> str:
        """Cycle top layer color mode. Returns new mode name."""
        if forward:
            self.top_color_index = (self.top_color_index + 1) % len(self.top_color_options)
        else:
            self.top_color_index = (self.top_color_index - 1) % len(self.top_color_options)
        
        mode = self.top_color_options[self.top_color_index]
        if mode == 'overflow':
            self.top_theme = None
        else:
            self.top_theme = get_theme(mode)
        return mode
    
    def get_top_color_mode(self) -> str:
        """Get current top layer color mode name."""
        return self.top_color_options[self.top_color_index]
    
    def draw(
        self,
        canvas,
        smoothed_bars: np.ndarray,
        peak_heights: Optional[np.ndarray] = None,
        top_bars: Optional[np.ndarray] = None
    ) -> None:
        """
        Draw vertical bars with configurable gradient and overflow.
        
        Args:
            canvas: RGB matrix canvas to draw on
            smoothed_bars: Normalized bar values (0-1, can exceed if overflow enabled)
            peak_heights: Optional peak indicator positions (0-1)
            top_bars: Optional top layer bars for dual mode (0-1)
        """
        if self.theme is None:
            raise RuntimeError("Theme not set. Call set_theme() before draw().")
        
        canvas.Clear()
        
        num_bins = len(smoothed_bars)
        height = self.height
        shadow_enabled = self.settings.shadow.enabled and self.shadow_buffer is not None
        
        # Debug mode: draw full screen with static color gradient per column
        if self.debug_mode:
            self._draw_debug(canvas, num_bins, height)
            return
        
        # Full mode: draw full screen with gradient scaled by FFT data
        if self.full_mode:
            self._draw_full(canvas, smoothed_bars, num_bins, height)
            return
        
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
        
        # Draw top layer if dual mode enabled and data provided
        if self.dual_enabled and top_bars is not None:
            self._draw_top_layer(canvas, top_bars, height)
    
    def _draw_top_layer(self, canvas, bars: np.ndarray, height: int) -> None:
        """Draw the top (foreground) layer bars for dual mode."""
        num_top_bins = len(bars)
        bar_width = self.width // num_top_bins
        
        for i, bar_value in enumerate(bars):
            if np.isnan(bar_value):
                bar_value = 0.0
            
            bar_value = min(1.0, max(0.0, bar_value))
            bar_height = int(bar_value * height)
            
            if bar_height <= 0:
                continue
            
            x_start = i * bar_width
            x_end = min(x_start + bar_width, self.width)
            column_ratio = i / num_top_bins
            
            for x in range(x_start, x_end):
                if self.gradient_mode:
                    for j in range(bar_height):
                        y = height - 1 - j
                        height_ratio = j / height
                        r, g, b = self._get_top_color(height_ratio, column_ratio)
                        canvas.SetPixel(x, y, r, g, b)
                else:
                    r, g, b = self._get_top_color(bar_value, column_ratio)
                    for j in range(bar_height):
                        y = height - 1 - j
                        canvas.SetPixel(x, y, r, g, b)
    
    def _get_top_color(self, height_ratio: float, column_ratio: float) -> tuple:
        """Get color for top layer pixel (overflow or alternate theme)."""
        if self.top_theme is not None:
            return self.top_theme.get_color(height_ratio, column_ratio)
        else:
            return self.theme.get_overflow_color(
                layer=1,
                height_ratio=height_ratio,
                column_ratio=column_ratio,
                frame=self.frame_count,
                bar_ratio=height_ratio
            )
    
    def _draw_full(self, canvas, smoothed_bars: np.ndarray, num_bins: int, height: int) -> None:
        """
        Draw full mode: entire screen lit with crossfade at bar height.
        
        - Top color from top of screen down to (bar_height + 10)
        - Crossfade zone from (bar_height + 10) to (bar_height - 10)
        - Base color from (bar_height - 10) to bottom
        """
        fade_radius = 10  # pixels above and below bar height for crossfade
        
        # Clean and clamp bar values once
        bar_values = np.nan_to_num(smoothed_bars, nan=0.0)
        bar_values = np.clip(bar_values, 0.0, 1.0)
        
        for col in range(num_bins):
            bar_value = bar_values[col]
            column_ratio = col / num_bins
            
            # Get top and base colors for this column
            top_r, top_g, top_b = self.theme.get_color(1.0, column_ratio)
            base_r, base_g, base_b = self.theme.get_color(0.0, column_ratio)
            
            # Calculate bar height in pixels (j index, 0=bottom)
            bar_height_px = int(bar_value * (height - 1))
            
            # Define crossfade zone boundaries (in j coordinates, 0=bottom)
            fade_top = bar_height_px + fade_radius  # above this = top color
            fade_bottom = bar_height_px - fade_radius  # below this = base color
            
            for j in range(height):
                y = height - 1 - j  # screen y coordinate
                
                if j >= fade_top:
                    # Above crossfade zone: top color
                    r, g, b = top_r, top_g, top_b
                elif j <= fade_bottom:
                    # Below crossfade zone: base color
                    r, g, b = base_r, base_g, base_b
                else:
                    # In crossfade zone: blend between base and top
                    # fade_t goes from 0 (at fade_bottom) to 1 (at fade_top)
                    fade_t = (j - fade_bottom) / (fade_top - fade_bottom)
                    r = int(base_r + (top_r - base_r) * fade_t)
                    g = int(base_g + (top_g - base_g) * fade_t)
                    b = int(base_b + (top_b - base_b) * fade_t)
                
                canvas.SetPixel(col, y, r, g, b)
    
    def _draw_debug(self, canvas, num_bins: int, height: int) -> None:
        """
        Draw debug mode: full screen with static color gradient per column.
        
        Each column shows the full color scale from base color (bottom)
        to top color (top), using non-gradient mode coloring (uniform
        color based on height ratio).
        """
        for col in range(num_bins):
            column_ratio = col / num_bins
            
            for j in range(height):
                y = height - 1 - j
                # height_ratio represents how high this pixel is (0=bottom, 1=top)
                height_ratio = j / (height - 1) if height > 1 else 0.0
                
                # Use the same coloring as non-gradient mode:
                # color is based on total bar height, which equals height_ratio here
                r, g, b = self.theme.get_color(height_ratio, column_ratio)
                canvas.SetPixel(col, y, r, g, b)
    
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
