# Step-6 Pi qualification plan

> **State:** execution complete. Four valid five-minute windows reject Pillow under
> the approved material-speed gate. The temporary override is removed; step 6 remains
> deployed in direct-default interim operation by explicit user direction.

## Decision this run informs

Whether the one-image Pillow output candidate materially outperforms the accepted
direct native-canvas path under the complete production-like FFT workload. A test
approval will not authorize production promotion.

## Source identities

- Accepted comparison/source baseline: qualified step 5
  `5e018a4a3ec36ea81fb985153b2897fd9a3d184a`.
- Committed step-6 candidate: `2616c56bddce72b0c08326ae9d3058ce68210954`.
- Documentation/protocol commit: `f9822508f2057a51e80eb189b62df829cf17724c`.
- Predeployment Pi checkout pointer:
  `7be139cde2fd4d36635a863855588705e786d0b8`; the checkout was intentionally dirty
  with existing controller and visualization work and was operationally on accepted
  step 4. The pointer is metadata only, not the measured identity.
- Measured tree: the nine exact installed runtime/qualification hashes below. The
  temporary instrumentation remained uncommitted and opt-in; staged and installed
  hashes matched before execution.

## Exact runtime file set

The Pi is recorded at step 4, so a coherent step-6 experiment requires every runtime
file changed from step 4 through the committed candidate:

1. `my_programs/fft_program/config/settings.py`
2. `my_programs/fft_program/core/__init__.py`
3. `my_programs/fft_program/core/audio.py`
4. `my_programs/fft_program/core/interfaces.py`
5. `my_programs/fft_program/core/output.py`
6. `my_programs/fft_program/main.py`
7. `my_programs/fft_program/runtime_control.py`
8. `my_programs/fft_program/visualizers/bars_unified.py`
9. temporary `my_programs/fft_program/qualification_metrics.py`

Only `main.py` and `core/audio.py` receive qualification wiring; the ninth file owns
fixed-capacity samples and artifact output. Local-only
`tests/test_fft_step6_qualification.py` verifies the recorder and need not enter the
production source tree. No documentation or ignored driver file is deployed.

Final local runtime/qualification SHA-256 values:

| File | SHA-256 |
| --- | --- |
| `config/settings.py` | `12233242f67345abfe1e72d55194dd65987b456301d5e0f55cccd65f4dafb3c6` |
| `core/__init__.py` | `6d5f10250b9d1d430ae86199aa232a9b7ae9d71c171c69e1d20ca0db2a686fdd` |
| `core/audio.py` (temporary merged) | `bd4795942c4465ef9c3e2602cd90fc6432f47aa194c4ba08cc1d9dd37141abd6` |
| `core/interfaces.py` | `953206f3c89d178456953fb39d7e738d93a48ce6d04b5e4dc9471e171e082823` |
| `core/output.py` | `2cba7097378e90bcf168b94b60329f7e2e14b63fb09aafa5bce7e21477ebd07f` |
| `main.py` (temporary merged) | `906907f5d5af72aa38311c4fff134b8ee90eb7f7b15cd5d21ca51f25ce254b20` |
| `runtime_control.py` | `b7761fe48043bdfe01a0441bce67cb6e640d3e9bfe52c2c561520d4b32941635` |
| `visualizers/bars_unified.py` | `a85ad22546dd46c17d1db29c79fa47a13f4abbeb83e6efc45f81116149b0cdc7` |
| temporary `qualification_metrics.py` | `320ae9f4a8c62c77d7f72a63f6dbad87ba27e8d1f2cc9339a86d9b9119889649` |

Local-only recorder test SHA-256:
`9003bf98c7e8741676676d0b6c1935c2978f7a61a0b9361bf45e2d2799ebd000`.

Prepared local transfer archives:

| Archive | Contents | SHA-256 |
| --- | --- | --- |
| `/tmp/rgb-matrix-fft-step6-qualification-runtime.tar.gz` | the nine runtime files listed above | `d61d4a9644232704212b23c7213e5b97f763ee919a010696322e7e2f6eea0e45` |
| `/tmp/rgb-matrix-fft-step6-qualification-tests.tar.gz` | four focused test modules for temporary staging | `95f21ef42f2d63acf7a180c48134aa44399fba1523179bf70b7b6371c75f7dfa` |

## Preflight and compatibility checks

After explicit hardware approval and before mutation, execution followed this
sequence:

1. Inspect remote `git status`, service state, active child, exact runtime-file
   hashes, free space, recent journal errors, temperature, and throttle state.
2. Resolve the installed `rgbmatrix` module and hash its binding artifact/source when
   available. Record Python and Pillow versions and confirm the actual Pillow image
   exposes the private storage expected by the binding.
3. Create a targeted timestamped recovery archive of every existing destination file
   and service override that will change. Preserve unrelated modified/untracked files.
4. Stage the candidate in a temporary directory, verify local/remote SHA-256 values,
   and run the focused hardware-free tests with the Pi interpreter.
5. Stop the active visualization only when required, install only the listed files,
   and perform one bounded 64x64 `RGB` `SetImage`/clear compatibility smoke test.
   Any ABI, pixel, service, or cleanup failure stops the experiment and triggers
   restoration.

## Fixed workload and configuration

- Same Pi, 64x64 HUB75 panel, mapping, slowdown, brightness, and service environment.
- iMM-6C input at 44,100 Hz; 512-sample capture block; 8192-point FFT; 64 bins.
- Ocean theme, overflow enabled; gradient, peaks, and shadows disabled.
- Five-millisecond post-frame sleep remains unchanged.
- Continuous representative live music from one unchanged source/playlist at fixed
  source and input volume for all four runs.
- Runtime-state reads/writes are disabled only while qualification metrics are active,
  matching prior qualification practice and preventing periodic file-I/O noise.

## Run order and duration

Run four fresh child processes in this exact counterbalanced order:

1. `direct-1`
2. `pillow-1`
3. `pillow-2`
4. `direct-2`

Each child settles for 60 seconds, measures exactly five minutes, writes unique
fail-closed JSON/NPZ artifacts, clears the panel, and exits. Keep music continuous
through restarts. Do not reuse artifact paths. The external observer samples every
five seconds, resolves the descendant FFT Python process rather than its `sudo`
wrapper, and verifies the command line before accepting CPU/RSS/thread evidence.

## Exact internal evidence

Preallocated `uint64` arrays retain at most 100,000 frames (333 FPS for five minutes):

- renderer duration;
- VSync/swap duration;
- complete draw-through-swap duration;
- swap-completion timestamps;
- exact acquired-result age at display;
- acquired analysis sequence; and
- sparse visible-pixel workload samples every 256 frames.

Each summary includes swap/display FPS, unique-content FPS, published-content FPS,
p50/p95/p99/max stage latencies, callback/analyzer/transport deltas, analyzed/published
fraction, workload density, capacity status, source identity, output mode, and run
label. CPU, RSS, threads, temperature, throttling, and journal errors are external
context. Raw samples remain available for pooled and pairwise reanalysis.

## Stop/go gate

Pillow wins only if all of the following hold:

- both `direct-1 -> pillow-1` and `pillow-2 -> direct-2` pairs favor Pillow on swap
  FPS and complete draw-through-swap p50/p95;
- pooled Pillow swap FPS, draw-through-swap p50, and p95 each improve by at least 10%;
- unique-content FPS regresses by no more than 5%;
- analyzed/published fraction is at least 95%;
- result-age p95 remains below two 512-sample blocks at 44.1 kHz (23.22 ms);
- callback-status and worker-failure counts remain zero, sample capacity is not
  exhausted, and no runtime/audio errors or throttling occur; and
- the panel shows equivalent ocean/overflow output with no new visible artifact.

CPU and RSS are reported but are not rejection gates unless load causes thermal,
throttling, or service-quality failure. Mixed, sub-threshold, or invalid evidence
retains direct rendering as the accepted path.

## Rollback and restoration

On any preflight or run failure, stop the experiment, preserve partial/invalid
artifacts with an explicit invalid label, restore the targeted archive, remove only
the temporary override and metrics module, and verify original hashes before restart.

After four valid runs, remove the temporary qualification override so it cannot rerun,
but do not restore step 4. Leave the deployed step-6 runtime files in normal
direct-default operation while results are reviewed. Verify service activity, child
command, panel clearing/startup, control endpoint behavior, recent journal errors,
and installed hashes. This is an explicitly approved interim state, not promotion.
If preflight, compatibility, deployment, or measurement fails before valid evidence
is collected, targeted rollback remains authorized for safety.

## Execution record

Read-only preflight found the Pi at checkout pointer
`7be139cde2fd4d36635a863855588705e786d0b8`, with the expected dirty operational
tree, active controller service, `system_info` child, 44.3°C temperature, and
`throttled=0x0`. The targeted FFT files matched restored step 4 rather than the local
step-6 bundle, so the complete nine-file set above was required. Unrelated Pi files
were preserved.

Target environment:

- Python 3.11.2;
- Pillow 9.4.0;
- installed `rgbmatrix` binding binary SHA-256
  `a838e482f0dcdccbd5c54300b34eae1a17074eb6ec328968bc01946cfa23c481`;
- binding `core.pyx` SHA-256
  `e8294ee5c24a9b69ab18bc91fc705d5089b0cec2b90e0d306301d0dbdea64bee`;
- Pillow `unsafe_ptrs` is a tuple of key/value pairs; the binding correctly wraps it
  with `dict(...)` before accessing `image32`.

Fresh predeployment archive:

```text
/home/stelliosbonadurer/rgb_Matrix.step6-predeploy-20261120T022848Z.tar.gz
SHA-256 7c887c9a88c9b5ca40bc13fb86f05244f876c8e694816ef58ac04875748a40b5
```

Remote archive hashes matched the prepared local hashes. An isolated Pi staging copy
passed the 56 controlling output/runtime/pipeline/recorder tests. The broader Pi
discovery also exposed one unrelated older entrypoint expectation; it was not part of
the local controlling suite or deployed candidate.

The first two smoke launches exited before rendering because artifact paths were not
traversable by the post-matrix `daemon` identity. A third setup attempt confirmed
that systemd recreates `RuntimeDirectory` at restart. The final procedure created a
daemon-owned results subdirectory after restart; no candidate source changed. The
two-second real Pillow smoke then completed 202 frames, wrote valid JSON/NPZ, cleared
the panel, and exited zero.

## Qualification results

| Run | Frames | Swap FPS | Total p50 ms | Total p95 ms | Unique FPS | Analysis fraction |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `direct-1` | 31,313 | 104.377 | 4.053 | 4.388 | 60.677 | 98.31% |
| `pillow-1` | 30,429 | 101.428 | 3.968 | 4.372 | 59.566 | 97.79% |
| `pillow-2` | 30,716 | 102.385 | 4.007 | 4.366 | 59.543 | 97.33% |
| `direct-2` | 31,277 | 104.256 | 4.076 | 4.369 | 60.286 | 97.89% |

Pooled Pillow changes relative to direct:

- swap FPS: **-2.31%**;
- draw-through-swap p50: **+1.70%**;
- draw-through-swap p95: **+0.27%**;
- renderer p50/p95: **-30.66% / -30.69%**;
- swap-only p50/p95: **+21.28% / +16.91%**;
- unique-content FPS: **-1.53%**; and
- published-content FPS: **-0.55%**.

Neither directional pair favored Pillow on all three primary metrics because Pillow
FPS was 2.82% lower in the first pair and 1.79% lower in the reversed pair. The
10% pooled gates all failed. The unique-content, analysis-fraction, result-age,
capacity, callback, worker, service, and thermal gates passed. CPU increased from
about 87% for direct to 92% for Pillow but remained informational as approved.

Pillow is therefore rejected as a speed optimization. Direct remains accepted.

## Evidence and final Pi state

Persistent evidence is stored at:

```text
/home/stelliosbonadurer/fft-step6-qualification-20261120/
```

It contains the smoke JSON/NPZ, four run JSON/NPZ pairs, four external TSVs, four
journal slices, and `analysis.json`. The analysis SHA-256 is
`aa2bbe8e9a0cbe0fddc4d5019fc7774a7656fdb8ac3c6aa9863547d0fe6594ef`.
The reproducible local analysis program is [analyze_results.py](analyze_results.py).

After four valid runs, the exact runtime override was deleted and systemd was
reloaded/restarted. Final verification found:

- controller service active;
- `system_info` running normally;
- no qualification environment in the service;
- installed `core/output.py`, temporary merged `main.py`, and
  `qualification_metrics.py` hashes unchanged from the table above;
- 45.7°C and `throttled=0x0`; and
- no recent service traceback/error/failure.

Step 4 was deliberately not restored. Step-6 runtime files remain deployed and FFT
will select direct by default until the user chooses the permanent Pi state.
