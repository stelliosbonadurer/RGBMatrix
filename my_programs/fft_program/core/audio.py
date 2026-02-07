"""
Audio processing and FFT computation for FFT visualizer.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # type: ignore
from dataclasses import dataclass
from typing import List, Optional, Callable, Tuple
import sounddevice as sd  # type: ignore

from config.settings import AudioSettings, FrequencySettings, SensitivitySettings


@dataclass
class FFTData:
    """Processed FFT data passed to visualizers."""
    bars: np.ndarray              # Raw bar values after noise floor
    smoothed_bars: np.ndarray     # After smoothing applied
    normalized_bars: np.ndarray   # Normalized 0-1 (or >1 for overflow)
    peak_heights: np.ndarray      # Peak indicator positions (0-1)
    raw_magnitudes: np.ndarray    # Raw FFT magnitudes
    current_scale: float          # Current normalization scale
    sample_rate: int              # Audio sample rate
    num_bins: int                 # Number of frequency bins


class AudioProcessor:
    """
    Handles audio capture and FFT processing.
    
    Responsible for:
    - Audio input stream management
    - FFT computation
    - Frequency binning with logarithmic distribution
    - Noise floor subtraction
    """
    
    def __init__(
        self,
        audio_settings: AudioSettings,
        freq_settings: FrequencySettings,
        sensitivity_settings: SensitivitySettings,
        num_bins: int
    ):
        """
        Initialize audio processor.
        
        Args:
            audio_settings: Audio device and buffer configuration
            freq_settings: Frequency range configuration
            sensitivity_settings: Noise floor and frequency weighting
            num_bins: Number of frequency bins (typically matrix width)
        """
        self.audio_settings = audio_settings
        self.freq_settings = freq_settings
        self.sensitivity_settings = sensitivity_settings
        self.num_bins = num_bins
        
        # Audio state
        self.latest_samples: Optional[np.ndarray] = None
        self.have_data: bool = False
        self.sample_rate: int = 0
        
        # FFT state (initialized in setup())
        self.freqs: Optional[np.ndarray] = None
        self.bin_masks: List[np.ndarray] = []
        self.bin_weights: Optional[np.ndarray] = None
        self.window: Optional[np.ndarray] = None
        
        # Optimized bin lookup (initialized in setup())
        self.bin_indices: Optional[List[np.ndarray]] = None  # Pre-computed indices for each bin
        self.empty_bins: Optional[np.ndarray] = None  # Mask for bins with no frequency coverage
        
        # Stream (initialized in start())
        self._stream: Optional[sd.InputStream] = None
        
        # Dual mode state (initialized in setup_dual())
        self.dual_enabled: bool = False
        self.dual_base_indices: Optional[List[np.ndarray]] = None
        self.dual_base_weights: Optional[np.ndarray] = None
        self.dual_base_empty: Optional[np.ndarray] = None
        self.dual_top_indices: Optional[List[np.ndarray]] = None
        self.dual_top_weights: Optional[np.ndarray] = None
        self.dual_top_empty: Optional[np.ndarray] = None
        self.dual_base_bins: int = 64
        self.dual_top_bins: int = 32
    
    def setup(self) -> int:
        """
        Initialize FFT parameters based on audio device.
        
        Returns:
            Sample rate of the audio device
        
        Raises:
            RuntimeError: If audio device not found
        """
        # Query device to get native sample rate
        try:
            device_info = sd.query_devices(self.audio_settings.device, 'input')
        except ValueError as e:
            raise RuntimeError(f"Audio device '{self.audio_settings.device}' not found: {e}")
        
        self.sample_rate = int(device_info['default_samplerate'])
        print(f"Using device: {device_info['name']} at {self.sample_rate} Hz")
        
        # Report frequency range
        freq_min = self.freq_settings.active_min_freq
        freq_max = self.freq_settings.active_max_freq
        mode = "ZOOM MODE" if self.freq_settings.zoom_mode else "Full range"
        print(f"{mode}: {freq_min} Hz - {freq_max} Hz")
        
        # Initialize FFT parameters
        self.latest_samples = np.zeros(self.audio_settings.block_size, dtype=np.float32)
        self.freqs = np.fft.rfftfreq(self.audio_settings.fft_size, 1 / self.sample_rate)
        self.bin_masks, self.bin_weights = self._create_frequency_bins(
            self.freqs, freq_min, freq_max, self.num_bins
        )
        self.window = np.hanning(self.audio_settings.block_size)
        
        # Pre-compute bin indices for vectorized magnitude calculation
        self.bin_indices = [np.where(mask)[0] for mask in self.bin_masks]
        self.empty_bins = np.array([len(idx) == 0 for idx in self.bin_indices])
        
        # Check bin coverage
        empty_count = np.sum(self.empty_bins)
        if empty_count > 0:
            print(f"Warning: {empty_count} bins have no frequency coverage. "
                  f"Consider increasing FFT_SIZE.")
        
        return self.sample_rate
    
    def update_frequency_range(self) -> None:
        """
        Update frequency bins after changing frequency settings.
        
        Call this after modifying freq_settings (e.g., changing zoom preset).
        """
        freq_min = self.freq_settings.active_min_freq
        freq_max = self.freq_settings.active_max_freq
        
        self.bin_masks, self.bin_weights = self._create_frequency_bins(
            self.freqs, freq_min, freq_max, self.num_bins
        )
        
        # Re-compute bin indices
        self.bin_indices = [np.where(mask)[0] for mask in self.bin_masks]
        self.empty_bins = np.array([len(idx) == 0 for idx in self.bin_indices])
        
        # Warn about empty bins
        empty_count = np.sum(self.empty_bins)
        if empty_count > 0:
            print(f"Warning: {empty_count} bins have no frequency coverage.")
    
    def setup_dual(
        self,
        base_range: Tuple[int, int],
        top_range: Tuple[int, int],
        base_bins: int = 64,
        top_bins: int = 32
    ) -> None:
        """
        Set up dual-layer frequency bins for layered visualization.
        
        Args:
            base_range: (min, max) frequency for base/background layer
            top_range: (min, max) frequency for top/foreground layer
            base_bins: Number of bins for base layer
            top_bins: Number of bins for top layer
        """
        if self.freqs is None:
            raise RuntimeError("Call setup() before setup_dual()")
        
        self.dual_enabled = True
        self.dual_base_bins = base_bins
        self.dual_top_bins = top_bins
        
        # Create base layer bins
        base_masks, base_weights = self._create_frequency_bins(
            self.freqs, base_range[0], base_range[1], base_bins
        )
        self.dual_base_indices = [np.where(mask)[0] for mask in base_masks]
        self.dual_base_weights = base_weights
        self.dual_base_empty = np.array([len(idx) == 0 for idx in self.dual_base_indices])
        
        # Create top layer bins with GLOBAL frequency weighting
        # (not relative to top_range, but to overall audible spectrum)
        top_masks, top_weights = self._create_frequency_bins_global(
            self.freqs, top_range[0], top_range[1], top_bins
        )
        self.dual_top_indices = [np.where(mask)[0] for mask in top_masks]
        self.dual_top_weights = top_weights
        self.dual_top_empty = np.array([len(idx) == 0 for idx in self.dual_top_indices])
        
        # Report setup
        base_empty = np.sum(self.dual_base_empty)
        top_empty = np.sum(self.dual_top_empty)
        print(f"Dual mode: Base {base_range[0]}-{base_range[1]}Hz ({base_bins} bins, {base_empty} empty)")
        print(f"           Top {top_range[0]}-{top_range[1]}Hz ({top_bins} bins, {top_empty} empty)")
    
    def get_dual_fft_magnitudes(self) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """
        Compute FFT and return magnitudes for both dual layers.
        
        Returns:
            Tuple of (base_bars, top_bars) arrays, or None if no data
        """
        if not self.have_data or self.latest_samples is None:
            return None
        
        if not self.dual_enabled:
            return None
        
        # Apply window and compute FFT with zero-padding (same as get_fft_magnitudes)
        x = self.latest_samples * self.window
        X = np.fft.rfft(x, n=self.audio_settings.fft_size)
        mag = np.abs(X)
        
        # Compute base layer bars
        base_bars = np.zeros(self.dual_base_bins, dtype=np.float32)
        for i, indices in enumerate(self.dual_base_indices):
            if len(indices) > 0:
                base_bars[i] = np.mean(mag[indices]) * self.dual_base_weights[i]
        base_bars = np.maximum(0, base_bars - self.sensitivity_settings.noise_floor)
        
        # Compute top layer bars
        top_bars = np.zeros(self.dual_top_bins, dtype=np.float32)
        for i, indices in enumerate(self.dual_top_indices):
            if len(indices) > 0:
                top_bars[i] = np.mean(mag[indices]) * self.dual_top_weights[i]
        top_bars = np.maximum(0, top_bars - self.sensitivity_settings.noise_floor)
        
        return base_bars, top_bars
    
    def _create_frequency_bins(
        self,
        freqs: np.ndarray,
        fmin: float,
        fmax: float,
        n: int
    ) -> tuple:
        """
        Create logarithmic frequency bins with frequency-dependent weights.
        
        Weights interpolate from low_freq_weight to high_freq_weight to compensate
        for the natural 1/f rolloff of most audio content.
        
        Args:
            freqs: Array of FFT frequency values
            fmin: Minimum frequency
            fmax: Maximum frequency
            n: Number of bins
        
        Returns:
            Tuple of (bin_masks, bin_weights)
        """
        edges = np.logspace(np.log10(fmin), np.log10(fmax), n + 1)
        bins = []
        weights = []
        
        low_weight = self.sensitivity_settings.low_freq_weight
        high_weight = self.sensitivity_settings.high_freq_weight
        
        for i in range(n):
            mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
            bins.append(mask)
            
            # Calculate center frequency
            center_freq = (edges[i] + edges[i + 1]) / 2
            
            # Normalized position: 0 at fmin, 1 at fmax (log scale)
            norm_pos = np.log10(center_freq / fmin) / np.log10(fmax / fmin)
            
            # Weight curve: interpolate from low to high weight
            weight = low_weight + (high_weight - low_weight) * (norm_pos ** 1.5)
            weights.append(weight)
        
        return bins, np.array(weights)
    
    def _create_frequency_bins_global(
        self,
        freqs: np.ndarray,
        fmin: float,
        fmax: float,
        n: int
    ) -> tuple:
        """
        Create frequency bins with weights based on GLOBAL frequency position.
        
        Unlike _create_frequency_bins which interpolates weights from low to high
        across the given range, this uses the global audible spectrum (20-20000Hz)
        to determine weights. This ensures high frequencies get appropriate boost
        even when they're at the "bottom" of a sub-range.
        
        Args:
            freqs: Array of FFT frequency values
            fmin: Minimum frequency of sub-range
            fmax: Maximum frequency of sub-range
            n: Number of bins
        
        Returns:
            Tuple of (bin_masks, bin_weights)
        """
        # Global reference range (full audible spectrum)
        global_fmin = 20.0
        global_fmax = 20000.0
        
        edges = np.logspace(np.log10(fmin), np.log10(fmax), n + 1)
        bins = []
        weights = []
        
        low_weight = self.sensitivity_settings.low_freq_weight
        high_weight = self.sensitivity_settings.high_freq_weight
        
        for i in range(n):
            mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
            bins.append(mask)
            
            # Calculate center frequency
            center_freq = (edges[i] + edges[i + 1]) / 2
            
            # Normalized position relative to GLOBAL spectrum (not sub-range)
            norm_pos = np.log10(center_freq / global_fmin) / np.log10(global_fmax / global_fmin)
            norm_pos = np.clip(norm_pos, 0, 1)
            
            # Weight curve: interpolate from low to high weight
            weight = low_weight + (high_weight - low_weight) * (norm_pos ** 1.5)
            weights.append(weight)
        
        return bins, np.array(weights)
    
    def _audio_callback(self, indata, frames, time_info, status):
        """Callback function for audio input stream."""
        if status:
            print(f"Audio status: {status}")
        self.latest_samples = indata[:, self.audio_settings.channel].copy()
        self.have_data = True
    
    def start(self) -> None:
        """Start the audio input stream."""
        self._stream = sd.InputStream(
            device=self.audio_settings.device,
            channels=1,
            samplerate=self.sample_rate,
            blocksize=self.audio_settings.block_size,
            dtype="float32",
            callback=self._audio_callback
        )
        self._stream.start()
        print("Listening... Press CTRL-C to stop")
    
    def stop(self) -> None:
        """Stop the audio input stream."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
    
    def get_fft_magnitudes(self) -> Optional[np.ndarray]:
        """
        Compute FFT and return bar magnitudes.
        
        Returns:
            Array of magnitude values per bin, or None if no data available
        """
        if not self.have_data or self.latest_samples is None:
            return None
        
        # Apply window and compute FFT with zero-padding
        x = self.latest_samples * self.window
        X = np.fft.rfft(x, n=self.audio_settings.fft_size)
        mag = np.abs(X)
        
        # Vectorized magnitude calculation using pre-computed bin indices
        bars = np.zeros(self.num_bins, dtype=np.float32)
        for i, indices in enumerate(self.bin_indices):
            if len(indices) > 0:
                bars[i] = np.mean(mag[indices]) * self.bin_weights[i]
        
        # Apply noise floor (vectorized)
        bars = np.maximum(0, bars - self.sensitivity_settings.noise_floor)
        
        return bars
    
    def __enter__(self):
        """Context manager entry."""
        self.setup()
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()
        return False
