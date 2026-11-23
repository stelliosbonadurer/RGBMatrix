# FFT refactor qualification

> **Status: obsolete qualification procedure for the rejected November 2026
> refactor.** The referenced benchmark program and runtime were removed during the
> rollback to `f23a34a`. Do not run these commands as an active procedure. This file
> is retained to show why the original gates were insufficient.

The procedure overemphasized isolated analyzer/compositor benchmarks and allowed a
30 FPS/2048-sample fallback to stand in for the standard workload. It did not require
an early end-to-end comparison with legacy for FPS, CPU, RSS, render-to-swap latency,
or analyzer starvation under renderer load. A future procedure must add those gates
before the full feature surface is implemented.

The automated harness records compositor/culling, effect modes, bulk-versus-pixel
publication, and one through four distinct analyzer transforms as versioned JSON:

```bash
cd my_programs/fft_program
python3 benchmark_refactor.py --frames 2000 --warmup 200 \
  --output fft-refactor-benchmark.json
```

Run it on the deployed Pi with the same panel dimensions and software revision used
for the visible test. A workstation result is useful for regression detection but
does not satisfy the hardware gates. If NumPy is unavailable, the report explicitly
marks analyzer scenarios as skipped.

For the 30-minute sustained run, use the four-distinct-analyzer/eight-layer
configuration with gradients, overflow, peaks, and shadows enabled. Poll
`/api/v1/music/telemetry` at a modest interval and retain the final bounded counters.
Alongside the JSON report, record:

- Pi model, OS image, Python/NumPy/Pillow/sounddevice and matrix-binding versions.
- Panel geometry and every native matrix flag.
- `vcgencmd measure_temp` and `vcgencmd get_throttled` before, during, and after.
- Process/thread CPU and RSS, audio overruns/discontinuities, analyzer p99 versus hop
  interval, content-frame p99/drops, runtime queue failures, and native refresh rate.
- USB fixture identity, repeatable audio stimulus, and the visible flicker result.
- An audio-to-photon latency measurement; software microbenchmarks cannot establish it.

The standard product profile is a 512-sample Hann window, 512-sample hop, 8192-point
FFT, and 60 FPS content publishing. The acceptance gates are analyzer p99 below 80%
of the 11.61 ms hop interval, no steady-state audio overruns, 60 FPS frame p99 below
16.7 ms, no thermal throttling, and no control-queue starvation. A 30 FPS/2048-sample
profile may be retained as a diagnostic fallback, but it does not pass the normal
visual-quality gate. Keep
the per-pixel rollout fallback enabled for the A/B measurement, then disable it after
the bulk path passes on the target hardware.
