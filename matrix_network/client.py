"""Unprivileged client for the root-owned network control Unix socket."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any


MAX_RESPONSE_BYTES = 64 * 1024


class NetworkClientError(RuntimeError):
    def __init__(self, message: str, *, code: str = "network_service_error", status: int = 503):
        super().__init__(message)
        self.code = code
        self.status = status


class NetworkClient:
    """Send one bounded JSON request per local Unix-socket connection."""

    def __init__(self, socket_path: str | Path, timeout: float = 5.0):
        self.socket_path = str(socket_path)
        self.timeout = timeout

    def request(
        self,
        operation: str,
        *,
        payload: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> Any:
        request = {
            "operation": operation,
            "payload": payload or {},
            "authorization": token,
        }
        encoded = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > MAX_RESPONSE_BYTES:
            raise NetworkClientError("network request is too large", status=400)

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout)
                connection.connect(self.socket_path)
                connection.sendall(encoded)
                chunks = bytearray()
                while len(chunks) <= MAX_RESPONSE_BYTES:
                    block = connection.recv(min(4096, MAX_RESPONSE_BYTES + 1 - len(chunks)))
                    if not block:
                        break
                    chunks.extend(block)
                    if b"\n" in block:
                        break
        except (OSError, TimeoutError) as exc:
            raise NetworkClientError("network control service is unavailable") from exc

        if len(chunks) > MAX_RESPONSE_BYTES:
            raise NetworkClientError("network control response is too large")
        try:
            response = json.loads(bytes(chunks).split(b"\n", 1)[0].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NetworkClientError("network control service returned an invalid response") from exc

        if not isinstance(response, dict):
            raise NetworkClientError("network control service returned an invalid response")
        if not response.get("ok"):
            raise NetworkClientError(
                str(response.get("error") or "network operation failed"),
                code=str(response.get("code") or "network_operation_failed"),
                status=int(response.get("status") or 500),
            )
        return response.get("result")

