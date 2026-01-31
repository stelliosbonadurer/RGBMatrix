"""
Empty visualizer that just clears the canvas.

Used when you only want to show peaks without bars.
"""
from typing import Optional
import numpy as np  # type: ignore

from .base import BaseVisualizer


class NoneVisualizer(BaseVisualizer):
    """
    Empty visualization - just clears the canvas.
    
    Use this when you want to display only peaks
    without any bar visualization underneath.
    """
    
    name = "none"
    description = "No bars (peaks only when enabled)"
    
    def draw(
        self,
        canvas,
        smoothed_bars: np.ndarray,
        peak_heights: Optional[np.ndarray] = None
    ) -> None:
        """
        Clear the canvas (no bars drawn).
        
        Args:
            canvas: RGB matrix canvas to draw on
            smoothed_bars: Normalized bar values (unused)
            peak_heights: Unused - peaks are drawn separately
        """
        canvas.Clear()
