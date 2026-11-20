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
- Hardware execution and deployment required separate approval; both were later
  granted. Promotion remains a separate decision.

## Status

The candidate was qualified on the Pi in four valid five-minute windows after a
successful 202-frame hardware smoke. Pillow failed the approved speed gate: pooled
swap FPS was 2.31% lower, while complete draw-through-swap p50 and p95 improved only
1.70% and 0.27%, far below the required 10%. Both directional pairs also showed
lower Pillow FPS. Direct rendering therefore remains the accepted output path.

The temporary override was removed. Per explicit user direction, step 4 was not
restored: the coherent step-6 files remain deployed on the Pi, with the normal
startup default selecting direct rendering. This is an interim deployed state, not
promotion of Pillow or of the temporary metrics hook. See [decisions.md](decisions.md),
[changes.md](changes.md), [tests.md](tests.md), [architecture.md](architecture.md), and
[handoff.md](handoff.md). The explanatory case-study record is
[concepts-and-findings.md](concepts-and-findings.md), and the bounded hardware plan is
[qualification-plan.md](qualification-plan.md). The detailed audit is in
[`../2026-11-19-1-Iterative-approach/03-step6-output-path-comparison.md`](../2026-11-19-1-Iterative-approach/03-step6-output-path-comparison.md).
The retained raw-evidence location and immutable manifest are indexed in
[artifacts/README.md](artifacts/README.md).
