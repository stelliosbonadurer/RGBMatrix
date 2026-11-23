"""Compiled in-process ordered layers for the current FFT scene.

This module turns the immutable Step 1 graph into concrete Step 3 execution once at
startup.  Frame execution retains direct analyzer and drawing callables; it does not
resolve IDs, select a backend, or build commands while a frame is running.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import copy
from dataclasses import dataclass
import math
from typing import Any, TypeAlias

import numpy as np  # type: ignore

from core.analyzer_registry import (
    AnalyzerRegistry,
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
    thaw_settings,
)
from core.scaling import ScalingProcessor
from runtime_control import ANALYSIS_PROJECTION_COMMAND_PATHS
from themes import get_theme, list_themes
from visualizers.bars_unified import BarsUnifiedVisualizer
from visualizers.peaks import draw_peaks


CURRENT_BARS_LAYER_ID = "current-bars"
CURRENT_BARS_LAYER_TYPE = "current-bars"
_ORDERED_PLAN_CONSTRUCTION_TOKEN = object()
CURRENT_BARS_SETTING_NAMES = frozenset({
    "theme",
    "gradient",
    "overflow",
    "overflow_multiplier",
    "bars",
    "full",
    "shadow_enabled",
    "peak_enabled",
    "peak_color_mode",
    "peak_fall_speed",
    "peak_hold_frames",
    "sensitivity_scalar",
    "smoothing_rise",
    "smoothing_fall",
    "dynamic_cycle_speed",
    "dynamic_frozen",
    "min_freq",
    "max_freq",
    "noise_floor",
    "low_freq_weight",
    "high_freq_weight",
})


class LayerPlanCompilationError(ValueError):
    """Raised before an incomplete executable plan can be staged."""


PrepareLayer: TypeAlias = Callable[[Any], None]
DrawLayer: TypeAlias = Callable[[Any], None]
SmoothedBars: TypeAlias = Callable[[], np.ndarray]
LayerRuntimeFactory: TypeAlias = Callable[
    [CompiledLayerDefinition, Any, str],
    "LayerRuntimeComponents",
]


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
        "_blank_smoothed",
        "_end_draw",
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
        end_draw: Callable[[Any], None],
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
        self._end_draw = end_draw
        self._blank_smoothed = np.zeros(0, dtype=np.float32)
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
            return self._blank_smoothed

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
        self._end_draw(canvas)


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
    analyzer_registry: AnalyzerRegistry,
    factories: Mapping[str, LayerRuntimeFactory],
    *,
    begin_draw: Callable[[Any], None],
    end_draw: Callable[[Any], None] = lambda _canvas: None,
    analyzer_resolver: Callable[[CompiledLayerDefinition], Any] | None = None,
) -> OrderedLayerPlan:
    """Compile direct references, visible order, and analyzer sharing once."""

    if not isinstance(plan, CompiledRuntimePlan):
        raise LayerPlanCompilationError("plan must be a CompiledRuntimePlan")
    if not callable(begin_draw):
        raise LayerPlanCompilationError("begin_draw must be callable")
    if not callable(end_draw):
        raise LayerPlanCompilationError("end_draw must be callable")

    layers: list[CompiledLayerRuntime] = []
    visible_layers: list[CompiledLayerRuntime] = []
    incompatible_runtime_paths: set[str] = set()
    for definition in plan.layers:
        factory = factories.get(definition.layer_type)
        if factory is None:
            raise LayerPlanCompilationError(
                f"no runtime factory for layer type {definition.layer_type!r}"
            )
        analyzer = (
            analyzer_resolver(definition)
            if analyzer_resolver is not None
            else analyzer_registry.resolve(definition.analyzer)
        )
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
        end_draw=end_draw,
        incompatible_runtime_paths=frozenset(incompatible_runtime_paths),
        _construction_token=_ORDERED_PLAN_CONSTRUCTION_TOKEN,
    )


class _LayerThemeCycler:
    """Small per-layer theme selector used by the legacy target adapter."""

    __slots__ = ("brightness_boost", "current_index", "themes")

    def __init__(self, initial_theme: str, brightness_boost: float) -> None:
        self.themes = list_themes()
        self.brightness_boost = brightness_boost
        self.current_index = (
            self.themes.index(initial_theme)
            if initial_theme in self.themes
            else 0
        )

    @property
    def current_theme_name(self) -> str:
        return self.themes[self.current_index]

    def next_theme(self) -> Any:
        self.current_index = (self.current_index + 1) % len(self.themes)
        return get_theme(
            self.current_theme_name,
            brightness_boost=self.brightness_boost,
        )

    def prev_theme(self) -> Any:
        self.current_index = (self.current_index - 1) % len(self.themes)
        return get_theme(
            self.current_theme_name,
            brightness_boost=self.brightness_boost,
        )


class _CurrentBarsPresentation:
    """Mutable scaling, peak, and visual state owned by the current bars layer."""

    __slots__ = (
        "peaks",
        "scaler",
        "settings",
        "smoothed",
        "theme_cycler",
        "visualizer",
    )

    def __init__(
        self,
        scaler: ScalingProcessor,
        visualizer: Any,
        settings: Any,
        theme_cycler: Any,
    ) -> None:
        self.scaler = scaler
        self.visualizer = visualizer
        self.settings = settings
        self.theme_cycler = theme_cycler
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

    __slots__ = ("_output", "_overflow_multiplier", "_presentation")

    def __init__(self, presentation: _CurrentBarsPresentation, output: Any) -> None:
        self._presentation = presentation
        self._output = output
        # The live multiplier control is unavailable on pillow-bulk, so the
        # layer's compile-time value stays authoritative for this plan.
        self._overflow_multiplier = float(presentation.settings.overflow.multiplier)

    @property
    def incompatible_runtime_paths(self) -> frozenset[str]:
        return self._output.incompatible_runtime_paths

    def draw(self, canvas: Any) -> None:
        del canvas
        self._output.paint_bars(
            self._presentation.smoothed,
            None,
            self._overflow_multiplier,
        )


def current_bars_settings_from_runtime(
    settings: Any,
    visualizer: Any | None = None,
) -> dict[str, object]:
    """Capture the complete normalized presentation configuration."""

    theme = getattr(visualizer, "theme", None)
    theme_name = str(
        getattr(theme, "name", getattr(settings.color, "theme", "ocean"))
    )
    return {
        "theme": theme_name,
        "gradient": bool(getattr(visualizer, "gradient_mode", False)),
        "overflow": bool(
            getattr(visualizer, "overflow_mode", settings.overflow.enabled)
        ),
        "overflow_multiplier": float(settings.overflow.multiplier),
        "bars": bool(getattr(visualizer, "bars_enabled", True)),
        "full": bool(getattr(visualizer, "full_mode", False)),
        "shadow_enabled": bool(settings.shadow.enabled),
        "peak_enabled": bool(settings.peak.enabled),
        "peak_color_mode": str(settings.peak.color_mode),
        "peak_fall_speed": float(settings.peak.fall_speed),
        "peak_hold_frames": int(settings.peak.hold_frames),
        "sensitivity_scalar": float(settings.scaling.sensitivity_scalar),
        "smoothing_rise": float(settings.smoothing.rise),
        "smoothing_fall": float(settings.smoothing.fall),
        "dynamic_cycle_speed": float(getattr(theme, "cycle_speed", 0.06)),
        "dynamic_frozen": bool(getattr(theme, "frozen", False)),
        "min_freq": float(settings.frequency.active_min_freq),
        "max_freq": float(settings.frequency.active_max_freq),
        "noise_floor": float(settings.sensitivity.noise_floor),
        "low_freq_weight": float(settings.sensitivity.low_freq_weight),
        "high_freq_weight": float(settings.sensitivity.high_freq_weight),
    }


def current_bars_settings_descriptor() -> dict[str, object]:
    """Return the small project-owned descriptor rendered by future clients."""

    return {
        "fields": [
            {"name": "theme", "type": "choice", "choices": list_themes(), "default": "ocean"},
            {"name": "gradient", "type": "boolean", "default": False},
            {"name": "overflow", "type": "boolean", "default": True},
            {"name": "overflow_multiplier", "type": "number", "min": 0.1, "max": 10.0, "default": 1.5},
            {"name": "bars", "type": "boolean", "default": True},
            {"name": "full", "type": "boolean", "default": False},
            {"name": "shadow_enabled", "type": "boolean", "default": False},
            {"name": "peak_enabled", "type": "boolean", "default": False},
            {"name": "peak_color_mode", "type": "choice", "choices": ["white", "bar", "contrast", "peak"], "default": "contrast"},
            {"name": "peak_fall_speed", "type": "number", "min": 0.0, "max": 1.0, "default": 0.08},
            {"name": "peak_hold_frames", "type": "integer", "min": 0, "max": 600, "default": 8},
            {"name": "sensitivity_scalar", "type": "number", "min": 0.01, "max": 20.0, "default": 1.0},
            {"name": "smoothing_rise", "type": "number", "min": 0.0, "max": 1.0, "default": 0.4},
            {"name": "smoothing_fall", "type": "number", "min": 0.0, "max": 1.0, "default": 0.1},
            {"name": "dynamic_cycle_speed", "type": "number", "min": 0.0, "max": 5.0, "default": 0.06},
            {"name": "dynamic_frozen", "type": "boolean", "default": False},
            {"name": "min_freq", "type": "number", "min": 1.0, "max": 24000.0, "default": 100.0},
            {"name": "max_freq", "type": "number", "min": 1.0, "max": 24000.0, "default": 6300.0},
            {"name": "noise_floor", "type": "number", "min": 0.0, "max": 100.0, "default": 0.3},
            {"name": "low_freq_weight", "type": "number", "min": 0.0, "max": 100.0, "default": 0.45},
            {"name": "high_freq_weight", "type": "number", "min": 0.0, "max": 100.0, "default": 10.0},
        ],
        "backend_constraints": {
            PILLOW_BULK_OUTPUT_PATH: {
                "theme": "ocean",
                "gradient": False,
                "overflow": True,
                "bars": True,
                "full": False,
                "shadow_enabled": False,
                "peak_enabled": False,
                "dynamic_cycle_speed": 0.06,
                "dynamic_frozen": False,
            }
        },
    }


def _number(
    settings: Mapping[str, object],
    name: str,
    minimum: float,
    maximum: float,
    *,
    integer: bool = False,
) -> int | float:
    value = settings[name]
    valid_type = isinstance(value, int) if integer else isinstance(value, (int, float))
    number = math.nan
    if valid_type and not isinstance(value, bool):
        try:
            number = float(value)
        except OverflowError:
            # A JSON integer too large for a float is out of range, not a
            # handler failure.
            number = math.nan
    if not math.isfinite(number) or not minimum <= number <= maximum:
        kind = "an integer" if integer else "a number"
        raise ValueError(
            f"{name} must be {kind} between {minimum:g} and {maximum:g}"
        )
    return int(value) if integer else number


def normalize_current_bars_settings(
    value: Mapping[str, object],
    output_backend: str,
) -> dict[str, object]:
    """Validate one complete current-bars settings object."""

    if not isinstance(value, Mapping):
        raise ValueError("settings must be an object")
    fields = set(value)
    missing = CURRENT_BARS_SETTING_NAMES - fields
    extra = fields - CURRENT_BARS_SETTING_NAMES
    if missing or extra:
        detail = []
        if missing:
            detail.append(f"missing {sorted(missing)!r}")
        if extra:
            detail.append(f"unknown {sorted(extra)!r}")
        raise ValueError("settings fields are invalid: " + "; ".join(detail))

    normalized = dict(value)
    if not isinstance(normalized["theme"], str) or normalized["theme"] not in list_themes():
        raise ValueError("theme is not registered")
    for name in (
        "gradient", "overflow", "bars", "full", "shadow_enabled",
        "peak_enabled", "dynamic_frozen",
    ):
        if not isinstance(normalized[name], bool):
            raise ValueError(f"{name} must be a boolean")
    if (
        not isinstance(normalized["peak_color_mode"], str)
        or normalized["peak_color_mode"] not in {"white", "bar", "contrast", "peak"}
    ):
        raise ValueError("peak_color_mode is unsupported")
    normalized["overflow_multiplier"] = _number(normalized, "overflow_multiplier", 0.1, 10.0)
    normalized["peak_fall_speed"] = _number(normalized, "peak_fall_speed", 0.0, 1.0)
    normalized["peak_hold_frames"] = _number(normalized, "peak_hold_frames", 0, 600, integer=True)
    normalized["sensitivity_scalar"] = _number(normalized, "sensitivity_scalar", 0.01, 20.0)
    normalized["smoothing_rise"] = _number(normalized, "smoothing_rise", 0.0, 1.0)
    normalized["smoothing_fall"] = _number(normalized, "smoothing_fall", 0.0, 1.0)
    normalized["dynamic_cycle_speed"] = _number(normalized, "dynamic_cycle_speed", 0.0, 5.0)
    normalized["min_freq"] = _number(normalized, "min_freq", 1.0, 24000.0)
    normalized["max_freq"] = _number(normalized, "max_freq", 1.0, 24000.0)
    if normalized["max_freq"] <= normalized["min_freq"]:
        raise ValueError("max_freq must be greater than min_freq")
    normalized["noise_floor"] = _number(normalized, "noise_floor", 0.0, 100.0)
    normalized["low_freq_weight"] = _number(normalized, "low_freq_weight", 0.0, 100.0)
    normalized["high_freq_weight"] = _number(normalized, "high_freq_weight", 0.0, 100.0)

    if output_backend == PILLOW_BULK_OUTPUT_PATH:
        fixed = current_bars_settings_descriptor()["backend_constraints"][PILLOW_BULK_OUTPUT_PATH]  # type: ignore[index]
        for name, expected in fixed.items():  # type: ignore[union-attr]
            if normalized[name] != expected:
                raise ValueError(
                    f"{name} must be {expected!r} for pillow-bulk"
                )
    return normalized


def _validate_current_bars_settings(settings: Any, output_backend: str) -> None:
    normalize_current_bars_settings(thaw_settings(settings), output_backend)


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
                settings_validator=_validate_current_bars_settings,
                settings_descriptor=current_bars_settings_descriptor(),
            ),
        ),
    )


def current_bars_runtime_definition(
    *,
    audio_settings: Any,
    frequency_settings: Any,
    sensitivity_settings: Any,
    num_bins: int,
    output_backend: str,
    layer_settings: Mapping[str, object],
) -> RuntimeDefinition:
    """Build the complete declarative default graph before compilation."""

    analyzer = standard_analyzer_definition(
        STANDARD_ANALYZER_ID,
        audio_settings=audio_settings,
        frequency_settings=frequency_settings,
        sensitivity_settings=sensitivity_settings,
        num_bins=num_bins,
    )
    normalized = normalize_current_bars_settings(layer_settings, output_backend)
    return RuntimeDefinition(
        analyzers=(analyzer,),
        layers=(
            LayerDefinition(
                layer_id=CURRENT_BARS_LAYER_ID,
                layer_type=CURRENT_BARS_LAYER_TYPE,
                analyzer_id=STANDARD_ANALYZER_ID,
                settings=normalized,
            ),
        ),
        output_backend=output_backend,
    )


def compile_current_bars_graph(
    *,
    audio_settings: Any,
    frequency_settings: Any,
    sensitivity_settings: Any,
    num_bins: int,
    output_backend: str,
    layer_settings: Mapping[str, object] | None = None,
    settings: Any | None = None,
) -> CompiledRuntimePlan:
    """Compile the complete one-analyzer, one-bars-layer current scene."""

    if layer_settings is None:
        if settings is None:
            from config.settings import Settings

            settings = Settings()
        layer_settings = current_bars_settings_from_runtime(settings)
    return compile_runtime_graph(
        current_bars_runtime_definition(
            audio_settings=audio_settings,
            frequency_settings=frequency_settings,
            sensitivity_settings=sensitivity_settings,
            num_bins=num_bins,
            output_backend=output_backend,
            layer_settings=layer_settings,
        ),
        current_bars_runtime_capabilities(),
    )


def _settings_for_layer(
    base_settings: Any,
    normalized: Mapping[str, object],
) -> Any:
    settings = copy.deepcopy(base_settings)
    # These objects remain analyzer-owned. Every presentation runtime observes the
    # one live analyzer control state instead of receiving a structural copy.
    settings.audio = base_settings.audio
    settings.frequency = base_settings.frequency
    settings.sensitivity = base_settings.sensitivity
    settings.color.theme = normalized["theme"]
    settings.overflow.enabled = normalized["overflow"]
    settings.overflow.multiplier = normalized["overflow_multiplier"]
    settings.shadow.enabled = normalized["shadow_enabled"]
    settings.peak.enabled = normalized["peak_enabled"]
    settings.peak.color_mode = normalized["peak_color_mode"]
    settings.peak.fall_speed = normalized["peak_fall_speed"]
    settings.peak.hold_frames = normalized["peak_hold_frames"]
    settings.scaling.sensitivity_scalar = normalized["sensitivity_scalar"]
    settings.smoothing.rise = normalized["smoothing_rise"]
    settings.smoothing.fall = normalized["smoothing_fall"]
    return settings


def _presentation_objects(
    normalized: Mapping[str, object],
    base_settings: Any,
    width: int,
    height: int,
) -> tuple[Any, Any, Any]:
    settings = _settings_for_layer(base_settings, normalized)
    visualizer = BarsUnifiedVisualizer(width, height, settings)
    visualizer.gradient_mode = normalized["gradient"]
    visualizer.overflow_mode = normalized["overflow"]
    visualizer.bars_enabled = normalized["bars"]
    visualizer.full_mode = normalized["full"]
    theme = get_theme(
        str(normalized["theme"]),
        brightness_boost=settings.color.brightness_boost,
    )
    if hasattr(theme, "cycle_speed"):
        theme.cycle_speed = normalized["dynamic_cycle_speed"]
        if bool(getattr(theme, "frozen", False)) != normalized["dynamic_frozen"]:
            theme.toggle_frozen()
    visualizer.set_theme(theme)
    theme_cycler = _LayerThemeCycler(
        str(normalized["theme"]),
        settings.color.brightness_boost,
    )
    return settings, visualizer, theme_cycler


def compile_current_bars_layer_plan(
    graph: CompiledRuntimePlan,
    analyzer_registry: AnalyzerRegistry,
    *,
    visualizer: Any,
    settings: Any,
    width: int,
    height: int,
    theme_cycler: Any | None = None,
) -> OrderedLayerPlan:
    """Compile current presentation state and the manually selected executor."""

    if any(layer.layer_type != CURRENT_BARS_LAYER_TYPE for layer in graph.layers):
        raise LayerPlanCompilationError("current bars compiler received another layer type")

    use_initial_objects = len(graph.layers) == 1 and visualizer is not None
    shared_output = None
    if graph.output_backend == PILLOW_BULK_OUTPUT_PATH:
        if graph.layers:
            prototype_settings = normalize_current_bars_settings(
                thaw_settings(graph.layers[0].settings),
                graph.output_backend,
            )
        else:
            prototype_settings = current_bars_settings_from_runtime(settings)
            prototype_settings.update(
                current_bars_settings_descriptor()["backend_constraints"][  # type: ignore[index]
                    PILLOW_BULK_OUTPUT_PATH
                ]
            )
        _prototype_settings, prototype, _prototype_cycler = _presentation_objects(
            prototype_settings,
            settings,
            width,
            height,
        )
        shared_output = compile_pillow_bulk_bars_output(prototype, width, height)

    def bars_factory(
        definition: CompiledLayerDefinition,
        _analyzer: Any,
        output_backend: str,
    ) -> LayerRuntimeComponents:
        normalized = normalize_current_bars_settings(
            thaw_settings(definition.settings),
            output_backend,
        )
        analyzer_settings = thaw_settings(definition.analyzer.settings)
        custom_projection = any(
            normalized[name] != analyzer_settings[name]
            for name in (
                "min_freq",
                "max_freq",
                "noise_floor",
                "low_freq_weight",
                "high_freq_weight",
            )
        )
        if use_initial_objects and definition.layer_id == CURRENT_BARS_LAYER_ID:
            layer_settings = settings
            layer_visualizer = visualizer
            layer_theme_cycler = theme_cycler or _LayerThemeCycler(
                str(normalized["theme"]),
                settings.color.brightness_boost,
            )
        else:
            layer_settings, layer_visualizer, layer_theme_cycler = _presentation_objects(
                normalized,
                settings,
                width,
                height,
            )
        presentation = _CurrentBarsPresentation(
            ScalingProcessor(
                scaling_settings=layer_settings.scaling,
                sensitivity_settings=layer_settings.sensitivity,
                smoothing_settings=layer_settings.smoothing,
                num_bins=width,
                frame_rate=1.0 / layer_settings.audio.sleep_delay,
            ),
            layer_visualizer,
            layer_settings,
            layer_theme_cycler,
        )
        if output_backend == DIRECT_OUTPUT_PATH:
            executor = _DirectBarsExecutor(presentation, height)
            incompatible_runtime_paths = (
                ANALYSIS_PROJECTION_COMMAND_PATHS
                if (
                    definition.layer_id == CURRENT_BARS_LAYER_ID
                    and custom_projection
                )
                else frozenset()
            )
        elif output_backend == PILLOW_BULK_OUTPUT_PATH:
            assert shared_output is not None
            executor = _PillowBulkBarsExecutor(
                presentation,
                shared_output,
            )
            incompatible_runtime_paths = executor.incompatible_runtime_paths
            if (
                definition.layer_id == CURRENT_BARS_LAYER_ID
                and custom_projection
            ):
                incompatible_runtime_paths = (
                    incompatible_runtime_paths
                    | ANALYSIS_PROJECTION_COMMAND_PATHS
                )
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
        end_draw = lambda _canvas: None
    else:
        assert shared_output is not None
        begin_draw = lambda _canvas: shared_output.begin_frame()
        end_draw = shared_output.end_frame

    def projection_analyzer(definition: CompiledLayerDefinition) -> Any:
        normalized = normalize_current_bars_settings(
            thaw_settings(definition.settings),
            graph.output_backend,
        )
        return analyzer_registry.resolve_projection(
            definition.analyzer,
            {
                name: normalized[name]
                for name in (
                    "min_freq",
                    "max_freq",
                    "noise_floor",
                    "low_freq_weight",
                    "high_freq_weight",
                )
            },
        )

    return compile_ordered_layer_plan(
        graph,
        analyzer_registry,
        {CURRENT_BARS_LAYER_TYPE: bars_factory},
        begin_draw=begin_draw,
        end_draw=end_draw,
        analyzer_resolver=projection_analyzer,
    )


__all__ = [
    "ActiveLayerPlan",
    "CURRENT_BARS_LAYER_ID",
    "CURRENT_BARS_LAYER_TYPE",
    "CURRENT_BARS_SETTING_NAMES",
    "CompiledLayerRuntime",
    "LayerPlanCompilationError",
    "LayerRuntimeComponents",
    "OrderedLayerPlan",
    "compile_current_bars_graph",
    "compile_current_bars_layer_plan",
    "compile_ordered_layer_plan",
    "current_bars_runtime_definition",
    "current_bars_runtime_capabilities",
    "current_bars_settings_descriptor",
    "current_bars_settings_from_runtime",
    "normalize_current_bars_settings",
]
