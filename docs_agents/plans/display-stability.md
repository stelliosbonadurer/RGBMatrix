# Display stability: tearing, flicker, and hardware PWM

Status: **deferred** — pick up after current FFT feature development is complete.

## Problem

While the FFT program runs, a horizontal tear/band occasionally appears on the 64x64
panel. Separately, the Adafruit Bonnet/HAT documents an optional "quality" mod
(solder bridge between GPIO4 and GPIO18) that we intend to apply.

## Diagnosis so far

Ruled out:

- **Frame-swap tearing.** The FFT program double-buffers: it draws to an offscreen
  canvas and calls `SwapOnVSync` (`my_programs/fft_program/core/matrix_app.py`,
  `swap_canvas`). Swaps occur only between full panel refreshes.
- **Interlaced scan in the FFT program.** `--led-scan-mode` is parsed (default `1`) but
  never assigned to `options.scan_mode`, so the library default (progressive) is used.

Most likely cause: **refresh-thread preemption with software-timed OE pulses.**

- With the `adafruit-hat` mapping, OE (output enable) is on GPIO4, which has no
  hardware PWM, so the library times every brightness pulse in software.
- If Linux preempts the refresh thread mid-scan, the rows lit at that moment are held
  too long/short, producing a brief horizontal band or offset.
- The FFT pipeline (audio callback, NumPy FFT, smoothing) competes for the same cores,
  which matches the intermittent nature of the artifact.

Rule out first: if the tear is only visible through a phone camera, it is rolling-shutter
interference, not a panel problem.

## Hardware PWM mod (GPIO4 ↔ GPIO18)

What it does: routes OE to GPIO18, which has hardware PWM, so pulse timing is done by
the PWM peripheral and is unaffected by CPU scheduling. Enabled by the
`adafruit-hat-pwm` mapping (library leaves GPIO4 as an input).

Benefits:

- Noticeably less flicker and brightness jitter, especially on dim colours and fades.
- Cleaner camera footage (less rolling-shutter banding).
- Headroom to raise refresh rate / lower `led_pwm_lsb_nanoseconds` (currently 130).

Costs / constraints:

- **Onboard Pi audio is unavailable** (shares PWM hardware). Requires
  `dtparam=audio=off` in `/boot/firmware/config.txt` and blacklisting `snd_bcm2835`.
  Acceptable: FFT input is a USB sound device (`config/settings.py`).
- GPIO4 and GPIO18 become reserved. Recovery button on GPIO19
  (`matrix_network/recovery_input.py`) is unaffected.
- Does not reduce CPU load; data shifting is still CPU-driven.
- `--led-no-hardware-pulse` must remain off or the benefit is lost.
- Reversible: remove the bridge and revert to `adafruit-hat`.

## Work items

Ordered cheapest/most reversible first. Validate each on the physical panel before
moving on, noting whether the tear still appears.

1. **Isolate a core for the refresh thread.** Append `isolcpus=3` to the single line in
   `/boot/firmware/cmdline.txt` and reboot. The library pins its refresh thread to
   core 3 automatically. Consider adding this to the portable deployment install
   (see [portable-deployment.md](portable-deployment.md)).
2. **Measure refresh jitter.** Run the FFT program with `--led-show-refresh` and record
   the min/max refresh rate under normal audio load.
3. **Expose and set a refresh cap.** The Python binding supports
   `options.limit_refresh_rate_hz`, but neither `matrix_app.py` nor the registry
   (`matrix_controller/definitions.py`) exposes `--led-limit-refresh`. Add it, then set
   it slightly below the measured minimum.
4. **Apply the GPIO4–GPIO18 mod.** After soldering:
   - Disable onboard audio as above.
   - Set `"led_gpio_mapping": "adafruit-hat-pwm"` in `config/matrix-controller.json`.
   - Update the default in `launch_rgbmatrix.py` and the run commands in
     `my_programs/fft_program/main.py` and `my_programs/fft_program/docs/`.
   - No code change required: `adafruit-hat-pwm` is already an accepted choice in
     both `definitions.py` and `matrix_app.py`.
5. **Optionally lower `led_pwm_bits`** (11 → 8 or 9) if artifacts remain; shorter
   refresh cycles give preemption less to hit. Colour-depth loss is minor for bars.
6. **Fix scan-mode inconsistency.**
   - FFT `matrix_app.py` parses `--led-scan-mode` but never applies it (bug).
   - DSA visualizations *do* apply it (`my_programs/dsa_visualizations/native_matrix.py`),
     and `config/matrix-controller.json` sets `"led_scan_mode": 1`, so those programs
     currently run **interlaced** while FFT runs progressive.
   - Decide on progressive (`0`) as the shared default, set it in config, apply it in
     `matrix_app.py`, and correct the misleading "(default)" help text.

## Done when

- No horizontal tear observed over a sustained (≥15 min) FFT session with music.
- Refresh rate is stable under load (documented min/max).
- All programs use the same scan mode and GPIO mapping from config.
