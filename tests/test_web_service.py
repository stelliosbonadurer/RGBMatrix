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

    def request(self, path: str, *, payload=None, token: str | None = None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if token is not None:
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.load(response)

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
        self.assertIn("/api/v1/stop", script)

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
