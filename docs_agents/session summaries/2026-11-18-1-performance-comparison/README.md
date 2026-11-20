# FFT performance comparison — 2026-11-18

> **Final status: refactor rejected and rolled back.** This directory is the legacy
> evidence package for the failed implementation. The measurements below established
> that the refactor was slower, used more CPU and memory, and allowed renderer load to
> starve analysis and audio capture. The implementation was subsequently removed and
> the repository code/tests were restored to commit `f23a34a`; these Markdown and JSON
> artifacts were deliberately retained so the same mistakes are not repeated.

This session compares the refactored FFT runtime at 60 FPS, at the schema's 240 FPS
ceiling, and with all application pacing removed against the pre-refactor legacy
runtime on the same Raspberry Pi, iMM-6C input, 64×64 HUB75 panel, matrix flags, and
512-sample audio geometry.

## Test order

1. Refactored runtime at the normal 60 FPS target.
2. Refactored runtime at the schema ceiling of 240 content FPS.
3. Legacy runtime with its original 512-sample audio block, 8192-point FFT, and
   5 ms post-frame loop delay.
4. Refactored runtime with the diagnostic `RGBMATRIX_FFT_UNCAPPED=1` flag, which
   removes all application pacing waits while retaining `SwapOnVSync`.

Raw JSON captures and the final comparison are added to this directory as each
hardware run completes.

## Results

| Metric | Refactored 60 | Refactored 240 | Refactored no-wait | Legacy |
|---|---:|---:|---:|---:|
| Achieved content FPS | 36.16 | 46.43 | 70.22 | 94.04 |
| Process CPU | ~127% post-window | 122.06% | 110.36% | 85.82% |
| RSS | ~67.5 MiB post-window | 69.7 MiB | 66.0–67.0 MiB | 56.4–56.6 MiB |
| Frame/publish p50 | 15.94 ms render | 15.01 ms render | 15.64 ms render | 9.84 ms interval |
| Frame/publish p95 | 22.76 ms render | 23.82 ms render | 23.79 ms render | 19.07 ms interval |
| Frame/publish p99 | 25.97 ms render | 28.03 ms render | 27.87 ms render | 19.86 ms interval |
| Analyzer rate | 46.38 Hz | 34.50 Hz | 21.59 Hz | Coupled to render loop |
| Missed analyzer hops | 1,194 | 1,552 | 1,714 | Not applicable |
| Audio overruns | 0 | 1 | 61 | Counter unavailable |
| Temperature | 51.1 °C | 56.4 °C | 56.9 °C | 54.5 °C |
| Throttled | No | No | No | No |

The 240 FPS request still waits for the next 4.17 ms scheduler boundary. Removing
that wait completely raised the refactored renderer to 70.22 FPS, proving that the
pacing algorithm was costing visible throughput. However, the no-wait renderer
starved the analyzer down to 21.59 Hz and produced 61 audio overruns in 30 seconds.
The bulk `SetImage` path was active throughout all refactored measurements.

The legacy loop retained its original 5 ms delay yet produced 94.04 FPS. Compared
with the literal no-wait refactored run, legacy delivered 1.34× the content rate,
used roughly 22% less process CPU, used about 10 MiB less resident memory, and did
not split FFT analysis into a competing scheduler thread.

The legacy internal frame capture covers the full 70.89-second run because its
instrumentation begins after audio startup and flushes on clean shutdown. Its CPU,
RSS, runtime-response, temperature, and throttling samples use the same 30-second
measurement window as the refactored run. Legacy audio overrun/underflow counters do
not exist, so those fields are reported as unavailable rather than inferred.

## Artifacts

- `refactored-60-target.json`: initial restored-default hardware window.
- `refactored-uncapped-240.json`: refactored configuration, counter deltas, timing,
  output-path, audio, process, temperature, and throttling metrics.
- `refactored-true-uncapped-20261118.json`: full telemetry and one-second process
  samples with application pacing completely disabled.
- `legacy-internal-20261118.json`: measured legacy frame count, achieved FPS,
  render-to-swap distribution, and publish-interval distribution.
- `legacy-external-20261118.json`: one-second legacy process samples plus complete
  generic runtime snapshots before and after the 30-second window.

## Final conclusion

The 60 FPS scheduler was one contributor, but not the primary regression. Removing
all application pacing raised output from 36.2 to 70.2 FPS, yet legacy still reached
94.0 FPS with lower CPU and memory. More importantly, maximizing refactored drawing
rate starved analysis and audio capture, so the extra displayed frames often reused
old analysis state. The regression is in total pipeline cost, GIL/thread contention,
and scheduling despite the bulk upload path being active.

This was a release-blocking architectural failure, not a tuning issue. The refactor
did deliver correct configuration, control, and lifecycle behavior, but its hot data
path was rejected as the basis for future work.

## What future work must avoid

- Do not confuse Python threads with analyzer/render CPU isolation. Python-heavy
  callback, analyzer, projection, and renderer work still competes for the GIL.
- Do not write PCM into a ring one sample at a time in the audio callback; use a
  contiguous bulk copy and block-level metadata.
- Do not convert a 4,097-bin NumPy spectrum to Python float tuples, then validate and
  convert it again for each visual layer.
- Do not put per-pixel span objects, color validation, coverage scans, and multiple
  full-frame copies in the standard one-layer render path.
- Do not assume `SetImage` is faster merely because it makes fewer binding calls.
  Compare the complete compose/pack/upload path against direct canvas drawing.
- Do not qualify a replacement only by reducing it to 30 FPS/2048-sample hops. The
  standard workload must pass a same-hardware legacy-relative gate first.
- Do not build the full product surface before a thin vertical slice proves callback
  health, analyzer cadence, content throughput, CPU, RSS, and render-to-swap latency.

The detailed pipeline diagnosis and recommended replacement architecture are in
[`pipeline-comparison-and-next-architecture.md`](pipeline-comparison-and-next-architecture.md).

## Pi state immediately after measurement

The controller service is healthy and the legacy FFT is left running for direct
visual inspection. A runtime-only systemd override at
`/run/systemd/system/rgb-matrix-web@stelliosbonadurer.service.d/legacy-fft.conf`
sets `RGBMATRIX_FFT_LEGACY=1`. It contains no metrics-file setting, so the completed
artifacts are not overwritten. Because the override is under `/run`, it disappears
on reboot; until then, controller restarts continue to select the legacy FFT path.

This section is a historical snapshot from the measurement session and should not be
used as evidence of the Pi's current state.
