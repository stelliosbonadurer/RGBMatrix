"""Core module for FFT visualizer."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.audio import AudioProcessor, FFTData
from core.audio_source import (
    AUDIO_SOURCE_IDS,
    AudioSourceResolutionError,
    resolve_audio_source,
)
from core.analyzer_registry import (
    AnalyzerRegistry,
    AnalyzerRegistryError,
    compile_standard_analyzer_plan,
)
from core.analysis_plan import (
    AnalysisPlanValidationError,
    StandardAnalysisCandidate,
    StandardAnalysisPlan,
    compile_standard_analysis_plan,
)
from core.interfaces import AudioCapture, FrameRenderer, MagnitudeAnalyzer
from core.layer_plan import (
    ActiveLayerPlan,
    LayerPlanCompilationError,
    OrderedLayerPlan,
    compile_current_bars_graph,
    compile_current_bars_layer_plan,
    current_bars_runtime_capabilities,
    current_bars_runtime_definition,
    current_bars_settings_from_runtime,
)
from core.runtime_graph import compile_runtime_graph
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
    'AUDIO_SOURCE_IDS',
    'AudioCapture',
    'AudioSourceResolutionError',
    'ActiveLayerPlan',
    'AnalyzerRegistry',
    'AnalyzerRegistryError',
    'AnalysisPlanValidationError',
    'FFTData',
    'FrameRenderer',
    'LayerPlanCompilationError',
    'MagnitudeAnalyzer',
    'DIRECT_OUTPUT_PATH',
    'OUTPUT_PATH_ENVIRONMENT',
    'PILLOW_BULK_OUTPUT_PATH',
    'PillowBulkBarsOutputPlan',
    'OrderedLayerPlan',
    'ScalingProcessor',
    'StandardAnalysisCandidate',
    'StandardAnalysisPlan',
    'compile_standard_analysis_plan',
    'compile_standard_analyzer_plan',
    'compile_current_bars_graph',
    'compile_current_bars_layer_plan',
    'compile_runtime_graph',
    'current_bars_runtime_capabilities',
    'current_bars_runtime_definition',
    'current_bars_settings_from_runtime',
    'compile_pillow_bulk_bars_output',
    'resolve_audio_source',
    'MatrixApp',
]
