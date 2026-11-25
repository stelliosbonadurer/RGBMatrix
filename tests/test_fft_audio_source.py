"""Hardware-free tests for public FFT audio-source resolution."""

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType
import unittest


FFT_ROOT = Path(__file__).resolve().parent.parent / "my_programs" / "fft_program"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))

_sounddevice = ModuleType("sounddevice")


class _PortAudioError(Exception):
    pass


_sounddevice.PortAudioError = _PortAudioError
_sounddevice.query_devices = lambda *_args, **_kwargs: {
    "name": "test input",
    "max_input_channels": 1,
    "default_samplerate": 44_100,
}
_sounddevice.InputStream = object
_prior_sounddevice = sys.modules.get("sounddevice")
sys.modules["sounddevice"] = _sounddevice
try:
    from config.settings import AudioSettings  # noqa: E402
    from core.audio_source import (  # noqa: E402
        AudioSourceResolutionError,
        resolve_audio_source,
    )
finally:
    if _prior_sounddevice is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = _prior_sounddevice


def device(name: str = "resolved input", *, channels: int = 1, rate: int = 44_100):
    return {
        "name": name,
        "max_input_channels": channels,
        "default_samplerate": rate,
    }


class AudioSourceResolutionTests(unittest.TestCase):
    def test_main_resolves_before_matrix_and_analyzer_construction(self):
        source = (FFT_ROOT / "main.py").read_text(encoding="utf-8")
        resolve = source.index("settings.audio = resolve_audio_source(")
        matrix = source.index("if not app.initialize_matrix():")
        analyzer = source.index("analyzer_registry = AnalyzerRegistry(")
        self.assertLess(resolve, matrix)
        self.assertLess(matrix, analyzer)

    def test_usb_preserves_configured_selector_and_records_effective_format(self):
        calls = []

        def query(selector, kind):
            calls.append((selector, kind))
            return device("iMM-6C resolved")

        original = AudioSettings(device="iMM-6C")
        resolved = resolve_audio_source("usb-mic", original, query_devices=query)

        self.assertEqual(calls, [("iMM-6C", "input")])
        self.assertEqual(resolved.device, "iMM-6C")
        self.assertEqual(resolved.source_id, "usb-mic")
        self.assertEqual(resolved.resolved_device, "iMM-6C resolved")
        self.assertEqual(resolved.resolved_sample_rate, 44_100)
        self.assertEqual(resolved.capture_channels, 1)
        self.assertEqual(resolved.capture_dtype, "float32")
        self.assertIsNone(original.resolved_device)

    def test_bluetooth_uses_private_loopback_without_falling_back_to_usb(self):
        calls = []

        def query(selector, kind):
            calls.append((selector, kind))
            return device("Loopback capture")

        resolved = resolve_audio_source(
            "bluetooth",
            AudioSettings(device="iMM-6C"),
            query_devices=query,
        )

        self.assertEqual(calls, [("hw:10,1", "input")])
        self.assertEqual(resolved.device, "hw:10,1")
        self.assertEqual(resolved.source_id, "bluetooth")
        self.assertEqual(resolved.capture_channels, 1)
        self.assertEqual(resolved.capture_dtype, "int32")

    def test_bluetooth_capture_matches_the_bridge_playback_format(self):
        # snd-aloop makes the second side to open match the first, so the FFT
        # must ask for the bridge's own format, or a launch before the phone
        # plays leaves PipeWire's sink failing to start.
        rule = (
            Path(__file__).resolve().parent.parent
            / "deploy" / "bluetooth-audio" / "files"
            / "60-rgb-matrix-bluetooth-audio.lua"
        ).read_text(encoding="utf-8")
        self.assertIn('["audio.format"] = "S32LE"', rule)
        self.assertIn('["audio.channels"] = 1', rule)
        resolved = resolve_audio_source(
            "bluetooth",
            AudioSettings(),
            query_devices=lambda *_args: device("Loopback capture"),
        )
        self.assertEqual(resolved.capture_dtype, "int32")  # S32LE
        self.assertEqual(resolved.capture_channels, 1)

    def test_missing_or_ambiguous_bluetooth_is_a_public_bounded_error(self):
        def query(_selector, _kind):
            raise ValueError("private device list")

        with self.assertRaisesRegex(
            AudioSourceResolutionError,
            "bluetooth audio input is unavailable or ambiguous",
        ) as caught:
            resolve_audio_source(
                "bluetooth",
                AudioSettings(),
                query_devices=query,
            )
        self.assertNotIn("hw:10,1", str(caught.exception))
        self.assertNotIn("iMM-6C", str(caught.exception))

    def test_portaudio_host_failure_is_a_public_bounded_error(self):
        def query(_selector, _kind):
            raise _PortAudioError("private host failure")

        with self.assertRaisesRegex(
            AudioSourceResolutionError,
            "usb-mic audio input is unavailable or ambiguous",
        ) as caught:
            resolve_audio_source(
                "usb-mic",
                AudioSettings(),
                query_devices=query,
            )
        self.assertNotIn("private host failure", str(caught.exception))

    def test_non_input_and_invalid_rate_are_rejected(self):
        for info in (device(channels=0), device(rate=0)):
            with self.subTest(info=info), self.assertRaises(
                AudioSourceResolutionError
            ):
                resolve_audio_source(
                    "usb-mic",
                    AudioSettings(),
                    query_devices=lambda *_args: info,
                )

    def test_unknown_public_source_is_rejected(self):
        with self.assertRaisesRegex(AudioSourceResolutionError, "unsupported"):
            resolve_audio_source("phone-name", AudioSettings())


if __name__ == "__main__":
    unittest.main()
