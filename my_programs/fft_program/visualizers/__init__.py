"""Visualizer module for FFT display modes."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from visualizers.base import BaseVisualizer
from visualizers.bars_unified import BarsUnifiedVisualizer
from visualizers.bars_dual import BarsDualVisualizer
from visualizers.peaks import draw_peaks
from visualizers.registry import get_visualizer, register_visualizer, list_visualizers

__all__ = [
    'BaseVisualizer',
    'BarsUnifiedVisualizer',
    'BarsDualVisualizer',
    'draw_peaks',
    'get_visualizer',
    'register_visualizer',
    'list_visualizers',
]
