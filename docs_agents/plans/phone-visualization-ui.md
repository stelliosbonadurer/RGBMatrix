# Phone Visualization Controls Plan

Status: planned; extracted from the completed web-control API milestone

## Goal

Build a responsive phone UI on the existing typed API so a user can select `fft` or
`sorts`, inspect every declared launch parameter, start/switch/stop the visualization,
and understand current or failed state. Preserve all direct CLI entry points.

## Work

1. Generate controls from `matrix_controller` parameter metadata rather than keeping
   a second hand-written parameter list.
2. Add visualization selection, start/stop, supervisor status, validation feedback,
   and reconnect behavior.
3. Decide how the existing bearer token is paired/stored on a phone; plain HTTP is
   acceptable only on a trusted LAN and must not become internet-facing.
4. Add presets only after defining schema and visualization versions.
5. Keep launch-time restart semantics initially. Add low-rate/coalesced IPC only for
   parameters that demonstrably benefit from live updates.

## Acceptance

- A phone can operate every visualization and parameter in the managed registry.
- UI values and validation come from the same schema used to build CLI argv.
- One visualization owns the panel, errors are visible, and reconnecting does not
  accidentally launch a duplicate child.
- CLI behavior remains independent of the web service.

The Wi-Fi-only `/setup` page is already implemented and is not this UI.
