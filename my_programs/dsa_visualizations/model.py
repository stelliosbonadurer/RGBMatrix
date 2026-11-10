"""Hardware-independent recurrence and Master Theorem calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class RecurrenceSpec:
    """A recurrence of the form T(n) = aT(n/b) + Theta(n^k)."""

    a: int
    b: int
    n: int
    k: float
    base_case: int = 1

    def validate(self) -> None:
        if type(self.a) is not int or self.a < 1:
            raise ValueError("a must be a positive integer")
        if type(self.b) is not int or self.b < 2:
            raise ValueError("b must be an integer of at least 2")
        if type(self.n) is not int or self.n < 2:
            raise ValueError("n must be an integer of at least 2")
        if type(self.base_case) is not int or self.base_case < 1:
            raise ValueError("base_case must be a positive integer")
        if isinstance(self.k, bool) or not isinstance(self.k, (int, float)):
            raise ValueError("k must be a non-negative finite number")
        if not math.isfinite(float(self.k)) or self.k < 0:
            raise ValueError("k must be a non-negative finite number")


@dataclass(frozen=True)
class RecurrenceLevel:
    index: int
    node_count: int
    problem_size: float
    work_per_node: float
    total_work: float


@dataclass(frozen=True)
class MasterTheoremAnalysis:
    spec: RecurrenceSpec
    levels: tuple[RecurrenceLevel, ...]
    critical_exponent: float
    level_work_ratio: float
    case: int
    dominance: str
    asymptotic: str

    @property
    def dominant_level_indices(self) -> tuple[int, ...]:
        if self.dominance == "root":
            return (0,)
        if self.dominance == "leaves":
            return (len(self.levels) - 1,)
        return tuple(range(len(self.levels)))


def analyze_recurrence(spec: RecurrenceSpec) -> MasterTheoremAnalysis:
    """Calculate every recursion level and classify the Master Theorem case."""

    spec.validate()
    k = float(spec.k)
    depth = max(0, int(math.ceil(math.log(spec.n / spec.base_case, spec.b) - 1e-12)))
    levels = []
    for index in range(depth + 1):
        node_count = spec.a**index
        problem_size = spec.n / (spec.b**index)
        work_per_node = problem_size**k
        levels.append(
            RecurrenceLevel(
                index=index,
                node_count=node_count,
                problem_size=problem_size,
                work_per_node=work_per_node,
                total_work=node_count * work_per_node,
            )
        )

    critical_exponent = math.log(spec.a, spec.b)
    level_work_ratio = spec.a / (spec.b**k)
    if k < critical_exponent - 1e-9:
        case = 1
        dominance = "leaves"
        asymptotic = f"Theta(n^{_format_number(critical_exponent)})"
    elif k > critical_exponent + 1e-9:
        case = 3
        dominance = "root"
        asymptotic = f"Theta(n^{_format_number(k)})"
    else:
        case = 2
        dominance = "balanced"
        asymptotic = (
            "Theta(log n)" if abs(k) < 1e-9 else f"Theta(n^{_format_number(k)} log n)"
        )

    return MasterTheoremAnalysis(
        spec=spec,
        levels=tuple(levels),
        critical_exponent=critical_exponent,
        level_work_ratio=level_work_ratio,
        case=case,
        dominance=dominance,
        asymptotic=asymptotic,
    )


def _format_number(value: float) -> str:
    rounded = round(value)
    if abs(value - rounded) < 1e-9:
        return str(rounded)
    return f"{value:.2f}".rstrip("0").rstrip(".")
