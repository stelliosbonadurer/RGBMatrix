# Tests and evidence

## Final local command

```text
'/Users/stelliosbonadurer/Documents/oldUMcoursework/CSCI477simulation/workspace/.venv/bin/python' \
  -m unittest discover -s tests -p 'test_fft*.py'
```

Result: 57 tests passed in 0.023 seconds; one expected skip.

An in-memory `compile()` check passed for all six changed Python/test files, and
`git diff --check` passed after the final documentation edit. An independent final
review reported no remaining correctness, hot-path, test, or documentation blocker.
The tested candidate is commit
`2616c56bddce72b0c08326ae9d3058ce68210954`.

## Qualification-patch verification

The focused recorder/output/runtime/pipeline run passed 56 tests with one expected
skip. Final `test_fft*.py` discovery with the temporary recorder test passed 61 tests
in 0.049 seconds with one expected skip. `git diff --check` and in-memory syntax
compilation passed. Independent re-review found no remaining duration, failure,
timing, symmetry, or bounded-storage blocker.

## What this proves

- Exact direct/candidate pixels for empty, NaN, partial, full, and overflow frames.
- Stable Pillow image storage, geometry, and color tables.
- One upload per draw and no retry or fallback on an upload exception.
- Startup-only selection/compilation and complete runtime guard coverage.
- Atomic rejection of mixed allowed/rejected command batches.

## Limits

The local fake canvas does not prove Pi binding compatibility, private Pillow ABI
compatibility, native copy cost, VSync timing, audio behavior, GPIO output, panel
appearance, service health, or comparative performance by themselves. Those limits
were addressed by the approved Pi qualification below.

## Pi staging and compatibility evidence

- Pi: Python 3.11.2, Pillow 9.4.0, binding binary SHA-256
  `a838e482f0dcdccbd5c54300b34eae1a17074eb6ec328968bc01946cfa23c481`.
- Binding source SHA-256:
  `e8294ee5c24a9b69ab18bc91fc705d5089b0cec2b90e0d306301d0dbdea64bee`.
  Its `dict(image.im.unsafe_ptrs)['image32']` conversion is compatible with Pillow
  9.4.0's tuple representation.
- The isolated Pi stage passed 56 controlling tests in 0.277 seconds.
- A real two-second Pillow run completed 202 frames, wrote a valid artifact, cleared
  the panel, and exited zero.

## Four-run qualification

All runs settled for 60 seconds and measured for 300 seconds:

| Run | Frames | Swap FPS | Total p50 ms | Total p95 ms | Unique FPS | Valid |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `direct-1` | 31,313 | 104.377 | 4.053 | 4.388 | 60.677 | yes |
| `pillow-1` | 30,429 | 101.428 | 3.968 | 4.372 | 59.566 | yes |
| `pillow-2` | 30,716 | 102.385 | 4.007 | 4.366 | 59.543 | yes |
| `direct-2` | 31,277 | 104.256 | 4.076 | 4.369 | 60.286 | yes |

Pooled Pillow changes relative to direct were -2.31% swap FPS, +1.70% total
p50, +0.27% total p95, and -1.53% unique-content FPS. Positive latency values mean
lower/faster. Pillow did not pass the material-win gate.

Every external service sample was active, every throttle sample was `0x0`, all runs
used seven threads, and temperatures stayed between 45.7°C and 53.0°C. Mean process
CPU was 86.9%/87.2% for direct and 91.9%/92.5% for Pillow; CPU was informational by
design. Analyzed/published fractions were 97.3–98.3%, result-age p95 stayed below
23.22 ms, callback-status and worker-failure deltas were zero, and no array exhausted
its 100,000-frame capacity.

The raw-evidence location, contents, and final SHA-256 manifest are indexed in
[artifacts/README.md](artifacts/README.md). The final analyzer rerun reproduced the
Pi's saved `analysis.json` byte-for-byte with SHA-256
`aa2bbe8e9a0cbe0fddc4d5019fc7774a7656fdb8ac3c6aa9863547d0fe6594ef`.

Final local verification after documentation completed:

```text
python -m unittest discover -s tests -p 'test_fft*.py'
61 tests passed in 0.066 seconds; one expected skip
```

In-memory syntax compilation passed for the temporary runtime patch, recorder test,
and analysis script. `git diff --check` passed.
