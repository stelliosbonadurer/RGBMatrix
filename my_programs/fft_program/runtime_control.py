"""Typed, in-process controls for the FFT visualizer.

This module is intentionally independent of the HTTP service. A keyboard adapter,
the Unix-socket listener, and tests all call :meth:`apply_request` at a
frame boundary.  The controller owns the live objects; callers never need to
re-create a second copy of visualization state.

The request envelope is deliberately small and JSON-shaped so the child IPC adapter
can pass it through without knowing FFT internals::

    {"protocol_version": 1, "run_id": "...", "expected_revision": 3,
     "operation": "commands", "commands": [
         {"op": "set", "path": "visualizer.gradient", "value": true}
     ]}

``apply_request`` performs validation and mutation synchronously.  A socket reader
should queue envelopes and invoke it from the render loop; this class does not
create a worker thread or mutate matrix/audio state from a request thread.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

from themes import get_theme, list_themes
from matrix_controller.runtime_transport import RuntimeControlError as TransportRuntimeControlError


PROTOCOL_VERSION = 1

# These bounds are deliberately code-owned. The web client renders them, but the
# child validates them again before touching live DSP state.
NUMERIC_CONTROLS = {
    "settings.noise_floor": ("Noise floor", "audio", 0.0, 100.0, 0.05, "live"),
    "settings.low_freq_weight": ("Low-frequency weight", "audio", 0.0, 100.0, 0.05, "rebuild"),
    "settings.high_freq_weight": ("High-frequency weight", "audio", 0.0, 100.0, 0.1, "rebuild"),
    "settings.silence_threshold": ("Silence threshold", "audio", 0.0, 100.0, 0.01, "live"),
    "settings.sensitivity_scalar": ("Sensitivity scale", "scaling", 0.01, 20.0, 0.05, "live"),
    "settings.smoothing_rise": ("Rise smoothing", "scaling", 0.0, 1.0, 0.01, "live"),
    "settings.smoothing_fall": ("Fall smoothing", "scaling", 0.0, 1.0, 0.01, "live"),
    "settings.overflow_multiplier": ("Overflow multiplier", "visualizer", 0.1, 10.0, 0.1, "live"),
    "settings.peak_fall_speed": ("Peak fall speed", "peaks", 0.0, 1.0, 0.01, "live"),
    "settings.peak_hold_frames": ("Peak hold frames", "peaks", 0, 600, 1, "live"),
}


class RuntimeControlError(ValueError):
    """A bounded, machine-readable error returned to a control client."""

    def __init__(self, code: str, message: str, *, path: str | None = None):
        super().__init__(message)
        self.code = code
        self.path = path


@dataclass(frozen=True)
class RuntimeCommand:
    """One semantic state change or runtime action."""

    op: str
    path: str = ""
    value: Any = None
    args: Dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "RuntimeCommand":
        if not isinstance(value, dict):
            raise RuntimeControlError("invalid_command", "command must be an object")
        op = value.get("op")
        if not isinstance(op, str):
            raise RuntimeControlError("invalid_command", "command op must be a string")
        path = value.get("path", "")
        if not isinstance(path, str):
            raise RuntimeControlError("invalid_command", "command path must be a string")
        args = value.get("args")
        if args is not None and not isinstance(args, dict):
            raise RuntimeControlError("invalid_command", "command args must be an object")
        return cls(op=op, path=path, value=value.get("value"), args=args)


def _json_safe(value: Any) -> Any:
    """Return a JSON-safe copy without leaking arbitrary object attributes."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    # Numpy scalar values occasionally appear in settings/theme state.
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)


def _dynamic_state(theme: Any) -> Dict[str, Any]:
    """Serialize the public controls of a dynamic theme, if it has them."""
    if theme is None or not hasattr(theme, "cycle_speed"):
        return {}
    return {
        "start_hue": getattr(theme, "start_hue", None),
        "cycle_speed": getattr(theme, "cycle_speed", None),
        "saturation": getattr(theme, "saturation", None),
        "value": getattr(theme, "value", None),
        "column_spread": getattr(theme, "column_spread", None),
        "height_spread": getattr(theme, "height_spread", None),
        "frozen": getattr(theme, "frozen", False),
    }


class FftRuntimeController:
    """Own and mutate live FFT visualization state through typed commands.

    ``ensure_layer_pipeline`` is supplied by ``main.py`` because it owns audio
    layer/scaler initialization.  The callback is only called when layered mode is
    enabled, and always before a layer command is applied.
    """

    def __init__(
        self,
        *,
        settings: Any,
        visualizer: Any,
        audio: Any,
        theme_cycler: Any,
        width: int,
        height: int,
        ensure_layer_pipeline: Optional[Callable[[], None]] = None,
        run_id: str = "local",
        visualization_id: str = "fft",
    ):
        self.settings = settings
        self.visualizer = visualizer
        self.audio = audio
        self.theme_cycler = theme_cycler
        self.width = int(width)
        self.height = int(height)
        self.ensure_layer_pipeline = ensure_layer_pipeline or (lambda: None)
        self.run_id = str(run_id)
        self.visualization_id = str(visualization_id)
        self.revision = 0

    # ------------------------------------------------------------------
    # Public protocol entry points
    # ------------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """Return the authoritative current state and control descriptors."""
        runtime_state = self.runtime_state()
        state = runtime_state["visualizer"]
        state["settings"] = runtime_state["settings"]
        state["frequency"] = {
            "min_freq": self.settings.frequency.active_min_freq,
            "max_freq": self.settings.frequency.active_max_freq,
            "zoom_preset_index": self.settings.frequency.zoom_preset_index,
            "zoom_presets": list(self.settings.frequency.zoom_presets),
        }
        return {
            "protocol_version": PROTOCOL_VERSION,
            "run_id": self.run_id,
            "visualization_id": self.visualization_id,
            "revision": self.revision,
            "connected": True,
            "state": _json_safe(state),
            "controls": self.control_schema(),
        }

    def apply_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and apply one IPC-shaped request at a frame boundary.

        The method returns an envelope instead of raising for expected client
        errors, which lets the socket adapter serialize it directly. The
        caller should invoke this method from the render thread after draining a
        bounded queue.
        """
        try:
            if not isinstance(request, dict):
                raise RuntimeControlError("invalid_request", "request must be an object")
            if request.get("protocol_version", PROTOCOL_VERSION) != PROTOCOL_VERSION:
                raise RuntimeControlError("unsupported_version", "unsupported runtime protocol")
            requested_run = request.get("run_id")
            if requested_run is not None and requested_run != self.run_id:
                raise RuntimeControlError("stale_run", "request belongs to another visualization run")
            expected = request.get("expected_revision")
            if expected is not None and expected != self.revision:
                raise RuntimeControlError("revision_conflict", "runtime state changed since this request was read")

            operation = request.get("operation", "commands")
            if operation == "snapshot":
                return {"ok": True, "snapshot": self.snapshot()}
            if operation not in {"commands", "command"}:
                raise RuntimeControlError("invalid_operation", "operation must be snapshot or commands")

            raw_commands = request.get("commands")
            if raw_commands is None and request.get("command") is not None:
                raw_commands = [request["command"]]
            if not isinstance(raw_commands, list) or not raw_commands:
                raise RuntimeControlError("invalid_command", "commands must be a non-empty list")
            if len(raw_commands) > 32:
                raise RuntimeControlError("busy", "command batch is too large")
            commands = [RuntimeCommand.from_dict(command) for command in raw_commands]
            self.apply_commands(commands)
            return {"ok": True, "snapshot": self.snapshot()}
        except RuntimeControlError as exc:
            error = {"code": exc.code, "message": str(exc)}
            if exc.path:
                error["path"] = exc.path
            return {"ok": False, "error": error, "snapshot": self.snapshot()}

    def handle_runtime_operation(self, operation: str, payload: Any) -> Dict[str, Any]:
        """Adapt the supervisor transport contract to the typed local protocol.

        The generic ``FrameBoundaryRuntimeBridge`` invokes this method from the
        render thread. It contains protocol translation only and never owns socket
        or threading policy.
        """
        payload = payload if isinstance(payload, dict) else {}
        if operation == "snapshot":
            request = {"operation": "snapshot", "run_id": self.run_id}
        elif operation == "command":
            command = payload.get("command")
            if not isinstance(command, dict):
                raise TransportRuntimeControlError("invalid_command", "runtime command must be an object")
            request = {
                "operation": "commands",
                "run_id": self.run_id,
                "expected_revision": payload.get("expected_revision"),
            }
            if command.get("operation") == "patch":
                changes = command.get("changes")
                if not isinstance(changes, list):
                    raise TransportRuntimeControlError("invalid_command", "runtime patch changes must be a list")
                if any(
                    not isinstance(change, dict)
                    or set(change) != {"path", "value"}
                    for change in changes
                ):
                    raise TransportRuntimeControlError(
                        "invalid_command",
                        "each runtime patch change must contain path and value only",
                    )
                request["commands"] = [
                    {"op": "set", "path": change.get("path"), "value": change.get("value")}
                    for change in changes
                ]
            elif command.get("operation") == "action":
                request["commands"] = [{
                    "op": "action",
                    "path": command.get("action"),
                    "args": command.get("args", {}),
                }]
            else:
                raise TransportRuntimeControlError("invalid_operation", "runtime command operation is unsupported")
        else:
            raise TransportRuntimeControlError("invalid_operation", "runtime operation is unsupported")

        response = self.apply_request(request)
        if not response.get("ok"):
            error = response.get("error") or {}
            raise TransportRuntimeControlError(
                str(error.get("code", "runtime_error")),
                str(error.get("message", "runtime command rejected")),
            )
        return response.get("snapshot", response)

    def apply_commands(self, commands: Iterable[RuntimeCommand]) -> None:
        """Validate a complete batch, then apply it atomically and bump revision."""
        commands = list(commands)
        if not commands:
            raise RuntimeControlError("invalid_command", "commands must not be empty")
        # Validation is intentionally a separate pass.  A bad second command can
        # therefore never leave the first command partially applied.
        for command in commands:
            self._validate_command(command)
        for command in commands:
            self._apply_command(command)
        self.revision += 1

    def handle_key(self, key: str | None) -> str | None:
        """Map the legacy key bindings to semantic commands and return a message."""
        if not key:
            return None
        command = self._key_command(key)
        if command is None:
            return None
        # A few key bindings are event-like actions; all still use the same
        # validation/mutation path as an IPC request.
        try:
            self.apply_commands([command])
        except RuntimeControlError as exc:
            return f"{exc.path or key}: {exc}"
        return self._key_message(key)

    # ------------------------------------------------------------------
    # Schema and persisted state
    # ------------------------------------------------------------------
    def control_schema(self) -> List[Dict[str, Any]]:
        """Describe the initial live controls for a schema-driven UI."""
        controls: List[Dict[str, Any]] = []
        for path, label, value, value_type, group in (
            ("visualizer.theme", "Theme", self._active_theme_name(), "choice", "theme"),
            ("visualizer.gradient", "Gradient", self._single_or_layer("gradient_mode", "gradient_enabled"), "boolean", "visualizer"),
            ("visualizer.overflow", "Overflow", self._single_or_layer("overflow_mode", "overflow_enabled"), "boolean", "visualizer"),
            ("visualizer.bars", "Bars", self._single_or_layer("bars_enabled", "bars_enabled"), "boolean", "visualizer"),
            ("visualizer.full", "Full mode", getattr(self.visualizer, "full_mode", False), "boolean", "visualizer"),
            ("visualizer.debug", "Debug mode", getattr(self.visualizer, "debug_mode", False), "boolean", "visualizer"),
            ("visualizer.layers_enabled", "Layered mode", getattr(self.visualizer, "layers_enabled", False), "boolean", "layers"),
            ("settings.shadow_enabled", "Shadows", bool(self.settings.shadow.enabled), "boolean", "settings"),
            ("settings.peak_enabled", "Peaks", bool(self.settings.peak.enabled), "boolean", "settings"),
            ("settings.peak_color_mode", "Peak color", self.settings.peak.color_mode, "choice", "settings"),
            ("settings.zoom_preset_index", "Zoom preset", self.settings.frequency.zoom_preset_index, "integer", "frequency"),
        ):
            descriptor: Dict[str, Any] = {
                "id": path,
                "path": path,
                "label": label,
                "type": value_type,
                "group": group,
                "apply_mode": "rebuild" if path == "settings.zoom_preset_index" else "live",
                "value": _json_safe(value),
            }
            if path == "settings.peak_color_mode":
                descriptor["choices"] = ["white", "bar", "contrast", "peak"]
            if path == "visualizer.theme":
                descriptor["choices"] = list_themes()
            if path == "settings.zoom_preset_index":
                descriptor["min"] = 0
                descriptor["max"] = max(0, len(self.settings.frequency.zoom_presets) - 1)
            controls.append(descriptor)
        numeric_values = self._numeric_values()
        for path, (label, group, minimum, maximum, step, apply_mode) in NUMERIC_CONTROLS.items():
            controls.append({
                "id": path,
                "path": path,
                "label": label,
                "type": "integer" if path == "settings.peak_hold_frames" else "number",
                "group": group,
                "apply_mode": apply_mode,
                "value": numeric_values[path],
                "min": minimum,
                "max": maximum,
                "step": step,
            })
        if getattr(self.visualizer, "layer_states", None):
            controls.append({
                "id": "visualizer.active_layer",
                "path": "visualizer.active_layer",
                "label": "Layer being edited",
                "type": "integer",
                "group": "layers",
                "apply_mode": "live",
                "value": int(getattr(self.visualizer, "active_layer", 0)),
                "choices": list(range(len(self.visualizer.layer_states))),
            })
        for index, state in enumerate(getattr(self.visualizer, "layer_states", []) or []):
            layer_id = self._layer_id(index)
            for field, label, value, value_type in (
                ("visible", "Visible", state.visible, "boolean"),
                ("bars", "Bars", state.bars_enabled, "boolean"),
                ("gradient", "Gradient", state.gradient_enabled, "boolean"),
                ("overflow", "Overflow", state.overflow_enabled, "boolean"),
                ("peaks", "Peaks", state.peak_enabled, "boolean"),
                ("theme", "Theme", state.theme_name, "choice"),
                ("boost", "Boost", state.boost, "number"),
            ):
                descriptor = {
                    "id": f"layers.{layer_id}.{field}",
                    "path": f"layers.{layer_id}.{field}",
                    "label": label,
                    "type": value_type,
                    "group": f"layer:{layer_id}",
                    "apply_mode": "live",
                    "value": _json_safe(value),
                }
                if field == "theme":
                    descriptor["choices"] = list_themes()
                if field == "boost":
                    descriptor.update({"min": 0, "max": 100, "step": 0.1})
                controls.append(descriptor)

        # Actions are schema entries as well, so adding controls to another
        # visualization does not require special-purpose browser code.
        controls.extend((
            self._action_descriptor("theme.previous", "Previous theme", "theme"),
            self._action_descriptor("theme.next", "Next theme", "theme"),
        ))
        layered = bool(getattr(self.visualizer, "layers_enabled", False))
        controls.extend((
            self._action_descriptor(
                "layer.move",
                "Move layer back",
                "layers",
                args={"direction": -1},
                available=layered and self._can_move_layer(-1),
            ),
            self._action_descriptor(
                "layer.move",
                "Move layer forward",
                "layers",
                args={"direction": 1},
                available=layered and self._can_move_layer(1),
            ),
        ))
        active_theme = self._active_theme()
        if hasattr(active_theme, "adjust_cycle_speed"):
            controls.extend((
                {
                    "id": "dynamic.cycle_speed",
                    "path": "dynamic.cycle_speed",
                    "label": "Animation speed",
                    "type": "number",
                    "group": "dynamic theme",
                    "apply_mode": "live",
                    "value": float(getattr(active_theme, "cycle_speed", 0.0)),
                    "min": 0.0,
                    "max": 5.0,
                    "step": 0.01,
                },
                {
                    "id": "dynamic.frozen",
                    "path": "dynamic.frozen",
                    "label": "Freeze animation",
                    "type": "boolean",
                    "group": "dynamic theme",
                    "apply_mode": "live",
                    "value": bool(getattr(active_theme, "frozen", False)),
                },
                self._action_descriptor(
                    "dynamic.reseed", "Reseed colors", "dynamic theme"
                ),
            ))
        return controls

    @staticmethod
    def _action_descriptor(
        action: str,
        label: str,
        group: str,
        *,
        args: Dict[str, Any] | None = None,
        available: bool = True,
    ) -> Dict[str, Any]:
        descriptor: Dict[str, Any] = {
            "id": f"action:{action}:{label}",
            "path": action,
            "action": action,
            "label": label,
            "button_label": label,
            "type": "action",
            "group": group,
            "apply_mode": "live",
            "available": available,
        }
        if args:
            descriptor["args"] = args
        return descriptor

    def runtime_state(self) -> Dict[str, Any]:
        """Return the legacy persisted-state shape using current live objects."""
        layer_states = []
        for state_obj in getattr(self.visualizer, "layer_states", []) or []:
            layer_states.append({
                "id": self._layer_id(len(layer_states)),
                "theme_name": getattr(state_obj, "theme_name", None),
                "bars_enabled": bool(getattr(state_obj, "bars_enabled", True)),
                "gradient_enabled": bool(getattr(state_obj, "gradient_enabled", False)),
                "overflow_enabled": bool(getattr(state_obj, "overflow_enabled", True)),
                "peak_enabled": bool(getattr(state_obj, "peak_enabled", False)),
                "visible": bool(getattr(state_obj, "visible", True)),
                "boost": getattr(state_obj, "boost", 1.0),
                "dynamic": _dynamic_state(getattr(state_obj, "theme", None)),
            })
        theme = getattr(self.visualizer, "theme", None)
        single_theme_name = getattr(theme, "name", getattr(self.settings.color, "theme", "ocean"))
        return _json_safe({
            "version": 1,
            "timestamp": time.time(),
            "settings": {
                "shadow_enabled": bool(self.settings.shadow.enabled),
                "peak_enabled": bool(self.settings.peak.enabled),
                "peak_color_mode": self.settings.peak.color_mode,
                "zoom_preset_index": self.settings.frequency.zoom_preset_index,
                **{
                    path.removeprefix("settings."): value
                    for path, value in self._numeric_values().items()
                },
            },
            "visualizer": {
                "layers_enabled": bool(getattr(self.visualizer, "layers_enabled", False)),
                "gradient_mode": bool(getattr(self.visualizer, "gradient_mode", False)),
                "overflow_mode": bool(getattr(self.visualizer, "overflow_mode", False)),
                "bars_enabled": bool(getattr(self.visualizer, "bars_enabled", True)),
                "full_mode": bool(getattr(self.visualizer, "full_mode", False)),
                "debug_mode": bool(getattr(self.visualizer, "debug_mode", False)),
                "active_layer": int(getattr(self.visualizer, "active_layer", 0)),
                "draw_order": list(getattr(self.visualizer, "draw_order", [])),
                "single_theme_name": single_theme_name,
                "single_dynamic": _dynamic_state(theme),
                "layer_states": layer_states,
            },
        })

    # ------------------------------------------------------------------
    # Command implementation
    # ------------------------------------------------------------------
    def _validate_command(self, command: RuntimeCommand) -> None:
        if command.op == "set":
            self._validate_set(command.path, command.value)
            return
        if command.op == "action":
            if command.path not in {
                "theme.next", "theme.previous", "zoom.next", "zoom.previous",
                "layer.move", "dynamic.adjust_speed", "dynamic.reseed", "dynamic.freeze",
            }:
                raise RuntimeControlError("invalid_path", "action is not supported", path=command.path)
            args = command.args or {}
            expected_args = {
                "layer.move": {"direction"},
                "dynamic.adjust_speed": {"delta"},
            }.get(command.path, set())
            if set(args) != expected_args:
                raise RuntimeControlError(
                    "invalid_command",
                    "action arguments contain missing or unknown fields",
                    path=command.path,
                )
            if command.path == "layer.move":
                if not getattr(self.visualizer, "layers_enabled", False):
                    raise RuntimeControlError(
                        "unavailable", "layered mode is not enabled", path=command.path
                    )
                direction = args.get("direction")
                if direction not in (-1, 1):
                    raise RuntimeControlError("invalid_value", "layer move direction must be -1 or 1", path=command.path)
                draw_order = list(getattr(self.visualizer, "draw_order", []))
                active_layer = getattr(self.visualizer, "active_layer", 0)
                try:
                    position = draw_order.index(active_layer)
                except ValueError:
                    position = -1
                if position < 0 or not 0 <= position + direction < len(draw_order):
                    raise RuntimeControlError("unavailable", "layer is already at the requested boundary", path=command.path)
            if command.path == "dynamic.adjust_speed":
                delta = args.get("delta")
                if (
                    isinstance(delta, bool)
                    or not isinstance(delta, (int, float))
                    or not math.isfinite(float(delta))
                    or abs(delta) > 1
                ):
                    raise RuntimeControlError("invalid_value", "dynamic speed delta must be between -1 and 1", path=command.path)
            if command.path.startswith("dynamic.") and not hasattr(
                self._active_theme(), "adjust_cycle_speed"
            ):
                raise RuntimeControlError(
                    "unavailable", "active theme is not dynamic", path=command.path
                )
            return
        raise RuntimeControlError("invalid_command", "unsupported command operation")

    def _validate_set(self, path: str, value: Any) -> None:
        boolean_paths = {
            "visualizer.gradient", "visualizer.overflow", "visualizer.bars",
            "visualizer.full", "visualizer.debug", "visualizer.layers_enabled",
            "visualizer.peak", "settings.shadow_enabled", "settings.peak_enabled",
            "dynamic.frozen",
        }
        if path in boolean_paths:
            if not isinstance(value, bool):
                raise RuntimeControlError("invalid_value", "value must be boolean", path=path)
            if path == "dynamic.frozen" and not hasattr(
                self._active_theme(), "frozen"
            ):
                raise RuntimeControlError(
                    "unavailable", "active theme is not dynamic", path=path
                )
            return
        if path == "settings.peak_color_mode":
            if value not in {"white", "bar", "contrast", "peak"}:
                raise RuntimeControlError("invalid_value", "unsupported peak color mode", path=path)
            return
        if path == "settings.zoom_preset_index":
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < len(self.settings.frequency.zoom_presets):
                raise RuntimeControlError("invalid_value", "zoom preset index is out of range", path=path)
            return
        if path in NUMERIC_CONTROLS:
            _label, _group, minimum, maximum, _step, _apply_mode = NUMERIC_CONTROLS[path]
            integer = path == "settings.peak_hold_frames"
            if (
                isinstance(value, bool)
                or (integer and not isinstance(value, int))
                or (not integer and not isinstance(value, (int, float)))
                or not math.isfinite(float(value))
                or not minimum <= value <= maximum
            ):
                kind = "an integer" if integer else "a number"
                raise RuntimeControlError(
                    "invalid_value",
                    f"value must be {kind} between {minimum:g} and {maximum:g}",
                    path=path,
                )
            return
        if path == "visualizer.active_layer":
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < len(getattr(self.visualizer, "layer_states", [])):
                raise RuntimeControlError("invalid_value", "active layer is out of range", path=path)
            return
        if path == "visualizer.theme":
            if value not in list_themes():
                raise RuntimeControlError("invalid_value", "unknown theme", path=path)
            return
        if path == "dynamic.cycle_speed":
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0 <= float(value) <= 5
            ):
                raise RuntimeControlError(
                    "invalid_value",
                    "dynamic cycle speed must be between 0 and 5",
                    path=path,
                )
            if not hasattr(self._active_theme(), "cycle_speed"):
                raise RuntimeControlError(
                    "unavailable", "active theme is not dynamic", path=path
                )
            return
        if path.startswith("layers."):
            parts = path.split(".")
            if len(parts) != 3 or parts[2] not in {"visible", "bars", "gradient", "overflow", "peaks", "theme", "boost"}:
                raise RuntimeControlError("invalid_path", "unsupported layer control", path=path)
            index = self._layer_index(parts[1])
            if index is None:
                raise RuntimeControlError("invalid_path", "unknown layer", path=path)
            field = parts[2]
            if field == "theme" and value not in list_themes():
                raise RuntimeControlError("invalid_value", "unknown theme", path=path)
            if field == "boost" and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or not 0 <= value <= 100
            ):
                raise RuntimeControlError("invalid_value", "layer boost must be between 0 and 100", path=path)
            if field in {"visible", "bars", "gradient", "overflow", "peaks"} and not isinstance(value, bool):
                raise RuntimeControlError("invalid_value", "layer flag must be boolean", path=path)
            return
        raise RuntimeControlError("invalid_path", "unsupported runtime control", path=path)

    def _apply_command(self, command: RuntimeCommand) -> None:
        if command.op == "set":
            self._apply_set(command.path, command.value)
            return
        args = command.args or {}
        if command.path == "theme.next":
            self._set_theme(self.theme_cycler.next_theme(), self.theme_cycler.current_theme_name)
        elif command.path == "theme.previous":
            self._set_theme(self.theme_cycler.prev_theme(), self.theme_cycler.current_theme_name)
        elif command.path == "zoom.next":
            self._set_zoom(self.settings.frequency.next_zoom_preset())
        elif command.path == "zoom.previous":
            self._set_zoom(self.settings.frequency.prev_zoom_preset())
        elif command.path == "layer.move":
            result = self.visualizer.swap_draw_order(args["direction"])
            if result is None:
                raise RuntimeControlError("unavailable", "layer is already at the requested boundary", path=command.path)
        elif command.path == "dynamic.adjust_speed":
            theme = self._active_theme()
            if not hasattr(theme, "adjust_cycle_speed"):
                raise RuntimeControlError("unavailable", "active theme is not dynamic", path=command.path)
            theme.adjust_cycle_speed(args["delta"])
        elif command.path == "dynamic.reseed":
            theme = self._active_theme()
            if not hasattr(theme, "reseed_start_hue"):
                raise RuntimeControlError("unavailable", "active theme is not dynamic", path=command.path)
            theme.reseed_start_hue()
        elif command.path == "dynamic.freeze":
            theme = self._active_theme()
            if not hasattr(theme, "toggle_frozen"):
                raise RuntimeControlError("unavailable", "active theme is not dynamic", path=command.path)
            theme.toggle_frozen()

    def _apply_set(self, path: str, value: Any) -> None:
        if path == "visualizer.gradient":
            self._set_active_or_single("gradient_enabled", "gradient_mode", value)
        elif path == "visualizer.overflow":
            self._set_active_or_single("overflow_enabled", "overflow_mode", value)
        elif path == "visualizer.bars":
            self._set_active_or_single("bars_enabled", "bars_enabled", value)
        elif path == "visualizer.full":
            self.visualizer.full_mode = value
        elif path == "visualizer.debug":
            self.visualizer.debug_mode = value
        elif path == "visualizer.peak":
            if getattr(self.visualizer, "layers_enabled", False) and getattr(self.visualizer, "layer_states", None):
                self._active_layer_state().peak_enabled = value
        elif path == "visualizer.layers_enabled":
            self.settings.layers.enabled = value
            self.visualizer.layers_enabled = value
            if value:
                self.ensure_layer_pipeline()
        elif path == "visualizer.active_layer":
            self.visualizer.select_layer(value)
            self.settings.layers.active_layer = value
            name = self.visualizer.layer_states[value].theme_name
            if name in getattr(self.theme_cycler, "themes", ()):
                self.theme_cycler.current_index = self.theme_cycler.themes.index(name)
        elif path == "visualizer.theme":
            self._set_theme(get_theme(value, brightness_boost=self.settings.color.brightness_boost), value)
        elif path == "settings.shadow_enabled":
            self.settings.shadow.enabled = value
            if value:
                try:
                    import numpy as np  # type: ignore
                    self.visualizer.shadow_buffer = np.zeros((self.width, self.height), dtype=np.float32)
                    self.visualizer.shadow_colors = np.zeros((self.width, self.height, 3), dtype=np.uint8)
                except ImportError:
                    # Hardware-free tests and state-only tooling may not install
                    # numpy.  The real renderer already imports it before this
                    # path, while simple fakes can still observe the state change.
                    self.visualizer.shadow_buffer = [[0.0] * self.height for _ in range(self.width)]
                    self.visualizer.shadow_colors = [
                        [[0, 0, 0] for _ in range(self.height)] for _ in range(self.width)
                    ]
            else:
                self.visualizer.shadow_buffer = None
                self.visualizer.shadow_colors = None
        elif path == "settings.peak_enabled":
            self.settings.peak.enabled = value
        elif path == "settings.peak_color_mode":
            self.settings.peak.color_mode = value
        elif path == "settings.zoom_preset_index":
            self.settings.frequency.zoom_preset_index = value
            self.audio.update_frequency_range()
        elif path in NUMERIC_CONTROLS:
            self._apply_numeric_setting(path, value)
        elif path == "dynamic.cycle_speed":
            self._active_theme().cycle_speed = float(value)
        elif path == "dynamic.frozen":
            theme = self._active_theme()
            if bool(theme.frozen) != value:
                theme.toggle_frozen()
        elif path.startswith("layers."):
            _, layer_id, field = path.split(".")
            index = self._layer_index(layer_id)
            self.ensure_layer_pipeline()
            state = self.visualizer.layer_states[index]
            if field == "theme":
                state.theme = get_theme(value, brightness_boost=self.settings.color.brightness_boost)
                state.theme_name = value
            elif field == "visible":
                state.visible = value
            elif field == "bars":
                state.bars_enabled = value
            elif field == "gradient":
                state.gradient_enabled = value
            elif field == "overflow":
                state.overflow_enabled = value
            elif field == "peaks":
                state.peak_enabled = value
            elif field == "boost":
                state.boost = value
                # Rendering currently reads boost from the layer configuration.
                # Keep that object aligned with the live renderer state.
                self.settings.layers.layers[index].boost = value

    # ------------------------------------------------------------------
    # Key adapter
    # ------------------------------------------------------------------
    def _key_command(self, key: str) -> RuntimeCommand | None:
        mapping = {
            "t": RuntimeCommand("action", "theme.next"),
            "T": RuntimeCommand("action", "theme.previous"),
            "r": RuntimeCommand("action", "zoom.next"),
            "R": RuntimeCommand("action", "zoom.previous"),
            "f": RuntimeCommand("set", "visualizer.full", not bool(getattr(self.visualizer, "full_mode", False))),
            "d": RuntimeCommand("set", "visualizer.debug", not bool(getattr(self.visualizer, "debug_mode", False))),
            "l": RuntimeCommand("set", "visualizer.layers_enabled", not bool(getattr(self.visualizer, "layers_enabled", False))),
            "s": RuntimeCommand("set", "settings.shadow_enabled", not bool(self.settings.shadow.enabled)),
            "P": RuntimeCommand("set", "settings.peak_color_mode", self._next_peak_mode()),
            "[": RuntimeCommand("action", "dynamic.adjust_speed", args={"delta": -0.01}),
            "]": RuntimeCommand("action", "dynamic.adjust_speed", args={"delta": 0.01}),
            "c": RuntimeCommand("action", "dynamic.reseed"),
            "x": RuntimeCommand("action", "dynamic.freeze"),
            "<": RuntimeCommand("action", "layer.move", args={"direction": -1}),
            ">": RuntimeCommand("action", "layer.move", args={"direction": 1}),
        }
        if key in mapping:
            return mapping[key]
        if key == "g":
            return RuntimeCommand("set", "visualizer.gradient", not bool(self._single_or_layer("gradient_mode", "gradient_enabled")))
        if key == "o":
            return RuntimeCommand("set", "visualizer.overflow", not bool(self._single_or_layer("overflow_mode", "overflow_enabled")))
        if key == "b":
            return RuntimeCommand("set", "visualizer.bars", not bool(self._single_or_layer("bars_enabled", "bars_enabled")))
        if key == "p":
            if getattr(self.visualizer, "layers_enabled", False):
                return RuntimeCommand("set", "visualizer.peak", not bool(self._active_layer_state().peak_enabled))
            return RuntimeCommand("set", "settings.peak_enabled", not bool(self.settings.peak.enabled))
        if key in "123":
            return RuntimeCommand("set", "visualizer.active_layer", int(key) - 1)
        if key in "!@#":
            layer_index = "!@#".index(key)
            if layer_index >= len(getattr(self.visualizer, "layer_states", [])):
                return None
            layer_id = self._layer_id(layer_index)
            return RuntimeCommand("set", f"layers.{layer_id}.visible", not bool(self.visualizer.layer_states[layer_index].visible))
        return None

    def _key_message(self, key: str) -> str:
        if key in "tT":
            scope = f"Layer {self.visualizer.active_layer + 1} " if getattr(self.visualizer, "layers_enabled", False) else ""
            return f"{scope}theme: {self.theme_cycler.current_theme_name}"
        if key in "rR":
            return f"Zoom preset: {self.settings.frequency.active_min_freq} - {self.settings.frequency.active_max_freq} Hz"
        labels = {"g": "Gradient", "o": "Overflow", "b": "Bars", "s": "Shadow", "p": "Peak", "f": "Full", "d": "Debug", "l": "Layered mode"}
        if key in labels:
            if key == "s": value = self.settings.shadow.enabled
            elif key == "p" and not self.visualizer.layers_enabled: value = self.settings.peak.enabled
            elif key == "l": value = self.visualizer.layers_enabled
            elif key == "f": value = self.visualizer.full_mode
            elif key == "d": value = self.visualizer.debug_mode
            else: value = self._single_or_layer({"g": "gradient_mode", "o": "overflow_mode", "b": "bars_enabled"}[key], {"g": "gradient_enabled", "o": "overflow_enabled", "b": "bars_enabled"}[key])
            return f"{labels[key]}: {'ON' if value else 'OFF'}"
        return f"Applied {key}"

    # ------------------------------------------------------------------
    # Small object helpers
    # ------------------------------------------------------------------
    def _active_layer_state(self) -> Any:
        return self.visualizer.layer_states[self.visualizer.active_layer]

    def _active_theme(self) -> Any:
        if getattr(self.visualizer, "layers_enabled", False) and getattr(self.visualizer, "layer_states", None):
            return self._active_layer_state().theme
        return self.visualizer.theme

    def _numeric_values(self) -> Dict[str, Any]:
        return {
            "settings.noise_floor": self.settings.sensitivity.noise_floor,
            "settings.low_freq_weight": self.settings.sensitivity.low_freq_weight,
            "settings.high_freq_weight": self.settings.sensitivity.high_freq_weight,
            "settings.silence_threshold": self.settings.sensitivity.silence_threshold,
            "settings.sensitivity_scalar": self.settings.scaling.sensitivity_scalar,
            "settings.smoothing_rise": self.settings.smoothing.rise,
            "settings.smoothing_fall": self.settings.smoothing.fall,
            "settings.overflow_multiplier": self.settings.overflow.multiplier,
            "settings.peak_fall_speed": self.settings.peak.fall_speed,
            "settings.peak_hold_frames": self.settings.peak.hold_frames,
        }

    def _apply_numeric_setting(self, path: str, value: Any) -> None:
        target_and_attr = {
            "settings.noise_floor": (self.settings.sensitivity, "noise_floor"),
            "settings.low_freq_weight": (self.settings.sensitivity, "low_freq_weight"),
            "settings.high_freq_weight": (self.settings.sensitivity, "high_freq_weight"),
            "settings.silence_threshold": (self.settings.sensitivity, "silence_threshold"),
            "settings.sensitivity_scalar": (self.settings.scaling, "sensitivity_scalar"),
            "settings.smoothing_rise": (self.settings.smoothing, "rise"),
            "settings.smoothing_fall": (self.settings.smoothing, "fall"),
            "settings.overflow_multiplier": (self.settings.overflow, "multiplier"),
            "settings.peak_fall_speed": (self.settings.peak, "fall_speed"),
            "settings.peak_hold_frames": (self.settings.peak, "hold_frames"),
        }
        target, attribute = target_and_attr[path]
        setattr(target, attribute, value)
        if path in {"settings.low_freq_weight", "settings.high_freq_weight"}:
            # Weight arrays are derived from these settings. Rebuild both the
            # single-range and layer bins at the same frame boundary.
            self.audio.update_frequency_range()
            if getattr(self.audio, "layers_enabled", False):
                self.audio.setup_layers(self.settings.layers.layers)

    def _active_theme_name(self) -> str:
        if getattr(self.visualizer, "layers_enabled", False) and getattr(
            self.visualizer, "layer_states", None
        ):
            return str(self._active_layer_state().theme_name)
        return str(
            getattr(
                getattr(self.visualizer, "theme", None),
                "name",
                self.settings.color.theme,
            )
        )

    def _can_move_layer(self, direction: int) -> bool:
        draw_order = list(getattr(self.visualizer, "draw_order", []))
        try:
            current = draw_order.index(self.visualizer.active_layer)
        except (AttributeError, ValueError):
            return False
        return 0 <= current + direction < len(draw_order)

    def _single_or_layer(self, single_attr: str, layer_attr: str) -> Any:
        if getattr(self.visualizer, "layers_enabled", False) and getattr(self.visualizer, "layer_states", None):
            return getattr(self._active_layer_state(), layer_attr)
        return getattr(self.visualizer, single_attr)

    def _set_active_or_single(self, layer_attr: str, single_attr: str, value: Any) -> None:
        if getattr(self.visualizer, "layers_enabled", False) and getattr(self.visualizer, "layer_states", None):
            setattr(self._active_layer_state(), layer_attr, value)
        else:
            setattr(self.visualizer, single_attr, value)

    def _layer_id(self, index: int) -> str:
        configs = getattr(getattr(self.settings, "layers", None), "layers", [])
        if 0 <= index < len(configs):
            return str(getattr(configs[index], "name", index))
        states = getattr(self.visualizer, "layer_states", [])
        if 0 <= index < len(states):
            return str(getattr(states[index], "name", getattr(states[index], "theme_name", index)))
        return str(index)

    def _layer_index(self, layer_id: str) -> int | None:
        states = getattr(self.visualizer, "layer_states", [])
        for index in range(len(states)):
            if self._layer_id(index) == layer_id:
                return index
        return None

    def _set_theme(self, theme: Any, name: str) -> None:
        if getattr(self.visualizer, "layers_enabled", False):
            self.visualizer.set_layer_theme(name, brightness_boost=self.settings.color.brightness_boost)
        else:
            self.visualizer.set_theme(theme)
        self.settings.color.theme = name
        if name in getattr(self.theme_cycler, "themes", ()):
            self.theme_cycler.current_index = self.theme_cycler.themes.index(name)

    def _set_zoom(self, _preset: Any) -> None:
        self.audio.update_frequency_range()

    def _next_peak_mode(self) -> str:
        modes = ["white", "bar", "contrast", "peak"]
        return modes[(modes.index(self.settings.peak.color_mode) + 1) % len(modes)]
