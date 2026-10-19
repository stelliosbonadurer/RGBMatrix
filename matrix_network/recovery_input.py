"""Monitor a local GPIO button and request the protected setup access point.

The default wiring is a normally-open momentary button between BCM GPIO 19
(physical pin 35) and ground (physical pin 39).  GPIO 19 is unused by the project's
``adafruit-hat`` matrix mapping.  A pull-up and a long hold prevent a floating input
or ordinary contact bounce from disrupting the active Wi-Fi connection.
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Iterable, TextIO


LOGGER = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 64 * 1024


@dataclass
class LongPressDetector:
    """Recognize one press/release pair using kernel event timestamps."""

    hold_seconds: float
    pressed_at: float | None = None

    def process(self, edge: int, timestamp: float) -> bool:
        if edge == 0:  # pull-up input transitions low when the button is pressed
            if self.pressed_at is None:
                self.pressed_at = timestamp
            return False
        if edge == 1 and self.pressed_at is not None:
            duration = max(0.0, timestamp - self.pressed_at)
            self.pressed_at = None
            return duration >= self.hold_seconds
        return False


def parse_event(line: str) -> tuple[int, float] | None:
    """Parse the stable custom format emitted by libgpiod 1.x ``gpiomon``."""

    fields = line.split()
    if len(fields) != 3:
        return None
    try:
        edge, seconds, nanoseconds = map(int, fields)
    except ValueError:
        return None
    if edge not in {0, 1} or seconds < 0 or not (0 <= nanoseconds < 1_000_000_000):
        return None
    return edge, seconds + nanoseconds / 1_000_000_000


def request_local_recovery(
    socket_path: str,
    *,
    timeout: float = 5.0,
    attempts: int = 5,
    retry_delay: float = 1.0,
) -> None:
    """Ask the root helper to enter AP mode through its root-peer-only operation.

    The units start in order, but a normal systemd service has no socket-readiness
    notification. A bounded retry makes a hold during that short startup window
    reliable without creating an unbounded retry loop.
    """

    request = {
        "operation": "local_force_ap",
        "payload": {},
    }
    if attempts <= 0:
        raise ValueError("attempts must be positive")
    response = b""
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(timeout)
                client.connect(socket_path)
                client.sendall(
                    json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
                )
                response = client.makefile("rb").readline(MAX_RESPONSE_BYTES + 1)
            break
        except OSError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(retry_delay)
    else:
        raise RuntimeError("network helper is unavailable") from last_error
    if len(response) > MAX_RESPONSE_BYTES:
        raise RuntimeError("network helper returned an oversized response")
    try:
        payload = json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("network helper returned an invalid response") from exc
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error", "network helper rejected recovery"))[:300])


def monitor_events(
    lines: Iterable[str],
    *,
    detector: LongPressDetector,
    socket_path: str,
) -> None:
    """Consume GPIO events until the source exits; malformed lines are ignored."""

    for line in lines:
        event = parse_event(line)
        if event is None:
            continue
        if detector.process(*event):
            LOGGER.warning("physical recovery hold accepted; requesting setup access point")
            request_local_recovery(socket_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RGB matrix physical network recovery input")
    parser.add_argument("--socket", default="/run/rgb-matrix-network/control.sock")
    parser.add_argument("--gpiomon", default="/usr/bin/gpiomon")
    parser.add_argument("--chip", default="gpiochip0")
    parser.add_argument("--line", type=int, default=19, help="BCM GPIO line offset")
    parser.add_argument("--hold-seconds", type=float, default=5.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.line < 0 or args.hold_seconds <= 0:
        raise SystemExit("GPIO line must be non-negative and hold time must be positive")

    # A fixed output format avoids parsing locale-dependent human-readable text.
    # Pull-up bias means the only required hardware is a button to ground.
    command = [
        args.gpiomon,
        "--bias=pull-up",
        "--line-buffered",
        "--format=%e %s %n",
        args.chip,
        str(args.line),
    ]
    try:
        with subprocess.Popen(
            command,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ) as process:
            if process.stdout is None:  # pragma: no cover - defensive subprocess API guard
                raise RuntimeError("gpiomon stdout pipe was not created")
            monitor_events(
                process.stdout,
                detector=LongPressDetector(args.hold_seconds),
                socket_path=args.socket,
            )
            return_code = process.wait()
    except OSError as exc:
        raise SystemExit("could not start the GPIO event monitor") from exc
    if return_code != 0:
        raise SystemExit("GPIO event monitor exited unexpectedly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
