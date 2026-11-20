# FFT rebuild progress — step 6 output-path comparison

> **Session date:** 2026-11-19 local time (America/Denver)
>
> **Status:** The first qualification-only Pillow candidate is committed as
> `2616c56bddce72b0c08326ae9d3058ce68210954` and passes focused hardware-free
> verification. Qualified step 5 is the accepted source baseline. No deployment,
> service restart, LAN test, or hardware measurement has been approved or performed.

## Scope

Step 6 compares the complete existing direct native-canvas output path with at most
one narrowly scoped, opt-in, preallocated bulk-output candidate. The comparison must
include geometry and color generation, buffer writes, Pillow work, native upload,
`SwapOnVSync`, and copies. An isolated `SetPixel`/`SetImage` microbenchmark cannot
select the accepted path.

The qualified step-5 analyzer, two-slot latest-value transport, scaler, render
cadence, and five-millisecond post-frame sleep remain fixed. Direct native-canvas
drawing remains the default and comparison baseline. This checkpoint does not begin
multiple visualization instances, composition, projection redesign, response-curve
work, noise-floor relocation, timing redesign, a process boundary, or permanent
telemetry.

## Approved decisions

1. Use qualified step 5, commit
   `5e018a4a3ec36ea81fb985153b2897fd9a3d184a`, as the exact step-6 comparison
   baseline.
2. Accept step 5 as the new rebuild baseline. This source/architecture decision does
   not itself authorize a Pi deployment or service restart. The Pi's last recorded
   operational state remains restored accepted step 4 until a separately approved
   production deployment.
3. Implement the first candidate as one reusable Pillow-owned `RGB` image. Clear and
   populate that image directly with the qualification bar geometry, then call
   `SetImage` once. Do not add a NumPy staging frame or a per-frame `frombytes` copy.
4. Keep this candidate deliberately limited to the qualification configuration and
   compile/select it once at startup through a temporary environment setting. Keep
   direct native-canvas drawing as the default and do not change the runtime-control
   protocol.
5. Treat a native binding extension as the only serious later alternative if the
   Pillow candidate loses. That alternative is not part of this checkpoint because
   it changes the installed matrix binding, build/deployment surface, and exact
   binding identity that must be qualified.
6. Reject candidate-invalidating runtime commands at the existing controller batch-
   validation boundary. Use the protocol's existing structured `unavailable` error,
   preserve snapshots and analyzer/scaler controls, and add no frame-loop validation.

## Exact starting identities

| Role | Identity |
| --- | --- |
| Accepted step-4 predecessor | `21a13d938be7178799302d35976a1c5c7d4c1065` |
| Accepted step-6 source baseline / qualified step 5 | `5e018a4a3ec36ea81fb985153b2897fd9a3d184a` |
| Step-5 source-identity documentation | `b25d567246fe4d6b0e42de0a5bce7d3316bb45ee` |
| Documentation HEAD at audit start | `44a49d37fe861df67edf05dff27c38e4a8100072` |
| Step-6 Pillow candidate source | `2616c56bddce72b0c08326ae9d3058ce68210954` |
| Pi checkout pointer | `7be139c` operational metadata only; not a measured source identity |

Starting tracked-file SHA-256 identities:

| File | SHA-256 |
| --- | --- |
| `my_programs/fft_program/main.py` | `f44957a535d5db1bb1c010c53171d52f2f745965f541b4e6de4d2e35e9784314` |
| `my_programs/fft_program/core/matrix_app.py` | `e9ff9e57690e3231de4d5913e87c1335d5b096bd4387128806602e3112bdb150` |
| `my_programs/fft_program/visualizers/bars_unified.py` | `a85ad22546dd46c17d1db29c79fa47a13f4abbeb83e6efc45f81116149b0cdc7` |
| `tests/test_fft_pipeline_interfaces.py` | `bf5be162cbe5d02d7e6b64ee3d3561bb661022e68a00d8e9bdee8764238d4b18` |

The ignored local binding source inspected during the audit is
`rpi-rgb-led-matrix/bindings/python/rgbmatrix/core.pyx`, SHA-256
`e8294ee5c24a9b69ab18bc91fc705d5089b0cec2b90e0d306301d0dbdea64bee`.
It is useful implementation evidence but is not a reproducibly pinned identity for
the installed Pi binding.

The current candidate is commit
`2616c56bddce72b0c08326ae9d3058ce68210954`, based on documentation HEAD
`44a49d37fe861df67edf05dff27c38e4a8100072`. It has not been used for a hardware
comparison. Candidate file identities are:

| File | SHA-256 |
| --- | --- |
| `my_programs/fft_program/core/output.py` | `2cba7097378e90bcf168b94b60329f7e2e14b63fb09aafa5bce7e21477ebd07f` |
| `my_programs/fft_program/core/__init__.py` | `6d5f10250b9d1d430ae86199aa232a9b7ae861d71c171c69e1d20ca0db2a686fdd` |
| `my_programs/fft_program/main.py` | `ae2cb190f90619309da4a63dad6e2bb3e1b321aa59f45e98145122719a3d16cf` |
| `my_programs/fft_program/runtime_control.py` | `b7761fe48043bdfe01a0441bce67cb6e640d3e9bfe52c2c561520d4b32941635` |
| `tests/test_fft_output_path.py` | `6f662bfba8ae6eb8d5ce130dd14d110b9bd3a4d3acc34c992e520d489a2b5fa2` |
| `tests/test_fft_runtime_control.py` | `9bb53490469f8039df89c44467040a6cd93f0db5dd8495dcde3b1fd13ad09e65` |

These hashes reflect the final local review and committed candidate. Any temporary
qualification patch and deployed merged-file hashes must be recorded separately.

## Read-only output-path audit

The complete current standard path is:

```text
lease latest complete 64-bin result
  -> current scaler / attack-decay / peaks
  -> release the analysis slot in finally
  -> BarsUnifiedVisualizer.draw
       -> native canvas.Clear
       -> qualification path: one overflow color per nonempty column
       -> native canvas.SetPixel for every visible bar pixel
  -> optional peak overlay
  -> MatrixApp.swap_canvas / blocking SwapOnVSync
  -> five-millisecond sleep
```

No analysis lease or analysis-worker lock is held during drawing, upload, VSync, or
sleep. The qualification path has no application-owned full-frame RGB buffer and no
full-frame copy. It clears the native canvas and performs sparse `SetPixel` calls for
lit pixels, with at most 4,096 bar-pixel calls on a 64x64 display.

The available Python binding's `SetImage` path:

1. requires a Pillow image whose mode is exactly `RGB`;
2. accesses Pillow's private `image.im.unsafe_ptrs['image32']` representation;
3. traverses every image pixel in Cython; and
4. calls the native frame canvas's `SetPixel` for every pixel.

It is one Python binding call but not a raw NumPy/byte-buffer upload. The raw C++ RGB
helper and `FrameCanvas::SetPixels` are not exposed by this Python binding. Any valid
candidate must therefore count construction of a complete RGB frame, transfer into
Pillow-owned image storage, the binding's full 4,096-pixel traversal/native writes,
and VSync. A reusable Pillow image identity does not imply zero-copy frame updates.

## Current copy and allocation paths

### Direct baseline

```text
smoothed float array
  -> Python geometry/color calculation
  -> Python/Cython SetPixel calls for visible pixels
  -> native bitplane writes
```

- Native `Clear` supplies the black background.
- There is no app-owned RGB frame or Pillow object.
- The binding creates a new Python `FrameCanvas` wrapper when each
  `SwapOnVSync` returns; native frame storage is reused. This common binding behavior
  must not be mistaken for candidate buffer instability.
- The ocean, overflow-enabled, non-gradient qualification path creates one returned
  RGB tuple per nonempty column through the existing theme method.

### Implemented Pillow bulk candidate

```text
smoothed float array
  -> clear the reusable Pillow RGB image in place
  -> direct Pillow writes for at most 64 one-column bar rectangles
  -> SetImage Cython traversal of all 4,096 pixels
  -> native bitplane writes
```

`core/output.py` now implements this path. Startup compilation validates the exact
64x64 ocean/overflow/non-gradient/no-shadow/no-peaks qualification configuration,
creates one Pillow-owned `RGB` image, and precomputes 64 by 65 one-column boxes plus
three 64-entry ocean overflow color tables. Steady-state drawing performs no theme
lookup and creates no geometry or RGB tuples. It clears and populates the same Pillow
image directly, so there is no application RGB staging buffer and no per-frame
`frombytes` copy. `SetImage` still performs the binding's complete image traversal
and native pixel writes, which the end-to-end Pi comparison must include.

Selection is read once at startup from `RGBMATRIX_FFT_OUTPUT_PATH`; `direct` remains
the default. The original direct renderer remains the object called by the unchanged
frame-loop statement when that default is selected. Candidate-invalidating runtime
commands are rejected before batch preparation or mutation, while snapshots and
analyzer/scaler controls remain available.

### Deferred native-binding alternative

The local driver source contains lower-level raw RGB and `FrameCanvas::SetPixels`
facilities that the installed Python binding does not expose. A purpose-built binding
method could accept a stable contiguous RGB buffer, validate its shape/type at setup,
and copy or translate it directly in C/C++ without Pillow or thousands of Python
calls. This has the highest plausible bulk-output ceiling, but it would require:

- pinning and hashing the exact driver/binding source and build flags;
- defining buffer ownership, stride, RGB order, lifetime, and GIL behavior;
- building a Pi-compatible extension and preserving a recovery path for the current
  installed binding;
- deploying a native artifact in addition to Python files; and
- separately testing matrix mapping, brightness/luminance behavior, failures, and
  service compatibility.

It is therefore a later experiment, not a fallback hidden inside the Pillow
candidate. If the complete Pillow path does not materially beat direct drawing, its
negative result will not prove that a true raw native upload cannot win.

## Existing test and qualification integration audit

The hardware-free suite now proves exact whole-frame direct/candidate pixel equality
across empty, NaN, partial, full, and two overflow bands; exact ocean band colors;
stable Pillow image storage, geometry-table, and color-table identities; one
`SetImage` call per draw; no retry or direct-path fallback on upload failure; startup-
only selection/compilation; compiler rejection outside the qualification scope; and
atomic runtime rejection of candidate-invalidating controls. Existing pipeline tests
continue to cover transport release, draw, peak overlay, swap, and sleep ordering.

These tests use an in-memory canvas and do not exercise the installed Pi binding,
driver timing, VSync duration, native copies, or visible hardware output. Those remain
Pi qualification requirements, not conclusions from local tests.

There is no tracked output-stage benchmark harness. Step-5 qualification used a
Pi-only `main.py` merge and opt-in temporary environment overrides. Its aggregate
draw-through-swap timings did not separately measure geometry, RGB writes, Pillow
work, upload, or VSync. Any step-6 instrumentation must be opt-in, bounded, and kept
outside the product architecture. The external observer must resolve and verify the
descendant FFT Python process rather than the idle `sudo` wrapper.

## Rejected alternatives so far

- A process/shared-memory boundary: step 5 passed its service gates.
- Restoring the rejected generic compositor or its output helper unchanged.
- Treating a lower Python call count as proof that `SetImage` is faster.
- Using the ignored local binding checkout as proof of the Pi's installed binding
  identity.
- Deploying or promoting files on the Pi as an implicit consequence of accepting
  step 5 as the rebuild baseline.

## Dependency and deployment boundary

Pillow is deliberately imported only when the candidate is selected. It is present
in the last recorded Pi environment, but it is not declared in `requirements.txt`.
The inspected binding uses Pillow's private `unsafe_ptrs` ABI, which is absent from
the newer development-host Pillow. Adding an unconstrained dependency would therefore
hide rather than solve compatibility. Before hardware use, record the exact installed
Pi Pillow version and binding identity, verify that a minimal `SetImage` call works,
and preserve the direct default as the recovery path.

No files have been deployed to the Pi, no service has been restarted, and no hardware
or LAN measurement has been run for this candidate.

## Verification performed so far

- `git log -4` and `git status --short` were inspected; the worktree was clean before
  this checkpoint work began, apart from later unrelated user-owned files that remain
  excluded from the candidate.
- All controlling step-3, step-4, step-5, qualification, noise-floor, and pipeline
  records listed in the request were read completely.
- Independent audits inspected the renderer/VSync path, binding and qualification
  constraints, and the final hot path and allocation behavior.
- The focused output/runtime/pipeline suite passed 51 tests with one skip before the
  complete guard-set assertion was added.
- The final run of all `test_fft*.py` modules passed 57 tests with one expected skip.
- In-memory syntax compilation and `git diff --check` passed.
