"""Privileged NetworkManager helper exposed through a narrow Unix socket."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import secrets
import signal
import socket
import socketserver
import struct
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .backend import NetworkManagerBackend
from .controller import NetworkController, NetworkPolicy, validate_credentials
from .dbus_secrets import NetworkManagerDbusSecrets
from .models import NetworkOperationError, NetworkValidationError
from .nmcli import NmcliRunner


MAX_REQUEST_BYTES = 64 * 1024
MAX_JOBS = 20
LOGGER = logging.getLogger(__name__)


class NetworkDaemon:
    """Authorize commands, track bounded async jobs, and call the controller."""

    def __init__(
        self,
        controller: NetworkController,
        *,
        admin_token: str,
        setup_token: str,
        transition_delay: float = 2.0,
    ):
        self.controller = controller
        self._admin_token = admin_token
        self._setup_token = setup_token
        self._transition_delay = transition_delay
        self._jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._jobs_lock = threading.RLock()
        self._active_job: str | None = None

    def dispatch(self, request: Any, *, peer_uid: int | None = None) -> dict[str, Any]:
        if not isinstance(request, dict):
            return self._error(400, "invalid_request", "request must be a JSON object")
        operation = request.get("operation")
        payload = request.get("payload", {})
        token = request.get("authorization")
        if type(operation) is not str or not isinstance(payload, dict):
            return self._error(400, "invalid_request", "operation and payload are required")

        try:
            if operation == "status":
                result = self.controller.status()
            elif operation == "scan":
                self._require_scope(token, {"admin", "setup"})
                result = {"networks": self.controller.scan()}
            elif operation == "profiles":
                self._require_scope(token, {"admin"})
                result = {"profiles": self.controller.profiles()}
            elif operation == "provision":
                scope = self._require_scope(token, {"admin", "setup"})
                self._validate_fields(
                    payload,
                    required={"ssid", "passphrase", "hidden", "confirm_disconnect"},
                )
                if type(payload["confirm_disconnect"]) is not bool or not payload["confirm_disconnect"]:
                    raise NetworkValidationError("confirm_disconnect must be true")
                # Reject malformed credentials before returning 202. Besides giving
                # the browser a useful immediate error, this guarantees a bad value
                # never reaches the privileged worker thread or nmcli.
                ssid, passphrase, hidden = validate_credentials(
                    payload["ssid"], payload["passphrase"], payload["hidden"]
                )
                if scope == "setup" and self.controller.status()["mode"] != "setup_ap":
                    raise PermissionError("setup credential is valid only while setup AP is active")
                result = self._schedule_job(
                    "provision",
                    {
                        "ssid": ssid,
                        "passphrase": passphrase,
                        "hidden": hidden,
                    },
                )
            elif operation == "job":
                self._require_scope(token, {"admin", "setup"})
                self._validate_fields(payload, required={"job_id"})
                result = self._get_job(payload["job_id"])
            elif operation == "force_ap":
                self._require_scope(token, {"admin"})
                self._validate_fields(payload, required={"confirm_disconnect"})
                if payload["confirm_disconnect"] is not True:
                    raise NetworkValidationError("confirm_disconnect must be true")
                result = self._schedule_job("force_ap", {})
            elif operation == "local_force_ap":
                # This operation deliberately has no bearer-token equivalent.  Linux
                # supplies the Unix peer UID; only the root GPIO watcher (or a local
                # root operator) can reach this path.  HTTP proxy requests originate
                # from the unprivileged service account and therefore cannot spoof it.
                if peer_uid != 0:
                    raise PermissionError("physical recovery requires a local root peer")
                self._validate_fields(payload, required=set())
                result = self._schedule_job("force_ap", {})
            elif operation == "retry_known":
                self._require_scope(token, {"admin", "setup"})
                self._validate_fields(payload, required=set())
                result = self._schedule_job("retry_known", {})
            elif operation == "forget":
                self._require_scope(token, {"admin"})
                self._validate_fields(payload, required={"profile_id", "confirm"})
                if payload["confirm"] is not True or type(payload["profile_id"]) is not str:
                    raise NetworkValidationError("profile_id and confirm=true are required")
                self.controller.forget_profile(payload["profile_id"])
                result = {"forgotten": True}
            else:
                return self._error(404, "unknown_operation", "unknown network operation")
            return {"ok": True, "result": result}
        except PermissionError as exc:
            return self._error(403, "forbidden", str(exc))
        except NetworkValidationError as exc:
            return self._error(400, "invalid_network_request", str(exc))
        except NetworkOperationError as exc:
            return self._error(503, exc.code, str(exc))
        except RuntimeError as exc:
            return self._error(409, "transition_in_progress", str(exc))

    def _require_scope(self, token: Any, allowed: set[str]) -> str:
        if type(token) is not str:
            raise PermissionError("missing network authorization")
        if hmac.compare_digest(token, self._admin_token):
            scope = "admin"
        elif hmac.compare_digest(token, self._setup_token):
            scope = "setup"
        else:
            raise PermissionError("invalid network authorization")
        if scope not in allowed:
            raise PermissionError("credential is not authorized for this operation")
        return scope

    @staticmethod
    def _validate_fields(payload: dict[str, Any], *, required: set[str]) -> None:
        supplied = set(payload)
        if supplied != required:
            missing = sorted(required - supplied)
            unknown = sorted(supplied - required)
            parts = []
            if missing:
                parts.append(f"missing fields: {', '.join(missing)}")
            if unknown:
                parts.append(f"unknown fields: {', '.join(unknown)}")
            raise NetworkValidationError("; ".join(parts))

    def _schedule_job(self, operation: str, values: dict[str, Any]) -> dict[str, Any]:
        with self._jobs_lock:
            if self._active_job is not None:
                active = self._jobs.get(self._active_job)
                if active and active["state"] in {"scheduled", "running"}:
                    raise RuntimeError("another network transition is already in progress")

            job_id = secrets.token_hex(6)
            # Never place a passphrase in the persisted/status job record. The worker
            # closure releases its private values after controller.provision returns.
            public_job = {
                "job_id": job_id,
                "operation": operation,
                "state": "scheduled",
                "ssid": values.get("ssid"),
                "error_code": None,
                "message": "Network transition scheduled; this connection may close.",
            }
            self._jobs[job_id] = public_job
            self._active_job = job_id
            while len(self._jobs) > MAX_JOBS:
                self._jobs.popitem(last=False)

        private_values = dict(values)
        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, operation, private_values),
            name=f"network-job-{job_id}",
            daemon=True,
        )
        thread.start()
        return dict(public_job)

    def _run_job(self, job_id: str, operation: str, values: dict[str, Any]) -> None:
        time.sleep(self._transition_delay)
        self._update_job(job_id, state="running", message="Network transition is in progress.")
        try:
            if operation == "provision":
                self.controller.provision(job_id=job_id, **values)
                message = "Connected. Rejoin the home network and open the device address."
            elif operation == "force_ap":
                self.controller.start_ap()
                message = "Setup access point is ready."
            elif operation == "retry_known":
                if not self.controller.try_known_once():
                    self.controller.start_ap()
                    raise NetworkOperationError(
                        "known_network_unavailable",
                        "No saved network connected; setup access point was restored",
                    )
                message = "Connected to a saved network."
            else:
                raise RuntimeError("unsupported network job")
            self._update_job(job_id, state="connected", message=message)
        except NetworkOperationError as exc:
            LOGGER.warning(
                "network job failed job_id=%s operation=%s code=%s",
                job_id,
                operation,
                exc.code,
            )
            self._update_job(
                job_id,
                state="returned_to_setup_ap" if self.controller.status()["mode"] == "setup_ap" else "failed",
                error_code=exc.code,
                message=str(exc),
            )
        except Exception:
            # The traceback contains code paths but not the cleared public job data;
            # values themselves are never interpolated into this log message.
            LOGGER.exception(
                "unexpected network job failure job_id=%s operation=%s",
                job_id,
                operation,
            )
            self._update_job(
                job_id,
                state="failed",
                error_code="internal_error",
                message="Unexpected network transition failure",
            )
        finally:
            values.clear()
            with self._jobs_lock:
                if self._active_job == job_id:
                    self._active_job = None

    def _update_job(self, job_id: str, **changes: Any) -> None:
        with self._jobs_lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(changes)

    def _get_job(self, job_id: Any) -> dict[str, Any]:
        if type(job_id) is not str:
            raise NetworkValidationError("job_id must be a string")
        with self._jobs_lock:
            try:
                return dict(self._jobs[job_id])
            except KeyError as exc:
                raise NetworkValidationError("unknown or expired network job") from exc

    @staticmethod
    def _error(status: int, code: str, message: str) -> dict[str, Any]:
        return {"ok": False, "status": status, "code": code, "error": message[:300]}


class NetworkRequestHandler(socketserver.StreamRequestHandler):
    """Read exactly one bounded JSON line and write exactly one response."""

    daemon: NetworkDaemon

    def _peer_uid(self) -> int | None:
        """Return Linux SO_PEERCRED UID, failing closed on unsupported platforms."""

        option = getattr(socket, "SO_PEERCRED", None)
        if option is None:
            return None
        try:
            credentials = self.request.getsockopt(
                socket.SOL_SOCKET,
                option,
                struct.calcsize("3i"),
            )
            _pid, uid, _gid = struct.unpack("3i", credentials)
            return uid
        except (OSError, struct.error):
            return None

    def handle(self) -> None:
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            response = NetworkDaemon._error(413, "request_too_large", "request is too large")
        else:
            try:
                request = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                response = NetworkDaemon._error(400, "invalid_json", "request must contain UTF-8 JSON")
            else:
                response = self.server.daemon.dispatch(  # type: ignore[attr-defined]
                    request,
                    peer_uid=self._peer_uid(),
                )
        self.wfile.write(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")


class NetworkUnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, socket_path: str, daemon: NetworkDaemon):
        self.daemon = daemon
        super().__init__(socket_path, NetworkRequestHandler)


def _read_secret(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"secret file is empty: {path}")
    return value


def _ensure_setup_secret(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        value = secrets.token_urlsafe(15)[:20]
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value + "\n")
    os.chmod(path, 0o600)
    return _read_secret(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Privileged RGB matrix network controller")
    parser.add_argument("--interface", default="wlan0")
    parser.add_argument("--socket", default="/run/rgb-matrix-network/control.sock")
    parser.add_argument("--admin-token-file", required=True)
    parser.add_argument("--state-directory", default="/var/lib/rgb-matrix-network")
    parser.add_argument("--boot-grace", type=float, default=60.0)
    parser.add_argument("--loss-grace", type=float, default=120.0)
    parser.add_argument("--monitor-interval", type=float, default=5.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if os.geteuid() != 0:
        raise SystemExit("network controller must run as root")
    if min(args.boot_grace, args.loss_grace, args.monitor_interval) <= 0:
        raise SystemExit("network timing values must be positive")

    state_directory = Path(args.state_directory)
    setup_secret_path = state_directory / "setup-passphrase"
    try:
        setup_token = _ensure_setup_secret(setup_secret_path)
        admin_token = _read_secret(Path(args.admin_token_file))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"could not initialize network secrets: {exc}") from exc

    runner = NmcliRunner()
    try:
        secret_store = NetworkManagerDbusSecrets()
    except NetworkOperationError as exc:
        raise SystemExit(f"could not initialize NetworkManager secret storage: {exc}") from exc
    backend = NetworkManagerBackend(
        runner,
        interface=args.interface,
        secret_store=secret_store,
    )
    suffix = backend.read_interface_suffix(args.interface)
    ap_ssid = f"RGB-Matrix-{suffix}"
    policy = NetworkPolicy(
        boot_grace_seconds=args.boot_grace,
        loss_grace_seconds=args.loss_grace,
        monitor_interval_seconds=args.monitor_interval,
    )
    try:
        ap_uuid = backend.ensure_ap_profile(
            ssid=ap_ssid,
            passphrase=setup_token,
            address=policy.ap_address,
        )
    except NetworkOperationError as exc:
        raise SystemExit(f"could not prepare setup access point: {exc}") from exc

    controller = NetworkController(
        backend,
        ap_uuid=ap_uuid,
        ap_ssid=ap_ssid,
        policy=policy,
    )
    # Initial tick records an already-connected LAN without waiting for the monitor.
    controller.tick()
    daemon = NetworkDaemon(controller, admin_token=admin_token, setup_token=setup_token)

    socket_path = Path(args.socket)
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()
    server = NetworkUnixServer(str(socket_path), daemon)
    os.chmod(socket_path, 0o660)

    stop_event = threading.Event()
    monitor = threading.Thread(
        target=controller.monitor,
        args=(stop_event,),
        name="network-monitor",
        daemon=True,
    )
    monitor.start()

    def request_shutdown(_signum, _frame) -> None:
        stop_event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    try:
        print(f"RGB matrix network controller listening on {socket_path}")
        server.serve_forever(poll_interval=0.25)
    finally:
        stop_event.set()
        server.server_close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
