# Phone Visualization Controls

Status: principal milestone completed 2026-10-21

## Delivered boundary

The responsive controller at `/` now lets a paired phone inspect, launch, switch,
restart, and stop every visualization in the managed registry. Forms are generated
from `ParameterSpec` metadata, so boolean, enum, integer, float, and string controls
use the same names, defaults, choices, and numeric bounds as API validation and fixed
argv construction.

Application settings are shown first and native matrix settings are kept in a
separate collapsible hardware section. Active settings seed the form when restarting
the current visualization. Launch errors remain visible without bypassing the
supervisor's validate-before-stop behavior.

Six-digit boot pairing exchanges for a scoped, in-memory control session stored in
the browser tab. Temporary sessions can operate visualizations but cannot administer
networking. The page polls status, highlights the active child, and retains the
existing authenticated Quit action.

## Launch configuration added with this milestone

`config/matrix-controller.json` is a strict version-one administrator file covering:

- visualization interpreter, sudo use, and stop timeout;
- boot visualization selection;
- the complete shared native matrix parameter contract; and
- FFT and sorting defaults.

`controller_config.py` rejects unknown structure, invalid types/ranges, and unknown
versions before the listener starts. Configuration changes only reviewed defaults;
it cannot provide scripts, shell commands, command prefixes, or arbitrary argv. An
explicit web request can override exposed parameters for one launch but never rewrites
the file.

## Preserved behavior

- Direct CLI entry points remain independent.
- One supervised process owns the panel at a time.
- Switching completely stops and reaps the previous process group.
- Wi-Fi setup remains a separate `/setup` workflow and privilege boundary.
- Parameters still take effect at process launch; there is no synthetic keyboard
  input or hidden live-control channel.

## Deferred enhancements

Presets, durable browser identity, TLS, and low-rate frame-boundary control IPC are
optional follow-ups. They are not required for the completed select/configure/start/
switch/stop milestone and should not weaken the typed registry boundary.
