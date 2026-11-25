"""Frequency-weighting control points for one FFT layer projection.

Kept free of NumPy and of the ``core`` package so ``config.settings`` can use it
without an import cycle. ``core.analysis_plan`` evaluates the curve.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import TypeAlias


# Frequency-weighting control points (web-control-refactor D-019-D-022). A point's
# position is 0-1 along the layer's log-frequency range; the weight between
# points is linear in position and flat beyond the first and last point.
MIN_WEIGHT_POINTS = 1
MAX_WEIGHT_POINTS = 16
WEIGHT_POSITION_MIN = 0.0
WEIGHT_POSITION_MAX = 1.0
WEIGHT_MIN = 0.0
WEIGHT_MAX = 100.0
WEIGHT_POINT_FIELDS = frozenset({"position", "weight"})
LEGACY_WEIGHT_POSITIONS = (0.0, 0.1, 0.3, 0.6, 1.0)
LEGACY_WEIGHT_EXPONENT = 1.5
LEGACY_WEIGHT_DECIMALS = 3
DEFAULT_LOW_FREQ_WEIGHT = 0.45
DEFAULT_HIGH_FREQ_WEIGHT = 10.0

# Canonical, hashable form: ((position, weight), ...) sorted by position.
WeightPoints: TypeAlias = tuple[tuple[float, float], ...]


class WeightPointsError(ValueError):
    """One invalid weighting-point list; ``field`` names the offending value.

    ``field`` is relative to the layer settings, for example
    ``weight_points[2].weight``.
    """

    def __init__(self, message: str, *, field: str = "weight_points") -> None:
        super().__init__(message)
        self.field = field


def _bounded_float(
    value: object,
    *,
    field: str,
    minimum: float,
    maximum: float,
) -> float:
    number = math.nan
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            number = float(value)
        except OverflowError:
            number = math.nan
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise WeightPointsError(
            f"{field} must be a number between {minimum:g} and {maximum:g}",
            field=field,
        )
    # + 0.0 folds -0.0 into 0.0 so the canonical form is exact.
    return number + 0.0


def canonical_weight_points(value: object) -> WeightPoints:
    """Validate weighting points and return them sorted by position.

    Accepts the public document form ``[{"position": p, "weight": w}, ...]`` or
    the canonical ``((p, w), ...)`` form. Raises :class:`WeightPointsError`.
    """

    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise WeightPointsError("weight_points must be a list of points")
    if not MIN_WEIGHT_POINTS <= len(value) <= MAX_WEIGHT_POINTS:
        raise WeightPointsError(
            f"weight_points must contain between {MIN_WEIGHT_POINTS} and "
            f"{MAX_WEIGHT_POINTS} points"
        )
    points: list[tuple[float, float, int]] = []
    for index, raw in enumerate(value):
        prefix = f"weight_points[{index}]"
        if isinstance(raw, Mapping):
            present = set(raw)
            if present != WEIGHT_POINT_FIELDS:
                detail = []
                missing = sorted(WEIGHT_POINT_FIELDS - present)
                unknown = sorted(str(name) for name in present - WEIGHT_POINT_FIELDS)
                if missing:
                    detail.append(f"missing {missing!r}")
                if unknown:
                    detail.append(f"unknown {unknown!r}")
                raise WeightPointsError(
                    f"{prefix} fields are invalid: " + "; ".join(detail),
                    field=prefix,
                )
            raw_position, raw_weight = raw["position"], raw["weight"]
        elif isinstance(raw, tuple) and len(raw) == 2:
            raw_position, raw_weight = raw
        else:
            raise WeightPointsError(f"{prefix} must be an object", field=prefix)
        position = _bounded_float(
            raw_position,
            field=f"{prefix}.position",
            minimum=WEIGHT_POSITION_MIN,
            maximum=WEIGHT_POSITION_MAX,
        )
        weight = _bounded_float(
            raw_weight,
            field=f"{prefix}.weight",
            minimum=WEIGHT_MIN,
            maximum=WEIGHT_MAX,
        )
        points.append((position, weight, index))
    points.sort(key=lambda point: point[0])
    for previous, current in zip(points, points[1:]):
        if current[0] == previous[0]:
            raise WeightPointsError(
                f"weight_points[{current[2]}].position duplicates "
                f"weight_points[{previous[2]}].position",
                field=f"weight_points[{current[2]}].position",
            )
    return tuple((position, weight) for position, weight, _index in points)


def weight_points_document(points: object) -> list[dict[str, float]]:
    """Return validated points in sorted public document form."""

    return [
        {"position": position, "weight": weight}
        for position, weight in canonical_weight_points(points)
    ]


def legacy_weight_points(
    low_freq_weight: float = DEFAULT_LOW_FREQ_WEIGHT,
    high_freq_weight: float = DEFAULT_HIGH_FREQ_WEIGHT,
) -> list[dict[str, float]]:
    """Approximate the retired ``low``/``high`` pair with points (D-022).

    Samples ``low + (high - low) * position ** 1.5`` at
    :data:`LEGACY_WEIGHT_POSITIONS`, rounded to 3 decimals.
    """

    low = _bounded_float(
        low_freq_weight, field="low_freq_weight", minimum=WEIGHT_MIN, maximum=WEIGHT_MAX
    )
    high = _bounded_float(
        high_freq_weight, field="high_freq_weight", minimum=WEIGHT_MIN, maximum=WEIGHT_MAX
    )
    return [
        {
            "position": position,
            "weight": round(
                low + (high - low) * position ** LEGACY_WEIGHT_EXPONENT,
                LEGACY_WEIGHT_DECIMALS,
            ),
        }
        for position in LEGACY_WEIGHT_POSITIONS
    ]


DEFAULT_WEIGHT_POINTS: WeightPoints = canonical_weight_points(legacy_weight_points())
