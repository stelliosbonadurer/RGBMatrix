"""
Color-stop themes: a theme described as data instead of code (fft-themes D-004).

A theme is a list of color stops along one axis (bar height or column position),
plus optional stop lists for the overflow layers. Each instance builds its lookup
tables once, with the brightness boost already applied, so ``get_color`` and
``get_overflow_color`` are a clamp, an index, and a tuple lookup per pixel: no
interpolation and no allocation.
"""
from typing import Sequence, Tuple, Type

from .base import BaseTheme

RGB = Tuple[int, int, int]
ColorStop = Tuple[float, RGB]

AXES = ("height", "column")
LUT_SIZE = 256
_LUT_LAST = LUT_SIZE - 1


class ColorStopError(ValueError):
    """A color-stop theme definition that can't be used."""


def _validate_stops(stops: Sequence[ColorStop], where: str) -> Tuple[ColorStop, ...]:
    if len(stops) < 2:
        raise ColorStopError(f"{where}: needs at least 2 color stops")
    checked = []
    previous = -1.0
    for index, stop in enumerate(stops):
        try:
            position, color = stop
            position = float(position)
            r, g, b = (int(channel) for channel in color)
        except (TypeError, ValueError) as exc:
            raise ColorStopError(f"{where}: stop {index} must be (position, (r, g, b))") from exc
        if not 0.0 <= position <= 1.0:
            raise ColorStopError(f"{where}: stop {index} position must be between 0 and 1")
        if position < previous:
            raise ColorStopError(f"{where}: stop positions must not decrease")
        if not all(0 <= channel <= 255 for channel in (r, g, b)):
            raise ColorStopError(f"{where}: stop {index} channels must be 0-255")
        previous = position
        checked.append((position, (r, g, b)))
    if checked[0][0] != 0.0 or checked[-1][0] != 1.0:
        raise ColorStopError(f"{where}: stops must start at 0 and end at 1")
    return tuple(checked)


def build_lut(stops: Sequence[ColorStop], brightness_boost: float = 1.0) -> Tuple[RGB, ...]:
    """Interpolate stops linearly in RGB into ``LUT_SIZE`` boosted colors."""
    table = []
    segment = 0
    for index in range(LUT_SIZE):
        ratio = index / _LUT_LAST
        while segment < len(stops) - 2 and ratio > stops[segment + 1][0]:
            segment += 1
        (start, low), (end, high) = stops[segment], stops[segment + 1]
        span = end - start
        t = 0.0 if span <= 0.0 else min(1.0, max(0.0, (ratio - start) / span))
        table.append(tuple(
            min(255, int(int(a + (b - a) * t) * brightness_boost))
            for a, b in zip(low, high)
        ))
    return tuple(table)


def wash_toward_white(stops: Sequence[ColorStop], amount: float) -> Tuple[ColorStop, ...]:
    """Return the stops blended ``amount`` (0-1) of the way toward white."""
    return tuple(
        (position, tuple(int(channel + (255 - channel) * amount) for channel in color))
        for position, color in stops
    )


def _lut_index(ratio: float) -> int:
    # ``not ratio > 0`` also catches NaN, which maps to the first color (D-006).
    if not ratio > 0.0:
        return 0
    if ratio >= 1.0:
        return _LUT_LAST
    return int(ratio * _LUT_LAST)


class ColorStopTheme(BaseTheme):
    """Theme driven by color stops; subclasses are made by ``color_stop_theme``."""

    name = "color_stops"
    description = "Color-stop theme"
    axis: str = "height"
    stops: Tuple[ColorStop, ...] = ((0.0, (0, 0, 0)), (1.0, (255, 255, 255)))
    overflow_stops: Tuple[Tuple[ColorStop, ...], ...] = ()

    def __init__(self, brightness_boost: float = 1.0):
        super().__init__(brightness_boost=brightness_boost)
        self._by_column = self.axis == "column"
        self._lut = build_lut(self.stops, brightness_boost)
        self._overflow_luts = tuple(
            build_lut(layer_stops, brightness_boost) for layer_stops in self.overflow_stops
        )

    def get_color(self, height_ratio: float, column_ratio: float = 0.0) -> RGB:
        return self._lut[_lut_index(column_ratio if self._by_column else height_ratio)]

    def get_overflow_color(
        self,
        layer: int,
        height_ratio: float,
        column_ratio: float = 0.0,
        frame: int = 0,
        bar_ratio: float = 0.0,
    ) -> RGB:
        luts = self._overflow_luts
        if layer <= 0 or not luts:
            return self.get_color(height_ratio, column_ratio)
        lut = luts[layer - 1] if layer <= len(luts) else luts[-1]
        return lut[_lut_index(column_ratio if self._by_column else height_ratio)]


def color_stop_theme(
    name: str,
    description: str,
    stops: Sequence[ColorStop],
    *,
    axis: str = "height",
    overflow: Sequence[Sequence[ColorStop]] = (),
) -> Type[ColorStopTheme]:
    """Validate a definition and return a registrable theme class.

    ``overflow`` holds one stop list per overflow layer (layer 1 first); layers past
    the last list reuse it. Without it, overflow layers repeat the main gradient.
    """
    if not isinstance(name, str) or not name:
        raise ColorStopError("theme name must be a non-empty string")
    if axis not in AXES:
        raise ColorStopError(f"{name}: axis must be one of {', '.join(AXES)}")
    checked = _validate_stops(stops, name)
    checked_overflow = tuple(
        _validate_stops(layer_stops, f"{name} overflow layer {index + 1}")
        for index, layer_stops in enumerate(overflow)
    )
    class_name = "".join(part.capitalize() for part in name.split("_")) + "Theme"
    return type(class_name, (ColorStopTheme,), {
        "name": name,
        "description": description,
        "axis": axis,
        "stops": checked,
        "overflow_stops": checked_overflow,
        "__module__": __name__,
    })
