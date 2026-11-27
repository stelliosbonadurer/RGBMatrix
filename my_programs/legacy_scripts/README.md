# Legacy FFT scripts

Standalone scripts from before the modular FFT program (`my_programs/fft_program`).
They are kept for reference only: nothing imports or tests them, and the web
controller doesn't list them. `launch_rgbmatrix.py --list` still finds them (it scans
`my_programs/` recursively), as `my_programs.legacy_scripts.<name>`. They keep their own hard-coded constants, so
changes to the FFT program (for example the normalized spectrum and dBFS noise
floor) don't apply to them.

| Script | What it was |
| --- | --- |
| `fft_terminal.py` | First prototype: spectrum bars in a terminal (blessed). |
| `fft_matrix.py` | First panel version, run from the Pi's rpi-rgb-led-matrix samples directory. |
| `fft_sandbox.py` | The monolithic panel script that `fft_program` was refactored from (see `fft_program/docs/optimization_notes.md`). |

`fft_matrix.py` and `fft_sandbox.py` import `samplebase` from the rpi-rgb-led-matrix
Python samples, which isn't in this repository.
