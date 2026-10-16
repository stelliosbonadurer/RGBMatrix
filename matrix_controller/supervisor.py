"""Exclusive subprocess ownership for RGB matrix visualizations."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from .registry import Registry
from .schema import ValidationError


class ManagedProcess(Protocol):
    """Small Popen surface used by the supervisor and injected test doubles."""

    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...


ProcessFactory = Callable[..., ManagedProcess]
GroupSignaler = Callable[[int, int], None]
ProcessGroupLookup = Callable[[int], int]
Clock = Callable[[], float]


@dataclass(frozen=True)
class LaunchResult:
    """Normalized values and argv for a successfully launched child."""

    visualization_id: str
    parameters: dict[str, Any]
    argv: tuple[str, ...]
    pid: int
    run_id: int


class VisualizationSupervisor:
    """Serialize lifecycle operations and guarantee one matrix owner at a time.

    The class contains no HTTP or hardware imports. This separation makes process
    policy testable with fake children and ensures request parsing can never bypass
    the registry's allowlist. ``RLock`` is used because ``start`` performs a complete
    stop while holding the same ownership lock; no second request can start another
    child in the gap between those operations.
    """

    VALID_STATES = {"idle", "starting", "running", "stopping", "failed"}

    def __init__(
        self,
        registry: Registry,
        *,
        python_executable: str = sys.executable,
        command_prefix: tuple[str, ...] = (),
        stop_timeout: float = 5.0,
        process_factory: ProcessFactory = subprocess.Popen,
        group_signaler: GroupSignaler = os.killpg,
        process_group_lookup: ProcessGroupLookup = os.getpgid,
        clock: Clock = time.time,
    ):
        self.registry = registry
        self.python_executable = python_executable
        self.command_prefix = tuple(command_prefix)
        self.stop_timeout = stop_timeout
        self._process_factory = process_factory
        self._group_signaler = group_signaler
        self._process_group_lookup = process_group_lookup
        self._clock = clock
        self._lock = threading.RLock()

        self._state = "idle"
        self._process: ManagedProcess | None = None
        self._visualization_id: str | None = None
        self._parameters: dict[str, Any] = {}
        self._argv: tuple[str, ...] = ()
        self._started_at: float | None = None
        self._last_exit_code: int | None = None
        self._last_error: str | None = None
        self._run_id = 0

    def list_visualizations(self) -> list[dict[str, Any]]:
        return [item.as_public_dict() for item in self.registry.list()]

    def start(
        self,
        visualization_id: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> LaunchResult:
        """Stop the current child, validate the new request, and launch it.

        Validation occurs before stopping the active visualization. A malformed
        switch request therefore cannot blank a working display. Once validation
        succeeds, teardown and launch remain inside one lock-protected transaction.
        """

        spec = self.registry.get(visualization_id)
        normalized = spec.validate_parameters(parameters)
        argv = spec.build_argv(
            self.python_executable,
            normalized,
            command_prefix=self.command_prefix,
        )

        if not spec.script_path.is_file():
            raise ValidationError(
                f"visualization program is not installed: {spec.script_path.name}"
            )

        with self._lock:
            self._reconcile_locked()
            self._stop_locked()
            self._state = "starting"
            self._last_error = None
            self._last_exit_code = None
            self._run_id += 1

            try:
                # stdin is closed so a missed non-interactive adapter fails instead of
                # hanging. Inheriting stdout/stderr lets systemd/journald apply its
                # existing bounded logging policy without an unread pipe deadlock.
                process = self._process_factory(
                    argv,
                    cwd=str(spec.working_directory),
                    stdin=subprocess.DEVNULL,
                    stdout=None,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except Exception as exc:
                self._state = "failed"
                self._last_error = self._bounded_error(f"launch failed: {exc}")
                raise RuntimeError(self._last_error) from exc

            self._process = process
            self._visualization_id = visualization_id
            self._parameters = dict(normalized)
            self._argv = tuple(argv)
            self._started_at = self._clock()

            immediate_exit = process.poll()
            if immediate_exit is not None:
                process.wait(timeout=0)
                self._last_exit_code = immediate_exit
                self._last_error = self._bounded_error(
                    f"process exited during startup with code {immediate_exit}"
                )
                self._process = None
                self._state = "failed"
                raise RuntimeError(self._last_error)

            self._state = "running"
            return LaunchResult(
                visualization_id=visualization_id,
                parameters=dict(normalized),
                argv=tuple(argv),
                pid=process.pid,
                run_id=self._run_id,
            )

    def stop(self) -> dict[str, Any]:
        """Idempotently stop and reap the active process group."""

        with self._lock:
            self._reconcile_locked()
            self._stop_locked()
            return self._status_locked()

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._reconcile_locked()
            return self._status_locked()

    def _stop_locked(self) -> None:
        process = self._process
        if process is None:
            # An explicit stop also acknowledges a previously observed failure. Keep
            # last_error/last_exit_code for diagnosis, but return lifecycle to idle.
            self._state = "idle"
            return

        self._state = "stopping"
        exit_code = process.poll()
        try:
            if exit_code is None:
                process_group = self._process_group_lookup(process.pid)
                self._group_signaler(process_group, signal.SIGTERM)
                try:
                    exit_code = process.wait(timeout=self.stop_timeout)
                except subprocess.TimeoutExpired:
                    # A process group can include helper children. Escalating the
                    # entire group prevents an orphan from continuing to own GPIO or
                    # audio after the supervisor believes the panel is free.
                    self._group_signaler(process_group, signal.SIGKILL)
                    exit_code = process.wait(timeout=self.stop_timeout)
            else:
                process.wait(timeout=0)
        except subprocess.TimeoutExpired as exc:
            # Do not forget a child that could not be reaped. Retaining ownership in
            # failed state prevents a subsequent start from creating two potential
            # matrix owners. A later stop/status call can reconcile if it finally
            # exits; systemd KillMode=control-group is the outer safety net.
            self._state = "failed"
            self._last_error = self._bounded_error(
                f"process {process.pid} did not exit after SIGKILL"
            )
            raise RuntimeError(self._last_error) from exc
        except ProcessLookupError:
            # The child disappeared between poll/getpgid/kill. poll() reaps it when
            # possible; the ownership record is then safe to clear.
            exit_code = process.poll()

        self._last_exit_code = exit_code
        self._process = None
        self._visualization_id = None
        self._parameters = {}
        self._argv = ()
        self._started_at = None
        self._state = "idle"

    def _reconcile_locked(self) -> None:
        """Notice a natural child exit before reporting or mutating state."""

        if self._process is None:
            return
        exit_code = self._process.poll()
        if exit_code is None:
            return

        self._last_exit_code = exit_code
        finished_id = self._visualization_id
        self._process = None
        self._visualization_id = None
        self._parameters = {}
        self._argv = ()
        self._started_at = None

        # A finite visualization exiting successfully is normal. Nonzero exit is kept
        # as a failed state so an operator can distinguish a crash from an explicit
        # stop without storing unbounded child output in memory.
        if exit_code == 0:
            self._state = "idle"
            self._last_error = None
        else:
            self._state = "failed"
            self._last_error = self._bounded_error(
                f"visualization {finished_id!r} exited with code {exit_code}"
            )

    def _status_locked(self) -> dict[str, Any]:
        process = self._process
        return {
            "state": self._state,
            "visualization_id": self._visualization_id,
            "parameters": dict(self._parameters),
            "pid": process.pid if process is not None else None,
            "started_at": self._started_at,
            "run_id": self._run_id,
            "last_exit_code": self._last_exit_code,
            "last_error": self._last_error,
        }

    @staticmethod
    def _bounded_error(message: str, limit: int = 500) -> str:
        return message if len(message) <= limit else f"{message[: limit - 3]}..."
