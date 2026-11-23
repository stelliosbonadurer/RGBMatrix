"""Run identity, revision, and snapshot for the FFT runtime protocol.

The FFT has one configuration path: the declarative layer structure that
``structural_control.StructuralRuntimeCoordinator`` validates, compiles, and
activates (``structure.set``). This controller owns only the run identity and the
revision counter that clients use for optimistic concurrency, and it builds the
JSON snapshot. It has no live per-control state; every ``patch`` path and every
other action is rejected (web-control-refactor D-001).

Calls arrive on the render thread through ``FrameBoundaryRuntimeBridge``, so the
revision is only ever changed there.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from matrix_controller.runtime_transport import RuntimeControlError


PROTOCOL_VERSION = 1


def _json_safe(value: Any) -> Any:
    """Return a JSON-safe copy without leaking arbitrary object attributes."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    # Numpy scalar values occasionally appear in settings.
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)


class FftRuntimeController:
    """Own the FFT run identity, revision, and public snapshot."""

    def __init__(
        self,
        *,
        run_id: str = "local",
        visualization_id: str = "fft",
    ):
        self.run_id = str(run_id)
        self.visualization_id = str(visualization_id)
        self._structure_snapshot_provider: Callable[[], Dict[str, Any]] | None = None
        self.revision = 0

    def bind_structure_snapshot(
        self,
        provider: Callable[[], Dict[str, Any]],
    ) -> None:
        """Attach the render-owned structural snapshot provider."""

        if not callable(provider):
            raise TypeError("structure snapshot provider must be callable")
        self._structure_snapshot_provider = provider

    def snapshot(self) -> Dict[str, Any]:
        """Return the authoritative current state."""
        snapshot: Dict[str, Any] = {
            "protocol_version": PROTOCOL_VERSION,
            "run_id": self.run_id,
            "visualization_id": self.visualization_id,
            "revision": self.revision,
            "connected": True,
        }
        if self._structure_snapshot_provider is not None:
            snapshot["structure"] = _json_safe(self._structure_snapshot_provider())
        return snapshot

    def handle_runtime_operation(self, operation: str, payload: Any) -> Dict[str, Any]:
        """Answer snapshots; reject every command that is not ``structure.set``.

        ``StructuralRuntimeCoordinator`` intercepts ``structure.set`` before the
        bridge, so any command that reaches this method is unsupported.
        """
        if operation == "snapshot":
            return self.snapshot()
        if operation != "command":
            raise RuntimeControlError("invalid_operation", "runtime operation is unsupported")
        payload = payload if isinstance(payload, dict) else {}
        command = payload.get("command")
        if not isinstance(command, dict):
            raise RuntimeControlError("invalid_command", "runtime command must be an object")
        if command.get("operation") == "patch":
            changes = command.get("changes")
            first = changes[0] if isinstance(changes, list) and changes else None
            path = first.get("path") if isinstance(first, dict) else None
            raise RuntimeControlError(
                "invalid_path",
                "FFT has no live controls; send the layer structure with structure.set",
                path=path if isinstance(path, str) else None,
            )
        if command.get("operation") == "action":
            action = command.get("action")
            raise RuntimeControlError(
                "invalid_path",
                "action is not supported",
                path=action if isinstance(action, str) else None,
            )
        raise RuntimeControlError("invalid_operation", "runtime command operation is unsupported")


__all__ = ["FftRuntimeController", "PROTOCOL_VERSION"]
