# The Mathematics of FFT and Bin Extraction

This document explains why extracting frequency bins from an FFT is computationally trivial compared to the FFT itself—a key insight for real-time audio visualization.

---

## Part 1: The FFT - Where the Real Work Happens

### What the FFT Computes

The Discrete Fourier Transform (DFT) converts a time-domain signal $x[n]$ into frequency-domain coefficients $X[k]$:

$$X[k] = \sum_{n=0}^{N-1} x[n] \cdot e^{-i \frac{2\pi kn}{N}}$$

Where:
- $N$ = number of samples (e.g., 2048)
- $x[n]$ = input sample at index $n$
- $X[k]$ = complex frequency coefficient at bin $k$
- $e^{-i\theta} = \cos(\theta) - i\sin(\theta)$ (Euler's formula)

### Naive DFT: O(N²)

Computing this directly requires:
- For each of $N$ output bins...
- Sum over all $N$ input samples...
- = $N \times N = N^2$ complex multiplications

For $N = 2048$: **4,194,304 operations** 😱

### The FFT Trick: O(N log N)

The Fast Fourier Transform exploits **symmetry** in the DFT. The key insight (Cooley-Tukey algorithm):

$$X[k] = E[k] + W_N^k \cdot O[k]$$
$$X[k + N/2] = E[k] - W_N^k \cdot O[k]$$

Where:
- $E[k]$ = DFT of even-indexed samples
- $O[k]$ = DFT of odd-indexed samples  
- $W_N^k = e^{-i \frac{2\pi k}{N}}$ (twiddle factor)

This **divide-and-conquer** approach:
1. Split $N$ samples into two $N/2$ problems
2. Recursively split until you have $N$ single-sample "DFTs"
3. Combine results back up the tree

```
Level 0:  [  2048 samples  ]           → 1 problem of size 2048
Level 1:  [ 1024 ]  [ 1024 ]           → 2 problems of size 1024
Level 2:  [512][512][512][512]         → 4 problems of size 512
...
Level 11: [1][1][1]...[1][1][1]        → 2048 trivial problems
```

**Depth of tree**: $\log_2(N)$ levels  
**Work per level**: $O(N)$ operations  
**Total**: $O(N \log N)$

For $N = 2048$: $2048 \times 11 \approx$ **22,528 operations** ✨

That's a **186× speedup** over naive DFT!

---

## Part 2: What the FFT Gives You

After the FFT, you have an array of $N$ complex numbers:

```python
fft_result = np.fft.rfft(audio_samples)  # Returns N/2+1 complex values
# fft_result[0]   → DC component (0 Hz)
# fft_result[1]   → First frequency bin
# fft_result[k]   → k-th frequency bin
# fft_result[N/2] → Nyquist frequency
```

### Frequency Resolution

Each bin $k$ corresponds to a specific frequency:

$$f_k = k \cdot \frac{f_s}{N}$$

Where:
- $f_s$ = sample rate (e.g., 44100 Hz)
- $N$ = FFT size (e.g., 2048)

**Bin width** (frequency resolution):
$$\Delta f = \frac{f_s}{N} = \frac{44100}{2048} \approx 21.5 \text{ Hz}$$

So with 2048 samples at 44.1kHz:
- Bin 0 → 0 Hz (DC)
- Bin 1 → 21.5 Hz
- Bin 10 → 215 Hz
- Bin 100 → 2150 Hz
- Bin 1024 → 22050 Hz (Nyquist)

---

## Part 3: Bin Extraction - The Cheap Part

### The Problem

You have 1025 frequency bins (from `rfft`), but you only want, say, 64 visualization bars spanning 80-6000 Hz.

### The Math: Frequency → Bin Index

To find which FFT bin corresponds to a frequency $f$:

$$k = \text{round}\left(\frac{f \cdot N}{f_s}\right)$$

Example: What bin is 440 Hz (concert A)?
$$k = \text{round}\left(\frac{440 \times 2048}{44100}\right) = \text{round}(20.44) = 20$$

### Creating Visualization Bins

For logarithmic frequency spacing (which matches human hearing):

```python
# Generate 64 logarithmically-spaced edges from 80Hz to 6000Hz
freq_edges = np.logspace(np.log10(80), np.log10(6000), num=65)
# [80, 93, 108, 125, 145, ... , 5173, 6000]

# Convert to FFT bin indices
bin_edges = (freq_edges * N / sample_rate).astype(int)
# [4, 4, 5, 6, 7, ... , 240, 279]
```

### The Extraction: Just Array Slicing!

```python
for i in range(64):
    start = bin_edges[i]
    end = bin_edges[i + 1]
    
    # This is just reading consecutive memory locations!
    bar_value = np.mean(fft_magnitudes[start:end])
```

**What's happening computationally:**

| Visualization Bin | FFT Bins | Operations |
|-------------------|----------|------------|
| Bar 0 (80-93 Hz)  | bins 4-4 | Read 1 value |
| Bar 1 (93-108 Hz) | bins 4-5 | Read 2, average |
| Bar 30 (500-580 Hz) | bins 23-27 | Read 5, average |
| Bar 63 (5173-6000 Hz) | bins 240-279 | Read 40, average |

### Complexity Analysis

**Per visualization bin:**
- Look up 2 indices: $O(1)$
- Slice array: $O(1)$ (just pointer arithmetic)
- Sum values: $O(w)$ where $w$ = width of bin
- Divide by count: $O(1)$

**Total for all bins:**
$$O\left(\sum_{i=0}^{B-1} w_i\right) = O(W)$$

Where $W$ = total width of all bins combined.

In practice, if you're extracting 64 bins from the range covering ~300 FFT bins:
- **Best case**: Each viz bin spans 1 FFT bin → 64 operations
- **Worst case**: Read every FFT bin in range → 300 operations
- **Typical**: ~150-200 operations

---

## Part 4: The Cost Comparison

### For N = 2048 samples, 64 visualization bins

| Operation | Complexity | Actual Operations |
|-----------|------------|-------------------|
| FFT | $O(N \log N)$ | ~22,500 |
| Bin extraction | $O(W)$ | ~200 |
| **Ratio** | | **112:1** |

The FFT is doing **99.1%** of the computational work!

### Why This Matters for Multi-Layer Mode

When we run multi-layer visualization (e.g., 3 layers: bass, mid, treble):

```
┌─────────────────────────────────────────┐
│           FFT Computation               │  ← 22,500 ops (done ONCE)
│         O(N log N) = O(2048 × 11)       │
└─────────────────────────────────────────┘
                    │
        ┌───────────┼───────────┬───────────┐
        ▼           ▼           ▼           ▼
┌────────────┐ ┌────────────┐ ┌────────────┐
│ Layer 0    │ │ Layer 1    │ │ Layer 2    │
│ Bass       │ │ Mid        │ │ Treble     │
│ 64 bins    │ │ 64 bins    │ │ 64 bins    │
│ 80-1950 Hz │ │ 2000-4000Hz│ │ 4000-8000Hz│
│ ~100 ops   │ │ ~100 ops   │ │ ~100 ops   │
└────────────┘ └────────────┘ └────────────┘
```

**Total**: 22,500 + 100 + 100 + 100 = 22,800 ops (3 layers)

vs. running three separate FFTs: 22,500 × 3 = 67,500 ops

**Multi-layer mode overhead: ~1.3%** 🎉

---

## Part 5: The Deeper Math - Why Logarithmic Bins?

### Human Hearing is Logarithmic

The cochlea (inner ear) maps frequencies logarithmically. An octave (2× frequency) always sounds like "the same distance":

| Interval | Frequencies | Ratio |
|----------|-------------|-------|
| A3 → A4 | 220 → 440 Hz | 2:1 |
| A4 → A5 | 440 → 880 Hz | 2:1 |
| A5 → A6 | 880 → 1760 Hz | 2:1 |

Each sounds like "going up one octave" even though the Hz difference grows!

### Log-spacing Formula

To create $B$ bins from $f_{min}$ to $f_{max}$:

$$f_i = f_{min} \cdot \left(\frac{f_{max}}{f_{min}}\right)^{i/B}$$

Or equivalently:
$$\log(f_i) = \log(f_{min}) + \frac{i}{B} \cdot \left(\log(f_{max}) - \log(f_{min})\right)$$

This is what `np.logspace()` computes.

### The Result

| Linear Bins (equal Hz) | Logarithmic Bins (equal octaves) |
|------------------------|----------------------------------|
| Bin 1: 0-100 Hz | Bin 1: 80-100 Hz |
| Bin 2: 100-200 Hz | Bin 2: 100-125 Hz |
| Bin 3: 200-300 Hz | Bin 3: 125-160 Hz |
| ... | ... |
| Bin 60: 5900-6000 Hz | Bin 60: 4000-5000 Hz |

Linear bins waste resolution on high frequencies we can't distinguish, while cramming all the bass detail into 1-2 bins!

---

## Part 6: Frequency Weighting - Compensating for Physics

### The Problem

Higher frequencies have less energy naturally (most sound energy is in bass). Without compensation, treble bars barely move.

### The Solution: Perceptual Weighting

We apply a frequency-dependent boost:

$$w(f) = w_{low} + (w_{high} - w_{low}) \cdot \left(\frac{f - f_{min}}{f_{max} - f_{min}}\right)$$

With $w_{low} = 0.55$ and $w_{high} = 10.0$:

| Frequency | Weight |
|-----------|--------|
| 80 Hz | 0.55× |
| 1000 Hz | 1.0× |
| 3000 Hz | 2.0× |
| 6000 Hz | 3.5× |
| 10000 Hz | 5.5× |
| 20000 Hz | 10.0× |

This compensates for:
1. Natural 1/f roll-off of most audio
2. Equal-loudness contours (Fletcher-Munson curves)
3. Microphone/speaker frequency response

---

## Summary

| Stage | Complexity | Operations (typical) | % of Total |
|-------|------------|----------------------|------------|
| Audio capture | $O(N)$ | 2,048 | 8% |
| Window function | $O(N)$ | 2,048 | 8% |
| **FFT** | $O(N \log N)$ | **22,528** | **83%** |
| Magnitude calc | $O(N)$ | 1,024 | 4% |
| Bin extraction | $O(W)$ | ~200 | **<1%** |

The FFT is the computational bottleneck. Everything else—including fancy multi-layer visualizations with different frequency ranges—is essentially free in comparison.

This is why we can run multi-layer mode, or even a hypothetical 10-layer system, with minimal performance impact: **the FFT is computed once, and we just slice it differently for each layer.**

Additional optimization: **Invisible layers skip even the bin extraction and scaling**, so hiding a layer saves its processing cost entirely.

---

## References

- Cooley, James W., and John W. Tukey. "An algorithm for the machine calculation of complex Fourier series." *Mathematics of computation* 19.90 (1965): 297-301.
- Smith, Steven W. *The Scientist and Engineer's Guide to Digital Signal Processing*. California Technical Publishing, 1997. (Free online: dspguide.com)
- Fletcher, H., and W.A. Munson. "Loudness, its definition, measurement and calculation." *Bell System Technical Journal* 12.4 (1933): 377-430.
