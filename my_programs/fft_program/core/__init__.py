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
from core.output import (
    DIRECT_OUTPUT_PATH,
    OUTPUT_PATH_ENVIRONMENT,
    PILLOW_BULK_OUTPUT_PATH,
    PillowBulkBarsOutputPlan,
    compile_pillow_bulk_bars_output,
)
from core.scaling import ScalingProcessor
from core.matrix_app import MatrixApp

__all__ = [
    'AudioProcessor',
    'AudioCapture',
    'AnalysisPlanValidationError',
    'FFTData',
    'FrameRenderer',
    'MagnitudeAnalyzer',
    'DIRECT_OUTPUT_PATH',
    'OUTPUT_PATH_ENVIRONMENT',
    'PILLOW_BULK_OUTPUT_PATH',
    'PillowBulkBarsOutputPlan',
    'ScalingProcessor',
    'StandardAnalysisCandidate',
    'StandardAnalysisPlan',
    'compile_standard_analysis_plan',
    'compile_pillow_bulk_bars_output',
    'MatrixApp',
]
