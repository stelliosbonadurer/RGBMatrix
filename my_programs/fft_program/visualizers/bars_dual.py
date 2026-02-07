"""
Dual-layer bar graph visualizer for FFT display.

Renders bass/mid frequencies as a background layer with higher frequencies
overlaid as foreground bars. Allows separate theming for each layer.

Press 'l' to toggle dual mode, 'L' to cycle top layer color mode.
"""
from typing import Optional, List
import numpy as np  # type: ignore

from .base import BaseVisualizer
from themes import get_theme, list_themes


class BarsDualVisualizer(BaseVisualizer):
    """
    Dual-layer bar visualization.
    
    Base layer: Bass/mid frequencies drawn as standard bars (with gradient support)
    Top layer: Higher frequencies drawn as bars on top, using overflow colors or alternate theme
    """
    
    name = "bars_dual"
    description = "Dual-layer bars (l=toggle, L=cycle top color)"
    
    def __init__(self, width: int, height: int, settings):
        super().__init__(width, height, settings)
        
        # Mode flags
        self.gradient_mode = getattr(settings, 'gradient_enabled', False)
        self.dual_enabled = settings.dual.enabled
        
        # Top layer color options: 'overflow' + all available themes
        self.top_color_options = ['overflow'] + list_themes()
        self.top_color_index = 0  # Start with 'overflow'
        if settings.dual.top_color_mode != 'overflow':
            # Find index if a theme is specified
            try:
                self.top_color_index = self.top_color_options.index(settings.dual.top_color_mode)
            except ValueError:
                self.top_color_index = 0
        
        # Top layer theme (None means use overflow colors)
        self.top_theme = None
        self._update_top_theme()
    
    def _update_top_theme(self) -> None:
        """Update top layer theme based on current color mode."""
        mode = self.top_color_options[self.top_color_index]
        if mode == 'overflow':
            self.top_theme = None
        else:
            self.top_theme = get_theme(mode)
    
    def toggle_dual(self) -> bool:
        """Toggle dual mode. Returns new state."""
        self.dual_enabled = not self.dual_enabled
        return self.dual_enabled
    
    def toggle_gradient(self) -> bool:
        """Toggle gradient mode for base layer. Returns new state."""
        self.gradient_mode = not self.gradient_mode
        return self.gradient_mode
    
    def cycle_top_color(self, forward: bool = True) -> str:
        """
        Cycle top layer color mode.
        
        Args:
            forward: True for next, False for previous
            
        Returns:
            Name of new color mode
        """
        if forward:
            self.top_color_index = (self.top_color_index + 1) % len(self.top_color_options)
        else:
            self.top_color_index = (self.top_color_index - 1) % len(self.top_color_options)
        
        self._update_top_theme()
        return self.top_color_options[self.top_color_index]
    
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
        Draw dual-layer visualization.
        
        Args:
            canvas: RGB matrix canvas to draw on
            smoothed_bars: Normalized base layer bar values (0-1)
            peak_heights: Optional peak indicator positions
            top_bars: Optional top layer bar values (0-1), required for dual mode
        """
        if self.theme is None:
            raise RuntimeError("Theme not set. Call set_theme() before draw().")
        
        canvas.Clear()
        
        height = self.height
        num_base_bins = len(smoothed_bars)
        
        # Draw base layer
        self._draw_base_layer(canvas, smoothed_bars, num_base_bins, height)
        
        # Draw top layer if dual mode enabled and data provided
        if self.dual_enabled and top_bars is not None:
            self._draw_top_layer(canvas, top_bars, height)
    
    def _draw_base_layer(
        self,
        canvas,
        bars: np.ndarray,
        num_bins: int,
        height: int
    ) -> None:
        """Draw the base (background) layer as standard bars."""
        for i, bar_value in enumerate(bars):
            if np.isnan(bar_value):
                bar_value = 0.0
            
            bar_value = min(1.0, max(0.0, bar_value))
            bar_height = int(bar_value * height)
            
            if bar_height <= 0:
                continue
            
            column_ratio = i / num_bins
            
            if self.gradient_mode:
                # Per-pixel gradient
                for j in range(bar_height):
                    y = height - 1 - j
                    height_ratio = j / height
                    r, g, b = self.theme.get_color(height_ratio, column_ratio)
                    canvas.SetPixel(i, y, r, g, b)
            else:
                # Uniform color based on bar height
                r, g, b = self.theme.get_color(bar_value, column_ratio)
                for j in range(bar_height):
                    y = height - 1 - j
                    canvas.SetPixel(i, y, r, g, b)
    
    def _draw_top_layer(
        self,
        canvas,
        bars: np.ndarray,
        height: int
    ) -> None:
        """Draw the top (foreground) layer bars."""
        num_top_bins = len(bars)
        
        # Calculate bar width and spacing for top layer
        # Top layer has fewer bins, so bars are wider
        bar_width = self.width // num_top_bins
        
        for i, bar_value in enumerate(bars):
            if np.isnan(bar_value):
                bar_value = 0.0
            
            bar_value = min(1.0, max(0.0, bar_value))
            bar_height = int(bar_value * height)
            
            if bar_height <= 0:
                continue
            
            # Calculate x position (center bars within their slot)
            x_start = i * bar_width
            x_end = min(x_start + bar_width, self.width)
            
            column_ratio = i / num_top_bins
            
            for x in range(x_start, x_end):
                if self.gradient_mode:
                    # Per-pixel gradient
                    for j in range(bar_height):
                        y = height - 1 - j
                        height_ratio = j / height
                        r, g, b = self._get_top_color(height_ratio, column_ratio)
                        canvas.SetPixel(x, y, r, g, b)
                else:
                    # Uniform color
                    r, g, b = self._get_top_color(bar_value, column_ratio)
                    for j in range(bar_height):
                        y = height - 1 - j
                        canvas.SetPixel(x, y, r, g, b)
    
    def _get_top_color(self, height_ratio: float, column_ratio: float) -> tuple:
        """
        Get color for top layer pixel.
        
        Uses overflow colors from base theme, or alternate theme if set.
        """
        if self.top_theme is not None:
            # Use alternate theme
            return self.top_theme.get_color(height_ratio, column_ratio)
        else:
            # Use overflow color (layer 1) from base theme
            return self.theme.get_overflow_color(
                layer=1,
                height_ratio=height_ratio,
                column_ratio=column_ratio,
                frame=self.frame_count,
                bar_ratio=height_ratio
            )
