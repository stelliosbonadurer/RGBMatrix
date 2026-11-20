# Changes

- Added `core/output.py` with a qualification-only `PillowBulkBarsOutputPlan`.
- Startup compilation validates 64x64 ocean overflow bars with gradient, shadows,
  peaks, full mode, and debug mode disabled.
- The plan allocates one Pillow `RGB` image, 64 by 65 stable geometry boxes, and three
  64-entry stable ocean color tables. Each frame clears and writes that image in
  place, then calls `SetImage` once.
- `main.py` reads `RGBMATRIX_FFT_OUTPUT_PATH` once at startup. `direct` remains the
  default; `pillow-bulk` selects the candidate. The frame-loop draw/swap/sleep shape
  is unchanged.
- `runtime_control.py` can reject an output plan's incompatible commands before
  analysis preparation or state mutation, preserving batch atomicity.
- Added exact pixel-equivalence, identity, guard-set, failure, scope, and hot-path
  tests. Extended runtime-control tests for guarded mixed batches and keyboard input.

There is no NumPy RGB staging frame, per-frame `frombytes`, candidate retry, direct
fallback, generic compositor, process boundary, or change to the direct renderer.

## Temporary qualification patch

The uncommitted patch adds `qualification_metrics.py`, two read-only exact-lease
properties in `core/audio.py`, and opt-in wiring in `main.py`. It preallocates exact
samples, uses an absolute settle/measurement watchdog, disables runtime-state writes
symmetrically, and writes fail-closed JSON/NPZ artifacts with explicit validity and
termination metadata. True renderer and VSync intervals are separate. A local-only
test file covers completed, capacity-exhausted, zero-frame, and interrupted-settle
runs. These files are measurement scaffolding, not a product architecture change.

## Qualification and operational changes

- Created a fresh exact predeployment recovery archive at
  `/home/stelliosbonadurer/rgb_Matrix.step6-predeploy-20261120T022848Z.tar.gz`,
  SHA-256 `7c887c9a88c9b5ca40bc13fb86f05244f876c8e694816ef58ac04875748a40b5`.
- Staged and verified the two transfer archives, ran 56 controlling Pi tests, and
  installed the nine exact runtime/qualification files listed in
  [qualification-plan.md](qualification-plan.md).
- Added only a runtime systemd override for each bounded run. The override was
  removed after four valid windows; it is absent now.
- Preserved JSON, compressed NPZ, five-second external samples, journals, and pooled
  analysis under `/home/stelliosbonadurer/fft-step6-qualification-20261120/`.
- Left the step-6 runtime files deployed on explicit instruction. Normal startup has
  no qualification environment and selects the direct default.
- Added [analyze_results.py](analyze_results.py) to reproduce the pooled, pairwise,
  gate, CPU/RSS, and thermal calculations from retained artifacts.
