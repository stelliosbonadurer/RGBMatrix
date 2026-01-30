"""
Base visualizer class for FFT display modes.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING
import numpy as np  # type: ignore

if TYPE_CHECKING:
    from themes.base import BaseTheme
    from config.settings import Settings


class BaseVisualizer(ABC):
    """
    Abstract base class for all visualizers.
    
    Subclasses must implement the draw() method to render
    FFT data to the LED matrix canvas.
    """
    
    name: str = "base"
    description: str = "Base visualizer"
    
    def __init__(self, width: int, height: int, settings: 'Settings'):
        """
        Initialize visualizer.
        
        Args:
            width: Matrix width in pixels
            height: Matrix height in pixels
            settings: Application settings
        """
        self.width = width
        self.height = height
        self.settings = settings
        self.theme: Optional['BaseTheme'] = None
    
    def set_theme(self, theme: 'BaseTheme') -> None:
        """
        Set the color theme for this visualizer.
        
        Args:
            theme: Theme instance to use for colors
        """
        self.theme = theme
    
    @abstractmethod
    def draw(
        self,
        canvas,
        smoothed_bars: np.ndarray,
        peak_heights: Optional[np.ndarray] = None
    ) -> None:
        """
        Draw one frame of the visualization.
        
        Args:
            canvas: RGB matrix canvas to draw on
            smoothed_bars: Normalized bar values (0-1, or >1 for overflow)
            peak_heights: Optional peak indicator positions (0-1)
        """
        pass
    
    def on_settings_changed(self, settings: 'Settings') -> None:
        """
        Called when settings are updated.
        
        Override to handle setting changes that require
        recalculation or state updates.
        
        Args:
            settings: New settings
        """
        self.settings = settings
