"""Small standard-library HTTP API for the RGB matrix supervisor MVP."""

from __future__ import annotations

import argparse
import hmac
import inspect
import ipaddress
import json
import math
import math
import os
import signal
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from matrix_network import NetworkClient, NetworkClientError

from .boot_code import DEFAULT_CODE_FILE, ensure_boot_code
from .controller_config import ControllerConfigError, load_controller_config
from .pairing import PairingError, PairingManager
from .registry import build_default_registry
from .schema import ValidationError
from .supervisor import VisualizationSupervisor


MAX_REQUEST_BYTES = 64 * 1024
MAX_RUNTIME_CHANGES = 32
MAX_RUNTIME_PATH_LENGTH = 256
MAX_RUNTIME_ACTION_LENGTH = 128
MAX_RUNTIME_TARGET_LENGTH = 256


class _RuntimeResponseError(RuntimeError):
    """Translate a child ``{ok: false, error: ...}`` result for HTTP clients."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _is_loopback_address(address_text: str) -> bool:
    """Recognize native and IPv4-mapped loopback peers without trusting headers."""

    try:
        address = ipaddress.ip_address(address_text)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped is not None and mapped.is_loopback


def _unsupported_runtime_snapshot(supervisor: Any) -> dict[str, Any]:
    """Return a stable response for visualizations without live control support.

    Live controls are opt-in. Keeping this fallback in the HTTP adapter means older
    supervisors and launch-only visualizations continue to work while the runtime
    protocol is rolled out incrementally.
    """

    try:
        status = supervisor.status()
    except Exception:  # pragma: no cover - a broken status provider is handled below
        status = {}
    if not isinstance(status, dict):
        status = {}
    return {
        "protocol_version": 1,
        "supported": False,
        "connected": False,
        "run_id": status.get("run_id"),
        "visualization_id": status.get("visualization_id"),
        "revision": None,
        "state": {},
        "controls": [],
    }


def _runtime_snapshot(supervisor: Any) -> dict[str, Any]:
    """Call the optional supervisor snapshot API and require a JSON object."""

    method = getattr(supervisor, "runtime_snapshot", None)
    if method is None:
        return _unsupported_runtime_snapshot(supervisor)
    # The real supervisor advertises whether the current registry entry opted into
    # live control. Avoid a one-second socket timeout on every poll while a normal
    # launch-only visualization (connection info or sorts) is running.
    try:
        status = supervisor.status()
    except Exception:
        status = None
    runtime_status = status.get("runtime_control") if isinstance(status, dict) else None
    if isinstance(runtime_status, dict) and runtime_status.get("available") is False:
        return _unsupported_runtime_snapshot(supervisor)
    snapshot = method()
    if snapshot is None:
        return _unsupported_runtime_snapshot(supervisor)
    return _normalize_runtime_result(snapshot)


def _normalize_runtime_result(result: Any) -> dict[str, Any]:
    """Accept both raw snapshots and the transport's ``ok/snapshot`` envelope."""

    if not isinstance(result, dict):
        raise RuntimeError("runtime response must be a JSON object")
    if result.get("ok") is False:
        error = result.get("error")
        if isinstance(error, dict):
            code = str(error.get("code", "runtime_error"))
            message = str(error.get("message", "runtime command rejected"))
        else:
            code = "runtime_error"
            message = "runtime command rejected"
        raise _RuntimeResponseError(code, message)
    snapshot = result.get("snapshot")
    if isinstance(snapshot, dict):
        return snapshot
    return result


def _bounded_runtime_value(value: Any, *, label: str, depth: int = 0) -> Any:
    """Reject pathological command values before they reach a child process."""

    if depth > 8:
        raise ValidationError(f"{label} is nested too deeply")
    if value is None or type(value) in {bool, int, float, str}:
        if type(value) is float and not math.isfinite(value):
            raise ValidationError(f"{label} must be finite")
        if isinstance(value, str) and len(value) > MAX_RUNTIME_PATH_LENGTH:
            raise ValidationError(f"{label} is too long")
        return value
    if isinstance(value, list):
        if len(value) > MAX_RUNTIME_CHANGES:
            raise ValidationError(f"{label} contains too many items")
        return [
            _bounded_runtime_value(item, label=label, depth=depth + 1)
            for item in value
        ]
    if isinstance(value, dict):
        if len(value) > MAX_RUNTIME_CHANGES:
            raise ValidationError(f"{label} contains too many fields")
        if any(type(key) is not str or len(key) > MAX_RUNTIME_PATH_LENGTH for key in value):
            raise ValidationError(f"{label} contains an invalid field name")
        return {
            key: _bounded_runtime_value(item, label=label, depth=depth + 1)
            for key, item in value.items()
        }
    raise ValidationError(f"{label} contains an unsupported JSON value")


def _parse_runtime_patch(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the public atomic patch envelope without knowing visualization IDs."""

    allowed = {"run_id", "expected_revision", "changes"}
    unknown = set(payload) - allowed
    if unknown:
        raise ValidationError(f"unknown runtime fields: {', '.join(sorted(unknown))}")
    run_id = payload.get("run_id")
    if type(run_id) not in {str, int} or (isinstance(run_id, str) and not run_id):
        raise ValidationError("run_id must be a non-empty string or integer")
    expected_revision = payload.get("expected_revision")
    if expected_revision is not None and (
        type(expected_revision) is not int or expected_revision < 0
    ):
        raise ValidationError("expected_revision must be a non-negative integer")
    changes = payload.get("changes")
    if not isinstance(changes, list) or not changes or len(changes) > MAX_RUNTIME_CHANGES:
        raise ValidationError(
            f"changes must contain between 1 and {MAX_RUNTIME_CHANGES} items"
        )
    normalized_changes = []
    for index, change in enumerate(changes):
        if not isinstance(change, dict) or set(change) != {"path", "value"}:
            raise ValidationError(f"changes[{index}] must contain path and value only")
        path = change["path"]
        if type(path) is not str or not path or len(path) > MAX_RUNTIME_PATH_LENGTH:
            raise ValidationError(f"changes[{index}].path is invalid")
        normalized_changes.append(
            {
                "path": path,
                "value": _bounded_runtime_value(
                    change["value"], label=f"changes[{index}].value"
                ),
            }
        )
    return {
        "operation": "patch",
        "run_id": run_id,
        "expected_revision": expected_revision,
        "changes": normalized_changes,
    }


def _parse_runtime_action(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate an allowlisted semantic action envelope."""

    allowed = {"run_id", "expected_revision", "action", "target", "args"}
    unknown = set(payload) - allowed
    if unknown:
        raise ValidationError(f"unknown runtime fields: {', '.join(sorted(unknown))}")
    run_id = payload.get("run_id")
    if type(run_id) not in {str, int} or (isinstance(run_id, str) and not run_id):
        raise ValidationError("run_id must be a non-empty string or integer")
    expected_revision = payload.get("expected_revision")
    if expected_revision is not None and (
        type(expected_revision) is not int or expected_revision < 0
    ):
        raise ValidationError("expected_revision must be a non-negative integer")
    action = payload.get("action")
    if type(action) is not str or not action or len(action) > MAX_RUNTIME_ACTION_LENGTH:
        raise ValidationError("action must be a non-empty string")
    target = payload.get("target")
    if target is not None and (type(target) is not str or len(target) > MAX_RUNTIME_TARGET_LENGTH):
        raise ValidationError("target must be a string")
    args = payload.get("args", {})
    if not isinstance(args, dict):
        raise ValidationError("args must be a JSON object")
    return {
        "operation": "action",
        "run_id": run_id,
        "expected_revision": expected_revision,
        "action": action,
        "target": target,
        "args": _bounded_runtime_value(args, label="args"),
    }


def _runtime_error_response(exc: Exception) -> tuple[HTTPStatus, str, str]:
    """Map supervisor errors to predictable HTTP status/code triples."""

    raw_code = getattr(exc, "code", None)
    code = raw_code if isinstance(raw_code, str) else "runtime_error"
    text = str(exc) or code
    if code == "runtime_error":
        lowered = text.lower()
        for candidate in ("stale_run", "revision_conflict", "invalid_value", "invalid_path", "invalid_command", "unsupported_version", "protocol_error", "unavailable", "busy", "unsupported", "timeout"):
            if candidate in lowered:
                code = candidate
                break
    status_value = getattr(exc, "status", None)
    if type(status_value) is int:
        try:
            status = HTTPStatus(status_value)
        except ValueError:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
    else:
        status = {
            "stale_run": HTTPStatus.CONFLICT,
            "revision_conflict": HTTPStatus.CONFLICT,
            "invalid_value": HTTPStatus.BAD_REQUEST,
            "invalid_path": HTTPStatus.BAD_REQUEST,
            "invalid_command": HTTPStatus.BAD_REQUEST,
            "unsupported_version": HTTPStatus.BAD_REQUEST,
            "protocol_error": HTTPStatus.BAD_REQUEST,
            "unsupported": HTTPStatus.NOT_IMPLEMENTED,
            "unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
            "busy": HTTPStatus.SERVICE_UNAVAILABLE,
            "timeout": HTTPStatus.GATEWAY_TIMEOUT,
        }.get(code, HTTPStatus.INTERNAL_SERVER_ERROR)
    return status, code, text


def build_handler(
    supervisor: VisualizationSupervisor,
    *,
    api_token: str | None,
    network_client: NetworkClient | None = None,
    pairing_manager: PairingManager,
) -> type[BaseHTTPRequestHandler]:
    """Bind one supervisor and token policy to an HTTP handler class."""

    pairing = pairing_manager

    class ControlRequestHandler(BaseHTTPRequestHandler):
        server_version = "RGBMatrixControl/1"

        def do_GET(self) -> None:  # noqa: N802 - HTTP handler API name
            path = urlsplit(self.path).path
            if path == "/":
                self._send_control_page()
            elif path == "/static/control.css":
                self._send_static_file("control.css", "text/css; charset=utf-8")
            elif path == "/static/control.js":
                self._send_static_file("control.js", "text/javascript; charset=utf-8")
            elif path == "/setup":
                self._send_setup_page()
            elif path in {"/generate_204", "/hotspot-detect.html", "/connecttest.txt", "/ncsi.txt"}:
                # These routes help only when a device has already resolved/reached
                # the Pi. Full captive-DNS interception remains deliberately out of
                # scope; the fixed manual URL is always authoritative.
                self._redirect("/setup")
            elif path == "/api/v1/network/status":
                self._handle_network_request("status")
            elif path == "/api/v1/network/scan":
                self._handle_network_request("scan", token=self._bearer_token())
            elif path == "/api/v1/network/profiles":
                self._handle_network_request("profiles", token=self._bearer_token())
            elif path.startswith("/api/v1/network/provision/"):
                job_id = path.removeprefix("/api/v1/network/provision/")
                self._handle_network_request(
                    "job",
                    payload={"job_id": job_id},
                    token=self._bearer_token(),
                )
            elif path == "/api/v1/visualizations":
                self._send_json(HTTPStatus.OK, {"visualizations": supervisor.list_visualizations()})
            elif path == "/api/v1/status":
                self._send_json(HTTPStatus.OK, supervisor.status())
            elif path == "/api/v1/runtime":
                if not self._is_control_authorized():
                    self._send_error_json(HTTPStatus.UNAUTHORIZED, "invalid or missing bearer token")
                else:
                    self._handle_runtime_snapshot()
            elif path == "/api/v1/pairing/status":
                supplied_token = self._bearer_token()
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "authorized": self._is_control_authorized(),
                        "temporary_session": pairing.is_session_token(supplied_token),
                    },
                )
            elif path == "/healthz":
                self._send_json(HTTPStatus.OK, {"status": "ok"})
            elif path == "/api/v1":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "service": "rgb-matrix-control",
                        "api_version": "v1",
                        "endpoints": [
                            "GET /api/v1/visualizations",
                            "GET /api/v1/status",
                            "GET /api/v1/runtime",
                            "PATCH /api/v1/runtime",
                            "POST /api/v1/runtime/actions",
                            "GET /api/v1/pairing/status",
                            "GET /api/v1/network/status",
                            "GET /setup",
                            "POST /api/v1/run",
                            "POST /api/v1/stop",
                            "POST /api/v1/pairing/exchange",
                            "POST /api/v1/pairing/logout",
                            "POST /api/v1/network/provision",
                        ],
                    },
                )
            else:
                self._send_error_json(HTTPStatus.NOT_FOUND, "endpoint not found")

        def do_POST(self) -> None:  # noqa: N802 - HTTP handler API name
            path = urlsplit(self.path).path
            try:
                if path == "/api/v1/pairing/exchange":
                    payload = self._read_json_object()
                    self._validate_exact_fields(payload, {"code"})
                    result = pairing.exchange(
                        payload["code"],
                        client_id=self.client_address[0],
                    )
                    self._send_json(
                        HTTPStatus.OK,
                        {"token": result.token, "expires_in": result.expires_in},
                    )
                    return
                if path == "/api/v1/pairing/logout":
                    payload = self._read_json_object()
                    self._validate_exact_fields(payload, set())
                    if not self._is_control_authorized():
                        self._send_error_json(HTTPStatus.UNAUTHORIZED, "invalid session")
                        return
                    pairing.revoke_session(self._bearer_token())
                    self._send_json(HTTPStatus.OK, {"revoked": True})
                    return
                if path == "/api/v1/network/provision":
                    self._handle_network_request(
                        "provision",
                        payload=self._read_json_object(),
                        token=self._bearer_token(),
                        success_status=HTTPStatus.ACCEPTED,
                    )
                    return
                if path == "/api/v1/network/force-ap":
                    self._handle_network_request(
                        "force_ap",
                        payload=self._read_json_object(),
                        token=self._bearer_token(),
                        success_status=HTTPStatus.ACCEPTED,
                    )
                    return
                if path == "/api/v1/network/retry-known":
                    self._handle_network_request(
                        "retry_known",
                        payload=self._read_json_object(),
                        token=self._bearer_token(),
                        success_status=HTTPStatus.ACCEPTED,
                    )
                    return
                if path == "/api/v1/network/forget":
                    self._handle_network_request(
                        "forget",
                        payload=self._read_json_object(),
                        token=self._bearer_token(),
                    )
                    return

                if not self._is_control_authorized():
                    self._send_error_json(HTTPStatus.UNAUTHORIZED, "invalid or missing bearer token")
                    return

                if path == "/api/v1/runtime/actions":
                    command = _parse_runtime_action(self._read_json_object())
                    self._handle_runtime_command(command)
                    return

                if path == "/api/v1/run":
                    payload = self._read_json_object()
                    visualization_id = payload.get("visualization_id")
                    if type(visualization_id) is not str or not visualization_id:
                        raise ValidationError("visualization_id must be a non-empty string")
                    parameters = payload.get("parameters", {})
                    result = supervisor.start(visualization_id, parameters)
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "visualization_id": result.visualization_id,
                            "parameters": result.parameters,
                            "pid": result.pid,
                            "run_id": result.run_id,
                        },
                    )
                elif path == "/api/v1/stop":
                    # Accept an empty body for ergonomic curl/client usage. If a body
                    # is supplied it must still be valid JSON, preventing request
                    # smuggling surprises between future proxies and this server.
                    if self.headers.get("Content-Length", "0") != "0":
                        self._read_json_object()
                    self._send_json(HTTPStatus.OK, supervisor.stop())
                else:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "endpoint not found")
            except ValidationError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            except PairingError as exc:
                try:
                    status = HTTPStatus(exc.status)
                except ValueError:
                    status = HTTPStatus.INTERNAL_SERVER_ERROR
                headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
                self._send_json(status, {"error": str(exc)}, headers=headers)
            except RuntimeError as exc:
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def do_PATCH(self) -> None:  # noqa: N802 - HTTP handler API name
            """Apply one authenticated atomic runtime patch."""

            path = urlsplit(self.path).path
            if path != "/api/v1/runtime":
                self._send_error_json(HTTPStatus.NOT_FOUND, "endpoint not found")
                return
            if not self._is_control_authorized():
                self._send_error_json(HTTPStatus.UNAUTHORIZED, "invalid or missing bearer token")
                return
            try:
                command = _parse_runtime_patch(self._read_json_object())
                self._handle_runtime_command(command)
            except ValidationError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))

        def _handle_runtime_snapshot(self) -> None:
            try:
                self._send_json(HTTPStatus.OK, _runtime_snapshot(supervisor))
            except Exception as exc:
                status, code, message = _runtime_error_response(exc)
                self._send_json(status, {"error": message, "code": code})

        def _handle_runtime_command(self, command: dict[str, Any]) -> None:
            method = getattr(supervisor, "runtime_command", None)
            if method is None:
                self._send_json(
                    HTTPStatus.NOT_IMPLEMENTED,
                    {"error": "active visualization does not support live controls", "code": "unsupported"},
                )
                return
            try:
                # New supervisors can enforce identity/revision at their boundary;
                # older adapters accept only the command mapping. Inspecting the
                # signature avoids catching a TypeError raised by the child itself
                # and accidentally issuing a command twice.
                kwargs: dict[str, Any] = {}
                try:
                    parameters = inspect.signature(method).parameters
                except (TypeError, ValueError):
                    parameters = {}
                if "run_id" in parameters:
                    kwargs["run_id"] = command["run_id"]
                if "expected_revision" in parameters:
                    kwargs["expected_revision"] = command.get("expected_revision")
                result = method(command, **kwargs)
                if result is None:
                    result = _runtime_snapshot(supervisor)
                self._send_json(HTTPStatus.OK, _normalize_runtime_result(result))
            except Exception as exc:
                status, code, message = _runtime_error_response(exc)
                self._send_json(status, {"error": message, "code": code})

        def _handle_network_request(
            self,
            operation: str,
            *,
            payload: dict[str, Any] | None = None,
            token: str | None = None,
            success_status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            if network_client is None:
                self._send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, "network control is unavailable")
                return
            if pairing.is_session_token(token):
                self._send_error_json(
                    HTTPStatus.FORBIDDEN,
                    "temporary visualization sessions cannot administer networking",
                )
                return
            try:
                result = network_client.request(operation, payload=payload, token=token)
            except NetworkClientError as exc:
                try:
                    status = HTTPStatus(exc.status)
                except ValueError:
                    status = HTTPStatus.INTERNAL_SERVER_ERROR
                self._send_json(status, {"error": str(exc), "code": exc.code})
                return
            self._send_json(success_status, result if isinstance(result, dict) else {"result": result})

        def _read_json_object(self) -> dict[str, Any]:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ValueError("Content-Length is required")
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise ValueError(f"request body must be at most {MAX_REQUEST_BYTES} bytes")

            raw_body = self.rfile.read(length)
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("request body must contain valid UTF-8 JSON") from exc
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            return payload

        @staticmethod
        def _validate_exact_fields(payload: dict[str, Any], required: set[str]) -> None:
            if set(payload) != required:
                raise ValueError("request contains missing or unknown fields")

        def _is_admin_authorized(self) -> bool:
            if api_token is None:
                return True
            supplied_token = self._bearer_token()
            return supplied_token is not None and hmac.compare_digest(supplied_token, api_token)

        def _is_control_authorized(self) -> bool:
            supplied_token = self._bearer_token()
            return self._is_admin_authorized() or pairing.is_session_token(supplied_token)

        def _is_loopback_client(self) -> bool:
            return _is_loopback_address(self.client_address[0])

        def _bearer_token(self) -> str | None:
            supplied = self.headers.get("Authorization", "")
            prefix = "Bearer "
            if not supplied.startswith(prefix):
                return None
            return supplied[len(prefix) :]

        def _send_control_page(self) -> None:
            self._send_static_file(
                "index.html",
                "text/html; charset=utf-8",
                content_security_policy=(
                    "default-src 'self'; connect-src 'self'; script-src 'self'; "
                    "style-src 'self'; img-src 'self' data:; object-src 'none'; "
                    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                ),
            )

        def _send_setup_page(self) -> None:
            self._send_static_file("setup.html", "text/html; charset=utf-8")

        def _send_static_file(
            self,
            filename: str,
            content_type: str,
            *,
            content_security_policy: str | None = None,
        ) -> None:
            """Serve one code-owned static file from an explicit route.

            Callers provide literal filenames; request paths are never joined into a
            filesystem path. This keeps the tiny standard-library server free of a
            general file-serving or traversal surface.
            """

            page_path = Path(__file__).resolve().parent / "static" / filename
            try:
                encoded = page_path.read_bytes()
            except OSError:
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "page asset is unavailable")
                return
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if content_security_policy is not None:
                self.send_header("Content-Security-Policy", content_security_policy)
            self.end_headers()
            self.wfile.write(encoded)

        def _redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.FOUND.value)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _send_json(
            self,
            status: HTTPStatus,
            payload: dict[str, Any],
            *,
            headers: dict[str, str] | None = None,
        ) -> None:
            encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(encoded)

        def _send_error_json(self, status: HTTPStatus, message: str) -> None:
            self._send_json(status, {"error": message})

        def log_message(self, format: str, *args: Any) -> None:
            # Keep the default concise access log but make the service prefix clear in
            # journald. Request bodies and bearer tokens are never included.
            super().log_message(f"control-api: {format}", *args)

    return ControlRequestHandler


def _read_token(token_file: str | None) -> str | None:
    env_token = os.environ.get("RGBMATRIX_API_TOKEN")
    if env_token:
        return env_token
    if token_file:
        with open(token_file, "r", encoding="utf-8") as handle:
            token = handle.read().strip()
        if not token:
            raise ValueError("token file is empty")
        return token
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RGB matrix visualization control API")
    parser.add_argument(
        "--config",
        type=Path,
        help="Versioned JSON file containing managed launch defaults",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Address on which to listen")
    parser.add_argument("--port", type=int, default=8080, help="TCP port on which to listen")
    parser.add_argument("--python", default=None, help="Python executable used for visualizations")
    parser.add_argument("--token-file", help="File containing the bearer token for POST requests")
    parser.add_argument(
        "--boot-code-file",
        type=Path,
        default=DEFAULT_CODE_FILE,
        help="State file holding the pairing code for the current boot",
    )
    parser.add_argument(
        "--network-socket",
        default="/run/rgb-matrix-network/control.sock",
        help="Unix socket for the privileged NetworkManager helper",
    )
    privilege_group = parser.add_mutually_exclusive_group()
    privilege_group.add_argument(
        "--sudo",
        dest="use_sudo",
        action="store_true",
        help="Prefix visualization commands with non-interactive sudo -E",
    )
    privilege_group.add_argument(
        "--no-sudo",
        dest="use_sudo",
        action="store_false",
        help="Do not prefix visualization commands with sudo",
    )
    parser.set_defaults(use_sudo=None)
    parser.add_argument("--stop-timeout", type=float, default=None)
    parser.add_argument(
        "--no-boot-display",
        action="store_true",
        help="Do not start the SSID, address, and pairing-code matrix display",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not (1 <= args.port <= 65535):
        raise SystemExit("--port must be between 1 and 65535")
    if args.stop_timeout is not None and args.stop_timeout <= 0:
        raise SystemExit("--stop-timeout must be positive")

    try:
        controller_config = load_controller_config(args.config)
    except ControllerConfigError as exc:
        raise SystemExit(f"could not load controller config: {exc}") from exc

    try:
        token = _read_token(args.token_file)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"could not load API token: {exc}") from exc

    loopback_hosts = {"127.0.0.1", "::1", "localhost"}
    if args.host not in loopback_hosts and token is None:
        raise SystemExit("a bearer token is required when listening beyond loopback")

    try:
        boot_code = ensure_boot_code(args.boot_code_file)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"could not initialize the boot pairing code: {exc}") from exc

    # The status visualization is a normal supervised child. These environment
    # values let it read the same boot state and public loopback status endpoint even
    # when an operator chooses non-default paths for diagnostics.
    os.environ["RGBMATRIX_BOOT_CODE_FILE"] = str(args.boot_code_file)
    os.environ["RGBMATRIX_CONTROL_STATUS_URL"] = (
        f"http://127.0.0.1:{args.port}/api/v1/network/status"
    )

    use_sudo = controller_config.use_sudo if args.use_sudo is None else args.use_sudo
    command_prefix = ("sudo", "-n", "-E") if use_sudo else ()
    python_executable = (
        args.python
        or controller_config.python_executable
        or os.environ.get("RGBMATRIX_PYTHON")
        or os.sys.executable
    )
    stop_timeout = (
        args.stop_timeout
        if args.stop_timeout is not None
        else controller_config.stop_timeout_seconds
    )
    registry_defaults = {
        visualization_id: controller_config.defaults_for(visualization_id)
        for visualization_id in ("system_info", "fft", "sorts")
    }
    supervisor = VisualizationSupervisor(
        build_default_registry(parameter_defaults=registry_defaults),
        python_executable=python_executable,
        command_prefix=command_prefix,
        stop_timeout=stop_timeout,
        runtime_dir=os.environ.get("RGBMATRIX_RUNTIME_DIR"),
    )
    server = ThreadingHTTPServer(
        (args.host, args.port),
        build_handler(
            supervisor,
            api_token=token,
            network_client=NetworkClient(args.network_socket),
            pairing_manager=PairingManager(boot_code),
        ),
    )

    # BaseServer.shutdown() must run from a different thread than serve_forever(). A
    # tiny helper thread keeps signal handlers simple and guarantees the active matrix
    # process is stopped in the outer finally block.
    def request_shutdown(_signum, _frame) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    try:
        boot_visualization_id = (
            None if args.no_boot_display else controller_config.boot_visualization_id
        )
        if boot_visualization_id is not None:
            try:
                supervisor.start(boot_visualization_id, {})
            except (OSError, RuntimeError, ValidationError) as exc:
                # A display wiring/driver failure must not take the recovery UI off
                # the network. Journald still records a concise actionable error.
                print(f"Could not start the boot information display: {exc}")
        print(f"RGB matrix control API listening on http://{args.host}:{args.port}")
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        supervisor.stop()
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
