# Iterative FFT rebuild — step 3: interfaces without execution changes

> **Status: accepted on 2026-11-18.** The legacy capture, analysis, and rendering
> stages now sit behind narrow structural interfaces. The standard Pi workload
> remained at least as fast and resource-efficient as the preserved legacy result.

## Purpose

This implements step 3 of the rebuild sequence in
[`pipeline-comparison-and-next-architecture.md`](../2026-11-18-1-performance-comparison/pipeline-comparison-and-next-architecture.md):

> Introduce interfaces without changing execution. Wrap legacy capture, analyzer,
> and renderer behind narrow contracts while keeping the same arrays, call order,
> and output path.

This checkpoint was intentionally not an FFT optimization. Its purpose was to prove
that modular seams do not require queues, boxed frame objects, generic composition,
additional copies, or a performance loss.

## Approved design

The design was selected one decision at a time before editing:

1. Use static `typing.Protocol` contracts rather than delegating adapter objects.
2. Expose the existing `AudioProcessor` as two typed views—capture and analyzer—
   rather than physically separating it in this step.
3. Keep the renderer boundary draw-only. Peak overlay, `SwapOnVSync`, and pacing
   remain in `main.py` in their existing order.
4. Include the current layered methods and draw arguments in the contracts. The
   existing second layered FFT remains unchanged.
5. Keep all three contracts in one small `core/interfaces.py` module.
6. Add hardware-free tests for the existing callback, FFT, and contract behavior.
7. Use a startup-only `typing.cast` at the generic visualizer-registry boundary.
   It returns the same object and adds no hot-path allocation or dispatch.

The protocols are not `runtime_checkable`. They exist for static structure and
substitution, not for repeated runtime validation.

## Implementation

| File | Change |
| --- | --- |
| `my_programs/fft_program/core/interfaces.py` | Added `AudioCapture`, `MagnitudeAnalyzer`, and `FrameRenderer` structural protocols. |
| `my_programs/fft_program/core/__init__.py` | Exported the three protocol types. |
| `my_programs/fft_program/main.py` | Created typed views of the existing audio and visualizer objects and used those views at the current call sites. |
| `my_programs/fft_program/visualizers/bars_unified.py` | Corrected annotations to describe the existing `None` placeholders for invisible layers. Runtime parameters and behavior did not change. |
| `tests/test_fft_pipeline_interfaces.py` | Added dependency-isolated callback, FFT-equivalence, protocol, and signature tests. |

The steady-state path is still:

```text
PortAudio callback
  -> copy the newest channel block into a NumPy array

single main loop
  -> current FFT and 64-bin projection
  -> current scaling, smoothing, and peak state
  -> current direct native-canvas drawing
  -> current peak overlay
  -> SwapOnVSync
  -> current 5 ms sleep
```

No FFT, frequency, sensitivity, scaling, theme, matrix, or drawing parameter was
changed. In particular, the qualified workload still uses a 512-sample input block,
8192-point FFT, 64 output columns, and 5 ms post-frame delay.

No new frame dataclass, dictionary, tuple conversion, queue, ring, lock, thread,
process, compositor, Pillow path, telemetry object, or per-frame protocol check was
added. Layered mode still performs its existing second FFT; sharing that computation
is a later change and was deliberately excluded from this checkpoint.

## Verification

### Local

- The focused FFT test discovery ran 12 tests: 11 passed and one existing test was
  skipped.
- The host system Python does not have NumPy. The new module therefore skips cleanly
  there rather than failing unrelated test discovery.
- The same focused suite under a NumPy-capable Python executed the new callback and
  numerical FFT tests successfully.
- In-memory compilation and `git diff --check` passed.

The new tests establish that:

- the callback copies the selected `float32` channel;
- publication remains latest-value replacement rather than shared input memory or a
  queue;
- a previously published array remains valid after the callback publishes another;
- the standard FFT projection still matches the existing NumPy calculation exactly;
- defaults remain 512 input samples and an 8192-point FFT;
- protocols remain static rather than runtime-checkable; and
- the existing `AudioProcessor` and `BarsUnifiedVisualizer` method signatures match
  the approved contracts.

### Raspberry Pi

The deployed FFT Python environment ran the focused interface and runtime-control
tests:

```text
Ran 12 tests in 0.056s
OK
```

All five deployed files also passed an in-memory syntax compilation. This avoided
writing bytecode into an existing root-owned `__pycache__` directory.

## Pi performance qualification

### Workload

The qualification used the same physical Pi, iMM-6C USB source, and 64×64 HUB75
panel as the preserved legacy comparison.

| Setting | Value |
| --- | ---: |
| Theme | `ocean` |
| Overflow | enabled |
| Gradient, peaks, shadows | disabled |
| Audio sample rate | 44,100 Hz |
| Audio block | 512 samples |
| FFT size | 8192 |
| Output bins | 64 |
| Post-frame sleep | 5 ms |

The Pi's retained opt-in legacy metrics hook was enabled through a temporary systemd
override. No measurement code or persistent telemetry was added to the local rebuild.
The external CPU/RSS/thermal observation used 30 one-second samples. The existing
internal metrics hook measures from post-audio-settle startup through clean shutdown,
so its complete frame window was 60.216 seconds rather than exactly 30 seconds. The
reported CPU range consists of `ps` lifetime-average readings sampled during that
external window.

### Result

| Metric | Step 3 result |
| --- | ---: |
| Frames published | 6,035 |
| Metrics elapsed time | 60.216 s |
| Achieved content FPS | **100.22** |
| Process CPU | **85.5–85.8%** |
| RSS | **57,444–57,828 KiB** (56.1–56.5 MiB) |
| Render-to-swap p50 | **4.45 ms** |
| Render-to-swap p95 | **4.76 ms** |
| Render-to-swap p99 | 14.28 ms |
| Render-to-swap max | 17.67 ms |
| Publish interval p50 | 9.51 ms |
| Publish interval p95 | 9.83 ms |
| Publish interval p99 | 19.42 ms |
| Publish interval max | 22.76 ms |
| Temperature | **45.2–47.7 °C** |
| Throttle status | **`0x0` throughout** |
| Recent runtime/audio errors | **none found** |

The legacy callback does not expose overrun/underflow counters, so their absence
cannot be inferred from this run. The journal contained no audio-status, runtime,
traceback, overrun, or underflow errors during the qualification.

### Historical comparison

| Metric | Preserved legacy run | Step 3 | Assessment |
| --- | ---: | ---: | --- |
| Content FPS | 94.04 | 100.22 | 6.6% higher; no regression |
| Process CPU | 85.82% | 85.5–85.8% | effectively unchanged |
| RSS | 56.4–56.6 MiB | 56.1–56.5 MiB | effectively unchanged |
| Render-to-swap p50 | 4.78 ms | 4.45 ms | no regression |
| Render-to-swap p95 | 13.99 ms | 4.76 ms | no regression |
| Temperature | 54.5 °C | 45.2–47.7 °C | no thermal concern; ambient/run conditions differ |
| Throttled | no | no | unchanged |

The two runs were not simultaneous A/B captures and their temperature and input
conditions may differ. The correct conclusion is therefore that step 3 introduced
no measurable regression—not that the protocol annotations made the FFT faster.
The performance result is consistent with the code change: the hot loop still makes
the same method calls on the same objects with the same arrays and arguments.

## Deployment safety and retained evidence

The Pi checkout was already dirty at commit `7be139c`. Its `main.py` contained the
failed-refactor selector and the opt-in legacy metrics hook, while many controller
and removed-refactor files were modified or untracked. Those changes were preserved.

Before deployment:

- remote `core/__init__.py`, `core/audio.py`, and `bars_unified.py` were verified
  against the local baseline hashes;
- the remote `main.py` was copied locally;
- only the approved protocol-view changes were applied to that copy;
- its diff against the Pi version was reviewed before upload; and
- the three pre-existing remote files were archived at:

```text
/home/stelliosbonadurer/rgb_Matrix.step3-predeploy-20261118T2055Z.tar.gz
```

Archive SHA-256:

```text
c4b8cc4a6eeb6ec0089dd224153a2b93f700a5d95a0eff6540521441554abab2
```

The raw qualification result remains on the Pi at:

```text
/var/lib/rgb-matrix-controller/fft-step3-qualification.json
```

After the run, the temporary `/run` service override was removed, the web service was
restarted with its original environment, and `system_info` was restored as the active
visualization. The pre-existing dirty Pi files and the unrelated local deleted plan
documents were not reverted or overwritten.

## Outcome and next boundary

Step 3 is accepted:

- capture, analyzer, and renderer now have explicit narrow contracts;
- the existing NumPy arrays cross those contracts without copying or boxing;
- execution remains serialized in the legacy loop;
- the direct-canvas fast path remains intact; and
- the measured Pi performance and resource envelope match or exceed the preserved
  legacy baseline.

The contracts do not yet isolate execution. `AudioProcessor` still owns capture and
analysis, and runtime control still reaches the concrete audio/visualizer objects.
That is intentional for this checkpoint.

The next rebuild step is step 4: compile validated configuration off the hot path
into array-oriented runtime plans. That work should begin with a new design gate and
must not alter this accepted fast path until its candidate behavior is separately
verified.
