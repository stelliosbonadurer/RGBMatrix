"""Small standard-library HTTP API for the RGB matrix supervisor MVP."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import signal
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from matrix_network import NetworkClient, NetworkClientError

from .registry import build_default_registry
from .schema import ValidationError
from .supervisor import VisualizationSupervisor


MAX_REQUEST_BYTES = 64 * 1024


def build_handler(
    supervisor: VisualizationSupervisor,
    *,
    api_token: str | None,
    network_client: NetworkClient | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Bind one supervisor and token policy to an HTTP handler class."""

    class ControlRequestHandler(BaseHTTPRequestHandler):
        server_version = "RGBMatrixControl/1"

        def do_GET(self) -> None:  # noqa: N802 - HTTP handler API name
            path = urlsplit(self.path).path
            if path == "/setup":
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
            elif path == "/healthz":
                self._send_json(HTTPStatus.OK, {"status": "ok"})
            elif path == "/":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "service": "rgb-matrix-control",
                        "api_version": "v1",
                        "endpoints": [
                            "GET /api/v1/visualizations",
                            "GET /api/v1/status",
                            "GET /api/v1/network/status",
                            "GET /setup",
                            "POST /api/v1/run",
                            "POST /api/v1/stop",
                            "POST /api/v1/network/provision",
                        ],
                    },
                )
            else:
                self._send_error_json(HTTPStatus.NOT_FOUND, "endpoint not found")

        def do_POST(self) -> None:  # noqa: N802 - HTTP handler API name
            path = urlsplit(self.path).path
            try:
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

                if not self._is_authorized():
                    self._send_error_json(HTTPStatus.UNAUTHORIZED, "invalid or missing bearer token")
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
            except RuntimeError as exc:
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

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

        def _is_authorized(self) -> bool:
            if api_token is None:
                return True
            supplied_token = self._bearer_token()
            return supplied_token is not None and hmac.compare_digest(supplied_token, api_token)

        def _bearer_token(self) -> str | None:
            supplied = self.headers.get("Authorization", "")
            prefix = "Bearer "
            if not supplied.startswith(prefix):
                return None
            return supplied[len(prefix) :]

        def _send_setup_page(self) -> None:
            page_path = Path(__file__).resolve().parent / "static" / "setup.html"
            try:
                encoded = page_path.read_bytes()
            except OSError:
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "setup page is unavailable")
                return
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def _redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.FOUND.value)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
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
    parser.add_argument("--host", default="127.0.0.1", help="Address on which to listen")
    parser.add_argument("--port", type=int, default=8080, help="TCP port on which to listen")
    parser.add_argument("--python", default=None, help="Python executable used for visualizations")
    parser.add_argument("--token-file", help="File containing the bearer token for POST requests")
    parser.add_argument(
        "--network-socket",
        default="/run/rgb-matrix-network/control.sock",
        help="Unix socket for the privileged NetworkManager helper",
    )
    parser.add_argument(
        "--sudo",
        action="store_true",
        help="Prefix visualization commands with non-interactive sudo -E",
    )
    parser.add_argument("--stop-timeout", type=float, default=5.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not (1 <= args.port <= 65535):
        raise SystemExit("--port must be between 1 and 65535")
    if args.stop_timeout <= 0:
        raise SystemExit("--stop-timeout must be positive")

    try:
        token = _read_token(args.token_file)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"could not load API token: {exc}") from exc

    loopback_hosts = {"127.0.0.1", "::1", "localhost"}
    if args.host not in loopback_hosts and token is None:
        raise SystemExit("a bearer token is required when listening beyond loopback")

    command_prefix = ("sudo", "-n", "-E") if args.sudo else ()
    supervisor = VisualizationSupervisor(
        build_default_registry(),
        python_executable=args.python or os.environ.get("RGBMATRIX_PYTHON") or os.sys.executable,
        command_prefix=command_prefix,
        stop_timeout=args.stop_timeout,
    )
    server = ThreadingHTTPServer(
        (args.host, args.port),
        build_handler(
            supervisor,
            api_token=token,
            network_client=NetworkClient(args.network_socket),
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
