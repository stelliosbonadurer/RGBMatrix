# Current System Audit

This audit records the evidence behind the refactor. The Pi inspection was read-only:
no packages, services, configuration, pairings, or visualizations were changed.

## Hardware and deployed environment

- Raspberry Pi 4 Model B Rev 1.5.
- Debian 12 Bookworm, aarch64, kernel `6.12.25+rpt-rpi-v8`.
- Four Cortex-A72 cores at 1.8 GHz, performance governor.
- 3.7 GiB RAM, approximately 2.6 GiB available during inspection.
- Temperature 57.4 C and no throttling (`throttled=0x0`).
- CPU 3 is isolated and used by the native matrix refresh thread.
- One configured 64x64 panel, one chain, `adafruit-hat`, GPIO slowdown 2,
  11 PWM bits, 130 ns LSB.
- The exact physical HAT/Bonnet revision cannot be proven remotely. If the
  GPIO4-to-GPIO18 quality jumper is installed, `adafruit-hat-pwm` is likely the
  appropriate mapping and must be physically verified before changing it.

The deployed repository was on commit
`7be139cde2fd4d36635a863855588705e786d0b8`; the audited FFT and controller files
matched that commit. The native driver checkout has no Git metadata, so its upstream
revision is unknown. Production currently depends on a virtual environment inside
the upstream `bindings/python/samples/fftdemo` tree.

## Current audio path

- Dayton Audio iMM-6C USB input, USB ID `0d8c:0147`.
- ALSA card 3, mono, `S24_3LE`, 44.1 kHz.
- 512-frame period and 2048-frame buffer.
- A 512-sample observation at 44.1 kHz is 11.61 ms, not the approximately 23 ms
  described in the current source comment.
- The application windows only those 512 real samples and zero-pads to 8192 before
  `np.fft.rfft`. Zero-padding creates a denser sampled frequency grid; it does not
  provide the resolving power of 8192 observed samples.

## Current data-flow violations

The target invariants are not satisfied today:

- The callback replaces a single `latest_samples` array and sets a persistent
  `have_data` flag. It does not attach a sequence number or mark a block consumed.
- The render loop can therefore analyze the same microphone block several times.
- The render loop always calls `get_fft_magnitudes()` and, in layered mode, also
  calls `get_layer_magnitudes()`.
- Both methods call `np.fft.rfft`, so current layered mode performs two FFTs per
  render iteration rather than one FFT per new audio hop.
- Visual layers, audio bin mappings, renderer states, scalers, and draw order are
  maintained as separate index-aligned collections. This does not safely support
  structural add/remove/duplicate operations.

Relevant source locations:

- [`main.py`](../my_programs/fft_program/main.py) around line 623: unconditional
  single-range FFT followed by the optional layered FFT.
- [`audio.py`](../my_programs/fft_program/core/audio.py) around line 195: layered
  FFT calculation.
- [`audio.py`](../my_programs/fft_program/core/audio.py) around line 326: latest
  sample block callback without a sequence/consumption boundary.
- [`bars_unified.py`](../my_programs/fft_program/visualizers/bars_unified.py) around
  line 308: bottom-to-top layer drawing in which later layers overwrite earlier
  pixels.

## Measured compute use

The current live mode was single-layer, 100-6300 Hz, ocean theme, gradient off,
overflow on. A five-second thread sample measured:

| Work | Utilization of one core |
| --- | ---: |
| Python main/render thread | 37.5% |
| Native matrix refresh thread | 44.0% |
| Audio callback thread | 1.0% |
| Total process | 82.5% |

The process RSS was approximately 57 MiB. Total use was about 20.6% of the Pi's
four-core capacity.

A low-priority benchmark on another application core measured:

| Operation | Mean time |
| --- | ---: |
| 8192-point `np.fft.rfft` | 0.240 ms |
| `rfft` plus magnitude | 0.276 ms |

At the current 512-sample hop, approximately 86.1 new blocks arrive each second.
One default analyzer would therefore spend roughly 2.4% of one core in
`rfft + abs`; four distinct default analyzers would spend roughly 9.5% of one core
in the transforms alone. Windowing, projection, scaling, rendering, IPC, and native
refresh are additional costs.

Conclusion: several FFT analyzers are feasible on this Pi, but FFT is not the main
cost. Rendering and continuous panel refresh deserve the same or greater attention.

## Current control behavior

The runtime backend already supports revisioned, validate-before-apply command
batches. The dedicated browser page does not use that capability effectively:

- Every checkbox, select, or number field submits immediately.
- A request contains only one changed field.
- All generated controls are disabled while the request is pending.
- The control tree is recreated after the response.
- Groups default open, resulting in a long phone page.

The refactor can preserve the authenticated HTTP/supervisor/private-socket security
boundary while replacing immediate field submission with one configuration draft and
one atomic Apply command.

## Current persistence defect

The matrix library drops the visualization child to UID `daemon`. The configured
state directory `/var/lib/rgb-matrix-controller` is owned by the normal controller
user and is mode `0750`. The FFT child repeatedly fails to create
`runtime_state.json.tmp`; 261 permission warnings were observed in roughly nine
minutes.

Durable analyzer favorites, layer templates, complete configurations, and boot
defaults must therefore be owned by the controller or another user-owned service.
The renderer should receive only the validated applied configuration and ephemeral
runtime state.

## Bluetooth readiness

The Pi already has:

- A powered and pairable Broadcom Bluetooth controller.
- BlueZ 5.66 active and enabled.
- PipeWire 1.2.7, `pipewire-pulse`, WirePlumber 0.4.13, and Bluetooth SPA support.
- Registered A2DP sink and source endpoints.

It does not yet have:

- Discoverability enabled.
- A paired phone.
- A connected Bluetooth audio node.
- Measured A2DP or phone-to-display latency.

PipeWire runs in the `stelliosbonadurer` user session, while the matrix renderer is
dropped to UID `daemon`. A user-owned audio service is therefore the correct owner
for Bluetooth PCM. It can publish bounded PCM or analysis frames to the renderer by
local IPC or shared memory.

## Authoritative external references

- [Adafruit RGB Matrix Bonnet guide](https://learn.adafruit.com/adafruit-rgb-matrix-bonnet-for-raspberry-pi?view=all)
- [`rpi-rgb-led-matrix` driver and CPU guidance](https://github.com/hzeller/rpi-rgb-led-matrix)
- [Python binding performance guidance](https://github.com/hzeller/rpi-rgb-led-matrix/blob/master/bindings/python/README.md#performance)
- [NumPy `rfft` zero-padding behavior](https://numpy.org/doc/stable/reference/generated/numpy.fft.rfft.html)
- [Raspberry Pi OS Bookworm audio/PipeWire changes](https://www.raspberrypi.com/news/bookworm-the-new-version-of-raspberry-pi-os/)
- [WirePlumber Bluetooth roles](https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/bluetooth.html)

