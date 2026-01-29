# FFT Audio Visualization: Deep Dive

This document explains how the FFT-based audio visualizer works, why certain issues occur (like empty columns on larger matrices), and how to tune parameters for optimal results.

---

## Table of Contents
1. [The Problem: Empty Columns on 64x64 Matrix](#the-problem-empty-columns-on-64x64-matrix)
2. [What is FFT?](#what-is-fft)
3. [FFT Frequency Resolution](#fft-frequency-resolution)
4. [Logarithmic Frequency Binning](#logarithmic-frequency-binning)
5. [Why Empty Bins Occur](#why-empty-bins-occur)
6. [Key Parameters Explained](#key-parameters-explained)
7. [The Complete Signal Chain](#the-complete-signal-chain)
8. [Tuning Guide](#tuning-guide)

---

## The Problem: Empty Columns on 64x64 Matrix

### Symptom
When upgrading from a 32x32 to a 64x64 LED matrix, columns 3, 5, 7 (and possibly others) showed no light at all, even with audio playing.

### Root Cause
The visualizer creates one frequency "bin" per column. With 64 columns, we need 64 bins. However, the FFT only produces a finite number of frequency data points. When we divide the frequency range into 64 logarithmic bins, some bins (especially in the lower frequencies) become so narrow that **no FFT data points fall within them**.

### The Fix
Increasing `FFT_SIZE` from 4096 to 8192 (or higher) increases the frequency resolution of the FFT, ensuring every bin has at least one FFT data point mapped to it.

---

## What is FFT?

### The Basics
**FFT (Fast Fourier Transform)** converts a time-domain signal (audio waveform) into a frequency-domain representation (spectrum). 

Think of it this way:
- **Time domain**: "The speaker cone moved this much at each moment in time"
- **Frequency domain**: "Here's how much energy is at each pitch/frequency"

### Visual Analogy
Imagine hearing a chord on a piano. Your ear hears one combined sound (time domain), but your brain separates it into individual notes (frequency domain). FFT does the same thing mathematically.

### The Math (Simplified)
The FFT decomposes your audio signal into a sum of sine waves at different frequencies:

```
signal(t) = A₁·sin(f₁·t) + A₂·sin(f₂·t) + A₃·sin(f₃·t) + ...
```

The FFT tells you the amplitude (A) at each frequency (f).

---

## FFT Frequency Resolution

### The Fundamental Equation

```
Frequency Resolution (Δf) = Sample Rate / FFT_SIZE
```

### Example Calculations

| Sample Rate | FFT_SIZE | Resolution (Δf) | Meaning |
|-------------|----------|-----------------|---------|
| 44100 Hz    | 1024     | 43.07 Hz        | Each FFT bin covers ~43 Hz |
| 44100 Hz    | 2048     | 21.53 Hz        | Each FFT bin covers ~21 Hz |
| 44100 Hz    | 4096     | 10.77 Hz        | Each FFT bin covers ~11 Hz |
| 44100 Hz    | 8192     | 5.38 Hz         | Each FFT bin covers ~5 Hz |
| 44100 Hz    | 16384    | 2.69 Hz         | Each FFT bin covers ~3 Hz |

### What This Means
With `FFT_SIZE = 4096` at 44.1kHz:
- You get `4096/2 + 1 = 2049` frequency bins (only half are useful due to Nyquist)
- Each bin represents ~10.77 Hz
- Bin 0 = 0 Hz (DC offset)
- Bin 1 = 10.77 Hz
- Bin 2 = 21.53 Hz
- Bin 100 = 1077 Hz
- Bin 2048 = 22050 Hz (Nyquist frequency)

### The Nyquist Limit
You can only measure frequencies up to **half the sample rate** (called the Nyquist frequency). At 44.1kHz, the maximum measurable frequency is 22,050 Hz—conveniently just above human hearing range (~20kHz).

---

## Logarithmic Frequency Binning

### Why Logarithmic?
Human hearing perceives pitch **logarithmically**, not linearly:
- The difference between 100 Hz and 200 Hz sounds like "one octave"
- The difference between 1000 Hz and 1100 Hz sounds like a tiny change
- The difference between 1000 Hz and 2000 Hz also sounds like "one octave"

### Linear vs Logarithmic

**Linear binning** (equal Hz per bin):
```
Bin 1: 0-100 Hz      (bass, sub-bass)
Bin 2: 100-200 Hz    (bass)
Bin 3: 200-300 Hz    (low-mid)
...
Bin 30: 2900-3000 Hz (mid-high)
```
Problem: Most of the display shows high frequencies that all sound similar, while bass is crammed into a few columns.

**Logarithmic binning** (equal octaves per bin):
```
Bin 1: 60-85 Hz      (sub-bass)
Bin 2: 85-120 Hz     (bass)
Bin 3: 120-170 Hz    (bass)
...
Bin 30: 7000-10000 Hz (high treble)
```
Result: The display spreads evenly across what we *hear* as evenly-spaced pitches.

### The Code
```python
edges = np.logspace(np.log10(fmin), np.log10(fmax), n + 1)
```
This creates `n+1` edges that are evenly spaced on a **logarithmic** scale, giving us `n` bins.

---

## Why Empty Bins Occur

### The Mismatch Problem
Consider this scenario with `FFT_SIZE = 4096` at 44.1kHz (resolution = 10.77 Hz):

For a 64-column display with frequency range 100-14000 Hz (logarithmic):

```
Column 0:  100.0 Hz - 106.5 Hz   (width: 6.5 Hz)   ← Problem!
Column 1:  106.5 Hz - 113.4 Hz   (width: 6.9 Hz)   ← Problem!
Column 2:  113.4 Hz - 120.8 Hz   (width: 7.4 Hz)   ← Problem!
Column 3:  120.8 Hz - 128.5 Hz   (width: 7.7 Hz)   ← Problem!
...
Column 60: 11000 Hz - 11700 Hz   (width: 700 Hz)   ← Many FFT bins!
Column 63: 13200 Hz - 14000 Hz   (width: 800 Hz)   ← Many FFT bins!
```

**The issue**: At low frequencies, the logarithmic bins are narrower than the FFT resolution!

- FFT resolution: 10.77 Hz per bin
- Column 0 width: 6.5 Hz
- Column 1 width: 6.9 Hz

If a logarithmic bin is narrower than the FFT resolution, there's a chance **no FFT data points fall within it**, resulting in an empty column.

### Visualizing the Problem

```
FFT bins:    |    10.77 Hz    |    10.77 Hz    |    10.77 Hz    |
             0 Hz          10.77 Hz        21.53 Hz         32.30 Hz

Log bins:    |6.5|6.9|7.4|7.7| 8.2 | 8.7 | 9.3 | ...
            100 106 113 121 128  137  145  155 Hz

Notice how some log bins might not contain any FFT bin centers!
```

### The Solution: Increase FFT_SIZE
With `FFT_SIZE = 8192`:
- Resolution = 44100 / 8192 = **5.38 Hz**
- Now even the narrowest log bins (6.5 Hz wide) will contain at least one FFT data point

With `FFT_SIZE = 16384`:
- Resolution = 44100 / 16384 = **2.69 Hz**
- Even more margin for safety

---

## Key Parameters Explained

### Audio Input Parameters

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `BLOCK_SIZE` | 512 | Samples per audio callback (~11.6ms at 44.1kHz). Smaller = lower latency, more CPU |
| `FFT_SIZE` | 8192 | Zero-padded FFT size. Larger = better frequency resolution, more CPU, slight latency |
| `CHANNEL` | 0 | Which audio channel to use (0 = left/mono) |

### Frequency Range

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `MIN_FREQ` | 60 Hz | Lowest frequency to display (below this is rumble/noise) |
| `MAX_FREQ` | 14000 Hz | Highest frequency to display (above this is mostly noise) |
| `ZOOM_MODE` | True | Use zoomed frequency range for specific content |
| `ZOOM_MIN_FREQ` | 100 Hz | Zoomed lower bound |
| `ZOOM_MAX_FREQ` | 3300 Hz | Zoomed upper bound |

### Zero-Padding Explained
`BLOCK_SIZE` is 512 samples, but `FFT_SIZE` is 8192. The FFT **zero-pads** the 512 samples to 8192 before processing. This is called **zero-padding**.

**Benefits of zero-padding:**
1. Better frequency resolution (more bins)
2. Smoother interpolation between true frequency peaks
3. Doesn't add latency (we're not waiting for more audio)

**What it doesn't do:**
- Doesn't add information that wasn't there
- Doesn't improve the fundamental frequency resolution for separating close frequencies (that's determined by `BLOCK_SIZE`)

Think of it as "upsampling" the frequency spectrum for display purposes.

### Windowing

```python
window = np.hanning(BLOCK_SIZE)
x = self.latest * window
```

**Why window?** The FFT assumes the signal repeats forever. If the audio chunk doesn't start and end at zero, the FFT sees a "jump" discontinuity, creating false high frequencies (spectral leakage).

**The Hanning window** smoothly fades the signal to zero at the edges, reducing leakage.

Common windows:
- **Hanning**: Good balance of frequency resolution and leakage reduction
- **Hamming**: Similar to Hanning, slightly different shape
- **Blackman**: Better leakage reduction, worse frequency resolution
- **Rectangular (none)**: Best frequency resolution, worst leakage

---

## The Complete Signal Chain

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AUDIO INPUT                                        │
│  Microphone → 44100 samples/sec → 512 samples per callback (~11.6ms)        │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           WINDOWING                                          │
│  Apply Hanning window to 512 samples (reduces spectral leakage)             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        ZERO-PADDING + FFT                                    │
│  Pad 512 samples → 8192 samples                                             │
│  FFT produces 4097 complex frequency bins                                    │
│  Resolution: 44100/8192 = 5.38 Hz per bin                                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        MAGNITUDE CALCULATION                                 │
│  |FFT| = sqrt(real² + imag²) for each bin                                   │
│  This gives the amplitude at each frequency                                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     LOGARITHMIC BINNING                                      │
│  Map 4097 FFT bins → 64 display columns (log scale)                         │
│  Average FFT magnitudes within each display bin                              │
│  Apply frequency weighting (boost bass or treble)                           │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       NOISE FLOOR                                            │
│  Subtract NOISE_FLOOR from all bins                                         │
│  Clips negative values to 0                                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      NORMALIZATION                                           │
│  Scale values to 0-1 range using:                                           │
│  - Fixed scale (consistent but may clip)                                    │
│  - Rolling RMS (punchy, recommended)                                        │
│  - Rolling max (less punchy)                                                │
│  - Instant (loudest always at max)                                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        SMOOTHING                                             │
│  Asymmetric smoothing: fast rise (SMOOTH_RISE), slow fall (SMOOTH_FALL)     │
│  Makes bars feel responsive but not jittery                                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         DISPLAY                                              │
│  Convert 0-1 values to pixel heights                                        │
│  Apply color theme                                                           │
│  Draw to LED matrix                                                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Tuning Guide

### For Different Matrix Sizes

| Matrix Size | Recommended FFT_SIZE | Notes |
|-------------|---------------------|-------|
| 16 columns  | 2048                | Plenty of resolution |
| 32 columns  | 4096                | Default works well |
| 64 columns  | 8192                | Needed to avoid empty bins |
| 128 columns | 16384               | May need even higher |

**Rule of thumb**: FFT_SIZE should give resolution better than your narrowest log bin.

### For Different Content Types

**Bass-heavy music (EDM, hip-hop)**:
```python
ZOOM_MIN_FREQ = 30
ZOOM_MAX_FREQ = 8000
LOW_FREQ_WEIGHT = 1.2
HIGH_FREQ_WEIGHT = 3.0
```

**Acoustic/Bluegrass**:
```python
ZOOM_MIN_FREQ = 80
ZOOM_MAX_FREQ = 8000
LOW_FREQ_WEIGHT = 0.6
HIGH_FREQ_WEIGHT = 8.0
```

**Voice/Podcasts**:
```python
ZOOM_MIN_FREQ = 100
ZOOM_MAX_FREQ = 4000
LOW_FREQ_WEIGHT = 0.8
HIGH_FREQ_WEIGHT = 5.0
```

**Full spectrum (varied content)**:
```python
ZOOM_MODE = False
MIN_FREQ = 60
MAX_FREQ = 14000
```

### Latency vs Resolution Tradeoffs

| Setting | Lower Latency | Higher Resolution |
|---------|---------------|-------------------|
| BLOCK_SIZE | 256 (5.8ms) | 1024 (23.2ms) |
| FFT_SIZE | 4096 | 16384 |
| SLEEP_DELAY | 0.001 | 0.02 |

**For live music**: Prioritize low latency
**For recorded playback**: Can use higher resolution

### Common Issues and Fixes

| Issue | Likely Cause | Fix |
|-------|--------------|-----|
| Empty columns | FFT_SIZE too small | Increase FFT_SIZE |
| Bars too jumpy | SMOOTH_FALL too high | Lower SMOOTH_FALL (0.1-0.2) |
| Bars too sluggish | SMOOTH_RISE too low | Raise SMOOTH_RISE (0.7-0.9) |
| Only bass shows | HIGH_FREQ_WEIGHT too low | Increase (5-15 range) |
| Too much noise | NOISE_FLOOR too low | Increase (0.3-0.5) |
| Bars always maxed | Scale too sensitive | Lower SENSITIVITY_SCALAR or increase HEADROOM_MULTIPLIER |
| Bars always short | Scale too conservative | Increase SENSITIVITY_SCALAR or decrease HEADROOM_MULTIPLIER |

---

## Mathematical Reference

### Useful Formulas

**Frequency resolution:**
```
Δf = sample_rate / FFT_SIZE
```

**Frequency of FFT bin k:**
```
f(k) = k × sample_rate / FFT_SIZE
```

**FFT bin for frequency f:**
```
k = f × FFT_SIZE / sample_rate
```

**Number of usable FFT bins:**
```
N = FFT_SIZE / 2 + 1
```

**Logarithmic bin edges:**
```
edges[i] = 10^(log10(fmin) + i × (log10(fmax) - log10(fmin)) / num_bins)
```

### Example: Finding FFT Bins for 100-200 Hz

With `FFT_SIZE = 8192` and `sample_rate = 44100`:
```
bin_100Hz = 100 × 8192 / 44100 = 18.6 → bin 18 or 19
bin_200Hz = 200 × 8192 / 44100 = 37.1 → bin 37

So frequencies 100-200 Hz are covered by FFT bins 18-37 (about 19 bins)
```

---

## Further Reading

- [Understanding the FFT](https://www.dspguide.com/ch12.htm) - The Scientist and Engineer's Guide to DSP
- [Spectral Leakage](https://en.wikipedia.org/wiki/Spectral_leakage) - Wikipedia
- [Window Functions](https://en.wikipedia.org/wiki/Window_function) - Wikipedia
- [rpi-rgb-led-matrix](https://github.com/hzeller/rpi-rgb-led-matrix) - The LED matrix library documentation
