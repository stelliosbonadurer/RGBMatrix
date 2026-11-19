"""Structural contracts for the existing FFT data plane.

These protocols describe the capture, analysis, and rendering seams without
adding runtime checks, adapters, or new per-frame data representations.
"""
from typing import Any, Optional, Protocol

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

    def update_frequency_range(self) -> None:
        """Rebuild frequency bins after a frequency-setting change."""
        ...

    def get_fft_magnitudes(self) -> Optional[np.ndarray]:
        """Lease the latest complete single-layer magnitude array, if available."""
        ...

    def release_fft_magnitudes(self) -> None:
        """Release the leased standard magnitude array after scaling reads it."""
        ...


class FrameRenderer(Protocol):
    """Draw the existing NumPy bar arrays directly onto a supplied canvas."""

    def draw(
        self,
        canvas: Any,
        smoothed_bars: np.ndarray,
        peak_heights: Optional[np.ndarray] = None,
    ) -> None:
        ...
