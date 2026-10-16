# Agent Guide

This directory is the fast-start context for agents working on this repository. It describes the code as it exists, including places where older project documentation has drifted from the implementation.

Read in this order:

1. [repository-map.md](repository-map.md) — what is active, legacy, generated, or local-only.
2. [architecture.md](architecture.md) — runtime data flow and component ownership.
3. [development.md](development.md) — setup, commands, validation, and safe change patterns.
4. [known-issues.md](known-issues.md) — verified inconsistencies and traps to check before editing.
5. [managed-visualization-inventory.md](managed-visualization-inventory.md) — the
   reviewed web allowlist, CLI/service modes, and current MVP boundary.

Implemented and future work is tracked under [`plans/`](plans/README.md). Read the
plan index before continuing a milestone because it records the selected order,
completed MVP boundary, deferred work, and shared contracts.

## Project in one paragraph

This is a Raspberry Pi/HUB75 RGB-matrix playground whose primary application is the modular real-time audio visualizer in `my_programs/fft_program/`. Audio arrives through `sounddevice`, NumPy converts it to logarithmic FFT bars, scaling smooths and normalizes the values, themes choose colors, and the `rgbmatrix` native binding writes a double-buffered frame to the panel. `matrix_controller/` now provides a hardware-independent allowlist, typed launch parameters, exclusive subprocess supervisor, and HTTP API for the FFT and sorting visualizations. `launch_rgbmatrix.py` remains the broader interactive developer launcher.

## Sources of truth

- Runtime behavior: the Python source, especially `my_programs/fft_program/main.py`.
- Defaults and JSON schema: `my_programs/fft_program/config/settings.py`.
- Hardware option wiring: `my_programs/fft_program/core/matrix_app.py` and `launch_rgbmatrix.py`.
- Managed/API parameter contract: `matrix_controller/definitions.py` and
  `matrix_controller/schema.py`.
- Web-service behavior and operations: [web-service-operations.md](web-service-operations.md).
- Detailed visualizer explanations: `my_programs/fft_program/docs/`, but verify claims against code; some pages describe removed CLI flags, old defaults, or intended behavior.
- Deferred portability/network/container work: [`plans/`](plans/README.md).

## Scope discipline

Visualization internals belong under `my_programs/`; shared launch/API policy belongs
under `matrix_controller/`. Do not update `viz2016version/` or unrelated standalone
scripts merely to keep them stylistically aligned. Do not edit the local
`rpi-rgb-led-matrix/` tree as if it were tracked project code: the entire directory is
ignored by this repository.
