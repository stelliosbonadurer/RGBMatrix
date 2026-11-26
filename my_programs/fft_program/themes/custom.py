"""
Custom themes: color-stop themes from FFT's program config file (fft-themes M2).

The web service saves the file on the Pi and passes it to each FFT launch in
``RGB_MATRIX_PROGRAM_CONFIG``; a run without it reads the tracked default
``config/custom_themes.json`` (web-control-refactor D-043, fft-themes D-005).
``parse_custom_themes`` is also the web service's only check on a saved file
(D-047), so its messages name the failing entry.

Layout (fft-themes D-008, D-009; at most 48 KiB as compact JSON, D-012)::

    {"themes": [{"name": "my_sunset", "description": "...", "axis": "height",
                 "stops": [{"position": 0.0, "color": [r, g, b]}, ...],
                 "overflow": [[stop, ...], ...]}]}
"""
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, List, Optional, TextIO, Tuple, Type

from .color_stops import AXES, ColorStop, ColorStopError, ColorStopTheme, color_stop_theme
from .registry import list_themes, register_theme

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "custom_themes.json"

NAME_PATTERN = re.compile(r"my_[a-z0-9_]{1,29}")
MAX_THEMES = 32
MIN_STOPS = 2
MAX_STOPS = 16
MAX_OVERFLOW_LAYERS = 4
MAX_DESCRIPTION = 80
# Compact JSON, as the supervisor encodes it: fits the web service's 64 KiB request
# body and stays far below Linux's 128 KiB environment string (fft-themes D-012).
MAX_DOCUMENT_BYTES = 48 * 1024

_THEME_KEYS = frozenset({"name", "description", "axis", "stops", "overflow"})
_STOP_KEYS = frozenset({"position", "color"})


class CustomThemeError(ValueError):
    """A custom-theme document that can't be used."""


def _key_problem(found: Any, expected: frozenset) -> str:
    missing = sorted(expected - set(found))
    unknown = sorted(str(key) for key in set(found) - expected)
    parts = []
    if missing:
        parts.append(f"missing {', '.join(missing)}")
    if unknown:
        parts.append(f"unknown {', '.join(unknown)}")
    return "; ".join(parts)


def _parse_stops(value: Any, where: str) -> List[ColorStop]:
    if not isinstance(value, list) or not MIN_STOPS <= len(value) <= MAX_STOPS:
        raise CustomThemeError(f"{where} must be an array of {MIN_STOPS} to {MAX_STOPS} stops")
    stops = []
    for index, stop in enumerate(value):
        here = f"{where}[{index}]"
        if not isinstance(stop, dict):
            raise CustomThemeError(f"{here} must be an object with position and color")
        if set(stop) != _STOP_KEYS:
            raise CustomThemeError(f"{here}: {_key_problem(stop, _STOP_KEYS)}")
        position = stop["position"]
        if isinstance(position, bool) or not isinstance(position, (int, float)):
            raise CustomThemeError(f"{here}.position must be a number")
        try:
            position = float(position)
        except OverflowError:
            position = math.inf
        if not math.isfinite(position):
            raise CustomThemeError(f"{here}.position must be between 0 and 1")
        color = stop["color"]
        # ``type(...) is int`` rejects booleans and fractional channels before
        # ``color_stop_theme`` would truncate them.
        if not isinstance(color, list) or len(color) != 3 or any(type(c) is not int for c in color):
            raise CustomThemeError(f"{here}.color must be [r, g, b] integers")
        stops.append((position, (color[0], color[1], color[2])))
    return stops


def parse_custom_themes(document: Any) -> Tuple[Type[ColorStopTheme], ...]:
    """Validate a decoded custom-theme document and return its theme classes."""

    if not isinstance(document, dict) or set(document) != {"themes"}:
        raise CustomThemeError("custom themes must be an object containing only themes")
    try:
        size = len(json.dumps(document, separators=(",", ":"), allow_nan=False))
    except (TypeError, ValueError, RecursionError) as exc:
        raise CustomThemeError("custom themes must be plain JSON") from exc
    if size > MAX_DOCUMENT_BYTES:
        raise CustomThemeError(
            f"custom themes must be at most {MAX_DOCUMENT_BYTES} bytes as compact JSON "
            f"(this is {size})"
        )
    entries = document["themes"]
    if not isinstance(entries, list):
        raise CustomThemeError("themes must be an array")
    if len(entries) > MAX_THEMES:
        raise CustomThemeError(f"themes may hold at most {MAX_THEMES} themes")
    seen = set()
    classes = []
    for index, entry in enumerate(entries):
        where = f"themes[{index}]"
        if not isinstance(entry, dict):
            raise CustomThemeError(f"{where} must be an object")
        if set(entry) != _THEME_KEYS:
            raise CustomThemeError(f"{where}: {_key_problem(entry, _THEME_KEYS)}")
        name = entry["name"]
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            raise CustomThemeError(
                f"{where}.name must be my_ followed by 1 to 29 lowercase letters, "
                "digits, or underscores"
            )
        if name in seen:
            raise CustomThemeError(f"theme name {name} is used more than once")
        seen.add(name)
        description = entry["description"]
        if not isinstance(description, str) or len(description) > MAX_DESCRIPTION:
            raise CustomThemeError(
                f"{name}: description must be text of at most {MAX_DESCRIPTION} characters"
            )
        axis = entry["axis"]
        if not isinstance(axis, str) or axis not in AXES:
            raise CustomThemeError(f"{name}: axis must be one of {', '.join(AXES)}")
        stops = _parse_stops(entry["stops"], f"{name} stops")
        overflow = entry["overflow"]
        if not isinstance(overflow, list) or len(overflow) > MAX_OVERFLOW_LAYERS:
            raise CustomThemeError(
                f"{name}: overflow must be an array of at most {MAX_OVERFLOW_LAYERS} stop lists"
            )
        overflow_stops = [
            _parse_stops(layer, f"{name} overflow layer {layer_index + 1}")
            for layer_index, layer in enumerate(overflow)
        ]
        try:
            classes.append(
                color_stop_theme(name, description, stops, axis=axis, overflow=overflow_stops)
            )
        except ColorStopError as exc:
            # Range and order checks live in color_stop_theme (fft-themes D-004).
            raise CustomThemeError(str(exc)) from exc
    return tuple(classes)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def load_custom_themes(
    override: Optional[str],
    default_path: Path = DEFAULT_PATH,
    *,
    stderr: Optional[TextIO] = None,
) -> Tuple[str, ...]:
    """Register this run's custom themes and return their names.

    ``override`` is the ``RGB_MATRIX_PROGRAM_CONFIG`` value, or None to read
    ``default_path``. A document that can't be used, or that names an already
    registered theme, registers nothing and prints one line (fft-themes D-010).
    """

    source = "saved custom themes" if override is not None else str(default_path)
    try:
        text = override if override is not None else default_path.read_text(encoding="utf-8")
        classes = parse_custom_themes(json.loads(text, parse_constant=_reject_constant))
        registered = set(list_themes())
        clashes = [theme.name for theme in classes if theme.name in registered]
        if clashes:
            raise CustomThemeError(f"already registered: {', '.join(clashes)}")
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        print(
            f"Custom themes not loaded ({source}): {exc}; using built-in themes only",
            file=stderr or sys.stderr,
        )
        return ()
    for theme in classes:
        register_theme(theme)
    return tuple(theme.name for theme in classes)
