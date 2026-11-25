"""Small standard-library HTTP API for the RGB matrix supervisor MVP."""

from __future__ import annotations

import argparse
import hmac
import inspect
import ipaddress
import json
import math
import os
import signal
import threading
from http.cookies import CookieError, SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from matrix_network import NetworkClient, NetworkClientError

from .bluetooth import (
    BluetoothController,
    BluetoothError,
    DbusBluetoothBackend,
    audio_source_catalog,
    unavailable_status,
)
from .boot_code import DEFAULT_CODE_FILE, ensure_boot_code
from .controller_config import ControllerConfig, ControllerConfigError, load_controller_config
from .matrix_settings import (
    DEFAULT_MATRIX_SETTINGS_FILE,
    MatrixSettingsError,
    MatrixSettingsStore,
    config_matrix_values,
)
from .pairing import PairingError, PairingManager
from .program_files import DEFAULT_PROGRAM_FILES_DIRECTORY, ProgramFileError, ProgramFileStore
from .presets import DEFAULT_PRESET_FILE, PresetError, PresetStore
from .registry import Registry, build_default_registry
from .runtime_transport import MAX_LAUNCH_STRUCTURE_BYTES
from .schema import ValidationError
from .supervisor import VisualizationSupervisor


MAX_REQUEST_BYTES = 64 * 1024
MAX_RUNTIME_CHANGES = 32
MAX_RUNTIME_PATH_LENGTH = 256
MAX_RUNTIME_ACTION_LENGTH = 128
MAX_RUNTIME_TARGET_LENGTH = 256
CONTROL_SESSION_COOKIE = "rgb_matrix_session"
BROWSER_SESSION_HEADER = "X-RGB-Matrix-Session"
# Temporary trusted-LAN convenience requested during UI development. Keep this
# explicit and easy to remove; it intentionally weakens possession-based pairing.
TEMPORARY_FALLBACK_PAIRING_CODE = "000000"

PROGRAM_PAGE_PREFIX = "/programs/"
MATRIX_SETTINGS_PATH = "/api/v1/matrix-settings"
PROGRAM_FILES_PREFIX = "/api/v1/programs/"
PAGE_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; connect-src 'self'; script-src 'self'; "
    "style-src 'self'; img-src 'self' data:; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
_JAVASCRIPT = "text/javascript; charset=utf-8"
# Every page asset has a literal route (web-control-refactor D-042); request paths
# are never joined into a filesystem path.
STATIC_ASSETS: dict[str, tuple[str, str]] = {
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
    "/static/js/api.js": ("js/api.js", _JAVASCRIPT),
    "/static/js/model.js": ("js/model.js", _JAVASCRIPT),
    "/static/js/ui.js": ("js/ui.js", _JAVASCRIPT),
    "/static/js/home.js": ("js/home.js", _JAVASCRIPT),
    "/static/js/program.js": ("js/program.js", _JAVASCRIPT),
    "/static/js/file_editor.js": ("js/file_editor.js", _JAVASCRIPT),
    "/static/js/fft_editor.js": ("js/fft_editor.js", _JAVASCRIPT),
    "/static/js/fft_graph.js": ("js/fft_graph.js", _JAVASCRIPT),
    "/static/js/fft_preset_controls.js": ("js/fft_preset_controls.js", _JAVASCRIPT),
    "/static/js/programs/fft.js": ("js/programs/fft.js", _JAVASCRIPT),
    "/static/js/programs/fft_layout.js": ("js/programs/fft_layout.js", _JAVASCRIPT),
    "/static/js/programs/fft_presets.js": ("js/programs/fft_presets.js", _JAVASCRIPT),
    "/static/js/programs/graph_traversal.js": (
        "js/programs/graph_traversal.js",
        _JAVASCRIPT,
    ),
    # Generated from the FFT code by tests/test_fft_catalog.py (D-049).
    "/static/fft_catalog.json": ("fft_catalog.json", "application/json; charset=utf-8"),
}


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


def validate_launch_structure(structure: Any) -> dict[str, Any]:
    """Apply the launch bounds to a layer structure and return a bounded copy.

    ``POST /api/v1/run`` and the preset store share this, so a saved preset can't
    hold a structure that launch would refuse (web-control-refactor D-025). The
    supervisor repeats the byte cap at its own boundary.
    """

    if not isinstance(structure, dict):
        raise ValidationError("structure must be a JSON object")
    bounded = _bounded_runtime_value(structure, label="structure")
    encoded = json.dumps(bounded, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_LAUNCH_STRUCTURE_BYTES:
        raise ValidationError(
            f"structure must be at most {MAX_LAUNCH_STRUCTURE_BYTES} bytes"
        )
    return bounded


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
            "invalid_structure": HTTPStatus.BAD_REQUEST,
            "invalid_operation": HTTPStatus.BAD_REQUEST,
            "invalid_reference": HTTPStatus.BAD_REQUEST,
            "limit_exceeded": HTTPStatus.BAD_REQUEST,
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
    preset_store: PresetStore | None = None,
    bluetooth: BluetoothController | None = None,
    matrix_settings: MatrixSettingsStore | None = None,
    program_files: ProgramFileStore | None = None,
    require_pairing: bool = True,
) -> type[BaseHTTPRequestHandler]:
    """Bind one supervisor and token policy to an HTTP handler class.

    Without ``preset_store`` the preset routes answer 503. Without ``bluetooth``
    the Bluetooth status reports ``available: false`` and its actions answer 503.
    Without ``matrix_settings`` the matrix-settings routes answer 503, and
    without ``program_files`` the program-file routes do.
    ``require_pairing=False`` opens visualization control to every request
    (web-control-refactor D-036); network administration still needs the token.
    """

    pairing = pairing_manager

    class ControlRequestHandler(BaseHTTPRequestHandler):
        server_version = "RGBMatrixControl/1"

        def do_GET(self) -> None:  # noqa: N802 - HTTP handler API name
            path = urlsplit(self.path).path
            if path == "/":
                self._send_page("home.html")
            elif path.startswith(PROGRAM_PAGE_PREFIX):
                # D-033: one shared page for every registered program. The ID is
                # only compared with the registry; it never reaches a file path.
                if path.removeprefix(PROGRAM_PAGE_PREFIX) in self._registered_ids():
                    self._send_page("program.html")
                else:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "program not found")
            elif path in STATIC_ASSETS:
                self._send_static_file(*STATIC_ASSETS[path])
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
            elif path == MATRIX_SETTINGS_PATH:
                # Public like the visualization list: the values aren't secret, and
                # a page without control access can still show them (D-008).
                self._handle_matrix_settings_request("get")
            elif path.startswith(PROGRAM_FILES_PREFIX):
                # Public like the matrix settings: colors aren't secret (D-044).
                self._handle_program_file_request("get", path)
            elif path == "/api/v1/runtime":
                if not self._is_control_authorized():
                    self._send_error_json(HTTPStatus.UNAUTHORIZED, "invalid or missing control session")
                else:
                    self._handle_runtime_snapshot()
            elif path == "/api/v1/presets" or path.startswith("/api/v1/presets/"):
                preset_id = self._preset_id_from_path(path)
                if path != "/api/v1/presets" and preset_id is None:
                    self._send_error_json(HTTPStatus.NOT_FOUND, "endpoint not found")
                elif not self._is_control_authorized():
                    self._send_error_json(HTTPStatus.UNAUTHORIZED, "invalid or missing control session")
                elif preset_id is None:
                    self._handle_preset_request("list")
                else:
                    self._handle_preset_request("get", preset_id=preset_id)
            elif path in {"/api/v1/bluetooth", "/api/v1/audio/sources"}:
                if not self._is_control_authorized():
                    self._send_error_json(HTTPStatus.UNAUTHORIZED, "invalid or missing control session")
                elif path == "/api/v1/bluetooth":
                    self._send_bluetooth_result(
                        lambda: bluetooth.status()
                        if bluetooth is not None
                        else unavailable_status("Bluetooth control is not configured")
                    )
                else:
                    self._send_bluetooth_result(
                        lambda: audio_source_catalog(supervisor.status(), bluetooth=bluetooth)
                    )
            elif path == "/api/v1/pairing/status":
                supplied_token = self._control_session_token()
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "authorized": self._is_control_authorized(),
                        "temporary_session": pairing.is_session_token(supplied_token),
                        "pairing_required": require_pairing,
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
                            "GET /",
                            "GET /programs/{visualization_id}",
                            "GET /api/v1/visualizations",
                            "GET /api/v1/status",
                            "GET /api/v1/matrix-settings",
                            "GET /api/v1/programs/{visualization_id}/files/{name}",
                            "GET /api/v1/runtime",
                            "POST /api/v1/runtime/actions",
                            "GET /api/v1/presets",
                            "GET /api/v1/presets/{preset_id}",
                            "GET /api/v1/bluetooth",
                            "GET /api/v1/audio/sources",
                            "GET /api/v1/pairing/status",
                            "GET /api/v1/network/status",
                            "GET /setup",
                            "POST /api/v1/run",
                            "POST /api/v1/stop",
                            "POST /api/v1/presets",
                            "PUT /api/v1/presets/{preset_id}",
                            "DELETE /api/v1/presets/{preset_id}",
                            "PUT /api/v1/matrix-settings",
                            "PUT /api/v1/programs/{visualization_id}/files/{name}",
                            "DELETE /api/v1/programs/{visualization_id}/files/{name}",
                            "POST /api/v1/bluetooth/actions",
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
            if self._refuse_cross_site_mutation():
                return
            try:
                if path == "/api/v1/pairing/exchange":
                    payload = self._read_json_object()
                    self._validate_exact_fields(payload, {"code"})
                    result = pairing.exchange(
                        payload["code"],
                        client_id=self.client_address[0],
                    )
                    browser_session = self.headers.get(BROWSER_SESSION_HEADER) == "cookie"
                    response_body: dict[str, Any] = {
                        "paired": True,
                        "expires_in": result.expires_in,
                    }
                    response_headers = None
                    if browser_session:
                        # The browser never receives the opaque credential in JSON or
                        # JavaScript-accessible storage. Path=/ shares it across the
                        # catalog, FFT controls, and future controller pages.
                        response_headers = {
                            "Set-Cookie": self._session_cookie_header(
                                result.token,
                                max_age=result.expires_in,
                            )
                        }
                    else:
                        # Preserve the pairing endpoint's bearer-token contract for
                        # non-browser clients that explicitly manage credentials.
                        response_body["token"] = result.token
                    self._send_json(
                        HTTPStatus.OK,
                        response_body,
                        headers=response_headers,
                    )
                    return
                if path == "/api/v1/pairing/logout":
                    payload = self._read_json_object()
                    self._validate_exact_fields(payload, set())
                    if not self._is_control_authorized():
                        self._send_error_json(HTTPStatus.UNAUTHORIZED, "invalid session")
                        return
                    if not self._cookie_mutation_is_same_origin():
                        self._send_error_json(HTTPStatus.FORBIDDEN, "cookie request origin is invalid")
                        return
                    pairing.revoke_session(self._control_session_token())
                    self._send_json(
                        HTTPStatus.OK,
                        {"revoked": True},
                        headers={"Set-Cookie": self._expired_session_cookie_header()},
                    )
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
                    self._send_error_json(HTTPStatus.UNAUTHORIZED, "invalid or missing control session")
                    return
                if not self._cookie_mutation_is_same_origin():
                    self._send_error_json(HTTPStatus.FORBIDDEN, "cookie request origin is invalid")
                    return

                if path == "/api/v1/runtime/actions":
                    command = _parse_runtime_action(self._read_json_object())
                    self._handle_runtime_command(command)
                    return

                if path == "/api/v1/presets":
                    self._handle_preset_request(
                        "create",
                        payload=self._read_json_object(),
                        success_status=HTTPStatus.CREATED,
                    )
                    return

                if path == "/api/v1/bluetooth/actions":
                    self._handle_bluetooth_action(self._read_json_object())
                    return

                if path == "/api/v1/run":
                    payload = self._read_json_object()
                    visualization_id = payload.get("visualization_id")
                    if type(visualization_id) is not str or not visualization_id:
                        raise ValidationError("visualization_id must be a non-empty string")
                    parameters = payload.get("parameters", {})
                    unknown = set(payload) - {"visualization_id", "parameters", "structure"}
                    if unknown:
                        raise ValidationError(
                            f"unknown run fields: {', '.join(sorted(unknown))}"
                        )
                    if "structure" in payload:
                        result = supervisor.start(
                            visualization_id,
                            parameters,
                            structure=validate_launch_structure(payload["structure"]),
                        )
                    else:
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

        def do_PUT(self) -> None:  # noqa: N802 - HTTP handler API name
            """Replace one whole preset (D-025), the matrix settings (D-035), or a
            program file (D-044)."""

            if self._refuse_cross_site_mutation():
                return
            path = urlsplit(self.path).path
            if path.startswith(PROGRAM_FILES_PREFIX):
                if not self._authorize_control_mutation():
                    return
                try:
                    payload = self._read_json_object()
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._handle_program_file_request("replace", path, payload=payload)
                return
            if path == MATRIX_SETTINGS_PATH:
                if not self._authorize_control_mutation():
                    return
                try:
                    payload = self._read_json_object()
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._handle_matrix_settings_request("replace", payload=payload)
                return
            preset_id = self._preset_id_from_path(path)
            if preset_id is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, "endpoint not found")
                return
            if not self._authorize_control_mutation():
                return
            try:
                payload = self._read_json_object()
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._handle_preset_request("replace", preset_id=preset_id, payload=payload)

        def do_DELETE(self) -> None:  # noqa: N802 - HTTP handler API name
            """Delete one preset at its expected revision (D-025), or clear an
            unusable program file (D-047; no body)."""

            if self._refuse_cross_site_mutation():
                return
            path = urlsplit(self.path).path
            if path.startswith(PROGRAM_FILES_PREFIX):
                if not self._authorize_control_mutation():
                    return
                # Like /stop: no body needed, but one that is sent must be JSON.
                if self.headers.get("Content-Length", "0") != "0":
                    try:
                        self._read_json_object()
                    except ValueError as exc:
                        self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                        return
                self._handle_program_file_request("remove", path)
                return
            preset_id = self._preset_id_from_path(path)
            if preset_id is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, "endpoint not found")
                return
            if not self._authorize_control_mutation():
                return
            try:
                payload = self._read_json_object()
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._handle_preset_request("delete", preset_id=preset_id, payload=payload)

        def _authorize_control_mutation(self) -> bool:
            """Send 401/403 and return False unless this mutation may proceed."""

            if not self._is_control_authorized():
                self._send_error_json(HTTPStatus.UNAUTHORIZED, "invalid or missing control session")
                return False
            if not self._cookie_mutation_is_same_origin():
                self._send_error_json(HTTPStatus.FORBIDDEN, "cookie request origin is invalid")
                return False
            return True

        @staticmethod
        def _preset_id_from_path(path: str) -> str | None:
            prefix = "/api/v1/presets/"
            if not path.startswith(prefix):
                return None
            preset_id = path[len(prefix) :]
            if not preset_id or "/" in preset_id:
                return None
            return preset_id

        def _handle_preset_request(
            self,
            operation: str,
            *,
            preset_id: str | None = None,
            payload: dict[str, Any] | None = None,
            success_status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            if preset_store is None:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "preset store is unavailable", "code": "preset_store_unavailable"},
                )
                return
            try:
                if operation == "list":
                    result = preset_store.list()
                elif operation == "get":
                    result = preset_store.get(preset_id)
                elif operation == "create":
                    result = preset_store.create(payload)
                elif operation == "replace":
                    result = preset_store.replace(preset_id, payload)
                elif operation == "delete":
                    result = preset_store.delete(preset_id, payload)
                else:  # pragma: no cover - code-owned operation names
                    raise RuntimeError(f"unknown preset operation: {operation}")
            except PresetError as exc:
                self._send_json(exc.status, exc.as_body())
                return
            self._send_json(success_status, result)

        def _handle_matrix_settings_request(
            self,
            operation: str,
            *,
            payload: dict[str, Any] | None = None,
        ) -> None:
            if matrix_settings is None:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {
                        "error": "matrix settings are unavailable",
                        "code": "matrix_settings_unavailable",
                    },
                )
                return
            try:
                if operation == "get":
                    result = matrix_settings.snapshot()
                else:
                    result = matrix_settings.replace(payload)
            except MatrixSettingsError as exc:
                self._send_json(exc.status, exc.as_body())
                return
            self._send_json(HTTPStatus.OK, result)

        def _handle_program_file_request(
            self,
            operation: str,
            path: str,
            *,
            payload: dict[str, Any] | None = None,
        ) -> None:
            # ``/api/v1/programs/<id>/files/<name>``. Both parts are only looked
            # up in the registry; neither reaches a file path.
            parts = path.removeprefix(PROGRAM_FILES_PREFIX).split("/")
            if len(parts) != 3 or parts[1] != "files" or not parts[0] or not parts[2]:
                self._send_error_json(HTTPStatus.NOT_FOUND, "endpoint not found")
                return
            program_id, _files, name = parts
            if program_files is None:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {
                        "error": "program files are unavailable",
                        "code": "program_file_unavailable",
                    },
                )
                return
            try:
                if operation == "get":
                    result = program_files.snapshot(program_id, name)
                elif operation == "replace":
                    result = program_files.replace(program_id, name, payload)
                else:
                    result = program_files.remove_unusable(program_id, name)
            except ProgramFileError as exc:
                self._send_json(exc.status, exc.as_body())
                return
            self._send_json(HTTPStatus.OK, result)

        def _registered_ids(self) -> set[str]:
            return {
                item["id"]
                for item in supervisor.list_visualizations()
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }

        def _handle_bluetooth_action(self, payload: dict[str, Any]) -> None:
            if bluetooth is None:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "Bluetooth control is not configured", "code": "bluetooth_unavailable"},
                )
                return
            self._send_bluetooth_result(lambda: bluetooth.perform(payload))

        def _send_bluetooth_result(self, produce: Any) -> None:
            """Send a Bluetooth result; never let an exception reach the log.

            The server would print an escaped traceback to journald, and a D-Bus
            message there can name a device path, which embeds the address (D-030).
            """

            try:
                result = produce()
            except BluetoothError as exc:
                self._send_json(exc.status, exc.as_body())
                return
            except Exception:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "Bluetooth request failed", "code": "bluetooth_failed"},
                )
                return
            self._send_json(HTTPStatus.OK, result)

        def _handle_runtime_snapshot(self) -> None:
            try:
                self._send_json(HTTPStatus.OK, _runtime_snapshot(supervisor))
            except Exception as exc:
                status, code, message = _runtime_error_response(exc)
                body = {"error": message, "code": code}
                if isinstance(getattr(exc, "path", None), str):
                    body["path"] = exc.path
                self._send_json(status, body)

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
                body = {"error": message, "code": code}
                if isinstance(getattr(exc, "path", None), str):
                    body["path"] = exc.path
                self._send_json(status, body)

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
            has_admin_bearer = (
                token is not None
                and api_token is not None
                and hmac.compare_digest(token, api_token)
            )
            if pairing.is_session_token(token) or (
                pairing.is_session_token(self._cookie_token()) and not has_admin_bearer
            ):
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
            """Allow visualization control (never network administration).

            With ``require_pairing`` off, every request counts as a paired session
            (web-control-refactor D-036); the pairing code paths stay in place.
            """

            if not require_pairing:
                return True
            supplied_token = self._control_session_token()
            return self._is_admin_authorized() or pairing.is_session_token(supplied_token)

        def _control_session_token(self) -> str | None:
            """Prefer an explicit bearer token, then fall back to the browser cookie."""

            return self._bearer_token() or self._cookie_token()

        def _cookie_token(self) -> str | None:
            raw_cookie = self.headers.get("Cookie")
            if not raw_cookie:
                return None
            cookies = SimpleCookie()
            try:
                cookies.load(raw_cookie)
            except CookieError:
                return None
            morsel = cookies.get(CONTROL_SESSION_COOKIE)
            if morsel is None or not morsel.value:
                return None
            return morsel.value

        def _cookie_mutation_is_same_origin(self) -> bool:
            """Require an exact Origin match only when a cookie grants access.

            Bearer clients remain usable from scripts and terminals without browser
            headers. Browsers send Origin on fetch-based POST/PUT/DELETE requests, so an
            unrelated site cannot silently drive the controller with ambient cookies.
            """

            bearer = self._bearer_token()
            if bearer is not None and (
                self._is_admin_authorized() or pairing.is_session_token(bearer)
            ):
                return True
            cookie_token = self._cookie_token()
            if not pairing.is_session_token(cookie_token):
                return True
            origin = self.headers.get("Origin")
            return origin is not None and self._origin_matches_host(origin)

        def _mutation_origin_is_allowed(self) -> bool:
            """Refuse every cross-site mutation (web-control-refactor D-036).

            With pairing off nothing else stops another site in the phone's browser
            from posting here: bodies aren't checked for a JSON content type and
            there is no preflight handler. Browsers send ``Origin`` on these
            requests; scripts and curl usually don't, and stay allowed.
            """

            origin = self.headers.get("Origin")
            return origin is None or self._origin_matches_host(origin)

        def _origin_matches_host(self, origin: str) -> bool:
            host = self.headers.get("Host")
            if not host:
                return False
            parsed = urlsplit(origin)
            return (
                parsed.scheme == "http"
                and parsed.netloc.casefold() == host.casefold()
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
            )

        def _refuse_cross_site_mutation(self) -> bool:
            """Send 403 and return True when this mutation comes from another site."""

            if self._mutation_origin_is_allowed():
                return False
            self._send_error_json(HTTPStatus.FORBIDDEN, "cross-site requests are refused")
            return True

        def _is_loopback_client(self) -> bool:
            return _is_loopback_address(self.client_address[0])

        def _bearer_token(self) -> str | None:
            supplied = self.headers.get("Authorization", "")
            prefix = "Bearer "
            if not supplied.startswith(prefix):
                return None
            return supplied[len(prefix) :]

        @staticmethod
        def _session_cookie_header(token: str, *, max_age: int) -> str:
            cookie = SimpleCookie()
            cookie[CONTROL_SESSION_COOKIE] = token
            morsel = cookie[CONTROL_SESSION_COOKIE]
            morsel["path"] = "/"
            morsel["max-age"] = str(max_age)
            morsel["httponly"] = True
            morsel["samesite"] = "Strict"
            # Secure cannot be used until the LAN service supports HTTPS. The cookie
            # remains host-only because no Domain attribute is set.
            return morsel.OutputString()

        @staticmethod
        def _expired_session_cookie_header() -> str:
            cookie = SimpleCookie()
            cookie[CONTROL_SESSION_COOKIE] = ""
            morsel = cookie[CONTROL_SESSION_COOKIE]
            morsel["path"] = "/"
            morsel["max-age"] = "0"
            morsel["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
            morsel["httponly"] = True
            morsel["samesite"] = "Strict"
            return morsel.OutputString()

        def _send_page(self, filename: str) -> None:
            self._send_static_file(
                filename,
                "text/html; charset=utf-8",
                content_security_policy=PAGE_CONTENT_SECURITY_POLICY,
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


def registry_for_config(controller_config: ControllerConfig) -> Registry:
    """Build the registry with every configured program's own defaults.

    The service once listed four programs here and left Graph Traversal out, so
    its config block never applied (fixed in web-control-refactor M4).
    """

    return build_default_registry(
        parameter_defaults=dict(controller_config.visualization_defaults)
    )


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
        "--preset-file",
        type=Path,
        default=DEFAULT_PRESET_FILE,
        help="State file holding the FFT presets saved from the web pages",
    )
    parser.add_argument(
        "--matrix-settings-file",
        type=Path,
        default=DEFAULT_MATRIX_SETTINGS_FILE,
        help="State file holding the shared matrix settings saved from the web pages",
    )
    parser.add_argument(
        "--program-files-dir",
        type=Path,
        default=DEFAULT_PROGRAM_FILES_DIRECTORY,
        help="State directory holding program config files saved from the web pages",
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
    registry = registry_for_config(controller_config)
    matrix_settings = MatrixSettingsStore(
        args.matrix_settings_file,
        config_values=config_matrix_values(controller_config.matrix_defaults),
    )
    program_files = ProgramFileStore(registry, args.program_files_dir)
    supervisor = VisualizationSupervisor(
        registry,
        python_executable=python_executable,
        command_prefix=command_prefix,
        stop_timeout=stop_timeout,
        runtime_dir=os.environ.get("RGBMATRIX_RUNTIME_DIR"),
        matrix_settings=matrix_settings.effective_values,
        program_files=program_files.launch_files,
    )
    # Nothing touches D-Bus until the first Bluetooth request; a missing binding or
    # bus only makes those routes report unavailable (M3 criterion 8).
    bluetooth = BluetoothController(DbusBluetoothBackend())
    server = ThreadingHTTPServer(
        (args.host, args.port),
        build_handler(
            supervisor,
            api_token=token,
            network_client=NetworkClient(args.network_socket),
            pairing_manager=PairingManager(
                boot_code,
                additional_codes=(TEMPORARY_FALLBACK_PAIRING_CODE,),
            ),
            preset_store=PresetStore(
                args.preset_file,
                validate_parameters=registry.get("fft").validate_parameters,
                validate_structure=validate_launch_structure,
            ),
            bluetooth=bluetooth,
            matrix_settings=matrix_settings,
            program_files=program_files,
            require_pairing=controller_config.require_pairing,
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
        try:
            bluetooth.shutdown()  # closes a pairing window left open
        except Exception:
            pass
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
