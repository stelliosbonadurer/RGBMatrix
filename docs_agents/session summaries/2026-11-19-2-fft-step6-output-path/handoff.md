# Handoff

## Result

The Pillow candidate is hardware-compatible but does not meet the approved speed
gate. Four valid counterbalanced five-minute windows produced pooled results of
104.316 FPS for direct and 101.907 FPS for Pillow. Pillow improved complete-frame
p50/p95 only 1.70%/0.27%, not the required 10%, and both directional pairs had lower
Pillow FPS. Direct rendering remains the accepted architecture.

Candidate source is commit `2616c56bddce72b0c08326ae9d3058ce68210954`, based on
qualified step 5 `5e018a4a3ec36ea81fb985153b2897fd9a3d184a`. The exact
temporary merged hashes and transfer archive hashes are in
[qualification-plan.md](qualification-plan.md).

## Pi operational state

- `rgb-matrix-web@stelliosbonadurer.service` is active.
- `system_info` is the active supervised visualization after cleanup.
- The temporary step-6 service override is absent and no qualification environment
  remains.
- Throttling is `0x0`; the final verification found no recent service error.
- Per explicit user instruction, step 4 was not restored. The nine deployed step-6
  runtime/qualification files remain in the checkout. With no environment selection,
  FFT starts in normal direct-default mode.
- This is an approved interim test state, not candidate promotion.

Fresh rollback archive:
`/home/stelliosbonadurer/rgb_Matrix.step6-predeploy-20261120T022848Z.tar.gz`,
SHA-256 `7c887c9a88c9b5ca40bc13fb86f05244f876c8e694816ef58ac04875748a40b5`.

Persistent evidence directory:
`/home/stelliosbonadurer/fft-step6-qualification-20261120/`. Its `analysis.json`
SHA-256 is `aa2bbe8e9a0cbe0fddc4d5019fc7774a7656fdb8ac3c6aa9863547d0fe6594ef`.
The directory contains the smoke JSON/NPZ, four run JSON/NPZ pairs, external TSVs,
journals, and pooled analysis.

## Local state

The qualification metrics patch remains uncommitted so it is not confused with
product telemetry. The session records now include the reproducible
[analyze_results.py](analyze_results.py) and the completed learning/case-study record
[concepts-and-findings.md](concepts-and-findings.md).

Unrelated user-owned `.DS_Store`, `AGENTS.md`, and
`LED_Matrix_Rendering_Design.md` remain excluded and untouched.

## Next decision

Choose the intended Pi state explicitly:

1. keep step 6 deployed but direct-default while removing temporary instrumentation;
2. restore a prior accepted deployment; or
3. begin a separately scoped native-buffer binding experiment.

The data does not support promoting the Pillow candidate. A native experiment should
first define exact buffer ownership/stride, GIL behavior, pinned binding build
identity, recovery, and an output-stage-only diagnostic in addition to the same
end-to-end gate.
