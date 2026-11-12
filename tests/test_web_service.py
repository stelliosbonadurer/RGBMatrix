"""HTTP contract tests with an injected supervisor and no subprocesses."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

from matrix_controller.web_service import _is_loopback_address, build_handler
from matrix_controller.pairing import PairingManager


class FakeSupervisor:
    def __init__(self):
        self.started: list[tuple[str, dict]] = []
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

    def start(self, visualization_id, parameters):
        self.started.append((visualization_id, parameters))
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
        self.runtime["revision"] += 1
        return self.runtime


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


if __name__ == "__main__":
    unittest.main()
