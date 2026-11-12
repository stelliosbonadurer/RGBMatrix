"""Planar graph generation and step-by-step traversal state."""

from __future__ import annotations

import heapq
import math
import random
from collections import deque
from dataclasses import dataclass
from typing import Iterator, Literal


TraversalMethod = Literal["bfs", "dfs", "dijkstra"]
StepMode = Literal["node", "edge"]
DirectedConnectivity = Literal["strong", "weak", "none"]
ComponentMode = Literal["off", "weak", "strong"]


@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True)
class Edge:
    index: int
    source: int
    target: int
    weight: int


@dataclass(frozen=True)
class Graph:
    points: tuple[Point, ...]
    edges: tuple[Edge, ...]
    directed: bool
    start: int
    target: int


@dataclass(frozen=True)
class GraphOptions:
    n: int = 12
    e: int = 0
    traversal: TraversalMethod = "bfs"
    seed: int = 0
    connected: bool = True
    allow_cycles: bool = False
    directed: bool = False
    directed_connectivity: DirectedConnectivity = "weak"
    component_mode: ComponentMode = "off"
    step_mode: StepMode = "edge"


@dataclass(frozen=True)
class EdgeBounds:
    minimum: int
    maximum: int
    automatic: int


@dataclass(frozen=True)
class TraversalFrame:
    current_node: int | None
    examined_edge: int | None
    visited_nodes: frozenset[int]
    frontier_nodes: frozenset[int]
    traversed_edges: frozenset[int]
    cycle_edges: frozenset[int]
    final_path_edges: frozenset[int]
    complete: bool = False
    target_reached: bool = False
    predecessor_edges: frozenset[int] = frozenset()


def edge_bounds(options: GraphOptions) -> EdgeBounds:
    """Return dependent edge limits and the value represented by ``e=0``."""

    if options.n < 2:
        raise ValueError("n must be at least 2")
    if options.n > 32:
        raise ValueError("n must be at most 32 on a 64x64 display")

    strong = options.directed and options.directed_connectivity == "strong"
    guaranteed = options.connected if not options.directed else (
        options.directed_connectivity in {"strong", "weak"}
    )
    if strong and not options.allow_cycles:
        raise ValueError("strongly connected directed graphs require allow_cycles=true")
    if strong and options.n < 3:
        raise ValueError("strong connectivity requires at least 3 nodes in this simple planar graph")

    if options.allow_cycles or options.directed:
        maximum = 1 if options.n == 2 else 3 * options.n - 6
        minimum = options.n if strong else (options.n - 1 if guaranteed else 0)
        automatic = min(max(math.ceil(options.n * 1.5), minimum), maximum)
    else:
        maximum = options.n - 1
        minimum = options.n - 1 if guaranteed else 0
        # An undirected acyclic graph can never use the usual 1.5n default.
        automatic = options.n - 1
    return EdgeBounds(minimum, maximum, automatic)


def resolve_edge_count(options: GraphOptions) -> int:
    bounds = edge_bounds(options)
    edge_count = bounds.automatic if options.e == 0 else options.e
    if edge_count < bounds.minimum:
        raise ValueError(
            f"e must be at least {bounds.minimum} for the selected connectivity "
            f"(received {edge_count})"
        )
    if edge_count > bounds.maximum:
        reason = "acyclic" if not options.allow_cycles else "strictly planar"
        raise ValueError(
            f"e must be at most {bounds.maximum} for a {reason} graph with "
            f"n={options.n} (received {edge_count})"
        )
    return edge_count


def validate_options(options: GraphOptions) -> int:
    if options.traversal not in {"bfs", "dfs", "dijkstra"}:
        raise ValueError("traversal must be bfs, dfs, or dijkstra")
    if options.step_mode not in {"node", "edge"}:
        raise ValueError("step_mode must be node or edge")
    if options.directed_connectivity not in {"strong", "weak", "none"}:
        raise ValueError("directed_connectivity must be strong, weak, or none")
    if options.component_mode not in {"off", "weak", "strong"}:
        raise ValueError("component_mode must be off, weak, or strong")
    if options.component_mode == "strong" and not options.directed:
        raise ValueError("strong component detection requires directed=true")
    if options.e < 0:
        raise ValueError("e must be zero (automatic) or a positive integer")
    return resolve_edge_count(options)


class _DisjointSet:
    def __init__(self, size: int):
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> bool:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return False
        self.parent[right_root] = left_root
        return True


def generate_graph(options: GraphOptions, width: int = 64, height: int = 64) -> Graph:
    """Generate a reproducible straight-line planar graph."""

    edge_count = validate_options(options)
    if width < 16 or height < 16:
        raise ValueError("graph traversal requires a matrix of at least 16x16")

    for layout_attempt in range(100):
        rng = random.Random(options.seed + layout_attempt * 104_729)
        points = _place_points(options.n, width, height, rng)
        try:
            pairs, strong_cycle = _select_planar_edges(points, edge_count, options, rng)
        except ValueError:
            continue

        edges: list[Edge] = []
        strong_orientations = {
            frozenset((source, target)): (source, target)
            for source, target in strong_cycle
        }
        topological_order = list(range(options.n))
        rng.shuffle(topological_order)
        topological_rank = {node: rank for rank, node in enumerate(topological_order)}
        for index, (left, right) in enumerate(pairs):
            if options.directed:
                orientation = strong_orientations.get(frozenset((left, right)))
                if orientation is None:
                    if not options.allow_cycles:
                        orientation = (
                            (left, right)
                            if topological_rank[left] < topological_rank[right]
                            else (right, left)
                        )
                    else:
                        orientation = (left, right) if rng.random() < 0.5 else (right, left)
                source, target = orientation
            else:
                source, target = left, right
            edges.append(Edge(index, source, target, rng.randint(1, 9)))

        start, target = _farthest_pair(points)
        return Graph(tuple(points), tuple(edges), options.directed, start, target)

    raise ValueError(
        "could not place the requested planar graph after 100 layouts; "
        "reduce n or e, or change the seed"
    )


def _place_points(n: int, width: int, height: int, rng: random.Random) -> list[Point]:
    margin = 3
    usable_width = width - 2 * margin
    usable_height = height - 2 * margin
    columns = max(1, math.ceil(math.sqrt(n * usable_width / usable_height)))
    rows = math.ceil(n / columns)
    cells = [(column, row) for row in range(rows) for column in range(columns)]
    rng.shuffle(cells)

    points: list[Point] = []
    cell_width = usable_width / columns
    cell_height = usable_height / rows
    jitter_x = max(0, int(cell_width * 0.24))
    jitter_y = max(0, int(cell_height * 0.24))
    for column, row in cells[:n]:
        x = round(margin + (column + 0.5) * cell_width)
        y = round(margin + (row + 0.5) * cell_height)
        x += rng.randint(-jitter_x, jitter_x)
        y += rng.randint(-jitter_y, jitter_y)
        points.append(Point(min(width - margin - 1, max(margin, x)), min(height - margin - 1, max(margin, y))))
    if len(set(points)) != n:
        raise ValueError("layout produced overlapping nodes")
    return points


def _select_planar_edges(
    points: list[Point],
    edge_count: int,
    options: GraphOptions,
    rng: random.Random,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    candidates = [
        (left, right)
        for left in range(len(points))
        for right in range(left + 1, len(points))
        if _edge_clear_of_nodes(points, left, right)
    ]
    rng.shuffle(candidates)
    candidates.sort(key=lambda pair: _distance_squared(points[pair[0]], points[pair[1]]))

    selected: list[tuple[int, int]] = []
    selected_keys: set[frozenset[int]] = set()
    strong_cycle: list[tuple[int, int]] = []
    is_strong = options.directed and options.directed_connectivity == "strong"

    if is_strong:
        center_x = sum(point.x for point in points) / len(points)
        center_y = sum(point.y for point in points) / len(points)
        order = sorted(
            range(len(points)),
            key=lambda index: math.atan2(points[index].y - center_y, points[index].x - center_x),
        )
        strong_cycle = [
            (order[index], order[(index + 1) % len(order)])
            for index in range(len(order))
        ]
        for source, target in strong_cycle:
            pair = (min(source, target), max(source, target))
            if not _can_add_edge(points, pair, selected):
                raise ValueError("strong cycle does not fit this layout")
            selected.append(pair)
            selected_keys.add(frozenset(pair))
    else:
        guaranteed = options.connected if not options.directed else (
            options.directed_connectivity == "weak"
        )
        disjoint = _DisjointSet(len(points))
        if guaranteed:
            for pair in candidates:
                if disjoint.find(pair[0]) == disjoint.find(pair[1]):
                    continue
                if not _can_add_edge(points, pair, selected):
                    continue
                disjoint.union(*pair)
                selected.append(pair)
                selected_keys.add(frozenset(pair))
                if len(selected) == len(points) - 1:
                    break
            if len(selected) != len(points) - 1:
                raise ValueError("could not build a planar spanning tree")

    disjoint = _DisjointSet(len(points))
    for pair in selected:
        disjoint.union(*pair)
    for pair in candidates:
        if len(selected) >= edge_count:
            break
        if frozenset(pair) in selected_keys:
            continue
        if (
            not options.allow_cycles
            and not options.directed
            and disjoint.find(pair[0]) == disjoint.find(pair[1])
        ):
            continue
        if not _can_add_edge(points, pair, selected):
            continue
        selected.append(pair)
        selected_keys.add(frozenset(pair))
        disjoint.union(*pair)

    if len(selected) != edge_count:
        raise ValueError("not enough non-crossing edges fit this layout")
    return selected, strong_cycle


def _distance_squared(left: Point, right: Point) -> int:
    return (left.x - right.x) ** 2 + (left.y - right.y) ** 2


def _orientation(a: Point, b: Point, c: Point) -> int:
    value = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
    return (value > 0) - (value < 0)


def _edge_clear_of_nodes(points: list[Point], left: int, right: int) -> bool:
    a, b = points[left], points[right]
    for index, point in enumerate(points):
        if index not in {left, right} and _orientation(a, b, point) == 0:
            if min(a.x, b.x) <= point.x <= max(a.x, b.x) and min(a.y, b.y) <= point.y <= max(a.y, b.y):
                return False
    return True


def _segments_cross(a: Point, b: Point, c: Point, d: Point) -> bool:
    return (
        _orientation(a, b, c) * _orientation(a, b, d) < 0
        and _orientation(c, d, a) * _orientation(c, d, b) < 0
    )


def _can_add_edge(
    points: list[Point], pair: tuple[int, int], selected: list[tuple[int, int]]
) -> bool:
    if not _edge_clear_of_nodes(points, *pair):
        return False
    for existing in selected:
        if set(pair) & set(existing):
            continue
        if _segments_cross(points[pair[0]], points[pair[1]], points[existing[0]], points[existing[1]]):
            return False
    return True


def _farthest_pair(points: list[Point]) -> tuple[int, int]:
    return max(
        ((left, right) for left in range(len(points)) for right in range(left + 1, len(points))),
        key=lambda pair: _distance_squared(points[pair[0]], points[pair[1]]),
    )


def traversal_frames(graph: Graph, method: TraversalMethod, step_mode: StepMode) -> Iterator[TraversalFrame]:
    if method == "bfs":
        yield from _bfs_frames(graph, step_mode)
    elif method == "dfs":
        yield from _dfs_frames(graph, step_mode)
    elif method == "dijkstra":
        yield from _dijkstra_frames(graph, step_mode)
    else:
        raise ValueError(f"unsupported traversal method: {method}")


def _adjacency(graph: Graph) -> list[list[tuple[int, Edge]]]:
    adjacent: list[list[tuple[int, Edge]]] = [[] for _ in graph.points]
    for edge in graph.edges:
        adjacent[edge.source].append((edge.target, edge))
        if not graph.directed:
            adjacent[edge.target].append((edge.source, edge))
    for neighbors in adjacent:
        neighbors.sort(key=lambda item: item[1].index)
    return adjacent


def connected_components(graph: Graph, mode: ComponentMode) -> tuple[int, ...]:
    """Return a stable component index for every node.

    Weak components ignore edge direction. Strong components use Kosaraju's
    algorithm and are available only for directed graphs. Component numbers are
    normalized by their lowest node index so seeded graphs keep stable colors.
    """

    if mode == "off":
        return tuple(-1 for _ in graph.points)
    if mode not in {"weak", "strong"}:
        raise ValueError("component mode must be off, weak, or strong")
    if mode == "strong" and not graph.directed:
        raise ValueError("strong component detection requires a directed graph")

    outgoing: list[list[int]] = [[] for _ in graph.points]
    reverse: list[list[int]] = [[] for _ in graph.points]
    for edge in graph.edges:
        outgoing[edge.source].append(edge.target)
        reverse[edge.target].append(edge.source)
        if mode == "weak" or not graph.directed:
            outgoing[edge.target].append(edge.source)
            reverse[edge.source].append(edge.target)
    for neighbors in (*outgoing, *reverse):
        neighbors.sort()

    if mode == "weak" or not graph.directed:
        groups = _component_groups(outgoing)
    else:
        visited: set[int] = set()
        finish_order: list[int] = []

        def finish(node: int) -> None:
            visited.add(node)
            for neighbor in outgoing[node]:
                if neighbor not in visited:
                    finish(neighbor)
            finish_order.append(node)

        for node in range(len(graph.points)):
            if node not in visited:
                finish(node)

        visited.clear()
        groups: list[set[int]] = []

        def collect(node: int, group: set[int]) -> None:
            visited.add(node)
            group.add(node)
            for neighbor in reverse[node]:
                if neighbor not in visited:
                    collect(neighbor, group)

        for node in reversed(finish_order):
            if node not in visited:
                group: set[int] = set()
                collect(node, group)
                groups.append(group)

    groups.sort(key=min)
    component_ids = [-1] * len(graph.points)
    for component_id, group in enumerate(groups):
        for node in group:
            component_ids[node] = component_id
    return tuple(component_ids)


def _component_groups(adjacency: list[list[int]]) -> list[set[int]]:
    groups: list[set[int]] = []
    visited: set[int] = set()
    for first in range(len(adjacency)):
        if first in visited:
            continue
        group = {first}
        visited.add(first)
        pending = [first]
        while pending:
            for neighbor in adjacency[pending.pop()]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    group.add(neighbor)
                    pending.append(neighbor)
        groups.append(group)
    return groups


def _frame(
    current: int | None,
    examined: int | None,
    visited: set[int],
    frontier: set[int],
    traversed: set[int],
    cycles: set[int],
    final_path: set[int] | None = None,
    *,
    predecessors: set[int] | None = None,
    complete: bool = False,
    target_reached: bool = False,
) -> TraversalFrame:
    return TraversalFrame(
        current,
        examined,
        frozenset(visited),
        frozenset(frontier),
        frozenset(traversed),
        frozenset(cycles),
        frozenset(final_path or ()),
        complete=complete,
        target_reached=target_reached,
        predecessor_edges=frozenset(predecessors or ()),
    )


def _path_edges(parent_edge: dict[int, int], parent_node: dict[int, int], target: int, start: int) -> set[int]:
    path: set[int] = set()
    current = target
    while current != start and current in parent_edge:
        path.add(parent_edge[current])
        current = parent_node[current]
    return path if current == start else set()


def _bfs_frames(graph: Graph, step_mode: StepMode) -> Iterator[TraversalFrame]:
    adjacent = _adjacency(graph)
    queue = deque([graph.start])
    discovered = {graph.start}
    visited: set[int] = set()
    traversed: set[int] = set()
    parent_edge: dict[int, int] = {}
    parent_node: dict[int, int] = {}
    yield _frame(None, None, visited, set(queue), traversed, set())

    reached = False
    while queue and not reached:
        current = queue.popleft()
        visited.add(current)
        if current == graph.target:
            reached = True
            break
        for neighbor, edge in adjacent[current]:
            traversed.add(edge.index)
            if neighbor not in discovered:
                discovered.add(neighbor)
                parent_edge[neighbor] = edge.index
                parent_node[neighbor] = current
                queue.append(neighbor)
            if step_mode == "edge":
                yield _frame(current, edge.index, visited, set(queue), traversed, set())
            if neighbor == graph.target:
                reached = True
                break
        if step_mode == "node":
            yield _frame(current, None, visited, set(queue), traversed, set())

    if reached:
        visited.add(graph.target)
    yield _frame(None, None, visited, set(queue), traversed, set(), complete=True, target_reached=reached)


def _dfs_frames(graph: Graph, step_mode: StepMode) -> Iterator[TraversalFrame]:
    adjacent = _adjacency(graph)
    visited: set[int] = set()
    active: list[int] = []
    traversed: set[int] = set()
    cycles: set[int] = set()
    parent_edge: dict[int, int] = {}
    parent_node: dict[int, int] = {}
    reached = False

    yield _frame(None, None, visited, {graph.start}, traversed, cycles)

    def visit(current: int) -> Iterator[TraversalFrame]:
        nonlocal reached
        visited.add(current)
        active.append(current)
        if step_mode == "node":
            yield _frame(current, None, visited, set(active), traversed, cycles)
        if current == graph.target:
            reached = True
            active.pop()
            return
        for neighbor, edge in adjacent[current]:
            if reached:
                break
            if not graph.directed and parent_edge.get(current) == edge.index:
                continue
            traversed.add(edge.index)
            if neighbor not in visited:
                parent_edge[neighbor] = edge.index
                parent_node[neighbor] = current
                if step_mode == "edge":
                    yield _frame(current, edge.index, visited, {neighbor}, traversed, cycles)
                yield from visit(neighbor)
            elif neighbor in active:
                cycle_start = active.index(neighbor)
                cycles.add(edge.index)
                for cycle_node in active[cycle_start + 1 :]:
                    cycles.add(parent_edge[cycle_node])
                if step_mode == "edge":
                    yield _frame(current, edge.index, visited, set(active), traversed, cycles)
        if active and active[-1] == current:
            active.pop()

    yield from visit(graph.start)
    yield _frame(None, None, visited, set(), traversed, cycles, complete=True, target_reached=reached)


def _dijkstra_frames(graph: Graph, step_mode: StepMode) -> Iterator[TraversalFrame]:
    adjacent = _adjacency(graph)
    distances = [math.inf] * len(graph.points)
    distances[graph.start] = 0
    queue: list[tuple[float, int]] = [(0, graph.start)]
    settled: set[int] = set()
    traversed: set[int] = set()
    parent_edge: dict[int, int] = {}
    parent_node: dict[int, int] = {}
    reached = False
    yield _frame(None, None, settled, {graph.start}, traversed, set())

    while queue:
        distance, current = heapq.heappop(queue)
        if current in settled or distance != distances[current]:
            continue
        settled.add(current)
        if current == graph.target:
            reached = True
            break
        for neighbor, edge in adjacent[current]:
            # With nonnegative weights, a settled node cannot be improved. In an
            # undirected graph this also avoids visually examining every tree edge
            # a second time in the reverse direction.
            if neighbor in settled:
                continue
            traversed.add(edge.index)
            candidate = distance + edge.weight
            if candidate < distances[neighbor]:
                distances[neighbor] = candidate
                parent_edge[neighbor] = edge.index
                parent_node[neighbor] = current
                heapq.heappush(queue, (candidate, neighbor))
            if step_mode == "edge":
                yield _frame(
                    current,
                    edge.index,
                    settled,
                    {item[1] for item in queue},
                    traversed,
                    set(),
                    predecessors=set(parent_edge.values()),
                )
        if step_mode == "node":
            yield _frame(
                current,
                None,
                settled,
                {item[1] for item in queue},
                traversed,
                set(),
                predecessors=set(parent_edge.values()),
            )

    path = _path_edges(parent_edge, parent_node, graph.target, graph.start) if reached else set()
    yield _frame(
        None,
        None,
        settled,
        set(),
        traversed,
        set(),
        path,
        predecessors=set(parent_edge.values()),
        complete=True,
        target_reached=reached,
    )
