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
    connected_components,
    edge_bounds,
    generate_graph,
    parallel_traversal_frames,
    resolve_edge_count,
    traversal_frames,
    validate_options,
)
from my_programs.dsa_visualizations.graph_rendering import (
    GraphRenderer,
    Viewport,
    load_render_config,
)
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
        with self.assertRaisesRegex(ValueError, "requires directed=true"):
            validate_options(
                GraphOptions(n=12, traversal="components", component_type="strong")
            )

    def test_component_traversal_does_not_require_an_endpoint_path(self) -> None:
        options = GraphOptions(
            n=12,
            traversal="components",
            directed=True,
            directed_connectivity="none",
            ensure_path=True,
        )
        self.assertEqual(edge_bounds(options).minimum, 0)

    def test_mst_requires_an_undirected_connected_graph(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires directed=false"):
            validate_options(GraphOptions(traversal="prim", directed=True))
        with self.assertRaisesRegex(ValueError, "requires connected=true"):
            validate_options(GraphOptions(traversal="kruskal", connected=False))
        with self.assertRaisesRegex(ValueError, "requires connected=true"):
            validate_options(
                GraphOptions(display_mode="prim_kruskal_split", connected=False)
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

    def test_split_graph_uses_one_logical_32_by_64_layout(self) -> None:
        graph = generate_graph(
            GraphOptions(n=12, seed=17, display_mode="bfs_dfs_split"),
            32,
            64,
        )
        self.assertTrue(all(0 <= point.x < 32 for point in graph.points))
        self.assertTrue(all(0 <= point.y < 64 for point in graph.points))
        self.assertEqual(len(graph.points), 12)

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
        self.assertIn(graph.target, reachable(graph, graph.start))

    def test_ensure_path_works_without_a_connectivity_guarantee(self) -> None:
        for allow_cycles in (False, True):
            with self.subTest(allow_cycles=allow_cycles):
                graph = generate_graph(
                    GraphOptions(
                        n=12,
                        e=6,
                        directed=True,
                        directed_connectivity="none",
                        ensure_path=True,
                        allow_cycles=allow_cycles,
                        seed=27,
                    )
                )
                self.assertIn(graph.target, reachable(graph, graph.start))
                self.assertEqual(edge_bounds(
                    GraphOptions(
                        n=12,
                        directed=True,
                        directed_connectivity="none",
                        ensure_path=True,
                    )
                ).minimum, 1)

    def test_component_graph_generation_ignores_ensure_path(self) -> None:
        common = {
            "n": 12,
            "e": 6,
            "traversal": "components",
            "directed": True,
            "directed_connectivity": "none",
            "allow_cycles": True,
            "seed": 41,
        }
        with_path_setting = generate_graph(GraphOptions(**common, ensure_path=True))
        without_path_setting = generate_graph(GraphOptions(**common, ensure_path=False))
        self.assertEqual(with_path_setting, without_path_setting)


class TraversalTests(unittest.TestCase):
    @staticmethod
    def mst_graph() -> Graph:
        return Graph(
            (Point(2, 2), Point(20, 2), Point(38, 2), Point(56, 2)),
            (
                Edge(0, 0, 1, 1),
                Edge(1, 1, 2, 1),
                Edge(2, 0, 2, 2),
                Edge(3, 2, 3, 3),
                Edge(4, 0, 3, 9),
            ),
            False,
            0,
            3,
        )

    def test_weak_and_strong_component_modes_are_distinct(self) -> None:
        graph = Graph(
            (Point(2, 2), Point(20, 2), Point(20, 20), Point(40, 20)),
            (
                Edge(0, 0, 1, 1),
                Edge(1, 1, 2, 1),
                Edge(2, 2, 0, 1),
                Edge(3, 2, 3, 1),
            ),
            True,
            0,
            3,
        )
        self.assertEqual(connected_components(graph, "weak"), (0, 0, 0, 0))
        self.assertEqual(connected_components(graph, "strong"), (0, 0, 0, 1))

    def test_weak_component_traversal_scans_every_node_without_a_target(self) -> None:
        graph = Graph(
            (Point(2, 2), Point(20, 2), Point(38, 2), Point(56, 2)),
            (Edge(0, 0, 1, 1), Edge(1, 2, 3, 1)),
            False,
            0,
            1,
        )
        for step_mode in ("edge", "node"):
            with self.subTest(step_mode=step_mode):
                frames = list(traversal_frames(graph, "components", step_mode, "weak"))
                self.assertEqual(frames[0].component_ids, (-1, -1, -1, -1))
                self.assertEqual(frames[-1].component_ids, (0, 0, 1, 1))
                self.assertEqual(frames[-1].visited_nodes, frozenset(range(4)))
                self.assertTrue(frames[-1].complete)
                self.assertFalse(frames[-1].target_reached)
                self.assertTrue(any(frame.current_node == 2 for frame in frames))

    def test_component_traversal_classifies_isolated_vertices(self) -> None:
        graph = Graph(
            (Point(2, 2), Point(20, 2), Point(38, 2)),
            (),
            False,
            0,
            2,
        )
        frames = list(traversal_frames(graph, "components", "edge", "weak"))
        self.assertEqual(frames[-1].component_ids, (0, 1, 2))
        examined_roots = {
            frame.current_node for frame in frames if frame.current_node is not None
        }
        self.assertEqual(examined_roots, {0, 1, 2})

    def test_strong_component_traversal_uses_the_mathematical_partition(self) -> None:
        graph = Graph(
            (Point(2, 2), Point(20, 2), Point(38, 2), Point(56, 2)),
            (
                Edge(0, 0, 1, 1),
                Edge(1, 1, 0, 1),
                Edge(2, 1, 2, 1),
                Edge(3, 2, 3, 1),
                Edge(4, 3, 2, 1),
            ),
            True,
            0,
            3,
        )
        frames = list(traversal_frames(graph, "components", "edge", "strong"))
        self.assertEqual(frames[-1].component_ids, connected_components(graph, "strong"))
        self.assertEqual(frames[-1].component_ids, (0, 0, 1, 1))
        self.assertEqual(frames[-1].visited_nodes, frozenset(range(4)))
        self.assertFalse(frames[-1].target_reached)

    def test_all_methods_finish_and_edge_mode_exposes_current_edges(self) -> None:
        graph = generate_graph(GraphOptions(n=12, allow_cycles=True, seed=4))
        for method in ("bfs", "dfs", "dijkstra"):
            with self.subTest(method=method):
                frames = list(traversal_frames(graph, method, "edge"))
                self.assertIsNone(frames[0].current_node)
                self.assertIsNone(frames[0].examined_edge)
                self.assertFalse(frames[0].traversed_edges)
                self.assertTrue(frames[-1].complete)
                self.assertTrue(frames[-1].target_reached)
                self.assertTrue(any(frame.examined_edge is not None for frame in frames))

    def test_node_mode_has_no_examined_edge_frames(self) -> None:
        graph = generate_graph(GraphOptions(n=8, seed=8))
        for method in ("bfs", "dfs", "dijkstra"):
            frames = list(traversal_frames(graph, method, "node"))
            self.assertTrue(all(frame.examined_edge is None for frame in frames))

    def test_parallel_scheduler_holds_the_earlier_finisher(self) -> None:
        graph = Graph(
            (Point(2, 2), Point(12, 2), Point(22, 2), Point(12, 16)),
            (
                Edge(0, 0, 1, 1),
                Edge(1, 0, 3, 1),
                Edge(2, 1, 2, 1),
            ),
            False,
            0,
            2,
        )
        bfs = list(traversal_frames(graph, "bfs", "edge"))
        dfs = list(traversal_frames(graph, "dfs", "edge"))
        paired = list(parallel_traversal_frames(graph, "bfs", "dfs", "edge"))
        self.assertEqual(len(paired), max(len(bfs), len(dfs)))
        for index, (left, right) in enumerate(paired):
            self.assertEqual(left, bfs[min(index, len(bfs) - 1)])
            self.assertEqual(right, dfs[min(index, len(dfs) - 1)])
        self.assertTrue(paired[-1][0].complete)
        self.assertTrue(paired[-1][1].complete)

    def test_prim_and_kruskal_build_minimum_trees_with_equal_weight(self) -> None:
        graph = self.mst_graph()
        prim = list(traversal_frames(graph, "prim", "edge"))
        kruskal = list(traversal_frames(graph, "kruskal", "edge"))
        self.assertEqual(prim[-1].accepted_edges, frozenset({0, 1, 3}))
        self.assertEqual(kruskal[-1].accepted_edges, frozenset({0, 1, 3}))
        self.assertEqual(prim[-1].total_weight, 5)
        self.assertEqual(kruskal[-1].total_weight, 5)
        self.assertEqual(kruskal[-1].rejected_edges, frozenset({2}))
        self.assertEqual(prim[-1].tree_nodes, frozenset(range(4)))
        self.assertEqual(kruskal[-1].tree_nodes, frozenset(range(4)))
        self.assertTrue(prim[-1].complete)
        self.assertTrue(kruskal[-1].complete)
        self.assertFalse(prim[-1].target_reached)
        self.assertFalse(kruskal[-1].target_reached)

    def test_mst_ties_are_broken_by_edge_index(self) -> None:
        graph = Graph(
            (Point(2, 2), Point(20, 2), Point(20, 20)),
            (
                Edge(0, 0, 1, 1),
                Edge(1, 0, 2, 1),
                Edge(2, 1, 2, 1),
            ),
            False,
            0,
            2,
        )
        prim = list(traversal_frames(graph, "prim", "node"))[-1]
        kruskal = list(traversal_frames(graph, "kruskal", "node"))[-1]
        self.assertEqual(prim.accepted_edges, frozenset({0, 1}))
        self.assertEqual(kruskal.accepted_edges, frozenset({0, 1}))

    def test_prim_kruskal_split_holds_and_finishes_with_equal_weight(self) -> None:
        paired = list(
            parallel_traversal_frames(self.mst_graph(), "prim", "kruskal", "edge")
        )
        self.assertTrue(any(left.complete and not right.complete for left, right in paired))
        self.assertTrue(paired[-1][0].complete)
        self.assertTrue(paired[-1][1].complete)
        self.assertEqual(paired[-1][0].total_weight, paired[-1][1].total_weight)

    def test_seeded_graphs_give_prim_and_kruskal_the_same_minimum_weight(self) -> None:
        for seed in range(12):
            with self.subTest(seed=seed):
                graph = generate_graph(
                    GraphOptions(n=12, allow_cycles=True, seed=seed, traversal="prim")
                )
                prim = list(traversal_frames(graph, "prim", "edge"))[-1]
                kruskal = list(traversal_frames(graph, "kruskal", "edge"))[-1]
                self.assertEqual(len(prim.accepted_edges), 11)
                self.assertEqual(len(kruskal.accepted_edges), 11)
                self.assertEqual(prim.total_weight, kruskal.total_weight)

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

    def test_dijkstra_replaces_predecessor_without_forgetting_exploration(self) -> None:
        graph = Graph(
            (Point(2, 2), Point(20, 2), Point(40, 2)),
            (Edge(0, 0, 1, 1), Edge(1, 0, 2, 5), Edge(2, 1, 2, 1)),
            False,
            0,
            2,
        )
        frames = list(traversal_frames(graph, "dijkstra", "edge"))
        relaxed = next(frame for frame in frames if frame.examined_edge == 2)
        self.assertEqual(relaxed.predecessor_edges, frozenset({0, 2}))
        self.assertEqual(relaxed.traversed_edges, frozenset({0, 1, 2}))
        self.assertNotIn(1, relaxed.predecessor_edges)

    def test_dijkstra_does_not_reexamine_edges_to_settled_nodes(self) -> None:
        graph = Graph(
            (Point(2, 2), Point(20, 2), Point(40, 2)),
            (Edge(0, 0, 1, 1), Edge(1, 1, 2, 1)),
            False,
            0,
            2,
        )
        frames = list(traversal_frames(graph, "dijkstra", "edge"))
        examined = [frame.examined_edge for frame in frames if frame.examined_edge is not None]
        self.assertEqual(examined, [0, 1])


class GraphRenderingTests(unittest.TestCase):
    def test_explicit_full_viewport_is_pixel_identical_to_default_rendering(self) -> None:
        config = load_render_config()
        graph = generate_graph(GraphOptions(n=8, allow_cycles=True, seed=22))
        frame = list(traversal_frames(graph, "bfs", "edge"))[2]
        default_canvas = RecordingCanvas()
        explicit_canvas = RecordingCanvas()
        renderer = GraphRenderer(config, "bfs")
        renderer.render(default_canvas, graph, frame)
        renderer.render(
            explicit_canvas,
            graph,
            frame,
            viewport=Viewport(0, 0, 64, 64),
        )
        self.assertEqual(explicit_canvas.pixels, default_canvas.pixels)

    def test_completed_mst_keeps_only_tree_edges_highlighted(self) -> None:
        config = load_render_config()
        graph = TraversalTests.mst_graph()
        complete = list(traversal_frames(graph, "kruskal", "edge"))[-1]
        renderer = GraphRenderer(config, "kruskal")
        empty = TraversalFrame(
            None,
            None,
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
        )
        for edge in graph.edges:
            color = renderer._edge_color(edge, complete)
            if edge.index in complete.accepted_edges:
                self.assertEqual(color, config.colors.mst_accepted_edge)
            else:
                self.assertEqual(color, renderer._edge_color(edge, empty))
                self.assertNotEqual(color, config.colors.mst_rejected_edge)

    def test_mst_endpoint_semantics_match_each_algorithm(self) -> None:
        config = load_render_config()
        graph = Graph(
            (Point(5, 5), Point(20, 5), Point(35, 5)),
            (),
            False,
            0,
            2,
        )
        empty = TraversalFrame(
            None,
            None,
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
        )
        prim_canvas = RecordingCanvas()
        GraphRenderer(config, "prim").render(prim_canvas, graph, empty)
        self.assertEqual(prim_canvas.pixels[(5, 5)], config.colors.start_node)
        self.assertEqual(prim_canvas.pixels[(35, 5)], config.colors.node)
        self.assertIn((6, 6), prim_canvas.pixels)
        self.assertNotIn((36, 6), prim_canvas.pixels)

        kruskal_canvas = RecordingCanvas()
        GraphRenderer(config, "kruskal").render(kruskal_canvas, graph, empty)
        self.assertEqual(kruskal_canvas.pixels[(5, 5)], config.colors.node)
        self.assertEqual(kruskal_canvas.pixels[(35, 5)], config.colors.node)
        self.assertNotIn((6, 6), kruskal_canvas.pixels)
        self.assertNotIn((36, 6), kruskal_canvas.pixels)

    def test_mst_accepted_edges_draw_above_rejected_edges(self) -> None:
        config = load_render_config()
        graph = Graph(
            (Point(5, 5), Point(20, 5), Point(20, 6)),
            (Edge(0, 0, 1, 1), Edge(1, 0, 2, 2)),
            False,
            0,
            2,
        )
        frame = TraversalFrame(
            None,
            None,
            frozenset({0, 1}),
            frozenset(),
            frozenset({0, 1}),
            frozenset(),
            frozenset(),
            accepted_edges=frozenset({0}),
            rejected_edges=frozenset({1}),
            tree_nodes=frozenset({0, 1}),
        )
        canvas = RecordingCanvas()
        renderer = GraphRenderer(config, "kruskal")
        renderer.render(canvas, graph, frame)
        self.assertEqual(renderer._edge_color(graph.edges[1], frame), config.colors.mst_rejected_edge)
        self.assertEqual(canvas.pixels[(10, 5)], config.colors.mst_accepted_edge)

    def test_split_viewports_are_exact_translations_and_do_not_cross_the_seam(self) -> None:
        config = load_render_config()
        graph = Graph(
            (Point(3, 4), Point(20, 4), Point(31, 20)),
            (Edge(0, 0, 1, 1), Edge(1, 1, 2, 1)),
            False,
            0,
            2,
        )
        frame = TraversalFrame(
            1,
            1,
            frozenset({0, 1}),
            frozenset({2}),
            frozenset({0, 1}),
            frozenset(),
            frozenset(),
        )
        left = RecordingCanvas()
        right = RecordingCanvas()
        renderer = GraphRenderer(config, "bfs")
        renderer.render(left, graph, frame, viewport=Viewport(0, 0, 32, 64))
        renderer.render(right, graph, frame, viewport=Viewport(32, 0, 32, 64))
        translated_left = {(x + 32, y): color for (x, y), color in left.pixels.items()}
        self.assertEqual(right.pixels, translated_left)
        self.assertTrue(all(x < 32 for x, _ in left.pixels))
        self.assertTrue(all(x >= 32 for x, _ in right.pixels))

    def test_component_colors_apply_only_to_ordinary_inactive_nodes(self) -> None:
        config = load_render_config()
        self.assertGreaterEqual(len(config.component_colors), 32)
        graph = Graph(
            (Point(5, 5), Point(20, 5), Point(35, 5), Point(50, 5)),
            (),
            False,
            0,
            2,
        )
        frame = TraversalFrame(
            None,
            None,
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
        )
        canvas = RecordingCanvas()
        frame = TraversalFrame(
            frame.current_node,
            frame.examined_edge,
            frame.visited_nodes,
            frame.frontier_nodes,
            frame.traversed_edges,
            frame.cycle_edges,
            frame.final_path_edges,
            component_ids=(0, 0, 1, 1),
        )
        GraphRenderer(config, "components").render(canvas, graph, frame)
        self.assertEqual(canvas.pixels[(5, 5)], config.component_colors[0])
        self.assertEqual(canvas.pixels[(20, 5)], config.component_colors[0])
        self.assertEqual(canvas.pixels[(35, 5)], config.component_colors[1])
        self.assertEqual(canvas.pixels[(50, 5)], config.component_colors[1])
        self.assertNotIn(config.colors.start_node, canvas.pixels.values())
        self.assertNotIn(config.colors.target_node, canvas.pixels.values())
        self.assertNotIn((6, 6), canvas.pixels)
        self.assertNotIn((36, 6), canvas.pixels)

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
        self.assertEqual(config.endpoint_node_size, 2)
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
        self.assertEqual(canvas.pixels[(6, 5)], config.colors.start_node)
        self.assertEqual(canvas.pixels[(5, 6)], config.colors.start_node)
        self.assertEqual(canvas.pixels[(6, 6)], config.colors.start_node)
        self.assertEqual(canvas.pixels[(20, 5)], config.colors.current_node)
        self.assertEqual(canvas.pixels[(35, 5)], config.colors.target_node)
        self.assertEqual(canvas.pixels[(36, 6)], config.colors.target_node)

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

    def test_predecessor_tree_is_drawn_above_overlapping_explored_edges(self) -> None:
        config = load_render_config()
        graph = Graph(
            (Point(5, 5), Point(20, 5), Point(20, 6)),
            (Edge(0, 0, 1, 1), Edge(1, 0, 2, 9)),
            False,
            0,
            2,
        )
        frame = TraversalFrame(
            None,
            None,
            frozenset({0}),
            frozenset({1, 2}),
            frozenset({0, 1}),
            frozenset(),
            frozenset(),
            predecessor_edges=frozenset({0}),
        )
        canvas = RecordingCanvas()
        renderer = GraphRenderer(config, "dijkstra")
        renderer.render(canvas, graph, frame)
        self.assertEqual(canvas.pixels[(10, 5)], config.colors.predecessor_edge)

        current = TraversalFrame(
            0,
            1,
            frame.visited_nodes,
            frame.frontier_nodes,
            frame.traversed_edges,
            frame.cycle_edges,
            frame.final_path_edges,
            predecessor_edges=frame.predecessor_edges,
        )
        renderer.render(canvas, graph, current)
        self.assertEqual(canvas.pixels[(10, 5)], config.colors.current_edge)

    def test_directed_edge_uses_configured_orange_pixels_nearest_destination(self) -> None:
        config = load_render_config()
        self.assertEqual(config.direction_marker_pixels, 2)
        graph = Graph(
            (Point(5, 5), Point(20, 5)),
            (Edge(0, 0, 1, 1),),
            True,
            0,
            1,
        )
        frame = TraversalFrame(
            None,
            None,
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
        )
        canvas = RecordingCanvas()
        GraphRenderer(config, "bfs").render(canvas, graph, frame)
        orange_pixels = {
            point for point, color in canvas.pixels.items()
            if color == config.colors.direction_marker
        }
        self.assertEqual(orange_pixels, {(18, 5), (19, 5)})
        self.assertEqual(canvas.pixels[(20, 5)], config.colors.target_node)

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
            predecessor_edges=frozenset({0, 1, 2}),
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
                "display_mode": "bfs_dfs_split",
                "traverse": "components",
                "allow_cycles": True,
                "directed": True,
                "directed_connectivity": "strong",
                "component_type": "strong",
            }
        )
        argv = definition.build_argv("/fixed/python", values)
        app = NativeMatrixApp()
        for parameter in application_parameters("graph_traversal"):
            parameter.add_to_argparse(app.parser)
        parsed = app.parser.parse_args(argv[2:])
        self.assertEqual(parsed.n, 18)
        self.assertEqual(parsed.display_mode, "bfs_dfs_split")
        self.assertEqual(parsed.traverse, "components")
        self.assertTrue(parsed.allow_cycles)
        self.assertTrue(parsed.ensure_path)
        self.assertEqual(parsed.directed_connectivity, "strong")
        self.assertEqual(parsed.component_type, "strong")

    def test_mst_split_managed_argv_is_accepted(self) -> None:
        definition = build_default_registry().get("graph_traversal")
        values = definition.validate_parameters(
            {"display_mode": "prim_kruskal_split", "allow_cycles": True}
        )
        argv = definition.build_argv("/fixed/python", values)
        app = NativeMatrixApp()
        for parameter in application_parameters("graph_traversal"):
            parameter.add_to_argparse(app.parser)
        parsed = app.parser.parse_args(argv[2:])
        self.assertEqual(parsed.display_mode, "prim_kruskal_split")
        self.assertTrue(parsed.connected)
        self.assertFalse(parsed.directed)


if __name__ == "__main__":
    unittest.main()
