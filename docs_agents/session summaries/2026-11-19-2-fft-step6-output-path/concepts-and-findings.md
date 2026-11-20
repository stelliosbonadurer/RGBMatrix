# Output-path concurrency, buffering, and performance findings

## Case-study question and answer

For a 64x64 HUB75 audio visualizer, does replacing many Python-to-Cython `SetPixel`
calls with one reusable Pillow image and one `SetImage` call materially increase
displayed and fresh-content frame rates?

No. The Pillow design worked correctly on the target hardware, but pooled swap FPS
fell 2.31%. Complete draw-through-swap p50 and p95 improved only 1.70% and 0.27%, far
below the approved 10% material-win threshold. Both counterbalanced directional
pairs showed lower Pillow FPS. Direct rendering remains the accepted path.

This is a useful negative result: reducing the number of calls visible in Python did
not remove the work performed below that call boundary.

## Experiment

The accepted step-5 threaded analyzer and latest-value transport remained fixed. The
comparison changed only output:

- **Direct:** clear the native canvas and call `SetPixel` for visible bar pixels.
- **Pillow:** clear and populate one reused Pillow `RGB` image, then call `SetImage`
  once; the binding traverses all 4,096 image pixels and writes them to the canvas.

Representative live music ran continuously. Four fresh processes used the order
`direct-1 -> pillow-1 -> pillow-2 -> direct-2`; each settled for 60 seconds and then
measured exactly 300 seconds. Exact `uint64` samples captured renderer, swap,
draw-through-swap, result age, sequence identity, and sparse visible-pixel density.
A separate observer sampled the real FFT Python child every five seconds.

## Results

| Run | Swap FPS | Total p50 ms | Total p95 ms | Renderer p50 ms | Swap p50 ms | Unique FPS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `direct-1` | 104.377 | 4.053 | 4.388 | 1.529 | 2.518 | 60.677 |
| `pillow-1` | 101.428 | 3.968 | 4.372 | 1.996 | 1.977 | 59.566 |
| `pillow-2` | 102.385 | 4.007 | 4.366 | 1.976 | 2.047 | 59.543 |
| `direct-2` | 104.256 | 4.076 | 4.369 | 1.510 | 2.594 | 60.286 |

Pooled comparison, expressed as Pillow improvement over direct (positive is better):

| Metric | Pillow change |
| --- | ---: |
| Swap FPS | **-2.31%** |
| Complete-frame p50 | +1.70% |
| Complete-frame p95 | +0.27% |
| Renderer p50 | **-30.66%** |
| Renderer p95 | **-30.69%** |
| Swap p50 | +21.28% |
| Swap p95 | +16.91% |
| Unique-content FPS | -1.53% |
| Published-content FPS | -0.55% |

The first pair (`direct-1` versus `pillow-1`) gave Pillow -2.82% FPS, +2.09% total
p50, and +0.36% total p95. The reversed second pair (`pillow-2` versus `direct-2`)
gave Pillow -1.79% FPS, +1.70% total p50, and +0.08% total p95. Direction reversal
therefore did not change the conclusion.

All four runs were valid. Analysis publication covered 97.3–98.3% of audio callback
generations, result-age p95 stayed under the 23.22 ms two-block gate, callback-status
and worker-failure deltas were zero, and no sample array exhausted capacity. Every
external service sample was active and every throttle reading was `0x0`; temperatures
remained 45.7–53.0°C.

## Why one call was not faster

### Call count is not work count

`SetImage` collapses many Python call sites into one, but the installed Cython
binding still loops over all 4,096 pixels and calls native `SetPixel` internally.
Before that traversal, the application clears and writes the Pillow image. The direct
path touches only visible pixels after a native clear. During these runs the sparse
density samples averaged about 389–457 visible pixels, so the full-frame candidate
often processed roughly an order of magnitude more pixels than the visible bars.

The measured stage split shows the transfer clearly:

- Pillow renderer work became about 31% slower.
- The subsequent measured swap became about 17–21% faster.
- Those effects almost canceled in complete-frame latency.
- The remaining extra work, plus the unchanged five-millisecond pacing sleep, lowered
  full-loop FPS.

The shorter Pillow swap interval does not prove the upload is intrinsically better.
`SetImage` performs native canvas writes during the renderer interval, so more work
has already been completed before `SwapOnVSync` begins. Stage boundaries must follow
where work actually occurs, not the name of the API call.

### Reuse is not zero-copy

The candidate reused one stable image and avoided a NumPy staging frame or per-frame
`frombytes`. That removed avoidable allocation and one possible application copy.
It did not make the installed binding zero-copy: the binding still interpreted
Pillow's private storage and translated every pixel into native canvas state.

“Stable buffer,” “one API call,” “bulk-looking API,” and “zero-copy” are four
different properties. They should never be treated as synonyms in a design review.

## What separate threads did and did not provide

The analyzer and renderer are on different OS threads, so they can overlap when
native NumPy code releases the GIL or when one thread waits. The results show useful
concurrency: both output paths published about 84 analyses per second while swapping
about 102–104 frames per second, and result age remained bounded.

Different threads do not guarantee continuous parallel execution:

- only one thread can execute Python bytecode while holding the CPython GIL;
- the inspected `SetImage` traversal is not declared `nogil`;
- `SwapOnVSync` is also a blocking binding boundary without an explicit `nogil`
  declaration; and
- CPU scheduling, native library behavior, and shared memory/cache traffic still
  affect overlap.

Pillow used more CPU—about 91.9–92.5% versus 86.9–87.2% mean process CPU—without
increasing throughput. Higher CPU was allowed by the experiment, but here it is
evidence of extra work rather than useful parallel speedup.

## Display FPS versus fresh-content FPS

Swap FPS counts every displayed frame, including repeated renders of the newest FFT
result. Unique-content FPS counts sequence changes actually shown. The Pillow unique
rate regressed only 1.53%, within the allowed 5%, while its swap rate regressed 2.31%.
This means Pillow did not catastrophically starve the analyzer; it simply made the
complete output loop slower. Measuring only display FPS would miss content freshness,
while measuring only analysis publication would miss repeated-frame output cost.

## What the experiment proves—and what it does not

It proves that this exact reusable-Pillow design, binding, 64x64 configuration, and
representative sparse music workload is not a material speed improvement. It also
proves target ABI compatibility and stable service behavior for the bounded test.

It does not prove that every Pillow workload loses. Dense effects may change the
direct-path cost. It does not prove that a true native bulk buffer cannot win. It
does not isolate causality as cleanly as deterministic replay because live music
varies, although counterbalancing, density evidence, and close repeats make the speed
decision robust. Finally, process `%CPU` from `ps` is a lifetime average sampled over
time, useful as context but not a precision profiler.

## Future work

The next credible output optimization is a separately scoped native binding method,
not another Pillow wrapper. It should:

1. accept a stable contiguous RGB buffer with explicit shape, stride, channel order,
   lifetime, and mutability contracts;
2. perform validation outside the frame loop;
3. move traversal/copy into C/C++ and deliberately specify whether the GIL is
   released;
4. pin the exact driver/binding source, compiler flags, and binary identity;
5. retain an exact recovery artifact for the installed binding;
6. add a deterministic output-stage diagnostic to explain native cost, while keeping
   representative end-to-end music as the architectural acceptance test; and
7. reuse the same counterbalanced gate rather than accepting a microbenchmark win.

The engineering lesson is broader than this matrix: optimize the complete critical
path, verify where an abstraction performs its hidden work, and treat concurrency as
a measured property rather than an implication of thread count.

## Scope caveat added after review (2026-11-19)

This rejection applies to **one sparse bar layer** (~400–450 lit pixels). Two later
findings limit how far it extends:

1. The loop was refresh-bound. `SwapOnVSync` waits for the end of a ≈9.6 ms
   (≈104 Hz) panel refresh at 11 PWM bits and 130 ns LSB. Any render time saved
   became VSync wait, so the FPS gate could not be passed by *any* output-path
   change at this workload.
2. Direct drawing cost grows with lit pixels across all layers, including overlap.
   A full-frame upload costs a fixed 4,096 pixels. With several layers, `SetImage` or
   a native buffer upload may scale better.

Re-run the output-path comparison when the composite multi-layer path exists. See
[`../2026-11-19-3-output-pacing-plan/`](../2026-11-19-3-output-pacing-plan/README.md)
(D-008).
