#!/usr/bin/env python
import sys
import os
import numpy as np
import sounddevice as sd
import time

# Add path to find the samplebase and rgbmatrix modules
sys.path.append(os.path.abspath(os.path.dirname(__file__) + '/..'))
from samplebase import SampleBase
from rgbmatrix import graphics

# ---------- CONFIG ----------
DEVICE = 'iMM-6C'  # Use device name instead of index
BLOCK_SIZE = 1024
CHANNEL = 0
MIN_FREQ = 50
MAX_FREQ = 12000
SLEEP_DELAY = 0.01  # Delay between frames
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
        """Create logarithmic frequency bins"""
        edges = np.logspace(np.log10(fmin), np.log10(fmax), n + 1)
        bins = []
        for i in range(n):
            mask = (freqs >= edges[i]) & (freqs < edges[i+1])
            bins.append(mask)
        return bins

    def run(self):
        """Main program loop"""
        offset_canvas = self.matrix.CreateFrameCanvas()
        
        # Use matrix width for number of frequency bins
        num_bins = self.matrix.width
        
        # Query device to get native sample rate
        device_info = sd.query_devices(DEVICE, 'input')
        sample_rate = int(device_info['default_samplerate'])
        print(f"Using device: {device_info['name']} at {sample_rate} Hz")
        
        # Initialize FFT parameters
        self.latest = np.zeros(BLOCK_SIZE, dtype=np.float32)
        freqs = np.fft.rfftfreq(BLOCK_SIZE, 1 / sample_rate)
        bin_masks = self.freq_to_bin(freqs, MIN_FREQ, MAX_FREQ, num_bins)
        window = np.hanning(BLOCK_SIZE)
        
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
                
                # Apply window and compute FFT
                x = self.latest * window
                X = np.fft.rfft(x)
                mag = np.abs(X)
                
                # Calculate magnitude for each frequency bin
                bars = []
                for mask in bin_masks:
                    val = np.mean(mag[mask]) if np.any(mask) else 0
                    bars.append(val)
                
                # Normalize to matrix height
                max_val = max(bars) + 1e-9
                bars = [int((b / max_val) * self.matrix.height) for b in bars]
                
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
