"""Pure formatting helpers shared by the boot display and hardware-free tests."""

from __future__ import annotations

from typing import Any


def connection_details(
    status: dict[str, Any],
    code: str,
    port: int = 8080,
) -> tuple[tuple[str, str], ...]:
    """Create the three user-facing pages from public network-helper status."""

    ap = status.get("ap") if isinstance(status.get("ap"), dict) else {}
    ssid = ap.get("ssid") if ap.get("active") else status.get("ssid")
    address = ap.get("address") if ap.get("active") else status.get("ipv4")
    return (
        ("CONNECT WIFI", str(ssid or "WAITING...")),
        # Modern phone browsers accept a bare IPv4 address and port in the address
        # bar. Omitting the scheme keeps the limited-width matrix line shorter while
        # leaving the actual HTTP service and its URLs unchanged.
        ("OPEN URL", f"{address}:{port}" if address else "WAITING..."),
        ("PAIR CODE", code),
    )


def text_positions(
    text: str,
    *,
    glyph_width: int,
    viewport_width: int,
    elapsed: float,
    pixels_per_second: float = 14.0,
    gap: int = 8,
) -> tuple[int, ...]:
    """Return centered or continuously wrapping x positions for one text line.

    Fixed-width BDF fonts make text width predictable without a live matrix canvas.
    Long values are drawn twice with a gap, producing a continuous marquee while
    short values remain centered and completely still.
    """

    text_width = len(text) * glyph_width
    if text_width <= viewport_width:
        return ((viewport_width - text_width) // 2,)
    span = text_width + gap
    offset = int(max(0.0, elapsed) * pixels_per_second) % span
    return (-offset, span - offset)
