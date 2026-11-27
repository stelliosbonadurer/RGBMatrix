"""The one-time preset conversion to gain (fft-level-controls D-020-D-022, M1 AC 6)."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import ModuleType
import unittest
import unittest.mock

try:
    import numpy  # noqa: F401
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("FFT preset conversion tests require NumPy") from exc

FFT_ROOT = Path(__file__).resolve().parent.parent / "my_programs" / "fft_program"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))
_prior_sounddevice = sys.modules.get("sounddevice")
sys.modules["sounddevice"] = ModuleType("sounddevice")
try:
    from config.settings import Settings  # noqa: E402
    from core.layer_plan import (  # noqa: E402
        GAIN_DEFAULT,
        GAIN_MAX,
        GAIN_MIN,
        current_bars_settings_from_runtime,
        normalize_current_bars_settings,
    )
finally:
    if _prior_sounddevice is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = _prior_sounddevice

from matrix_controller.presets import DEFAULT_PRESET_FILE, PresetStore  # noqa: E402
from matrix_controller.registry import build_default_registry  # noqa: E402
from matrix_controller.web_service import validate_launch_structure  # noqa: E402
from tools import convert_fft_presets_gain as conversion  # noqa: E402


def _old_settings(**overrides) -> dict:
    """A complete pre-M1 current-bars settings object."""

    settings = current_bars_settings_from_runtime(Settings())
    settings.pop("gain")
    settings.update(sensitivity_scalar=1.0, overflow_multiplier=1.5)
    settings.update(overrides)
    return settings


def _layer(layer_id: str, settings: dict, layer_type: str = "current-bars") -> dict:
    return {
        "layer_id": layer_id,
        "layer_type": layer_type,
        "analyzer_id": "main-audio",
        "visible": True,
        "settings": settings,
    }


def _preset(index: int, name: str, layers: list[dict], **extra) -> dict:
    preset = {
        "preset_id": f"{index:016x}",
        "name": name,
        "revision": 3,
        "parameters": {"audio_source": "bluetooth"},
        "structure": {"output_backend": "direct", "layers": layers},
    }
    preset.update(extra)
    return preset


def _fixture() -> dict:
    return {
        "version": 1,
        "presets": [
            _preset(1, "Warm bass", [
                _layer("loud", _old_settings(sensitivity_scalar=0.5, overflow_multiplier=2.0)),
                _layer("plain", _old_settings(overflow=False, sensitivity_scalar=2.0)),
            ], description="Two layers"),
            _preset(2, "Extremes", [
                _layer("hot", _old_settings(sensitivity_scalar=0.01, overflow_multiplier=10.0)),
                _layer("cold", _old_settings(sensitivity_scalar=20.0, overflow_multiplier=0.1)),
                _layer("partial", {
                    key: value for key, value in _old_settings().items()
                    if key != "overflow_multiplier"
                }),
            ]),
            _preset(3, "Already new", [
                _layer("new", current_bars_settings_from_runtime(Settings())),
                _layer("other", {"anything": 1}, layer_type="future-layer"),
            ]),
        ],
    }


class PresetGainConversionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "fft-presets.json"
        self.original = _fixture()
        self.path.write_text(json.dumps(self.original))
        os.chmod(self.path, 0o600)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _run(self, **kwargs) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = conversion.run(self.path, now=0, **kwargs)
        return code, out.getvalue()

    def _layers(self, document: dict, preset: int) -> list[dict]:
        return [layer["settings"] for layer in document["presets"][preset]["structure"]["layers"]]

    def test_constants_mirror_the_code_they_copy(self) -> None:
        self.assertEqual(
            (conversion.GAIN_MIN, conversion.GAIN_MAX, conversion.GAIN_DEFAULT),
            (GAIN_MIN, GAIN_MAX, GAIN_DEFAULT),
        )
        self.assertEqual(conversion.DEFAULT_PRESET_FILE, DEFAULT_PRESET_FILE)

    def test_converts_exactly_clamps_and_bumps_revisions(self) -> None:
        code, output = self._run()
        self.assertEqual(code, 0, output)
        converted = json.loads(self.path.read_text())

        warm, extremes = self._layers(converted, 0), self._layers(converted, 1)
        self.assertEqual(warm[0]["gain"], 4.0)      # overflow: 2.0 / 0.5
        self.assertEqual(warm[1]["gain"], 0.5)      # overflow off: 1 / 2.0
        self.assertEqual(extremes[0]["gain"], 10.0)  # 1000 limited
        self.assertEqual(extremes[1]["gain"], 0.1)   # 0.005 limited
        self.assertEqual(extremes[2]["gain"], 1.5)   # missing multiplier: old 1.5
        self.assertIn("limited to 10", output)
        self.assertIn("limited to 0.1", output)
        for settings in warm + extremes:
            for key in conversion.OLD_KEYS:
                self.assertNotIn(key, settings)

        revisions = [preset["revision"] for preset in converted["presets"]]
        self.assertEqual(revisions, [4, 4, 3])
        # Everything but gain, the old keys, and the revision is unchanged.
        expected = copy.deepcopy(self.original)
        for preset, after in zip(expected["presets"], converted["presets"]):
            preset["revision"] = after["revision"]
            for layer, new in zip(preset["structure"]["layers"], after["structure"]["layers"]):
                if "gain" in new["settings"] and "gain" not in layer["settings"]:
                    for key in conversion.OLD_KEYS:
                        layer["settings"].pop(key, None)
                    layer["settings"]["gain"] = new["settings"]["gain"]
        self.assertEqual(converted, expected)

    def test_converted_layers_pass_the_child_and_the_store(self) -> None:
        self.assertEqual(self._run()[0], 0)
        converted = json.loads(self.path.read_text())
        for preset in (0, 1):
            for settings in self._layers(converted, preset):
                normalize_current_bars_settings(settings, "direct")
        store = PresetStore(
            self.path,
            validate_parameters=build_default_registry().get("fft").validate_parameters,
            validate_structure=validate_launch_structure,
        )
        listed = store.list()["presets"]
        self.assertEqual([entry["revision"] for entry in listed], [4, 4, 3])
        self.assertEqual(store.get(f"{1:016x}")["structure"]["layers"][0]["settings"]["gain"], 4.0)

    def test_writes_atomically_with_a_backup_and_a_second_run_changes_nothing(self) -> None:
        raw = self.path.read_bytes()
        self.assertEqual(self._run()[0], 0)
        backup = self.path.with_name("fft-presets.json.pre-gain-19700101T000000Z.bak")
        self.assertEqual(backup.read_bytes(), raw)
        for path in (self.path, backup):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(
            sorted(path.name for path in self.path.parent.iterdir()),
            sorted([self.path.name, backup.name]),
        )

        converted = self.path.read_bytes()
        code, output = self._run()
        self.assertEqual(code, 0)
        self.assertIn("nothing to convert", output)
        self.assertEqual(self.path.read_bytes(), converted)

    def test_a_file_changed_after_reading_is_not_replaced(self) -> None:
        original_write = conversion._write_like

        def save_during_backup(path, data, original):
            original_write(path, data, original)
            if path != self.path:
                self.path.write_text(json.dumps({"version": 1, "presets": []}))

        with unittest.mock.patch.object(conversion, "_write_like", save_during_backup):
            code, output = self._run()
        self.assertEqual(code, 1)
        self.assertIn("changed while converting", output)
        self.assertEqual(json.loads(self.path.read_text()), {"version": 1, "presets": []})

    def test_dry_run_writes_nothing(self) -> None:
        raw = self.path.read_bytes()
        code, output = self._run(dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("dry run", output)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_invalid_old_values_reset_to_the_default(self) -> None:
        settings = {"overflow": True, "sensitivity_scalar": 0, "overflow_multiplier": "x"}
        note = conversion.convert_layer_settings(settings)
        self.assertEqual(settings, {"overflow": True, "gain": GAIN_DEFAULT})
        self.assertIn("reset", note)

    def test_unreadable_file_is_left_alone(self) -> None:
        self.path.write_text("{not json")
        code, output = self._run()
        self.assertEqual(code, 1)
        self.assertIn("not converted", output)
        self.assertEqual(self.path.read_text(), "{not json")


if __name__ == "__main__":
    unittest.main()
