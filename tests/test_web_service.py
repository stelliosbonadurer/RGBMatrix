"""HTTP contract tests with an injected supervisor and no subprocesses."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from matrix_controller.bluetooth import (
    BluetoothController,
    BluetoothDevice,
    FakeBluetoothBackend,
)
from matrix_controller.presets import MAX_PRESETS, PresetStore
from matrix_controller.registry import build_default_registry
from matrix_controller.runtime_transport import MAX_LAUNCH_STRUCTURE_BYTES
from matrix_controller.web_service import (
    _is_loopback_address,
    build_handler,
    validate_launch_structure,
)
from matrix_controller.pairing import PairingManager


class FakeSupervisor:
    def __init__(self):
        self.started: list[tuple[str, dict]] = []
        self.start_kwargs: list[dict] = []
        self.stop_count = 0
        self.runtime_commands: list[dict] = []
        self.runtime = {
            "protocol_version": 1,
            "supported": True,
            "connected": True,
            "run_id": 1,
            "visualization_id": "fft",
            "revision": 4,
            "state": {"visualizer": {"gradient": False}},
            "controls": [
                {
                    "id": "visualizer.gradient",
                    "path": "visualizer.gradient",
                    "label": "Gradient",
                    "type": "boolean",
                    "group": "visualizer",
                    "value": False,
                },
                {
                    "id": "dynamic.reseed",
                    "type": "action",
                    "action": "dynamic.reseed",
                    "group": "theme",
                    "label": "Reseed",
                },
            ],
        }

    def list_visualizations(self):
        return [{"id": "fft", "name": "Music FFT", "parameters": []}]

    def status(self):
        return {"state": "idle", "visualization_id": None}

    def start(self, visualization_id, parameters, **kwargs):
        self.started.append((visualization_id, parameters))
        self.start_kwargs.append(kwargs)
        return SimpleNamespace(
            visualization_id=visualization_id,
            parameters=parameters,
            pid=42,
            run_id=1,
        )

    def stop(self):
        self.stop_count += 1
        return self.status()

    def runtime_snapshot(self):
        return self.runtime

    def runtime_command(self, command, **_kwargs):
        self.runtime_commands.append(command)
        if self.runtime_error is not None:
            raise self.runtime_error
        self.runtime["revision"] += 1
        return self.runtime

    runtime_error: Exception | None = None


class FakeNetworkClient:
    def __init__(self):
        self.requests = []

    def request(self, operation, *, payload=None, token=None):
        self.requests.append((operation, payload, token))
        if operation == "status":
            return {"mode": "lan", "state": "lan_connected", "ipv4": "10.0.0.9"}
        if operation == "provision":
            return {"job_id": "job-1", "state": "scheduled"}
        return {}


class WebServiceTests(unittest.TestCase):
    def test_loopback_address_detection_supports_ipv4_ipv6_and_mapped_ipv4(self) -> None:
        self.assertTrue(_is_loopback_address("127.0.0.1"))
        self.assertTrue(_is_loopback_address("::1"))
        self.assertTrue(_is_loopback_address("::ffff:127.0.0.1"))
        self.assertFalse(_is_loopback_address("192.168.1.143"))
        self.assertFalse(_is_loopback_address("not-an-address"))

    def setUp(self) -> None:
        self.supervisor = FakeSupervisor()
        self.network = FakeNetworkClient()
        self.pairing = PairingManager(
            "012345",
            token_factory=lambda: "temporary-session-token-with-128-bits",
        )
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            build_handler(
                self.supervisor,
                api_token="secret-token",
                network_client=self.network,
                pairing_manager=self.pairing,
            ),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(
        self,
        path: str,
        *,
        payload=None,
        token: str | None = None,
        method=None,
        headers: dict[str, str] | None = None,
        include_headers: bool = False,
    ):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if token is not None:
            request.add_header("Authorization", f"Bearer {token}")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        with urllib.request.urlopen(request, timeout=2) as response:
            body = json.load(response)
            if include_headers:
                return response.status, body, response.headers
            return response.status, body

    def request_raw(self, path: str):
        with urllib.request.urlopen(self.base_url + path, timeout=2) as response:
            return response.status, response.headers, response.read().decode("utf-8")

    def test_control_page_and_assets_are_served(self) -> None:
        status, headers, body = self.request_raw("/")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "text/html")
        self.assertIn("RGB Matrix Control", body)
        self.assertIn('id="visualization-grid"', body)
        self.assertIn('id="quit-button"', body)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])

        css_status, css_headers, css = self.request_raw("/static/control.css")
        self.assertEqual(css_status, 200)
        self.assertEqual(css_headers.get_content_type(), "text/css")
        self.assertIn(".visualization-grid", css)

        js_status, js_headers, script = self.request_raw("/static/control.js")
        self.assertEqual(js_status, 200)
        self.assertEqual(js_headers.get_content_type(), "text/javascript")
        self.assertIn("/api/v1/visualizations", script)
        self.assertIn("/api/v1/run", script)
        self.assertIn("/api/v1/stop", script)
        self.assertIn("createParameterField", script)
        self.assertIn("configureGraphTraversalForm", script)
        self.assertIn("/config/fft", script)

        config_status, config_headers, config_page = self.request_raw("/config/fft")
        self.assertEqual(config_status, 200)
        self.assertEqual(config_headers.get_content_type(), "text/html")
        self.assertIn("Music Controls", config_page)
        self.assertIn('id="music-controls"', config_page)
        self.assertIn("default-src 'self'", config_headers["Content-Security-Policy"])

        config_js_status, config_js_headers, config_script = self.request_raw(
            "/static/fft-config.js"
        )
        self.assertEqual(config_js_status, 200)
        self.assertEqual(config_js_headers.get_content_type(), "text/javascript")
        self.assertIn("/api/v1/runtime", config_script)
        self.assertIn("/api/v1/runtime/actions", config_script)
        self.assertIn("/api/v1/pairing/exchange", config_script)
        self.assertIn("X-RGB-Matrix-Session", config_script)
        self.assertNotIn("sessionStorage", config_script)

    def test_read_endpoints_do_not_require_token(self) -> None:
        status, body = self.request("/api/v1/visualizations")
        self.assertEqual(status, 200)
        self.assertEqual(body["visualizations"][0]["id"], "fft")

    def test_run_requires_token_and_uses_structured_body(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(
                "/api/v1/run",
                payload={"visualization_id": "fft", "parameters": {}},
            )
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()

        status, body = self.request(
            "/api/v1/run",
            payload={"visualization_id": "fft", "parameters": {"theme": "warm"}},
            token="secret-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["pid"], 42)
        self.assertEqual(self.supervisor.started, [("fft", {"theme": "warm"})])

    def test_run_forwards_a_bounded_launch_structure(self) -> None:
        document = self._structure_document()
        status, _body = self.request(
            "/api/v1/run",
            payload={
                "visualization_id": "fft",
                "parameters": {},
                "structure": document,
            },
            token="secret-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.supervisor.start_kwargs[-1], {"structure": document})

        # Without a structure the supervisor sees the original call shape.
        self.request(
            "/api/v1/run",
            payload={"visualization_id": "fft", "parameters": {}},
            token="secret-token",
        )
        self.assertEqual(self.supervisor.start_kwargs[-1], {})

    def test_run_rejects_bad_structure_shape_before_supervisor(self) -> None:
        too_many = {"layers": [{} for _ in range(33)]}
        too_deep: dict = {}
        node = too_deep
        for _ in range(10):
            node["x"] = {}
            node = node["x"]
        for payload in (
            {"visualization_id": "fft", "structure": ["layers"]},
            {"visualization_id": "fft", "structure": too_many},
            {"visualization_id": "fft", "structure": too_deep},
            {"visualization_id": "fft", "structur": {}},
        ):
            with self.subTest(payload=str(payload)[:40]), self.assertRaises(
                urllib.error.HTTPError
            ) as caught:
                self.request("/api/v1/run", payload=payload, token="secret-token")
            self.assertEqual(caught.exception.code, 400)
            caught.exception.close()
        self.assertEqual(self.supervisor.started, [])

    def test_runtime_snapshot_requires_control_authentication(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/v1/runtime")
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()

        status, body = self.request("/api/v1/runtime", token="secret-token")
        self.assertEqual(status, 200)
        self.assertEqual(body["visualization_id"], "fft")
        self.assertEqual(body["controls"][0]["path"], "visualizer.gradient")

    def test_runtime_patch_is_bounded_and_forwards_typed_command(self) -> None:
        payload = {
            "run_id": 1,
            "expected_revision": 4,
            "changes": [{"path": "visualizer.gradient", "value": True}],
        }
        status, body = self.request(
            "/api/v1/runtime",
            payload=payload,
            token="secret-token",
            method="PATCH",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["revision"], 5)
        self.assertEqual(
            self.supervisor.runtime_commands[-1],
            {
                "operation": "patch",
                "run_id": 1,
                "expected_revision": 4,
                "changes": [{"path": "visualizer.gradient", "value": True}],
            },
        )

    def test_runtime_action_forwards_allowlisted_envelope(self) -> None:
        payload = {
            "run_id": 1,
            "expected_revision": 4,
            "action": "dynamic.reseed",
            "target": "active",
            "args": {},
        }
        status, body = self.request(
            "/api/v1/runtime/actions",
            payload=payload,
            token="secret-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["revision"], 5)
        self.assertEqual(self.supervisor.runtime_commands[-1]["operation"], "action")
        self.assertEqual(self.supervisor.runtime_commands[-1]["action"], "dynamic.reseed")

    def _structure_payload(self) -> dict:
        settings = {
            "theme": "ocean", "gradient": False, "overflow": True,
            "overflow_multiplier": 1.5, "bars": True, "full": False,
            "shadow_enabled": False, "peak_enabled": False,
            "peak_color_mode": "contrast", "peak_fall_speed": 0.08,
            "peak_hold_frames": 8, "sensitivity_scalar": 1.0,
            "smoothing_rise": 0.4, "smoothing_fall": 0.1,
            "dynamic_cycle_speed": 0.06, "dynamic_frozen": False,
        }
        return {
            "run_id": 1,
            "expected_revision": 4,
            "action": "structure.set",
            "args": self._structure_document(settings),
        }

    @staticmethod
    def _structure_document(settings: dict | None = None) -> dict:
        settings = settings or {"theme": "ocean", "peak_hold_frames": 8}
        return {
            "output_backend": "direct",
            "layers": [
                {
                    "layer_id": layer_id, "layer_type": "current-bars",
                    "analyzer_id": "main-audio", "visible": True,
                    "settings": settings,
                }
                for layer_id in ("top", "current-bars")
            ],
        }

    @staticmethod
    def _largest_structure_document() -> dict:
        """8 layers with 64-character IDs, every setting, and 16 weight points."""
        settings = {
            "theme": "dynamic_lateral_gradient", "gradient": False, "overflow": True,
            "overflow_multiplier": 1.523456789012345, "bars": True, "full": False,
            "shadow_enabled": False, "peak_enabled": False,
            "peak_color_mode": "contrast", "peak_fall_speed": 0.08123456789012345,
            "peak_hold_frames": 600, "sensitivity_scalar": 1.0123456789012345,
            "smoothing_rise": 0.4123456789012345, "smoothing_fall": 0.1123456789012345,
            "dynamic_cycle_speed": 0.06123456789012345, "dynamic_frozen": False,
            "min_freq": 100.12345678901234, "max_freq": 6300.123456789012,
            "noise_floor": 0.3123456789012345,
            "weight_points": [
                {"position": index / 15 + 1e-13, "weight": 99.12345678901234}
                for index in range(15)
            ] + [{"position": 1.0, "weight": 0.123456789012345}],
        }
        return {
            "output_backend": "pillow-bulk",
            "layers": [
                {
                    "layer_id": f"{index}".ljust(64, "x"), "layer_type": "current-bars",
                    "analyzer_id": "main-audio", "visible": True,
                    "settings": dict(settings),
                }
                for index in range(8)
            ],
        }

    def test_largest_weighted_structure_fits_the_web_and_launch_bounds(self) -> None:
        # web-control-refactor M2 AC 8: weight_points[k].weight sits at depth 6
        # of _bounded_runtime_value's 8, and 16 points stay under the 32-item cap.
        document = self._largest_structure_document()
        encoded = json.dumps(document, separators=(",", ":"), allow_nan=False)
        self.assertLessEqual(len(encoded.encode("utf-8")), MAX_LAUNCH_STRUCTURE_BYTES)

        status, _body = self.request(
            "/api/v1/run",
            payload={"visualization_id": "fft", "parameters": {}, "structure": document},
            token="secret-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.supervisor.start_kwargs[-1], {"structure": document})

        status, _body = self.request(
            "/api/v1/runtime/actions",
            payload={
                "run_id": 1, "expected_revision": 4,
                "action": "structure.set", "args": document,
            },
            token="secret-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.supervisor.runtime_commands[-1]["args"], document)

    def test_runtime_structure_batch_passes_existing_action_bounds(self) -> None:
        status, _body = self.request(
            "/api/v1/runtime/actions",
            payload=self._structure_payload(),
            token="secret-token",
        )
        self.assertEqual(status, 200)
        command = self.supervisor.runtime_commands[-1]
        self.assertEqual(command["action"], "structure.set")
        self.assertEqual(
            command["args"]["layers"][0]["settings"]["peak_hold_frames"],
            8,
        )

    def test_runtime_structure_errors_include_bounded_field_path(self) -> None:
        from matrix_controller.runtime_transport import RuntimeRemoteError

        for error, status in (
            (RuntimeRemoteError(
                "invalid_reference",
                "unknown layer ID",
                path="structure.layers[1].settings.max_freq",
            ), 400),
            (RuntimeRemoteError("revision_conflict", "runtime state changed"), 409),
            (RuntimeRemoteError("busy", "structural request in flight"), 503),
        ):
            self.supervisor.runtime_error = error
            with self.subTest(code=error.code), self.assertRaises(
                urllib.error.HTTPError
            ) as caught:
                self.request(
                    "/api/v1/runtime/actions",
                    payload=self._structure_payload(),
                    token="secret-token",
                )
            body = json.loads(caught.exception.read().decode("utf-8"))
            caught.exception.close()
            self.assertEqual(caught.exception.code, status)
            self.assertEqual(body["code"], error.code)
            if error.path is None:
                self.assertNotIn("path", body)
            else:
                self.assertEqual(body["path"], error.path)

    def test_runtime_patch_rejects_unknown_fields_before_supervisor(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(
                "/api/v1/runtime",
                payload={
                    "run_id": 1,
                    "changes": [{"path": "x", "value": True}],
                    "shell": "echo unsafe",
                },
                token="secret-token",
                method="PATCH",
            )
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()
        self.assertEqual(self.supervisor.runtime_commands, [])

    def test_stop_requires_token_and_quits_current_visualization(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/v1/stop", payload={})
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()

        status, body = self.request("/api/v1/stop", payload={}, token="secret-token")
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "idle")
        self.assertEqual(self.supervisor.stop_count, 1)

    def test_boot_pairing_code_creates_control_only_session(self) -> None:
        status, paired = self.request(
            "/api/v1/pairing/exchange",
            payload={"code": "012345"},
        )
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(paired["token"]), 22)

        status, body = self.request(
            "/api/v1/stop",
            payload={},
            token=paired["token"],
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "idle")

        with self.assertRaises(urllib.error.HTTPError) as blocked:
            self.request(
                "/api/v1/network/provision",
                payload={
                    "ssid": "Home",
                    "passphrase": "password",
                    "hidden": False,
                    "confirm_disconnect": True,
                },
                token=paired["token"],
            )
        self.assertEqual(blocked.exception.code, 403)
        blocked.exception.close()

    def test_pairing_code_is_reusable_and_temporary_session_can_logout(self) -> None:
        _, first = self.request(
            "/api/v1/pairing/exchange",
            payload={"code": "012345"},
        )
        _, second = self.request(
            "/api/v1/pairing/exchange",
            payload={"code": "012345"},
        )
        self.assertTrue(first["token"])
        self.assertTrue(second["token"])

        status, pairing_status = self.request(
            "/api/v1/pairing/status",
            token=first["token"],
        )
        self.assertEqual(status, 200)
        self.assertTrue(pairing_status["authorized"])
        self.assertTrue(pairing_status["temporary_session"])

        status, _body = self.request(
            "/api/v1/pairing/logout",
            payload={},
            token=first["token"],
        )
        self.assertEqual(status, 200)
        with self.assertRaises(urllib.error.HTTPError) as logged_out:
            self.request("/api/v1/stop", payload={}, token=first["token"])
        self.assertEqual(logged_out.exception.code, 401)
        logged_out.exception.close()

    def test_browser_pairing_cookie_survives_pages_and_requires_same_origin_for_mutation(self) -> None:
        status, paired, response_headers = self.request(
            "/api/v1/pairing/exchange",
            payload={"code": "012345"},
            headers={"X-RGB-Matrix-Session": "cookie"},
            include_headers=True,
        )
        self.assertEqual(status, 200)
        self.assertTrue(paired["paired"])
        self.assertNotIn("token", paired)

        set_cookie = response_headers["Set-Cookie"]
        self.assertIn("rgb_matrix_session=", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Strict", set_cookie)
        self.assertIn("Path=/", set_cookie)
        self.assertIn("Max-Age=14400", set_cookie)
        self.assertNotIn("Secure", set_cookie)
        cookie = set_cookie.split(";", 1)[0]

        status, pairing_status = self.request(
            "/api/v1/pairing/status",
            headers={"Cookie": cookie},
        )
        self.assertEqual(status, 200)
        self.assertTrue(pairing_status["authorized"])
        self.assertTrue(pairing_status["temporary_session"])

        # Reading state is safe without an Origin header. Mutations using ambient
        # cookie authority must prove that the browser is on this exact origin.
        status, runtime = self.request(
            "/api/v1/runtime",
            headers={"Cookie": cookie},
        )
        self.assertEqual(status, 200)
        self.assertEqual(runtime["visualization_id"], "fft")

        with self.assertRaises(urllib.error.HTTPError) as missing_origin:
            self.request("/api/v1/stop", payload={}, headers={"Cookie": cookie})
        self.assertEqual(missing_origin.exception.code, 403)
        missing_origin.exception.close()

        with self.assertRaises(urllib.error.HTTPError) as wrong_origin:
            self.request(
                "/api/v1/stop",
                payload={},
                headers={"Cookie": cookie, "Origin": "http://example.invalid"},
            )
        self.assertEqual(wrong_origin.exception.code, 403)
        wrong_origin.exception.close()

        status, runtime = self.request(
            "/api/v1/runtime",
            payload={
                "run_id": 1,
                "expected_revision": 4,
                "changes": [{"path": "visualizer.gradient", "value": True}],
            },
            method="PATCH",
            headers={"Cookie": cookie, "Origin": self.base_url},
        )
        self.assertEqual(status, 200)
        self.assertEqual(runtime["revision"], 5)

        status, runtime = self.request(
            "/api/v1/runtime/actions",
            payload={
                "run_id": 1,
                "expected_revision": 5,
                "action": "dynamic.reseed",
                "args": {},
            },
            headers={"Cookie": cookie, "Origin": self.base_url},
        )
        self.assertEqual(status, 200)
        self.assertEqual(runtime["revision"], 6)

        status, _body = self.request(
            "/api/v1/stop",
            payload={},
            headers={"Cookie": cookie, "Origin": self.base_url},
        )
        self.assertEqual(status, 200)

        with self.assertRaises(urllib.error.HTTPError) as blocked_network:
            self.request(
                "/api/v1/network/provision",
                payload={
                    "ssid": "Home",
                    "passphrase": "password",
                    "hidden": False,
                    "confirm_disconnect": True,
                },
                headers={"Cookie": cookie, "Origin": self.base_url},
            )
        self.assertEqual(blocked_network.exception.code, 403)
        blocked_network.exception.close()

        status, _body, logout_headers = self.request(
            "/api/v1/pairing/logout",
            payload={},
            headers={"Cookie": cookie, "Origin": self.base_url},
            include_headers=True,
        )
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", logout_headers["Set-Cookie"])
        with self.assertRaises(urllib.error.HTTPError) as logged_out:
            self.request("/api/v1/runtime", headers={"Cookie": cookie})
        self.assertEqual(logged_out.exception.code, 401)
        logged_out.exception.close()

    def test_non_object_json_is_rejected(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/v1/run", payload=["fft"], token="secret-token")
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()

    def test_network_status_is_proxied_without_token(self) -> None:
        status, body = self.request("/api/v1/network/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["mode"], "lan")
        self.assertEqual(self.network.requests[-1], ("status", None, None))

    def test_network_provision_forwards_body_and_bearer_token(self) -> None:
        payload = {
            "ssid": "Home",
            "passphrase": "password",
            "hidden": False,
            "confirm_disconnect": True,
        }
        status, body = self.request(
            "/api/v1/network/provision", payload=payload, token="setup-token"
        )
        self.assertEqual(status, 202)
        self.assertEqual(body["job_id"], "job-1")
        self.assertEqual(self.network.requests[-1], ("provision", payload, "setup-token"))

    def test_preset_routes_without_a_store_are_unavailable(self) -> None:
        for method, path in (("GET", "/api/v1/presets"), ("POST", "/api/v1/presets")):
            with self.subTest(method=method), self.assertRaises(urllib.error.HTTPError) as caught:
                self.request(
                    path,
                    payload={} if method == "POST" else None,
                    method=method,
                    token="secret-token",
                )
            self.assertEqual(caught.exception.code, 503)
            self.assertEqual(json.load(caught.exception)["code"], "preset_store_unavailable")
            caught.exception.close()

    def test_endpoint_list_names_the_preset_and_bluetooth_routes(self) -> None:
        _, body = self.request("/api/v1")
        for endpoint in (
            "GET /api/v1/presets",
            "GET /api/v1/presets/{preset_id}",
            "POST /api/v1/presets",
            "PUT /api/v1/presets/{preset_id}",
            "DELETE /api/v1/presets/{preset_id}",
            "GET /api/v1/bluetooth",
            "POST /api/v1/bluetooth/actions",
            "GET /api/v1/audio/sources",
        ):
            self.assertIn(endpoint, body["endpoints"])

    def test_bluetooth_without_a_controller_is_unavailable(self) -> None:
        status, body = self.request("/api/v1/bluetooth", token="secret-token")
        self.assertEqual(status, 200)
        self.assertEqual(body["available"], False)
        self.assertEqual(body["reason"], "Bluetooth control is not configured")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(
                "/api/v1/bluetooth/actions",
                payload={"action": "window.open"},
                token="secret-token",
            )
        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(json.load(caught.exception)["code"], "bluetooth_unavailable")
        caught.exception.close()
        status, body = self.request("/api/v1/audio/sources", token="secret-token")
        self.assertEqual(status, 200)
        self.assertEqual(body["sources"][1]["connected"], None)


class PresetApiTests(unittest.TestCase):
    """M3 criteria 2-4 and 9 over HTTP, with a real store in a temp directory."""

    request = WebServiceTests.request
    _structure_document = staticmethod(WebServiceTests._structure_document)

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.preset_path = Path(self.directory.name) / "fft-presets.json"
        self.fft = build_default_registry().get("fft")
        self.store = PresetStore(
            self.preset_path,
            validate_parameters=self.fft.validate_parameters,
            validate_structure=validate_launch_structure,
        )
        self.supervisor = FakeSupervisor()
        self.pairing = PairingManager(
            "012345",
            token_factory=lambda: "temporary-session-token-with-128-bits",
        )
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            build_handler(
                self.supervisor,
                api_token="secret-token",
                network_client=FakeNetworkClient(),
                pairing_manager=self.pairing,
                preset_store=self.store,
            ),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"
        _, paired = self.request("/api/v1/pairing/exchange", payload={"code": "012345"})
        self.session = paired["token"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.directory.cleanup()

    def error(self, path: str, **kwargs) -> tuple[int, dict]:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(path, **kwargs)
        try:
            return caught.exception.code, json.load(caught.exception)
        finally:
            caught.exception.close()

    def preset_payload(self, name: str = "Warm bass") -> dict:
        return {
            "name": name,
            "parameters": {"theme": "warm", "audio_source": "bluetooth"},
            "structure": self._structure_document(),
        }

    def test_paired_session_saves_loads_launches_edits_and_deletes(self) -> None:
        status, created = self.request(
            "/api/v1/presets", payload=self.preset_payload(), token=self.session
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["revision"], 1)
        preset_id = created["preset_id"]

        status, listing = self.request("/api/v1/presets", token=self.session)
        self.assertEqual(status, 200)
        self.assertEqual(
            listing["presets"],
            [{"preset_id": preset_id, "name": "Warm bass", "revision": 1}],
        )
        self.assertEqual(listing["limits"]["max_presets"], MAX_PRESETS)

        status, loaded = self.request(f"/api/v1/presets/{preset_id}", token=self.session)
        self.assertEqual(status, 200)
        self.assertEqual(loaded["parameters"], {"theme": "warm", "audio_source": "bluetooth"})

        # The loaded pair goes to launch unchanged.
        status, _ = self.request(
            "/api/v1/run",
            payload={
                "visualization_id": "fft",
                "parameters": loaded["parameters"],
                "structure": loaded["structure"],
            },
            token=self.session,
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.supervisor.started[-1], ("fft", loaded["parameters"]))
        self.assertEqual(self.supervisor.start_kwargs[-1], {"structure": loaded["structure"]})

        replacement = dict(self.preset_payload("Renamed"), expected_revision=1)
        status, replaced = self.request(
            f"/api/v1/presets/{preset_id}",
            payload=replacement,
            method="PUT",
            token=self.session,
        )
        self.assertEqual(status, 200)
        self.assertEqual((replaced["name"], replaced["revision"]), ("Renamed", 2))

        before = self.preset_path.read_bytes()
        for method, payload in (
            ("PUT", replacement),
            ("DELETE", {"expected_revision": 1}),
        ):
            with self.subTest(method=method):
                status, body = self.error(
                    f"/api/v1/presets/{preset_id}",
                    payload=payload,
                    method=method,
                    token=self.session,
                )
                self.assertEqual(status, 409)
                self.assertEqual(body["code"], "revision_conflict")
                self.assertEqual(body["current_revision"], 2)
        self.assertEqual(self.preset_path.read_bytes(), before)

        status, deleted = self.request(
            f"/api/v1/presets/{preset_id}",
            payload={"expected_revision": 2},
            method="DELETE",
            token=self.session,
        )
        self.assertEqual((status, deleted), (200, {"deleted": True, "preset_id": preset_id}))
        status, body = self.error(f"/api/v1/presets/{preset_id}", token=self.session)
        self.assertEqual((status, body["code"]), (404, "preset_not_found"))

    def test_rejections_carry_codes_and_leave_the_file_unchanged(self) -> None:
        self.request("/api/v1/presets", payload=self.preset_payload(), token=self.session)
        before = self.preset_path.read_bytes()
        oversize = self._structure_document()
        oversize["layers"][0]["settings"]["pad"] = ["x" * 250 for _ in range(32)]
        # About 40 KB: over the 32 KB structure cap, under the 64 KB request cap.
        oversize["layers"] = [oversize["layers"][0]] * 5
        self.assertGreater(len(json.dumps(oversize)), MAX_LAUNCH_STRUCTURE_BYTES)
        cases = {
            "duplicate name": (dict(self.preset_payload("WARM BASS")), 409, "name_conflict"),
            "empty name": (dict(self.preset_payload(" ")), 400, "invalid_preset"),
            "unknown parameter": (
                dict(self.preset_payload("x"), parameters={"zoom": 2}),
                400,
                "invalid_preset",
            ),
            "structure list": (dict(self.preset_payload("x"), structure=[]), 400, "invalid_preset"),
            "structure over 32 KB": (
                dict(self.preset_payload("x"), structure=oversize),
                400,
                "invalid_preset",
            ),
        }
        for label, (payload, expected_status, code) in cases.items():
            with self.subTest(label):
                status, body = self.error("/api/v1/presets", payload=payload, token=self.session)
                self.assertEqual((status, body["code"]), (expected_status, code))
        for method, path in (
            ("GET", "/api/v1/presets/0000000000000000"),
            ("PUT", "/api/v1/presets/0000000000000000"),
            ("DELETE", "/api/v1/presets/0000000000000000"),
        ):
            with self.subTest(method=method):
                payload = None
                if method == "PUT":
                    payload = dict(self.preset_payload("y"), expected_revision=1)
                elif method == "DELETE":
                    payload = {"expected_revision": 1}
                status, body = self.error(path, payload=payload, method=method, token=self.session)
                self.assertEqual((status, body["code"]), (404, "preset_not_found"))
        status, _ = self.error("/api/v1/presets/a/b", token=self.session)
        self.assertEqual(status, 404)
        self.assertEqual(self.preset_path.read_bytes(), before)

    def test_the_33rd_preset_returns_limit_exceeded(self) -> None:
        for index in range(MAX_PRESETS):
            self.store.create({"name": f"Preset {index}", "structure": {}})
        before = self.preset_path.read_bytes()
        status, body = self.error(
            "/api/v1/presets", payload=self.preset_payload("Extra"), token=self.session
        )
        self.assertEqual((status, body["code"]), (400, "limit_exceeded"))
        self.assertEqual(self.preset_path.read_bytes(), before)

    def test_a_corrupt_file_makes_every_route_unavailable(self) -> None:
        self.preset_path.write_bytes(b'{"version": 9, "presets": []}')
        for method, path, payload in (
            ("GET", "/api/v1/presets", None),
            ("GET", "/api/v1/presets/0000000000000000", None),
            ("POST", "/api/v1/presets", self.preset_payload()),
            ("PUT", "/api/v1/presets/0000000000000000", dict(self.preset_payload(), expected_revision=1)),
            ("DELETE", "/api/v1/presets/0000000000000000", {"expected_revision": 1}),
        ):
            with self.subTest(method=method, path=path):
                status, body = self.error(path, payload=payload, method=method, token=self.session)
                self.assertEqual((status, body["code"]), (503, "preset_store_unavailable"))
        self.assertEqual(self.preset_path.read_bytes(), b'{"version": 9, "presets": []}')

    def test_authorization_and_cookie_origin(self) -> None:
        for method, path, payload in (
            ("GET", "/api/v1/presets", None),
            ("GET", "/api/v1/presets/0000000000000000", None),
            ("POST", "/api/v1/presets", self.preset_payload()),
            ("PUT", "/api/v1/presets/0000000000000000", {}),
            ("DELETE", "/api/v1/presets/0000000000000000", {}),
        ):
            with self.subTest(method=method, path=path):
                status, _ = self.error(path, payload=payload, method=method)
                self.assertEqual(status, 401)
                status, _ = self.error(path, payload=payload, method=method, token="wrong")
                self.assertEqual(status, 401)
        self.assertFalse(self.preset_path.exists())

        # The admin bearer token also passes control authorization.
        status, created = self.request(
            "/api/v1/presets", payload=self.preset_payload(), token="secret-token"
        )
        self.assertEqual(status, 201)

        _, _, headers = self.request(
            "/api/v1/pairing/exchange",
            payload={"code": "012345"},
            headers={"X-RGB-Matrix-Session": "cookie"},
            include_headers=True,
        )
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        path = f"/api/v1/presets/{created['preset_id']}"
        status, _ = self.request(path, headers={"Cookie": cookie})
        self.assertEqual(status, 200)  # reads need no Origin

        replacement = dict(self.preset_payload("Cookie edit"), expected_revision=1)
        for method, payload in (
            ("POST", self.preset_payload("Other")),
            ("PUT", replacement),
            ("DELETE", {"expected_revision": 1}),
        ):
            target = "/api/v1/presets" if method == "POST" else path
            for origin in (None, "http://example.invalid"):
                with self.subTest(method=method, origin=origin):
                    extra = {"Cookie": cookie}
                    if origin:
                        extra["Origin"] = origin
                    status, _ = self.error(target, payload=payload, method=method, headers=extra)
                    self.assertEqual(status, 403)
        status, edited = self.request(
            path,
            payload=replacement,
            method="PUT",
            headers={"Cookie": cookie, "Origin": self.base_url},
        )
        self.assertEqual((status, edited["revision"]), (200, 2))
        status, _ = self.request(
            path,
            payload={"expected_revision": 2},
            method="DELETE",
            headers={"Cookie": cookie, "Origin": self.base_url},
        )
        self.assertEqual(status, 200)

        # Pairing still doesn't grant network administration.
        status, _ = self.error(
            "/api/v1/network/forget", payload={"profile": "x"}, token=self.session
        )
        self.assertEqual(status, 403)


class BluetoothApiTests(unittest.TestCase):
    """M3 criteria 5-9 over HTTP, with the fake backend."""

    request = WebServiceTests.request
    error = PresetApiTests.error

    def setUp(self) -> None:
        self.backend = FakeBluetoothBackend(
            [
                BluetoothDevice(
                    "AA:BB:CC:DD:EE:01", "Pixel", paired=True, trusted=False, connected=False
                )
            ]
        )
        self.bluetooth = BluetoothController(self.backend, timer_factory=None)
        self.supervisor = FakeSupervisor()
        self.pairing = PairingManager(
            "012345",
            token_factory=lambda: "temporary-session-token-with-128-bits",
        )
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            build_handler(
                self.supervisor,
                api_token="secret-token",
                network_client=FakeNetworkClient(),
                pairing_manager=self.pairing,
                bluetooth=self.bluetooth,
            ),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"
        _, paired = self.request("/api/v1/pairing/exchange", payload={"code": "012345"})
        self.session = paired["token"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def act(self, **payload) -> dict:
        _, body = self.request("/api/v1/bluetooth/actions", payload=payload, token=self.session)
        return body

    def test_paired_session_pairs_trusts_and_forgets_a_phone(self) -> None:
        status, body = self.request("/api/v1/bluetooth", token=self.session)
        self.assertEqual((status, body["available"], body["window"]["open"]), (200, True, False))
        phone_id = body["devices"][0]["device_id"]

        body = self.act(action="window.open")
        self.assertTrue(body["window"]["open"])
        decision = self.backend.request_confirmation("AA:BB:CC:DD:EE:04", "New phone", 4321)
        _, body = self.request("/api/v1/bluetooth", token=self.session)
        pending = body["pending_request"]
        self.assertEqual((pending["code"], pending["name"]), ("004321", "New phone"))
        self.act(action="request.confirm", request_id=pending["request_id"])
        self.assertEqual(decision.outcome, "accepted")

        status, error = self.error(
            "/api/v1/bluetooth/actions",
            payload={"action": "request.confirm", "request_id": pending["request_id"]},
            token=self.session,
        )
        self.assertEqual((status, error["code"]), (409, "stale_request"))

        body = self.act(action="device.trust", device_id=phone_id)
        self.assertEqual(body["devices"][0]["trusted"], True)
        status, error = self.error(
            "/api/v1/bluetooth/actions",
            payload={"action": "device.forget", "device_id": phone_id, "confirm": False},
            token=self.session,
        )
        self.assertEqual((status, error["code"]), (400, "confirmation_required"))
        status, error = self.error(
            "/api/v1/bluetooth/actions",
            payload={"action": "device.connect", "device_id": "0" * 16},
            token=self.session,
        )
        self.assertEqual((status, error["code"]), (404, "device_not_found"))
        body = self.act(action="device.forget", device_id=phone_id, confirm=True)
        self.assertEqual(body["devices"], [])
        body = self.act(action="window.close")
        self.assertFalse(body["window"]["open"])
        self.assertNotIn("AA:BB", json.dumps(body))

    def test_unavailable_backend(self) -> None:
        self.backend.unavailable_reason = "BlueZ is not running"
        status, body = self.request("/api/v1/bluetooth", token=self.session)
        self.assertEqual((status, body["available"], body["reason"]), (200, False, "BlueZ is not running"))
        status, error = self.error(
            "/api/v1/bluetooth/actions", payload={"action": "window.open"}, token=self.session
        )
        self.assertEqual((status, error["code"]), (503, "bluetooth_unavailable"))
        # Other routes still work.
        status, _ = self.request("/api/v1/status")
        self.assertEqual(status, 200)

    def test_the_service_log_carries_no_address_code_name_or_id(self) -> None:
        # Criterion 6: the access log (journald) sees every request line.
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            self.act(action="window.open")
            self.backend.request_confirmation("AA:BB:CC:DD:EE:04", "New phone", 987123)
            _, body = self.request("/api/v1/bluetooth", token=self.session)
            request_id = body["pending_request"]["request_id"]
            phone_id = next(d["device_id"] for d in body["devices"] if d["name"] == "Pixel")
            self.act(action="request.confirm", request_id=request_id)
            self.act(action="device.trust", device_id=phone_id)
            self.error(
                "/api/v1/bluetooth/actions",
                payload={"action": "device.connect", "device_id": "0" * 16},
                token=self.session,
            )
            self.backend.fail_with = "Failed"
            self.error(
                "/api/v1/bluetooth/actions",
                payload={"action": "device.disconnect", "device_id": phone_id},
                token=self.session,
            )
            self.backend.fail_with = None
            self.act(action="window.close")
        text = log.getvalue()
        self.assertIn("POST /api/v1/bluetooth/actions", text)  # the log was captured
        for secret in (
            "AA:BB:CC:DD:EE",
            "aa:bb:cc:dd:ee",
            "AA_BB_CC_DD_EE",
            "987123",
            "New phone",
            "Pixel",
            phone_id,
            request_id,
        ):
            self.assertNotIn(secret, text)

    def test_unexpected_errors_return_a_fixed_body(self) -> None:
        def broken():
            raise KeyError("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_01")

        self.backend.devices = broken
        for method, path, payload in (
            ("GET", "/api/v1/bluetooth", None),
            ("GET", "/api/v1/audio/sources", None),
            ("POST", "/api/v1/bluetooth/actions", {"action": "device.trust", "device_id": "0" * 16}),
        ):
            with self.subTest(path=path):
                status, body = self.error(path, payload=payload, method=method, token=self.session)
                self.assertEqual(
                    (status, body),
                    (500, {"error": "Bluetooth request failed", "code": "bluetooth_failed"}),
                )

    def test_source_catalog_reports_the_running_fft_source(self) -> None:
        status, body = self.request("/api/v1/audio/sources", token=self.session)
        self.assertEqual(status, 200)
        self.assertEqual(body["sources"][0], {"source_id": "usb-mic", "availability": "checked_at_launch"})
        self.assertEqual(body["sources"][1]["connected"], False)
        self.assertIsNone(body["active_source"])
        self.supervisor.status = lambda: {
            "state": "running",
            "visualization_id": "fft",
            "parameters": {"audio_source": "bluetooth"},
        }
        _, body = self.request("/api/v1/audio/sources", token=self.session)
        self.assertEqual(body["active_source"], "bluetooth")

    def test_authorization_and_cookie_origin(self) -> None:
        for method, path, payload in (
            ("GET", "/api/v1/bluetooth", None),
            ("GET", "/api/v1/audio/sources", None),
            ("POST", "/api/v1/bluetooth/actions", {"action": "window.open"}),
        ):
            with self.subTest(path=path):
                status, _ = self.error(path, payload=payload, method=method)
                self.assertEqual(status, 401)
        _, _, headers = self.request(
            "/api/v1/pairing/exchange",
            payload={"code": "012345"},
            headers={"X-RGB-Matrix-Session": "cookie"},
            include_headers=True,
        )
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _ = self.request("/api/v1/bluetooth", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        status, _ = self.error(
            "/api/v1/bluetooth/actions",
            payload={"action": "window.open"},
            headers={"Cookie": cookie},
        )
        self.assertEqual(status, 403)
        self.assertEqual(self.backend.calls, [])
        _, body = self.request(
            "/api/v1/bluetooth/actions",
            payload={"action": "window.open"},
            headers={"Cookie": cookie, "Origin": self.base_url},
        )
        self.assertTrue(body["window"]["open"])
        status, _ = self.error(
            "/api/v1/network/forget", payload={"profile": "x"}, token=self.session
        )
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
