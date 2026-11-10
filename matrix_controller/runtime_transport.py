"""Private, per-run control transport for interactive visualizations.

The web service and a visualization process have deliberately different
responsibilities.  The supervisor owns process lifetime while the child owns its
live settings.  This module provides the small bridge between those owners without
exposing a control socket on the network and without encoding controls as terminal
keypresses.

The protocol is newline-delimited JSON over a Unix-domain socket.  Frames are
bounded, requests carry a run identifier and a random one-run capability, and all
I/O has an explicit timeout.  A visualization can use :class:`RuntimeControlClient`
from a daemon thread and hand each validated operation to its own frame-boundary
controller.  The client never knows anything about FFT, matrix hardware, or HTTP.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import queue
import select
import secrets
import shutil
import socket
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 64 * 1024
MAX_OPERATION_LENGTH = 64
MAX_REQUEST_ID_LENGTH = 128
DEFAULT_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 10.0
# The RGB matrix native library initializes GPIO as root, then deliberately drops
# the visualization to the system ``daemon`` account. That child must be able to
# traverse to and connect to the supervisor-owned endpoint after the UID change.
# Endpoint discovery and authentication remain capability-based: parent/child
# directories cannot be listed by other users, each path has high entropy, and every
# request carries an independent 256-bit one-run token.
ENDPOINT_DIRECTORY_MODE = 0o711
ENDPOINT_SOCKET_MODE = 0o622


class RuntimeTransportError(RuntimeError):
    """Base error for connection, protocol, and remote runtime failures."""

    code = "runtime_error"


class RuntimeUnavailable(RuntimeTransportError):
    """The child is not connected or the control endpoint has gone away."""

    code = "unavailable"


class RuntimeTimeout(RuntimeUnavailable):
    """A control request did not complete within its bounded timeout."""

    code = "timeout"


class RuntimeProtocolError(RuntimeTransportError):
    """A peer sent malformed, oversized, or incompatible protocol data."""

    code = "protocol_error"


class RuntimeAuthenticationError(RuntimeProtocolError):
    """A peer supplied the wrong per-run identity or capability."""


class RuntimeRunMismatch(RuntimeTransportError):
    """A caller tried to use a control channel for a different visualization run."""

    code = "stale_run"


class RuntimeRemoteError(RuntimeTransportError):
    """The child rejected a valid request and returned a structured error."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = _bounded_message(message)
        super().__init__(f"{code}: {self.message}")


class RuntimeControlError(RuntimeTransportError):
    """Handler exception that is safe to return to the supervisor."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = _bounded_message(message)
        super().__init__(self.message)


RuntimeRequestHandler = Callable[[str, Any], Mapping[str, Any] | Any]


@dataclass
class _PendingRuntimeOperation:
    """One operation waiting for the visualization's frame-boundary thread."""

    operation: str
    payload: Any
    completed: threading.Event
    result: Any = None
    error: RuntimeControlError | None = None
    cancelled: bool = False


class FrameBoundaryRuntimeBridge:
    """Bounded adapter between the socket listener and the render loop.

    ``RuntimeControlClient.serve`` runs in a daemon listener thread.  A
    visualization should pass ``bridge.handle`` to it, then call
    ``bridge.process_pending(controller.apply, max_items=...)`` once per render
    frame.  The listener waits for the frame-boundary result and sends the result
    back to the supervisor; it never mutates renderer/audio state itself.

    The queue has a fixed capacity and each wait has a timeout.  A slow or
    disconnected client therefore cannot accumulate work or hold the render loop
    hostage.  Commands that time out before the frame thread picks them up are
    marked cancelled and skipped.
    """

    def __init__(self, *, max_pending: int = 32, response_timeout: float = 1.0):
        if max_pending <= 0:
            raise ValueError("max_pending must be greater than zero")
        self._response_timeout = _timeout(response_timeout)
        self._pending: queue.Queue[_PendingRuntimeOperation] = queue.Queue(maxsize=max_pending)
        self._lock = threading.RLock()
        self._closed = False

    def handle(self, operation: str, payload: Any) -> Any:
        """Enqueue one operation and wait for its frame-boundary result."""

        item = _PendingRuntimeOperation(operation, payload, threading.Event())
        with self._lock:
            if self._closed:
                raise RuntimeControlError("unavailable", "runtime command bridge is closed")
            try:
                self._pending.put_nowait(item)
            except queue.Full as exc:
                raise RuntimeControlError("busy", "runtime command queue is full") from exc

        if not item.completed.wait(self._response_timeout):
            with self._lock:
                item.cancelled = True
            raise RuntimeControlError("timeout", "runtime command was not applied in time")
        if item.error is not None:
            raise item.error
        return item.result

    def __call__(self, operation: str, payload: Any) -> Any:
        return self.handle(operation, payload)

    def process_pending(
        self,
        handler: RuntimeRequestHandler,
        *,
        max_items: int = 1,
    ) -> int:
        """Apply up to ``max_items`` queued operations at the caller's frame boundary."""

        if max_items <= 0:
            raise ValueError("max_items must be greater than zero")
        processed = 0
        while processed < max_items:
            try:
                item = self._pending.get_nowait()
            except queue.Empty:
                break
            try:
                with self._lock:
                    cancelled = item.cancelled or self._closed
                if cancelled:
                    continue
                item.result = handler(item.operation, item.payload)
            except RuntimeControlError as exc:
                item.error = exc
            except Exception as exc:  # pragma: no cover - handler owns policy
                item.error = RuntimeControlError("handler_error", _bounded_message(exc))
            finally:
                item.completed.set()
                self._pending.task_done()
                processed += 1
        return processed

    def close(self) -> None:
        """Wake queued listeners and prevent new work during visualization teardown."""

        with self._lock:
            self._closed = True
            while True:
                try:
                    item = self._pending.get_nowait()
                except queue.Empty:
                    break
                item.error = RuntimeControlError("unavailable", "runtime command bridge is closed")
                item.completed.set()
                self._pending.task_done()


@dataclass(frozen=True)
class RuntimeEndpoint:
    """Credentials and endpoint passed to exactly one child process.

    ``token`` is intentionally excluded from the dataclass representation so a
    logged endpoint cannot accidentally disclose the capability.  Callers should
    use :meth:`environment` to pass it to a child and should never expose that
    mapping through a public HTTP response.
    """

    socket_path: Path
    run_id: str
    token: str

    def __repr__(self) -> str:  # pragma: no cover - defensive logging behavior
        return f"RuntimeEndpoint(socket_path={self.socket_path!s}, run_id={self.run_id!r})"

    def environment(self) -> dict[str, str]:
        """Return the child-side environment contract.

        These names are intentionally stable so a visualization can opt into live
        controls without importing supervisor or web-service code.
        """

        return {
            "RGB_MATRIX_RUNTIME_SOCKET": str(self.socket_path),
            "RGB_MATRIX_RUNTIME_TOKEN": self.token,
            "RGB_MATRIX_RUN_ID": self.run_id,
        }


def _bounded_message(message: str, limit: int = 500) -> str:
    text = str(message)
    return text if len(text) <= limit else f"{text[: limit - 3]}..."


def _timeout(value: float | None) -> float:
    timeout = DEFAULT_TIMEOUT_SECONDS if value is None else float(value)
    if timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
        raise ValueError(
            f"runtime timeout must be greater than zero and at most {MAX_TIMEOUT_SECONDS:g} seconds"
        )
    return timeout


def _json_frame(value: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            value,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeProtocolError(f"runtime message is not JSON-safe: {exc}") from exc
    if len(encoded) + 1 > MAX_FRAME_BYTES:
        raise RuntimeProtocolError("runtime message exceeds the frame size limit")
    return encoded + b"\n"


def _read_frame(sock: socket.socket, timeout: float, buffer: bytearray | None = None) -> dict[str, Any]:
    """Read exactly one bounded JSON line, retaining no unbounded peer data."""

    data = buffer if buffer is not None else bytearray()
    deadline = time.monotonic() + timeout
    while True:
        newline = data.find(b"\n")
        if newline >= 0:
            line = bytes(data[:newline])
            del data[: newline + 1]
            if not line:
                raise RuntimeProtocolError("runtime message cannot be empty")
            try:
                decoded = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeProtocolError(f"invalid runtime JSON: {exc}") from exc
            if not isinstance(decoded, dict):
                raise RuntimeProtocolError("runtime message must be a JSON object")
            return decoded

        if len(data) >= MAX_FRAME_BYTES:
            raise RuntimeProtocolError("runtime message exceeds the frame size limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeTimeout("timed out waiting for runtime message")
        try:
            readable, _, _ = select.select([sock], [], [], remaining)
        except OSError as exc:
            raise RuntimeUnavailable("runtime peer closed while waiting for a message") from exc
        if not readable:
            raise RuntimeTimeout("timed out waiting for runtime message")
        try:
            chunk = sock.recv(min(4096, MAX_FRAME_BYTES - len(data)))
        except OSError as exc:
            raise RuntimeUnavailable("runtime peer closed while reading a message") from exc
        if not chunk:
            raise RuntimeUnavailable("runtime peer closed the control socket")
        data.extend(chunk)


def _send_frame(sock: socket.socket, message: Mapping[str, Any], timeout: float) -> None:
    frame = _json_frame(message)
    previous_timeout = sock.gettimeout()
    try:
        sock.settimeout(timeout)
        sock.sendall(frame)
    except OSError as exc:
        raise RuntimeUnavailable("runtime peer closed while sending a message") from exc
    finally:
        sock.settimeout(previous_timeout)


def _validate_common_message(message: Mapping[str, Any]) -> None:
    if message.get("protocol") != PROTOCOL_VERSION:
        raise RuntimeProtocolError("unsupported runtime protocol version")
    request_id = message.get("request_id")
    if not isinstance(request_id, str) or not request_id or len(request_id) > MAX_REQUEST_ID_LENGTH:
        raise RuntimeProtocolError("runtime request_id is invalid")


class RuntimeControlServer:
    """Supervisor-side endpoint for one visualization process.

    The server is created before ``Popen`` and binds a private socket.  The child
    connects using the environment returned by :attr:`endpoint`.  Requests are
    serialized so a response always belongs to the caller's request, and a closed
    child connection can never block process shutdown indefinitely.
    """

    def __init__(
        self,
        run_id: str | int,
        *,
        runtime_dir: str | os.PathLike[str] | None = None,
        token: str | None = None,
    ):
        self._run_id = str(run_id)
        self._token = token or base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
        root = Path(runtime_dir) if runtime_dir is not None else None
        if root is not None:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Keep the filesystem component compact: sockaddr_un is only about 104
        # bytes on macOS, and development temp roots can already be quite long.
        self._directory = Path(tempfile.mkdtemp(prefix="rm-", dir=str(root) if root else None))
        self._directory.chmod(ENDPOINT_DIRECTORY_MODE)
        self._socket_path = self._directory / "control.sock"
        self._listener: socket.socket | None = None
        self._connection: socket.socket | None = None
        self._receive_buffer = bytearray()
        self._lock = threading.RLock()
        self._closed = False
        self._bind()

    def _bind(self) -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self._socket_path))
            os.chmod(self._socket_path, ENDPOINT_SOCKET_MODE)
            listener.listen(1)
        except Exception:
            listener.close()
            self.close()
            raise
        self._listener = listener

    @property
    def endpoint(self) -> RuntimeEndpoint:
        return RuntimeEndpoint(self._socket_path, self._run_id, self._token)

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connection is not None

    def request(
        self,
        operation: str,
        payload: Any = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """Send one operation and return its child-provided result.

        The operation and payload are intentionally generic.  A visualization owns
        its schema and can define ``snapshot``/``command`` or additional operations
        while the supervisor only enforces transport safety and run ownership.
        """

        if not isinstance(operation, str) or not operation or len(operation) > MAX_OPERATION_LENGTH:
            raise RuntimeProtocolError("runtime operation is invalid")
        wait = _timeout(timeout)
        request_id = uuid.uuid4().hex
        message = {
            "protocol": PROTOCOL_VERSION,
            "request_id": request_id,
            "run_id": self._run_id,
            "token": self._token,
            "operation": operation,
            "payload": payload,
        }
        # Validate size before waiting for a child.  This keeps malformed caller
        # input from occupying the one available control channel.
        _json_frame(message)

        with self._lock:
            connection = self._connection_or_accept(wait)
            try:
                _send_frame(connection, message, wait)
                response = _read_frame(connection, wait, self._receive_buffer)
                self._validate_response(response, request_id, self._run_id)
            except RuntimeTransportError:
                self._drop_connection_locked()
                raise

        if not response.get("ok", False):
            error = response.get("error")
            if not isinstance(error, dict):
                raise RuntimeProtocolError("runtime error response is malformed")
            raise RuntimeRemoteError(str(error.get("code", "remote_error")), str(error.get("message", "request rejected")))
        return response.get("result")

    def _connection_or_accept(self, timeout: float) -> socket.socket:
        if self._closed or self._listener is None:
            raise RuntimeUnavailable("runtime control endpoint is closed")
        if self._connection is not None:
            return self._connection
        self._listener.settimeout(timeout)
        try:
            connection, _ = self._listener.accept()
        except socket.timeout as exc:
            raise RuntimeTimeout("timed out waiting for visualization control client") from exc
        except OSError as exc:
            raise RuntimeUnavailable(f"could not accept runtime control client: {exc}") from exc
        connection.setblocking(True)
        self._connection = connection
        self._receive_buffer.clear()
        return connection

    @staticmethod
    def _validate_response(
        response: Mapping[str, Any], request_id: str, run_id: str
    ) -> None:
        _validate_common_message(response)
        if response.get("request_id") != request_id:
            raise RuntimeProtocolError("runtime response does not match request")
        if response.get("run_id") != run_id:
            raise RuntimeProtocolError("runtime response has no run identity")

    def _drop_connection_locked(self) -> None:
        connection = self._connection
        self._connection = None
        self._receive_buffer.clear()
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._drop_connection_locked()
            listener = self._listener
            self._listener = None
            if listener is not None:
                listener.close()
            try:
                self._socket_path.unlink(missing_ok=True)
            except OSError:
                pass
            shutil.rmtree(self._directory, ignore_errors=True)

    def __enter__(self) -> "RuntimeControlServer":
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()


class RuntimeControlClient:
    """Child-side adapter used by a visualization's control listener thread.

    Typical integration is::

        client = RuntimeControlClient.from_environment()
        if client is not None:
            threading.Thread(
                target=client.serve,
                args=(controller.handle_runtime_operation,),
                daemon=True,
            ).start()

    ``handle_runtime_operation(operation, payload)`` should enqueue or apply the
    operation at the visualization's frame boundary and return a JSON-safe result.
    It should raise :class:`RuntimeControlError` for expected validation/conflict
    failures.  The listener is separate from the render loop, so a disconnected or
    slow phone cannot stall audio or matrix drawing.
    """

    def __init__(self, endpoint: RuntimeEndpoint, *, connect_timeout: float | None = None):
        self.endpoint = endpoint
        self.connect_timeout = _timeout(connect_timeout)

    @classmethod
    def from_environment(cls, *, connect_timeout: float | None = None) -> "RuntimeControlClient | None":
        socket_path = os.environ.get("RGB_MATRIX_RUNTIME_SOCKET")
        token = os.environ.get("RGB_MATRIX_RUNTIME_TOKEN")
        run_id = os.environ.get("RGB_MATRIX_RUN_ID")
        if not socket_path and not token and not run_id:
            return None
        if not socket_path or not token or not run_id:
            raise RuntimeProtocolError("runtime control environment is incomplete")
        if len(socket_path) > 107:
            raise RuntimeProtocolError("runtime control socket path is too long")
        return cls(RuntimeEndpoint(Path(socket_path), run_id, token), connect_timeout=connect_timeout)

    def serve(
        self,
        handler: RuntimeRequestHandler,
        *,
        stop_event: threading.Event | None = None,
    ) -> None:
        """Connect once and serve bounded requests until the supervisor disconnects."""

        if not callable(handler):
            raise TypeError("runtime handler must be callable")
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(self.connect_timeout)
            try:
                connection.connect(str(self.endpoint.socket_path))
            except OSError:
                # The supervisor may have stopped/replaced the run before the
                # visualization's listener thread started. This is a normal
                # lifecycle outcome, not an uncaught child-thread exception.
                return
            connection.setblocking(True)
            receive_buffer = bytearray()
            while stop_event is None or not stop_event.is_set():
                try:
                    request = _read_frame(connection, 0.5, receive_buffer)
                except RuntimeTimeout:
                    continue
                except RuntimeUnavailable:
                    return
                try:
                    result = self._dispatch(request, handler)
                    response: dict[str, Any] = {
                        "protocol": PROTOCOL_VERSION,
                        "request_id": request.get("request_id"),
                        "run_id": self.endpoint.run_id,
                        "ok": True,
                        "result": result,
                    }
                except RuntimeControlError as exc:
                    response = self._error_response(request, exc.code, exc.message)
                except RuntimeProtocolError as exc:
                    response = self._error_response(request, "protocol_error", str(exc))
                except Exception as exc:  # pragma: no cover - defensive child boundary
                    response = self._error_response(request, "handler_error", _bounded_message(exc))
                _send_frame(connection, response, self.connect_timeout)
        finally:
            connection.close()

    def _dispatch(self, request: Mapping[str, Any], handler: RuntimeRequestHandler) -> Any:
        _validate_common_message(request)
        if request.get("run_id") != self.endpoint.run_id:
            raise RuntimeAuthenticationError("runtime run identity does not match")
        supplied_token = request.get("token")
        if not isinstance(supplied_token, str) or not hmac.compare_digest(supplied_token, self.endpoint.token):
            raise RuntimeAuthenticationError("runtime capability is invalid")
        operation = request.get("operation")
        if not isinstance(operation, str) or not operation or len(operation) > MAX_OPERATION_LENGTH:
            raise RuntimeProtocolError("runtime operation is invalid")
        return handler(operation, request.get("payload"))

    def _error_response(self, request: Mapping[str, Any], code: str, message: str) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL_VERSION,
            "request_id": request.get("request_id"),
            "run_id": self.endpoint.run_id,
            "ok": False,
            "error": {"code": _bounded_message(code, 64), "message": _bounded_message(message)},
        }
