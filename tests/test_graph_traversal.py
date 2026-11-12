"""Hardware-free tests for planar graph traversal generation and rendering."""

from __future__ import annotations

import unittest

from matrix_controller.definitions import application_parameters
from matrix_controller.registry import build_default_registry
from my_programs.dsa_visualizations.graph_model import (
    Edge,
    Graph,
    GraphOptions,
    Point,
    TraversalFrame,
    edge_bounds,
    generate_graph,
    resolve_edge_count,
    traversal_frames,
)
from my_programs.dsa_visualizations.graph_rendering import GraphRenderer, load_render_config
from my_programs.dsa_visualizations.native_matrix import NativeMatrixApp


class RecordingCanvas:
    def __init__(self, width: int = 64, height: int = 64):
        self.width = width
        self.height = height
        self.pixels: dict[tuple[int, int], tuple[int, int, int]] = {}

    def Clear(self) -> None:
        self.pixels.clear()

    def SetPixel(self, x: int, y: int, red: int, green: int, blue: int) -> None:
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise AssertionError(f"out-of-bounds pixel: {(x, y)}")
        self.pixels[(x, y)] = (red, green, blue)


def reachable(graph: Graph, start: int) -> set[int]:
    adjacent = {index: [] for index in range(len(graph.points))}
    for edge in graph.edges:
        adjacent[edge.source].append(edge.target)
        if not graph.directed:
            adjacent[edge.target].append(edge.source)
    seen = {start}
    pending = [start]
    while pending:
        for neighbor in adjacent[pending.pop()]:
            if neighbor not in seen:
                seen.add(neighbor)
                pending.append(neighbor)
    return seen


class GraphConstraintTests(unittest.TestCase):
    def test_automatic_edges_follow_cycle_and_connectivity_options(self) -> None:
        self.assertEqual(resolve_edge_count(GraphOptions(n=12)), 11)
        self.assertEqual(
            resolve_edge_count(GraphOptions(n=12, allow_cycles=True)),
            18,
        )
        self.assertEqual(resolve_edge_count(GraphOptions(n=12, directed=True)), 18)
        strong = GraphOptions(
            n=12,
            directed=True,
            directed_connectivity="strong",
            allow_cycles=True,
        )
        self.assertEqual(edge_bounds(strong).minimum, 12)
        self.assertEqual(resolve_edge_count(strong), 18)

    def test_invalid_edge_combinations_have_clear_errors(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 11"):
            resolve_edge_count(GraphOptions(n=12, e=10, connected=True))
        with self.assertRaisesRegex(ValueError, "at most 11"):
            resolve_edge_count(GraphOptions(n=12, e=12, allow_cycles=False))
        with self.assertRaisesRegex(ValueError, "require allow_cycles=true"):
            resolve_edge_count(
                GraphOptions(n=12, directed=True, directed_connectivity="strong")
            )


class GraphGenerationTests(unittest.TestCase):
    def test_seed_reproduces_points_edges_directions_and_weights(self) -> None:
        options = GraphOptions(n=16, allow_cycles=True, directed=True, seed=8675309)
        self.assertEqual(generate_graph(options), generate_graph(options))
        self.assertNotEqual(generate_graph(options), generate_graph(GraphOptions(**{**options.__dict__, "seed": 2})))

    def test_default_graph_is_a_connected_tree(self) -> None:
        graph = generate_graph(GraphOptions(n=12, seed=3))
        self.assertEqual(len(graph.edges), 11)
        self.assertEqual(reachable(graph, graph.start), set(range(12)))

    def test_strong_directed_graph_reaches_every_node_from_every_node(self) -> None:
        graph = generate_graph(
            GraphOptions(
                n=12,
                allow_cycles=True,
                directed=True,
                directed_connectivity="strong",
                seed=9,
            )
        )
        for node in range(12):
            self.assertEqual(reachable(graph, node), set(range(12)))

    def test_directed_graph_without_cycles_is_a_dag_with_more_than_a_tree(self) -> None:
        graph = generate_graph(GraphOptions(n=12, directed=True, seed=13))
        self.assertEqual(len(graph.edges), 18)
        incoming = [0] * len(graph.points)
        outgoing: list[list[int]] = [[] for _ in graph.points]
        for edge in graph.edges:
            incoming[edge.target] += 1
            outgoing[edge.source].append(edge.target)
        ready = [node for node, count in enumerate(incoming) if count == 0]
        removed = 0
        while ready:
            node = ready.pop()
            removed += 1
            for neighbor in outgoing[node]:
                incoming[neighbor] -= 1
                if incoming[neighbor] == 0:
                    ready.append(neighbor)
        self.assertEqual(removed, len(graph.points))


class TraversalTests(unittest.TestCase):
    def test_all_methods_finish_and_edge_mode_exposes_current_edges(self) -> None:
        graph = generate_graph(GraphOptions(n=12, allow_cycles=True, seed=4))
        for method in ("bfs", "dfs", "dijkstra"):
            with self.subTest(method=method):
                frames = list(traversal_frames(graph, method, "edge"))
                self.assertTrue(frames[-1].complete)
                self.assertTrue(frames[-1].target_reached)
                self.assertTrue(any(frame.examined_edge is not None for frame in frames))

    def test_node_mode_has_no_examined_edge_frames(self) -> None:
        graph = generate_graph(GraphOptions(n=8, seed=8))
        for method in ("bfs", "dfs", "dijkstra"):
            frames = list(traversal_frames(graph, method, "node"))
            self.assertTrue(all(frame.examined_edge is None for frame in frames))

    def test_dfs_marks_the_entire_directed_cycle(self) -> None:
        graph = Graph(
            (Point(5, 5), Point(20, 5), Point(12, 20), Point(40, 40)),
            (Edge(0, 0, 1, 1), Edge(1, 1, 2, 1), Edge(2, 2, 0, 1)),
            True,
            0,
            3,
        )
        final = list(traversal_frames(graph, "dfs", "edge"))[-1]
        self.assertEqual(final.cycle_edges, frozenset({0, 1, 2}))
        self.assertFalse(final.target_reached)

    def test_dijkstra_highlights_only_the_lowest_weight_final_path(self) -> None:
        graph = Graph(
            (Point(2, 2), Point(20, 2), Point(40, 2)),
            (Edge(0, 0, 2, 9), Edge(1, 0, 1, 2), Edge(2, 1, 2, 2)),
            False,
            0,
            2,
        )
        final = list(traversal_frames(graph, "dijkstra", "edge"))[-1]
        self.assertEqual(final.final_path_edges, frozenset({1, 2}))


class GraphRenderingTests(unittest.TestCase):
    def test_config_and_current_state_colors_are_used(self) -> None:
        config = load_render_config()
        graph = Graph((Point(5, 5), Point(20, 5)), (Edge(0, 0, 1, 5),), False, 0, 1)
        frame = TraversalFrame(0, 0, frozenset(), frozenset(), frozenset({0}), frozenset(), frozenset())
        canvas = RecordingCanvas()
        GraphRenderer(config, "bfs").render(canvas, graph, frame)
        self.assertIn(config.colors.current_edge, canvas.pixels.values())
        self.assertEqual(canvas.pixels[(5, 5)], config.colors.current_node)
        self.assertIn(config.colors.target_node, canvas.pixels.values())

    def test_processed_nodes_return_to_their_stable_colors(self) -> None:
        config = load_render_config()
        self.assertEqual(config.node_size, 1)
        graph = Graph(
            (Point(5, 5), Point(20, 5), Point(35, 5)),
            (Edge(0, 0, 1, 1), Edge(1, 1, 2, 1)),
            False,
            0,
            2,
        )
        active = TraversalFrame(
            1,
            None,
            frozenset({0, 1}),
            frozenset({2}),
            frozenset({0}),
            frozenset(),
            frozenset(),
        )
        canvas = RecordingCanvas()
        renderer = GraphRenderer(config, "bfs")
        renderer.render(canvas, graph, active)
        self.assertEqual(canvas.pixels[(5, 5)], config.colors.start_node)
        self.assertEqual(canvas.pixels[(20, 5)], config.colors.current_node)
        self.assertEqual(canvas.pixels[(35, 5)], config.colors.target_node)

        complete = TraversalFrame(
            None,
            None,
            frozenset({0, 1, 2}),
            frozenset(),
            frozenset({0, 1}),
            frozenset(),
            frozenset(),
            complete=True,
            target_reached=True,
        )
        renderer.render(canvas, graph, complete)
        self.assertEqual(canvas.pixels[(5, 5)], config.colors.start_node)
        self.assertEqual(canvas.pixels[(20, 5)], config.colors.node)
        self.assertEqual(canvas.pixels[(35, 5)], config.colors.target_node)

    def test_dijkstra_unvisited_weights_use_different_colors(self) -> None:
        config = load_render_config()
        graph = Graph(
            (Point(4, 4), Point(20, 4), Point(4, 20)),
            (Edge(0, 0, 1, 1), Edge(1, 0, 2, 9)),
            False,
            1,
            2,
        )
        empty = TraversalFrame(None, None, frozenset(), frozenset(), frozenset(), frozenset(), frozenset())
        canvas = RecordingCanvas()
        GraphRenderer(config, "dijkstra").render(canvas, graph, empty)
        self.assertIn(config.colors.dijkstra_low_weight, canvas.pixels.values())
        self.assertIn(config.colors.dijkstra_high_weight, canvas.pixels.values())

    def test_completed_dijkstra_only_keeps_the_shortest_path_green(self) -> None:
        config = load_render_config()
        graph = Graph(
            (Point(4, 4), Point(20, 4), Point(36, 4), Point(20, 20)),
            (
                Edge(0, 0, 1, 1),
                Edge(1, 1, 2, 1),
                Edge(2, 2, 3, 9),
                Edge(3, 3, 0, 9),
            ),
            False,
            0,
            2,
        )
        complete = TraversalFrame(
            None,
            None,
            frozenset({0, 1, 2, 3}),
            frozenset(),
            frozenset({0, 1, 2, 3}),
            frozenset(),
            frozenset({0, 1}),
            complete=True,
            target_reached=True,
        )
        renderer = GraphRenderer(config, "dijkstra")
        self.assertEqual(renderer._edge_color(graph.edges[0], complete), config.colors.shortest_path)
        self.assertEqual(renderer._edge_color(graph.edges[1], complete), config.colors.shortest_path)
        self.assertEqual(
            renderer._edge_color(graph.edges[2], complete),
            config.colors.dijkstra_high_weight,
        )
        self.assertEqual(
            renderer._edge_color(graph.edges[3], complete),
            config.colors.dijkstra_high_weight,
        )
        self.assertNotIn(
            config.colors.traversed_edge,
            [renderer._edge_color(edge, complete) for edge in graph.edges],
        )


class GraphTraversalCliTests(unittest.TestCase):
    def test_managed_argv_is_accepted_by_visualization_parser(self) -> None:
        definition = build_default_registry().get("graph_traversal")
        values = definition.validate_parameters(
            {
                "n": 18,
                "traverse": "dijkstra",
                "allow_cycles": True,
                "directed": True,
                "directed_connectivity": "strong",
            }
        )
        argv = definition.build_argv("/fixed/python", values)
        app = NativeMatrixApp()
        for parameter in application_parameters("graph_traversal"):
            parameter.add_to_argparse(app.parser)
        parsed = app.parser.parse_args(argv[2:])
        self.assertEqual(parsed.n, 18)
        self.assertEqual(parsed.traverse, "dijkstra")
        self.assertTrue(parsed.allow_cycles)
        self.assertEqual(parsed.directed_connectivity, "strong")


if __name__ == "__main__":
    unittest.main()
