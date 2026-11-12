"""Render a static Master Theorem recursion tree onto a pixel canvas."""

from __future__ import annotations

import math
from typing import Protocol

from .model import MasterTheoremAnalysis, RecurrenceLevel


Color = tuple[int, int, int]
INPUT_Y = 0
FIRST_LAYER_Y = 2
LAYER_GAP = 1
NODE_GAP = 1
INPUT_COLOR: Color = (255, 255, 255)
WORK_COLOR: Color = (65, 155, 255)

# Kept for callers that imported the old palette. The static view deliberately
# uses one color for all work rather than coloring the Master Theorem result.
CASE_COLORS: dict[int, Color] = {1: WORK_COLOR, 2: WORK_COLOR, 3: WORK_COLOR}


class Canvas(Protocol):
    width: int
    height: int

    def Clear(self) -> None: ...

    def SetPixel(self, x: int, y: int, red: int, green: int, blue: int) -> None: ...


class MasterTheoremRenderer:
    """Draw the input and every recursion layer without labels or animation."""

    def __init__(self, width: int = 64, height: int = 64):
        if width < 16 or height < 4:
            raise ValueError("canvas is too small for the Master Theorem layout")
        self.width = width
        self.height = height

    def input_width(self, analysis: MasterTheoremAnalysis) -> int:
        """Return the literal model n used by the input line."""

        if analysis.spec.n > self.width:
            raise ValueError("n cannot be wider than the matrix")
        return analysis.spec.n

    def visible_levels(self, analysis: MasterTheoremAnalysis) -> tuple[RecurrenceLevel, ...]:
        """Return the recursive layers that fit below the input line."""

        levels: list[RecurrenceLevel] = []
        y = FIRST_LAYER_Y
        for level in analysis.levels[1:]:
            _shape_width, shape_height, _pixel_count = self._work_shape(
                level, analysis.spec.k
            )
            if y + shape_height > self.height:
                break
            levels.append(level)
            y += shape_height + LAYER_GAP
        return tuple(levels)

    def render(
        self,
        canvas: Canvas,
        analysis: MasterTheoremAnalysis,
        *,
        reveal_levels: int | None = None,
        highlight_level: int | None = None,
        show_result: bool = False,
    ) -> None:
        """Render one static frame.

        The keyword arguments remain accepted for compatibility with the old
        animated renderer. They intentionally do not alter the static layout.
        """

        del reveal_levels, highlight_level, show_result
        canvas.Clear()

        root_width = self.input_width(analysis)
        root_left = (self.width - root_width) // 2
        self._fill_rect(
            canvas,
            root_left,
            INPUT_Y,
            root_left + root_width - 1,
            INPUT_Y,
            INPUT_COLOR,
        )

        y = FIRST_LAYER_Y
        for level in self.visible_levels(analysis):
            shape_width, shape_height, pixel_count = self._work_shape(
                level, analysis.spec.k
            )
            self._draw_level(
                canvas,
                level,
                y,
                shape_width,
                shape_height,
                pixel_count,
            )
            y += shape_height + LAYER_GAP

    def _draw_level(
        self,
        canvas: Canvas,
        level: RecurrenceLevel,
        y: int,
        shape_width: int,
        shape_height: int,
        pixel_count: int,
    ) -> None:
        node_count = level.node_count
        desired_span = node_count * shape_width + max(0, node_count - 1) * NODE_GAP
        span = min(self.width, max(1, desired_span))
        span_left = (self.width - span) // 2

        # More nodes exist than readable columns. Preserve the increasing node
        # density instead of attempting to allocate one object per logical node.
        if node_count > self.width:
            for x in range(self.width):
                for row in range(shape_height):
                    if (x + row) % 2 == 0 or node_count >= self.width * 2:
                        self._pixel(canvas, x, y + row, WORK_COLOR)
            return

        if desired_span <= self.width:
            for node_index in range(node_count):
                left = span_left + node_index * (shape_width + NODE_GAP)
                self._draw_work_shape(
                    canvas, left, y, level, pixel_count
                )
            return

        for node_index in range(node_count):
            slot_left = span_left + math.floor(node_index * span / node_count)
            slot_right = span_left + math.floor((node_index + 1) * span / node_count) - 1
            slot_width = max(1, slot_right - slot_left + 1)
            drawn_width = min(shape_width, slot_width)
            left = slot_left + (slot_width - drawn_width) // 2
            self._draw_work_shape(canvas, left, y, level, pixel_count, drawn_width)

    @staticmethod
    def _work_shape(
        level: RecurrenceLevel,
        exponent: float,
    ) -> tuple[int, int, int]:
        """Return width, height, and exact rounded pixel count for one node.

        Work fills vertical columns no taller than the rounded n at this level.
        Thus n is a vertical line, n^2 is an n-by-n square, and fractional
        exponents fill full columns followed by a partial final column.
        """

        level_n = max(1, round(level.problem_size))
        pixel_count = max(1, round(level_n**exponent))
        width = math.ceil(pixel_count / level_n)
        height = min(level_n, pixel_count)
        return width, height, pixel_count

    def _draw_work_shape(
        self,
        canvas: Canvas,
        left: int,
        top: int,
        level: RecurrenceLevel,
        pixel_count: int,
        maximum_width: int | None = None,
    ) -> None:
        """Fill one node column-by-column, including a partial last column."""

        column_height = max(1, round(level.problem_size))
        remaining = pixel_count
        column = 0
        while remaining > 0 and (maximum_width is None or column < maximum_width):
            pixels_in_column = min(column_height, remaining)
            self._fill_rect(
                canvas,
                left + column,
                top,
                left + column,
                top + pixels_in_column - 1,
                WORK_COLOR,
            )
            remaining -= pixels_in_column
            column += 1

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
