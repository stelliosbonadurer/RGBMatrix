"""Bounded, shell-free execution and parsing for NetworkManager's nmcli tool."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable, Sequence

from .models import NetworkOperationError


MAX_CAPTURED_OUTPUT = 16 * 1024


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


RunFunction = Callable[..., subprocess.CompletedProcess[str]]


class NmcliRunner:
    """Execute fixed nmcli argument vectors without leaking credential arguments.

    Callers mark credential-bearing operations ``sensitive=True``. Their stderr is
    intentionally discarded from exceptions because some tool versions echo values
    while explaining a malformed property. The runner never logs or formats argv.
    """

    def __init__(self, executable: str = "/usr/bin/nmcli", run: RunFunction = subprocess.run):
        self.executable = executable
        self._run = run

    def execute(
        self,
        arguments: Sequence[str],
        *,
        timeout: float = 20.0,
        sensitive: bool = False,
    ) -> CommandResult:
        try:
            completed = self._run(
                [self.executable, *arguments],
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise NetworkOperationError("nm_timeout", "NetworkManager operation timed out") from exc
        except OSError as exc:
            raise NetworkOperationError("nm_unavailable", "NetworkManager command is unavailable") from exc

        stdout = (completed.stdout or "")[:MAX_CAPTURED_OUTPUT]
        stderr = (completed.stderr or "")[:MAX_CAPTURED_OUTPUT]
        result = CommandResult(completed.returncode, stdout, stderr)
        if completed.returncode != 0:
            if sensitive:
                message = "NetworkManager rejected the credential operation"
            else:
                detail = " ".join(stderr.strip().split())[:300]
                message = detail or "NetworkManager operation failed"
            raise NetworkOperationError("nm_failed", message)
        return result


def split_terse_line(line: str) -> list[str]:
    """Split nmcli ``--terse --escape yes`` output without losing escaped colons."""

    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for character in line.rstrip("\n"):
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def parse_property_lines(output: str) -> dict[str, str]:
    """Parse ``KEY:value`` device output, retaining only the first value per key."""

    properties: dict[str, str] = {}
    for raw_line in output.splitlines():
        fields = split_terse_line(raw_line)
        if len(fields) < 2:
            continue
        properties.setdefault(fields[0], ":".join(fields[1:]))
    return properties

