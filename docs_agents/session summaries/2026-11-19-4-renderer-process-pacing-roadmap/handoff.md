# Handoff

## Final state

The roadmap is limited to time-based animation, a renderer/matrix child process, and
fixed-sleep versus VSync-only pacing. Direct remains the default and Pillow remains a
manual option, but auto-selection, crossover thresholds, and multi-layer parity are
not active work.

All dated session folders now carry a one-based per-day sequence number. Known links
and the root convention were updated.

No runtime code, test, binding, Pi, service, or deployment state changed.

## Smallest useful next step

Start Step 0 only:

1. classify the existing dirty and untracked files;
2. record local and Pi source and binary hashes;
3. reconcile the installed output and binding identities;
4. decide whether qualification instrumentation remains reusable;
5. update this folder with the baseline decision and stop.

## Following decision

After Step 0 closes, approve the exact reference interval and long-pause behavior for
time-based effects. That choice controls the offline equivalence tests in Step 1.

## Risks to carry forward

- Moving matrix ownership changes startup, privilege, signal, and failure behavior.
- Runtime controls need an explicit acknowledged child-process boundary.
- Shared memory must never expose a partially published numeric frame.
- VSync-only pacing may increase renderer CPU even though display refresh remains
  bounded.
- Time-based conversion can preserve equations numerically yet still need visual
  tuning with representative live music.
