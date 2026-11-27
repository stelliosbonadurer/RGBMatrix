"""Always-on frame counters for the runtime ``stats`` operation.

fft-live-fps D-002/D-007/D-008. The render thread calls ``record_swap()`` once per
swapped frame; the runtime listener thread calls ``sample()``. The counters use the
same meanings as ``core.telemetry``'s ``rates``:

- ``swap_frames``: frames swapped to the panel;
- ``unique_content_frames``: swapped frames whose analysis sequence differs from the
  previous swapped frame's.

There is no lock. The render thread is the only writer of every field except
``_requested_window``, which only ``sample()`` writes. Each is a single attribute
store, so a reader sees either the old or the new value, and fields may be skewed by
at most one frame relative to each other.

The slowest swap-to-swap interval is tracked per *window*. ``sample()`` asks for a
new window; the render thread notices at its next swap, publishes the finished
window's maximum as one ``(window, slowest_ns)`` tuple, and starts the next window.
A sample therefore reports the window that the previous sample closed, about one
sample period late, and no interval is ever lost or counted twice.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional


class FrameRateCounter:
    """Count swapped and fresh frames and the slowest interval per window."""

    __slots__ = (
        "_clock",
        "swap_frames",
        "unique_content_frames",
        "last_swap_ns",
        "_last_sequence",
        "_window",
        "_window_slowest_ns",
        "_requested_window",
        "_closed_window",
    )

    def __init__(self, clock: Callable[[], int] = time.perf_counter_ns) -> None:
        self._clock = clock
        self.swap_frames = 0
        self.unique_content_frames = 0
        self.last_swap_ns: Optional[int] = None
        self._last_sequence: Optional[int] = None
        # The window the render thread is filling, and its slowest interval so far
        # (0 means no interval has ended in it yet).
        self._window = 0
        self._window_slowest_ns = 0
        # Written only by sample(): the window the next swap should start.
        self._requested_window = 0
        # The last finished window and its slowest interval (None when no interval
        # ended in it). Replaced as one tuple so readers never see a mixed pair.
        self._closed_window: tuple[int, Optional[int]] = (-1, None)

    def record_swap(self, analysis_sequence: int) -> None:
        """Render thread: account for one frame that was just swapped."""

        now = self._clock()
        self.swap_frames += 1
        if analysis_sequence != self._last_sequence:
            self.unique_content_frames += 1
            self._last_sequence = analysis_sequence
        last = self.last_swap_ns
        self.last_swap_ns = now
        requested = self._requested_window
        if requested != self._window:
            # Close the finished window before this interval is counted, so the
            # interval that ends now belongs to the new window.
            slowest = self._window_slowest_ns
            self._closed_window = (self._window, slowest if slowest > 0 else None)
            self._window = requested
            self._window_slowest_ns = 0
        if last is not None:
            interval = now - last
            if interval > self._window_slowest_ns:
                self._window_slowest_ns = interval

    def sample(self) -> dict[str, Any]:
        """Listener thread: read the counters and request a new slowest window."""

        closed_window, slowest_ns = self._closed_window
        result = {
            "monotonic_ns": self._clock(),
            "swap_frames": self.swap_frames,
            "unique_content_frames": self.unique_content_frames,
            "last_swap_ns": self.last_swap_ns,
            "slowest_frame_window": closed_window if closed_window >= 0 else None,
            "slowest_frame_interval_ns": slowest_ns,
        }
        self._requested_window = self._requested_window + 1
        return result


__all__ = ["FrameRateCounter"]
