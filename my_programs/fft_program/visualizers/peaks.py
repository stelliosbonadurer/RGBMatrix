"""
Standalone peak overlay for FFT display.

Draws peak indicators independently of any bar visualizer.
"""
from typing import TYPE_CHECKING
import numpy as np  # type: ignore

if TYPE_CHECKING:
    from themes.base import BaseTheme
    from config.settings import Settings


def draw_peaks(
    canvas,
    peak_heights: np.ndarray,
    theme: 'BaseTheme',
    settings: 'Settings',
    height: int
) -> None:
    """
    Draw peak indicators as a standalone overlay.
    
    This function draws peaks independently of any bar visualization,
    allowing peaks to work with bars, bars_overflow, or no bars at all.
    
    Args:
        canvas: RGB matrix canvas to draw on
        peak_heights: Peak height values (0-1) for each column
        theme: Color theme to use for peak colors
        settings: Application settings (for peak color mode)
        height: Display height in pixels
    """
    num_bins = len(peak_heights)
    color_mode = settings.peak.color_mode
    
    for i, peak_value in enumerate(peak_heights):
        # Guard against NaN
        if np.isnan(peak_value):
            peak_value = 0.0
        
        # Clamp peak to at least 0 (bottom row) - peaks rest on floor
        peak_y = max(0, int(min(peak_value, 1.0) * height))
        y = height - 1 - peak_y
        
        if y < 0 or y >= height:
            continue
        
        column_ratio = i / num_bins
        
        # Get peak color based on mode
        # Use peak's own height for color reference (since we may not have bars)
        reference_color = theme.get_color(peak_value, column_ratio)
        pr, pg, pb = theme.get_peak_color(color_mode, reference_color, column_ratio)
        
        canvas.SetPixel(i, y, pr, pg, pb)
