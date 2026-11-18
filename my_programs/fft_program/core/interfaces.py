"""Structural contracts for the existing FFT data plane.

These protocols describe the legacy capture, analysis, and rendering seams without
adding runtime checks, adapters, or new per-frame data representations.
"""
from typing import Any, List, Optional, Protocol

import numpy as np  # type: ignore


class AudioCapture(Protocol):
    """Lifecycle contract for the current audio capture source."""

    def setup(self) -> int:
        """Initialize capture and return the source sample rate."""
        ...

    def start(self) -> None:
        """Start capture."""
        ...

    def stop(self) -> None:
        """Stop capture."""
        ...

    def __enter__(self) -> "AudioCapture":
        ...

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        ...


class MagnitudeAnalyzer(Protocol):
    """Contract for the current NumPy FFT and magnitude projection path."""

    layers_enabled: bool

    def update_frequency_range(self) -> None:
        """Rebuild frequency bins after a frequency-setting change."""
        ...

    def setup_layers(self, layer_configs: List) -> None:
        """Initialize the existing layered magnitude projections."""
        ...

    def get_fft_magnitudes(self) -> Optional[np.ndarray]:
        """Return the current single-layer magnitude array, if available."""
        ...

    def get_layer_magnitudes(self) -> Optional[List[np.ndarray]]:
        """Return the current list of layered magnitude arrays, if available."""
        ...


class FrameRenderer(Protocol):
    """Draw the existing NumPy bar arrays directly onto a supplied canvas."""

    def draw(
        self,
        canvas: Any,
        smoothed_bars: np.ndarray,
        peak_heights: Optional[np.ndarray] = None,
        layer_bars: Optional[List[Optional[np.ndarray]]] = None,
        layer_peaks: Optional[List[Optional[np.ndarray]]] = None,
    ) -> None:
        ...
