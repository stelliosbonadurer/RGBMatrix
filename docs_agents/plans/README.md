# Active Plans Index

This folder contains only unfinished work. Completed implementation plans are kept,
in historical implementation order, under [`../plans_archvie/`](../plans_archvie/README.md).
The archive directory intentionally retains the requested `plans_archvie` spelling.

## Current order

1. [Portable Raspberry Pi deployment](portable-deployment.md) — produce a repeatable
   native install for explicitly supported Pi models before reconsidering containers.
2. [Phone visualization controls](phone-visualization-ui.md) — add the mobile picker,
   generated parameter controls, start/stop state, and useful errors on top of the
   existing API.
3. [Web visualization emulator](web-visualization-emulator.md) — execute the existing
   Python renderers against a software matrix and stream frames to a browser preview.
4. [Runtime modernization](runtime-modernization.md) — continue removing production
   dependence on the upstream demo layout and address duplicate FFT work when useful.
5. [Network recovery hardening](network-recovery-hardening.md) — physically validate
   LAN-to-AP fallback and add a recovery input before treating failure recovery as
   field-proven.

Current FFT latency is accepted as sufficient. Performance measurement is not a gate
unless a new Pi or packaging method visibly regresses.

## Shared boundaries

The visualization registry and typed CLI contract are already implemented. The web
service consumes that allowlist, while the privileged network helper is a separate
NetworkManager-only service. Portability work owns installation of both services but
must not merge their privilege boundaries.
