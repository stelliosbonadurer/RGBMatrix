#!/usr/bin/env python
import sys
import os
import numpy as np # type: ignore
import sounddevice as sd # type: ignore
import time

# Add path to find the samplebase and rgbmatrix modules
sys.path.append(os.path.abspath(os.path.dirname(__file__) + '/..'))
from samplebase import SampleBase # type: ignore
from rgbmatrix import graphics # type: ignore

# ---------- CONFIG ----------
DEVICE = 'iMM-6C'  # Use device name instead of index
BLOCK_SIZE = 512  # Actual audio samples per callback (~23ms at 44kHz)
FFT_SIZE = 4096    # Zero-pad to this size for better freq resolution
CHANNEL = 0
MIN_FREQ = 120     # Raise to cut more rumble/ambient noise
MAX_FREQ = 12000
SLEEP_DELAY = 0.005  # Delay between frames

# Zoom mode - focus on a narrower frequency range
ZOOM_MODE = False         # Set to True to zoom in on active frequency range
ZOOM_MIN_FREQ = 360      # ~25% of log scale (where action starts)
ZOOM_MAX_FREQ = 3500     # ~60% of log scale (where action ends)

# Sensitivity tuning
NOISE_FLOOR = 0.1       # Subtract from signal (0 = no floor, 1 = mute everything)
LOW_FREQ_WEIGHT = 0.15   # Multiplier for lowest freq bin (0 = mute, 1 = neutral, >1 = boost)

HIGH_FREQ_WEIGHT = 8.0   # Multiplier for highest freq bin (0 = mute, 1 = neutral, >1 = boost)
USE_FIXED_SCALE = True   # True = fixed sensitivity, False = auto-normalize (loudest bar always max)
USE_ROLLING_MAX_SCALE = False  # True = normalize to max in last 30 seconds
FIXED_SCALE_MAX = 0.3    # Divide signal by this (0 = infinite gain/clipped, 1 = low sensitivity)
ROLLING_MAX_SECONDS = 30  # Window for rolling max normalization
SILENCE_THRESHOLD = 0.00 # Below this = black (0 = always show something, 1 = mute nearly everything)

# Smoothing
SMOOTH_RISE = 0.7       # Slightly slower rise (was 0.6)
SMOOTH_FALL = 0.55       # How fast bars fall (slower = smoother decay)
# ---------------------------

class FFTMatrix(SampleBase):
    def __init__(self, *args, **kwargs):
        super(FFTMatrix, self).__init__(*args, **kwargs)
        self.latest = None
        self.have_data = False

    def audio_callback(self, indata, frames, time_info, status):
        """Callback function for audio input stream"""
        self.latest = indata[:, CHANNEL].copy()
        self.have_data = True

    def freq_to_bin(self, freqs, fmin, fmax, n):
        """Create logarithmic frequency bins with frequency-dependent weights.
        
        Weights interpolate from LOW_FREQ_WEIGHT to HIGH_FREQ_WEIGHT to compensate
        for the natural 1/f rolloff of most audio content.
        """
        edges = np.logspace(np.log10(fmin), np.log10(fmax), n + 1)
        bins = []
        weights = []
        for i in range(n):
            mask = (freqs >= edges[i]) & (freqs < edges[i+1])
            bins.append(mask)
            # Calculate center frequency
            center_freq = (edges[i] + edges[i+1]) / 2
            # Normalized position: 0 at fmin, 1 at fmax (log scale)
            norm_pos = np.log10(center_freq / fmin) / np.log10(fmax / fmin)
            # Weight curve: interpolate from LOW_FREQ_WEIGHT to HIGH_FREQ_WEIGHT
            # At low freq (norm_pos=0): weight = LOW_FREQ_WEIGHT
            # At high freq (norm_pos=1): weight = HIGH_FREQ_WEIGHT
            weight = LOW_FREQ_WEIGHT + (HIGH_FREQ_WEIGHT - LOW_FREQ_WEIGHT) * (norm_pos ** 1.5)
            weights.append(weight)
        return bins, np.array(weights)

    def run(self):
        import collections
        rolling_max_buffer = collections.deque(maxlen=int(ROLLING_MAX_SECONDS / SLEEP_DELAY))
        """Main program loop"""
        offset_canvas = self.matrix.CreateFrameCanvas()
        
        # Use matrix width for number of frequency bins
        num_bins = self.matrix.width
        
        # Query device to get native sample rate
        device_info = sd.query_devices(DEVICE, 'input')
        sample_rate = int(device_info['default_samplerate'])
        print(f"Using device: {device_info['name']} at {sample_rate} Hz")
        
        # Select frequency range based on zoom mode
        if ZOOM_MODE:
            freq_min = ZOOM_MIN_FREQ
            freq_max = ZOOM_MAX_FREQ
            print(f"ZOOM MODE: {freq_min} Hz - {freq_max} Hz")
        else:
            freq_min = MIN_FREQ
            freq_max = MAX_FREQ
            print(f"Full range: {freq_min} Hz - {freq_max} Hz")
        
        # Initialize FFT parameters
        self.latest = np.zeros(BLOCK_SIZE, dtype=np.float32)
        freqs = np.fft.rfftfreq(FFT_SIZE, 1 / sample_rate)  # Use FFT_SIZE for freq bins
        bin_masks, bin_weights = self.freq_to_bin(freqs, freq_min, freq_max, num_bins)
        window = np.hanning(BLOCK_SIZE)
        
        # Check bin coverage and warn about empty bins
        empty_bins = sum(1 for mask in bin_masks if not np.any(mask))
        if empty_bins > 0:
            print(f"Warning: {empty_bins} bins have no frequency coverage. Consider increasing FFT_SIZE.")
        
        # Initialize smoothed bars for interpolation
        smoothed_bars = np.zeros(num_bins, dtype=np.float32)
        
        # Start audio input stream
        with sd.InputStream(
            device=DEVICE,
            channels=1,
            samplerate=sample_rate,
            blocksize=BLOCK_SIZE,
            dtype="float32",
            callback=self.audio_callback
        ):
            print("Listening... Press CTRL-C to stop")
            time.sleep(0.5)
            
            while True:
                # Wait for audio data
                if not self.have_data:
                    time.sleep(0.001)
                    continue
                
                # Apply window and compute FFT with zero-padding
                x = self.latest * window
                X = np.fft.rfft(x, n=FFT_SIZE)  # Zero-pad to FFT_SIZE
                mag = np.abs(X)
                
                # Calculate magnitude for each frequency bin with weights
                bars = []
                for i, mask in enumerate(bin_masks):
                    if np.any(mask):
                        val = np.mean(mag[mask]) * bin_weights[i]
                    else:
                        # For empty bins, interpolate from neighbors
                        val = 0
                    bars.append(val)
                
                # Apply noise floor
                bars = np.array([max(0, b - NOISE_FLOOR) for b in bars], dtype=np.float32)
                
                # If signal is too quiet, fade to silence (prevents noise dancing)
                peak = np.max(bars)
                if peak < SILENCE_THRESHOLD:
                    bars = bars * (peak / SILENCE_THRESHOLD)  # Fade out near silence
                

                # Normalize to 0-1 range
                if USE_ROLLING_MAX_SCALE:
                    # Update rolling max buffer
                    peak = np.max(bars)
                    rolling_max_buffer.append(peak)
                    rolling_max = max(rolling_max_buffer) if rolling_max_buffer else 1e-9
                    max_val = rolling_max + 1e-9
                elif USE_FIXED_SCALE:
                    max_val = FIXED_SCALE_MAX
                else:
                    max_val = max(bars) + 1e-9
                bars = np.clip(bars / max_val, 0, 1)
                
                # Apply smoothing (asymmetric: fast rise, slow fall)
                for i in range(num_bins):
                    if bars[i] > smoothed_bars[i]:
                        smoothed_bars[i] += (bars[i] - smoothed_bars[i]) * SMOOTH_RISE
                    else:
                        smoothed_bars[i] += (bars[i] - smoothed_bars[i]) * SMOOTH_FALL
                
                # Convert to pixel heights
                bars = [int(b * self.matrix.height) for b in smoothed_bars]
                
                # Draw on the matrix
                self.draw_fft(bars, offset_canvas)
                
                time.sleep(SLEEP_DELAY)

    def draw_fft(self, bars, canvas):
        """Draw FFT bars on the RGB matrix"""
        # Clear previous frame
        canvas.Clear()
        
        # Draw each frequency bin as a vertical bar
        for i, height in enumerate(bars):
            # Clamp height to canvas dimensions
            col_height = min(height, canvas.height)
            
            # Calculate color based on height (gradient from blue to red)
            if col_height > 0:
                # Color gradient: blue (low) -> green (mid) -> red (high)
                ratio = col_height / canvas.height
                if ratio < 0.5:
                    # Blue to green
                    r = 0
                    g = int(255 * (ratio * 2))
                    b = int(255 * (1 - ratio * 2))
                else:
                    # Green to red
                    r = int(255 * ((ratio - 0.5) * 2))
                    g = int(255 * (1 - (ratio - 0.5) * 2))
                    b = 0
                
                # Draw column from bottom up
                for j in range(col_height):
                    canvas.SetPixel(i, canvas.height - 1 - j, r, g, b)
        
        # Swap buffers to display
        canvas = self.matrix.SwapOnVSync(canvas)
        return canvas

# Main function
if __name__ == "__main__":
    fft_matrix = FFTMatrix()
    if not fft_matrix.process():
        fft_matrix.print_help()
