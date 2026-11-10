"""Focused tests for the private per-run visualization control channel."""

from __future__ import annotations

import socket
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path

from matrix_controller.runtime_transport import (
    FrameBoundaryRuntimeBridge,
    MAX_FRAME_BYTES,
    RuntimeControlClient,
    RuntimeControlError,
    RuntimeControlServer,
    RuntimeProtocolError,
    RuntimeRemoteError,
    RuntimeTimeout,
)
from matrix_controller.registry import build_default_registry


def _unix_sockets_are_available() -> bool:
    """The managed laptop sandbox blocks local sockets; Pi/CI does not."""

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "probe.sock"
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.bind(str(path))
            return True
        except OSError:
            return False
        finally:
            probe.close()


@unittest.skipUnless(_unix_sockets_are_available(), "Unix-domain sockets unavailable")
class RuntimeTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.server = RuntimeControlServer("run-17", runtime_dir=self.directory.name)
        self.stop_event = threading.Event()
        self.requests: list[tuple[str, object]] = []

        def handler(operation: str, payload: object) -> object:
            self.requests.append((operation, payload))
            if operation == "snapshot":
                return {"revision": 4, "layers": {"bass": {"visible": True}}}
            if operation == "command":
                return {"revision": 5, "applied": payload}
            raise RuntimeControlError("unsupported", "operation is not supported")

        self.client = RuntimeControlClient(self.server.endpoint)
        self.thread = threading.Thread(
            target=self.client.serve,
            args=(handler,),
            kwargs={"stop_event": self.stop_event},
            daemon=True,
        )
        self.thread.start()

    def tearDown(self) -> None:
        self.stop_event.set()
        self.server.close()
        self.thread.join(timeout=1)
        self.directory.cleanup()

    def test_snapshot_and_command_are_bounded_json_operations(self) -> None:
        snapshot = self.server.request("snapshot", {}, timeout=1)
        command = self.server.request("command", {"path": "layers.bass.visible", "value": False})

        self.assertEqual(snapshot["revision"], 4)
        self.assertEqual(command["revision"], 5)
        self.assertEqual(self.requests[0], ("snapshot", {}))

    def test_handler_errors_are_structured_and_do_not_break_connection(self) -> None:
        with self.assertRaises(RuntimeRemoteError) as context:
            self.server.request("unknown", {}, timeout=1)
        self.assertEqual(context.exception.code, "unsupported")
        self.assertEqual(self.server.request("snapshot", {}, timeout=1)["revision"], 4)

    def test_endpoint_identity_and_environment_are_per_run(self) -> None:
        endpoint = self.server.endpoint
        environment = endpoint.environment()
        self.assertEqual(environment["RGB_MATRIX_RUNTIME_SOCKET"], str(endpoint.socket_path))
        self.assertEqual(environment["RGB_MATRIX_RUN_ID"], "run-17")
        self.assertEqual(environment["RGB_MATRIX_RUNTIME_TOKEN"], endpoint.token)
        self.assertTrue(endpoint.socket_path.exists())
        self.assertEqual(
            stat.S_IMODE(endpoint.socket_path.parent.stat().st_mode),
            0o711,
        )
        self.assertEqual(stat.S_IMODE(endpoint.socket_path.stat().st_mode), 0o622)
        self.assertGreaterEqual(len(endpoint.socket_path.parent.name), 11)

    def test_stale_child_identity_is_rejected(self) -> None:
        # The active client is already connected; the server accepts a replacement
        # only after the active channel is dropped. This is a direct child-side
        # identity check using a fresh endpoint after closing the first client.
        self.stop_event.set()
        self.server.close()
        self.thread.join(timeout=1)

        # Recreate the endpoint and client pair for the isolated stale identity test.
        replacement = RuntimeControlServer("run-18", runtime_dir=self.directory.name)
        stale_client = RuntimeControlClient(
            replacement.endpoint.__class__(
                replacement.endpoint.socket_path,
                "old-run",
                replacement.endpoint.token,
            )
        )
        thread = threading.Thread(
            target=stale_client.serve,
            args=(lambda _operation, _payload: {},),
            daemon=True,
        )
        thread.start()
        with self.assertRaises(RuntimeProtocolError):
            replacement.request("snapshot", {}, timeout=0.05)
        replacement.close()
        thread.join(timeout=1)

    def test_unconnected_request_times_out_and_large_frame_is_rejected(self) -> None:
        self.stop_event.set()
        self.server.close()
        self.thread.join(timeout=1)
        unconnected = RuntimeControlServer("run-no-client", runtime_dir=self.directory.name)
        with self.assertRaises(RuntimeTimeout):
            unconnected.request("snapshot", {}, timeout=0.05)
        unconnected.close()
        # The public request path rejects oversized JSON before waiting for a child.
        # Use a fresh endpoint so the close above cannot affect the assertion.
        oversized = RuntimeControlServer("run-large", runtime_dir=self.directory.name)
        with self.assertRaisesRegex(RuntimeProtocolError, "frame size"):
            oversized.request("command", {"data": "x" * MAX_FRAME_BYTES})
        oversized.close()


class FrameBoundaryBridgeTests(unittest.TestCase):
    def test_listener_waits_for_render_thread_result(self) -> None:
        bridge = FrameBoundaryRuntimeBridge(response_timeout=1)
        result: dict[str, object] = {}

        def listener() -> None:
            result["value"] = bridge.handle("command", {"enabled": True})

        thread = threading.Thread(target=listener)
        thread.start()
        for _ in range(20):
            if bridge.process_pending(lambda operation, payload: {"operation": operation, "payload": payload}):
                break
            time.sleep(0.005)
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["value"], {"operation": "command", "payload": {"enabled": True}})

    def test_queue_capacity_and_frame_timeout_are_bounded(self) -> None:
        bridge = FrameBoundaryRuntimeBridge(max_pending=1, response_timeout=0.02)
        first_result: dict[str, object] = {}
        first = threading.Thread(
            target=lambda: first_result.setdefault("error", self._capture(bridge, "first")),
        )
        first.start()
        # Let the first call occupy the single slot and then verify saturation.
        deadline = time.monotonic() + 0.5
        while bridge._pending.empty() and time.monotonic() < deadline:
            time.sleep(0.001)
        with self.assertRaisesRegex(RuntimeControlError, "queue is full"):
            bridge.handle("second", {})
        first.join(timeout=1)
        self.assertIsInstance(first_result["error"], RuntimeControlError)
        self.assertEqual(first_result["error"].code, "timeout")
        self.assertEqual(bridge.process_pending(lambda _operation, _payload: {}), 1)

    @staticmethod
    def _capture(bridge: FrameBoundaryRuntimeBridge, operation: str) -> object:
        try:
            return bridge.handle(operation, {})
        except RuntimeControlError as exc:
            return exc


class LiveCapabilityTests(unittest.TestCase):
    def test_only_interactive_visualizations_request_runtime_control(self) -> None:
        registry = build_default_registry()
        self.assertTrue(registry.get("fft").supports_live_control)
        self.assertFalse(registry.get("system_info").supports_live_control)
        self.assertFalse(registry.get("sorts").supports_live_control)
        self.assertFalse(registry.get("master_theorem").supports_live_control)
        public = {item["id"]: item for item in (spec.as_public_dict() for spec in registry.list())}
        self.assertTrue(public["fft"]["supports_live_control"])
        self.assertFalse(public["sorts"]["supports_live_control"])
        self.assertFalse(public["master_theorem"]["supports_live_control"])


if __name__ == "__main__":
    unittest.main()
