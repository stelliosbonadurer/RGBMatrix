"""
Audio scaling and normalization for FFT visualizer.

Handles adaptive gain control to normalize audio levels.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # type: ignore
from collections import deque
from typing import Deque

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
        
        # Rolling buffer for RMS/max calculation
        buffer_size = int(scaling_settings.rolling_window_seconds * frame_rate)
        self.rms_buffer: Deque[float] = deque(maxlen=max(1, buffer_size))
        
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
        
        # Apply smoothing (asymmetric: fast rise, slow fall)
        for i in range(self.num_bins):
            if normalized[i] > self.smoothed_bars[i]:
                self.smoothed_bars[i] += (normalized[i] - self.smoothed_bars[i]) * self.smoothing.rise
            else:
                self.smoothed_bars[i] += (normalized[i] - self.smoothed_bars[i]) * self.smoothing.fall
        
        # Update peak tracking
        self._update_peaks(peak_hold_frames, peak_fall_speed)
        
        return normalized, self.smoothed_bars.copy(), self.peak_heights.copy()
    
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
            self.rms_buffer.append(peak ** 2)
            if len(self.rms_buffer) > 0:
                rms = np.sqrt(np.mean(self.rms_buffer))
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
            # Rolling max (less punchy)
            self.rms_buffer.append(peak)
            rolling_max = max(self.rms_buffer) if self.rms_buffer else 1e-9
            max_val = rolling_max + 1e-9
            
        elif self.scaling.use_fixed_scale:
            max_val = self.scaling.fixed_scale_max
            
        else:
            # Instant auto-normalize (loudest bar always max)
            max_val = peak + 1e-9
        
        # Apply manual sensitivity scalar
        max_val = max_val * self.scaling.sensitivity_scalar
        
        return max_val
    
    def _update_peaks(self, hold_frames: int, fall_speed: float) -> None:
        """
        Update peak indicator positions.
        
        Args:
            hold_frames: Frames to hold peak at position
            fall_speed: Speed of peak descent
        """
        for i in range(self.num_bins):
            bar_height = self.smoothed_bars[i]
            
            if bar_height >= self.peak_heights[i]:
                # New peak
                self.peak_heights[i] = bar_height
                self.peak_hold_counters[i] = hold_frames
            else:
                # Peak above bar - hold or fall
                if self.peak_hold_counters[i] > 0:
                    self.peak_hold_counters[i] -= 1
                else:
                    self.peak_heights[i] -= fall_speed
                    if self.peak_heights[i] < 0:
                        self.peak_heights[i] = 0
    
    def reset(self) -> None:
        """Reset all state (useful when switching visualizers)."""
        self.rms_buffer.clear()
        self.current_scale = self.scaling.min_scale
        self.smoothed_bars.fill(0)
        self.peak_heights.fill(0)
        self.peak_hold_counters.fill(0)
