# Iterative FFT rebuild — step 5: decouple analysis with the smallest transport

> **Status: threaded checkpoint A is implemented, passes local and Pi tests, and
> passes its paired five-minute Raspberry Pi qualification.**
> The thread preserved callback/analyzer service and materially improved content
> throughput and render latency. There is no evidence justifying a process boundary.

## Purpose and scope

This implements step 5 of
[`pipeline-comparison-and-next-architecture.md`](../2026-11-18-performance-comparison/pipeline-comparison-and-next-architecture.md):

> Decouple analysis with the smallest transport. First try a latest 64-bin double
> buffer. If a thread cannot preserve analyzer/callback service under render load,
> move the same contract to a separate process with shared memory.

Checkpoint A is deliberately a threaded standard-mode experiment. It does not
pre-judge the need for a process. A process boundary is permitted only after the
threaded contract fails the approved service gates reproducibly under an approved
same-session hardware comparison.

This checkpoint does not change the standard qualification parameters or the direct
native-canvas output path. It does not begin output-path
experiments, `SetImage` comparisons, composite rendering, advanced analyzers, or
multi-layer optimization.

During implementation, the user deliberately contracted the product surface: the
obsolete special-case layered feature was deleted. Future multi-view work will create
multiple instances of the standard analyzer/scaler/drawing path built here. That
future work is not part of step 5.

## Source-version map

The Pi checkout is deliberately dirty, so its Git pointer alone never identifies
the measured source. The versions relevant to step 5 are:

| Version label | Source identity | Use in this record |
| --- | --- | --- |
| Accepted step 3 | Local commit `4680c6aa7c1bb889e1cd51b9a452e3d7137f967f` (`Refactor FFT pipeline behind zero-overhead protocols`) | Preserved execution-path reference and step-3 side of the accepted paired comparison. |
| Accepted step 4 / step-5 baseline | Local commit `21a13d938be7178799302d35976a1c5c7d4c1065` (`Compile FFT configuration into atomic runtime plans`) | Actual accepted step-4 source and the baseline from which the threaded step-5 candidate is being developed. |
| Pi repository metadata | Pi checkout pointer `7be139c`, with intentional modified and untracked rejected-refactor/deployment artifacts | Operational checkout only; **not** the accepted step-4 source identity or a measured-version label. |
| Exact accepted step-3 comparison files | `/home/stelliosbonadurer/rgb_Matrix.step4-predeploy-20261119T014040Z.tar.gz`, SHA-256 `6aa8802ec607274a47592604b9b1e1b65d7eb375ffcbf5ff3bf0755c091e93d4` | Exact files used for the accepted step-3 side of the paired step-4 comparison. This is not a step-5 rollback point. |
| Step-5 threaded candidate | Local commit `5e018a4a3ec36ea81fb985153b2897fd9a3d184a` (`Decouple FFT analysis with latest-value transport`) | Exact locally verified threaded implementation and the source of the production changes used by the qualification variant below. |
| Exact pre-step-5 Pi baseline | `/home/stelliosbonadurer/rgb_Matrix.step5-predeploy-20261120T002147Z.tar.gz`, SHA-256 `1f419182b714294d28630e0fc8570c020c85705cc84c656d10bbec996852946e` | Exact accepted step-4 production/test files used for the paired baseline and final restoration. It preserves the Pi-only rollout/metrics integration layered over accepted step 4. |
| Measured step-5 qualification variant | Step-5 commit changes narrowly merged into the exact deployed baseline; qualification archive SHA-256 `fcfb853ab94a2a6cc9bad22709f2074959639b36c671331b9fd9e64f79460084` | Four runtime files exactly match `5e018a4`; `settings.py` differs only by the Pi's corrected 512-sample timing comment; `main.py` retains the Pi-only service route and adds opt-in qualification metrics. Exact hashes are recorded below. |

The accepted step-4 baseline file identities remain recorded in
[`02-compile-configuration-off-hot-path.md`](02-compile-configuration-off-hot-path.md).
The step-5 hashes below identify the locally verified files in implementation commit
`5e018a4a3ec36ea81fb985153b2897fd9a3d184a`.

Reviewed step-5 implementation file identities:

| File | SHA-256 |
| --- | --- |
| `my_programs/fft_program/main.py` | `f44957a535d5db1bb1c010c53171d52f2f745965f541b4e6de4d2e35e9784314` |
| `my_programs/fft_program/config/settings.py` | `12233242f67345abfe1e72d55194dd65987b456301d5e0f55cccd65f4dafb3c6` |
| `my_programs/fft_program/core/audio.py` | `250edf5d8dbcf1a8313bceb7c231011b71336de788cc14f8c8d23fbcfa37bec9` |
| `my_programs/fft_program/core/interfaces.py` | `953206f3c89d178456953fb39d7e738d93a48ce6d04b5e4dc9471e171e082823` |
| `my_programs/fft_program/core/analysis_plan.py` | `9c2580b02736111e130984b82a6558e355f33e93cb3d80fb6b77cadb053bc882` |
| `my_programs/fft_program/core/scaling.py` | `b144e5cc0db3408e34df80b07cde3afcee1d2bf2f950efd75e80f018a23c0eac` |
| `my_programs/fft_program/runtime_control.py` | `6f410139d3c0e58b0a0158aa985e1c7e03fd9e9be908925ecd20f05a05bf6fdd` |
| `my_programs/fft_program/visualizers/bars_unified.py` | `a85ad22546dd46c17d1db29c79fa47a13f4abbeb83e6efc45f81116149b0cdc7` |
| `tests/test_fft_analysis_plan.py` | `33a26764926a96a69dd292c2d0d585454e7a24be9c561014ccbed7fbde9fd5fa` |
| `tests/test_fft_pipeline_interfaces.py` | `bf5be162cbe5d02d7e6b64ee3d3561bb661022e68a00d8e9bdee8764238d4b18` |
| `tests/test_fft_runtime_control.py` | `5ab94045006ed711586311a9ee3379e1b91e3d18c019d0835c02b27ca514930e` |

Measured qualification deployment identities:

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

Independent review confirmed that the measured merge retained the direct-canvas call
order, removed layered execution, used the exact acquired-slot timestamp for result
age, and introduced no `SetImage` or output-path change. The merged candidate passed
47 focused tests on the Pi; the restored step-4 baseline passed its 29 focused tests.

The progress record itself is intentionally identified by the eventual commit rather
than a self-referential hash. `analysis_plan.py`, `scaling.py`, and
`test_fft_analysis_plan.py` are unchanged accepted dependencies included so a future
deployment/comparison can verify the complete active standard path.

## Implementation map

| File | Step-5 role |
| --- | --- |
| `core/audio.py` | Owns callback generation publication, the one analysis worker, two fixed result slots, epochs, counters, failure propagation, and bounded lifecycle. |
| `core/interfaces.py` | Adds explicit result-lease release and removes the deleted layer contract. |
| `main.py` | Keeps scaling and continuous direct-canvas drawing on the render thread, releases result leases in `finally`, and removes the legacy layer branch. |
| `runtime_control.py` | Publishes coherent plan/noise worker state only after successful Apply and removes obsolete layer schema/state/actions. |
| `config/settings.py` | Removes the obsolete layered configuration model; older unknown JSON groups remain harmlessly ignored. |
| `visualizers/bars_unified.py` | Retains the standard direct-canvas and overflow-band renderer while deleting special-case multi-layer drawing. |
| `tests/test_fft_pipeline_interfaces.py` | Covers transport, callback, real worker coalescing, exact math, epochs, lifecycle/failure, protocol, draw order, and pacing. |
| `tests/test_fft_runtime_control.py` | Covers Apply compilation/activation, noise-only worker-state replacement, failed Apply atomicity, and deliberate layer-path rejection. |
| Current architecture/user docs | Remove instructions for the deleted feature and distinguish overflow color bands from future independent standard views. |

## Preserved accepted path

The accepted standard path at the start of step 5 is:

```text
PortAudio callback
  -> copy and replace the latest NumPy sample array

single main/render loop
  -> snapshot one immutable compiled analysis plan
  -> current 8192-point FFT and 64-bin projection
  -> current scaling, smoothing, and peaks
  -> direct native-canvas drawing
  -> peak overlay
  -> SwapOnVSync
  -> existing 5 ms sleep
```

The qualification configuration remains:

- 64×64 matrix;
- 44,100 Hz input and 512-sample capture blocks;
- 8192-point FFT and 64 output bins;
- `ocean` theme with overflow enabled;
- gradient, peaks, and shadows disabled;
- direct native-canvas output; and
- 5 ms post-frame sleep.

The standard external runtime-control paths remain unchanged. The deleted legacy
layer paths are intentionally absent from the schema and reject as `invalid_path`.

## Baseline audit findings

Read-only inspection of accepted step 4 established these implementation facts
before editing:

- `AudioProcessor._audio_callback()` copies the selected channel to a new NumPy
  array, replaces `latest_samples`, sets `have_data`, and currently prints callback
  status synchronously when status is present.
- `AudioProcessor.get_fft_magnitudes()` currently performs windowing, the 8192-point
  `rfft`, 64-bin projection, weighting, noise subtraction, and result allocation on
  the render thread every time it is called. It has no sample generation, so the
  same 512-sample block may be analyzed repeatedly.
- The accepted baseline still contained a separate synchronous legacy layered
  computation and second FFT. That observation motivated the later explicit decision
  to remove the obsolete feature instead of adapting it to the new worker.
- The standard analysis hot path snapshots `active_plan` once. The plan owns the
  immutable window, bin-index arrays, weights, and FFT size; noise floor remains a
  live scalar outside the plan.
- The render loop calls standard analysis, optionally calls layered analysis,
  processes standard attack/decay and peaks through `ScalingProcessor`, draws
  directly to the native canvas, optionally overlays peaks, swaps on VSync, and
  sleeps for the configured delay.
- When no analysis data exists, the render loop performs its existing 1 ms yield.
  Once data exists, rendering is cadence-driven rather than analysis-event-driven.
- Successful zoom/weight Apply currently compiles a complete candidate before
  mutation and activates one plan at the frame boundary. Invalid compilation leaves
  the accepted plan and live state unchanged. Noise-floor changes currently mutate
  only their live scalar.
- Capture lifecycle is context-managed by `AudioProcessor`: setup and start occur on
  entry, and stream stop/close occur on exit. There is not yet an analysis-worker
  lifecycle or result transport.

These are audit observations, not step-5 verification results.

## Approved checkpoint-A design

The following choices were approved one at a time before implementation.

### Execution boundary

- Decouple only the standard FFT and 64-bin projection in checkpoint A.
- The analysis worker publishes the existing weighted, noise-subtracted 64-bin
  magnitude result. Scaling, attack/decay, smoothing, peaks, themes, drawing, VSync,
  and pacing remain on the render thread.
- The render loop continues at its existing cadence and reuses the latest complete
  result when no newer analysis sequence exists. It never waits for the analyzer.
  This is required because attack/decay behavior advances on render cadence rather
  than merely on new FFT input.

The initial approved choice was to keep the old layered path synchronous. Independent
review then found that carrying it beside the new standard worker created an awkward
second execution model. The user first proposed disabling it, then explicitly approved
deleting it and clarified that the eventual replacement will be multiple instances of
this standard drawing path. The deletion removes its configuration model, analyzer and
renderer branches, runtime controls, persistence state, key bindings, and tests. It
does not add the future multi-instance feature.

The pre-existing, registered-but-unusable `bars_dual.py` visualizer is a separate dead
artifact: it reads a nonexistent `settings.dual` and is not assembled by `main.py`.
It remains explicitly outside this layered-feature deletion and is still recorded in
`docs_agents/known-issues.md`.

### Callback-to-analyzer publication

- Retain latest-sample replacement; do not add a sample ring, history, task object,
  or queue.
- Add one monotonically increasing generation so the worker can distinguish a newly
  published 512-sample block from one already analyzed.
- Publish and snapshot `(latest_samples, generation)` coherently under a dedicated,
  narrowly held sample-publication lock. The NumPy copy and FFT remain outside the
  lock.
- Use one fixed `threading.Event` only to wake the analyzer when the callback has
  published new audio. The event is not a job counter and cannot accumulate work.
  If callbacks arrive during analysis, the worker skips to the latest generation.
- The render loop neither reads nor waits on this event. Shutdown sets it only to
  wake a sleeping analyzer for prompt exit.
- Remove callback-side printing. Callback status increments only a fixed scalar
  counter outside any logging path.

The intended interaction is:

```text
PortAudio callback
  copy newest channel block
  publish samples + generation
  set analyzer wake event
            |
            v
analysis worker
  wake and snapshot newest generation
  skip a generation already analyzed
  compute one standard FFT/projection
  publish the latest complete 64-bin result

render loop (independent and continuous)
  lease latest complete result
  run scaling/attack/decay
  release lease
  draw -> peak overlay -> VSync -> 5 ms sleep
  repeat even if the analysis sequence did not change
```

### Fixed two-slot result transport

- Keep a private implementation class in `core/audio.py`; do not introduce a public
  subsystem, registry, generic graph, or message model.
- Preallocate exactly two contiguous `float32[64]` NumPy payload slots. Their array
  identities remain stable after startup.
- The worker claims an inactive writable slot under one transport-private lock,
  releases the lock, writes all 64 values, and only then briefly reacquires the lock
  to publish the complete slot, analysis sequence, epoch, and timestamp.
- The renderer briefly locks only to lease the newest complete slot and later to
  release it. It reads the NumPy array directly and releases the lease immediately
  after `scaler.process()` in a `finally` block.
- The worker never rewrites a published or leased slot. If neither slot is writable,
  it drops the new result rather than waiting, allocating, or creating backlog.
- No lock is held during callback copying, FFT, 64-bin filling, scaling, drawing,
  VSync, or sleeping.
- `get_fft_magnitudes()` continues to return only the NumPy array. Add an explicit
  `release_fft_magnitudes()` method rather than returning a tuple, dataclass, context
  object, dictionary, or message per frame.
- Add an internal analyze-into-destination path. It fills the claimed slot directly
  and applies noise subtraction in place, avoiding a fresh 64-bin result allocation
  and transport copy. Existing FFT temporaries are not broadened by this decision.

The slot lifecycle is:

```text
writable -> writing -> published -> leased -> writable
```

The sequence identifies new analysis; a consumer may skip stale sequences and read
only the newest complete one. The transport is fixed-size and lossy by design.

### Atomic analysis state and Apply

- The worker snapshots one immutable control-plane state containing the complete
  `StandardAnalysisPlan`, noise floor, and an analysis epoch before beginning FFT.
- Successful standard-plan or noise-floor changes create and publish a new worker
  state. Zoom/weight changes retain step 4's compile-before-mutation behavior;
  noise-only changes do not compile a plan.
- Theme, gradient, overflow, scaling, smoothing, peaks, and other
  non-analysis controls do not advance the epoch or touch the transport.
- A result is tagged with its snapshotted epoch. If Apply changes the epoch during
  the FFT, the worker discards that old-epoch result rather than publishing it. The
  last result that completed before Apply remains eligible only as a stale display
  value until the current epoch publishes its replacement.
- Retaining that already-complete frame keeps scaling, attack/decay, drawing, VSync,
  and pacing continuous across Apply. It does not permit an in-flight old computation
  to publish and does not expose partial or mixed plan arrays.
- Failed Apply changes neither the worker state nor the previously published frame.
  It cannot expose mixed old/new plan arrays or noise-floor state.
- Worker-state objects are created only at startup or relevant successful Apply;
  there is no per-frame configuration object, mapping, validation, or registry
  lookup.

### Lifecycle and failure

- `AudioProcessor` owns the one analysis worker and its fixed transport within the
  existing context-managed application lifecycle.
- Startup launches the analysis worker before PortAudio. If PortAudio startup fails,
  startup signals and joins the worker before propagating failure.
- Shutdown first stops and closes PortAudio, preventing new callbacks, then signals
  and joins the analysis worker with an approved 1.0-second bound.
- A worker exception is captured once and terminates the worker. The next render-side
  result access raises a clear runtime error so the existing outer cleanup path can
  stop capture, join the worker, save state, and clear the matrix. There is no
  automatic in-process restart and no indefinite rendering of a frozen frame; the
  surrounding service may restart the failed application.
- If the analysis worker remains alive after the one-second join, shutdown raises
  rather than silently abandoning a live thread.

### Minimum measurement state

Keep only fixed scalar state sufficient to evaluate the thread experiment:

- callback generation;
- callback-status count;
- last analyzed generation;
- skipped sample generations;
- published analysis sequence;
- transport-drop count; and
- worker-failure count.

Each slot stores one `monotonic_ns` publication timestamp. Result age is calculated
by opt-in qualification code outside the transport. The implementation does not keep
a timing history, percentile arrays, per-frame metrics objects, or an accumulating
event stream.

## Thread-failure gate and process boundary

The threaded candidate must be tested before a process is designed. In an approved
five-minute standard-configuration run, it must demonstrate:

- zero callback-status errors;
- analysis of at least 95% of published sample generations; and
- p95 result age no greater than two 512-sample capture blocks, approximately
  46.4 ms at 44,100 Hz.

A process/shared-memory experiment is justified only if the threaded candidate
misses at least one of these gates reproducibly in two same-session runs while the
accepted step-4 comparison remains healthy. A close render-throughput difference by
itself is not sufficient. Existing content FPS, render-to-swap latency, CPU, and RSS
remain separate acceptance evidence.

## Required hardware-free verification

The implementation is expected to add focused tests proving:

- callback work remains bounded and performs no FFT or transport allocation;
- the worker does not repeatedly analyze an identical sample generation;
- both result slots and their payload arrays retain stable identities;
- publication exposes only a complete 64-bin frame;
- the consumer reads the newest complete sequence and may skip stale sequences;
- neither the sample side nor result side can form a queue or unbounded backlog;
- FFT/projection output exactly matches the accepted NumPy calculation;
- the standard render path receives the same arrays and draw arguments;
- direct-canvas draw order, peak overlay, VSync, and 5 ms pacing remain unchanged;
- successful Apply cannot expose mixed old/new worker projection state;
- failed Apply leaves worker state and the published frame unchanged;
- worker startup, PortAudio-start failure, worker failure, shutdown, and join timeout
  are bounded and testable;
- the removed legacy layer API is absent and its former runtime paths reject without
  mutation; and
- no configuration model, mapping, tuple, message, or registry lookup is allocated
  per frame.

### Local results

Using the NumPy-capable project interpreter
`/Users/stelliosbonadurer/Documents/oldUMcoursework/CSCI477simulation/workspace/.venv/bin/python`:

- `python -m unittest discover -s tests -p 'test_fft*.py'`: **46 tests run, 45
  passed, 1 skipped, 0 failures**. The skip is the existing platform-dependent Unix
  socket test.
- In-memory `compile()` of nine relevant Python implementation/test files:
  **passed**.
- `git diff --check`: **passed**.

These results cover the fixed slot identities, complete publication, newest-value
consumption, event coalescing, exact accepted FFT/projection math, Apply epochs,
worker-start/PortAudio-start/failure/shutdown bounds, standard and overflow-band
direct drawing, draw order/arguments, 5 ms pacing, and removal of the legacy layer
surface. They are hardware-free functional evidence only.

### Staged Raspberry Pi compatibility verification — 2026-11-19

The user approved a staging-only Pi test of exact step-5 commit
`5e018a4a3ec36ea81fb985153b2897fd9a3d184a`. This was not a production deployment,
visualization run, audio-input run, benchmark, service restart, or performance
qualification.

Read-only preflight reconfirmed:

- hostname `raspberrypi` through the strict `raspberrypi.local` host-key alias;
- ED25519 fingerprint
  `SHA256:vcXQ5NGdLjeu/JBS3Dmb+VUTAYhvJpDc8IlFjPt9mwo`;
- remote Git pointer `7be139c` and the intentional modified/untracked artifacts;
- active web service with no systemd drop-in override; and
- production `system_info` visualization still running.

The deployed `config/settings.py` and `main.py` hashes differed from the clean
step-4 commit while the other six inspected source/test hashes matched it. Those
differences were treated as remote-only state and were not overwritten. Instead,
the exact candidate FFT tree, focused tests, and their exact-commit
`matrix_controller` dependency were extracted under one temporary `/tmp` directory.
The primary candidate archive SHA-256 was
`3d600e7f5fac866ae23a039e03d5f59ceb3b26ebbb45f14ffe31f694bb27a56a`.

The first two discovery attempts intentionally exposed incomplete staging bundles:
`test_fft_runtime_control` could not import `matrix_controller`, then the narrow
package subset lacked `matrix_controller.registry`. These were staging-dependency
errors, not candidate failures. After staging the complete exact-commit controller
package, the Pi FFT virtual environment reported:

- `python -B -m unittest discover -s tests -p 'test_fft*.py'`: **46 tests passed in
  0.185 seconds**;
- in-memory `compile()` of all **38 staged Python files**: **passed**; and
- bytecode-file check: **no `__pycache__` directory created**.

Post-test verification found the service healthy, `system_info` still active,
throttle status `0x0`, no recent runtime/audio errors, Git still at `7be139c`, and
all eight production hashes unchanged from preflight. The temporary Pi directory and
local transfer archives were then removed. This establishes Pi interpreter/platform
compatibility only; the separately approved performance qualification is recorded
below.

## Performance baseline and comparison discipline

The strongest accepted step-4 evidence is the paired five-minute run:

| Metric | Accepted step 4 (`21a13d938…`) | Accepted step 3 (`4680c6aa…`) |
| --- | ---: | ---: |
| Content FPS | 76.59 | 77.40 |
| Render-to-swap p50 | 4.67 ms | 4.86 ms |
| Render-to-swap p95 | 14.17 ms | 14.12 ms |
| Steady CPU mean | 78.83% | 81.43% |
| Steady RSS | 56,976–58,896 KiB | 59,392–60,416 KiB |

The 1.0% FPS difference was accepted because step 4 had faster median latency,
essentially equal tail latency, and lower CPU/RSS. Both versions shared the same
lower absolute throughput in that Pi/audio session, so the absolute change was not
attributed to code.

Any future step-5 comparison must document whether music or another repeatable input
was present, compare exact committed versions in the same Pi/audio session, prefer
paired five-minute runs, and alternate order or repeat runs when a close result makes
order bias material. Before every hardware measurement, obtain approval stating the
exact versions/configurations, input and run conditions, duration, and decision the
measurement will inform.

## Paired five-minute step-5 qualification — 2026-11-19 local session

The user approved a candidate-first paired comparison against the exact deployed
step-4 baseline. Continuous representative music played at fixed volume through the
iMM-6C for both windows. Both used the accepted 64x64, 44,100 Hz, 512-sample,
8192-point FFT, 64-bin, `ocean`, overflow-enabled, gradient/peaks/shadows-disabled,
direct-canvas, 5 ms configuration. Each run produced 60 external samples at five-
second intervals; the startup sample is excluded from the 59-sample steady-state
CPU/RSS/temperature statistics.

The first paired attempt produced valid internal timing/analyzer artifacts, but its
external observer followed the supervisor's idle `sudo` wrapper rather than the FFT
Python child. Those external CPU/RSS samples are invalid and were not used. The
mistake was preserved rather than hidden. The observer was changed to resolve and
verify the descendant command containing `my_programs/fft_program/main.py`, passed a
two-second smoke check, and the complete pair was repeated. The corrected pair below
is the acceptance evidence. The first attempt's internal results independently showed
104.05 FPS for step 5 and 78.85 FPS for step 4, consistent with the corrected result
and the known sensitivity of absolute baseline throughput to physical input/session
conditions.

### Corrected paired result

| Metric | Step 5 threaded | Exact step-4 baseline | Assessment |
| --- | ---: | ---: | --- |
| Frames published | 32,087 | 21,843 | step 5 published 46.9% more frames |
| Metrics elapsed | 307.657 s | 307.372 s | equivalent |
| Content FPS | **104.29** | **71.06** | step 5 46.8% higher |
| Render-to-swap p50 | **4.39 ms** | **4.77 ms** | step 5 8.0% faster |
| Render-to-swap p95 | **4.69 ms** | **14.28 ms** | step 5 67.1% faster |
| Render-to-swap p99 | **4.77 ms** | **14.53 ms** | step 5 67.1% faster |
| Publish interval p50 | **9.54 ms** | **9.84 ms** | step 5 faster |
| Publish interval p95 | **9.78 ms** | **19.35 ms** | step 5 removed the baseline's frequent missed cadence |
| Publish interval p99 | **9.88 ms** | **19.62 ms** | step 5 removed the baseline's frequent missed cadence |
| Steady CPU mean | **86.66%** | **78.12%** | step 5 uses 8.54 more points, or 10.9% more CPU |
| Steady CPU range | 86.5-88.5% | 75.2-80.1% | both below one full core on average |
| Steady RSS range | 56,916-61,140 KiB | 60,164-60,804 KiB | overlapping; do not claim a material memory win |
| Python thread count | 7 | 6 | expected one analysis worker |
| Temperature range | 48.2-51.6 C | 49.1-52.1 C | both healthy; order dependent |
| Throttle status | `0x0` throughout | `0x0` throughout | equal |
| Recent runtime/audio errors | none | none | equal |

The opt-in qualification hook accumulates frame-duration samples and therefore grows
with frame count; step 5 accumulated roughly 47% more entries. The RSS ranges are
reported transparently but are not clean evidence of steady production allocation.

### Thread-service gates

| Gate/evidence | Step-5 result | Decision |
| --- | ---: | --- |
| Callback sample generations | 26,534 | context |
| Complete published analyses | 26,014 | context |
| Analyzed/published fraction | **98.04%** | passes >=95% gate |
| Skipped generations | 520 | bounded latest-value loss, 1.96% |
| Callback-status errors | **0** | passes |
| Worker failures | **0** | passes |
| Transport drops | 7 | bounded; no backlog |
| Result age p50 | 2.56 ms | healthy |
| Result age p95 | **18.49 ms** | passes |
| Result age p99 | 20.60 ms | below two capture blocks |
| Result age maximum | 21.79 ms | below two capture blocks |

The earlier planning text called two 512-sample blocks "approximately 46.4 ms." At
44,100 Hz, one block is 11.61 ms and two are **23.22 ms**; 46.4 ms is four blocks.
The acquired-slot p95, p99, and maximum all pass the stricter correct two-block
threshold, so this correction does not change the decision.

### Acceptance decision and retained artifacts

The threaded transport passes every service gate on its first valid long run and
reproduced essentially identical candidate results in the corrected repeat. It also
materially improves content throughput and tail latency for an 8.54-point CPU cost.
The evidence supports accepting checkpoint A as the step-5 direction; final user
acceptance remains a separate decision. A process/shared-memory design is **not
justified** by this evidence.

Corrected raw artifacts retained on the Pi:

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

The original attempt's artifacts are also retained with their non-`rerun` names;
their internal JSON is valid, but their external sample files must not be used for
CPU/RSS conclusions.

## Deployment safety boundary

The accepted step-4 production files are currently restored on the Pi, but its Git
pointer remains `7be139c` with intentional modified and untracked artifacts. The
qualification followed this safety sequence, which remains required for any future
deployment:

1. inspect remote status and hashes again;
2. verify the locally committed step-4 source identity;
3. create a new targeted, timestamped archive of the current accepted step-4 files;
4. preserve all remote-only changes;
5. upload or merge only explicitly approved files;
6. verify local/remote hashes before execution;
7. use only temporary `/run` service overrides for qualification; and
8. remove overrides and restore `system_info` afterward.

The new targeted step-5 recovery archive, not the older pre-step-4 archive, was used
to restore the exact accepted baseline between runs and at completion. After the
comparison, all eight overlapping production/test hashes matched preflight, the
temporary `/run` override was absent, the web service was active, `system_info` was
running, throttle status was `0x0`, and recent runtime/audio errors were absent. All
remote-only rejected-refactor/deployment artifacts remained preserved.

## Rejected alternatives at this checkpoint

- A process and shared-memory transport before threaded evidence.
- Adapting or optimizing the obsolete layered analysis path. It was deleted after
  explicit approval; future multiple standard instances remain separate work.
- Moving scaling, smoothing, attack/decay, or peaks to the analysis worker.
- Rendering only when a new analysis sequence arrives.
- A queue carrying every callback or analysis result.
- A PCM ring, sample history, per-sample timestamps, or per-sample metadata.
- Publishing all 4,097 FFT coefficients instead of the required 64 bins.
- A lock held across FFT, slot filling, scaling, drawing, VSync, or sleeping.
- Busy polling for new samples.
- Per-frame dataclasses, mappings, tuples, messages, validation, or configuration
  construction.
- Automatic worker restart, silent worker abandonment, or indefinite stale-frame
  rendering after failure.
- Output-path changes, generic composition, unnecessary telemetry, advanced
  analyzers, or future multi-instance optimization.

## Current checkpoint

The threaded implementation, local verification, Pi compatibility tests, independent
diff/hot-path review, targeted recovery procedure, deployed tests, and paired hardware
qualification are complete. The exact implementation identity is
`5e018a4a3ec36ea81fb985153b2897fd9a3d184a`; the exact measured Pi integration variant
and hashes are recorded above. The thread passes its callback/analyzer service gates
and materially improves throughput and tail latency, so the same contract should not
be moved to a process at this checkpoint.

The Pi was deliberately returned to accepted step 4 rather than silently promoting
the candidate. A separate acceptance/deployment decision can choose when to make step
5 the production state. Future work must not treat the qualification-only metrics
hook as product telemetry.
