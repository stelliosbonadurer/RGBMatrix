"""Process-policy tests using fake children instead of matrix hardware."""

from __future__ import annotations

import signal
import subprocess
import unittest
from pathlib import Path
from typing import Any

from matrix_controller.registry import build_default_registry
from matrix_controller.schema import ValidationError
from matrix_controller.supervisor import VisualizationSupervisor


class FakeProcess:
    def __init__(self, pid: int):
        self.pid = pid
        self.returncode: int | None = None
        self.wait_calls: list[float | None] = []
        self.timeout_once = False
        self.timeout_always = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        if (self.timeout_once or self.timeout_always) and self.returncode is None:
            self.timeout_once = False
            raise subprocess.TimeoutExpired("fake", timeout)
        if self.returncode is None:
            raise AssertionError("test signaler did not stop fake process")
        return self.returncode


class FakeProcessFactory:
    def __init__(self):
        self.processes: list[FakeProcess] = []
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> FakeProcess:
        process = FakeProcess(1000 + len(self.processes))
        self.processes.append(process)
        self.calls.append((list(argv), kwargs))
        return process


class SupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = FakeProcessFactory()
        self.signals: list[tuple[int, int]] = []

        def signal_group(group_id: int, signum: int) -> None:
            self.signals.append((group_id, signum))
            process = next(item for item in self.factory.processes if item.pid == group_id)
            process.returncode = 0 if signum == signal.SIGTERM else -signum

        self.supervisor = VisualizationSupervisor(
            build_default_registry(),
            python_executable="/fixed/python",
            command_prefix=("sudo", "-n", "-E"),
            process_factory=self.factory,
            group_signaler=signal_group,
            process_group_lookup=lambda pid: pid,
            clock=lambda: 123.5,
        )

    def test_start_passes_vector_and_service_safety_options(self) -> None:
        result = self.supervisor.start("fft", {"theme": "warm"})
        argv, kwargs = self.factory.calls[0]
        self.assertEqual(argv[:4], ["sudo", "-n", "-E", "/fixed/python"])
        self.assertEqual(result.parameters["theme"], "warm")
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(self.supervisor.status()["state"], "running")

    def test_switch_stops_and_reaps_old_child_before_new_start(self) -> None:
        first = self.supervisor.start("fft")
        second = self.supervisor.start("sorts", {"mode": "single"})
        self.assertEqual(self.signals, [(first.pid, signal.SIGTERM)])
        self.assertEqual(len(self.factory.processes), 2)
        self.assertEqual(self.factory.processes[0].wait_calls, [5.0])
        self.assertEqual(second.run_id, first.run_id + 1)
        self.assertEqual(self.supervisor.status()["visualization_id"], "sorts")

    def test_invalid_switch_keeps_current_child_running(self) -> None:
        running = self.supervisor.start("fft")
        with self.assertRaises(ValidationError):
            self.supervisor.start("sorts", {"mode": "../../bin/sh"})
        status = self.supervisor.status()
        self.assertEqual(status["pid"], running.pid)
        self.assertEqual(self.signals, [])

    def test_stop_is_idempotent(self) -> None:
        running = self.supervisor.start("fft")
        first_status = self.supervisor.stop()
        second_status = self.supervisor.stop()
        self.assertEqual(self.signals, [(running.pid, signal.SIGTERM)])
        self.assertEqual(first_status["state"], "idle")
        self.assertEqual(second_status["state"], "idle")

    def test_timeout_escalates_to_sigkill(self) -> None:
        running = self.supervisor.start("fft")
        process = self.factory.processes[0]
        process.timeout_once = True

        def ignore_term(group_id: int, signum: int) -> None:
            self.signals.append((group_id, signum))
            if signum == signal.SIGKILL:
                process.returncode = -signal.SIGKILL

        self.supervisor._group_signaler = ignore_term
        self.supervisor.stop()
        self.assertEqual(
            self.signals,
            [(running.pid, signal.SIGTERM), (running.pid, signal.SIGKILL)],
        )

    def test_natural_failure_is_reconciled_and_bounded(self) -> None:
        self.supervisor.start("fft")
        self.factory.processes[0].returncode = 7
        status = self.supervisor.status()
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["last_exit_code"], 7)
        self.assertLessEqual(len(status["last_error"]), 500)

    def test_unreaped_child_is_retained_as_owner(self) -> None:
        running = self.supervisor.start("fft")
        process = self.factory.processes[0]
        process.timeout_always = True

        def ignore_all_signals(group_id: int, signum: int) -> None:
            self.signals.append((group_id, signum))

        self.supervisor._group_signaler = ignore_all_signals
        with self.assertRaisesRegex(RuntimeError, "did not exit"):
            self.supervisor.stop()

        status = self.supervisor.status()
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["pid"], running.pid)


if __name__ == "__main__":
    unittest.main()
