"""Hardware-free tests for the FFT in-process runtime-control contract."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace


FFT_ROOT = Path(__file__).resolve().parent.parent / "my_programs" / "fft_program"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))

from config import Settings, load_settings  # noqa: E402
from config.settings import save_settings  # noqa: E402
from runtime_control import FftRuntimeController  # noqa: E402
from themes import get_theme  # noqa: E402
from matrix_controller.runtime_transport import (  # noqa: E402
    FrameBoundaryRuntimeBridge,
    RuntimeControlClient,
    RuntimeControlServer,
)


class FakeAudio:
    def __init__(self) -> None:
        self.updates = 0

    def update_frequency_range(self) -> None:
        self.updates += 1


class FakeCycler:
    def __init__(self, initial: str = "ocean") -> None:
        self.themes = ["ocean", "warm", "forest"]
        self.current_index = self.themes.index(initial)

    @property
    def current_theme_name(self) -> str:
        return self.themes[self.current_index]

    def next_theme(self):
        self.current_index = (self.current_index + 1) % len(self.themes)
        return get_theme(self.current_theme_name)

    def prev_theme(self):
        self.current_index = (self.current_index - 1) % len(self.themes)
        return get_theme(self.current_theme_name)


class FakeVisualizer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.theme = get_theme("ocean")
        self.gradient_mode = False
        self.overflow_mode = True
        self.bars_enabled = True
        self.full_mode = False
        self.debug_mode = False
        self.layers_enabled = False
        self.active_layer = 0
        self.layer_states = []
        self.draw_order = []
        self.shadow_buffer = None
        self.shadow_colors = None

    def set_theme(self, theme) -> None:
        self.theme = theme

    def set_layer_theme(self, name: str, brightness_boost: float = 1.0) -> None:
        state = self.layer_states[self.active_layer]
        state.theme_name = name
        state.theme = get_theme(name, brightness_boost=brightness_boost)

    def setup_layers(self) -> None:
        self.layer_states = [
            SimpleNamespace(
                theme=get_theme("ocean"), theme_name="ocean", bars_enabled=True,
                gradient_enabled=False, overflow_enabled=True, peak_enabled=False,
                visible=True, boost=1.0,
            ),
            SimpleNamespace(
                theme=get_theme("warm"), theme_name="warm", bars_enabled=True,
                gradient_enabled=False, overflow_enabled=False, peak_enabled=False,
                visible=True, boost=2.5,
            ),
        ]
        self.draw_order = [0, 1]

    def select_layer(self, index: int) -> bool:
        if 0 <= index < len(self.layer_states):
            self.active_layer = index
            return True
        return False

    def swap_draw_order(self, direction: int):
        position = self.draw_order.index(self.active_layer)
        target = position + direction
        if not 0 <= target < len(self.draw_order):
            return None
        self.draw_order[position], self.draw_order[target] = self.draw_order[target], self.draw_order[position]
        return position, target


class FftRuntimeControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings()
        self.visualizer = FakeVisualizer(self.settings)
        self.audio = FakeAudio()
        self.cycler = FakeCycler()
        self.ensure_calls = 0

        def ensure_layers() -> None:
            self.ensure_calls += 1
            if not self.visualizer.layer_states:
                self.visualizer.setup_layers()

        self.controller = FftRuntimeController(
            settings=self.settings,
            visualizer=self.visualizer,
            audio=self.audio,
            theme_cycler=self.cycler,
            width=8,
            height=8,
            ensure_layer_pipeline=ensure_layers,
            run_id="run-test",
        )

    def test_snapshot_is_json_safe_and_advertises_controls(self) -> None:
        snapshot = self.controller.snapshot()
        json.dumps(snapshot)
        self.assertEqual(snapshot["protocol_version"], 1)
        self.assertEqual(snapshot["run_id"], "run-test")
        self.assertTrue(any(item["path"] == "visualizer.gradient" for item in snapshot["controls"]))
        self.assertTrue(any(item["path"] == "visualizer.theme" for item in snapshot["controls"]))
        self.assertTrue(any(item.get("action") == "theme.next" for item in snapshot["controls"]))

    def test_typed_set_increments_revision_and_rejects_stale_revision(self) -> None:
        result = self.controller.apply_request({
            "run_id": "run-test",
            "expected_revision": 0,
            "commands": [{"op": "set", "path": "visualizer.gradient", "value": True}],
        })
        self.assertTrue(result["ok"])
        self.assertTrue(self.visualizer.gradient_mode)
        self.assertEqual(result["snapshot"]["revision"], 1)

        stale = self.controller.apply_request({
            "run_id": "run-test",
            "expected_revision": 0,
            "commands": [{"op": "set", "path": "visualizer.overflow", "value": False}],
        })
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["error"]["code"], "revision_conflict")
        self.assertTrue(self.visualizer.overflow_mode)

    def test_bad_batch_is_rejected_without_partial_mutation(self) -> None:
        result = self.controller.apply_request({
            "commands": [
                {"op": "set", "path": "visualizer.gradient", "value": True},
                {"op": "set", "path": "settings.peak_color_mode", "value": "invalid"},
            ]
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_value")
        self.assertFalse(self.visualizer.gradient_mode)
        self.assertEqual(self.controller.revision, 0)

    def test_keyboard_and_semantic_command_share_state_path(self) -> None:
        self.controller.handle_key("g")
        self.assertTrue(self.visualizer.gradient_mode)
        self.controller.handle_key("s")
        self.assertTrue(self.settings.shadow.enabled)
        self.controller.handle_key("P")
        self.assertEqual(self.settings.peak.color_mode, "peak")
        self.controller.handle_key("r")
        self.assertEqual(self.audio.updates, 1)

    def test_layer_controls_use_stable_config_names(self) -> None:
        self.controller.handle_key("l")
        self.assertTrue(self.visualizer.layers_enabled)
        result = self.controller.apply_request({
            "commands": [{"op": "set", "path": "layers.bass.visible", "value": False}]
        })
        self.assertTrue(result["ok"])
        self.assertFalse(self.visualizer.layer_states[0].visible)
        self.assertTrue(any(item["path"] == "layers.bass.visible" for item in result["snapshot"]["controls"]))

        boost = self.controller.apply_request({
            "commands": [
                {"op": "set", "path": "layers.bass.boost", "value": 3.5}
            ]
        })
        self.assertTrue(boost["ok"])
        self.assertEqual(self.visualizer.layer_states[0].boost, 3.5)
        self.assertEqual(self.settings.layers.layers[0].boost, 3.5)

    def test_numeric_and_dynamic_controls_are_typed(self) -> None:
        changed = self.controller.apply_request({
            "commands": [
                {
                    "op": "set",
                    "path": "settings.smoothing_rise",
                    "value": 0.75,
                }
            ]
        })
        self.assertTrue(changed["ok"])
        self.assertEqual(self.settings.smoothing.rise, 0.75)

        bad = self.controller.apply_request({
            "commands": [
                {
                    "op": "set",
                    "path": "settings.smoothing_fall",
                    "value": 2.0,
                }
            ]
        })
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"]["code"], "invalid_value")

        self.controller.apply_request({
            "commands": [
                {"op": "set", "path": "visualizer.theme", "value": "dynamic"}
            ]
        })
        dynamic = self.controller.apply_request({
            "commands": [
                {"op": "set", "path": "dynamic.cycle_speed", "value": 0.2},
                {"op": "set", "path": "dynamic.frozen", "value": True},
            ]
        })
        self.assertTrue(dynamic["ok"])
        self.assertEqual(self.visualizer.theme.cycle_speed, 0.2)
        self.assertTrue(self.visualizer.theme.frozen)
        paths = {item["path"] for item in dynamic["snapshot"]["controls"]}
        self.assertIn("dynamic.reseed", paths)

    def test_settings_round_trip_restores_shadow_and_layers(self) -> None:
        self.settings.shadow.enabled = True
        self.settings.layers.enabled = True
        self.settings.layers.layers[0].boost = 4.25
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(self.settings, str(path))
            restored = load_settings(str(path))
        self.assertTrue(restored.shadow.enabled)
        self.assertTrue(restored.layers.enabled)
        self.assertEqual(restored.layers.layers[0].name, "bass")
        self.assertEqual(restored.layers.layers[0].freq_range, (80, 1950))
        self.assertEqual(restored.layers.layers[0].boost, 4.25)

    def test_private_transport_round_trip_reaches_frame_boundary_controller(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            try:
                server = RuntimeControlServer("run-test", runtime_dir=directory)
            except OSError:
                self.skipTest("Unix-domain sockets unavailable")
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
                request = threading.Thread(
                    target=lambda: result.append(
                        server.request(operation, payload, timeout=1)
                    )
                )
                request.start()
                deadline = time.monotonic() + 1
                while request.is_alive() and time.monotonic() < deadline:
                    bridge.process_pending(
                        self.controller.handle_runtime_operation,
                        max_items=4,
                    )
                    time.sleep(0.002)
                request.join(1)
                self.assertFalse(request.is_alive())
                return result[0]

            snapshot = exchange("snapshot", {})
            self.assertEqual(snapshot["run_id"], "run-test")
            changed = exchange("command", {
                "command": {
                    "operation": "patch",
                    "changes": [
                        {"path": "visualizer.gradient", "value": True}
                    ],
                },
                "expected_revision": 0,
            })
            self.assertEqual(changed["revision"], 1)
            self.assertTrue(self.visualizer.gradient_mode)

            stop_event.set()
            bridge.close()
            server.close()
            listener.join(1)


if __name__ == "__main__":
    unittest.main()
