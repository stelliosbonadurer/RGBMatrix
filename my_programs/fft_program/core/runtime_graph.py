"""Non-executing definitions and compiler for the modular FFT runtime graph.

This module is deliberately control-plane only.  It validates a flexible definition
graph and snapshots it into immutable tuples with resolved analyzer references.  It
does not construct analyzers, renderers, buffers, canvases, or execution callables.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import math
from typing import TypeAlias


RUNTIME_GRAPH_SCHEMA_VERSION = 1
MAX_RUNTIME_LAYERS = 8
DIRECT_OUTPUT_BACKEND = "direct"
PILLOW_BULK_OUTPUT_BACKEND = "pillow-bulk"
SUPPORTED_OUTPUT_BACKENDS = frozenset({
    DIRECT_OUTPUT_BACKEND,
    PILLOW_BULK_OUTPUT_BACKEND,
})

CanonicalValue: TypeAlias = tuple[str, object]
AnalyzerSettingsValidator: TypeAlias = Callable[["FrozenSettings"], None]
LayerSettingsValidator: TypeAlias = Callable[["FrozenSettings", str], None]


class RuntimeGraphValidationError(ValueError):
    """Raised before an incomplete runtime graph can become a compiled plan."""


@dataclass(frozen=True, slots=True)
class FrozenSettings:
    """Deterministic, recursively immutable snapshot of one settings mapping."""

    items: tuple[tuple[str, CanonicalValue], ...]


@dataclass(frozen=True, slots=True)
class AnalyzerDefinition:
    """Flexible control-plane definition for one stable analyzer identity."""

    analyzer_id: str
    analyzer_type: str
    result_shape: Sequence[int]
    result_dtype: str
    settings: Mapping[str, object] = field(default_factory=dict)
    schema_version: int = RUNTIME_GRAPH_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class LayerDefinition:
    """Flexible control-plane definition for one ordered visual layer."""

    layer_id: str
    layer_type: str
    analyzer_id: str
    visible: bool = True
    settings: Mapping[str, object] = field(default_factory=dict)
    schema_version: int = RUNTIME_GRAPH_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class RuntimeDefinition:
    """Complete candidate graph; layer sequence is explicitly bottom to top."""

    analyzers: Sequence[AnalyzerDefinition]
    layers: Sequence[LayerDefinition]
    output_backend: str
    schema_version: int = RUNTIME_GRAPH_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class AnalyzerCapability:
    """Trusted metadata accepted by one future analyzer implementation."""

    analyzer_type: str
    result_dtypes: frozenset[str]
    result_ranks: frozenset[int]
    settings_validator: AnalyzerSettingsValidator | None = field(
        default=None,
        compare=False,
        repr=False,
    )
    settings_descriptor: Mapping[str, object] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )


@dataclass(frozen=True, slots=True)
class LayerCapability:
    """Trusted analyzer and output support declared by one future layer type."""

    layer_type: str
    analyzer_types: frozenset[str]
    output_backends: frozenset[str]
    settings_validator: LayerSettingsValidator | None = field(
        default=None,
        compare=False,
        repr=False,
    )
    settings_descriptor: Mapping[str, object] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    """Compile-time support catalog; it owns no runtime objects or execution."""

    analyzers: Sequence[AnalyzerCapability]
    layers: Sequence[LayerCapability]


@dataclass(frozen=True, slots=True)
class AnalyzerCanonicalKey:
    """Stable semantic identity used to group equivalent analyzer definitions.

    The public analyzer ID is intentionally excluded.  Mapping key order and whether
    a sequence arrived as a list or tuple do not affect the key.  Scalar JSON types
    remain distinct, so an integer and a float are not silently treated as the same
    analyzer setting.
    """

    schema_version: int
    analyzer_type: str
    settings: FrozenSettings
    result_shape: tuple[int, ...]
    result_dtype: str


@dataclass(frozen=True, slots=True)
class CompiledAnalyzerDefinition:
    """Immutable analyzer metadata; ``canonical_slot`` is the future reuse seam."""

    analyzer_id: str
    analyzer_type: str
    settings: FrozenSettings
    result_shape: tuple[int, ...]
    result_dtype: str
    canonical_key: AnalyzerCanonicalKey
    canonical_slot: int


@dataclass(frozen=True, slots=True)
class CompiledLayerDefinition:
    """Immutable layer metadata with its analyzer reference already resolved."""

    layer_id: str
    layer_type: str
    analyzer: CompiledAnalyzerDefinition
    visible: bool
    settings: FrozenSettings


@dataclass(frozen=True, slots=True)
class CompiledRuntimePlan:
    """Concrete non-executing plan suitable for later runtime compilation."""

    schema_version: int
    output_backend: str
    analyzers: tuple[CompiledAnalyzerDefinition, ...]
    canonical_analyzer_keys: tuple[AnalyzerCanonicalKey, ...]
    layers: tuple[CompiledLayerDefinition, ...]


def _require_schema_version(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeGraphValidationError(f"{name} must be an integer")
    if value != RUNTIME_GRAPH_SCHEMA_VERSION:
        raise RuntimeGraphValidationError(
            f"unsupported {name}: {value!r}; expected {RUNTIME_GRAPH_SCHEMA_VERSION}"
        )
    return value


def _require_name(name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeGraphValidationError(f"{name} must be a non-empty string")
    if value != value.strip():
        raise RuntimeGraphValidationError(
            f"{name} must not have leading or trailing whitespace"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise RuntimeGraphValidationError(f"{name} must not contain control characters")
    return value


def _freeze_value(
    value: object,
    *,
    path: str,
    active_container_ids: set[int],
) -> CanonicalValue:
    if value is None:
        return ("null", None)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RuntimeGraphValidationError(f"{path} must be a finite number")
        return ("float", value.hex())
    if isinstance(value, str):
        return ("string", value)

    if isinstance(value, Mapping):
        container_id = id(value)
        if container_id in active_container_ids:
            raise RuntimeGraphValidationError(f"{path} must not contain a cycle")
        active_container_ids.add(container_id)
        try:
            keys = tuple(value.keys())
            for key in keys:
                if not isinstance(key, str):
                    raise RuntimeGraphValidationError(
                        f"{path} keys must all be strings"
                    )
            items = tuple(
                (
                    key,
                    _freeze_value(
                        value[key],
                        path=f"{path}.{key}",
                        active_container_ids=active_container_ids,
                    ),
                )
                for key in sorted(keys)
            )
            return ("mapping", items)
        finally:
            active_container_ids.remove(container_id)

    if isinstance(value, (list, tuple)):
        container_id = id(value)
        if container_id in active_container_ids:
            raise RuntimeGraphValidationError(f"{path} must not contain a cycle")
        active_container_ids.add(container_id)
        try:
            values = tuple(
                _freeze_value(
                    item,
                    path=f"{path}[{index}]",
                    active_container_ids=active_container_ids,
                )
                for index, item in enumerate(value)
            )
            return ("sequence", values)
        finally:
            active_container_ids.remove(container_id)

    raise RuntimeGraphValidationError(
        f"{path} contains unsupported value type {type(value).__name__!r}"
    )


def _freeze_settings(name: str, settings: object) -> FrozenSettings:
    if not isinstance(settings, Mapping):
        raise RuntimeGraphValidationError(f"{name} must be a mapping")
    frozen = _freeze_value(
        settings,
        path=name,
        active_container_ids=set(),
    )
    return FrozenSettings(items=frozen[1])  # type: ignore[arg-type]


def _thaw_value(value: CanonicalValue) -> object:
    value_type, payload = value
    if value_type in {"null", "bool", "int", "string"}:
        return payload
    if value_type == "float":
        return float.fromhex(payload)  # type: ignore[arg-type]
    if value_type == "mapping":
        return {
            key: _thaw_value(item)
            for key, item in payload  # type: ignore[union-attr]
        }
    if value_type == "sequence":
        return [
            _thaw_value(item)
            for item in payload  # type: ignore[union-attr]
        ]
    raise RuntimeGraphValidationError(f"unsupported frozen value type {value_type!r}")


def thaw_settings(settings: FrozenSettings) -> dict[str, object]:
    """Return a detached JSON-shaped copy of immutable compiled settings."""

    if not isinstance(settings, FrozenSettings):
        raise TypeError("settings must be FrozenSettings")
    return {key: _thaw_value(value) for key, value in settings.items}


def runtime_definition_to_dict(definition: RuntimeDefinition) -> dict[str, object]:
    """Serialize only the declarative public runtime graph."""

    if not isinstance(definition, RuntimeDefinition):
        raise TypeError("definition must be RuntimeDefinition")
    return {
        "schema_version": definition.schema_version,
        "output_backend": definition.output_backend,
        "analyzers": [
            {
                "schema_version": analyzer.schema_version,
                "analyzer_id": analyzer.analyzer_id,
                "analyzer_type": analyzer.analyzer_type,
                "result_shape": list(analyzer.result_shape),
                "result_dtype": analyzer.result_dtype,
                "settings": dict(analyzer.settings),
            }
            for analyzer in definition.analyzers
        ],
        "layers": [
            {
                "schema_version": layer.schema_version,
                "layer_id": layer.layer_id,
                "layer_type": layer.layer_type,
                "analyzer_id": layer.analyzer_id,
                "visible": layer.visible,
                "settings": dict(layer.settings),
            }
            for layer in definition.layers
        ],
    }


def runtime_capabilities_to_dict(
    capabilities: RuntimeCapabilities,
) -> dict[str, object]:
    """Serialize the closed trusted catalog without exposing callables."""

    analyzer_capabilities, layer_capabilities = _compile_capabilities(capabilities)
    return {
        "analyzer_types": [
            {
                "analyzer_type": capability.analyzer_type,
                "result_dtypes": sorted(capability.result_dtypes),
                "result_ranks": sorted(capability.result_ranks),
                "settings": dict(capability.settings_descriptor),
            }
            for capability in analyzer_capabilities.values()
        ],
        "layer_types": [
            {
                "layer_type": capability.layer_type,
                "analyzer_types": sorted(capability.analyzer_types),
                "output_backends": sorted(capability.output_backends),
                "settings": dict(capability.settings_descriptor),
            }
            for capability in layer_capabilities.values()
        ],
        "output_backends": sorted(SUPPORTED_OUTPUT_BACKENDS),
        "limits": {"max_layers": MAX_RUNTIME_LAYERS},
    }


def _compile_capabilities(
    capabilities: RuntimeCapabilities,
) -> tuple[dict[str, AnalyzerCapability], dict[str, LayerCapability]]:
    if not isinstance(capabilities, RuntimeCapabilities):
        raise RuntimeGraphValidationError(
            "capabilities must be a RuntimeCapabilities value"
        )
    if not isinstance(capabilities.analyzers, (list, tuple)):
        raise RuntimeGraphValidationError(
            "analyzer capabilities must be an ordered list or tuple"
        )
    if not isinstance(capabilities.layers, (list, tuple)):
        raise RuntimeGraphValidationError(
            "layer capabilities must be an ordered list or tuple"
        )

    analyzer_capabilities: dict[str, AnalyzerCapability] = {}
    for capability in capabilities.analyzers:
        if not isinstance(capability, AnalyzerCapability):
            raise RuntimeGraphValidationError(
                "every analyzer capability must be an AnalyzerCapability"
            )
        analyzer_type = _require_name(
            "analyzer capability type",
            capability.analyzer_type,
        )
        if analyzer_type in analyzer_capabilities:
            raise RuntimeGraphValidationError(
                f"duplicate analyzer capability type: {analyzer_type!r}"
            )
        if not isinstance(capability.result_dtypes, frozenset) or not capability.result_dtypes:
            raise RuntimeGraphValidationError(
                f"analyzer capability {analyzer_type!r} must declare result dtypes"
            )
        for result_dtype in capability.result_dtypes:
            _require_name("analyzer capability result dtype", result_dtype)
        if not isinstance(capability.result_ranks, frozenset) or not capability.result_ranks:
            raise RuntimeGraphValidationError(
                f"analyzer capability {analyzer_type!r} must declare result ranks"
            )
        for result_rank in capability.result_ranks:
            if (
                isinstance(result_rank, bool)
                or not isinstance(result_rank, int)
                or result_rank < 1
            ):
                raise RuntimeGraphValidationError(
                    "analyzer capability result ranks must be positive integers"
                )
        if (
            capability.settings_validator is not None
            and not callable(capability.settings_validator)
        ):
            raise RuntimeGraphValidationError(
                f"analyzer capability {analyzer_type!r} has an invalid settings validator"
            )
        analyzer_capabilities[analyzer_type] = capability

    layer_capabilities: dict[str, LayerCapability] = {}
    for capability in capabilities.layers:
        if not isinstance(capability, LayerCapability):
            raise RuntimeGraphValidationError(
                "every layer capability must be a LayerCapability"
            )
        layer_type = _require_name("layer capability type", capability.layer_type)
        if layer_type in layer_capabilities:
            raise RuntimeGraphValidationError(
                f"duplicate layer capability type: {layer_type!r}"
            )
        if not isinstance(capability.analyzer_types, frozenset) or not capability.analyzer_types:
            raise RuntimeGraphValidationError(
                f"layer capability {layer_type!r} must declare analyzer types"
            )
        for analyzer_type in capability.analyzer_types:
            _require_name("layer capability analyzer type", analyzer_type)
            if analyzer_type not in analyzer_capabilities:
                raise RuntimeGraphValidationError(
                    f"layer capability {layer_type!r} references unsupported "
                    f"analyzer type {analyzer_type!r}"
                )
        if not isinstance(capability.output_backends, frozenset) or not capability.output_backends:
            raise RuntimeGraphValidationError(
                f"layer capability {layer_type!r} must declare output backends"
            )
        unsupported_backends = capability.output_backends - SUPPORTED_OUTPUT_BACKENDS
        if unsupported_backends:
            raise RuntimeGraphValidationError(
                f"layer capability {layer_type!r} declares unsupported output "
                f"backends: {sorted(unsupported_backends)!r}"
            )
        if (
            capability.settings_validator is not None
            and not callable(capability.settings_validator)
        ):
            raise RuntimeGraphValidationError(
                f"layer capability {layer_type!r} has an invalid settings validator"
            )
        layer_capabilities[layer_type] = capability

    return analyzer_capabilities, layer_capabilities


def _validate_analyzer_settings(
    owner: str,
    validator: AnalyzerSettingsValidator | None,
    settings: FrozenSettings,
) -> None:
    if validator is None:
        return
    try:
        validator(settings)
    except (RuntimeGraphValidationError, ValueError) as exc:
        raise RuntimeGraphValidationError(f"invalid {owner} settings: {exc}") from exc


def _validate_layer_settings(
    owner: str,
    validator: LayerSettingsValidator | None,
    settings: FrozenSettings,
    output_backend: str,
) -> None:
    if validator is None:
        return
    try:
        validator(settings, output_backend)
    except (RuntimeGraphValidationError, ValueError) as exc:
        raise RuntimeGraphValidationError(f"invalid {owner} settings: {exc}") from exc


def _compile_analyzer(
    definition: AnalyzerDefinition,
    capability: AnalyzerCapability,
    canonical_slots: dict[AnalyzerCanonicalKey, int],
    canonical_keys: list[AnalyzerCanonicalKey],
) -> CompiledAnalyzerDefinition:
    _require_schema_version("analyzer schema version", definition.schema_version)
    analyzer_id = _require_name("analyzer ID", definition.analyzer_id)
    analyzer_type = _require_name("analyzer type", definition.analyzer_type)
    result_dtype = _require_name("analyzer result dtype", definition.result_dtype)

    if not isinstance(definition.result_shape, (list, tuple)):
        raise RuntimeGraphValidationError(
            f"analyzer {analyzer_id!r} result shape must be a list or tuple"
        )
    result_shape = tuple(definition.result_shape)
    if not result_shape:
        raise RuntimeGraphValidationError(
            f"analyzer {analyzer_id!r} result shape must not be empty"
        )
    for dimension in result_shape:
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1:
            raise RuntimeGraphValidationError(
                f"analyzer {analyzer_id!r} result dimensions must be positive integers"
            )

    if result_dtype not in capability.result_dtypes:
        raise RuntimeGraphValidationError(
            f"analyzer {analyzer_id!r} type {analyzer_type!r} does not support "
            f"result dtype {result_dtype!r}"
        )
    if len(result_shape) not in capability.result_ranks:
        raise RuntimeGraphValidationError(
            f"analyzer {analyzer_id!r} type {analyzer_type!r} does not support "
            f"result rank {len(result_shape)}"
        )

    settings = _freeze_settings(f"analyzer {analyzer_id!r} settings", definition.settings)
    _validate_analyzer_settings(
        f"analyzer {analyzer_id!r}",
        capability.settings_validator,
        settings,
    )
    canonical_key = AnalyzerCanonicalKey(
        schema_version=definition.schema_version,
        analyzer_type=analyzer_type,
        settings=settings,
        result_shape=result_shape,
        result_dtype=result_dtype,
    )
    canonical_slot = canonical_slots.get(canonical_key)
    if canonical_slot is None:
        canonical_slot = len(canonical_keys)
        canonical_slots[canonical_key] = canonical_slot
        canonical_keys.append(canonical_key)

    return CompiledAnalyzerDefinition(
        analyzer_id=analyzer_id,
        analyzer_type=analyzer_type,
        settings=settings,
        result_shape=result_shape,
        result_dtype=result_dtype,
        canonical_key=canonical_key,
        canonical_slot=canonical_slot,
    )


def compile_runtime_graph(
    definition: RuntimeDefinition,
    capabilities: RuntimeCapabilities,
) -> CompiledRuntimePlan:
    """Validate and compile one complete graph without mutating active state."""

    if not isinstance(definition, RuntimeDefinition):
        raise RuntimeGraphValidationError(
            "definition must be a RuntimeDefinition value"
        )
    schema_version = _require_schema_version(
        "runtime graph schema version",
        definition.schema_version,
    )
    output_backend = _require_name("output backend", definition.output_backend)
    if output_backend not in SUPPORTED_OUTPUT_BACKENDS:
        raise RuntimeGraphValidationError(
            f"unsupported output backend {output_backend!r}; expected "
            f"{DIRECT_OUTPUT_BACKEND!r} or {PILLOW_BULK_OUTPUT_BACKEND!r}"
        )
    if not isinstance(definition.analyzers, (list, tuple)):
        raise RuntimeGraphValidationError(
            "analyzer definitions must be an ordered list or tuple"
        )
    if not isinstance(definition.layers, (list, tuple)):
        raise RuntimeGraphValidationError(
            "layer definitions must be an ordered bottom-to-top list or tuple"
        )
    if len(definition.layers) > MAX_RUNTIME_LAYERS:
        raise RuntimeGraphValidationError(
            f"runtime graph supports at most {MAX_RUNTIME_LAYERS} layers"
        )

    analyzer_capabilities, layer_capabilities = _compile_capabilities(capabilities)
    analyzers: list[CompiledAnalyzerDefinition] = []
    analyzers_by_id: dict[str, CompiledAnalyzerDefinition] = {}
    canonical_slots: dict[AnalyzerCanonicalKey, int] = {}
    canonical_keys: list[AnalyzerCanonicalKey] = []

    for analyzer_definition in definition.analyzers:
        if not isinstance(analyzer_definition, AnalyzerDefinition):
            raise RuntimeGraphValidationError(
                "every analyzer definition must be an AnalyzerDefinition"
            )
        analyzer_id = _require_name("analyzer ID", analyzer_definition.analyzer_id)
        if analyzer_id in analyzers_by_id:
            raise RuntimeGraphValidationError(
                f"duplicate analyzer ID: {analyzer_id!r}"
            )
        analyzer_type = _require_name(
            "analyzer type",
            analyzer_definition.analyzer_type,
        )
        capability = analyzer_capabilities.get(analyzer_type)
        if capability is None:
            raise RuntimeGraphValidationError(
                f"analyzer {analyzer_id!r} has unsupported type {analyzer_type!r}"
            )
        compiled_analyzer = _compile_analyzer(
            analyzer_definition,
            capability,
            canonical_slots,
            canonical_keys,
        )
        analyzers.append(compiled_analyzer)
        analyzers_by_id[analyzer_id] = compiled_analyzer

    layers: list[CompiledLayerDefinition] = []
    layer_ids: set[str] = set()
    for layer_definition in definition.layers:
        if not isinstance(layer_definition, LayerDefinition):
            raise RuntimeGraphValidationError(
                "every layer definition must be a LayerDefinition"
            )
        _require_schema_version("layer schema version", layer_definition.schema_version)
        layer_id = _require_name("layer ID", layer_definition.layer_id)
        if layer_id in layer_ids:
            raise RuntimeGraphValidationError(f"duplicate layer ID: {layer_id!r}")
        layer_ids.add(layer_id)
        layer_type = _require_name("layer type", layer_definition.layer_type)
        analyzer_id = _require_name(
            f"layer {layer_id!r} analyzer ID",
            layer_definition.analyzer_id,
        )
        analyzer = analyzers_by_id.get(analyzer_id)
        if analyzer is None:
            raise RuntimeGraphValidationError(
                f"layer {layer_id!r} references missing analyzer {analyzer_id!r}"
            )
        if not isinstance(layer_definition.visible, bool):
            raise RuntimeGraphValidationError(
                f"layer {layer_id!r} visibility must be a boolean"
            )
        capability = layer_capabilities.get(layer_type)
        if capability is None:
            raise RuntimeGraphValidationError(
                f"layer {layer_id!r} has unsupported type {layer_type!r}"
            )
        if analyzer.analyzer_type not in capability.analyzer_types:
            raise RuntimeGraphValidationError(
                f"layer {layer_id!r} type {layer_type!r} does not support "
                f"analyzer type {analyzer.analyzer_type!r}"
            )
        if output_backend not in capability.output_backends:
            raise RuntimeGraphValidationError(
                f"layer {layer_id!r} type {layer_type!r} does not support "
                f"output backend {output_backend!r}"
            )
        settings = _freeze_settings(
            f"layer {layer_id!r} settings",
            layer_definition.settings,
        )
        _validate_layer_settings(
            f"layer {layer_id!r}",
            capability.settings_validator,
            settings,
            output_backend,
        )
        layers.append(
            CompiledLayerDefinition(
                layer_id=layer_id,
                layer_type=layer_type,
                analyzer=analyzer,
                visible=layer_definition.visible,
                settings=settings,
            )
        )

    return CompiledRuntimePlan(
        schema_version=schema_version,
        output_backend=output_backend,
        analyzers=tuple(analyzers),
        canonical_analyzer_keys=tuple(canonical_keys),
        layers=tuple(layers),
    )


__all__ = [
    "AnalyzerCanonicalKey",
    "AnalyzerCapability",
    "AnalyzerDefinition",
    "CompiledAnalyzerDefinition",
    "CompiledLayerDefinition",
    "CompiledRuntimePlan",
    "DIRECT_OUTPUT_BACKEND",
    "FrozenSettings",
    "LayerCapability",
    "LayerDefinition",
    "MAX_RUNTIME_LAYERS",
    "PILLOW_BULK_OUTPUT_BACKEND",
    "RUNTIME_GRAPH_SCHEMA_VERSION",
    "RuntimeCapabilities",
    "RuntimeDefinition",
    "RuntimeGraphValidationError",
    "SUPPORTED_OUTPUT_BACKENDS",
    "compile_runtime_graph",
    "runtime_capabilities_to_dict",
    "runtime_definition_to_dict",
    "thaw_settings",
]
