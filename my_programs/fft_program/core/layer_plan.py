"""Compiled in-process ordered layers for the current FFT scene.

This module turns the immutable Step 1 graph into concrete Step 3 execution once at
startup.  Frame execution retains direct analyzer and drawing callables; it does not
resolve IDs, select a backend, or build commands while a frame is running.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypeAlias

import numpy as np  # type: ignore

from core.analyzer_registry import (
    STANDARD_ANALYZER_ID,
    STANDARD_ANALYZER_TYPE,
    standard_analyzer_capabilities,
    standard_analyzer_definition,
)
from core.output import (
    DIRECT_OUTPUT_PATH,
    PILLOW_BULK_OUTPUT_PATH,
    compile_pillow_bulk_bars_output,
)
from core.runtime_graph import (
    CompiledLayerDefinition,
    CompiledRuntimePlan,
    LayerCapability,
    LayerDefinition,
    RuntimeCapabilities,
    RuntimeDefinition,
    compile_runtime_graph,
)
from core.scaling import ScalingProcessor
from visualizers.peaks import draw_peaks


CURRENT_BARS_LAYER_ID = "current-bars"
CURRENT_BARS_LAYER_TYPE = "current-bars"
_ORDERED_PLAN_CONSTRUCTION_TOKEN = object()


class LayerPlanCompilationError(ValueError):
    """Raised before an incomplete executable plan can be staged."""


PrepareLayer: TypeAlias = Callable[[Any], None]
DrawLayer: TypeAlias = Callable[[Any], None]
SmoothedBars: TypeAlias = Callable[[], np.ndarray]
LayerRuntimeFactory: TypeAlias = Callable[
    [CompiledLayerDefinition, Any, str],
    "LayerRuntimeComponents",
]


class AnalyzerResolver(Protocol):
    """Startup-only lookup surface used while compiling a render plan."""

    def resolve(self, definition: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class LayerRuntimeComponents:
    """Factory result containing one layer's precompiled execution callables."""

    presentation_state: object
    prepare: PrepareLayer
    draw: DrawLayer
    smoothed_bars: SmoothedBars
    incompatible_runtime_paths: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class CompiledLayerRuntime:
    """One stable layer identity with direct analyzer and execution references."""

    definition: CompiledLayerDefinition
    analyzer: Any
    presentation_state: object
    prepare: PrepareLayer
    draw: DrawLayer
    smoothed_bars: SmoothedBars
    incompatible_runtime_paths: frozenset[str]

    @property
    def layer_id(self) -> str:
        return self.definition.layer_id

    @property
    def visible(self) -> bool:
        return self.definition.visible


@dataclass(frozen=True, slots=True)
class _AnalyzerLayerGroup:
    """Precompiled consumers for one physical analyzer result lease."""

    analyzer: Any
    layers: tuple[CompiledLayerRuntime, ...]


class OrderedLayerPlan:
    """Immutable ordered layer structure with mutable layer-owned state.

    The structural tuples never change during a frame.  Each physical analyzer is
    leased once, every visible consumer updates its own presentation state, and the
    lease is released before drawing starts.
    """

    __slots__ = (
        "_analyzer_groups",
        "_begin_draw",
        "_incompatible_runtime_paths",
        "_layers",
        "_output_backend",
        "_visible_layers",
    )

    def __init__(
        self,
        *,
        output_backend: str,
        layers: tuple[CompiledLayerRuntime, ...],
        visible_layers: tuple[CompiledLayerRuntime, ...],
        analyzer_groups: tuple[_AnalyzerLayerGroup, ...],
        begin_draw: Callable[[Any], None],
        incompatible_runtime_paths: frozenset[str],
        _construction_token: object,
    ) -> None:
        if _construction_token is not _ORDERED_PLAN_CONSTRUCTION_TOKEN:
            raise TypeError("OrderedLayerPlan values must be created by the compiler")
        self._output_backend = output_backend
        self._layers = layers
        self._visible_layers = visible_layers
        self._analyzer_groups = analyzer_groups
        self._begin_draw = begin_draw
        self._incompatible_runtime_paths = incompatible_runtime_paths

    @property
    def output_backend(self) -> str:
        return self._output_backend

    @property
    def layers(self) -> tuple[CompiledLayerRuntime, ...]:
        return self._layers

    @property
    def visible_layers(self) -> tuple[CompiledLayerRuntime, ...]:
        return self._visible_layers

    @property
    def incompatible_runtime_paths(self) -> frozenset[str]:
        return self._incompatible_runtime_paths

    @property
    def primary_analyzer(self) -> Any:
        if not self._analyzer_groups:
            raise RuntimeError("ordered layer plan has no visible analyzer")
        return self._analyzer_groups[0].analyzer

    def prepare_frame(self) -> np.ndarray | None:
        """Update visible layer state from each newest complete analyzer result."""

        if not self._visible_layers:
            raise RuntimeError("ordered layer plan has no visible layers")

        for group in self._analyzer_groups:
            result = group.analyzer.get_fft_magnitudes()
            if result is None:
                return None
            try:
                for layer in group.layers:
                    layer.prepare(result)
            finally:
                group.analyzer.release_fft_magnitudes()

        return self._visible_layers[0].smoothed_bars()

    def draw_frame(self, canvas: Any) -> None:
        """Clear the selected backend once and execute visible layers in order."""

        self._begin_draw(canvas)
        for layer in self._visible_layers:
            layer.draw(canvas)


class ActiveLayerPlan:
    """Render-thread owner for complete plan replacement at a frame boundary."""

    __slots__ = ("_active", "_pending")

    def __init__(self, initial_plan: OrderedLayerPlan) -> None:
        if not isinstance(initial_plan, OrderedLayerPlan):
            raise TypeError("initial plan must be an OrderedLayerPlan")
        self._active = initial_plan
        self._pending: OrderedLayerPlan | None = None

    @property
    def active(self) -> OrderedLayerPlan:
        return self._active

    @property
    def pending(self) -> OrderedLayerPlan | None:
        return self._pending

    def stage(self, candidate: OrderedLayerPlan) -> None:
        """Stage one already complete candidate without changing the active frame."""

        if not isinstance(candidate, OrderedLayerPlan):
            raise TypeError("candidate must be a complete OrderedLayerPlan")
        self._pending = candidate

    def begin_frame(self) -> OrderedLayerPlan:
        """Promote a staged candidate only as the render thread starts a frame."""

        pending = self._pending
        if pending is not None:
            self._active = pending
            self._pending = None
        return self._active


def _validate_components(
    definition: CompiledLayerDefinition,
    components: object,
) -> LayerRuntimeComponents:
    if not isinstance(components, LayerRuntimeComponents):
        raise LayerPlanCompilationError(
            f"factory for layer {definition.layer_id!r} did not return "
            "LayerRuntimeComponents"
        )
    for name in ("prepare", "draw", "smoothed_bars"):
        if not callable(getattr(components, name)):
            raise LayerPlanCompilationError(
                f"factory for layer {definition.layer_id!r} returned invalid {name}"
            )
    if not isinstance(components.incompatible_runtime_paths, frozenset):
        raise LayerPlanCompilationError(
            f"layer {definition.layer_id!r} incompatible paths must be a frozenset"
        )
    return components


def compile_ordered_layer_plan(
    plan: CompiledRuntimePlan,
    analyzer_registry: AnalyzerResolver,
    factories: Mapping[str, LayerRuntimeFactory],
    *,
    begin_draw: Callable[[Any], None],
) -> OrderedLayerPlan:
    """Compile direct references, visible order, and analyzer sharing once."""

    if not isinstance(plan, CompiledRuntimePlan):
        raise LayerPlanCompilationError("plan must be a CompiledRuntimePlan")
    if not plan.layers:
        raise LayerPlanCompilationError("ordered layer plan requires at least one layer")
    if not callable(begin_draw):
        raise LayerPlanCompilationError("begin_draw must be callable")

    layers: list[CompiledLayerRuntime] = []
    visible_layers: list[CompiledLayerRuntime] = []
    incompatible_runtime_paths: set[str] = set()
    for definition in plan.layers:
        factory = factories.get(definition.layer_type)
        if factory is None:
            raise LayerPlanCompilationError(
                f"no runtime factory for layer type {definition.layer_type!r}"
            )
        analyzer = analyzer_registry.resolve(definition.analyzer)
        components = _validate_components(
            definition,
            factory(definition, analyzer, plan.output_backend),
        )
        runtime = CompiledLayerRuntime(
            definition=definition,
            analyzer=analyzer,
            presentation_state=components.presentation_state,
            prepare=components.prepare,
            draw=components.draw,
            smoothed_bars=components.smoothed_bars,
            incompatible_runtime_paths=components.incompatible_runtime_paths,
        )
        layers.append(runtime)
        incompatible_runtime_paths.update(components.incompatible_runtime_paths)
        if runtime.visible:
            visible_layers.append(runtime)

    if not visible_layers:
        raise LayerPlanCompilationError(
            "Step 3 requires at least one visible layer in the active plan"
        )

    # Group by physical runtime identity during compilation.  Frame execution keeps
    # only direct analyzer references and immutable consumer tuples.
    grouped: list[tuple[Any, list[CompiledLayerRuntime]]] = []
    group_indexes: dict[int, int] = {}
    for layer in visible_layers:
        identity = id(layer.analyzer)
        group_index = group_indexes.get(identity)
        if group_index is None:
            group_indexes[identity] = len(grouped)
            grouped.append((layer.analyzer, [layer]))
        else:
            grouped[group_index][1].append(layer)
    analyzer_groups = tuple(
        _AnalyzerLayerGroup(analyzer=analyzer, layers=tuple(consumers))
        for analyzer, consumers in grouped
    )

    return OrderedLayerPlan(
        output_backend=plan.output_backend,
        layers=tuple(layers),
        visible_layers=tuple(visible_layers),
        analyzer_groups=analyzer_groups,
        begin_draw=begin_draw,
        incompatible_runtime_paths=frozenset(incompatible_runtime_paths),
        _construction_token=_ORDERED_PLAN_CONSTRUCTION_TOKEN,
    )


class _CurrentBarsPresentation:
    """Mutable scaling, peak, and visual state owned by the current bars layer."""

    __slots__ = ("peaks", "scaler", "settings", "smoothed", "visualizer")

    def __init__(self, scaler: ScalingProcessor, visualizer: Any, settings: Any) -> None:
        self.scaler = scaler
        self.visualizer = visualizer
        self.settings = settings
        self.smoothed = scaler.smoothed_bars
        self.peaks = scaler.peak_heights

    def prepare(self, magnitudes: np.ndarray) -> None:
        _normalized, self.smoothed, self.peaks = self.scaler.process(
            magnitudes,
            peak_hold_frames=self.settings.peak.hold_frames,
            peak_fall_speed=self.settings.peak.fall_speed,
        )

    def smoothed_bars(self) -> np.ndarray:
        return self.smoothed


class _DirectBarsExecutor:
    """Existing native canvas bars and peaks, selected once at compilation."""

    __slots__ = ("_height", "_presentation")

    def __init__(self, presentation: _CurrentBarsPresentation, height: int) -> None:
        self._presentation = presentation
        self._height = height

    def draw(self, canvas: Any) -> None:
        presentation = self._presentation
        visualizer = presentation.visualizer
        visualizer.draw_layer(canvas, presentation.smoothed, None)
        if presentation.settings.peak.enabled:
            draw_peaks(
                canvas,
                presentation.peaks,
                visualizer.theme,
                presentation.settings,
                self._height,
            )


class _PillowBulkBarsExecutor:
    """Existing reusable Pillow image and one SetImage upload per frame."""

    __slots__ = ("_output", "_presentation")

    def __init__(self, presentation: _CurrentBarsPresentation, output: Any) -> None:
        self._presentation = presentation
        self._output = output

    @property
    def incompatible_runtime_paths(self) -> frozenset[str]:
        return self._output.incompatible_runtime_paths

    def draw(self, canvas: Any) -> None:
        self._output.draw(canvas, self._presentation.smoothed, None)


def current_bars_runtime_capabilities() -> RuntimeCapabilities:
    """Trusted Step 3 catalog for the standard analyzer and current bars layer."""

    analyzer_capabilities = standard_analyzer_capabilities().analyzers
    return RuntimeCapabilities(
        analyzers=analyzer_capabilities,
        layers=(
            LayerCapability(
                layer_type=CURRENT_BARS_LAYER_TYPE,
                analyzer_types=frozenset({STANDARD_ANALYZER_TYPE}),
                output_backends=frozenset({
                    DIRECT_OUTPUT_PATH,
                    PILLOW_BULK_OUTPUT_PATH,
                }),
            ),
        ),
    )


def compile_current_bars_graph(
    *,
    audio_settings: Any,
    frequency_settings: Any,
    sensitivity_settings: Any,
    num_bins: int,
    output_backend: str,
) -> CompiledRuntimePlan:
    """Compile the complete one-analyzer, one-bars-layer current scene."""

    analyzer = standard_analyzer_definition(
        STANDARD_ANALYZER_ID,
        audio_settings=audio_settings,
        frequency_settings=frequency_settings,
        sensitivity_settings=sensitivity_settings,
        num_bins=num_bins,
    )
    return compile_runtime_graph(
        RuntimeDefinition(
            analyzers=(analyzer,),
            layers=(
                LayerDefinition(
                    layer_id=CURRENT_BARS_LAYER_ID,
                    layer_type=CURRENT_BARS_LAYER_TYPE,
                    analyzer_id=STANDARD_ANALYZER_ID,
                ),
            ),
            output_backend=output_backend,
        ),
        current_bars_runtime_capabilities(),
    )


def compile_current_bars_layer_plan(
    graph: CompiledRuntimePlan,
    analyzer_registry: AnalyzerResolver,
    *,
    visualizer: Any,
    settings: Any,
    width: int,
    height: int,
) -> OrderedLayerPlan:
    """Compile current presentation state and the manually selected executor."""

    if len(graph.layers) != 1 or graph.layers[0].layer_type != CURRENT_BARS_LAYER_TYPE:
        raise LayerPlanCompilationError(
            "current bars compiler requires exactly one current-bars layer"
        )

    def bars_factory(
        _definition: CompiledLayerDefinition,
        _analyzer: Any,
        output_backend: str,
    ) -> LayerRuntimeComponents:
        presentation = _CurrentBarsPresentation(
            ScalingProcessor(
                scaling_settings=settings.scaling,
                sensitivity_settings=settings.sensitivity,
                smoothing_settings=settings.smoothing,
                num_bins=width,
                frame_rate=1.0 / settings.audio.sleep_delay,
            ),
            visualizer,
            settings,
        )
        if output_backend == DIRECT_OUTPUT_PATH:
            executor = _DirectBarsExecutor(presentation, height)
            incompatible_runtime_paths = frozenset()
        elif output_backend == PILLOW_BULK_OUTPUT_PATH:
            executor = _PillowBulkBarsExecutor(
                presentation,
                compile_pillow_bulk_bars_output(visualizer, width, height),
            )
            incompatible_runtime_paths = executor.incompatible_runtime_paths
        else:
            raise LayerPlanCompilationError(
                f"unsupported current bars backend {output_backend!r}"
            )
        return LayerRuntimeComponents(
            presentation_state=presentation,
            prepare=presentation.prepare,
            draw=executor.draw,
            smoothed_bars=presentation.smoothed_bars,
            incompatible_runtime_paths=incompatible_runtime_paths,
        )

    if graph.output_backend == DIRECT_OUTPUT_PATH:
        begin_draw = lambda canvas: canvas.Clear()
    else:
        # The existing bulk executor clears its reusable Pillow image before its
        # single SetImage submission.  Clearing the native canvas here would mix
        # output paths and change the qualified implementation.
        begin_draw = lambda _canvas: None

    return compile_ordered_layer_plan(
        graph,
        analyzer_registry,
        {CURRENT_BARS_LAYER_TYPE: bars_factory},
        begin_draw=begin_draw,
    )


__all__ = [
    "ActiveLayerPlan",
    "CURRENT_BARS_LAYER_ID",
    "CURRENT_BARS_LAYER_TYPE",
    "CompiledLayerRuntime",
    "LayerPlanCompilationError",
    "LayerRuntimeComponents",
    "OrderedLayerPlan",
    "compile_current_bars_graph",
    "compile_current_bars_layer_plan",
    "compile_ordered_layer_plan",
    "current_bars_runtime_capabilities",
]
