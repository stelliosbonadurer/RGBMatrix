# Bluetooth audio input

The Raspberry Pi can receive phone media over Bluetooth and expose it to the FFT as
the public `bluetooth` source. The phone audio is routed by the desktop user's
PipeWire/WirePlumber session into a persistent ALSA loopback. The matrix service
does not connect to the session bus or PipeWire socket.

This procedure is for the proved Raspberry Pi stack: WirePlumber 0.4.x, PipeWire,
BlueZ, and desktop autologin. It does not make the Pi a speaker, mix sources, or
automatically fall back to the USB mic.

## Install the bridge

Run as the desktop/autologin user, not root:

```bash
cd ~/rgb_Matrix
deploy/bluetooth-audio/install.sh
```

The installer places two narrowly named boot files under `/etc`, installs one
WirePlumber 0.4 Lua rule under the current user's configuration, loads `snd-aloop`
as card 10, and sets the managed loopback sink to the default at 100%, unmuted. It
refuses to overwrite a different file at any managed path. Rerunning it is safe.

When configuration changes, WirePlumber restarts. That restart normally drops the
phone's A2DP stream for roughly 5–50 seconds. If it does not return, reselect the Pi
as the phone's active media output; do not restart the matrix service.

## Pair and manage phones from the web service

The web service can do the pairing below without SSH. On the Music FFT page
(`/programs/fft`), open the **Analyzer** row:

1. Under **Bluetooth phones**, tap **Pair a new phone**. The Pi stays visible to new
   phones for two minutes; the row counts down and names what to pick in the phone's
   Bluetooth settings.
2. When the phone asks to pair, the row shows its name and a six-digit code. Check
   that the phone shows the same code, then tap **Pair** on the page and accept on
   the phone.
3. Tap **Trust** on the new phone, then **Connect**. Pairing alone doesn't let it
   stream.
4. Under **Source**, choose **Bluetooth phone** and press **Launch** (or **Relaunch
   with changes** while FFT runs). Play audio on the phone with the Pi as its output.

Each phone also has **Untrust**, **Disconnect**, and **Forget…** (asks first). These
act on the Pi at once; they aren't part of Send update. The same routes work
without the page:

- `GET /api/v1/bluetooth` returns the adapter state, the pairing window, any pending
  pairing request, and the paired phones under opaque `device_id`s. Addresses are
  never shown.
- `POST /api/v1/bluetooth/actions` with `{"action": "window.open"}` makes the Pi
  discoverable and pairable for 120 seconds. When a phone asks to pair,
  `pending_request` shows its name and six-digit code. Check that the code matches
  the phone, then send `request.confirm` (or `request.reject`) with its
  `request_id`.
- Then send `device.trust` and `device.connect` with the phone's `device_id`.
  `device.disconnect`, `device.untrust`, and `device.forget` (with
  `"confirm": true`) undo them. `window.close` ends the window early. Closing
  leaves the adapter's pairable setting and BlueZ timeouts as the window found
  them.
- `GET /api/v1/audio/sources` reports whether the loopback bridge is loaded, whether
  a paired phone is connected and streaming, and which source the running FFT uses.
  To change the source, relaunch FFT with `audio_source`.

A phone stays untrusted until you trust it. While the window is open, the
service's agent refuses media connections from untrusted phones; outside the window,
whatever agent the Pi already had (for example the desktop panel's) answers. If the
service can't reach BlueZ, the status says why and the
actions return 503; the rest of the web service keeps working. Pairing, trust,
connect, and choosing the source from the page were tried on the Pi with a real
phone.

The list shows every device paired with the Pi, not only phones: a paired mouse or
keyboard appears too. Use **Forget…** on it only if you want it unpaired from the Pi.

## Pair one phone over SSH

Keep identifiers on screen only. Do not paste the phone alias or address into
evidence, issue text, or logs.

```text
ssh <user>@raspberrypi.local
bluetoothctl
[bluetooth]# default-agent
[bluetooth]# discoverable on
```

`discoverable on` uses BlueZ's bounded adapter timeout (180 seconds on the proved
Pi). From the phone, select `raspberrypi` and accept the same confirmation on both
sides. The explicit `default-agent` is required because the desktop panel may own
the prior default agent even when no monitor is attached.

When `bluetoothctl` prints `Request confirmation`, type `yes`. It may then move the
prompt from `[bluetooth]#` into a device submenu. The following commands belong to
the main menu: type `back` first if the prompt is in a device submenu. The console's
`menu <submenu>` command enters a submenu and `back` returns to the main menu.

Still inside `bluetoothctl`, use the address shown by that interactive pairing only:

```text
[bluetooth]# trust <phone-address>
[bluetooth]# connect <phone-address>
[bluetooth]# info <phone-address>
[bluetooth]# discoverable off
[bluetooth]# quit
```

`info` must report `Paired: yes`, `Trusted: yes`, and `Connected: yes`. Turning
discoverability off explicitly closes the window early; the proved Pi's 180-second
adapter timeout remains the upper bound if pairing does not finish. If media is
silent after pairing, select the Pi in the phone's media-output control and start
playback. No Bluetooth scan is needed: the phone discovers the temporarily
discoverable Pi.

## Select and operate the source

The web launch form exposes only these stable values:

- `usb-mic` — default; preserves the existing iMM-6C path.
- `bluetooth` — opens the private loopback capture endpoint.

For a direct foreground launch, add:

```text
--audio-source bluetooth
```

The Bluetooth endpoint is validated before the child initializes the matrix. If the
bridge is absent or ambiguous, launch exits with a bounded error and never falls
back to the microphone. Source changes require relaunching FFT.

The loopback gives both of its sides one format, set by whichever side opens first.
The FFT therefore captures in the bridge's own format (mono, 44.1 kHz, `S32_LE`) and
scales it to the same float range as the USB mic, so the two sides always agree. An
earlier float capture opened before the phone played made the bridge's sink fail to
start, and the panel stayed dark (see "Panel dark while the phone plays" below).
Launching with the phone already playing is proved on the Pi. Launching first, right
after boot and before any phone audio, should work the same but hasn't been seen
working yet.

Turning phone Bluetooth off leaves the persistent capture endpoint open and silent.
After reconnect, the phone may take more than 15 seconds to re-enable the Pi media
output. Reselect it on the phone and relaunch FFT if needed; automatic reopen and
loss handling are intentionally out of scope.

## Status checks

These checks do not scan Bluetooth and should not be saved without redacting phone
identifiers:

```bash
cat /proc/asound/cards
wpctl status
wpctl get-volume @DEFAULT_AUDIO_SINK@
systemctl --user is-active pipewire wireplumber
```

Expected bridge state:

- `snd-aloop` is card 10 and the iMM-6C retains its card number;
- `Bluetooth loopback (RGB Matrix)` is the default sink at `1.00`;
- playback is mono, 44.1 kHz, `S32_LE`;
- PipeWire does not expose/hold the loopback capture side used by FFT; and
- the physical USB microphone remains independently available.

After reboot, desktop autologin starts PipeWire and WirePlumber and the loopback
card returns as card 10. A trusted phone may reconnect, but the user may still need
to reselect the Pi as its media output.

### Panel dark while the phone plays

If the page says the phone is connected but not streaming while it plays, check the
bridge sink. Its id is the number next to `Bluetooth loopback (RGB Matrix)` in
`wpctl status`:

```bash
pw-cli info <sink-id> | grep state
```

A sink in `error` (for example `Start error: Invalid argument`) stays there and
refuses every stream until WirePlumber restarts. To recover:

1. On the FFT page, relaunch with **USB mic** or stop FFT, so nothing holds the
   loopback capture.
2. As the desktop user: `systemctl --user restart wireplumber`.
3. Check that `wpctl status` lists `Bluetooth loopback (RGB Matrix)` as the default
   sink again, reconnect the phone if it dropped, and relaunch with **Bluetooth
   phone**.

Don't restart WirePlumber while FFT runs with **Bluetooth phone**: WirePlumber
probes each card when it starts, and a card it can't open drops out of PipeWire
until the next restart, the loopback included. That holds for rerunning the
installer too. A USB mic run is fine: the iMM-6C drops out of PipeWire the same way,
but FFT reads it directly, and it returns at the next boot.

## Disconnect, forget, or return to USB-only

Disconnect or forget the phone interactively, using its on-screen address:

```text
bluetoothctl
[bluetooth]# disconnect <phone-address>
[bluetooth]# remove <phone-address>
[bluetooth]# quit
```

To remove the bridge while retaining unrelated Bluetooth pairings:

```bash
cd ~/rgb_Matrix
deploy/bluetooth-audio/uninstall.sh
```

The uninstaller clears only the managed default, removes only the three managed
files, stops WirePlumber, unloads `snd-aloop`, and starts WirePlumber again. The
stop → unload → start order is required because WirePlumber holds the loopback
control node even after no PCM stream is active. A cleanup trap starts WirePlumber
if unloading fails. The script refuses to delete a managed file that was modified.
It does not remove phones; use `remove` above when forgetting is intended.

After uninstall, `snd_aloop` and card 10 must be absent, WirePlumber must be active,
and `usb-mic` remains the FFT default.
