"""Render a Master Theorem recursion tree onto a canvas-shaped object."""

from __future__ import annotations

import math
from typing import Protocol

from .model import MasterTheoremAnalysis, RecurrenceLevel


Color = tuple[int, int, int]
HEADER_HEIGHT = 5
TREE_TOP = 6
LEVEL_HEIGHT = 6
FOOTER_HEIGHT = 4
MAX_INDIVIDUAL_NODES = 16


class Canvas(Protocol):
    width: int
    height: int

    def Clear(self) -> None: ...

    def SetPixel(self, x: int, y: int, red: int, green: int, blue: int) -> None: ...


CASE_COLORS: dict[int, Color] = {
    1: (240, 70, 70),      # Leaf-dominant growth.
    2: (70, 220, 110),     # Balanced work.
    3: (65, 155, 255),     # Root-dominant work.
}


FONT_3X5 = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    "A": ("010", "101", "111", "101", "101"),
    "B": ("110", "101", "110", "101", "110"),
    "C": ("111", "100", "100", "100", "111"),
    "K": ("101", "110", "100", "110", "101"),
    ".": ("000", "000", "000", "000", "010"),
    " ": ("000", "000", "000", "000", "000"),
}


class MasterTheoremRenderer:
    """Map exact recurrence levels to a bounded 64x64 visual explanation."""

    def __init__(self, width: int = 64, height: int = 64):
        if width < 16 or height < TREE_TOP + LEVEL_HEIGHT + FOOTER_HEIGHT:
            raise ValueError("canvas is too small for the Master Theorem layout")
        self.width = width
        self.height = height

    @property
    def maximum_visible_levels(self) -> int:
        return max(1, (self.height - TREE_TOP - FOOTER_HEIGHT) // LEVEL_HEIGHT)

    def visible_levels(self, analysis: MasterTheoremAnalysis) -> tuple[RecurrenceLevel, ...]:
        maximum = self.maximum_visible_levels
        if len(analysis.levels) <= maximum:
            return analysis.levels
        return (*analysis.levels[: maximum - 1], analysis.levels[-1])

    def render(
        self,
        canvas: Canvas,
        analysis: MasterTheoremAnalysis,
        *,
        reveal_levels: int | None = None,
        highlight_level: int | None = None,
        show_result: bool = False,
    ) -> None:
        canvas.Clear()
        color = CASE_COLORS[analysis.case]
        self._draw_header(canvas, analysis, color)

        visible = self.visible_levels(analysis)
        count = len(visible) if reveal_levels is None else max(0, min(reveal_levels, len(visible)))
        work_heights = self._work_heights(visible, analysis.spec.k)
        dominant = set(analysis.dominant_level_indices)

        for display_index, level in enumerate(visible[:count]):
            y = TREE_TOP + display_index * LEVEL_HEIGHT
            is_highlighted = highlight_level == display_index or (
                show_result and level.index in dominant
            )
            level_color = (255, 255, 255) if is_highlighted else self._level_color(
                color, level, visible
            )
            size_fill_ratio = min(
                1.0,
                (level.problem_size / analysis.spec.n) * level.node_count,
            )
            self._draw_level(
                canvas,
                level,
                y,
                work_heights[display_index],
                level_color,
                size_fill_ratio,
            )

        if show_result:
            self._draw_result_footer(canvas, analysis, color)

    def _draw_header(
        self,
        canvas: Canvas,
        analysis: MasterTheoremAnalysis,
        color: Color,
    ) -> None:
        k_text = _compact_number(analysis.spec.k)
        label = f"A{analysis.spec.a} B{analysis.spec.b} K{k_text}"
        self._draw_text(canvas, 0, 0, label, color)
        case_label = f"C{analysis.case}"
        case_x = self.width - (len(case_label) * 4 - 1)
        self._draw_text(canvas, max(0, case_x), 0, case_label, color)

    def _draw_level(
        self,
        canvas: Canvas,
        level: RecurrenceLevel,
        y: int,
        block_height: int,
        color: Color,
        size_fill_ratio: float,
    ) -> None:
        if y >= self.height - FOOTER_HEIGHT:
            return
        node_count = level.node_count
        if node_count <= MAX_INDIVIDUAL_NODES:
            for node_index in range(node_count):
                left = math.floor(node_index * self.width / node_count)
                right = math.floor((node_index + 1) * self.width / node_count) - 1
                center = (left + right) // 2
                self._pixel(canvas, center, y, _dim(color, 0.55))
                slot_width = right - left + 1
                block_width = max(1, round(slot_width * size_fill_ratio))
                if node_count > 1 and block_width > 1:
                    block_width -= 1
                block_left = max(left, center - (block_width - 1) // 2)
                block_right = min(right, block_left + block_width - 1)
                self._fill_rect(
                    canvas,
                    block_left,
                    y + LEVEL_HEIGHT - block_height,
                    block_right,
                    y + LEVEL_HEIGHT - 1,
                    color,
                )
            return

        # More logical nodes exist than can be read individually. A deterministic
        # density pattern communicates aggregation without allocating a^i objects.
        visible_columns = min(self.width, node_count)
        stride = self.width / visible_columns
        for index in range(visible_columns):
            x = min(self.width - 1, int(index * stride))
            self._pixel(canvas, x, y, _dim(color, 0.55))
            for row in range(block_height):
                if (x + row) % 2 == 0 or visible_columns >= self.width:
                    self._pixel(canvas, x, y + LEVEL_HEIGHT - 1 - row, color)

    def _draw_result_footer(
        self,
        canvas: Canvas,
        analysis: MasterTheoremAnalysis,
        color: Color,
    ) -> None:
        top = self.height - FOOTER_HEIGHT
        if analysis.dominance == "balanced":
            self._fill_rect(canvas, 0, top + 1, self.width - 1, top + 2, color)
            return
        center = self.width // 2
        if analysis.dominance == "root":
            rows = ((top + 3, 5), (top + 2, 3), (top + 1, 1))
        else:
            rows = ((top, 1), (top + 1, 3), (top + 2, 5))
        for row, half_width in rows:
            self._fill_rect(canvas, center - half_width, row, center + half_width, row, color)

    @staticmethod
    def _work_heights(
        levels: tuple[RecurrenceLevel, ...],
        exponent: float,
    ) -> list[int]:
        # The exponent supplies the visual metaphor: constant/linear work is a
        # line, quadratic work is a five-row box, and fractional powers interpolate.
        maximum_height = max(1, min(5, 1 + round(4 * max(0.0, exponent - 1.0))))
        if maximum_height == 1:
            return [1] * len(levels)
        logs = [math.log(max(level.work_per_node, 1e-12)) for level in levels]
        low, high = min(logs), max(logs)
        if high - low < 1e-12:
            return [maximum_height] * len(levels)
        return [
            1 + round((maximum_height - 1) * (value - low) / (high - low))
            for value in logs
        ]

    @staticmethod
    def _level_color(
        base: Color,
        level: RecurrenceLevel,
        levels: tuple[RecurrenceLevel, ...],
    ) -> Color:
        totals = [math.log(max(item.total_work, 1e-12)) for item in levels]
        low, high = min(totals), max(totals)
        factor = 0.72 if high - low < 1e-12 else 0.35 + 0.65 * (
            math.log(max(level.total_work, 1e-12)) - low
        ) / (high - low)
        return _dim(base, factor)

    def _draw_text(self, canvas: Canvas, x: int, y: int, text: str, color: Color) -> None:
        cursor = x
        for character in text.upper():
            glyph = FONT_3X5.get(character, FONT_3X5[" "])
            if cursor + 2 >= self.width:
                return
            for row, pixels in enumerate(glyph):
                for column, enabled in enumerate(pixels):
                    if enabled == "1":
                        self._pixel(canvas, cursor + column, y + row, color)
            cursor += 4

    def _fill_rect(
        self,
        canvas: Canvas,
        left: int,
        top: int,
        right: int,
        bottom: int,
        color: Color,
    ) -> None:
        for y in range(max(0, top), min(self.height - 1, bottom) + 1):
            for x in range(max(0, left), min(self.width - 1, right) + 1):
                self._pixel(canvas, x, y, color)

    def _pixel(self, canvas: Canvas, x: int, y: int, color: Color) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            canvas.SetPixel(x, y, *color)


def _dim(color: Color, factor: float) -> Color:
    return tuple(max(0, min(255, round(channel * factor))) for channel in color)  # type: ignore[return-value]


def _compact_number(value: float) -> str:
    numeric = float(value)
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:.1f}".rstrip("0").rstrip(".")
