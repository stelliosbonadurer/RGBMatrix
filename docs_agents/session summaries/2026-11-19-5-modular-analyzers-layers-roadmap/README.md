# Modular analyzers and layers roadmap

## Objective

Refocus the FFT rebuild on a small modular runtime in which analyzers can be shared
and visual layers can be added, removed, hidden, and reordered without rewriting the
matrix loop.

The runtime must preserve both existing output approaches:

- `direct`: draw against the native matrix canvas;
- `setimage`: render into the reusable Pillow image and submit it with `SetImage`
  (currently named `pillow-bulk` in configuration).

This is the active roadmap for future work. Superseded pacing-first ideas are
condensed in `2026-11-19-6-considerations` and are not active requirements.

## Success criteria

- Analyzer instances have stable identities and can be referenced by more than one
  layer without duplicate equivalent analysis work.
- The compiled render plan has stable layer identities and an explicit bottom-to-top
  order.
- Structural changes can replace the compiled plan atomically at a frame boundary.
- Direct and SetImage remain first-class, manually selected output backends.
- The current single-layer behavior and hot paths remain available during migration.
- A bounded experiment determines whether matrix ownership should move to a separate
  process before the process boundary becomes a product dependency.
- No timing, deadline-pacing, automatic output selection, or general compositing
  project is pulled into this refactor.

## Baseline

- Branch: `FFT-Refactor`
- Base commit: `ae7e263` (`Checkpoint FFT qualification and pacing roadmap`)
- Direct output is the accepted default.
- The qualified single sparse-layer comparison was approximately 104.3 FPS direct
  and 101.9 FPS SetImage/Pillow. Those results do not select a universal winner.
- The current application has one audio/analyzer path, one scaling/presentation
  path, one primary visualizer, and a separate peaks draw pass.

At creation, the working tree also contained `.DS_Store` and four modified records
from the superseded pacing plan. The user later authorized consolidating those plan
records into `2026-11-19-6-considerations`.

## Scope

In scope:

- analyzer definitions, identity, sharing, and ownership;
- ordered visual-layer definitions and compiled runtime plans;
- per-layer presentation state where state must be independent;
- manual selection and preservation of both output backends;
- atomic plan replacement;
- an early, bounded matrix-process feasibility experiment;
- minimal controls needed to prove add/remove/hide/reorder behavior.

Deferred:

- frame-to-time animation conversion;
- sleep, VSync-only, deadline, or unlimited-rate pacing changes;
- automatic backend selection or crossover thresholds;
- a general alpha/mask compositor;
- a broad catalog of equivalent multi-layer scenes;
- one process per analyzer or layer;
- unrelated UI redesign.

## Status

Planning complete. No runtime code, deployment, process topology, or production
baseline changed in this session.

## Records

- [decisions.md](decisions.md) — accepted scope and architecture choices
- [architecture.md](architecture.md) — target ownership and data flow
- [roadmap.md](roadmap.md) — deliberate implementation increments and gates
- [changes.md](changes.md) — files and behavior changed in this session
- [tests.md](tests.md) — documentation checks and evidence limits
- [handoff.md](handoff.md) — next-agent starting point
