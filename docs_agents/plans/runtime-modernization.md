# Runtime Modernization Plan

Status: deferred follow-up to the archived runtime architecture decision

## Goal

Keep Python for orchestration, FFT, and visualization logic while making production
applications independent of the upstream demo-directory layout. Move native only
when a measured target-device problem justifies it.

## Remaining work

1. Define a small project-owned application lifecycle beyond the current launch
   adapters and migrate additional visualizations only when they enter the allowlist.
2. Pin/vendor the `rpi-rgb-led-matrix` dependency through the portable deployment
   plan rather than relying on an ignored local checkout.
3. Remove the duplicate rFFT performed in layered FFT mode before considering a
   language rewrite.
4. Add fake-canvas coverage for renderer output, teardown, and lifecycle behavior.
5. Keep live control IPC optional and separate from the baseline CLI contract.

Current FFT latency is acceptable and requires no benchmark gate.
