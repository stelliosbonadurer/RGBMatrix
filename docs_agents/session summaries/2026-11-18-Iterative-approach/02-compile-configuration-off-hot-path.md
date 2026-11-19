# Iterative FFT rebuild — step 4: compile configuration off the hot path

> **Status: checkpoints A and B are locally complete and deployed, and all 29
> focused Pi tests pass. A paired five-minute A/B run against the accepted step-3
> files found no material step-4 performance regression. The user accepted step 4
> for proceeding to the next rebuild iteration.**

## Scope

Step 4 is intentionally split into two independently accepted checkpoints:

1. **Checkpoint A — startup compilation.** Validate a versioned candidate for the
   standard one-analyzer projection, compile its derived NumPy arrays once, and use
   one compact runtime-plan reference in the existing FFT loop.
2. **Checkpoint B — transactional live Apply.** Aggregate zoom and weighting changes
   into one candidate, compile once off to the side, and replace the complete plan
   once at the frame boundary. Reject invalid candidates without changing live
   settings, plan identity, or revision.

This record was updated throughout the work. Step 4 is now accepted for proceeding
because both approved implementation checkpoints passed their functional gates and
the paired Pi comparison found no material regression attributable to step 4.

## Source-version map

The Pi checkout is deliberately dirty, so its Git pointer alone does not identify
the code that ran. The versions used in this work are:

| Version label | Source identity | Use in this record |
| --- | --- | --- |
| Accepted step 3 | Local commit `4680c6aa7c1bb889e1cd51b9a452e3d7137f967f` (`Refactor FFT pipeline behind zero-overhead protocols`) | Source baseline for step 4 and the best-code side of the paired comparison. |
| Pi repository metadata | Pi checkout pointer `7be139c`, with preserved dirty rejected-refactor and deployed iterative-rebuild files | Operational checkout only; **not** a complete source-version identifier. |
| Step-3 comparison snapshot | `/home/stelliosbonadurer/rgb_Matrix.step4-predeploy-20261119T014040Z.tar.gz`, SHA-256 `6aa8802ec607274a47592604b9b1e1b65d7eb375ffcbf5ff3bf0755c091e93d4` | Exact step-3 files temporarily restored for the five-minute B run. Its overlapping files match local commit `4680c6aa…`. |
| Accepted step 4 | Pre-commit working tree based on local commit `4680c6aa…`, with the file hashes below | Current deployed version and five-minute A-run code. Replace this working-tree label with the resulting commit hash after committing. |

Accepted step-4 file identities:

| File | SHA-256 |
| --- | --- |
| `my_programs/fft_program/core/__init__.py` | `13b651f950c77400c1eb44a1ffeb7b7a8883d8f7dfbc7142b5119d212dc3565a` |
| `my_programs/fft_program/core/audio.py` | `2505ad78413d18a35268c1929fc16b4754414466f13ee7a8068b83edee815f16` |
| `my_programs/fft_program/core/analysis_plan.py` | `9c2580b02736111e130984b82a6558e355f33e93cb3d80fb6b77cadb053bc882` |
| `my_programs/fft_program/runtime_control.py` | `cc6fbad8c2a3db11995e97202d4060120191323a8fc34d03a380e44691984d02` |
| `tests/test_fft_analysis_plan.py` | `33a26764926a96a69dd292c2d0d585454e7a24be9c561014ccbed7fbde9fd5fa` |
| `tests/test_fft_pipeline_interfaces.py` | `55d076aa9fe8f0c45a8f510aaa88c952c5ae2a92f01001d4597574f6a72ae637` |
| `tests/test_fft_runtime_control.py` | `a8f7b4e93cb6fb059e9b2078d3663509057b5dc192b430349e782a88b690eecf` |

The progress record itself is intentionally identified by the eventual step-4 commit
rather than a self-referential file hash.

## Approved checkpoint-A decisions

- Version and compile only the standard single-analyzer FFT projection first.
- Leave scaling, drawing, themes, peaks, pacing, and layered configuration on their
  existing paths.
- Keep noise floor outside the plan as a live scalar.
- Use a single frozen, slotted plan object allocated only during compilation.
- The versioned candidate contains only inputs that determine projection arrays:
  schema version, block size, FFT size, output-bin count, active frequency range,
  and low/high frequency weights.
- The detected device sample rate is a compiler input, not a configuration field.
- The compiled plan owns only FFT size, the Hann window, 64 bin-index arrays, and
  the 64-element weight array.
- Do not retain duplicate mutable `AudioProcessor` aliases for plan-owned arrays.
- Reject invalid startup configuration rather than silently falling back.
- Keep layered mode as legacy, including its existing second FFT.

## Checkpoint-A implementation

| File | Change |
| --- | --- |
| `my_programs/fft_program/core/analysis_plan.py` | Added the schema-v1 candidate, validation error, frozen slotted runtime plan, and one-time NumPy compiler. |
| `my_programs/fft_program/core/audio.py` | Compiles the standard plan after device-rate discovery and snapshots `active_plan` once per FFT call. Layered mode borrows the compiled window/FFT size but retains its legacy arrays and second FFT. |
| `my_programs/fft_program/core/__init__.py` | Exposes the candidate, plan, validation error, and compiler. |
| `tests/test_fft_analysis_plan.py` | Covers schema/type/range/Nyquist rejection, exact legacy-equivalent arrays, and read-only compiled arrays. |
| `tests/test_fft_pipeline_interfaces.py` | Covers exact FFT output, compile-once startup, stable per-frame plan identity, failed-recompile plan retention, and the existing layered second FFT. |

The standard steady-state analysis path is now:

```text
latest NumPy sample array
  -> snapshot one active_plan reference
  -> existing window multiplication and 8192-point rFFT
  -> existing 64 indexed means and weights
  -> existing live noise-floor subtraction
```

There is no candidate parsing, validation, compilation, registry lookup, mapping,
or new configuration-object allocation in the frame loop. Existing FFT and output
array allocations are unchanged.

## Approved checkpoint-B decisions

- Preserve the existing runtime-command protocol; do not expose a new complete-
  candidate API.
- Let `FftRuntimeController` derive the complete final candidate for a batch while
  `AudioProcessor` remains responsible for hardware-dependent compilation and plan
  activation.
- For a batch containing zoom or weight changes, project every relevant command
  first, compile exactly one final candidate before any mutation, and then apply the
  settings and publish the compiled plan at the same frame boundary.
- If candidate validation or compilation fails, return `invalid_value` and leave
  settings, candidate identity, active-plan identity, unrelated commands, revision,
  and layered arrays unchanged.
- Pre-validate ordered actions whose result depends on earlier commands in the same
  batch. This prevents a later layer-order boundary failure from partially applying
  an already compiled mixed batch.
- Do not add a generic state snapshot/rollback framework for unrelated visual or
  theme operations. Their existing validation and application behavior remains in
  place.
- Keep layered compilation on its legacy path rather than expanding the standard
  plan into a universal graph.

## Checkpoint-B implementation

| File | Change |
| --- | --- |
| `my_programs/fft_program/runtime_control.py` | Projects the final zoom/weight values, compiles one complete candidate before live mutation, suppresses command-by-command recompiles, activates once, and pre-validates ordered layer moves. |
| `tests/test_fft_runtime_control.py` | Covers one compilation/activation for weight, zoom, repeated-zoom, and combined zoom/weight batches; failure leaves all live state unchanged; non-rebuild controls do no plan work; and ordered action failure precedes compilation or mutation. |

The accepted live Apply sequence is now:

```text
validate every command and ordered batch action
  -> project final zoom and weighting values
  -> construct and compile one candidate into temporary arrays
  -> on success, apply command state at the existing frame boundary
  -> rebuild legacy layer arrays once when already active and weights changed
  -> replace analysis_candidate and active_plan together
  -> increment revision once
```

Candidate construction and compilation happen only for startup or a rebuild-class
runtime command. Non-rebuild controls and steady-state frames never construct or
validate a plan.

## Preserved parameters and behavior

- 44,100 Hz accepted hardware profile
- 512-sample capture block
- 8192-point FFT
- 64 output bins
- `ocean` theme
- overflow enabled
- gradient, peaks, and shadows disabled for qualification
- 5 ms post-frame sleep
- direct native-canvas drawing
- peak overlay, VSync swap, and pacing remain in `main.py`
- layered mode still performs a second FFT

No scaling, drawing, matrix, theme, or pacing parameter changed.

## Local verification

Using a local NumPy-capable Python environment after checkpoints A and B:

- Focused FFT/runtime discovery: 29 tests run, 28 passed, one Unix-socket test
  skipped because that environment does not provide the socket operation.
- Runtime control, transport, and controller configuration: 28 tests passed in the
  system Python environment, including its Unix-socket round trip.
- The compiler's window, bin indices, and weights match the prior NumPy operations
  exactly for 44,100 Hz, 512/8192 geometry, and 64 bins.
- Repeated FFT calls retain the same plan identity and do not invoke compilation.
- A failed recompile leaves the active-plan reference unchanged.
- Layered mode still invokes two FFTs when both standard and layered magnitudes are
  requested.
- Weight-only, zoom-only, repeated-zoom, and combined zoom/weight batches compile
  and activate exactly one final standard plan.
- Candidate compilation failure leaves rebuild settings, unrelated batch state,
  candidate, plan, and revision unchanged.
- Ordered action failure is detected before candidate compilation or mutation.
- In-memory syntax compilation passed for all seven changed Python files.
- `git diff --check` passed.

No Pi deployment, service restart, LAN test, or hardware measurement has run for
checkpoint A.

## Pi preflight

The approved read-only preflight completed before deployment:

- `raspberrypi.local` did not resolve from the development host.
- The retained SSH configuration and deployment records identified the accepted
  step-3 target as `stelliosbonadurer@10.8.184.98`; that address connected.
- The remote checkout remains at commit `7be139c` on `main` and retains the expected
  modified and untracked rejected-refactor artifacts.
- Remote `core/__init__.py`, `core/audio.py`, and
  `tests/test_fft_pipeline_interfaces.py` exactly match the SHA-256 hashes of the
  local pre-checkpoint-A baseline. They contain no additional remote-only changes
  that need a three-way merge.
- `core/analysis_plan.py` and `tests/test_fft_analysis_plan.py` do not yet exist on
  the Pi and can be added without overlap.
- The service still launches `/usr/bin/python3 -m matrix_controller.web_service` and
  configures FFT children to use the existing FFT virtual environment.
- The retained opt-in metrics hook still uses `RGBMATRIX_FFT_LEGACY=1` and
  `RGBMATRIX_FFT_METRICS_PATH`; no local telemetry code needs to be added.

The preflight made no remote changes and did not restart a service or visualization.

## Deliberately retained boundaries

- The atomic guarantee in checkpoint B covers candidate validation/compilation and
  known ordered action validation. It does not introduce generic rollback for an
  unexpected exception in an unrelated theme or visualizer setter after application
  begins.
- Layered mode remains legacy. When it is already active, a weight change rebuilds
  its arrays before publishing the standard plan; an unexpected failure inside that
  legacy rebuild is not covered by a second staged layered plan.
- A mixed batch that enables layered mode and changes weights may initialize legacy
  layer arrays during enablement and rebuild them once after the final weights are
  applied. This control-plane-only duplicate work is accepted for this step rather
  than adding a universal layered plan.
- Layered compilation, analysis decoupling, shared memory, processes, output-path
  experiments, generic composition, and additional telemetry remain out of scope.

## Qualification gate decision

The initial plan was to qualify checkpoint A independently on the Pi. After the
read-only preflight proved that all overlapping files match known local baselines,
the user approved deferring hardware qualification until checkpoint B was complete.

This avoids two deployments and two qualification runs. Checkpoint B must add no
steady-state validation, compilation, mapping lookup, or configuration allocation,
so the steady-state performance measurement evaluates the checkpoint-A plan-reference
change. The user subsequently approved the combined deployment, targeted recovery
copy, focused Pi tests, and one 60-second qualification. Their results follow.

## Combined Pi deployment and first qualification

The user approved the combined run. The supplied address `192.167.40.204` timed out;
the development host's neighbor table contained `192.168.40.204` with Raspberry Pi
MAC prefix `2c:cf:67`. The latter host presented the exact ED25519 fingerprint already
stored for both `raspberrypi.local` and the prior VPN address, then identified itself
as the expected `raspberrypi` ARM64 host at commit `7be139c`.

Before upload, all five overlapping remote files matched the local pre-step-4 `HEAD`
hashes exactly. The two new files were absent. No rejected-refactor artifact was
cleaned or overwritten. A targeted recovery archive was created at:

```text
/home/stelliosbonadurer/rgb_Matrix.step4-predeploy-20261119T014040Z.tar.gz
SHA-256 6aa8802ec607274a47592604b9b1e1b65d7eb375ffcbf5ff3bf0755c091e93d4
```

The seven deployed source/test files matched their local SHA-256 hashes exactly.
The Pi FFT virtual environment then ran the analysis-plan, pipeline-interface, and
runtime-control modules: all 29 tests passed in 0.137 seconds.

The qualification used the same retained metrics hook and accepted configuration.
Its raw result remains on the Pi at:

```text
/var/lib/rgb-matrix-controller/fft-step4-qualification.json
```

| Metric | Step 3 baseline | Step 4 first run | Assessment |
| --- | ---: | ---: | --- |
| Frames published | 6,035 | 5,388 | lower |
| Metrics elapsed time | 60.216 s | 60.233 s | equivalent |
| Content FPS | 100.22 | 89.45 | 10.7% lower; gate not met |
| Render-to-swap p50 | 4.45 ms | 4.61 ms | 3.7% slower |
| Render-to-swap p95 | 4.76 ms | 14.02 ms | tail stalls much more frequent |
| Render-to-swap p99 | 14.28 ms | 14.52 ms | similar tail magnitude |
| Publish interval p50 | 9.51 ms | 9.68 ms | 1.7% slower |
| Publish interval p95 | 9.83 ms | 19.11 ms | tail stalls much more frequent |
| Publish interval p99 | 19.42 ms | 19.67 ms | similar tail magnitude |
| Correct renderer CPU window | 85.5–85.8% | 83.7–84.8% | slightly lower lifetime average |
| Renderer RSS | 57,444–57,828 KiB | 59,160 KiB | about 1.3–1.7 MiB higher |
| Active-run temperature | 45.2–47.7 °C | 49.7–51.1 °C | warmer, but unthrottled |
| Throttle status | `0x0` | `0x0` | unchanged |
| Recent runtime/audio errors | none | none | unchanged |

The controller-reported PID was the root `sudo` launcher, not the privilege-dropped
FFT renderer. Its initial `0% / 3,920 KiB` samples were discarded. A corrected
25-second window targeted the actual renderer PID; 22 samples completed before the
expected stop. This correction added process/thermal polling late in the same run,
so the first performance result is valid evidence that the acceptance gate was not
met but is not yet enough to attribute the regression to the compiled plan.

The median render and publish times stayed close to baseline while the p95 values
moved near the prior p99 values. That pattern points to a higher frequency of
scheduling/tail stalls rather than a uniform 10.7% increase in FFT work. A read-only
post-run check found a 1.8 GHz ARM clock, modest load, and no throttle flags.

Cleanup completed despite the metrics file being root-readable only: the temporary
`/run` override is absent, the web service is active, and `system_info` is again the
running visualization. Any repeat or stage-bisect hardware measurement requires a
new explicit approval.

## Paired five-minute step-4 versus step-3 comparison

The user noted that audio content changes how much is drawn and approved a longer,
paired comparison. Each configuration ran for approximately five minutes against the
same physical input and Pi session. The current step-4 configuration ran first. The
script then stopped FFT, restored only `core/__init__.py`, `core/audio.py`, and
`runtime_control.py` from the verified pre-step-4 archive, hash-checked them, and ran
the accepted step-3 configuration. Both runs used the same accepted FFT, matrix,
theme, overflow, direct-canvas, and pacing settings.

External observation used 60 samples at five-second intervals per run. The first
sample captured process initialization and is excluded from the steady-state
CPU/RSS ranges below; all 59 later samples are retained. The lower sampling cadence
also reduced observer interference compared with the first qualification.

| Metric | Current step 4 | Accepted step 3 | A/B assessment |
| --- | ---: | ---: | --- |
| Frames published | 23,326 | 23,609 | step 4 1.2% lower |
| Metrics elapsed time | 304.540 s | 305.012 s | equivalent |
| Content FPS | 76.59 | 77.40 | step 4 1.0% lower |
| Render-to-swap p50 | 4.67 ms | 4.86 ms | step 4 3.9% faster |
| Render-to-swap p95 | 14.17 ms | 14.12 ms | effectively equal |
| Render-to-swap p99 | 14.45 ms | 14.75 ms | step 4 2.0% faster |
| Publish interval p50 | 9.74 ms | 9.93 ms | step 4 1.9% faster |
| Publish interval p95 | 19.24 ms | 19.20 ms | effectively equal |
| Publish interval p99 | 19.54 ms | 19.87 ms | step 4 1.6% faster |
| Steady CPU mean | 78.83% | 81.43% | step 4 2.60 points lower |
| Steady CPU range | 75.9–79.4% | 80.5–83.4% | step 4 lower |
| Steady RSS range | 56,976–58,896 KiB | 59,392–60,416 KiB | step 4 lower |
| Temperature range | 46.3–51.6 °C | 49.7–52.1 °C | order-dependent; both unthrottled |
| Throttle status | `0x0` | `0x0` | equal |
| Recent runtime/audio errors | none | none | equal |

The absolute throughput of both configurations was below the historical 100.22 FPS
run, and both exhibited nearly identical p95 tail behavior. Because the slowdown
followed both code versions in the same session, it cannot be attributed to the
compiled step-4 plan. The direct A/B comparison instead shows a 1.0% FPS difference
with faster step-4 median latency, essentially equal tail latency, and lower step-4
CPU and RSS. This is evidence of no material steady-state performance regression
from step 4 under the observed workload.

Raw paired artifacts remain on the Pi:

```text
/var/lib/rgb-matrix-controller/fft-step4-current-5min.json
/var/lib/rgb-matrix-controller/fft-step3-best-5min.json
/var/lib/rgb-matrix-controller/step4-current-5min-external-samples.txt
/var/lib/rgb-matrix-controller/step3-best-5min-external-samples.txt
```

After the comparison, the current step-4 production files were restored and matched
their deployed hashes. The temporary override was removed, the service was active,
and `system_info` was restored. The rejected-refactor artifacts remained untouched.
