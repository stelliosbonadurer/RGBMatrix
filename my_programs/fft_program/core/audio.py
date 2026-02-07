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
        
        # Layer mode state (initialized in setup_layers())
        self.layers_enabled: bool = False
        self.num_layers: int = 0
        self.layer_indices: List[List[np.ndarray]] = []  # Per-layer bin indices
        self.layer_weights: List[np.ndarray] = []        # Per-layer frequency weights
        self.layer_empty: List[np.ndarray] = []          # Per-layer empty bin masks
        self.layer_bins: List[int] = []                  # Per-layer bin counts
    
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
    
    def setup_layers(self, layer_configs: List) -> None:
        """
        Set up multi-layer frequency bins for layered visualization.
        
        Args:
            layer_configs: List of LayerConfig objects defining each layer
        """
        if self.freqs is None:
            raise RuntimeError("Call setup() before setup_layers()")
        
        self.layers_enabled = True
        self.num_layers = len(layer_configs)
        self.layer_indices = []
        self.layer_weights = []
        self.layer_empty = []
        self.layer_bins = []
        
        # Global frequency range for proper weighting (20Hz - 20kHz audible spectrum)
        global_fmin = 20.0
        global_fmax = 20000.0
        
        for i, config in enumerate(layer_configs):
            fmin, fmax = config.freq_range
            bins = config.bins
            
            # Use global weighting for all layers to ensure proper treble boost
            masks, weights = self._create_frequency_bins_global(
                self.freqs, fmin, fmax, bins, global_fmin, global_fmax
            )
            
            indices = [np.where(mask)[0] for mask in masks]
            empty = np.array([len(idx) == 0 for idx in indices])
            
            self.layer_indices.append(indices)
            self.layer_weights.append(weights)
            self.layer_empty.append(empty)
            self.layer_bins.append(bins)
            
            empty_count = np.sum(empty)
            print(f"Layer {i} '{config.name}': {fmin}-{fmax}Hz ({bins} bins, {empty_count} empty)")
    
    def get_layer_magnitudes(self) -> Optional[List[np.ndarray]]:
        """
        Compute FFT and return magnitudes for all layers.
        
        Returns:
            List of magnitude arrays (one per layer), or None if no data
        """
        if not self.have_data or self.latest_samples is None:
            return None
        
        if not self.layers_enabled:
            return None
        
        # Apply window and compute FFT with zero-padding (done ONCE)
        x = self.latest_samples * self.window
        X = np.fft.rfft(x, n=self.audio_settings.fft_size)
        mag = np.abs(X)
        
        # Extract bins for each layer (cheap O(bins) per layer)
        layer_bars = []
        for layer_idx in range(self.num_layers):
            indices_list = self.layer_indices[layer_idx]
            weights = self.layer_weights[layer_idx]
            num_bins = self.layer_bins[layer_idx]
            
            bars = np.zeros(num_bins, dtype=np.float32)
            for i, indices in enumerate(indices_list):
                if len(indices) > 0:
                    bars[i] = np.mean(mag[indices]) * weights[i]
            
            bars = np.maximum(0, bars - self.sensitivity_settings.noise_floor)
            layer_bars.append(bars)
        
        return layer_bars
    
    def _create_frequency_bins_global(
        self,
        freqs: np.ndarray,
        fmin: float,
        fmax: float,
        n: int,
        global_fmin: float = 20.0,
        global_fmax: float = 20000.0
    ) -> tuple:
        """
        Create frequency bins with GLOBAL frequency weighting.
        
        Uses the full audible spectrum (20Hz-20kHz) as reference for weight calculation,
        not just the layer's frequency range. This ensures treble frequencies get
        proper boost even when extracted as a separate layer.
        
        Args:
            freqs: Array of FFT frequency values
            fmin: Minimum frequency for this layer
            fmax: Maximum frequency for this layer
            n: Number of bins
            global_fmin: Global minimum frequency (20Hz)
            global_fmax: Global maximum frequency (20kHz)
        
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
            
            # GLOBAL normalized position: 0 at 20Hz, 1 at 20kHz (log scale)
            norm_pos = np.log10(center_freq / global_fmin) / np.log10(global_fmax / global_fmin)
            norm_pos = np.clip(norm_pos, 0, 1)
            
            # Weight curve: interpolate from low to high weight
            weight = low_weight + (high_weight - low_weight) * (norm_pos ** 1.5)
            weights.append(weight)
        
        return bins, np.array(weights)
    
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
