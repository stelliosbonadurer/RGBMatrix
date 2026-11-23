"""Focused tests for the public 1-8 layer configuration tool."""

from __future__ import annotations

import contextlib
import copy
import io
from pathlib import Path
import unittest
from unittest import mock

from tools import fft_layers


FIELDS = [
    {"name": "theme", "default": "ocean"},
    {"name": "gradient", "default": False},
    {"name": "overflow", "default": True},
    {"name": "overflow_multiplier", "default": 1.5},
    {"name": "bars", "default": True},
    {"name": "full", "default": False},
    {"name": "shadow_enabled", "default": False},
    {"name": "peak_enabled", "default": False},
    {"name": "peak_color_mode", "default": "contrast"},
    {"name": "peak_fall_speed", "default": 0.08},
    {"name": "peak_hold_frames", "default": 8},
    {"name": "sensitivity_scalar", "default": 1.0},
    {"name": "smoothing_rise", "default": 0.4},
    {"name": "smoothing_fall", "default": 0.1},
    {"name": "dynamic_cycle_speed", "default": 0.06},
    {"name": "dynamic_frozen", "default": False},
]
PILLOW_CONSTRAINTS = {
    "theme": "ocean",
    "gradient": False,
    "overflow": True,
    "bars": True,
    "full": False,
    "shadow_enabled": False,
    "peak_enabled": False,
    "dynamic_cycle_speed": 0.06,
    "dynamic_frozen": False,
}


def snapshot(*, layers: list[dict] | None = None) -> dict:
    settings = {field["name"]: field["default"] for field in FIELDS}
    settings.update({"theme": "warm", "gradient": True})
    if layers is None:
        layers = [{
            "layer_id": "current-bars",
            "layer_type": "current-bars",
            "analyzer_id": "main-audio",
            "visible": True,
            "settings": settings,
        }]
    return {
        "run_id": 7,
        "revision": 3,
        "structure": {
            "output_backend": "direct",
            "layers": layers,
            "available_analyzer_ids": ["main-audio"],
            "capabilities": {
                "output_backends": ["direct", "pillow-bulk"],
                "limits": {"max_layers": 8, "max_operations": 32},
                "layer_types": [{
                    "layer_type": "current-bars",
                    "output_backends": ["direct", "pillow-bulk"],
                    "settings": {
                        "fields": copy.deepcopy(FIELDS),
                        "backend_constraints": {
                            "pillow-bulk": dict(PILLOW_CONSTRAINTS),
                        },
                    },
                }],
            },
        },
    }


class LayerPlanTests(unittest.TestCase):
    def test_all_counts_and_backends_build_one_bounded_web_action(self) -> None:
        for backend in fft_layers.SUPPORTED_BACKENDS:
            for count in range(1, 9):
                with self.subTest(backend=backend, count=count):
                    action = fft_layers.build_structure_action(
                        snapshot(), count, backend
                    )
                    self.assertEqual(action["run_id"], 7)
                    self.assertEqual(action["expected_revision"], 3)
                    self.assertEqual(action["action"], "structure.apply")
                    operations = action["args"]["operations"]
                    self.assertLessEqual(len(operations), 32)
                    added = [
                        operation["layer"]
                        for operation in operations
                        if operation["op"] == "add_layer"
                    ]
                    self.assertEqual(
                        [layer["layer_id"] for layer in added],
                        ["current-bars", *(f"layer-{index}" for index in range(2, count + 1))],
                    )
                    self.assertEqual(
                        {layer["analyzer_id"] for layer in added},
                        {"main-audio"},
                    )
                    self.assertEqual(
                        operations[-1],
                        {"op": "set_output_backend", "output_backend": backend},
                    )
                    if backend == "pillow-bulk":
                        for layer in added:
                            for name, value in PILLOW_CONSTRAINTS.items():
                                self.assertEqual(layer["settings"][name], value)
                    else:
                        self.assertEqual(added[0]["settings"]["theme"], "warm")
                        self.assertTrue(added[0]["settings"]["gradient"])

    def test_empty_structure_uses_advertised_defaults(self) -> None:
        action = fft_layers.build_structure_action(
            snapshot(layers=[]), 2, "direct"
        )
        added = [
            operation["layer"]
            for operation in action["args"]["operations"]
            if operation["op"] == "add_layer"
        ]
        self.assertEqual(added[0]["settings"]["theme"], "ocean")
        self.assertFalse(added[0]["settings"]["gradient"])

    def test_snapshot_limits_and_catalog_are_authoritative(self) -> None:
        cases = []
        too_small = snapshot()
        too_small["structure"]["capabilities"]["limits"]["max_layers"] = 4
        cases.append((too_small, 5, "direct", "at most 4"))
        no_backend = snapshot()
        no_backend["structure"]["capabilities"]["output_backends"] = ["direct"]
        cases.append((no_backend, 1, "pillow-bulk", "does not advertise"))
        too_few_ops = snapshot()
        too_few_ops["structure"]["capabilities"]["limits"]["max_operations"] = 2
        cases.append((too_few_ops, 2, "direct", "needs 4 operations"))
        for value, count, backend, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                fft_layers.LayerToolError, message
            ):
                fft_layers.build_structure_action(value, count, backend)

    def test_direct_is_the_command_default(self) -> None:
        args = fft_layers.parse_args(["--layers", "3"])
        self.assertEqual(args.backend, "direct")


class LayerToolMainTests(unittest.TestCase):
    def test_apply_reads_snapshot_then_posts_action_and_verifies_response(self) -> None:
        initial = snapshot()
        applied = snapshot(layers=[
            {
                **initial["structure"]["layers"][0],
                "layer_id": "current-bars",
            },
            {
                **initial["structure"]["layers"][0],
                "layer_id": "layer-2",
            },
        ])
        applied["revision"] = 4
        requests = []

        def request(base_url, token, method, path, body=None, **_kwargs):
            requests.append((base_url, token, method, path, body))
            return initial if method == "GET" else applied

        output = io.StringIO()
        with (
            mock.patch.object(fft_layers, "_read_token", return_value="secret"),
            mock.patch.object(fft_layers, "_request_json", side_effect=request),
            contextlib.redirect_stdout(output),
        ):
            code = fft_layers.main([
                "--layers", "2",
                "--token-file", str(Path("unused")),
            ])

        self.assertEqual(code, 0)
        self.assertEqual([item[2:4] for item in requests], [
            ("GET", "/api/v1/runtime"),
            ("POST", "/api/v1/runtime/actions"),
        ])
        self.assertEqual(requests[1][4]["action"], "structure.apply")
        self.assertNotIn("secret", output.getvalue())
        self.assertIn("Applied 2 layer(s) with direct", output.getvalue())


if __name__ == "__main__":
    unittest.main()
