"""Preset store tests (web-control-refactor D-024, D-025; M3 criteria 2-4).

The store gets the real FFT ``validate_parameters`` and the web layer's real
launch-structure bounds, so these tests exercise the same rules as launch.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest import mock

from matrix_controller.presets import (
    MAX_PRESET_NAME_LENGTH,
    MAX_PRESETS,
    PresetError,
    PresetStore,
)
from matrix_controller.registry import build_default_registry
from matrix_controller.runtime_transport import MAX_LAUNCH_STRUCTURE_BYTES
from matrix_controller.web_service import validate_launch_structure


def structure_document(layer_id: str = "main") -> dict:
    return {
        "output_backend": "direct",
        "layers": [
            {
                "layer_id": layer_id,
                "layer_type": "current-bars",
                "analyzer_id": "default",
                "visible": True,
                "settings": {
                    "weight_points": [
                        {"position": 0.0, "weight": 1.0},
                        {"position": 1.0, "weight": 3.0},
                    ]
                },
            }
        ],
    }


class PresetStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "state" / "fft-presets.json"
        self.fft = build_default_registry().get("fft")
        self.ids = iter(f"{index:016x}" for index in range(1, 1000))
        self.store = self._store()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _store(self) -> PresetStore:
        return PresetStore(
            self.path,
            validate_parameters=self.fft.validate_parameters,
            validate_structure=validate_launch_structure,
            id_factory=lambda: next(self.ids),
        )

    def _create(self, name: str = "Warm bass", **extra) -> dict:
        payload = {"name": name, "parameters": {"audio_source": "bluetooth"}, "structure": structure_document()}
        payload.update(extra)
        return self.store.create(payload)

    def assertPresetError(self, code: str, status: HTTPStatus, call, *args):
        with self.assertRaises(PresetError) as caught:
            call(*args)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.status, status)
        return caught.exception

    def test_missing_file_is_an_empty_store_and_reading_creates_nothing(self) -> None:
        listing = self.store.list()
        self.assertEqual(listing["presets"], [])
        self.assertEqual(
            listing["limits"],
            {"max_presets": MAX_PRESETS, "max_name_length": MAX_PRESET_NAME_LENGTH},
        )
        self.assertFalse(self.path.exists())

    def test_create_list_get_replace_and_delete(self) -> None:
        created = self._create("  Warm bass  ")
        self.assertEqual(created["name"], "Warm bass")
        self.assertEqual(created["revision"], 1)
        self.assertRegex(created["preset_id"], r"^[0-9a-f]{16}$")
        preset_id = created["preset_id"]

        self.assertEqual(
            self.store.list()["presets"],
            [{"preset_id": preset_id, "name": "Warm bass", "revision": 1}],
        )
        loaded = self.store.get(preset_id)
        self.assertEqual(loaded, created)
        # The loaded pair is exactly what launch accepts.
        self.fft.validate_parameters(loaded["parameters"])
        self.assertEqual(validate_launch_structure(loaded["structure"]), loaded["structure"])

        replaced = self.store.replace(
            preset_id,
            {
                "expected_revision": 1,
                "name": "warm BASS",  # its own name in another case is allowed
                "parameters": {"audio_source": "bluetooth"},
                "structure": structure_document("other"),
            },
        )
        self.assertEqual(replaced["revision"], 2)
        self.assertEqual(replaced["parameters"], {"audio_source": "bluetooth"})
        self.assertEqual(replaced["structure"]["layers"][0]["layer_id"], "other")
        self.assertEqual(self.store.get(preset_id), replaced)

        self.assertEqual(
            self.store.delete(preset_id, {"expected_revision": 2}),
            {"deleted": True, "preset_id": preset_id},
        )
        self.assertEqual(self.store.list()["presets"], [])
        self.assertPresetError("preset_not_found", HTTPStatus.NOT_FOUND, self.store.get, preset_id)

    def test_only_supplied_parameters_are_stored(self) -> None:
        created = self.store.create(
            {"name": "Defaults", "structure": structure_document()}
        )
        self.assertEqual(created["parameters"], {})
        created = self._create("Two", parameters={"audio_source": "bluetooth"})
        self.assertEqual(created["parameters"], {"audio_source": "bluetooth"})

    def test_matrix_settings_are_not_preset_parameters(self) -> None:
        # web-control-refactor D-035: presets hold FFT launch options only.
        with self.assertRaises(PresetError) as caught:
            self._create("Bright", parameters={"led_brightness": 60})
        self.assertEqual(caught.exception.code, "invalid_preset")
        self.assertIn("led_brightness", str(caught.exception))
        self.assertFalse(self.path.exists())

    def test_store_survives_a_new_instance_with_mode_0600(self) -> None:
        created = self._create()
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(self._store().get(created["preset_id"]), created)
        document = json.loads(self.path.read_text())
        self.assertEqual(document["version"], 1)
        self.assertEqual(len(document["presets"]), 1)
        self.assertEqual(
            [entry.name for entry in self.path.parent.iterdir()], [self.path.name]
        )

    def test_stale_revision_conflicts_and_leaves_the_file_unchanged(self) -> None:
        preset_id = self._create()["preset_id"]
        self.store.replace(
            preset_id,
            {"expected_revision": 1, "name": "Warm bass", "structure": structure_document()},
        )
        before = self.path.read_bytes()
        error = self.assertPresetError(
            "revision_conflict",
            HTTPStatus.CONFLICT,
            self.store.replace,
            preset_id,
            {"expected_revision": 1, "name": "Late", "structure": structure_document()},
        )
        self.assertEqual(error.current_revision, 2)
        self.assertEqual(error.as_body()["current_revision"], 2)
        error = self.assertPresetError(
            "revision_conflict",
            HTTPStatus.CONFLICT,
            self.store.delete,
            preset_id,
            {"expected_revision": 1},
        )
        self.assertEqual(error.current_revision, 2)
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_requests_are_rejected_with_the_file_unchanged(self) -> None:
        first = self._create("First")
        self._create("Second")
        before = self.path.read_bytes()
        too_deep: dict = {}
        node = too_deep
        for _ in range(10):
            node["x"] = {}
            node = node["x"]
        oversize = structure_document()
        oversize["layers"][0]["settings"]["pad"] = [
            "x" * 250 for _ in range(32)
        ]
        oversize["layers"] = [oversize["layers"][0]] * 8
        self.assertGreater(len(json.dumps(oversize)), MAX_LAUNCH_STRUCTURE_BYTES)
        structure = structure_document()
        invalid_creates = {
            "empty name": {"name": "   ", "structure": structure},
            "long name": {"name": "x" * (MAX_PRESET_NAME_LENGTH + 1), "structure": structure},
            "control character": {"name": "bad\x07name", "structure": structure},
            "delete character": {"name": "bad\x7fname", "structure": structure},
            "name not a string": {"name": 7, "structure": structure},
            "unknown parameter": {"name": "x", "parameters": {"zoom": 1}, "structure": structure},
            "invalid parameter": {"name": "x", "parameters": {"audio_source": "fire"}, "structure": structure},
            # D-050: a retired FFT launch option is refused like any unknown one.
            "retired parameter": {"name": "x", "parameters": {"theme": "ocean"}, "structure": structure},
            "parameters not an object": {"name": "x", "parameters": [], "structure": structure},
            "structure not an object": {"name": "x", "structure": ["layers"]},
            "structure missing": {"name": "x"},
            "structure too deep": {"name": "x", "structure": too_deep},
            "structure too wide": {"name": "x", "structure": {"layers": [{}] * 33}},
            "structure too large": {"name": "x", "structure": oversize},
            "unknown field": {"name": "x", "structure": structure, "preset_id": "f" * 16},
        }
        for label, payload in invalid_creates.items():
            with self.subTest(label):
                self.assertPresetError(
                    "invalid_preset", HTTPStatus.BAD_REQUEST, self.store.create, payload
                )
        self.assertPresetError(
            "name_conflict", HTTPStatus.CONFLICT, self._create, "  FIRST "
        )
        self.assertPresetError(
            "name_conflict",
            HTTPStatus.CONFLICT,
            self.store.replace,
            first["preset_id"],
            {"expected_revision": 1, "name": "second", "structure": structure},
        )
        for label, payload in {
            "no revision": {"name": "x", "structure": structure},
            "boolean revision": {"expected_revision": True, "name": "x", "structure": structure},
            "zero revision": {"expected_revision": 0, "name": "x", "structure": structure},
        }.items():
            with self.subTest(label):
                self.assertPresetError(
                    "invalid_preset",
                    HTTPStatus.BAD_REQUEST,
                    self.store.replace,
                    first["preset_id"],
                    payload,
                )
        self.assertPresetError(
            "invalid_preset", HTTPStatus.BAD_REQUEST, self.store.delete, first["preset_id"], {}
        )
        for call, args in (
            (self.store.get, ("0" * 16,)),
            (self.store.get, ("../etc",)),
            (self.store.replace, ("0" * 16, {"expected_revision": 1, "name": "x", "structure": structure})),
            (self.store.delete, ("0" * 16, {"expected_revision": 1})),
        ):
            with self.subTest(call=call.__name__, args=str(args)[:30]):
                self.assertPresetError("preset_not_found", HTTPStatus.NOT_FOUND, call, *args)
        self.assertEqual(self.path.read_bytes(), before)

    def test_the_33rd_preset_is_refused(self) -> None:
        for index in range(MAX_PRESETS):
            self._create(f"Preset {index}")
        before = self.path.read_bytes()
        self.assertPresetError(
            "limit_exceeded", HTTPStatus.BAD_REQUEST, self._create, "One too many"
        )
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.store.list()["presets"]), MAX_PRESETS)

    def test_corrupt_or_unknown_files_are_unavailable_and_never_rewritten(self) -> None:
        valid_entry = {
            "preset_id": "a" * 16,
            "name": "Kept",
            "revision": 1,
            "parameters": {},
            "structure": {},
        }
        cases = {
            "not json": b"{not json",
            "not utf-8": b"\xff\xfe",
            "not an object": b"[]",
            "unknown version": json.dumps({"version": 2, "presets": []}).encode(),
            "boolean version": json.dumps({"version": True, "presets": []}).encode(),
            "extra key": json.dumps({"version": 1, "presets": [], "x": 1}).encode(),
            "bad entry": json.dumps({"version": 1, "presets": [{"name": "x"}]}).encode(),
            "untrimmed name": json.dumps(
                {"version": 1, "presets": [dict(valid_entry, name=" Kept")]}
            ).encode(),
            "duplicate names": json.dumps(
                {
                    "version": 1,
                    "presets": [valid_entry, dict(valid_entry, preset_id="b" * 16, name="KEPT")],
                }
            ).encode(),
            # Python's json accepts these by default; they aren't JSON.
            "NaN value": json.dumps(
                {"version": 1, "presets": [dict(valid_entry, parameters={"gain": float("nan")})]}
            ).encode(),
            "Infinity value": json.dumps(
                {"version": 1, "presets": [dict(valid_entry, structure={"x": float("inf")})]}
            ).encode(),
        }
        self.path.parent.mkdir(parents=True)
        for label, raw in cases.items():
            with self.subTest(label):
                self.path.write_bytes(raw)
                for call, args in (
                    (self.store.list, ()),
                    (self.store.get, ("a" * 16,)),
                    (self.store.create, ({"name": "New", "structure": {}},)),
                    (self.store.replace, ("a" * 16, {"expected_revision": 1, "name": "x", "structure": {}})),
                    (self.store.delete, ("a" * 16, {"expected_revision": 1})),
                    # An invalid body still gets 503: the file is read first.
                    (self.store.create, ({},)),
                    (self.store.replace, ("a" * 16, {})),
                    (self.store.delete, ("a" * 16, {})),
                ):
                    self.assertPresetError(
                        "preset_store_unavailable",
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        call,
                        *args,
                    )
                self.assertEqual(self.path.read_bytes(), raw)

    def test_non_finite_numbers_are_refused_before_anything_is_written(self) -> None:
        created = self._create("First")
        before = self.path.read_bytes()
        # Permissive validators, so the value reaches the encoder.
        store = PresetStore(
            self.path,
            validate_parameters=dict,
            validate_structure=dict,
            id_factory=lambda: next(self.ids),
        )
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                self.assertPresetError(
                    "invalid_preset",
                    HTTPStatus.BAD_REQUEST,
                    store.create,
                    {"name": "Bad", "structure": {"gain": value}},
                )
                self.assertPresetError(
                    "invalid_preset",
                    HTTPStatus.BAD_REQUEST,
                    store.replace,
                    created["preset_id"],
                    {"expected_revision": 1, "name": "First", "parameters": {"gain": value}, "structure": {}},
                )
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), [self.path.name])

    def test_a_failed_replace_keeps_the_old_file_and_removes_the_temp_file(self) -> None:
        self._create("First")
        before = self.path.read_bytes()
        with mock.patch("matrix_controller.presets.os.replace", side_effect=OSError("disk")):
            self.assertPresetError(
                "preset_store_unavailable",
                HTTPStatus.SERVICE_UNAVAILABLE,
                self._create,
                "Second",
            )
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(
            [entry.name for entry in self.path.parent.iterdir()], [self.path.name]
        )

    def test_generated_ids_are_unique_and_well_formed(self) -> None:
        ids = iter(["not-hex", "0" * 16, "0" * 16, "1" * 16])
        store = PresetStore(
            self.path,
            validate_parameters=self.fft.validate_parameters,
            validate_structure=validate_launch_structure,
            id_factory=lambda: next(ids),
        )
        self.assertEqual(store.create({"name": "a", "structure": {}})["preset_id"], "0" * 16)
        self.assertEqual(store.create({"name": "b", "structure": {}})["preset_id"], "1" * 16)
        default_ids = PresetStore(
            Path(self.directory.name) / "other.json",
            validate_parameters=self.fft.validate_parameters,
            validate_structure=validate_launch_structure,
        )
        self.assertRegex(
            default_ids.create({"name": "a", "structure": {}})["preset_id"],
            r"^[0-9a-f]{16}$",
        )


if __name__ == "__main__":
    unittest.main()
