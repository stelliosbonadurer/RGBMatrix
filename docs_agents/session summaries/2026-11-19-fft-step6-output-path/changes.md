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
