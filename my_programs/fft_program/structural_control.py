"""Bounded off-frame structural compilation and frame-boundary activation."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import threading
import time
from typing import Any

from core.layer_plan import (
    CURRENT_BARS_LAYER_ID,
    CURRENT_BARS_LAYER_TYPE,
    OrderedLayerPlan,
    normalize_current_bars_settings,
)
from core.runtime_graph import (
    MAX_RUNTIME_LAYERS,
    LayerDefinition,
    RuntimeCapabilities,
    RuntimeDefinition,
    SUPPORTED_OUTPUT_BACKENDS,
    runtime_capabilities_to_dict,
    runtime_definition_to_dict,
)
from matrix_controller.runtime_transport import (
    FrameBoundaryRuntimeBridge,
    RuntimeControlError,
)
from runtime_control import PRESENTATION_COMMAND_PATHS


MAX_STRUCTURAL_OPERATIONS = 32
STRUCTURAL_OPERATION_NAMES = (
    "add_layer",
    "remove_layer",
    "set_layer_visibility",
    "set_layer_order",
    "set_layer_analyzer",
    "set_layer_settings",
    "set_output_backend",
)


class StructuralValidationError(ValueError):
    """One bounded client-visible structural validation failure."""

    def __init__(self, code: str, message: str, *, path: str) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


@dataclass(frozen=True, slots=True)
class _StructuralBase:
    definition: RuntimeDefinition
    revision: int


@dataclass(frozen=True, slots=True)
class _ActivationEnvelope:
    definition: RuntimeDefinition
    plan: OrderedLayerPlan
    base_revision: int
    legacy_presentation: Any | None
    unavailable_paths: frozenset[str]


def _object(
    value: object,
    *,
    required: frozenset[str],
    path: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise StructuralValidationError(
            "invalid_structure",
            "value must be an object",
            path=path,
        )
    fields = set(value)
    if fields != required:
        raise StructuralValidationError(
            "invalid_structure",
            "object contains missing or unknown fields",
            path=path,
        )
    return value


def _name(value: object, *, path: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise StructuralValidationError(
            "invalid_value",
            "value must be a non-empty trimmed string",
            path=path,
        )
    return value


def _settings(
    value: object,
    *,
    output_backend: str,
    path: str,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise StructuralValidationError(
            "invalid_value",
            "settings must be an object",
            path=path,
        )
    try:
        return normalize_current_bars_settings(value, output_backend)
    except ValueError as exc:
        raise StructuralValidationError(
            "invalid_value",
            str(exc),
            path=path,
        ) from exc


def apply_structural_operations(
    definition: RuntimeDefinition,
    operations: object,
    capabilities: RuntimeCapabilities,
) -> RuntimeDefinition:
    """Apply one strictly shaped batch to a private declarative candidate."""

    if (
        not isinstance(operations, list)
        or not operations
        or len(operations) > MAX_STRUCTURAL_OPERATIONS
    ):
        raise StructuralValidationError(
            "invalid_structure",
            f"operations must contain between 1 and {MAX_STRUCTURAL_OPERATIONS} items",
            path="args.operations",
        )

    analyzer_ids = {analyzer.analyzer_id for analyzer in definition.analyzers}
    layer_types = {capability.layer_type for capability in capabilities.layers}
    layers = list(definition.layers)
    output_backend = definition.output_backend

    def find_layer(layer_id: str, path: str) -> int:
        for index, layer in enumerate(layers):
            if layer.layer_id == layer_id:
                return index
        raise StructuralValidationError(
            "invalid_reference",
            f"unknown layer ID {layer_id!r}",
            path=path,
        )

    for index, raw_operation in enumerate(operations):
        path = f"args.operations[{index}]"
        if not isinstance(raw_operation, Mapping):
            raise StructuralValidationError(
                "invalid_structure", "operation must be an object", path=path
            )
        operation_name = raw_operation.get("op")
        if operation_name not in STRUCTURAL_OPERATION_NAMES:
            raise StructuralValidationError(
                "invalid_operation",
                "unknown structural operation",
                path=f"{path}.op",
            )

        if operation_name == "add_layer":
            operation = _object(
                raw_operation,
                required=frozenset({"op", "layer"}),
                path=path,
            )
            raw_layer = _object(
                operation["layer"],
                required=frozenset({
                    "layer_id", "layer_type", "analyzer_id", "visible", "settings"
                }),
                path=f"{path}.layer",
            )
            layer_id = _name(raw_layer["layer_id"], path=f"{path}.layer.layer_id")
            if any(layer.layer_id == layer_id for layer in layers):
                raise StructuralValidationError(
                    "invalid_value",
                    f"duplicate layer ID {layer_id!r}",
                    path=f"{path}.layer.layer_id",
                )
            if len(layers) >= MAX_RUNTIME_LAYERS:
                raise StructuralValidationError(
                    "limit_exceeded",
                    f"runtime supports at most {MAX_RUNTIME_LAYERS} layers",
                    path=f"{path}.layer",
                )
            layer_type = _name(
                raw_layer["layer_type"], path=f"{path}.layer.layer_type"
            )
            if layer_type not in layer_types:
                raise StructuralValidationError(
                    "invalid_reference",
                    f"unknown layer type {layer_type!r}",
                    path=f"{path}.layer.layer_type",
                )
            analyzer_id = _name(
                raw_layer["analyzer_id"], path=f"{path}.layer.analyzer_id"
            )
            if analyzer_id not in analyzer_ids:
                raise StructuralValidationError(
                    "invalid_reference",
                    f"unknown analyzer ID {analyzer_id!r}",
                    path=f"{path}.layer.analyzer_id",
                )
            visible = raw_layer["visible"]
            if not isinstance(visible, bool):
                raise StructuralValidationError(
                    "invalid_value",
                    "visibility must be a boolean",
                    path=f"{path}.layer.visible",
                )
            settings = _settings(
                raw_layer["settings"],
                output_backend=output_backend,
                path=f"{path}.layer.settings",
            )
            layers.append(
                LayerDefinition(
                    layer_id=layer_id,
                    layer_type=layer_type,
                    analyzer_id=analyzer_id,
                    visible=visible,
                    settings=settings,
                )
            )
            continue

        if operation_name == "set_output_backend":
            operation = _object(
                raw_operation,
                required=frozenset({"op", "output_backend"}),
                path=path,
            )
            output_backend = _name(
                operation["output_backend"], path=f"{path}.output_backend"
            )
            if output_backend not in SUPPORTED_OUTPUT_BACKENDS:
                raise StructuralValidationError(
                    "invalid_value",
                    f"unsupported output backend {output_backend!r}",
                    path=f"{path}.output_backend",
                )
            continue

        if operation_name == "set_layer_order":
            operation = _object(
                raw_operation,
                required=frozenset({"op", "layer_ids"}),
                path=path,
            )
            raw_ids = operation["layer_ids"]
            if not isinstance(raw_ids, list):
                raise StructuralValidationError(
                    "invalid_value",
                    "layer_ids must be a list",
                    path=f"{path}.layer_ids",
                )
            layer_ids = [
                _name(value, path=f"{path}.layer_ids[{item_index}]")
                for item_index, value in enumerate(raw_ids)
            ]
            current_ids = [layer.layer_id for layer in layers]
            if len(layer_ids) != len(set(layer_ids)) or set(layer_ids) != set(current_ids):
                raise StructuralValidationError(
                    "invalid_value",
                    "layer_ids must name every candidate layer exactly once",
                    path=f"{path}.layer_ids",
                )
            by_id = {layer.layer_id: layer for layer in layers}
            layers = [by_id[layer_id] for layer_id in layer_ids]
            continue

        required = {
            "remove_layer": frozenset({"op", "layer_id"}),
            "set_layer_visibility": frozenset({"op", "layer_id", "visible"}),
            "set_layer_analyzer": frozenset({"op", "layer_id", "analyzer_id"}),
            "set_layer_settings": frozenset({"op", "layer_id", "settings"}),
        }[operation_name]
        operation = _object(raw_operation, required=required, path=path)
        layer_id = _name(operation["layer_id"], path=f"{path}.layer_id")
        layer_index = find_layer(layer_id, f"{path}.layer_id")

        if operation_name == "remove_layer":
            layers.pop(layer_index)
        elif operation_name == "set_layer_visibility":
            visible = operation["visible"]
            if not isinstance(visible, bool):
                raise StructuralValidationError(
                    "invalid_value",
                    "visibility must be a boolean",
                    path=f"{path}.visible",
                )
            layers[layer_index] = replace(layers[layer_index], visible=visible)
        elif operation_name == "set_layer_analyzer":
            analyzer_id = _name(
                operation["analyzer_id"], path=f"{path}.analyzer_id"
            )
            if analyzer_id not in analyzer_ids:
                raise StructuralValidationError(
                    "invalid_reference",
                    f"unknown analyzer ID {analyzer_id!r}",
                    path=f"{path}.analyzer_id",
                )
            layers[layer_index] = replace(
                layers[layer_index], analyzer_id=analyzer_id
            )
        else:
            settings = _settings(
                operation["settings"],
                output_backend=output_backend,
                path=f"{path}.settings",
            )
            layers[layer_index] = replace(layers[layer_index], settings=settings)

    normalized_layers = []
    for layer_index, layer in enumerate(layers):
        if layer.layer_type == CURRENT_BARS_LAYER_TYPE:
            normalized_layers.append(
                replace(
                    layer,
                    settings=_settings(
                        layer.settings,
                        output_backend=output_backend,
                        path=f"structure.layers[{layer_index}].settings",
                    ),
                )
            )
        else:
            normalized_layers.append(layer)

    return replace(
        definition,
        layers=tuple(normalized_layers),
        output_backend=output_backend,
    )


class StructuralRuntimeCoordinator:
    """Coordinate listener-thread compilation with render-boundary activation."""

    def __init__(
        self,
        *,
        controller: Any,
        active_plan: Any,
        initial_definition: RuntimeDefinition,
        capabilities: RuntimeCapabilities,
        compile_definition: Callable[[RuntimeDefinition], OrderedLayerPlan],
        bridge: FrameBoundaryRuntimeBridge,
        deadline_seconds: float = 0.7,
    ) -> None:
        self._controller = controller
        self._active_plan = active_plan
        self._active_definition = initial_definition
        self._capabilities = capabilities
        self._compile_definition = compile_definition
        self._bridge = bridge
        self._deadline_seconds = float(deadline_seconds)
        if not self._deadline_seconds > 0:
            raise ValueError("structural deadline must be greater than zero")
        self._transaction_lock = threading.Lock()
        # The trusted catalog is immutable, so validate and describe it once
        # instead of on every render-thread snapshot. controller.snapshot()
        # copies it through _json_safe; callers must not mutate it.
        capability_data = runtime_capabilities_to_dict(capabilities)
        capability_data["operations"] = list(STRUCTURAL_OPERATION_NAMES)
        capability_data["limits"]["max_operations"] = MAX_STRUCTURAL_OPERATIONS  # type: ignore[index]
        self._capability_data = capability_data
        controller.bind_structure_snapshot(self.structure_snapshot)

    def _fold_live_presentation(self) -> RuntimeDefinition:
        live_settings = self._controller.current_presentation_settings()
        layers = tuple(
            replace(layer, settings=live_settings)
            if layer.layer_id == CURRENT_BARS_LAYER_ID
            else layer
            for layer in self._active_definition.layers
        )
        return replace(self._active_definition, layers=layers)

    def structure_snapshot(self) -> dict[str, object]:
        definition = self._fold_live_presentation()
        public = runtime_definition_to_dict(definition)
        # Analyzer settings in the definition are assembly-time values; live
        # noise, weight, and zoom controls own the current ones (see "state" and
        # "controls"). Publish analyzer identity only so clients can't read stale
        # analysis settings here.
        for analyzer in public["analyzers"]:  # type: ignore[union-attr]
            analyzer.pop("settings", None)
        public["available_analyzer_ids"] = [
            analyzer.analyzer_id for analyzer in definition.analyzers
        ]
        public["capabilities"] = self._capability_data
        return public

    def handle_runtime_operation(self, operation: str, payload: Any) -> Any:
        """Run structural compilation here; bridge ordinary work to rendering."""

        if operation != "command" or not isinstance(payload, Mapping):
            return self._bridge.handle(operation, payload)
        command = payload.get("command")
        if (
            not isinstance(command, Mapping)
            or command.get("operation") != "action"
            or command.get("action") != "structure.apply"
        ):
            return self._bridge.handle(operation, payload)

        if not self._transaction_lock.acquire(blocking=False):
            raise RuntimeControlError(
                "busy", "another structural request is already in flight"
            )
        try:
            expected_revision = payload.get("expected_revision")
            if (
                isinstance(expected_revision, bool)
                or not isinstance(expected_revision, int)
                or expected_revision < 0
            ):
                raise RuntimeControlError(
                    "invalid_value",
                    "expected_revision must be a non-negative integer",
                    path="expected_revision",
                )
            args = command.get("args")
            if not isinstance(args, Mapping) or set(args) != {"operations"}:
                raise RuntimeControlError(
                    "invalid_structure",
                    "structure.apply args must contain operations only",
                    path="args",
                )

            # One deadline bounds capture, compilation, and activation together so
            # the transaction finishes before the supervisor's request timeout. An
            # expired wait cancels its queued item; the render thread skips it.
            deadline = time.monotonic() + self._deadline_seconds

            def remaining() -> float:
                value = deadline - time.monotonic()
                if value <= 0:
                    raise RuntimeControlError(
                        "timeout", "structural request exceeded its deadline"
                    )
                return value

            base = self._bridge.handle(
                "structure.capture",
                {"expected_revision": expected_revision},
                timeout=remaining(),
            )
            try:
                candidate_definition = apply_structural_operations(
                    base.definition,
                    args["operations"],
                    self._capabilities,
                )
                candidate_plan = self._compile_definition(candidate_definition)
            except StructuralValidationError as exc:
                raise RuntimeControlError(
                    exc.code, str(exc), path=exc.path
                ) from exc
            except ValueError as exc:
                raise RuntimeControlError(
                    "invalid_structure", str(exc), path="args.operations"
                ) from exc

            # Never submit an already timed-out compile result.
            remaining()

            legacy = next(
                (
                    layer.presentation_state
                    for layer in candidate_plan.layers
                    if layer.layer_id == CURRENT_BARS_LAYER_ID
                ),
                None,
            )
            unavailable = candidate_plan.incompatible_runtime_paths
            if legacy is None:
                unavailable = unavailable | PRESENTATION_COMMAND_PATHS
            envelope = _ActivationEnvelope(
                definition=candidate_definition,
                plan=candidate_plan,
                base_revision=base.revision,
                legacy_presentation=legacy,
                unavailable_paths=unavailable,
            )
            self._bridge.handle(
                "structure.activate",
                envelope,
                timeout=remaining(),
            )
            # The render owner only swaps prepared references and signals this
            # listener. Build the JSON-shaped response off the frame path.
            return self._controller.snapshot()
        finally:
            self._transaction_lock.release()

    def process_pending(self, *, max_items: int = 4) -> int:
        """Process bounded control work at the beginning of a render frame."""

        return self._bridge.process_pending(
            self._handle_frame_boundary,
            max_items=max_items,
        )

    def _handle_frame_boundary(self, operation: str, payload: Any) -> Any:
        if operation == "structure.capture":
            expected_revision = payload["expected_revision"]
            if expected_revision != self._controller.revision:
                raise RuntimeControlError(
                    "revision_conflict",
                    "runtime state changed since this request was read",
                )
            return _StructuralBase(
                definition=self._fold_live_presentation(),
                revision=self._controller.revision,
            )
        if operation == "structure.activate":
            envelope = payload
            if envelope.base_revision != self._controller.revision:
                raise RuntimeControlError(
                    "revision_conflict",
                    "runtime state changed during structural compilation",
                )
            self._active_plan.stage(envelope.plan)
            self._active_plan.begin_frame()
            self._active_definition = envelope.definition
            self._controller.rebind_presentation(
                envelope.legacy_presentation,
                unavailable_command_paths=envelope.unavailable_paths,
            )
            self._controller.revision += 1
            return self._controller.revision
        return self._controller.handle_runtime_operation(operation, payload)

    def close(self) -> None:
        self._bridge.close()


__all__ = [
    "MAX_STRUCTURAL_OPERATIONS",
    "STRUCTURAL_OPERATION_NAMES",
    "StructuralRuntimeCoordinator",
    "StructuralValidationError",
    "apply_structural_operations",
]
