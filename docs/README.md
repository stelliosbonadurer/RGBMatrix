# Agent Guide

This directory is the fast-start context for agents working on this repository. It describes the code as it exists, including places where older project documentation has drifted from the implementation.

Read in this order:

1. [repository-map.md](repository-map.md) — what is active, legacy, generated, or local-only.
2. [architecture.md](architecture.md) — runtime data flow and component ownership.
3. [development.md](development.md) — setup, commands, validation, and safe change patterns.
4. [known-issues.md](known-issues.md) — verified inconsistencies and traps to check before editing.

## Project in one paragraph

This is a Raspberry Pi/HUB75 RGB-matrix playground whose current application is the modular real-time audio visualizer in `my_programs/fft_program/`. Audio arrives through `sounddevice`, NumPy converts it to logarithmic FFT bars, scaling smooths and normalizes the values, themes choose colors, and the `rgbmatrix` native binding writes a double-buffered frame to the panel. `launch_rgbmatrix.py` discovers runnable scripts and supplies common 64x64 Adafruit HAT arguments. The repository also contains older standalone demos, 2016 experiments, a large Conway's Life pattern corpus, and a local ignored checkout of the upstream matrix driver.

## Sources of truth

- Runtime behavior: the Python source, especially `my_programs/fft_program/main.py`.
- Defaults and JSON schema: `my_programs/fft_program/config/settings.py`.
- Hardware option wiring: `my_programs/fft_program/core/matrix_app.py` and `launch_rgbmatrix.py`.
- Detailed visualizer explanations: `my_programs/fft_program/docs/`, but verify claims against code; some pages describe removed CLI flags, old defaults, or intended behavior.
- Future provisioning work: `documentation/Plans-Todo/pi_provisioning_and_golden_image_plan.md`; it is a plan, not implemented setup.

## Scope discipline

Unless a task says otherwise, make current visualizer changes under `my_programs/fft_program/`. Do not update `viz2016version/` or the standalone scripts merely to keep them stylistically aligned. Do not edit the local `rpi-rgb-led-matrix/` tree as if it were tracked project code: the entire directory is ignored by this repository.
