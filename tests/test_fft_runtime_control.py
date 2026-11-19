"""Hardware-free tests for the FFT in-process runtime-control contract."""

from __future__ import annotations

from dataclasses import dataclass
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

from config import Settings, load_settings  # noqa: E402
from config.settings import save_settings  # noqa: E402
from runtime_control import FftRuntimeController  # noqa: E402
from themes import get_theme  # noqa: E402
from matrix_controller.runtime_transport import (  # noqa: E402
    FrameBoundaryRuntimeBridge,
    RuntimeControlClient,
    RuntimeControlServer,
)


@dataclass(frozen=True)
class FakeAnalysisCandidate:
    min_freq: float = 100
    max_freq: float = 6300
    low_freq_weight: float = 0.45
    high_freq_weight: float = 10.0


@dataclass(frozen=True)
class FakeAnalysisPlan:
    candidate: FakeAnalysisCandidate


class FakeAudio:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.analysis_candidate = FakeAnalysisCandidate()
        self.active_plan = FakeAnalysisPlan(self.analysis_candidate)
        self.compiled_candidates = []
        self.activations = []
        self.input_activations = []
        self.analysis_input_state = (
            self.active_plan,
            self.settings.sensitivity.noise_floor,
        )
        self.compile_failure = None

    def compile_analysis_candidate(self, candidate):
        self.compiled_candidates.append(candidate)
        if self.compile_failure is not None:
            raise self.compile_failure
        return FakeAnalysisPlan(candidate)

    def activate_analysis_plan(self, candidate, plan) -> None:
        self.activations.append((candidate, plan))
        self.analysis_candidate = candidate
        self.active_plan = plan
        self.analysis_input_state = (
            self.active_plan,
            self.settings.sensitivity.noise_floor,
        )

    def activate_analysis_inputs(self) -> None:
        self.analysis_input_state = (
            self.active_plan,
            self.settings.sensitivity.noise_floor,
        )
        self.input_activations.append(self.analysis_input_state)


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
        self.shadow_buffer = None
        self.shadow_colors = None

    def set_theme(self, theme) -> None:
        self.theme = theme


class FftRuntimeControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings()
        self.visualizer = FakeVisualizer(self.settings)
        self.audio = FakeAudio(self.settings)
        self.cycler = FakeCycler()

        self.controller = FftRuntimeController(
            settings=self.settings,
            visualizer=self.visualizer,
            audio=self.audio,
            theme_cycler=self.cycler,
            width=8,
            height=8,
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
        self.assertFalse(
            any(
                item.get("path", "").startswith("layers.")
                or item.get("path") == "visualizer.layers_enabled"
                or item.get("action", "").startswith("layer.")
                for item in snapshot["controls"]
            )
        )
        self.assertNotIn("layers_enabled", snapshot["state"])
        self.assertNotIn("active_layer", snapshot["state"])
        self.assertNotIn("draw_order", snapshot["state"])
        self.assertNotIn("layer_states", snapshot["state"])

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
        self.assertEqual(self.settings.frequency.zoom_preset_index, 1)
        self.assertEqual(len(self.audio.compiled_candidates), 1)
        self.assertEqual(len(self.audio.activations), 1)
        self.assertEqual(self.audio.analysis_candidate.min_freq, 80)
        self.assertEqual(self.audio.analysis_candidate.max_freq, 1950)

    def test_rebuild_batch_compiles_and_activates_complete_candidate_once(self) -> None:
        result = self.controller.apply_request({
            "commands": [
                {
                    "op": "set",
                    "path": "settings.low_freq_weight",
                    "value": 1.25,
                },
                {
                    "op": "set",
                    "path": "settings.high_freq_weight",
                    "value": 9.5,
                },
            ]
        })

        self.assertTrue(result["ok"])
        self.assertEqual(result["snapshot"]["revision"], 1)
        self.assertEqual(len(self.audio.compiled_candidates), 1)
        self.assertEqual(len(self.audio.activations), 1)
        candidate = self.audio.compiled_candidates[0]
        self.assertEqual(candidate.low_freq_weight, 1.25)
        self.assertEqual(candidate.high_freq_weight, 9.5)
        self.assertIs(self.audio.analysis_candidate, candidate)
        self.assertIs(self.audio.active_plan, self.audio.activations[0][1])
        self.assertEqual(self.settings.sensitivity.low_freq_weight, 1.25)
        self.assertEqual(self.settings.sensitivity.high_freq_weight, 9.5)

    def test_noise_floor_only_activates_worker_inputs_without_compilation(self) -> None:
        original_candidate = self.audio.analysis_candidate
        original_plan = self.audio.active_plan

        result = self.controller.apply_request({
            "commands": [{
                "op": "set",
                "path": "settings.noise_floor",
                "value": 0.35,
            }]
        })

        self.assertTrue(result["ok"])
        self.assertEqual(result["snapshot"]["revision"], 1)
        self.assertEqual(self.audio.compiled_candidates, [])
        self.assertEqual(self.audio.activations, [])
        self.assertEqual(
            self.audio.input_activations,
            [(original_plan, 0.35)],
        )
        self.assertEqual(self.audio.analysis_input_state, (original_plan, 0.35))
        self.assertIs(self.audio.analysis_candidate, original_candidate)
        self.assertIs(self.audio.active_plan, original_plan)
        self.assertEqual(self.settings.sensitivity.noise_floor, 0.35)

    def test_rebuild_and_noise_activate_one_complete_worker_input_state(self) -> None:
        result = self.controller.apply_request({
            "commands": [
                {
                    "op": "set",
                    "path": "settings.low_freq_weight",
                    "value": 1.25,
                },
                {
                    "op": "set",
                    "path": "settings.noise_floor",
                    "value": 0.35,
                },
            ]
        })

        self.assertTrue(result["ok"])
        self.assertEqual(len(self.audio.compiled_candidates), 1)
        self.assertEqual(len(self.audio.activations), 1)
        self.assertEqual(self.audio.input_activations, [])
        activated_plan = self.audio.activations[0][1]
        self.assertEqual(
            self.audio.analysis_input_state,
            (activated_plan, 0.35),
        )
        self.assertIs(self.audio.active_plan, activated_plan)
        self.assertEqual(self.settings.sensitivity.noise_floor, 0.35)

    def test_compile_failure_leaves_all_live_state_unchanged(self) -> None:
        original_candidate = self.audio.analysis_candidate
        original_plan = self.audio.active_plan
        original_analysis_input_state = self.audio.analysis_input_state
        original_noise_floor = self.settings.sensitivity.noise_floor
        original_low_weight = self.settings.sensitivity.low_freq_weight
        self.audio.compile_failure = ValueError("candidate cannot be compiled")

        result = self.controller.apply_request({
            "commands": [
                {
                    "op": "set",
                    "path": "visualizer.gradient",
                    "value": True,
                },
                {
                    "op": "set",
                    "path": "settings.low_freq_weight",
                    "value": 2.0,
                },
                {
                    "op": "set",
                    "path": "settings.noise_floor",
                    "value": 0.35,
                },
            ]
        })

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_value")
        self.assertEqual(len(self.audio.compiled_candidates), 1)
        self.assertEqual(self.audio.activations, [])
        self.assertEqual(self.audio.input_activations, [])
        self.assertIs(self.audio.analysis_input_state, original_analysis_input_state)
        self.assertIs(self.audio.analysis_candidate, original_candidate)
        self.assertIs(self.audio.active_plan, original_plan)
        self.assertEqual(
            self.settings.sensitivity.noise_floor,
            original_noise_floor,
        )
        self.assertEqual(
            self.settings.sensitivity.low_freq_weight,
            original_low_weight,
        )
        self.assertFalse(self.visualizer.gradient_mode)
        self.assertEqual(self.controller.revision, 0)

    def test_legacy_layer_set_and_action_are_invalid_without_mutation(self) -> None:
        self.assertIsNone(self.controller.handle_key("l"))
        self.assertEqual(self.controller.revision, 0)

        for command in (
            {"op": "set", "path": "layers.bass.visible", "value": False},
            {
                "op": "action",
                "path": "layer.move",
                "args": {"direction": 1},
            },
        ):
            result = self.controller.apply_request({"commands": [command]})

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "invalid_path")
            self.assertEqual(self.controller.revision, 0)
            self.assertEqual(self.audio.compiled_candidates, [])
            self.assertEqual(self.audio.activations, [])

    def test_zoom_index_stages_the_selected_frequency_range(self) -> None:
        result = self.controller.apply_request({
            "commands": [{
                "op": "set",
                "path": "settings.zoom_preset_index",
                "value": 2,
            }]
        })

        self.assertTrue(result["ok"])
        self.assertEqual(self.settings.frequency.zoom_preset_index, 2)
        self.assertEqual(len(self.audio.compiled_candidates), 1)
        self.assertEqual(len(self.audio.activations), 1)
        candidate = self.audio.analysis_candidate
        self.assertEqual(candidate.min_freq, 60)
        self.assertEqual(candidate.max_freq, 14000)

    def test_multiple_zoom_commands_compile_only_the_final_range(self) -> None:
        result = self.controller.apply_request({
            "commands": [
                {"op": "action", "path": "zoom.next"},
                {"op": "action", "path": "zoom.next"},
                {"op": "action", "path": "zoom.previous"},
            ]
        })

        self.assertTrue(result["ok"])
        self.assertEqual(self.settings.frequency.zoom_preset_index, 1)
        self.assertEqual(len(self.audio.compiled_candidates), 1)
        self.assertEqual(len(self.audio.activations), 1)
        candidate = self.audio.analysis_candidate
        self.assertEqual(candidate.min_freq, 80)
        self.assertEqual(candidate.max_freq, 1950)

    def test_zoom_and_weights_compile_one_complete_final_candidate(self) -> None:
        result = self.controller.apply_request({
            "commands": [
                {"op": "action", "path": "zoom.next"},
                {
                    "op": "set",
                    "path": "settings.low_freq_weight",
                    "value": 1.25,
                },
                {
                    "op": "set",
                    "path": "settings.high_freq_weight",
                    "value": 9.5,
                },
            ]
        })

        self.assertTrue(result["ok"])
        self.assertEqual(len(self.audio.compiled_candidates), 1)
        self.assertEqual(len(self.audio.activations), 1)
        candidate = self.audio.analysis_candidate
        self.assertEqual(candidate.min_freq, 80)
        self.assertEqual(candidate.max_freq, 1950)
        self.assertEqual(candidate.low_freq_weight, 1.25)
        self.assertEqual(candidate.high_freq_weight, 9.5)

    def test_non_rebuild_controls_do_not_compile_or_replace_plan(self) -> None:
        original_candidate = self.audio.analysis_candidate
        original_plan = self.audio.active_plan

        result = self.controller.apply_request({
            "commands": [
                {
                    "op": "set",
                    "path": "visualizer.gradient",
                    "value": True,
                },
                {
                    "op": "set",
                    "path": "settings.smoothing_rise",
                    "value": 0.75,
                },
            ]
        })

        self.assertTrue(result["ok"])
        self.assertTrue(self.visualizer.gradient_mode)
        self.assertEqual(self.settings.smoothing.rise, 0.75)
        self.assertEqual(self.audio.compiled_candidates, [])
        self.assertEqual(self.audio.activations, [])
        self.assertEqual(self.audio.input_activations, [])
        self.assertIs(self.audio.analysis_candidate, original_candidate)
        self.assertIs(self.audio.active_plan, original_plan)

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

    def test_settings_round_trip_restores_shadow_without_legacy_layers(self) -> None:
        self.settings.shadow.enabled = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(self.settings, str(path))
            restored = load_settings(str(path))
        self.assertTrue(restored.shadow.enabled)
        self.assertFalse(hasattr(restored, "layers"))

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
