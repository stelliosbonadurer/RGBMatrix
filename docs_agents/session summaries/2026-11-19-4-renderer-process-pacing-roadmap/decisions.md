# Decisions

## D-001 — retain two manually selected output paths

- `direct` remains the default.
- `pillow-bulk` remains available for its currently supported scene.
- This roadmap does not add automatic selection or try to find a layer-count
  crossover.

## D-002 — defer multi-layer equivalence

- Do not build equivalent multi-layer scenes merely to compare output paths.
- Preserve the future possibility without making it a dependency of pacing or
  process isolation.

## D-003 — make animation time-based before changing cadence

- Rise/fall, scaling attack/decay, peak hold/fall, and applicable shadow decay must
  represent elapsed time before the fixed sleep is removed.
- Preserve current behavior at an explicit reference interval.

## D-004 — give the renderer process exclusive matrix ownership

- The parent owns audio, analysis, controls, and supervision.
- The child owns temporal display state, output implementation, native matrix and
  canvas, and VSync.
- Use latest-value shared memory for high-rate numeric data and a separate low-rate
  control channel.

## D-005 — define “as fast as possible” as VSync-only

- Remove the explicit five-millisecond sleep and begin the next frame after
  `SwapOnVSync` returns.
- The native VSync wait remains; it is required for tear-free double buffering.

## D-006 — defer the GIL patch under process isolation

- Releasing the GIL during `SwapOnVSync` is no longer a prerequisite when analysis
  and rendering live in different processes.
- Revisit it only if the renderer process later contains Python work that must run
  concurrently during the native wait.

## D-007 — number session folders chronologically

- Session folders use `YYYY-MM-DD-N-short-topic`.
- `N` starts at 1 each day and increases in creation order.
- Existing dated folders were renamed and their known cross-references updated.

## D-008 — retain stop/go boundaries

- Time conversion, process isolation, pacing, and hardware promotion remain separate
  decisions.
- Approval for one step does not authorize the next or promote its result.
