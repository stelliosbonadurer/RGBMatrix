"""Core module for FFT visualizer."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.audio import AudioProcessor, FFTData
from core.analysis_plan import (
    AnalysisPlanValidationError,
    StandardAnalysisCandidate,
    StandardAnalysisPlan,
    compile_standard_analysis_plan,
)
from core.interfaces import AudioCapture, FrameRenderer, MagnitudeAnalyzer
from core.scaling import ScalingProcessor
from core.matrix_app import MatrixApp

__all__ = [
    'AudioProcessor',
    'AudioCapture',
    'AnalysisPlanValidationError',
    'FFTData',
    'FrameRenderer',
    'MagnitudeAnalyzer',
    'ScalingProcessor',
    'StandardAnalysisCandidate',
    'StandardAnalysisPlan',
    'compile_standard_analysis_plan',
    'MatrixApp',
]
