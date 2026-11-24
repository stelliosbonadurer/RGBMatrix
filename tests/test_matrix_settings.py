"""The shared matrix settings store (web-control-refactor D-035), with no hardware."""

from __future__ import annotations

import json
import stat
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest import mock

from matrix_controller.definitions import MATRIX_PARAMETERS
from matrix_controller.matrix_settings import (
    COMMON_MATRIX_SETTINGS,
    MatrixSettingsError,
    MatrixSettingsStore,
    config_matrix_values,
    normalize_matrix_values,
)


CONFIG_VALUES = config_matrix_values({"led_gpio_mapping": "adafruit-hat-pwm"})


class MatrixSettingsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state" / "matrix-settings.json"
        self.store = MatrixSettingsStore(self.path, config_values=CONFIG_VALUES)

    def values(self, **changes) -> dict:
        return {**CONFIG_VALUES, **changes}

    def assertMatrixError(self, code: str, status: HTTPStatus, call, *args):
        with self.assertRaises(MatrixSettingsError) as caught:
            call(*args)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.status, status)
        return caught.exception

    def entries(self) -> list[str]:
        return sorted(entry.name for entry in self.path.parent.iterdir()) if self.path.parent.exists() else []

    def test_config_values_overlay_the_code_defaults(self) -> None:
        self.assertEqual(len(CONFIG_VALUES), len(MATRIX_PARAMETERS))
        self.assertEqual(CONFIG_VALUES["led_gpio_mapping"], "adafruit-hat-pwm")
        self.assertEqual(CONFIG_VALUES["led_rows"], 64)
        with self.assertRaises(ValueError):
            config_matrix_values({"led_rows": 0})

    def test_missing_file_uses_config_values_at_revision_zero(self) -> None:
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["revision"], 0)
        self.assertEqual(snapshot["source"], "config")
        self.assertIsNone(snapshot["error"])
        self.assertEqual(snapshot["values"], CONFIG_VALUES)
        self.assertEqual(snapshot["config_values"], CONFIG_VALUES)
        self.assertEqual([item["name"] for item in snapshot["parameters"]],
                         [parameter.name for parameter in MATRIX_PARAMETERS])
        self.assertEqual(snapshot["common"], list(COMMON_MATRIX_SETTINGS))
        self.assertTrue(set(COMMON_MATRIX_SETTINGS) <= set(CONFIG_VALUES))
        self.assertEqual(self.store.effective_values(), CONFIG_VALUES)
        self.assertFalse(self.path.exists())

    def test_replace_writes_atomically_with_mode_0600_and_survives_restart(self) -> None:
        saved = self.store.replace({"expected_revision": 0, "values": self.values(led_brightness=40)})
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(saved["source"], "stored")
        self.assertEqual(saved["values"]["led_brightness"], 40)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(self.entries(), [self.path.name])
        document = json.loads(self.path.read_text())
        self.assertEqual(set(document), {"version", "revision", "values"})
        reopened = MatrixSettingsStore(self.path, config_values=CONFIG_VALUES)
        self.assertEqual(reopened.snapshot(), saved)
        self.assertEqual(reopened.effective_values()["led_brightness"], 40)
        second = reopened.replace({"expected_revision": 1, "values": self.values(led_brightness=55)})
        self.assertEqual(second["revision"], 2)

    def test_stale_revision_is_a_conflict_that_changes_nothing(self) -> None:
        self.store.replace({"expected_revision": 0, "values": self.values(led_brightness=40)})
        before = self.path.read_bytes()
        error = self.assertMatrixError(
            "revision_conflict",
            HTTPStatus.CONFLICT,
            self.store.replace,
            {"expected_revision": 0, "values": self.values(led_brightness=90)},
        )
        self.assertEqual(error.current_revision, 1)
        self.assertEqual(error.as_body()["current_revision"], 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_requests_are_rejected_without_writing(self) -> None:
        partial = dict(CONFIG_VALUES)
        del partial["led_cols"]
        cases = (
            None,
            [],
            {"values": CONFIG_VALUES},
            {"expected_revision": 0},
            {"expected_revision": 0, "values": CONFIG_VALUES, "extra": 1},
            {"expected_revision": -1, "values": CONFIG_VALUES},
            {"expected_revision": True, "values": CONFIG_VALUES},
            {"expected_revision": 0, "values": partial},
            {"expected_revision": 0, "values": {**CONFIG_VALUES, "led_extra": 1}},
            {"expected_revision": 0, "values": self.values(led_rows=0)},
            {"expected_revision": 0, "values": self.values(led_gpio_mapping="other")},
            {"expected_revision": 0, "values": self.values(led_show_refresh=1)},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertMatrixError(
                    "invalid_matrix_settings", HTTPStatus.BAD_REQUEST, self.store.replace, payload
                )
        self.assertFalse(self.path.exists())

    def test_messages_name_the_field(self) -> None:
        error = self.assertMatrixError(
            "invalid_matrix_settings",
            HTTPStatus.BAD_REQUEST,
            self.store.replace,
            {"expected_revision": 0, "values": self.values(led_brightness=101)},
        )
        self.assertIn("led_brightness", str(error))

    def test_unusable_file_falls_back_to_config_and_is_never_rewritten(self) -> None:
        good = json.dumps({"version": 1, "revision": 2, "values": CONFIG_VALUES})
        cases = {
            "not JSON": b"{",
            "NaN": json.dumps({"version": 1, "revision": 2, "values": CONFIG_VALUES}).replace(
                '"led_rows": 64', '"led_rows": NaN'
            ).encode(),
            "layout": json.dumps({"version": 1, "values": CONFIG_VALUES}).encode(),
            "version": good.replace('"version": 1', '"version": 2').encode(),
            "revision": good.replace('"revision": 2', '"revision": 0').encode(),
            "values": json.dumps({"version": 1, "revision": 2, "values": {"led_rows": 64}}).encode(),
            "bad value": json.dumps(
                {"version": 1, "revision": 2, "values": {**CONFIG_VALUES, "led_rows": 0}}
            ).encode(),
        }
        self.path.parent.mkdir(parents=True)
        for label, raw in cases.items():
            with self.subTest(label=label):
                self.path.write_bytes(raw)
                snapshot = self.store.snapshot()
                self.assertIsNone(snapshot["revision"])
                self.assertEqual(snapshot["source"], "config")
                self.assertTrue(snapshot["error"])
                self.assertEqual(snapshot["values"], CONFIG_VALUES)
                self.assertEqual(self.store.effective_values(), CONFIG_VALUES)
                self.assertMatrixError(
                    "matrix_settings_unavailable",
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    self.store.replace,
                    {"expected_revision": 0, "values": CONFIG_VALUES},
                )
                self.assertEqual(self.path.read_bytes(), raw)

    def test_a_failed_write_keeps_the_old_file_and_leaves_no_temp_file(self) -> None:
        self.store.replace({"expected_revision": 0, "values": self.values(led_brightness=40)})
        before = self.path.read_bytes()
        with mock.patch("matrix_controller.state_files.os.replace", side_effect=OSError("disk")):
            self.assertMatrixError(
                "matrix_settings_unavailable",
                HTTPStatus.SERVICE_UNAVAILABLE,
                self.store.replace,
                {"expected_revision": 1, "values": self.values(led_brightness=60)},
            )
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.entries(), [self.path.name])

    def test_normalize_requires_the_complete_set(self) -> None:
        self.assertEqual(normalize_matrix_values(CONFIG_VALUES), CONFIG_VALUES)
        with self.assertRaisesRegex(ValueError, "missing matrix settings"):
            normalize_matrix_values({"led_rows": 64})
        with self.assertRaisesRegex(ValueError, "must be a JSON object"):
            normalize_matrix_values([])


if __name__ == "__main__":
    unittest.main()
