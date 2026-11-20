# Renderer process and pacing roadmap — 2026-11-19

## Objective

Plan the next FFT visualizer increments around three questions:

1. How should animation preserve the same real-time behavior when frame intervals
   vary?
2. Should the render and matrix loop move into its own process?
3. Should the fixed five-millisecond sleep be replaced by VSync-only pacing?

The existing `direct` and `pillow-bulk` output paths remain manually selectable.
Direct remains the default. This roadmap does not investigate automatic selection,
the layer-count crossover, or equivalent multi-layer implementations.

## Key pacing conclusion

With the current double-buffered driver, `SwapOnVSync` is a blocking call. Therefore
the safe “as fast as the display can accept frames” mode is the same practical mode
as display-clock pacing:

```text
render -> SwapOnVSync -> render -> SwapOnVSync
```

It removes the application sleep but still waits for a clean panel refresh boundary.
The separate analysis process does not wait on the display. A matrix loop with no
wait of any kind would require a different native API, live-canvas writes, or another
output stage; it is outside the current roadmap.

## Baseline

- Repository HEAD: `6d0b6b8de0484ac18b4dad9ef49f8ffa73671154`.
- Accepted output default: direct native-canvas rendering.
- Retained alternative: `pillow-bulk`, selected at startup.
- Current render loop calls `SwapOnVSync` and then sleeps for five milliseconds.
- Analysis and rendering currently run as threads in one Python process.
- Step 6 measured about 104.3 direct swap FPS and about 101.9 Pillow swap FPS under
  one sparse bar workload; no new output-path comparison is planned here.

## Scope

This session changes documentation and folder names only. The implementation roadmap
is limited to baseline reconciliation, time-based temporal effects, a dedicated
renderer/matrix process, VSync-only pacing, and a bounded hardware comparison.

## Status and records

The roadmap is ready for review. Begin only with Step 0 in
[roadmap.md](roadmap.md).

- [decisions.md](decisions.md) — accepted direction and explicit deferrals.
- [architecture.md](architecture.md) — proposed process ownership and pacing model.
- [changes.md](changes.md) — documentation and folder changes.
- [tests.md](tests.md) — checks and limits.
- [handoff.md](handoff.md) — the next safe action.
