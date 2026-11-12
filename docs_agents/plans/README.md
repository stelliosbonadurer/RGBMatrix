# Active Plans Index

This folder contains only unfinished work. Completed implementation plans are kept,
in historical implementation order, under [`../plans_archvie/`](../plans_archvie/README.md).
The archive directory intentionally retains the requested `plans_archvie` spelling.

## Current order

1. [Graph algorithms follow-up](graph-algorithms-next.md) — add full connected-component
   traversal, separate Prim and Kruskal modes, and synchronized 32x64 + 32x64 BFS/DFS
   and Prim/Kruskal comparisons on the same seeded graph.
2. [Master Theorem visualization](master-theorem-visualization.md) — initial 64x64
   recurrence-tree implementation is complete; validate it in the classroom and on
   the physical panel, then refine its visual language.
3. [Live visualization interaction](live-visualization-interaction.md) — the typed
   FFT phone-control MVP is implemented; finish named presets and safe dynamic layer
   composition on the established runtime protocol.
4. [Portable Raspberry Pi deployment](portable-deployment.md) — produce a repeatable
   native install for explicitly supported Pi models before reconsidering containers.
5. [Web visualization emulator](web-visualization-emulator.md) — execute the existing
   Python renderers against a software matrix and stream frames to a browser preview.
6. [Runtime modernization](runtime-modernization.md) — continue removing production
   dependence on the upstream demo layout and address duplicate FFT work when useful.
7. [Network recovery hardening](network-recovery-hardening.md) — physically validate
   LAN-to-AP fallback and add a recovery input before treating failure recovery as
   field-proven.

Current FFT latency is accepted as sufficient. Performance measurement is not a gate
unless a new Pi or packaging method visibly regresses.

## Shared boundaries

The visualization registry and typed CLI contract are already implemented. The web
service consumes that allowlist, while the privileged network helper is a separate
NetworkManager-only service. Portability work owns installation of both services but
must not merge their privilege boundaries.

Live interaction should establish the shared runtime state/control contract before
the emulator grows an interactive preview. The emulator can then reuse the same
controller and schema rather than developing a parallel set of browser controls.
