# Changes

Documentation only. No application code, binding, configuration, test, deployment,
service, or Raspberry Pi state changed.

## Roadmap scope

- Removed automatic output selection and crossover investigation.
- Removed multi-layer equivalence and layer-scaling work.
- Centered the roadmap on time-based animation, a renderer/matrix child process, and
  fixed-sleep versus VSync-only pacing.
- Defined VSync-only as the current safe “as fast as possible” mode.
- Deferred the GIL patch because process isolation removes analyzer/render GIL
  contention.

## Session folder chronology

Renamed dated session folders to `YYYY-MM-DD-N-short-topic` and updated known
repository cross-links. Updated the root `AGENTS.md` convention.
