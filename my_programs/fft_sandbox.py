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
MIN_FREQ = 60      # Lower bound for full range (60 = catch upright bass fundamentals)
MAX_FREQ = 12000
SLEEP_DELAY = 0.005  # Delay between frames (lower = smoother but more CPU)

# Zoom mode - focus on a narrower frequency range
ZOOM_MODE = True         # True = use ZOOM frequencies, False = use MIN/MAX frequencies
ZOOM_MIN_FREQ = 150       # Good for bluegrass: 80 catches guitar/bass, cuts rumble
ZOOM_MAX_FREQ = 3300     # Good for bluegrass: 8000 catches fiddle/banjo harmonics

# ---------- DISPLAY MODE ----------
MIRROR_HORIZONTAL = False  # True = bars mirror left/right from center, False = normal left-to-right
CENTER_VERTICAL = False    # True = bars grow from vertical center up AND down, False = grow from bottom
FLIP_X = False             # True = reverse x-axis (low freq on right), False = low freq on left. Works with mirror mode.

# ---------- PEAK INDICATORS ----------
PEAK_ENABLED = False        # True = show floating peak dots above bars, False = bars only
PEAK_FALL_SPEED = 0.08     # How fast peaks fall (0.01 = slow/floaty, 0.2 = fast, 0.05-0.1 = nice)
PEAK_HOLD_FRAMES = 8       # Frames to hold peak before falling (0 = immediate fall, 15 = long hold)
PEAK_COLOR_MODE = 'contrast'   # 'white' = always white, 'bar' = match bar color, 'contrast' = opposite of bar, 'peak' = max height color of theme

# ---------- COLOR THEME ----------
# Options: 'classic', 'warm', 'fire', 'ocean', 'forest', 'purple', 'rainbow', 'spectrum', 'fire_spectrum', 'blue_flame', 'mono_green', 'mono_amber'
COLOR_THEME = 'blue_flame'
#   'classic'    - Blue (low) -> Green (mid) -> Red (high) - original theme
#   'warm'       - Dark red (low) -> Orange (mid) -> Yellow (high) - great for bluegrass
#   'fire'       - Black/red (low) -> Orange (mid) -> Yellow/white (high) - intense
#   'ocean'      - Deep blue (low) -> Cyan (mid) -> White (high) - cool/calm
#   'forest'     - Dark green (low) -> Bright green (mid) -> Yellow (high) - nature
#   'purple'     - Deep purple (low) -> Magenta (mid) -> Pink (high) - vibrant
#   'rainbow'    - Full spectrum based on bar position (column), brightness varies with height
#   'spectrum'   - Rainbow hue by column + hue shifts with amplitude (low=blue-ish, high=red-ish)
#   'fire_spectrum' - Fire hues vary by column (deep red to orange), fire gradient on y-axis
#   'blue_flame' - Fire spectrum but peaks fade to blue (like hottest part of flame)
#   'mono_green' - Single color (green) with brightness based on height - retro
#   'mono_amber' - Single color (amber/orange) with brightness based on height - vintage VU

BRIGHTNESS_BOOST = 1.0     # Overall brightness multiplier (0.5 = dim, 1.0 = normal, 1.5 = bright)

# ---------- SENSITIVITY ----------
NOISE_FLOOR = 0.3      # Subtract from signal (0 = no floor/sensitive, 0.5 = cut noise, 1 = mute all)
LOW_FREQ_WEIGHT = 0.7   # Multiplier for bass frequencies (0 = mute, 1 = neutral, >1 = boost bass)
HIGH_FREQ_WEIGHT = 10.0   # Multiplier for treble frequencies (0 = mute, 1 = neutral, >1 = boost treble)

# ---------- SCALING MODES ----------
# Only ONE of these should be True, or all False for instant auto-normalize
USE_FIXED_SCALE = False   # True = fixed sensitivity (consistent but may clip or be quiet)
USE_ROLLING_RMS_SCALE = True  # True = scale to average energy + headroom (RECOMMENDED - punchy peaks)
USE_ROLLING_MAX_SCALE = False  # True = scale to max in window (less punchy, everything similar height)

FIXED_SCALE_MAX = 0.3    # For fixed scale: divide signal by this (0.1 = very sensitive, 0.5 = needs loud audio)
ROLLING_WINDOW_SECONDS = 8  # Seconds of history for RMS/max calculation (shorter = more reactive, longer = stable)
HEADROOM_MULTIPLIER = 2.5   # RMS multiplier for headroom (2.0 = punchy, 3.0 = very punchy, 1.5 = more filled)
ATTACK_SPEED = 0.3         # How fast scale adapts to LOUDER audio (0.1 = slow, 0.5 = instant)
DECAY_SPEED = 0.02          # How fast scale adapts to QUIETER audio (0.01 = very slow, 0.1 = fast)
MIN_SCALE = 0.05            # Minimum scale to prevent infinite gain in silence (0.01-0.1)
SILENCE_THRESHOLD = 0.00 # Below this peak = fade to black (0 = always show, 0.1 = hide quiet noise)

# ---------- SMOOTHING ----------
SMOOTH_RISE = 0.9       # How fast bars rise (0.3 = smooth/slow, 1.0 = instant, 0.6-0.8 = snappy)
SMOOTH_FALL = 0.35      # How fast bars fall (0.2 = slow decay, 0.8 = fast drop, 0.4-0.6 = natural)
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
        # Rolling buffer for RMS calculation
        rms_buffer = collections.deque(maxlen=int(ROLLING_WINDOW_SECONDS / SLEEP_DELAY))
        current_scale = MIN_SCALE  # Adaptive scale that smoothly follows the audio
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
        
        # Initialize peak tracking for peak indicators
        peak_heights = np.zeros(num_bins, dtype=np.float32)  # Current peak dot positions (0-1)
        peak_hold_counters = np.zeros(num_bins, dtype=np.int32)  # Frames remaining in hold
        
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
                

                # Normalize to 0-1 range with adaptive scaling
                peak = np.max(bars)
                
                if USE_ROLLING_RMS_SCALE:
                    # Track RMS (root mean square) of peaks for average energy
                    rms_buffer.append(peak ** 2)
                    if len(rms_buffer) > 0:
                        rms = np.sqrt(np.mean(rms_buffer))
                    else:
                        rms = peak
                    
                    # Target scale = RMS * headroom (so average signal is ~40% height, peaks punch through)
                    target_scale = max(rms * HEADROOM_MULTIPLIER, MIN_SCALE)
                    
                    # Asymmetric smoothing: fast attack (loud), slow decay (quiet)
                    if target_scale > current_scale:
                        # Getting louder - adapt quickly to avoid clipping
                        current_scale += (target_scale - current_scale) * ATTACK_SPEED
                    else:
                        # Getting quieter - adapt slowly to maintain punch
                        current_scale += (target_scale - current_scale) * DECAY_SPEED
                    
                    max_val = current_scale
                elif USE_ROLLING_MAX_SCALE:
                    # Legacy: rolling max (less punchy)
                    rms_buffer.append(peak)
                    rolling_max = max(rms_buffer) if rms_buffer else 1e-9
                    max_val = rolling_max + 1e-9
                elif USE_FIXED_SCALE:
                    max_val = FIXED_SCALE_MAX
                else:
                    # Instant auto-normalize (loudest bar always max)
                    max_val = peak + 1e-9
                    
                bars = np.clip(bars / max_val, 0, 1)
                
                # Apply smoothing (asymmetric: fast rise, slow fall)
                for i in range(num_bins):
                    if bars[i] > smoothed_bars[i]:
                        smoothed_bars[i] += (bars[i] - smoothed_bars[i]) * SMOOTH_RISE
                    else:
                        smoothed_bars[i] += (bars[i] - smoothed_bars[i]) * SMOOTH_FALL
                
                # Convert to pixel heights
                bars = [int(b * self.matrix.height) for b in smoothed_bars]
                
                # Update peak indicators
                if PEAK_ENABLED:
                    for i in range(num_bins):
                        bar_height_normalized = smoothed_bars[i]
                        if bar_height_normalized >= peak_heights[i]:
                            # New peak - set position and reset hold counter
                            peak_heights[i] = bar_height_normalized
                            peak_hold_counters[i] = PEAK_HOLD_FRAMES
                        else:
                            # Peak is above bar - hold or fall
                            if peak_hold_counters[i] > 0:
                                peak_hold_counters[i] -= 1
                            else:
                                # Fall towards bar
                                peak_heights[i] -= PEAK_FALL_SPEED
                                if peak_heights[i] < 0:
                                    peak_heights[i] = 0
                    peak_pixels = [int(p * self.matrix.height) for p in peak_heights]
                else:
                    peak_pixels = None
                
                # Draw on the matrix
                self.draw_fft(bars, offset_canvas, peak_pixels, smoothed_bars)
                
                time.sleep(SLEEP_DELAY)

    def get_color(self, ratio, column_ratio=0):
        """Get RGB color based on height ratio and optional column position.
        
        Args:
            ratio: 0-1 value representing bar height (0=bottom, 1=top)
            column_ratio: 0-1 value representing column position (0=left, 1=right) - used for rainbow
        
        Returns:
            Tuple of (r, g, b) values 0-255
        """
        if COLOR_THEME == 'classic':
            # Blue (low) -> Green (mid) -> Red (high)
            if ratio < 0.5:
                r = 0
                g = int(255 * (ratio * 2))
                b = int(255 * (1 - ratio * 2))
            else:
                r = int(255 * ((ratio - 0.5) * 2))
                g = int(255 * (1 - (ratio - 0.5) * 2))
                b = 0
                
        elif COLOR_THEME == 'warm':
            # Dark red (low) -> Orange (mid) -> Yellow (high) - great for bluegrass
            r = int(255 * min(1, ratio * 2 + 0.3))
            g = int(255 * (ratio ** 1.2))
            b = 0
            
        elif COLOR_THEME == 'fire':
            # Black/red (low) -> Orange (mid) -> Yellow/white (high)
            if ratio < 0.33:
                r = int(255 * (ratio * 3))
                g = 0
                b = 0
            elif ratio < 0.66:
                r = 255
                g = int(255 * ((ratio - 0.33) * 3))
                b = 0
            else:
                r = 255
                g = 255
                b = int(255 * ((ratio - 0.66) * 3))
                
        elif COLOR_THEME == 'ocean':
            # Deep blue (low) -> Cyan (mid) -> White (high)
            r = int(255 * (ratio ** 2))
            g = int(255 * ratio)
            b = int(255 * (0.5 + ratio * 0.5))
            
        elif COLOR_THEME == 'forest':
            # Dark green (low) -> Bright green (mid) -> Yellow (high)
            r = int(255 * (ratio ** 1.5))
            g = int(255 * (0.3 + ratio * 0.7))
            b = 0
            
        elif COLOR_THEME == 'purple':
            # Deep purple (low) -> Magenta (mid) -> Pink (high)
            r = int(255 * (0.4 + ratio * 0.6))
            g = int(255 * (ratio ** 2))
            b = int(255 * (0.6 + ratio * 0.4))
            
        elif COLOR_THEME == 'rainbow':
            # Full spectrum based on column position, brightness based on height
            hue = column_ratio * 360
            # HSV to RGB conversion (saturation=1, value=ratio)
            c = ratio
            x = c * (1 - abs((hue / 60) % 2 - 1))
            if hue < 60:
                r, g, b = c, x, 0
            elif hue < 120:
                r, g, b = x, c, 0
            elif hue < 180:
                r, g, b = 0, c, x
            elif hue < 240:
                r, g, b = 0, x, c
            elif hue < 300:
                r, g, b = x, 0, c
            else:
                r, g, b = c, 0, x
            r, g, b = int(r * 255), int(g * 255), int(b * 255)
        
        elif COLOR_THEME == 'spectrum':
            # Rainbow hue based on column position, PLUS hue shifts with amplitude
            # Base hue from column (0-360), then shift based on amplitude
            # Low amplitude: shifts toward blue/purple, High amplitude: shifts toward red/yellow
            base_hue = column_ratio * 270  # Use 270 degrees so we get variety
            amplitude_shift = (ratio - 0.5) * 90  # -45 to +45 degree shift based on amplitude
            hue = (base_hue + amplitude_shift) % 360
            
            # Full saturation, value based on amplitude (but keep minimum brightness)
            saturation = 1.0
            value = 0.3 + ratio * 0.7  # Range from 0.3 to 1.0 so quiet bins still visible
            
            # HSV to RGB conversion
            c = value * saturation
            x = c * (1 - abs((hue / 60) % 2 - 1))
            m = value - c
            
            if hue < 60:
                r, g, b = c, x, 0
            elif hue < 120:
                r, g, b = x, c, 0
            elif hue < 180:
                r, g, b = 0, c, x
            elif hue < 240:
                r, g, b = 0, x, c
            elif hue < 300:
                r, g, b = x, 0, c
            else:
                r, g, b = c, 0, x
            
            r, g, b = int((r + m) * 255), int((g + m) * 255), int((b + m) * 255)
        
        elif COLOR_THEME == 'fire_spectrum':
            # Fire hues vary across x-axis, fire gradient on y-axis
            # X-axis: shifts base color from deep red/crimson (left) to orange/gold (right)
            # Y-axis: black/red (low) -> orange (mid) -> yellow/white (high) like fire theme
            
            # Column determines the "warmth" - how much orange/yellow vs deep red
            # 0.0 = deep red fire, 1.0 = orange/gold fire
            warmth = column_ratio
            
            # Apply fire gradient based on amplitude (ratio)
            if ratio < 0.33:
                # Bottom third: black to red/dark-orange
                intensity = ratio * 3  # 0 to 1 within this band
                r = int(255 * intensity)
                # Add some orange tint based on column position
                g = int(60 * warmth * intensity)
                b = 0
            elif ratio < 0.66:
                # Middle third: red to orange/yellow
                intensity = (ratio - 0.33) * 3  # 0 to 1 within this band
                r = 255
                # Green ramps up, more so for warmer columns
                g = int((120 + 135 * warmth) * intensity)
                b = 0
            else:
                # Top third: orange to yellow/white
                intensity = (ratio - 0.66) * 3  # 0 to 1 within this band
                r = 255
                g = int(120 + 135 * warmth + (255 - (120 + 135 * warmth)) * intensity)
                # Add white hot tips for high amplitude
                b = int(255 * intensity * warmth)  # More white for warmer columns
        
        elif COLOR_THEME == 'blue_flame':
            # Fire spectrum but the very top transitions to blue (hottest flame)
            # X-axis: shifts base color from deep red/crimson (left) to orange/gold (right)
            # Y-axis: black -> red -> orange -> yellow -> WHITE -> BLUE at the very top
            
            warmth = column_ratio
            
            if ratio < 0.25:
                # Bottom quarter: black to red/dark-orange
                intensity = ratio * 4  # 0 to 1 within this band
                r = int(255 * intensity)
                g = int(60 * warmth * intensity)
                b = 0
            elif ratio < 0.5:
                # Second quarter: red to orange
                intensity = (ratio - 0.25) * 4
                r = 255
                g = int((120 + 100 * warmth) * intensity)
                b = 0
            elif ratio < 0.75:
                # Third quarter: orange to yellow/white
                intensity = (ratio - 0.5) * 4
                r = 255
                g = int(120 + 100 * warmth + (255 - (120 + 100 * warmth)) * intensity)
                b = int(180 * intensity)  # Start adding white
            else:
                # Top quarter: yellow/white transitioning to blue
                intensity = (ratio - 0.75) * 4  # 0 to 1 within this band
                # Fade red and green down, blue up
                r = int(255 * (1 - intensity * 0.8))  # Fade to ~50
                g = int(255 * (1 - intensity * 0.6))  # Fade to ~100
                b = int(180 + (255 - 180) * intensity)  # Ramp to full blue
            
        elif COLOR_THEME == 'mono_green':
            # Single green color, brightness varies with height
            r = 0
            g = int(255 * ratio)
            b = 0
            
        elif COLOR_THEME == 'mono_amber':
            # Vintage amber VU meter look
            r = int(255 * ratio)
            g = int(140 * ratio)
            b = 0
            
        else:
            # Default to classic if unknown theme
            if ratio < 0.5:
                r, g, b = 0, int(255 * ratio * 2), int(255 * (1 - ratio * 2))
            else:
                r, g, b = int(255 * (ratio - 0.5) * 2), int(255 * (1 - (ratio - 0.5) * 2)), 0
        
        # Apply brightness boost
        r = min(255, int(r * BRIGHTNESS_BOOST))
        g = min(255, int(g * BRIGHTNESS_BOOST))
        b = min(255, int(b * BRIGHTNESS_BOOST))
        
        return r, g, b

    def draw_fft(self, bars, canvas, peak_pixels=None, smoothed_bars=None):
        """Draw FFT bars on the RGB matrix with optional peak indicators.
        
        Args:
            bars: List of bar heights in pixels
            canvas: RGB matrix canvas to draw on
            peak_pixels: Optional list of peak indicator positions in pixels
            smoothed_bars: Optional list of normalized bar values (0-1) for color calculation
        """
        # Clear previous frame
        canvas.Clear()
        
        num_bins = len(bars)
        height = canvas.height
        width = canvas.width
        
        # Handle mirror mode - combine adjacent bins then display symmetrically
        # 32 bins -> 16 combined bins (bin 0+1 -> combined 0, bin 2+3 -> combined 1, etc.)
        # Then display those 16 bins mirrored across 32 columns
        if MIRROR_HORIZONTAL:
            half = num_bins // 2  # 16 combined bins
            
            # Step 1: Combine adjacent bins (0+1, 2+3, 4+5, ... 30+31) -> 16 bins
            combined_bars = []
            combined_peaks = [] if peak_pixels else None
            combined_ratios = [] if smoothed_bars is not None else None
            
            for i in range(half):
                # Combine bins 2*i and 2*i+1 using max (captures peaks from both)
                idx1 = 2 * i
                idx2 = 2 * i + 1
                combined_bars.append(max(bars[idx1], bars[idx2]))
                
                if peak_pixels:
                    combined_peaks.append(max(peak_pixels[idx1], peak_pixels[idx2]))
                
                if smoothed_bars is not None:
                    combined_ratios.append(max(smoothed_bars[idx1], smoothed_bars[idx2]))
            
            # Step 2: Map 16 combined bins to 32 display columns (mirrored)
            display_bars = [0] * num_bins
            display_peaks = [0] * num_bins if peak_pixels else None
            display_ratios = [0.0] * num_bins if smoothed_bars is not None else None
            
            for i in range(half):
                # Determine which combined bin to use based on FLIP_X
                # FLIP_X=False: combined bin 0 (low freq) in center, bin 15 (high freq) on edges
                # FLIP_X=True: combined bin 15 (high freq) in center, bin 0 (low freq) on edges
                if FLIP_X:
                    src_idx = half - 1 - i  # 15,14,13...0 (high to low as we go from center to edge)
                else:
                    src_idx = i  # 0,1,2...15 (low to high as we go from center to edge)
                
                # Left side: column 15 is center-left, column 0 is left edge
                left_col = half - 1 - i
                # Right side: column 16 is center-right, column 31 is right edge
                right_col = half + i
                
                display_bars[left_col] = combined_bars[src_idx]
                display_bars[right_col] = combined_bars[src_idx]
                
                if peak_pixels:
                    display_peaks[left_col] = combined_peaks[src_idx]
                    display_peaks[right_col] = combined_peaks[src_idx]
                
                if smoothed_bars is not None:
                    display_ratios[left_col] = combined_ratios[src_idx]
                    display_ratios[right_col] = combined_ratios[src_idx]
            
            bars = display_bars
            peak_pixels = display_peaks
            if smoothed_bars is not None:
                smoothed_bars = display_ratios
        else:
            # Non-mirror mode: simple flip if enabled
            if FLIP_X:
                bars = list(reversed(bars))
                if peak_pixels:
                    peak_pixels = list(reversed(peak_pixels))
                if smoothed_bars is not None:
                    smoothed_bars = list(reversed(smoothed_bars))
        
        # Draw each frequency bin as a vertical bar
        for i, bar_height in enumerate(bars):
            # Clamp height to canvas dimensions
            col_height = min(bar_height, height)
            
            # Get normalized ratio for color calculation
            if smoothed_bars is not None:
                color_ratio = smoothed_bars[i]
            else:
                color_ratio = col_height / height if height > 0 else 0
            
            column_ratio = i / num_bins  # For rainbow mode
            
            if col_height > 0:
                r, g, b = self.get_color(color_ratio, column_ratio)
                
                if CENTER_VERTICAL:
                    # Bars grow from center up AND down
                    center = height // 2
                    half_height = col_height // 2
                    # Upper half
                    for j in range(half_height):
                        y = center - 1 - j
                        if 0 <= y < height:
                            canvas.SetPixel(i, y, r, g, b)
                    # Lower half
                    for j in range(half_height):
                        y = center + j
                        if 0 <= y < height:
                            canvas.SetPixel(i, y, r, g, b)
                else:
                    # Normal: bars grow from bottom up
                    for j in range(col_height):
                        canvas.SetPixel(i, height - 1 - j, r, g, b)
            
            # Draw peak indicator
            if peak_pixels and PEAK_ENABLED:
                peak_y = peak_pixels[i]
                if peak_y > 0:
                    # Get peak color based on mode
                    if PEAK_COLOR_MODE == 'white':
                        pr, pg, pb = 255, 255, 255
                    elif PEAK_COLOR_MODE == 'bar':
                        pr, pg, pb = self.get_color(color_ratio, column_ratio)
                    elif PEAK_COLOR_MODE == 'contrast':
                        # Invert the bar color
                        br, bg, bb = self.get_color(color_ratio, column_ratio)
                        pr, pg, pb = 255 - br, 255 - bg, 255 - bb
                    elif PEAK_COLOR_MODE == 'peak':
                        # Use the color for max height (ratio = 1.0) in the current theme
                        pr, pg, pb = self.get_color(1.0, column_ratio)
                    else:
                        pr, pg, pb = 255, 255, 255
                    
                    if CENTER_VERTICAL:
                        # Peak dots at both top and bottom of center-out bars
                        center = height // 2
                        half_peak = peak_y // 2
                        # Upper peak
                        y_up = center - half_peak - 1
                        if 0 <= y_up < height:
                            canvas.SetPixel(i, y_up, pr, pg, pb)
                        # Lower peak
                        y_down = center + half_peak
                        if 0 <= y_down < height:
                            canvas.SetPixel(i, y_down, pr, pg, pb)
                    else:
                        # Normal: peak dot above bar
                        y = height - 1 - peak_y
                        if 0 <= y < height:
                            canvas.SetPixel(i, y, pr, pg, pb)
        
        # Swap buffers to display
        canvas = self.matrix.SwapOnVSync(canvas)
        return canvas

# Main function
if __name__ == "__main__":
    fft_matrix = FFTMatrix()
    if not fft_matrix.process():
        fft_matrix.print_help()
