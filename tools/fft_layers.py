#!/usr/bin/env python3
"""Configure one through eight FFT layers through the public runtime API.

The tool deliberately uses the same snapshot and ``structure.set`` action a web
client uses. It does not import FFT internals or add a startup-only configuration
path. By default it plans a direct-output structure; pass ``--backend
pillow-bulk`` to exercise the reusable Pillow/SetImage path.

Examples, run where the controller token is available::

    python3 tools/fft_layers.py --layers 4 --dry-run
    python3 tools/fft_layers.py --layers 8 --backend pillow-bulk

Applying a document replaces the complete layer list and therefore resets
layer-owned presentation history. The analyzer runtime remains shared by every generated
layer. The API token is read from a file and is never printed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_TOKEN_FILE = Path.home() / ".config/rgb-matrix-controller/api-token"
DIRECT_BACKEND = "direct"
PILLOW_BACKEND = "pillow-bulk"
SUPPORTED_BACKENDS = (DIRECT_BACKEND, PILLOW_BACKEND)
CURRENT_BARS_LAYER_TYPE = "current-bars"
CURRENT_BARS_LAYER_ID = "current-bars"


class LayerToolError(RuntimeError):
    """A bounded input, snapshot, or HTTP failure suitable for CLI output."""


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LayerToolError(f"{label} must be an object")
    return value


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise LayerToolError(f"{label} must be a list")
    return value


def _name(value: object, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise LayerToolError(f"{label} must be a non-empty trimmed string")
    return value


def _non_negative_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise LayerToolError(f"{label} must be a non-negative integer")
    return value


def _current_bars_capability(structure: Mapping[str, Any]) -> Mapping[str, Any]:
    capabilities = _mapping(structure.get("capabilities"), "structure.capabilities")
    for raw_capability in _list(
        capabilities.get("layer_types"),
        "structure.capabilities.layer_types",
    ):
        capability = _mapping(raw_capability, "layer capability")
        if capability.get("layer_type") == CURRENT_BARS_LAYER_TYPE:
            return capability
    raise LayerToolError("runtime does not advertise the current-bars layer type")


def _default_settings(capability: Mapping[str, Any]) -> dict[str, Any]:
    descriptor = _mapping(capability.get("settings"), "current-bars settings descriptor")
    settings: dict[str, Any] = {}
    for index, raw_field in enumerate(
        _list(descriptor.get("fields"), "current-bars settings fields")
    ):
        field = _mapping(raw_field, f"current-bars settings field {index}")
        name = _name(field.get("name"), f"current-bars settings field {index} name")
        if "default" not in field:
            raise LayerToolError(f"current-bars setting {name!r} has no default")
        settings[name] = field["default"]
    if not settings:
        raise LayerToolError("current-bars settings descriptor has no fields")
    return settings


def _layer_settings(
    structure: Mapping[str, Any],
    capability: Mapping[str, Any],
    backend: str,
) -> dict[str, Any]:
    settings = _default_settings(capability)
    layers = _list(structure.get("layers"), "structure.layers")
    base_layer: Mapping[str, Any] | None = None
    for raw_layer in layers:
        layer = _mapping(raw_layer, "structure layer")
        if layer.get("layer_id") == CURRENT_BARS_LAYER_ID:
            base_layer = layer
            break
        if base_layer is None and layer.get("layer_type") == CURRENT_BARS_LAYER_TYPE:
            base_layer = layer
    if base_layer is not None:
        live_settings = _mapping(base_layer.get("settings"), "current-bars layer settings")
        unknown = set(live_settings) - set(settings)
        if unknown:
            raise LayerToolError(
                "current-bars layer has settings absent from its descriptor: "
                + ", ".join(sorted(unknown))
            )
        settings.update(live_settings)

    descriptor = _mapping(capability.get("settings"), "current-bars settings descriptor")
    constraints = _mapping(
        descriptor.get("backend_constraints", {}),
        "current-bars backend constraints",
    )
    raw_constraints = constraints.get(backend, {})
    settings.update(_mapping(raw_constraints, f"{backend} settings constraints"))
    return settings


def build_structure_document(
    snapshot: Mapping[str, Any],
    layer_count: int,
    backend: str = DIRECT_BACKEND,
) -> dict[str, Any]:
    """Build one complete ``structure.set`` document from a public snapshot."""

    if type(layer_count) is not int or not 1 <= layer_count <= 8:
        raise LayerToolError("layer count must be an integer from 1 through 8")
    if backend not in SUPPORTED_BACKENDS:
        raise LayerToolError(
            f"backend must be one of: {', '.join(SUPPORTED_BACKENDS)}"
        )

    structure = _mapping(snapshot.get("structure"), "snapshot.structure")
    capabilities = _mapping(structure.get("capabilities"), "structure.capabilities")
    limits = _mapping(capabilities.get("limits"), "structure.capabilities.limits")
    max_layers = _non_negative_int(limits.get("max_layers"), "max_layers")
    if layer_count > max_layers:
        raise LayerToolError(
            f"runtime supports at most {max_layers} layers, not {layer_count}"
        )

    advertised_backends = _list(
        capabilities.get("output_backends"),
        "structure.capabilities.output_backends",
    )
    if backend not in advertised_backends:
        raise LayerToolError(f"runtime does not advertise backend {backend!r}")

    capability = _current_bars_capability(structure)
    layer_backends = _list(
        capability.get("output_backends"),
        "current-bars output_backends",
    )
    if backend not in layer_backends:
        raise LayerToolError(
            f"current-bars does not advertise backend {backend!r}"
        )

    analyzer_ids = [
        _name(value, "available analyzer ID")
        for value in _list(
            structure.get("available_analyzer_ids"),
            "structure.available_analyzer_ids",
        )
    ]
    if not analyzer_ids:
        raise LayerToolError("runtime does not advertise an analyzer instance")

    layers = _list(structure.get("layers"), "structure.layers")
    existing_ids: list[str] = []
    preferred_analyzer: str | None = None
    for index, raw_layer in enumerate(layers):
        layer = _mapping(raw_layer, f"structure.layers[{index}]")
        layer_id = _name(layer.get("layer_id"), f"structure.layers[{index}].layer_id")
        if layer_id in existing_ids:
            raise LayerToolError(f"runtime snapshot repeats layer ID {layer_id!r}")
        existing_ids.append(layer_id)
        analyzer_id = layer.get("analyzer_id")
        if (
            layer_id == CURRENT_BARS_LAYER_ID
            and analyzer_id in analyzer_ids
        ):
            preferred_analyzer = str(analyzer_id)

    analyzer_id = preferred_analyzer or analyzer_ids[0]
    settings = _layer_settings(structure, capability, backend)
    return {
        "output_backend": backend,
        "layers": [
            {
                "layer_id": (
                    CURRENT_BARS_LAYER_ID if index == 0 else f"layer-{index + 1}"
                ),
                "layer_type": CURRENT_BARS_LAYER_TYPE,
                "analyzer_id": analyzer_id,
                "visible": True,
                "settings": dict(settings),
            }
            for index in range(layer_count)
        ],
    }


def build_structure_action(
    snapshot: Mapping[str, Any],
    layer_count: int,
    backend: str = DIRECT_BACKEND,
) -> dict[str, Any]:
    """Build the public HTTP action body for a complete layer replacement."""

    run_id = snapshot.get("run_id")
    if type(run_id) not in {str, int} or (type(run_id) is str and not run_id):
        raise LayerToolError("snapshot.run_id must be a non-empty string or integer")
    revision = _non_negative_int(snapshot.get("revision"), "snapshot.revision")
    return {
        "run_id": run_id,
        "expected_revision": revision,
        "action": "structure.set",
        "args": build_structure_document(snapshot, layer_count, backend),
    }


def _request_json(
    base_url: str,
    token: str,
    method: str,
    path: str,
    body: object | None = None,
    *,
    timeout: float = 10.0,
) -> Mapping[str, Any]:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LayerToolError("base URL must be an absolute HTTP or HTTPS URL")
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Authorization": "Bearer " + token},
    )
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            detail = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            detail = {}
        message = detail.get("error") if isinstance(detail, dict) else None
        code = detail.get("code") if isinstance(detail, dict) else None
        suffix = f" ({code})" if code else ""
        raise LayerToolError(
            f"controller returned HTTP {exc.code}{suffix}: {message or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise LayerToolError(f"could not reach controller: {exc.reason}") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise LayerToolError("controller returned invalid JSON") from exc
    return _mapping(value, "controller response")


def _read_token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise LayerToolError(f"could not read API token file {path}: {exc}") from exc
    if not token:
        raise LayerToolError(f"API token file is empty: {path}")
    return token


def _verify_result(
    result: Mapping[str, Any],
    layer_count: int,
    backend: str,
) -> None:
    structure = _mapping(result.get("structure"), "response.structure")
    layers = _list(structure.get("layers"), "response.structure.layers")
    expected_ids = [
        CURRENT_BARS_LAYER_ID,
        *(f"layer-{index}" for index in range(2, layer_count + 1)),
    ]
    observed_ids = [
        _name(
            _mapping(layer, "response layer").get("layer_id"),
            "response layer ID",
        )
        for layer in layers
    ]
    if observed_ids != expected_ids:
        raise LayerToolError(
            f"controller returned layer order {observed_ids!r}, expected {expected_ids!r}"
        )
    if structure.get("output_backend") != backend:
        raise LayerToolError(
            f"controller returned backend {structure.get('output_backend')!r}, "
            f"expected {backend!r}"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--layers",
        type=int,
        required=True,
        choices=range(1, 9),
        metavar="1..8",
        help="exact number of visible current-bars layers",
    )
    parser.add_argument(
        "--backend",
        choices=SUPPORTED_BACKENDS,
        default=DIRECT_BACKEND,
        help="output backend (default: direct)",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the action body but do not apply it",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.timeout <= 0:
            raise LayerToolError("timeout must be greater than zero")
        token = _read_token(args.token_file)
        snapshot = _request_json(
            args.base_url,
            token,
            "GET",
            "/api/v1/runtime",
            timeout=args.timeout,
        )
        action = build_structure_action(snapshot, args.layers, args.backend)
        if args.dry_run:
            print(json.dumps(action, indent=2, sort_keys=True))
            return 0
        result = _request_json(
            args.base_url,
            token,
            "POST",
            "/api/v1/runtime/actions",
            action,
            timeout=args.timeout,
        )
        _verify_result(result, args.layers, args.backend)
        print(
            f"Applied {args.layers} layer(s) with {args.backend}; "
            f"revision {result.get('revision')}."
        )
        return 0
    except LayerToolError as exc:
        print(f"fft_layers: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
