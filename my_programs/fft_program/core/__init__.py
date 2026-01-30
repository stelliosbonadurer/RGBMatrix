"""Core module for FFT visualizer."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.audio import AudioProcessor, FFTData
from core.scaling import ScalingProcessor
from core.matrix_app import MatrixApp

__all__ = [
    'AudioProcessor',
    'FFTData',
    'ScalingProcessor',
    'MatrixApp',
]
