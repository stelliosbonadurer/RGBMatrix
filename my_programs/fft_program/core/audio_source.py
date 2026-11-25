"""Resolve public FFT audio-source IDs to private capture devices."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

import sounddevice as sd  # type: ignore

from config.settings import AudioSettings


USB_MIC_SOURCE = "usb-mic"
BLUETOOTH_SOURCE = "bluetooth"
AUDIO_SOURCE_IDS = (USB_MIC_SOURCE, BLUETOOTH_SOURCE)

# The deployment pins snd-aloop to card 10. This selector remains private to the
# FFT child; the controller and browser exchange only the public source IDs above.
_BLUETOOTH_DEVICE_SELECTOR = "hw:10,1"
_CAPTURE_CHANNELS = 1
_CAPTURE_DTYPE = "float32"
# snd-aloop gives both halves of the cable one format: whichever side opens first
# sets it and the other must match. The bridge pins its playback side to mono
# 44.1 kHz S32LE (deploy/bluetooth-audio), so the FFT captures int32 too (mono, at
# the device's default rate, which PortAudio reports as 44100 for the loopback). A
# float32 capture opened before the phone plays made PipeWire's sink fail to
# start ("Invalid argument") and the panel stayed dark. AudioProcessor scales
# int32 to the same float32 range as USB.
_BLUETOOTH_CAPTURE_DTYPE = "int32"


class AudioSourceResolutionError(RuntimeError):
    """Raised before matrix initialization when a selected input is unusable."""


DeviceQuery = Callable[[Any, str], Any]
_portaudio_error = getattr(sd, "PortAudioError", None)
_QUERY_ERRORS = (TypeError, ValueError) + (
    (_portaudio_error,) if isinstance(_portaudio_error, type) else ()
)


def resolve_audio_source(
    source_id: str,
    audio_settings: AudioSettings,
    *,
    query_devices: DeviceQuery | None = None,
) -> AudioSettings:
    """Return settings pinned to one validated input device and native rate.

    ``sounddevice.query_devices`` requires a unique match for a string selector.
    That gives both public sources the same unavailable/ambiguous failure boundary
    without exposing the private ALSA selector through the controller contract.
    """

    if source_id not in AUDIO_SOURCE_IDS:
        raise AudioSourceResolutionError(f"unsupported audio source: {source_id!r}")

    selector = (
        audio_settings.device
        if source_id == USB_MIC_SOURCE
        else _BLUETOOTH_DEVICE_SELECTOR
    )
    query = sd.query_devices if query_devices is None else query_devices
    try:
        device_info = query(selector, "input")
    except _QUERY_ERRORS as exc:
        raise AudioSourceResolutionError(
            f"{source_id} audio input is unavailable or ambiguous"
        ) from exc

    try:
        device_name = str(device_info["name"])
        input_channels = int(device_info["max_input_channels"])
        sample_rate = int(device_info["default_samplerate"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise AudioSourceResolutionError(
            f"{source_id} audio input reported an invalid format"
        ) from exc

    if not device_name or input_channels < _CAPTURE_CHANNELS or sample_rate <= 0:
        raise AudioSourceResolutionError(
            f"{source_id} audio input does not support mono capture"
        )

    return replace(
        audio_settings,
        device=selector,
        source_id=source_id,
        resolved_device=device_name,
        resolved_sample_rate=sample_rate,
        capture_channels=_CAPTURE_CHANNELS,
        capture_dtype=(
            _CAPTURE_DTYPE if source_id == USB_MIC_SOURCE else _BLUETOOTH_CAPTURE_DTYPE
        ),
    )
