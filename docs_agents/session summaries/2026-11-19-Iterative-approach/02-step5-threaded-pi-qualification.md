# FFT rebuild session summary — step-5 threaded Pi qualification

> **Session date:** 2026-11-19 local time (America/Denver)
>
> **Outcome:** The smallest threaded, latest-value 64-bin transport passed its
> Raspberry Pi compatibility tests and corrected paired five-minute qualification.
> It preserved callback/analyzer service and materially improved content throughput
> and render-tail latency. The evidence does not justify moving this contract to a
> process or shared memory. The Pi was restored to accepted step 4 after testing;
> production promotion of step 5 remains a separate decision.

## Scope and architectural boundary

This session qualified rebuild step 5:

> Decouple analysis with the smallest transport. First try a latest 64-bin double
> buffer. If a thread cannot preserve analyzer/callback service under render load,
> move the same contract to a separate process with shared memory.

The candidate uses one analysis thread, two fixed contiguous NumPy result slots, and
latest-value publication. The PortAudio callback publishes only the newest copied
512-sample block and its generation. The worker coalesces generations when necessary,
performs the accepted 8192-point FFT and 64-bin projection, and publishes only a
complete result. The render loop never waits for analysis: it continues to scale,
advance attack/decay and peaks, draw to the direct native canvas, swap on VSync, and
sleep for 5 ms using the latest complete analysis frame.

The obsolete special-case layered path was removed before qualification. Future
multi-view work will use multiple instances of the standard path rather than restore
that implementation. No output-path experiment, `SetImage` comparison, composite
renderer, generic graph, advanced analyzer, or multi-layer optimization was included.

Forward-looking decisions and unanswered questions about analyzer sharing, noise
floor ownership, per-visualization response curves, projection parameters, and a
future timing model are recorded separately in
[`01-noise-floor-placement-and-shared-analysis.md`](01-noise-floor-placement-and-shared-analysis.md).

## Exact source and recovery map

The Pi checkout is intentionally dirty, so its Git pointer is operational metadata,
not a measured source identity.

| Role | Exact identity |
| --- | --- |
| Accepted step-3 reference | Local commit `4680c6aa7c1bb889e1cd51b9a452e3d7137f967f` |
| Accepted step-4 baseline | Local commit `21a13d938be7178799302d35976a1c5c7d4c1065` (`Compile FFT configuration into atomic runtime plans`) |
| Step-5 threaded implementation | Local commit `5e018a4a3ec36ea81fb985153b2897fd9a3d184a` (`Decouple FFT analysis with latest-value transport`) |
| Current documentation-only HEAD after recording the step-5 source identity | Local commit `b25d567246fe4d6b0e42de0a5bce7d3316bb45ee` (`Record accepted step 5 source identity`) |
| Pi checkout pointer | `7be139c`, with intentional modified and untracked artifacts; **not a measured-version label** |
| Exact pre-step-5 Pi baseline/recovery archive | `/home/stelliosbonadurer/rgb_Matrix.step5-predeploy-20261120T002147Z.tar.gz` |
| Recovery archive SHA-256 | `1f419182b714294d28630e0fc8570c020c85705cc84c656d10bbec996852946e` |
| Measured qualification-variant archive SHA-256 | `fcfb853ab94a2a6cc9bad22709f2074959639b36c671331b9fd9e64f79460084` |

The recovery archive has a November 20 UTC timestamp but was created during the
November 19 local session. It is the exact accepted step-4 production/test state used
for the paired baseline and final restoration. The older pre-step-4 archive remains
historical comparison evidence and is not the step-5 rollback point.

The measured candidate was a narrow merge of commit `5e018a4` into the exact deployed
baseline:

- `core/audio.py`, `core/interfaces.py`, `runtime_control.py`, and
  `visualizers/bars_unified.py` exactly matched the step-5 commit;
- `config/settings.py` preserved the Pi's corrected comment that 512 samples are
  approximately 11.6 ms at 44.1 kHz; and
- `main.py` combined the step-5 execution semantics with the Pi-only legacy/service
  routing, disabled runtime-state writes, and temporary opt-in qualification metrics.
  Result age used the timestamp of the exact acquired transport slot.

### Measured deployment file identities

| File | SHA-256 |
| --- | --- |
| `my_programs/fft_program/config/settings.py` | `f077ff87c72c391086528839350e81c19a428ca1058e9b0c90ad852ae444dbe1` |
| `my_programs/fft_program/core/audio.py` | `250edf5d8dbcf1a8313bceb7c231011b71336de788cc14f8c8d23fbcfa37bec9` |
| `my_programs/fft_program/core/interfaces.py` | `953206f3c89d178456953fb39d7e738d93a48ce6d04b5e4dc9471e171e082823` |
| `my_programs/fft_program/main.py` | `4ed6c0d9b6160b9e02dc89c1be807a0bc43562521dcd65aef7f4f52d093d11c0` |
| `my_programs/fft_program/runtime_control.py` | `6f410139d3c0e58b0a0158aa985e1c7e03fd9e9be908925ecd20f05a05bf6fdd` |
| `my_programs/fft_program/visualizers/bars_unified.py` | `a85ad22546dd46c17d1db29c79fa47a13f4abbeb83e6efc45f81116149b0cdc7` |
| `tests/test_fft_pipeline_interfaces.py` | `bf5be162cbe5d02d7e6b64ee3d3561bb661022e68a00d8e9bdee8764238d4b18` |
| `tests/test_fft_runtime_control.py` | `5ab94045006ed711586311a9ee3379e1b91e3d18c019d0835c02b27ca514930e` |

## Verification before measurement

The candidate passed three relevant test stages:

1. A non-deployment staging run on the Pi initially exposed two incomplete staging
   bundles (`matrix_controller`, then `matrix_controller.registry`, were absent).
   These were staging dependency errors, not candidate failures. With the complete
   exact-commit package staged, **46 tests passed in 0.185 seconds**, all **38 Python
   files** compiled in memory, and no `__pycache__` was created.
2. The qualification merge passed **46 focused local tests**, with the one existing
   platform-dependent Unix-socket test skipped.
3. The deployed candidate passed **47 focused tests in 0.202 seconds** on the Pi. The
   restored step-4 baseline then passed its **29 focused tests in 0.118 seconds**.

Independent diff and hot-path review found no blocking issue. It verified the direct
standard-path order:

```text
acquire latest complete analysis slot
  -> sample exact slot age
  -> scaling / attack-decay / peaks
  -> release slot in finally
  -> direct native-canvas draw
  -> optional peak overlay
  -> SwapOnVSync
  -> optional qualification timestamp
  -> 5 ms sleep
```

No `SetImage` call or layered execution path was introduced.

## Approved measurement conditions

Both versions ran in the same Raspberry Pi/audio session with continuous
representative music at fixed volume through the iMM-6C. The candidate ran first,
followed by the exact step-4 baseline. Each window was five minutes with 60 external
samples at five-second intervals; the first startup sample was excluded from steady
CPU, RSS, and temperature statistics.

Both versions used the accepted qualification settings:

- 64x64 matrix;
- 44,100 Hz input and 512-sample capture block;
- 8192-point FFT and 64 output bins;
- `ocean` theme with overflow enabled;
- gradient, peaks, and shadows disabled;
- direct native-canvas output; and
- 5 ms post-frame sleep.

Only temporary `/run` service overrides were used.

## Observer correction

The first paired attempt's external sampler followed the supervisor's idle `sudo`
wrapper instead of the descendant FFT Python process. Its reported 0% CPU and roughly
4 MiB RSS were therefore invalid. The internal metrics from those runs remain valid:
the first candidate run reported 104.05 FPS and the first baseline run 78.85 FPS.

The invalid evidence was retained rather than overwritten. The sampler was corrected
to resolve the descendant Python PID and verify that its command contained
`my_programs/fft_program/main.py`. A two-second smoke check observed the wrapper and
Python child separately, including the Python process at 92.5% CPU, 58,912 KiB RSS,
and seven threads after startup. The complete five-minute pair was then repeated.
Only this corrected pair is used for CPU and RSS acceptance evidence.

## Corrected paired five-minute result

| Metric | Step 5 threaded | Exact step-4 baseline | Comparison |
| --- | ---: | ---: | --- |
| Frames published | 32,087 | 21,843 | step 5 published 46.9% more frames |
| Metrics elapsed | 307.657 s | 307.372 s | equivalent windows |
| Content FPS | **104.29** | **71.06** | **46.8% higher** |
| Render-to-swap p50 | **4.39 ms** | **4.77 ms** | **8.0% faster** |
| Render-to-swap p95 | **4.69 ms** | **14.28 ms** | **67.1% faster** |
| Render-to-swap p99 | **4.77 ms** | **14.53 ms** | **67.1% faster** |
| Publish interval p50 | **9.54 ms** | **9.84 ms** | faster |
| Publish interval p95 | **9.78 ms** | **19.35 ms** | frequent doubled interval removed |
| Publish interval p99 | **9.88 ms** | **19.62 ms** | frequent doubled interval removed |
| Steady CPU mean | **86.66%** | **78.12%** | +8.54 percentage points; +10.9% relative |
| Steady CPU range | 86.5-88.5% | 75.2-80.1% | both below one full core on average |
| Steady RSS mean | 58,864.2 KiB | 60,296.3 KiB | not clean production-memory evidence |
| Steady RSS range | 56,916-61,140 KiB | 60,164-60,804 KiB | overlapping |
| Python threads | 7 | 6 | expected analysis worker difference |
| Temperature | 48.2-51.6 C | 49.1-52.1 C | healthy; order-sensitive |
| Throttle status | `0x0` | `0x0` | equal |
| Recent runtime/audio errors | none | none | equal |

The temporary metrics hook accumulates frame-duration samples. Because step 5
rendered about 47% more frames, it also retained about 47% more timing entries. The
RSS figures are documented, but they must not be presented as evidence of a material
step-5 memory win.

## Analyzer and callback service gates

| Evidence | Step-5 result | Decision |
| --- | ---: | --- |
| Callback sample generations | 26,534 | context |
| Last analyzed generation | 26,534 | worker reached the newest callback generation |
| Complete published analysis sequences | 26,014 | context |
| Analyzed/published fraction | **98.04%** | passes the >=95% gate |
| Skipped sample generations | 520 (1.96%) | expected bounded latest-value loss |
| Callback-status errors | **0** | passes |
| Worker failures | **0** | passes |
| Result-transport drops | 7 | bounded; no queue or backlog |
| Result age p50 | 2.56 ms | healthy |
| Result age p95 | **18.49 ms** | passes |
| Result age p99 | 20.60 ms | below two capture blocks |
| Result age maximum | 21.79 ms | below two capture blocks |

One 512-sample block at 44.1 kHz is 11.61 ms, so two blocks are **23.22 ms**. Earlier
planning prose incorrectly described two blocks as approximately 46.4 ms; that is
four blocks. The measured p95, p99, and maximum pass the stricter, corrected
two-block threshold, so the correction strengthens rather than weakens the result.

## Interpretation and checkpoint decision

The thread met every approved service gate on the first valid long run. It kept the
render loop continuously active, analyzed 98.04% of callback generations, introduced
no callback or worker failures, maintained a bounded lossy transport without backlog,
and kept even the maximum acquired-result age below two capture blocks.

Compared in the same session, step 5 delivered 46.8% higher content FPS and sharply
reduced p95/p99 render latency. The cost was 8.54 additional CPU percentage points.
That is an intentional use of otherwise available multicore capacity to improve
service regularity and output cadence. The result supports the threaded checkpoint
and provides no evidence for paying the complexity and copy/synchronization cost of a
process/shared-memory boundary.

This is qualification evidence, not an automatic production promotion. At the end of
the session the exact accepted step-4 files were restored. A separate user decision
is required before step 5 becomes the production state.

## Retained raw evidence

Corrected acceptance artifacts on the Pi:

```text
/var/lib/rgb-matrix-controller/fft-step5-threaded-paired-rerun-5min-20261119.json
SHA-256 43819dd18a8fd76385261fb6046a26299690332de76d262e45b72a874c2a63ba

/var/lib/rgb-matrix-controller/step5-threaded-paired-rerun-5min-external-samples-20261119.txt
SHA-256 1e22b16f15fbfd736dc12fb03e310147d4c0061d22e84b389f709d322da077e0

/var/lib/rgb-matrix-controller/fft-step4-baseline-paired-rerun-5min-20261119.json
SHA-256 2d4e773c69556d0c9192198634f02e567de01ebaae45d7ffed27da444cf1d932

/var/lib/rgb-matrix-controller/step4-baseline-paired-rerun-5min-external-samples-20261119.txt
SHA-256 e78b49a2d4b21a43264de1bafe636ddc5efa53ad0fe5dc11aced73184a0fe7c0
```

The first attempt's artifacts remain under the equivalent non-`rerun` names. Their
internal JSON may be used as supporting timing evidence, but their external sample
files must never be used for CPU or RSS conclusions.

## Final Pi operational state

After qualification:

- all eight overlapping production/test files matched their preflight step-4 hashes;
- the temporary `/run` override was absent;
- the web service was active;
- `system_info` was the active visualization;
- throttle status was `0x0`;
- recent runtime/audio errors were absent;
- the Pi Git pointer remained `7be139c`; and
- intentional remote-only rejected-refactor and deployment artifacts were preserved.

## Remaining decisions and next work

The immediate open decision is whether and when to promote the already-qualified
step-5 implementation to the Pi's production state. The temporary metrics hook is
qualification instrumentation and must not silently become a permanent telemetry
subsystem.

After that decision, later modular checkpoints can address the design questions in
the first file in this folder: multiple visualization instances sharing analyzers,
analyzer-owned configurable projections, per-visualization four-point response
curves, source versus view noise controls, and a cadence model that makes rise/fall
behavior time-consistent when FPS changes. None of those changes should be folded
retroactively into the qualified step-5 comparison.
