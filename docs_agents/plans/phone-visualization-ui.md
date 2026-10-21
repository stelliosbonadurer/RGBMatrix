# Phone Visualization Controls Plan

Status: in progress; catalog/status/stop MVP implemented

## Goal

Build a responsive phone UI on the existing typed API so a user can select `fft` or
`sorts`, inspect every declared launch parameter, start/switch/stop the visualization,
and understand current or failed state. Preserve all direct CLI entry points.

## Work

Implemented first slice:

- responsive `/` control page generated from visualization metadata;
- current supervisor state with three-second foreground polling;
- active visualization highlighting and authenticated Quit action;
- temporary control token retained only in browser `sessionStorage`;
- boot-scoped six-digit phone pairing, shown with SSID and URL on the matrix and
  available through `python3 -m matrix_controller.boot_code`;
- reusable-within-boot code exchange with throttling, four-hour visualization-only
  sessions, logout, and session revocation on service restart;
- fixed static asset routes with a restrictive content security policy.

Remaining work:

1. Generate controls from `matrix_controller` parameter metadata rather than keeping
   a second hand-written parameter list.
2. Add visualization launch/switch controls, parameter validation feedback, and
   reconnect behavior. Status and stop are complete.
3. Pairing is implemented. Revisit persistent device identity only if users need
   authorization to survive tab closure or service restarts. Plain HTTP remains
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
