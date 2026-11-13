# Audio Sources and Bluetooth Plan

This is a separate delivery track that consumes the audio-source and analyzer
contracts from the FFT/music refactor.

## Product decision

The initial phone mode is **analysis without playback**. A phone connects to the Pi
as an A2DP audio source/sender, and the Pi consumes the decoded PCM for visualization
without routing it to a speaker.

Optional playback through a Pi audio output may be added later. It must be explicit
because it changes routing and synchronization. Forwarding from the Pi to a second
Bluetooth speaker is not an initial goal; it adds another codec/radio path and much
more latency. If playback is desired, a USB DAC, USB speaker, or another verified Pi
audio output is the preferred first target.

The UI must warn that analysis-only Bluetooth causes the phone to route audio to a
silent sink. This is intentional but may surprise users.

## Source abstraction

Both current and future inputs implement the same `AudioSource` interface:

| Source | Capture owner | Initial role |
| --- | --- | --- |
| Dayton iMM-6C USB mic | user-owned audio service via ALSA/PipeWire | ambient/reference source |
| Bluetooth phone | user-owned audio service via PipeWire/BlueZ | direct digital music source |

Source enumeration returns stable IDs, display names, type, availability, sample
formats, latency information where available, and health.

Only one source is active for the first release. Supporting simultaneous mic and
Bluetooth analysis is a later feature and would create separate PCM generations and
compute budgets.

## Why the audio service is separate

The native matrix library starts with elevated hardware access and drops the
visualizer to UID `daemon`. PipeWire and WirePlumber run in the regular user's
session under `/run/user/1000`. The dropped child should not attempt to impersonate
that session or directly control Bluetooth.

A normal-user audio service owns:

- Source discovery and selection.
- BlueZ/PipeWire interaction.
- PCM capture and timestamps.
- PCM ring and analyzer scheduling.
- Source health and reconnect policy.
- Bounded local analysis-frame transport to the renderer.

The matrix renderer remains the exclusive GPIO/panel owner.

## Bluetooth pairing and routing flow

1. An authenticated user opens Audio Sources.
2. They press `Add phone`.
3. The Pi becomes discoverable for a short bounded interval, for example 120 seconds.
4. The page lists pairing requests and displays/requests passkey confirmation when
   the Bluetooth method requires it.
5. On success the user may explicitly trust the phone for reconnect.
6. The controller selects the A2DP sink profile and waits for an incoming stream.
7. PipeWire exposes the incoming media source as input to the audio service.
8. The user explicitly selects that phone as the active music source.
9. A successful source switch increments source generation, rebuilds compatible
   analyzers, and reports effective sample rate/codec/latency metadata.
10. Disconnect returns the source to unavailable. Automatic fallback to the USB mic
    is configurable and visible, never silent magic.

Discoverability is off by default and automatically disabled after pairing or
timeout.

## Web controls

Audio source controls remain separate from visualization layer cards:

- Active source selector.
- Source status: available, connecting, streaming, unavailable, error.
- USB input level and clipping health.
- Paired phone list.
- Add phone / bounded discoverability timer.
- Connect, disconnect, trust/untrust, and forget.
- Codec/sample-rate/channel information.
- Analysis-only warning.
- Optional playback output and volume only after playback is implemented.
- Automatic fallback selector.

Pairing/forget/trust actions require the stronger permanent controller authority if
the existing temporary visualization session is intentionally limited. They should
not be granted merely because a client can change visualization colors.

## PipeWire/BlueZ implementation notes

- Validate against installed BlueZ 5.66, PipeWire 1.2.7, and WirePlumber 0.4.13.
- Do not copy WirePlumber 0.5 configuration syntax into the 0.4 installation.
- Prefer supported D-Bus/PipeWire interfaces and narrow helper operations over
  parsing interactive `bluetoothctl` output.
- Route the incoming media source as an input for the audio service instead of
  playing it by default.
- Keep pairing agent, trust, profile selection, and audio capture responsibilities
  explicit and testable.
- Never expose Bluetooth control D-Bus broadly to the web process; use a narrow
  privileged/local helper if an operation requires it.

## Source switching transaction

1. Request target source with expected current source generation.
2. Probe readiness and negotiate format without stopping the active source when
   possible.
3. Allocate a fresh ring and new source generation.
4. Start target capture and require valid PCM within a timeout.
5. Compile analyzers against the actual rate/format.
6. Atomically publish the new source/analyzer graph.
7. Stop the previous source after successful handoff.
8. On any failure, retain or restore the previous source and report the reason.

The matrix may hold or fade its last frame during handoff. It must not display stale
analysis as if it came from the new source.

## Latency and synchronization

Measure these separately:

- Phone encoding/transmission to decoded PCM on the Pi.
- Decoded PCM to analysis-frame completion.
- Analysis frame to content publication.
- Content publication to visible panel response.
- Optional decoded PCM to Pi audio output.

Analysis-only mode has no local sound reference to synchronize against; report
source-to-display latency as diagnostic information. If Pi playback is added, expose
a bounded visual delay so the picture can align with the sound path. Do not add an
unmeasured fixed delay as a hidden default.

## Bluetooth performance/coexistence tests

The HUB75 driver is sensitive to system load and timing. Test Bluetooth with:

- One and four FFT analyzers.
- Worst-case visible layers and gradients.
- Active Wi-Fi and control-page polling.
- Pair/connect/disconnect while a safe configuration runs.
- 30-minute continuous A2DP stream.
- Codec/sample-rate changes.
- Phone moving out of range and returning.
- Service restart and full Pi reboot.

Pass conditions:

- No native refresh collapse or new visible flicker.
- No unbounded PCM queue growth.
- No sustained audio overruns or analyzer backlog.
- Reconnect/fallback state is accurately represented.
- Pairing state survives only according to explicit trust policy.
- Visualization configuration remains intact across source loss.

## Optional Pi audio-out follow-up

Before adding playback, choose and validate one physical output. The follow-up then:

1. Routes the A2DP stream to that output and taps the same decoded PCM for analysis.
2. Measures output latency and drift.
3. Adds volume/mute and output selection.
4. Adds visual-delay calibration.
5. Tests for conflicts with matrix timing. USB audio is preferred; onboard audio may
   conflict with hardware-pulse use in the matrix driver.

Playback is complete only when it is stable under the same sustained matrix load as
analysis-only mode.

