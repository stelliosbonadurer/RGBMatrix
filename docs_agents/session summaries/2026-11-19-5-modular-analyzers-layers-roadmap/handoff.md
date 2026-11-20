# Handoff

## Current state

The active direction is now modular analyzers and ordered visual layers. Both direct
and reusable Pillow/SetImage output paths are preserved as explicit manual choices.
Timing and generalized compositing work are outside this refactor.

No runtime code has been changed. Begin with Step 1 only; do not start the analyzer
registry, render migration, or process experiment in the same increment.

## Smallest useful next step

Implement and test the non-executing compiled runtime graph described in Roadmap
Step 1:

1. Inspect current settings/config conventions and existing protocols.
2. Define stable analyzer and layer IDs plus their immutable definitions.
3. Define ordered runtime-plan compilation and explicit output backend selection.
4. Validate duplicates, references, ordering, and backend support.
5. Prove the model with unit tests without connecting it to `main.py`.
6. Stop for review.

Do not rename the existing `pillow-bulk` setting merely because the architecture
calls the method SetImage; compatibility should be decided during implementation.

## Process recommendation

Do address process separation during this architecture, but not first. Run the
matrix-process spike immediately after the in-process analyzer and layer seams are
working. This timing is early enough to influence ownership and control messages,
while avoiding the risk of designing modularity around unproven IPC.

The candidate has two processes, not one per module: analysis owns audio and all
analyzers; matrix/render owns presentation state, ordered layers, the selected output
backend, and every native matrix object. Only compact latest-value arrays cross the
hot boundary.

## Working tree and operations

- Base commit: `ae7e263`
- This folder is uncommitted.
- Superseded pacing-plan material was later consolidated with user approval in
  `2026-11-19-6-considerations`.
- `.DS_Store` remains unrelated and must be excluded from later commits.
- No remote files were inspected or changed.
- No service was restarted and no baseline was deployed or promoted.

## Remaining risks

- Backend-specific layer kernels can drift if compatibility expectations are not
  stated per layer.
- Stable IDs must not become per-frame dictionary lookups in inner drawing loops.
- Layer-owned smoothing/peak state needs careful migration from the current global
  path.
- Shared-memory publication must prove completeness and bounded staleness before a
  process split is accepted.
- A process split may improve isolation without improving FPS; its value must be
  judged against lifecycle and operational cost, not FPS alone.
