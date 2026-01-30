"""
Audio scaling and normalization for FFT visualizer.

Handles adaptive gain control to normalize audio levels.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # type: ignore
from typing import Optional

from config.settings import ScalingSettings, SensitivitySettings, SmoothingSettings


class ScalingProcessor:
    """
    Handles audio normalization and bar smoothing.
    
    Supports three scaling modes:
    - Fixed scale: Consistent sensitivity
    - Rolling RMS: Adapts to average energy with headroom (recommended)
    - Rolling Max: Adapts to maximum in window
    """
    
    def __init__(
        self,
        scaling_settings: ScalingSettings,
        sensitivity_settings: SensitivitySettings,
        smoothing_settings: SmoothingSettings,
        num_bins: int,
        frame_rate: float = 250  # Approximate frames per second
    ):
        """
        Initialize scaling processor.
        
        Args:
            scaling_settings: Scaling mode and parameters
            sensitivity_settings: Silence threshold
            smoothing_settings: Rise/fall smoothing
            num_bins: Number of frequency bins
            frame_rate: Approximate frame rate for buffer sizing
        """
        self.scaling = scaling_settings
        self.sensitivity = sensitivity_settings
        self.smoothing = smoothing_settings
        self.num_bins = num_bins
        
        # Rolling buffer for RMS/max calculation (using numpy for O(1) mean)
        self.buffer_size = max(1, int(scaling_settings.rolling_window_seconds * frame_rate))
        self.rms_buffer: np.ndarray = np.zeros(self.buffer_size, dtype=np.float32)
        self.buffer_index: int = 0
        self.buffer_count: int = 0  # Track how many values have been added
        self.buffer_sum: float = 0.0  # Running sum for O(1) mean calculation
        
        # Current adaptive scale
        self.current_scale = scaling_settings.min_scale
        
        # Smoothed bar values
        self.smoothed_bars = np.zeros(num_bins, dtype=np.float32)
        
        # Peak indicator tracking
        self.peak_heights = np.zeros(num_bins, dtype=np.float32)
        self.peak_hold_counters = np.zeros(num_bins, dtype=np.int32)
    
    def process(
        self,
        bars: np.ndarray,
        peak_hold_frames: int = 8,
        peak_fall_speed: float = 0.08
    ) -> tuple:
        """
        Process raw bar values through scaling and smoothing.
        
        Args:
            bars: Raw FFT magnitudes after noise floor
            peak_hold_frames: Frames to hold peak before falling
            peak_fall_speed: How fast peaks fall
        
        Returns:
            Tuple of (normalized_bars, smoothed_bars, peak_heights)
        """
        # Apply silence threshold fade
        peak = np.max(bars)
        if self.sensitivity.silence_threshold > 0 and peak < self.sensitivity.silence_threshold:
            bars = bars * (peak / self.sensitivity.silence_threshold)
        
        # Get normalization scale
        max_val = self._calculate_scale(peak)
        
        # Normalize
        normalized = bars / max_val
        
        # Vectorized asymmetric smoothing: fast rise, slow fall
        delta = normalized - self.smoothed_bars
        rates = np.where(delta > 0, self.smoothing.rise, self.smoothing.fall)
        self.smoothed_bars += delta * rates
        
        # Vectorized peak tracking
        self._update_peaks_vectorized(peak_hold_frames, peak_fall_speed)
        
        return normalized, self.smoothed_bars, self.peak_heights
    
    def _calculate_scale(self, peak: float) -> float:
        """
        Calculate the normalization scale based on current mode.
        
        Args:
            peak: Current peak value
        
        Returns:
            Scale value to divide by
        """
        if self.scaling.use_rolling_rms_scale:
            # Track RMS (root mean square) of peaks for average energy
            # O(1) rolling mean using circular buffer and running sum
            peak_squared = peak ** 2
            
            # Subtract old value from sum, add new value
            if self.buffer_count >= self.buffer_size:
                self.buffer_sum -= self.rms_buffer[self.buffer_index]
            self.buffer_sum += peak_squared
            self.rms_buffer[self.buffer_index] = peak_squared
            self.buffer_index = (self.buffer_index + 1) % self.buffer_size
            self.buffer_count = min(self.buffer_count + 1, self.buffer_size)
            
            # Calculate RMS from running sum
            if self.buffer_count > 0:
                rms = np.sqrt(self.buffer_sum / self.buffer_count)
            else:
                rms = peak
            
            # Target scale = RMS * headroom
            target_scale = max(rms * self.scaling.headroom_multiplier, self.scaling.min_scale)
            
            # Asymmetric smoothing: fast attack (loud), slow decay (quiet)
            if target_scale > self.current_scale:
                self.current_scale += (target_scale - self.current_scale) * self.scaling.attack_speed
            else:
                self.current_scale += (target_scale - self.current_scale) * self.scaling.decay_speed
            
            max_val = self.current_scale
            
        elif self.scaling.use_rolling_max_scale:
            # Rolling max (less punchy) - still need to track individual values
            self.rms_buffer[self.buffer_index] = peak
            self.buffer_index = (self.buffer_index + 1) % self.buffer_size
            self.buffer_count = min(self.buffer_count + 1, self.buffer_size)
            rolling_max = np.max(self.rms_buffer[:self.buffer_count]) if self.buffer_count > 0 else 1e-9
            max_val = rolling_max + 1e-9
            
        elif self.scaling.use_fixed_scale:
            max_val = self.scaling.fixed_scale_max
            
        else:
            # Instant auto-normalize (loudest bar always max)
            max_val = peak + 1e-9
        
        # Apply manual sensitivity scalar
        max_val = max_val * self.scaling.sensitivity_scalar
        
        return max_val
    
    def _update_peaks_vectorized(self, hold_frames: int, fall_speed: float) -> None:
        """
        Update peak indicator positions (vectorized).
        
        Args:
            hold_frames: Frames to hold peak at position
            fall_speed: Speed of peak descent
        """
        # Identify where bars have reached or exceeded peaks (new peaks)
        new_peaks = self.smoothed_bars >= self.peak_heights
        
        # Update peak heights and reset hold counters where new peaks occurred
        self.peak_heights = np.where(new_peaks, self.smoothed_bars, self.peak_heights)
        self.peak_hold_counters = np.where(new_peaks, hold_frames, self.peak_hold_counters)
        
        # For non-new-peak positions: decrement hold counter or apply fall
        not_new_peaks = ~new_peaks
        holding = (self.peak_hold_counters > 0) & not_new_peaks
        falling = (self.peak_hold_counters <= 0) & not_new_peaks
        
        # Decrement hold counters
        self.peak_hold_counters = np.where(holding, self.peak_hold_counters - 1, self.peak_hold_counters)
        
        # Apply fall speed and clamp to 0
        self.peak_heights = np.where(falling, np.maximum(0, self.peak_heights - fall_speed), self.peak_heights)
    
    def reset(self) -> None:
        """Reset all state (useful when switching visualizers)."""
        self.rms_buffer.fill(0)
        self.buffer_index = 0
        self.buffer_count = 0
        self.buffer_sum = 0.0
        self.current_scale = self.scaling.min_scale
        self.smoothed_bars.fill(0)
        self.peak_heights.fill(0)
        self.peak_hold_counters.fill(0)
