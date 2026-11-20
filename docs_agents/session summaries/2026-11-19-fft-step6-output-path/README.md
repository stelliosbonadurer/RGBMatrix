# FFT step 6 — output-path candidate

## Objective

Compare the complete accepted direct-canvas renderer with one opt-in, preallocated
Pillow candidate without changing the step-5 analyzer, transport, scaler, cadence,
VSync, or five-millisecond pacing.

## Baseline and acceptance criteria

- Qualified source baseline: step 5 commit
  `5e018a4a3ec36ea81fb985153b2897fd9a3d184a`.
- Audit-start HEAD: `44a49d37fe861df67edf05dff27c38e4a8100072`.
- Direct rendering remains the default and behaviorally unchanged.
- The candidate owns one stable Pillow `RGB` image, matches qualification pixels,
  performs one `SetImage` per frame, and adds no staging buffer or fallback path.
- A dedicated concepts/findings record distinguishes hypotheses from measured results
  and is completed for future engineering work and case-study use.
- No Pi deployment, restart, LAN test, hardware run, or production promotion occurs
  without separate approval.

## Status

The local candidate and hardware-free tests are complete and independently reviewed.
Candidate source commit `2616c56bddce72b0c08326ae9d3058ce68210954` contains
only the six reviewed runtime/test files. All `test_fft*.py` tests pass (57 tests, one
expected skip). The candidate remains unmeasured on hardware. See [decisions.md](decisions.md),
[changes.md](changes.md), [tests.md](tests.md), [architecture.md](architecture.md), and
[handoff.md](handoff.md). The explanatory case-study record is
[concepts-and-findings.md](concepts-and-findings.md). The detailed audit is in
[`../2026-11-19-Iterative-approach/03-step6-output-path-comparison.md`](../2026-11-19-Iterative-approach/03-step6-output-path-comparison.md).
