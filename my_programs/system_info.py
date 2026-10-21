#!/usr/bin/env python3
"""Boot information display for connecting to the RGB matrix controller.

This program deliberately runs through the same supervisor as FFT and sorting.
Starting another visualization therefore terminates this process before acquiring
the panel, avoiding a second GPIO owner or a separate display-coordination daemon.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_SAMPLE_CANDIDATES = (
    PROJECT_ROOT / "rpi-rgb-led-matrix" / "bindings" / "python" / "samples",
    Path.home() / "rpi-rgb-led-matrix" / "bindings" / "python" / "samples",
)
for import_root in (PROJECT_ROOT, *UPSTREAM_SAMPLE_CANDIDATES):
    import_root_string = str(import_root)
    if import_root_string not in sys.path:
        sys.path.insert(0, import_root_string)

from matrix_controller.boot_code import DEFAULT_CODE_FILE, read_boot_code
from matrix_controller.connection_info import connection_details, text_positions
from samplebase import SampleBase
# Load text rendering before RGBMatrix initialization. The native driver drops root
# privileges during initialization; this Pi's virtualenv lives below a mode-0700 home
# directory, which becomes untraversable to that reduced account afterward.
import rgbmatrix.graphics as graphics


FRAME_SECONDS = 0.05
STATUS_REFRESH_SECONDS = 3.0


class SystemInfoDisplay(SampleBase):
    """Show all connection values until the supervisor replaces this process."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stopping = False
        # SampleBase initializes GPIO and then deliberately drops root privileges.
        # Load protected state and font files while the sudo-launched process can
        # still traverse their parent directories; the in-memory objects remain safe
        # to use after the privilege drop.
        state_path = Path(os.environ.get("RGBMATRIX_BOOT_CODE_FILE", str(DEFAULT_CODE_FILE)))
        try:
            self._code = read_boot_code(state_path)
        except RuntimeError:
            self._code = "------"

        font_candidates = (
            PROJECT_ROOT / "rpi-rgb-led-matrix" / "fonts",
            Path.home() / "rpi-rgb-led-matrix" / "fonts",
        )
        font_directory = next(
            (candidate for candidate in font_candidates if (candidate / "4x6.bdf").is_file()),
            font_candidates[0],
        )
        self._info_font = graphics.Font()
        self._info_font.LoadFont(str(font_directory / "4x6.bdf"))
        self._code_font = graphics.Font()
        self._code_font.LoadFont(str(font_directory / "10x20.bdf"))
        self._ssid_color = graphics.Color(70, 200, 255)
        self._url_color = graphics.Color(255, 255, 255)
        self._code_color = graphics.Color(255, 190, 40)
        self._status_url = os.environ.get(
            "RGBMATRIX_CONTROL_STATUS_URL",
            "http://127.0.0.1:8080/api/v1/network/status",
        )

    def _network_status(self) -> dict:
        """Read public local status after the RGB driver drops privileges."""

        try:
            with urlopen(self._status_url, timeout=1.0) as response:
                payload = json.load(response)
            return payload if isinstance(payload, dict) else {}
        except (OSError, URLError, ValueError):
            # The child starts immediately before serve_forever(), so the first
            # request may race normal server startup. Each cycle retries.
            return {}

    def run(self):
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        previous_sigint = signal.getsignal(signal.SIGINT)

        def stop(_signum, _frame):
            self._stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        canvas = self.matrix.CreateFrameCanvas()
        status: dict = {}
        values = tuple(value for _label, value in connection_details(status, self._code))
        next_status_refresh = 0.0
        animation_started = time.monotonic()
        try:
            while not self._stopping:
                now = time.monotonic()
                if now >= next_status_refresh:
                    status = self._network_status()
                    values = tuple(
                        value for _label, value in connection_details(status, self._code)
                    )
                    next_status_refresh = now + STATUS_REFRESH_SECONDS

                ssid, url, code = values
                elapsed = now - animation_started
                canvas.Clear()

                # The first two values share one compact fixed-width font. Each line
                # scrolls independently only when it cannot fit the physical width.
                for value, baseline, color in (
                    (ssid, 8, self._ssid_color),
                    (url, 20, self._url_color),
                ):
                    for x in text_positions(
                        value,
                        glyph_width=4,
                        viewport_width=self.matrix.width,
                        elapsed=elapsed,
                    ):
                        graphics.DrawText(canvas, self._info_font, x, baseline, color, value)

                code_x = max(0, (self.matrix.width - len(code) * 10) // 2)
                graphics.DrawText(canvas, self._code_font, code_x, 54, self._code_color, code)
                canvas = self.matrix.SwapOnVSync(canvas)
                time.sleep(FRAME_SECONDS)
        finally:
            canvas.Clear()
            self.matrix.SwapOnVSync(canvas)
            signal.signal(signal.SIGTERM, previous_sigterm)
            signal.signal(signal.SIGINT, previous_sigint)
        return True


if __name__ == "__main__":
    display = SystemInfoDisplay()
    if not display.process():
        raise SystemExit(1)
