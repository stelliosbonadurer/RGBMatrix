#!/usr/bin/env python3
"""Animate BFS, DFS, and Dijkstra traversal on a planar graph."""

from __future__ import annotations

import signal
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROGRAMS_ROOT = PROJECT_ROOT / "my_programs"
for import_root in (PROJECT_ROOT, PROGRAMS_ROOT):
    value = str(import_root)
    if value not in sys.path:
        sys.path.insert(0, value)

from dsa_visualizations.graph_model import (
    GraphOptions,
    connected_components,
    generate_graph,
    traversal_frames,
    validate_options,
)
from dsa_visualizations.graph_rendering import GraphRenderer, load_render_config
from dsa_visualizations.native_matrix import NativeMatrixApp
from matrix_controller.definitions import application_parameters


def build_options(args) -> GraphOptions:
    return GraphOptions(
        n=args.n,
        e=args.e,
        traversal=args.traverse,
        seed=args.seed,
        connected=args.connected,
        allow_cycles=args.allow_cycles,
        directed=args.directed,
        directed_connectivity=args.directed_connectivity,
        component_mode=args.component_mode,
        step_mode=args.step_mode,
    )


def _request_exit(_signum, _frame) -> None:
    raise KeyboardInterrupt


def main() -> int:
    app = NativeMatrixApp()
    for parameter in application_parameters("graph_traversal"):
        parameter.add_to_argparse(app.parser)
    args = app.process_args()
    options = build_options(args)
    try:
        resolved_edges = validate_options(options)
        render_config = load_render_config()
    except ValueError as exc:
        app.parser.error(str(exc))

    print(
        f"Graph traversal: {options.traversal.upper()}, n={options.n}, "
        f"e={resolved_edges}, seed={options.seed}, step={options.step_mode}"
    )
    if not app.initialize_matrix():
        app.print_help()
        return 1
    if app.width != 64 or app.height != 64:
        print(f"Warning: this visualization is designed for 64x64; got {app.width}x{app.height}.")

    try:
        graph = generate_graph(options, app.width, app.height)
    except ValueError as exc:
        app.parser.error(str(exc))
    component_ids = (
        connected_components(graph, options.component_mode)
        if options.component_mode != "off"
        else None
    )
    renderer = GraphRenderer(render_config, options.traversal, component_ids)

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _request_exit)
    try:
        # Replay the same seeded graph so a managed visualization remains alive and reproducible.
        while True:
            for frame_index, frame in enumerate(
                traversal_frames(graph, options.traversal, options.step_mode)
            ):
                renderer.render(app.canvas, graph, frame)
                app.swap_canvas()
                if frame_index == 0 or frame.complete:
                    time.sleep(args.frame_duration * args.holding_period)
                else:
                    time.sleep(args.frame_duration)
    except KeyboardInterrupt:
        print("\nGraph traversal visualization stopped.")
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        app.clear()
        app.swap_canvas()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
