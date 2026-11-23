"""Compiled output plans for the FFT visualizer's step-6 comparison.

The direct native-canvas renderer remains the default path.  This module contains
only the deliberately narrow Pillow candidate used to compare a complete one-buffer
bulk path under the accepted qualification configuration.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np  # type: ignore


DIRECT_OUTPUT_PATH = "direct"
PILLOW_BULK_OUTPUT_PATH = "pillow-bulk"
OUTPUT_PATH_ENVIRONMENT = "RGBMATRIX_FFT_OUTPUT_PATH"
_BLACK = (0, 0, 0)


class PillowBulkBarsOutputPlan:
    """One reusable Pillow RGB frame for uniform ocean overflow bars.

    Instances are compiled at startup for the exact 64x64 qualification path.  The
    Pillow image and every possible one-column geometry box are allocated once and
    retain stable identities.  Steady-state drawing clears and updates that image in
    place before one ``SetImage`` call.
    """

    __slots__ = (
        "_full_frame_box",
        "_geometry_boxes",
        "_height",
        "_image",
        "_overflow_multiplier",
        "_overflow_colors",
        "_width",
    )

    def __init__(
        self,
        width: int,
        height: int,
        image: Any,
        overflow_multiplier: float,
        overflow_colors: tuple[tuple[tuple[int, int, int], ...], ...],
    ) -> None:
        self._width = width
        self._height = height
        self._image = image
        self._full_frame_box = (0, 0, width, height)
        self._overflow_multiplier = overflow_multiplier
        self._overflow_colors = overflow_colors
        self._geometry_boxes = tuple(
            tuple(
                (column, height - visible, column + 1, height)
                for visible in range(height + 1)
            )
            for column in range(width)
        )

    @property
    def image(self) -> Any:
        """Return the one reusable Pillow image used as the candidate frame."""

        return self._image

    @property
    def geometry_boxes(self) -> tuple[tuple[tuple[int, int, int, int], ...], ...]:
        """Return the startup-compiled one-column boxes indexed by visible height."""

        return self._geometry_boxes

    @property
    def overflow_colors(self) -> tuple[tuple[tuple[int, int, int], ...], ...]:
        """Return the startup-compiled ocean colors by layer class and position."""

        return self._overflow_colors

    def begin_frame(self) -> None:
        """Clear the one reusable image before ordered layer painting."""

        self._image.paste(_BLACK, self._full_frame_box)

    def paint_bars(
        self,
        smoothed_bars: np.ndarray,
        peak_heights: Optional[np.ndarray] = None,
        overflow_multiplier: Optional[float] = None,
    ) -> None:
        """Paint one qualified bars layer without clearing or uploading.

        ``peak_heights`` is intentionally unused because the candidate compiler
        rejects peak mode.  The argument keeps the same call shape as the accepted
        direct renderer without introducing an adapter or per-frame message.
        ``overflow_multiplier`` lets each ordered layer paint with its own
        compile-time multiplier; ``None`` uses the plan's compiled value.
        """

        del peak_heights
        image = self._image

        height = self._height
        width = self._width
        if overflow_multiplier is None:
            overflow_multiplier = self._overflow_multiplier
        geometry_boxes = self._geometry_boxes
        overflow_colors = self._overflow_colors

        for column in range(width):
            raw_ratio = smoothed_bars[column]
            if np.isnan(raw_ratio):
                raw_ratio = 0.0

            total_pixels = int(raw_ratio * height * overflow_multiplier)
            if total_pixels <= 0:
                continue

            visible_pixels = min(total_pixels, height)
            top_layer = (total_pixels - 1) // height
            top_position_in_layer = (total_pixels - 1) % height
            color = overflow_colors[min(top_layer, 2)][top_position_in_layer]
            image.paste(color, geometry_boxes[column][visible_pixels])

    def end_frame(self, canvas: Any) -> None:
        """Upload the completed shared image exactly once."""

        try:
            canvas.SetImage(self._image)
        except Exception as exc:
            # No fallback or retry is allowed here: either would mix output paths and
            # invalidate the comparison.  Propagation follows the existing bounded
            # application cleanup/service-restart path.
            raise RuntimeError("bulk output upload failed") from exc

    def draw(
        self,
        canvas: Any,
        smoothed_bars: np.ndarray,
        peak_heights: Optional[np.ndarray] = None,
    ) -> None:
        """Compatibility wrapper for the qualified one-layer path."""

        self.begin_frame()
        self.paint_bars(smoothed_bars, peak_heights)
        self.end_frame(canvas)


def compile_pillow_bulk_bars_output(
    visualizer: Any,
    width: int,
    height: int,
) -> PillowBulkBarsOutputPlan:
    """Compile the one-buffer candidate for the exact qualification configuration."""

    if width != 64 or height != 64:
        raise ValueError("pillow-bulk output requires the 64x64 qualification geometry")
    if visualizer.width != width or visualizer.height != height:
        raise ValueError("visualizer and output geometry must match")
    if visualizer.theme is None or getattr(visualizer.theme, "name", None) != "ocean":
        raise ValueError("pillow-bulk output requires the ocean qualification theme")
    if visualizer.gradient_mode:
        raise ValueError("pillow-bulk output does not support gradient mode")
    if visualizer.settings.shadow.enabled:
        raise ValueError("pillow-bulk output does not support shadow mode")
    if visualizer.settings.peak.enabled:
        raise ValueError("pillow-bulk output does not support peak mode")
    if visualizer.full_mode:
        raise ValueError("pillow-bulk output does not support full mode")
    if not visualizer.overflow_mode:
        raise ValueError("pillow-bulk output requires overflow mode")
    if not visualizer.bars_enabled:
        raise ValueError("pillow-bulk output requires bars to be enabled")

    try:
        from PIL import Image
    except ModuleNotFoundError as exc:
        raise RuntimeError("pillow-bulk output requires Pillow") from exc

    theme = visualizer.theme
    overflow_colors = tuple(
        tuple(
            theme.get_overflow_color(
                layer,
                position / height,
                0.0,
                0,
                0.0,
            )
            for position in range(height)
        )
        for layer in range(3)
    )
    image = Image.new("RGB", (width, height))
    return PillowBulkBarsOutputPlan(
        width=width,
        height=height,
        image=image,
        overflow_multiplier=visualizer.settings.overflow.multiplier,
        overflow_colors=overflow_colors,
    )


__all__ = [
    "DIRECT_OUTPUT_PATH",
    "OUTPUT_PATH_ENVIRONMENT",
    "PILLOW_BULK_OUTPUT_PATH",
    "PillowBulkBarsOutputPlan",
    "compile_pillow_bulk_bars_output",
]
