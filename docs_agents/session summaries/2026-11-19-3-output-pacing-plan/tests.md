# Tests and evidence

## Source inspection (local ignored driver checkout)

- `lib/led-matrix.cc` `Run` and `SwapOnVSync`: the swap waits for the end of a
  refresh pass.
- `lib/framebuffer.cc`: bit-plane timing, CIE1931 mapping into 11 bits, and
  low-plane truncation for `pwm_bits < 11`.
- `bindings/python/rgbmatrix/cppinc.pxd:25` and `core.pyx:255`: `SwapOnVSync` is not
  `nogil`.
- `core/scaling.py`: smoothing, attack/decay and peaks use per-call constants.

The local `core.pyx` hash matches the step-6 Pi binding source. The `.pxd` was not
hashed on the Pi.

## Calculations

- Colour levels per PWM depth: computed from the driver's `luminance_cie1931` with
  truncation (inline script in conversation). At 11/10/9/8/7 bits there are
  254/235/210/174/123 distinct levels per channel.
- Refresh model: calibrated to measured 104.3 Hz (≈3.05 µs inferred overhead per
  plane). It is an estimate only.

## Microbenchmark

`artifacts/bench_topdown.py`, run with the project venv Python on an Apple M4.
Output is in [artifacts/bench_topdown-output.txt](artifacts/bench_topdown-output.txt):
Python per-pixel top-down over 5 layers took 0.55 ms; NumPy mask compose took
0.113 ms.

Limits: the benchmark replaces Cython `SetPixel` with a Python stub and runs on a
much faster CPU than the Pi 4. It shows only relative shape. It is not Pi timing and
is not an architecture selection.

## Not run

No hardware, Pi, `--led-show-refresh`, or test-suite runs. No NPZ inter-swap analysis;
the artifacts are on the Pi.
