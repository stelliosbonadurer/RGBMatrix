#!/usr/bin/env python3
"""Animate a Master Theorem recursion tree on a 64x64 RGB matrix."""

from __future__ import annotations

import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROGRAMS_ROOT = PROJECT_ROOT / "my_programs"
for import_root in (PROJECT_ROOT, PROGRAMS_ROOT):
    value = str(import_root)
    if value not in sys.path:
        sys.path.insert(0, value)

from dsa_visualizations.model import RecurrenceSpec, analyze_recurrence
from dsa_visualizations.native_matrix import NativeMatrixApp
from dsa_visualizations.rendering import MasterTheoremRenderer
from matrix_controller.definitions import application_parameters


@dataclass(frozen=True)
class Example:
    a: int
    b: int
    n: int
    k: float


EXAMPLES = {
    "binary_search": Example(1, 2, 64, 0.0),
    "merge_sort": Example(2, 2, 64, 1.0),
    "root_heavy": Example(2, 2, 64, 2.0),
    "strassen": Example(7, 2, 64, 2.0),
    "leaf_heavy": Example(4, 2, 64, 1.0),
}


def build_analysis(args):
    example = EXAMPLES.get(args.example)
    spec = RecurrenceSpec(
        a=example.a if example else args.a,
        b=example.b if example else args.b,
        n=example.n if example else args.n,
        k=example.k if example else args.k,
        base_case=args.base_case,
    )
    return analyze_recurrence(spec)


def animate(app: NativeMatrixApp, analysis, *, delay: float, hold: float, cycles: int) -> None:
    renderer = MasterTheoremRenderer(app.width, app.height)
    visible_count = len(renderer.visible_levels(analysis))
    completed = 0
    while cycles == 0 or completed < cycles:
        for reveal in range(1, visible_count + 1):
            renderer.render(app.canvas, analysis, reveal_levels=reveal)
            app.swap_canvas()
            time.sleep(delay)
        for level_index in range(visible_count):
            renderer.render(app.canvas, analysis, highlight_level=level_index)
            app.swap_canvas()
            time.sleep(delay)
        renderer.render(app.canvas, analysis, show_result=True)
        app.swap_canvas()
        time.sleep(hold)
        completed += 1


def _request_exit(_signum, _frame) -> None:
    raise KeyboardInterrupt


def main() -> int:
    app = NativeMatrixApp()
    for parameter in application_parameters("master_theorem"):
        parameter.add_to_argparse(app.parser)
    args = app.process_args()

    try:
        analysis = build_analysis(args)
    except ValueError as exc:
        app.parser.error(str(exc))

    print(
        f"T(n) = {analysis.spec.a}T(n/{analysis.spec.b}) + Theta(n^{analysis.spec.k:g})\n"
        f"critical exponent: log_{analysis.spec.b}({analysis.spec.a}) = "
        f"{analysis.critical_exponent:.3f}\n"
        f"level-work ratio: {analysis.level_work_ratio:.3f}\n"
        f"case {analysis.case}: {analysis.dominance} dominate; {analysis.asymptotic}"
    )

    if not app.initialize_matrix():
        app.print_help()
        return 1
    if app.width != 64 or app.height != 64:
        print(f"Warning: this visualization is designed for 64x64; got {app.width}x{app.height}.")

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _request_exit)
    try:
        animate(
            app,
            analysis,
            delay=args.delay,
            hold=args.hold,
            cycles=args.cycles,
        )
    except KeyboardInterrupt:
        print("\nMaster Theorem visualization stopped.")
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        app.clear()
        app.swap_canvas()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
