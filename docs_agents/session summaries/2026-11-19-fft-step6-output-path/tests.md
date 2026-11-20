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

## What this proves

- Exact direct/candidate pixels for empty, NaN, partial, full, and overflow frames.
- Stable Pillow image storage, geometry, and color tables.
- One upload per draw and no retry or fallback on an upload exception.
- Startup-only selection/compilation and complete runtime guard coverage.
- Atomic rejection of mixed allowed/rejected command batches.

## Limits

The local fake canvas does not prove Pi binding compatibility, private Pillow ABI
compatibility, native copy cost, VSync timing, audio behavior, GPIO output, panel
appearance, service health, or comparative performance. No raw benchmark artifact
exists yet because hardware measurement is not approved and instrumentation design
has not yet been implemented.
