"""Pixel renderer and editable color configuration for graph traversal."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .graph_model import Edge, Graph, Point, TraversalFrame


Color = tuple[int, int, int]
DEFAULT_CONFIG_PATH = Path(__file__).with_name("graph_traversal_config.json")


@dataclass(frozen=True)
class GraphColors:
    background: Color
    node: Color
    start_node: Color
    target_node: Color
    current_node: Color
    unvisited_edge: Color
    traversed_edge: Color
    predecessor_edge: Color
    current_edge: Color
    direction_marker: Color
    cycle_edge: Color
    shortest_path: Color
    dijkstra_low_weight: Color
    dijkstra_high_weight: Color


@dataclass(frozen=True)
class RenderConfig:
    node_size: int
    endpoint_node_size: int
    direction_marker_pixels: int
    component_colors: tuple[Color, ...]
    colors: GraphColors


def load_render_config(path: Path = DEFAULT_CONFIG_PATH) -> RenderConfig:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"could not read graph rendering config: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in graph rendering config: {exc}") from exc
    if set(data) != {
        "node_size",
        "endpoint_node_size",
        "direction_marker_pixels",
        "component_colors",
        "colors",
    }:
        raise ValueError(
            "graph rendering config must contain only node_size, endpoint_node_size, "
            "direction_marker_pixels, component_colors, and colors"
        )
    node_size = data["node_size"]
    if type(node_size) is not int or not 1 <= node_size <= 2:
        raise ValueError("node_size must be 1 or 2")
    endpoint_node_size = data["endpoint_node_size"]
    if type(endpoint_node_size) is not int or not 1 <= endpoint_node_size <= 2:
        raise ValueError("endpoint_node_size must be 1 or 2")
    direction_marker_pixels = data["direction_marker_pixels"]
    if (
        type(direction_marker_pixels) is not int
        or not 1 <= direction_marker_pixels <= 8
    ):
        raise ValueError("direction_marker_pixels must be an integer from 1 to 8")
    raw_component_colors = data["component_colors"]
    if not isinstance(raw_component_colors, list) or not raw_component_colors:
        raise ValueError("component_colors must be a non-empty array of colors")
    component_colors = tuple(
        _parse_color(f"component_colors[{index}]", value)
        for index, value in enumerate(raw_component_colors)
    )

    expected = set(GraphColors.__dataclass_fields__)
    supplied = set(data["colors"])
    if supplied != expected:
        missing = sorted(expected - supplied)
        extra = sorted(supplied - expected)
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if extra:
            details.append(f"unknown: {', '.join(extra)}")
        raise ValueError(f"invalid graph colors ({'; '.join(details)})")
    colors = GraphColors(**{name: _parse_color(name, data["colors"][name]) for name in expected})
    return RenderConfig(
        node_size,
        endpoint_node_size,
        direction_marker_pixels,
        component_colors,
        colors,
    )


def _parse_color(name: str, value: object) -> Color:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(type(channel) is not int or not 0 <= channel <= 255 for channel in value)
    ):
        raise ValueError(f"color '{name}' must be an array of three integers from 0 to 255")
    return value[0], value[1], value[2]


class GraphRenderer:
    def __init__(
        self,
        config: RenderConfig,
        traversal: str,
        component_ids: tuple[int, ...] | None = None,
    ):
        self.config = config
        self.traversal = traversal
        self.component_ids = component_ids

    def render(self, canvas, graph: Graph, frame: TraversalFrame) -> None:
        canvas.Clear()
        ordered_edges = sorted(
            graph.edges,
            key=lambda edge: self._edge_draw_priority(edge, frame),
        )
        for edge in ordered_edges:
            color = self._edge_color(edge, frame)
            self._draw_line(canvas, graph.points[edge.source], graph.points[edge.target], color)
        if graph.directed:
            for edge in ordered_edges:
                self._draw_direction_marker(
                    canvas,
                    graph.points[edge.source],
                    graph.points[edge.target],
                    self.config.colors.direction_marker,
                    self.config.direction_marker_pixels,
                )
        for index, point in enumerate(graph.points):
            size = (
                self.config.endpoint_node_size
                if index in {graph.start, graph.target}
                else self.config.node_size
            )
            self._draw_node(canvas, point, self._node_color(index, graph, frame), size)

    @staticmethod
    def _edge_draw_priority(edge: Edge, frame: TraversalFrame) -> int:
        """Draw persistent algorithm state above less important edge states."""

        if frame.examined_edge == edge.index:
            return 4
        if edge.index in frame.final_path_edges or edge.index in frame.cycle_edges:
            return 3
        if edge.index in frame.predecessor_edges and not frame.complete:
            return 2
        if edge.index in frame.traversed_edges:
            return 1
        return 0

    def _edge_color(self, edge: Edge, frame: TraversalFrame) -> Color:
        colors = self.config.colors
        if frame.examined_edge == edge.index:
            return colors.current_edge
        if edge.index in frame.final_path_edges:
            return colors.shortest_path
        if edge.index in frame.cycle_edges:
            return colors.cycle_edge
        if edge.index in frame.predecessor_edges and not (
            self.traversal == "dijkstra" and frame.complete
        ):
            return colors.predecessor_edge
        if edge.index in frame.traversed_edges and not (
            self.traversal == "dijkstra" and frame.complete
        ):
            return colors.traversed_edge
        if self.traversal == "dijkstra":
            return _blend(colors.dijkstra_low_weight, colors.dijkstra_high_weight, (edge.weight - 1) / 8)
        return colors.unvisited_edge

    def _node_color(self, index: int, graph: Graph, frame: TraversalFrame) -> Color:
        colors = self.config.colors
        if index == frame.current_node:
            return colors.current_node
        if index == graph.start:
            return colors.start_node
        if index == graph.target:
            return colors.target_node
        if self.component_ids is not None and self.component_ids[index] >= 0:
            component = self.component_ids[index]
            return self.config.component_colors[component % len(self.config.component_colors)]
        return colors.node

    def _draw_node(self, canvas, point: Point, color: Color, size: int) -> None:
        offset = (size - 1) // 2
        for dx in range(size):
            for dy in range(size):
                _set_pixel(canvas, point.x + dx - offset, point.y + dy - offset, color)

    @staticmethod
    def _draw_line(canvas, start: Point, end: Point, color: Color) -> None:
        for x, y in _line_pixels(start.x, start.y, end.x, end.y):
            _set_pixel(canvas, x, y, color)

    @staticmethod
    def _draw_direction_marker(
        canvas,
        start: Point,
        end: Point,
        color: Color,
        pixel_count: int,
    ) -> None:
        # Exclude the endpoint because the destination node is drawn over it later.
        edge_pixels = list(_line_pixels(start.x, start.y, end.x, end.y))
        for x, y in edge_pixels[max(1, len(edge_pixels) - pixel_count - 1) : -1]:
            _set_pixel(canvas, x, y, color)


def _blend(low: Color, high: Color, fraction: float) -> Color:
    return tuple(round(left + (right - left) * fraction) for left, right in zip(low, high))  # type: ignore[return-value]


def _line_pixels(x0: int, y0: int, x1: int, y1: int) -> Iterable[tuple[int, int]]:
    dx = abs(x1 - x0)
    step_x = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    step_y = 1 if y0 < y1 else -1
    error = dx + dy
    while True:
        yield x0, y0
        if x0 == x1 and y0 == y1:
            return
        doubled = 2 * error
        if doubled >= dy:
            error += dy
            x0 += step_x
        if doubled <= dx:
            error += dx
            y0 += step_y


def _set_pixel(canvas, x: int, y: int, color: Color) -> None:
    width = getattr(canvas, "width", 64)
    height = getattr(canvas, "height", 64)
    if 0 <= x < width and 0 <= y < height:
        canvas.SetPixel(x, y, *color)
