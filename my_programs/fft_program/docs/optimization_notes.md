# FFT Visualizer Optimization Notes

## The Problem

After refactoring the `fft_sandbox.py` monolithic script into a modular architecture, the new program was noticeably slower and less responsive than the original. There was also a visual artifact where pixels appeared dimmer around the halfway point horizontally.

## Initial "Optimizations" That Made Things Worse

We initially applied several common optimization techniques that backfired on a Raspberry Pi with a 64x64 display:

### 1. Pre-computed Color Lookup Tables (LUTs)

**What we tried:**

- Built a 64×64 color lookup table at theme initialization
- Used array indexing instead of calculating colors on-the-fly

**Why it failed:**

- For simple color calculations like `r=255, g=int(165*ratio), b=0`, the LUT overhead was *more* expensive than the math
- LUT access involved:
  - Property access with `None` check (lazy loading)
  - Array bounds checking
  - Tuple construction with 3 `int()` casts
- The 64-level quantization caused visible color banding (the "dimmer pixels" artifact)

### 2. NumPy Pre-computed Arrays for Ratios

**What we tried:**

```python
self.column_ratios = np.arange(width, dtype=np.float32) / width
self.height_ratios = np.arange(height, dtype=np.float32) / height
```

**Why it failed:**

- Array lookup with bounds checking: `self.height_ratios[j] if j < len(self.height_ratios) else 0.0`
- Was slower than simple division: `j / height`

### 3. Extracting Color Calculation to Methods

**What we tried:**

```python
r, g, b = self._get_layer_color(layer, layer_ratio, column_ratio)
# Which called:
return self.theme.get_overflow_color(layer, height_ratio, column_ratio)
```

**Why it failed:**

- Python function calls have ~100-200ns overhead each
- With 64×64 = 4096 potential pixels per frame at 250fps, this adds significant latency
- The original `fft_sandbox.py` used **inline** color calculation with zero function calls in the hot loop

## The Fix: Match fft_sandbox.py's Approach

### Direct Color Calculation

Changed from abstract `_calculate_color()` + LUT to direct `get_color()` methods:

```python
# Before (LUT-based)
def get_color(self, height_ratio, column_ratio):
    h_idx = min(self.height_levels - 1, max(0, int(height_ratio * (self.height_levels - 1))))
    c_idx = min(self.column_levels - 1, max(0, int(column_ratio * (self.column_levels - 1))))
    color = self.color_lut[h_idx, c_idx]
    return (int(color[0]), int(color[1]), int(color[2]))

# After (direct calculation)
def get_color(self, height_ratio, column_ratio):
    r = 255
    g = int(165 * height_ratio)
    b = 0
    return self._apply_brightness(r, g, b)
```

### Inline Color Calculation in Draw Loop

Changed `bars_overflow.py` to match `fft_sandbox.py` exactly:

```python
# Before
r, g, b = self._get_layer_color(layer, layer_ratio, column_ratio)

# After (inline, no function calls)
if layer == 0:
    r = 255
    g = int(165 * layer_ratio)
    b = 0
elif layer == 1:
    r = 255
    g = int(165 + 90 * layer_ratio)
    b = int(255 * layer_ratio)
elif layer == 2:
    r, g, b = overflow.color_2
else:
    r, g, b = overflow.color_3
```

### Simple Division Over Array Lookup

```python
# Before
layer_ratio = self.height_ratios[j] if j < len(self.height_ratios) else 0.0

# After
layer_ratio = j / height
```

## Key Lessons Learned

1. **Profile before optimizing** - Our "optimizations" were based on general best practices, not actual profiling on the target hardware.

2. **Function call overhead matters in Python** - In tight loops with thousands of iterations, even simple method calls add up.

3. **NumPy isn't always faster** - For small arrays (64 elements), Python's native operations can be faster due to NumPy's setup overhead.

4. **LUTs can hurt small data** - When the calculation is trivial (one multiply, one int cast), a LUT lookup with bounds checking is slower.

5. **Copy what works** - The original `fft_sandbox.py` was fast for a reason. Match its patterns rather than "improving" them.

## Performance Characteristics

| Approach | Per-Pixel Cost | Notes |

| Inline math | ~50ns | Fastest, no function calls |
| Method call | ~150-250ns | 3-5x slower |
| LUT lookup | ~200-300ns | Array access + tuple creation |
| NumPy array | ~100-200ns | Overhead for small arrays |

## What We Kept

The numpy vectorization in `scaling.py` for smoothing and peak tracking was kept because:

- It operates on arrays of 64 elements once per frame (not per pixel)
- The vectorized operations (`np.where`, `np.maximum`) are genuinely faster than Python loops for this size
- These run once per frame, not thousands of times per frame

```python
# This is fine - runs once per frame on 64 elements
delta = normalized - self.smoothed_bars
rates = np.where(delta > 0, self.smoothing.rise, self.smoothing.fall)
self.smoothed_bars += delta * rates
```

## Conclusion

The modular architecture is valuable for maintainability, but performance-critical inner loops should minimize abstraction. The draw loop runs thousands of times per frame and needs inline code. The audio processing and scaling can use cleaner abstractions since they only run once per frame.

**Rule of thumb:** If code runs O(width × height) times per frame, inline it. If it runs O(width) times per frame, methods are fine.
