"""Immutable visualization and command-line parameter definitions.

The same definitions are consumed by the HTTP API, subprocess command builder, and
the converted applications' argument parsers. Keeping validation here prevents the
web service and command-line entry points from slowly acquiring different accepted
values or ranges.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Mapping


ParameterKind = Literal["bool", "int", "float", "string", "enum"]
_USE_SPEC_DEFAULT = object()


class ValidationError(ValueError):
    """A safe, user-correctable manifest or parameter validation failure."""


@dataclass(frozen=True)
class ParameterSpec:
    """Describe one allowlisted parameter from JSON value through CLI argument.

    ``negative_cli_flag`` is used for booleans whose false value must be explicit.
    If it is absent, false simply omits the positive flag. ``true_cli_value`` supports
    legacy flags that require a fixed value after the flag instead of conventional
    ``store_true`` parsing. Neither mechanism accepts arbitrary argument fragments.
    """

    name: str
    cli_flag: str
    kind: ParameterKind
    default: Any
    description: str
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    negative_cli_flag: str | None = None
    maximum_length: int = 256
    true_cli_value: str | None = None

    def normalize(self, value: Any) -> Any:
        """Return a validated canonical value or raise ``ValidationError``.

        JSON booleans are deliberately not accepted as integers even though Python's
        ``bool`` subclasses ``int``. Floats must be finite so values such as NaN and
        infinity can never reach visualization math through a JSON parser that
        happens to accept them.
        """

        label = f"parameter '{self.name}'"

        if self.kind == "bool":
            if type(value) is not bool:
                raise ValidationError(f"{label} must be a boolean")
            normalized: Any = value
        elif self.kind == "int":
            if type(value) is not int:
                raise ValidationError(f"{label} must be an integer")
            normalized = value
        elif self.kind == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError(f"{label} must be a number")
            normalized = float(value)
            if not math.isfinite(normalized):
                raise ValidationError(f"{label} must be finite")
        elif self.kind in {"string", "enum"}:
            if type(value) is not str:
                raise ValidationError(f"{label} must be a string")
            if len(value) > self.maximum_length:
                raise ValidationError(
                    f"{label} must be at most {self.maximum_length} characters"
                )
            normalized = value
        else:  # Defensive guard for a malformed code-owned manifest.
            raise RuntimeError(f"unsupported parameter kind: {self.kind}")

        if self.kind in {"int", "float"}:
            if self.minimum is not None and normalized < self.minimum:
                raise ValidationError(f"{label} must be at least {self.minimum:g}")
            if self.maximum is not None and normalized > self.maximum:
                raise ValidationError(f"{label} must be at most {self.maximum:g}")

        if self.kind == "enum" and normalized not in self.choices:
            allowed = ", ".join(self.choices)
            raise ValidationError(f"{label} must be one of: {allowed}")

        return normalized

    def cli_arguments(self, value: Any) -> list[str]:
        """Render a previously normalized value as a fixed argument vector."""

        normalized = self.normalize(value)
        if self.kind == "bool":
            if normalized:
                arguments = [self.cli_flag]
                if self.true_cli_value is not None:
                    arguments.append(self.true_cli_value)
                return arguments
            return [self.negative_cli_flag] if self.negative_cli_flag else []
        return [self.cli_flag, str(normalized)]

    def add_to_argparse(
        self,
        parser: argparse.ArgumentParser,
        *,
        argument_default: Any = _USE_SPEC_DEFAULT,
    ) -> None:
        """Add this contract to an application's direct CLI parser.

        ``argument_default`` can be used when direct interactive execution needs an
        auto-detected default while supervised execution has a concrete manifest
        default. Values and choices still come from this single contract.
        """

        default = self.default if argument_default is _USE_SPEC_DEFAULT else argument_default
        kwargs: dict[str, Any] = {"dest": self.name, "default": default, "help": self.description}

        if self.kind == "bool":
            group = parser.add_mutually_exclusive_group()
            group.add_argument(self.cli_flag, action="store_true", dest=self.name, help=self.description)
            if self.negative_cli_flag:
                group.add_argument(
                    self.negative_cli_flag,
                    action="store_false",
                    dest=self.name,
                    help=f"Disable: {self.description}",
                )
            parser.set_defaults(**{self.name: default})
            return

        def parse_and_validate(raw_value: str) -> Any:
            try:
                if self.kind == "int":
                    parsed: Any = int(raw_value)
                elif self.kind == "float":
                    parsed = float(raw_value)
                else:
                    parsed = raw_value
                return self.normalize(parsed)
            except (ValueError, ValidationError) as exc:
                raise argparse.ArgumentTypeError(str(exc)) from exc

        kwargs["type"] = parse_and_validate
        if self.kind == "enum":
            kwargs["choices"] = self.choices
        parser.add_argument(self.cli_flag, **kwargs)

    def as_public_dict(self) -> dict[str, Any]:
        """Return JSON-safe metadata for API clients and future generated UIs."""

        data: dict[str, Any] = {
            "name": self.name,
            "type": self.kind,
            "default": self.default,
            "description": self.description,
            "restart_required": True,
            # The pages show the exact flags a launch passes (web-control-refactor
            # D-035, D-037) and rebuild them with the rules of ``cli_arguments``.
            "cli_flag": self.cli_flag,
            "negative_cli_flag": self.negative_cli_flag,
            "true_cli_value": self.true_cli_value,
        }
        if self.minimum is not None:
            data["minimum"] = self.minimum
        if self.maximum is not None:
            data["maximum"] = self.maximum
        if self.choices:
            data["choices"] = list(self.choices)
        if self.kind in {"string", "enum"}:
            data["maximum_length"] = self.maximum_length
        return data


@dataclass(frozen=True)
class ProgramFileSpec:
    """A program config file the pages can edit (web-control-refactor D-039).

    ``default_path`` is the tracked file, never written. ``validate`` raises
    ``ValidationError`` for a document the program can't use; it is the store's
    only check (D-047). ``fields`` describes the editor and is read only by the
    page.
    """

    name: str
    label: str
    default_path: Path
    validate: Callable[[Any], None]
    fields: tuple[Mapping[str, Any], ...]

    def as_public_dict(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label}


@dataclass(frozen=True)
class VisualizationSpec:
    """A single code-owned, web-launchable visualization definition.

    ``parameters`` are the program's own launch options. ``matrix_parameters`` are
    the shared panel settings (web-control-refactor D-035): a launch never
    supplies them, and ``build_argv`` takes their values separately.
    """

    visualization_id: str
    display_name: str
    description: str
    script_path: Path
    working_directory: Path
    parameters: tuple[ParameterSpec, ...]
    requires_audio: bool = False
    supports_live_control: bool = False
    parameter_validator: Callable[[Mapping[str, Any]], None] | None = None
    matrix_parameters: tuple[ParameterSpec, ...] = ()
    # At most one: the override reaches the child in a single environment
    # variable whose value is the document itself (web-control-refactor D-043).
    program_files: tuple[ProgramFileSpec, ...] = ()

    def __post_init__(self) -> None:
        if len(self.program_files) > 1:
            raise ValueError(f"{self.visualization_id} may register at most one program file")

    def program_file(self, name: str) -> ProgramFileSpec:
        for program_file in self.program_files:
            if program_file.name == name:
                return program_file
        raise KeyError(name)

    def parameter(self, name: str) -> ParameterSpec:
        for parameter in self.parameters:
            if parameter.name == name:
                return parameter
        raise KeyError(name)

    def validate_parameters(self, supplied: Mapping[str, Any] | None) -> dict[str, Any]:
        """Strictly validate a request and fill all omitted manifest defaults."""

        if supplied is None:
            supplied = {}
        if not isinstance(supplied, Mapping):
            raise ValidationError("parameters must be a JSON object")

        matrix_names = {parameter.name for parameter in self.matrix_parameters}
        shared = sorted(set(supplied) & matrix_names)
        if shared:
            raise ValidationError(
                f"shared matrix settings aren't launch parameters: {', '.join(shared)}; "
                "change them in the matrix settings"
            )
        known = {parameter.name for parameter in self.parameters}
        unknown = sorted(set(supplied) - known)
        if unknown:
            raise ValidationError(f"unknown parameters: {', '.join(unknown)}")

        normalized = {
            parameter.name: parameter.normalize(
                supplied[parameter.name] if parameter.name in supplied else parameter.default
            )
            for parameter in self.parameters
        }
        if self.parameter_validator is not None:
            self.parameter_validator(normalized)
        return normalized

    def validate_matrix_values(self, supplied: Mapping[str, Any] | None) -> dict[str, Any]:
        """Normalize a complete set of shared matrix values, or their defaults.

        ``None`` means the code defaults. Otherwise every matrix parameter must be
        present, so a launch can never mix stored values with silent defaults.
        """

        if supplied is None:
            return {parameter.name: parameter.default for parameter in self.matrix_parameters}
        if not isinstance(supplied, Mapping):
            raise ValidationError("matrix values must be a JSON object")
        names = {parameter.name for parameter in self.matrix_parameters}
        unknown = sorted(set(supplied) - names)
        if unknown:
            raise ValidationError(f"unknown matrix settings: {', '.join(unknown)}")
        missing = sorted(names - set(supplied))
        if missing:
            raise ValidationError(f"missing matrix settings: {', '.join(missing)}")
        return {
            parameter.name: parameter.normalize(supplied[parameter.name])
            for parameter in self.matrix_parameters
        }

    def build_argv(
        self,
        python_executable: str,
        parameters: Mapping[str, Any],
        command_prefix: tuple[str, ...] = (),
        *,
        matrix_values: Mapping[str, Any] | None = None,
    ) -> list[str]:
        """Build an argv list without invoking a shell or accepting raw arguments.

        Matrix flags come first, then the program's own, matching the order the
        combined parameter lists produced before D-035.
        """

        normalized = self.validate_parameters(parameters)
        matrix = self.validate_matrix_values(matrix_values)
        argv = [*command_prefix, python_executable, str(self.script_path)]
        for parameter in self.matrix_parameters:
            argv.extend(parameter.cli_arguments(matrix[parameter.name]))
        for parameter in self.parameters:
            argv.extend(parameter.cli_arguments(normalized[parameter.name]))
        return argv

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.visualization_id,
            "name": self.display_name,
            "description": self.description,
            "requires_audio": self.requires_audio,
            "supports_live_control": self.supports_live_control,
            "parameters": [parameter.as_public_dict() for parameter in self.parameters],
            "files": [program_file.as_public_dict() for program_file in self.program_files],
        }
