"""Whole-structure validation, off-frame compilation, and frame-boundary activation.

The FFT's only configuration path is one declarative structure document
(web-control-refactor D-015)::

    {"output_backend": "direct",
     "layers": [{"layer_id": "bass", "layer_type": "current-bars",
                 "analyzer_id": "main-audio", "visible": true,
                 "settings": {...every current-bars setting...}}]}

It arrives either as the ``structure.set`` action's ``args`` or, at launch, through
``RGB_MATRIX_LAUNCH_STRUCTURE`` (D-016). Both use the same validator and compiler.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
import json
import threading
import time
from typing import Any

from core.layer_plan import (
    LayerPlanCompilationError,
    LayerSettingsError,
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
    MAX_LAUNCH_STRUCTURE_BYTES,
    FrameBoundaryRuntimeBridge,
    RuntimeControlError,
    RuntimeOperationTimeout,
)
from themes import list_themes
from themes.custom import NAME_PATTERN as CUSTOM_THEME_NAME


STRUCTURE_SET_ACTION = "structure.set"
STRUCTURE_DOCUMENT_FIELDS = frozenset({"output_backend", "layers"})
LAYER_DOCUMENT_FIELDS = frozenset({
    "layer_id", "layer_type", "analyzer_id", "visible", "settings"
})
# The descriptor's default theme; a launch layer naming a missing custom theme
# uses it (fft-themes D-013).
FALLBACK_THEME = "ocean"


def launch_theme_fallbacks(
    document: object,
) -> tuple[object, list[dict[str, str]]]:
    """Replace unregistered custom themes in a launch document with the default.

    Custom themes load only at launch (fft-themes D-005), so a preset can name one
    this run doesn't have. Only names shaped like a custom theme (``my_*``) fall
    back; anything else is left for the validator to refuse. Returns the document
    (copied only where it changed) and one ``{path, layer_id, theme}`` per change.
    Anything that isn't the expected shape is returned unchanged for the validator.
    """

    if not isinstance(document, dict) or not isinstance(document.get("layers"), list):
        return document, []
    registered = set(list_themes())
    layers = list(document["layers"])
    fallbacks: list[dict[str, str]] = []
    for index, layer in enumerate(layers):
        if not isinstance(layer, dict) or not isinstance(layer.get("settings"), dict):
            continue
        theme = layer["settings"].get("theme")
        if (
            not isinstance(theme, str)
            or theme in registered
            or not CUSTOM_THEME_NAME.fullmatch(theme)
        ):
            continue
        layers[index] = {**layer, "settings": {**layer["settings"], "theme": FALLBACK_THEME}}
        layer_id = layer.get("layer_id")
        fallbacks.append({
            "path": f"structure.layers[{index}].settings.theme",
            "layer_id": layer_id if isinstance(layer_id, str) else "",
            "theme": theme,
        })
    if not fallbacks:
        return document, []
    return {**document, "layers": layers}, fallbacks


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
    commit: Callable[[], None]
    rollback: Callable[[], None]
    # Set by the render thread when it begins activating; from then on it alone
    # owns rollback (D-018).
    claimed: threading.Event = field(default_factory=threading.Event)
    # Set by the render thread once it has committed or rolled back.
    finished: threading.Event = field(default_factory=threading.Event)


@dataclass(frozen=True, slots=True)
class PreparedStructuralPlan:
    """Executable candidate plus registry transaction hooks."""

    plan: OrderedLayerPlan
    commit: Callable[[], None]
    rollback: Callable[[], None]


def _exact_object(
    value: object,
    *,
    fields: frozenset[str],
    path: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise StructuralValidationError(
            "invalid_structure", "value must be an object", path=path
        )
    present = set(value)
    if present != fields:
        detail = []
        missing = sorted(fields - present)
        unknown = sorted(str(name) for name in present - fields)
        if missing:
            detail.append(f"missing {missing!r}")
        if unknown:
            detail.append(f"unknown {unknown!r}")
        raise StructuralValidationError(
            "invalid_structure",
            "object fields are invalid: " + "; ".join(detail),
            path=path,
        )
    return value


def _name(value: object, *, path: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 64
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise StructuralValidationError(
            "invalid_value",
            "value must be a non-empty trimmed string of at most 64 characters",
            path=path,
        )
    return value


def _settings(
    value: object,
    *,
    output_backend: str,
    path: str,
) -> dict[str, object]:
    try:
        return normalize_current_bars_settings(value, output_backend)  # type: ignore[arg-type]
    except LayerSettingsError as exc:
        raise StructuralValidationError(
            "invalid_value",
            str(exc),
            path=f"{path}.{exc.field}" if exc.field else path,
        ) from exc
    except ValueError as exc:
        raise StructuralValidationError("invalid_value", str(exc), path=path) from exc


BIN_LAYOUT_RULES = {
    # The named rules of core.analysis_plan.projection_layout (D-023). The bin
    # graph recomputes a draft layer's bars from these and the layer's own
    # min_freq, max_freq, and weight_points.
    "spacing": "log",
    "bar_center": "arithmetic-midpoint",
    "bar_bins": "half-open",
    "weight_curve": "piecewise-linear-position",
}


def bin_layout_snapshot(definition: RuntimeDefinition) -> dict[str, object] | None:
    """Describe the inputs each layer's bars are computed from.

    Every layer reads the one standard analyzer, so its sample rate, FFT size,
    and bar count apply to every layer. ``None`` when there is no analyzer.
    """

    if not definition.analyzers:
        return None
    analyzer = definition.analyzers[0]
    return {
        "analyzer_id": analyzer.analyzer_id,
        "sample_rate": analyzer.settings.get("sample_rate"),
        "fft_size": analyzer.settings.get("fft_size"),
        "num_bars": analyzer.result_shape[0],
        **BIN_LAYOUT_RULES,
    }


def structure_document_to_definition(
    base: RuntimeDefinition,
    document: object,
    capabilities: RuntimeCapabilities,
) -> RuntimeDefinition:
    """Validate one complete structure document against the base's analyzers.

    The result replaces every layer and the output backend of ``base``. Analyzers
    are launch-owned and never change here.
    """

    document = _exact_object(
        document, fields=STRUCTURE_DOCUMENT_FIELDS, path="structure"
    )
    output_backend = _name(
        document["output_backend"], path="structure.output_backend"
    )
    if output_backend not in SUPPORTED_OUTPUT_BACKENDS:
        raise StructuralValidationError(
            "invalid_value",
            f"unsupported output backend {output_backend!r}",
            path="structure.output_backend",
        )
    raw_layers = document["layers"]
    if not isinstance(raw_layers, list):
        raise StructuralValidationError(
            "invalid_structure", "layers must be a list", path="structure.layers"
        )
    if len(raw_layers) > MAX_RUNTIME_LAYERS:
        raise StructuralValidationError(
            "limit_exceeded",
            f"runtime supports at most {MAX_RUNTIME_LAYERS} layers",
            path="structure.layers",
        )

    analyzer_ids = {analyzer.analyzer_id for analyzer in base.analyzers}
    layer_types = {capability.layer_type for capability in capabilities.layers}
    layers: list[LayerDefinition] = []
    seen_ids: set[str] = set()
    for index, raw_layer in enumerate(raw_layers):
        path = f"structure.layers[{index}]"
        layer = _exact_object(raw_layer, fields=LAYER_DOCUMENT_FIELDS, path=path)
        layer_id = _name(layer["layer_id"], path=f"{path}.layer_id")
        if layer_id in seen_ids:
            raise StructuralValidationError(
                "invalid_value",
                f"duplicate layer ID {layer_id!r}",
                path=f"{path}.layer_id",
            )
        seen_ids.add(layer_id)
        layer_type = _name(layer["layer_type"], path=f"{path}.layer_type")
        if layer_type not in layer_types:
            raise StructuralValidationError(
                "invalid_reference",
                f"unknown layer type {layer_type!r}",
                path=f"{path}.layer_type",
            )
        analyzer_id = _name(layer["analyzer_id"], path=f"{path}.analyzer_id")
        if analyzer_id not in analyzer_ids:
            raise StructuralValidationError(
                "invalid_reference",
                f"unknown analyzer ID {analyzer_id!r}",
                path=f"{path}.analyzer_id",
            )
        visible = layer["visible"]
        if not isinstance(visible, bool):
            raise StructuralValidationError(
                "invalid_value",
                "visibility must be a boolean",
                path=f"{path}.visible",
            )
        layers.append(
            LayerDefinition(
                layer_id=layer_id,
                layer_type=layer_type,
                analyzer_id=analyzer_id,
                visible=visible,
                settings=_settings(
                    layer["settings"],
                    output_backend=output_backend,
                    path=f"{path}.settings",
                ),
            )
        )

    return replace(base, layers=tuple(layers), output_backend=output_backend)


class StructuralRuntimeCoordinator:
    """Coordinate listener-thread compilation with render-boundary activation."""

    def __init__(
        self,
        *,
        controller: Any,
        active_plan: Any,
        initial_definition: RuntimeDefinition,
        capabilities: RuntimeCapabilities,
        compile_definition: Callable[
            [RuntimeDefinition], OrderedLayerPlan | PreparedStructuralPlan
        ],
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
        # An activation the render thread started but had not finished when the
        # listener gave up. The next transaction waits for it (as busy) instead
        # of opening a second projection transaction.
        self._unfinished_activation: _ActivationEnvelope | None = None
        self._launch_error: dict[str, str] | None = None
        # Launch layers that use the default theme because theirs isn't
        # registered (D-013); cleared, like the launch error, by structure.set.
        self._theme_fallbacks: list[dict[str, str]] = []
        # The trusted catalog is immutable, so validate and describe it once
        # instead of on every render-thread snapshot. controller.snapshot()
        # copies it through _json_safe; callers must not mutate it.
        capability_data = runtime_capabilities_to_dict(capabilities)
        capability_data["actions"] = [STRUCTURE_SET_ACTION]
        self._capability_data = capability_data
        controller.bind_structure_snapshot(self.structure_snapshot)

    # ------------------------------------------------------------------
    # Snapshot and launch
    # ------------------------------------------------------------------
    def structure_snapshot(self) -> dict[str, object]:
        public = runtime_definition_to_dict(self._active_definition)
        # Layers are published exactly in structure.set document shape, so a
        # client can send them back unchanged (D-015).
        for layer in public["layers"]:  # type: ignore[union-attr]
            layer.pop("schema_version", None)
        public["available_analyzer_ids"] = [
            analyzer.analyzer_id for analyzer in self._active_definition.analyzers
        ]
        public["bin_layout"] = bin_layout_snapshot(self._active_definition)
        public["capabilities"] = self._capability_data
        if self._launch_error is not None:
            public["launch_error"] = dict(self._launch_error)
        if self._theme_fallbacks:
            public["theme_fallbacks"] = [dict(item) for item in self._theme_fallbacks]
        return public

    def apply_launch_structure(self, raw: str) -> bool:
        """Activate the launch document, or keep the default and record why not.

        Call on the render thread after audio setup and before the control
        listener starts, so no transaction can be in flight. Returns whether the
        launch document is now active.
        """

        try:
            if len(raw.encode("utf-8")) > MAX_LAUNCH_STRUCTURE_BYTES:
                raise StructuralValidationError(
                    "invalid_structure",
                    "launch structure is too large",
                    path="structure",
                )
            try:
                document = json.loads(raw)
            except (ValueError, RecursionError) as exc:
                raise StructuralValidationError(
                    "invalid_structure",
                    "launch structure is not valid JSON",
                    path="structure",
                ) from exc
            # Launch only: a live structure.set comes from a page that knows
            # this run's themes, so it is still refused (D-013).
            document, fallbacks = launch_theme_fallbacks(document)
            candidate = structure_document_to_definition(
                self._active_definition, document, self._capabilities
            )
            plan, commit, _rollback = self._prepare(candidate)
        except RuntimeControlError as exc:
            self._launch_error = {
                "code": exc.code,
                "message": exc.message,
                "path": exc.path or "structure",
            }
            return False
        except StructuralValidationError as exc:
            self._launch_error = {
                "code": exc.code, "message": str(exc), "path": exc.path
            }
            return False
        self._active_plan.stage(plan)
        self._active_plan.begin_frame()
        commit()
        self._active_definition = candidate
        self._theme_fallbacks = fallbacks
        return True

    # ------------------------------------------------------------------
    # Listener thread
    # ------------------------------------------------------------------
    def _prepare(
        self,
        candidate: RuntimeDefinition,
    ) -> tuple[OrderedLayerPlan, Callable[[], None], Callable[[], None]]:
        """Compile off the frame path; map compile errors to client errors."""

        try:
            compiled = self._compile_definition(candidate)
        except LayerPlanCompilationError as exc:
            path = "structure.layers"
            for index, layer in enumerate(candidate.layers):
                if layer.layer_id == exc.layer_id:
                    path = f"structure.layers[{index}].settings"
                    break
            raise RuntimeControlError("invalid_value", str(exc), path=path) from exc
        except ValueError as exc:
            raise RuntimeControlError(
                "invalid_structure", str(exc), path="structure"
            ) from exc
        if isinstance(compiled, PreparedStructuralPlan):
            return compiled.plan, compiled.commit, compiled.rollback
        return compiled, (lambda: None), (lambda: None)

    def handle_runtime_operation(self, operation: str, payload: Any) -> Any:
        """Run structural compilation here; bridge ordinary work to rendering."""

        if operation != "command" or not isinstance(payload, Mapping):
            return self._bridge.handle(operation, payload)
        command = payload.get("command")
        if (
            not isinstance(command, Mapping)
            or command.get("operation") != "action"
            or command.get("action") != STRUCTURE_SET_ACTION
        ):
            return self._bridge.handle(operation, payload)

        if not self._transaction_lock.acquire(blocking=False):
            raise RuntimeControlError(
                "busy", "another structural request is already in flight"
            )
        try:
            unfinished = self._unfinished_activation
            if unfinished is not None:
                if not unfinished.finished.is_set():
                    raise RuntimeControlError(
                        "busy", "the previous structure is still being activated"
                    )
                self._unfinished_activation = None

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
            document = command.get("args")

            # One deadline bounds capture, compilation, and activation together so
            # the transaction finishes before the supervisor's request timeout.
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
                candidate = structure_document_to_definition(
                    base.definition, document, self._capabilities
                )
            except StructuralValidationError as exc:
                raise RuntimeControlError(exc.code, str(exc), path=exc.path) from exc
            plan, commit, rollback = self._prepare(candidate)

            envelope = _ActivationEnvelope(
                definition=candidate,
                plan=plan,
                base_revision=base.revision,
                commit=commit,
                rollback=rollback,
            )
            # Rollback ownership is exclusive (D-018): this thread rolls back only
            # an activation the render thread never started. Once started, the
            # render thread commits or rolls back itself.
            try:
                self._bridge.handle(
                    "structure.activate",
                    envelope,
                    timeout=remaining(),
                )
            except RuntimeOperationTimeout as exc:
                if exc.started:
                    self._unfinished_activation = envelope
                else:
                    rollback()
                raise
            except BaseException:
                # Roll back only if the render thread never claimed it (never
                # queued, queue full, bridge closed). A claimed activation's
                # failure was handled there, possibly after the plan was swapped
                # in, when rolling back would free projections it reads.
                if not envelope.claimed.is_set():
                    rollback()
                raise
            # The render owner only swaps prepared references. Build the
            # JSON-shaped response off the frame path.
            return self._controller.snapshot()
        finally:
            self._transaction_lock.release()

    # ------------------------------------------------------------------
    # Render thread
    # ------------------------------------------------------------------
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
                definition=self._active_definition,
                revision=self._controller.revision,
            )
        if operation == "structure.activate":
            return self._activate(payload)
        return self._controller.handle_runtime_operation(operation, payload)

    def _activate(self, envelope: _ActivationEnvelope) -> int:
        envelope.claimed.set()
        try:
            try:
                if envelope.base_revision != self._controller.revision:
                    raise RuntimeControlError(
                        "revision_conflict",
                        "runtime state changed during structural compilation",
                    )
                self._active_plan.stage(envelope.plan)
            except BaseException:
                # Not swapped in yet: the candidate's projections are unused.
                envelope.rollback()
                raise
            # Nothing reads projections between frames, so the swap and the
            # commit (which drops views only the old plan used) are one step
            # here. Once swapped in, the candidate's projections are live and are
            # never rolled back.
            self._active_plan.begin_frame()
            envelope.commit()
            self._active_definition = envelope.definition
            self._launch_error = None
            self._theme_fallbacks = []
            self._controller.revision += 1
            return self._controller.revision
        finally:
            envelope.finished.set()

    def close(self) -> None:
        self._bridge.close()


__all__ = [
    "LAYER_DOCUMENT_FIELDS",
    "PreparedStructuralPlan",
    "STRUCTURE_DOCUMENT_FIELDS",
    "STRUCTURE_SET_ACTION",
    "StructuralRuntimeCoordinator",
    "StructuralValidationError",
    "structure_document_to_definition",
]
