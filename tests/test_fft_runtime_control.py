"""Hardware-free tests for the FFT runtime protocol controller.

Since web-control-refactor M1 the controller owns only run identity, revision, and
the snapshot. Every live control, patch path, and legacy action is rejected; the
layer structure (``structure.set``) is the only configuration path (D-001, D-015).
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest


FFT_ROOT = Path(__file__).resolve().parent.parent / "my_programs" / "fft_program"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))

from config import Settings  # noqa: E402
from config.presets import get_preset  # noqa: E402
from config.settings import SensitivitySettings, load_settings  # noqa: E402
from config.weighting import WeightPointsError, legacy_weight_points  # noqa: E402
from runtime_control import FftRuntimeController  # noqa: E402
from matrix_controller.runtime_transport import (  # noqa: E402
    FrameBoundaryRuntimeBridge,
    RuntimeControlClient,
    RuntimeControlError,
    RuntimeControlServer,
    RuntimeRemoteError,
)


LEGACY_PATCH_PATHS = (
    "visualizer.theme",
    "visualizer.gradient",
    "visualizer.overflow",
    "visualizer.bars",
    "visualizer.full",
    "settings.shadow_enabled",
    "settings.peak_enabled",
    "settings.peak_color_mode",
    "settings.zoom_preset_index",
    "settings.noise_floor",
    "settings.low_freq_weight",
    "settings.high_freq_weight",
    "settings.silence_threshold",
    "settings.sensitivity_scalar",
    "settings.smoothing_rise",
    "settings.smoothing_fall",
    "settings.overflow_multiplier",
    "settings.peak_fall_speed",
    "settings.peak_hold_frames",
    "dynamic.cycle_speed",
    "dynamic.frozen",
)
LEGACY_ACTIONS = (
    "theme.next",
    "theme.previous",
    "zoom.next",
    "zoom.previous",
    "dynamic.adjust_speed",
    "dynamic.reseed",
    "dynamic.freeze",
    "structure.apply",
)


class FftRuntimeControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.structure = {"output_backend": "direct", "layers": []}
        self.controller = FftRuntimeController(run_id="run-1")
        self.controller.bind_structure_snapshot(lambda: self.structure)

    def test_snapshot_is_json_safe_and_has_no_legacy_state(self) -> None:
        snapshot = self.controller.handle_runtime_operation("snapshot", {})

        self.assertEqual(json.loads(json.dumps(snapshot)), snapshot)
        self.assertEqual(snapshot["run_id"], "run-1")
        self.assertEqual(snapshot["visualization_id"], "fft")
        self.assertEqual(snapshot["revision"], 0)
        self.assertTrue(snapshot["connected"])
        self.assertEqual(snapshot["structure"], self.structure)
        self.assertNotIn("controls", snapshot)
        self.assertNotIn("state", snapshot)

    def test_every_legacy_patch_path_is_rejected_without_revision_change(self) -> None:
        for path in LEGACY_PATCH_PATHS:
            with self.subTest(path=path), self.assertRaises(RuntimeControlError) as caught:
                self.controller.handle_runtime_operation("command", {
                    "command": {
                        "operation": "patch",
                        "changes": [{"path": path, "value": True}],
                    },
                    "expected_revision": 0,
                })
            self.assertEqual(caught.exception.code, "invalid_path")
            self.assertEqual(caught.exception.path, path)
        self.assertEqual(self.controller.revision, 0)

    def test_every_legacy_action_is_rejected_without_revision_change(self) -> None:
        # structure.set is intercepted by the structural coordinator; anything
        # that reaches the controller, including the retired structure.apply, is
        # unknown here.
        for action in LEGACY_ACTIONS:
            with self.subTest(action=action), self.assertRaises(RuntimeControlError) as caught:
                self.controller.handle_runtime_operation("command", {
                    "command": {"operation": "action", "action": action, "args": {}},
                    "expected_revision": 0,
                })
            self.assertEqual(caught.exception.code, "invalid_path")
            self.assertEqual(caught.exception.path, action)
        self.assertEqual(self.controller.revision, 0)

    def test_malformed_operations_are_bounded_errors(self) -> None:
        cases = (
            ("bogus", {}, "invalid_operation"),
            ("command", {"command": "x"}, "invalid_command"),
            ("command", {"command": {"operation": "other"}}, "invalid_operation"),
            # A huge integer no longer reaches any float() conversion
            # (runtime-control hardening item 2 is moot).
            ("command", {"command": {
                "operation": "patch",
                "changes": [{"path": "settings.noise_floor", "value": 10 ** 400}],
            }}, "invalid_path"),
        )
        for operation, payload, code in cases:
            with self.subTest(code=code), self.assertRaises(RuntimeControlError) as caught:
                self.controller.handle_runtime_operation(operation, payload)
            self.assertEqual(caught.exception.code, code)

    def test_controller_has_no_legacy_control_surface(self) -> None:
        for name in (
            "apply_request",
            "apply_commands",
            "control_schema",
            "handle_key",
            "runtime_state",
            "rebind_presentation",
            "current_presentation_settings",
        ):
            self.assertFalse(hasattr(self.controller, name), name)

    def test_silence_threshold_is_not_a_setting(self) -> None:
        self.assertNotIn("silence_threshold", SensitivitySettings.__dataclass_fields__)
        self.assertFalse(hasattr(Settings().sensitivity, "silence_threshold"))

    def test_private_transport_round_trip_reaches_frame_boundary_controller(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            try:
                server = RuntimeControlServer("run-test", runtime_dir=directory)
            except OSError:
                self.skipTest("Unix-domain sockets unavailable")
            controller = FftRuntimeController(run_id="run-test")
            client = RuntimeControlClient(server.endpoint)
            bridge = FrameBoundaryRuntimeBridge(response_timeout=0.75)
            stop_event = threading.Event()
            listener = threading.Thread(
                target=client.serve,
                args=(bridge.handle,),
                kwargs={"stop_event": stop_event},
                daemon=True,
            )
            listener.start()

            def exchange(operation, payload):
                result = []

                def request() -> None:
                    try:
                        result.append(server.request(operation, payload, timeout=1))
                    except RuntimeRemoteError as exc:
                        result.append(exc)

                thread = threading.Thread(target=request)
                thread.start()
                deadline = time.monotonic() + 1
                while thread.is_alive() and time.monotonic() < deadline:
                    bridge.process_pending(
                        controller.handle_runtime_operation,
                        max_items=4,
                    )
                    time.sleep(0.002)
                thread.join(1)
                self.assertFalse(thread.is_alive())
                return result[0]

            snapshot = exchange("snapshot", {})
            self.assertEqual(snapshot["run_id"], "run-test")
            rejected = exchange("command", {
                "command": {
                    "operation": "patch",
                    "changes": [
                        {"path": "visualizer.gradient", "value": True}
                    ],
                },
                "expected_revision": 0,
            })
            self.assertIsInstance(rejected, RuntimeRemoteError)
            self.assertEqual(rejected.code, "invalid_path")
            self.assertEqual(rejected.path, "visualizer.gradient")
            self.assertEqual(controller.revision, 0)

            stop_event.set()
            bridge.close()
            server.close()
            listener.join(1)


class SettingsFileTests(unittest.TestCase):
    """Retired sensitivity keys in a --settings file convert (D-022)."""

    def _load(self, sensitivity):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"sensitivity": sensitivity}))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                settings = load_settings(str(path))
        return settings, output.getvalue()

    def test_retired_weight_pair_converts_to_points_with_a_note(self) -> None:
        settings, output = self._load(
            {"noise_floor": 0.2, "low_freq_weight": 0.6, "high_freq_weight": 8.0}
        )
        self.assertEqual(settings.sensitivity.noise_floor, 0.2)
        self.assertEqual(
            settings.sensitivity.weight_points, legacy_weight_points(0.6, 8.0)
        )
        self.assertIn("converted retired high_freq_weight, low_freq_weight", output)

        settings, _output = self._load({"high_freq_weight": 4.0})
        self.assertEqual(
            settings.sensitivity.weight_points, legacy_weight_points(0.45, 4.0)
        )

    def test_silence_threshold_is_dropped_with_a_note(self) -> None:
        settings, output = self._load({"silence_threshold": 0.1, "noise_floor": 0.4})
        self.assertEqual(settings.sensitivity.noise_floor, 0.4)
        self.assertEqual(settings.sensitivity.weight_points, legacy_weight_points())
        self.assertIn("ignored retired sensitivity.silence_threshold", output)

    def test_weight_points_win_over_retired_keys_and_are_validated(self) -> None:
        settings, output = self._load({
            "low_freq_weight": 0.6,
            "weight_points": [
                {"position": 1, "weight": 3},
                {"position": 0, "weight": 1},
            ],
        })
        self.assertEqual(
            settings.sensitivity.weight_points,
            [{"position": 0.0, "weight": 1.0}, {"position": 1.0, "weight": 3.0}],
        )
        self.assertIn("low_freq_weight key(s) were ignored", output)
        with self.assertRaises(WeightPointsError):
            self._load({"weight_points": []})

    def test_built_in_presets_use_points(self) -> None:
        # Only "default" constructs today: the others set the read-only
        # zoom_min_freq property (pre-existing, parked in the M2 log).
        self.assertEqual(
            get_preset("default").sensitivity.weight_points, legacy_weight_points()
        )
        source = (FFT_ROOT / "config" / "presets.py").read_text()
        self.assertNotIn(".low_freq_weight", source)
        self.assertNotIn(".high_freq_weight", source)
        self.assertEqual(source.count("weight_points = legacy_weight_points("), 4)


if __name__ == "__main__":
    unittest.main()
