"""In-process ownership and reuse for compiled FFT analyzers.

The registry is a control/lifecycle seam around the existing analyzer runtime.  It
uses the canonical slots produced by :mod:`core.runtime_graph` to construct one
runtime per equivalent definition, then hands callers a direct runtime reference.
It is deliberately not consulted by the per-frame analysis or render paths.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypeAlias

from core.analysis_plan import (
    StandardAnalysisCandidate,
    canonical_weight_points,
    weight_points_document,
)
from core.runtime_graph import (
    AnalyzerCapability,
    AnalyzerDefinition,
    CompiledAnalyzerDefinition,
    CompiledRuntimePlan,
    RuntimeCapabilities,
    RuntimeDefinition,
    compile_runtime_graph,
    thaw_settings,
)


STANDARD_ANALYZER_TYPE = "standard-fft-magnitudes"
STANDARD_ANALYZER_ID = "main-audio"


class AnalyzerRegistryError(ValueError):
    """Raised before an unsupported compiled analyzer plan can start."""


class _AnalyzerRuntime(Protocol):
    """Lifecycle plus direct result-lease surface required by this registry."""

    def setup(self) -> int: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def get_fft_magnitudes(self) -> Any: ...

    def release_fft_magnitudes(self) -> None: ...


AnalyzerFactory: TypeAlias = Callable[[CompiledAnalyzerDefinition], _AnalyzerRuntime]


_CAPTURE_SETTING_NAMES = (
    "source",
    "device",
    "resolved_device",
    "sample_rate",
    "capture_channels",
    "capture_dtype",
    "channel",
    "block_size",
)
_TRANSFORM_SETTING_NAMES = ("block_size", "fft_size")
_PROJECTION_SETTING_NAMES = (
    "min_freq",
    "max_freq",
    "noise_floor_dbfs",
    "aggregation",
    "weight_points",
)


def _settings_for(definition: CompiledAnalyzerDefinition) -> dict[str, object]:
    settings = thaw_settings(definition.settings)
    required = set(_CAPTURE_SETTING_NAMES) | {"fft_size"} | set(
        _PROJECTION_SETTING_NAMES
    )
    if set(settings) != required:
        raise AnalyzerRegistryError(
            f"analyzer {definition.analyzer_id!r} has incomplete standard settings"
        )
    return settings


def _capture_key(settings: Mapping[str, object]) -> tuple[object, ...]:
    return tuple(settings[name] for name in _CAPTURE_SETTING_NAMES)


def _transform_key(settings: Mapping[str, object]) -> tuple[object, ...]:
    # Window type is fixed to Hann in schema version 1. Keeping the transform key
    # distinct makes another window/size/cadence an explicit future runtime choice.
    return ("hann", *(settings[name] for name in _TRANSFORM_SETTING_NAMES))


def _projection_candidate(
    definition: CompiledAnalyzerDefinition,
    settings: Mapping[str, object],
) -> tuple[StandardAnalysisCandidate, float]:
    return (
        StandardAnalysisCandidate(
            schema_version=1,
            block_size=settings["block_size"],  # type: ignore[arg-type]
            fft_size=settings["fft_size"],  # type: ignore[arg-type]
            num_bins=definition.result_shape[0],
            min_freq=settings["min_freq"],  # type: ignore[arg-type]
            max_freq=settings["max_freq"],  # type: ignore[arg-type]
            weight_points=canonical_weight_points(settings["weight_points"]),
            aggregation=settings["aggregation"],  # type: ignore[arg-type]
        ),
        float(settings["noise_floor_dbfs"]),
    )


@dataclass(slots=True)
class PreparedProjectionPlan:
    """Per-layer projection views staged until a frame-boundary swap."""

    _registry: "AnalyzerRegistry"
    _used_keys: set[object]
    _created_keys: list[object]
    _finished: bool = False

    def commit(self) -> None:
        if self._finished:
            return
        self._registry._commit_projection_prepare(self)
        self._finished = True

    def rollback(self) -> None:
        if self._finished:
            return
        self._registry._rollback_projection_prepare(self)
        self._finished = True


def standard_analyzer_definition(
    analyzer_id: str,
    *,
    audio_settings: Any,
    frequency_settings: Any,
    sensitivity_settings: Any,
    num_bins: int,
) -> AnalyzerDefinition:
    """Snapshot every source/projection input that determines standard results."""

    return AnalyzerDefinition(
        analyzer_id=analyzer_id,
        analyzer_type=STANDARD_ANALYZER_TYPE,
        result_shape=(num_bins,),
        result_dtype="float32",
        settings={
            "source": audio_settings.source_id,
            "device": audio_settings.device,
            "resolved_device": audio_settings.resolved_device,
            "sample_rate": audio_settings.resolved_sample_rate,
            "capture_channels": audio_settings.capture_channels,
            "capture_dtype": audio_settings.capture_dtype,
            "channel": audio_settings.channel,
            "block_size": audio_settings.block_size,
            "fft_size": audio_settings.fft_size,
            "min_freq": frequency_settings.active_min_freq,
            "max_freq": frequency_settings.active_max_freq,
            "noise_floor_dbfs": sensitivity_settings.noise_floor_dbfs,
            "aggregation": sensitivity_settings.aggregation,
            "weight_points": weight_points_document(
                sensitivity_settings.weight_points
            ),
        },
    )


def standard_analyzer_capabilities() -> RuntimeCapabilities:
    """Return the trusted Step 2 catalog for the one real analyzer type."""

    return RuntimeCapabilities(
        analyzers=(
            AnalyzerCapability(
                analyzer_type=STANDARD_ANALYZER_TYPE,
                result_dtypes=frozenset({"float32"}),
                result_ranks=frozenset({1}),
            ),
        ),
        layers=(),
    )


def compile_standard_analyzer_plan(
    *,
    audio_settings: Any,
    frequency_settings: Any,
    sensitivity_settings: Any,
    num_bins: int,
    output_backend: str,
) -> CompiledRuntimePlan:
    """Compile the current single-analyzer graph without introducing layers."""

    definition = standard_analyzer_definition(
        STANDARD_ANALYZER_ID,
        audio_settings=audio_settings,
        frequency_settings=frequency_settings,
        sensitivity_settings=sensitivity_settings,
        num_bins=num_bins,
    )
    return compile_runtime_graph(
        RuntimeDefinition(
            analyzers=(definition,),
            layers=(),
            output_backend=output_backend,
        ),
        standard_analyzer_capabilities(),
    )


class AnalyzerRegistry:
    """Own one capture/FFT runtime and its canonical projection views."""

    def __init__(
        self,
        plan: CompiledRuntimePlan,
        factories: Mapping[str, AnalyzerFactory],
    ) -> None:
        if not isinstance(plan, CompiledRuntimePlan):
            raise AnalyzerRegistryError("plan must be a CompiledRuntimePlan")
        if not plan.analyzers:
            raise AnalyzerRegistryError("analyzer registry requires one analyzer")
        if len(plan.canonical_analyzer_keys) != 1:
            raise AnalyzerRegistryError(
                "runtime supports exactly one canonical FFT transform"
            )
        first = plan.analyzers[0]
        factory = factories.get(first.analyzer_type)
        if factory is None:
            raise AnalyzerRegistryError(
                f"no runtime factory for analyzer type {first.analyzer_type!r}"
            )
        owner = factory(first)
        self._validate_runtime(owner, first)
        first_settings = _settings_for(first)
        self._capture_key = _capture_key(first_settings)
        self._transform_key = _transform_key(first_settings)
        self._owner = owner
        self._primary_key = first.canonical_key
        self._primary_projection_key = self._layer_projection_key(
            first, first_settings
        )
        self._projection_runtimes: dict[object, _AnalyzerRuntime] = {
            self._primary_projection_key: owner
        }
        self._projection_prepared: PreparedProjectionPlan | None = None
        for definition in plan.analyzers:
            if (
                definition.canonical_slot != 0
                or definition.canonical_key != self._primary_key
            ):
                raise AnalyzerRegistryError("compiled analyzer slots are inconsistent")
        self._state = "created"

    @staticmethod
    def _layer_projection_key(
        definition: CompiledAnalyzerDefinition,
        settings: Mapping[str, object],
    ) -> tuple[object, ...]:
        return (
            definition.analyzer_type,
            definition.result_shape,
            definition.result_dtype,
            settings["block_size"],
            settings["fft_size"],
            settings["min_freq"],
            settings["max_freq"],
            settings["noise_floor_dbfs"],
            # How bins combine is a projection setting (D-012); curve and spread
            # are presentation and never reach this key.
            settings["aggregation"],
            # Lists are unhashable; the canonical tuple also makes key equality
            # independent of the order points arrived in.
            canonical_weight_points(settings["weight_points"]),
        )

    def _validate_shared_pipeline(
        self,
        definition: CompiledAnalyzerDefinition,
    ) -> dict[str, object]:
        if (
            definition.canonical_slot != 0
            or definition.canonical_key != self._primary_key
        ):
            raise AnalyzerRegistryError(
                f"analyzer {definition.analyzer_id!r} is not owned by this registry"
            )
        settings = _settings_for(definition)
        if _capture_key(settings) != self._capture_key:
            raise AnalyzerRegistryError(
                "all standard analyzers must share one physical capture source"
            )
        if _transform_key(settings) != self._transform_key:
            raise AnalyzerRegistryError(
                "separate FFT transforms are not active yet; analyzers must share "
                "block_size, fft_size, and Hann window"
            )
        return settings

    @staticmethod
    def _validate_runtime(
        runtime: object,
        definition: CompiledAnalyzerDefinition,
    ) -> None:
        for method_name in (
            "setup",
            "start",
            "stop",
            "get_fft_magnitudes",
            "release_fft_magnitudes",
        ):
            if not callable(getattr(runtime, method_name, None)):
                raise AnalyzerRegistryError(
                    f"factory for analyzer {definition.analyzer_id!r} returned a "
                    f"runtime without callable {method_name}()"
                )

    @property
    def state(self) -> str:
        """Expose the small lifecycle state for control-plane diagnostics/tests."""

        return self._state

    @property
    def runtime_count(self) -> int:
        """Number of active FFT transform runtimes."""

        return 1

    @property
    def projection_count(self) -> int:
        """Number of retained layer projection result views."""

        return len(self._projection_runtimes)

    @property
    def capture_count(self) -> int:
        """The number of physical capture/FFT owners (currently exactly one)."""

        return 1

    def begin_projection_prepare(self) -> PreparedProjectionPlan:
        """Begin one bounded layer-plan projection transaction."""
        if self._projection_prepared is not None:
            raise RuntimeError("a layer projection plan is already prepared")
        prepared = PreparedProjectionPlan(self, set(), [])
        self._projection_prepared = prepared
        return prepared

    def resolve_projection(
        self,
        definition: CompiledAnalyzerDefinition,
        projection_settings: Mapping[str, object],
    ) -> _AnalyzerRuntime:
        """Resolve one layer range onto the shared FFT owner."""
        base = self._validate_shared_pipeline(definition)
        settings = dict(base)
        if set(projection_settings) != set(_PROJECTION_SETTING_NAMES):
            raise AnalyzerRegistryError("layer projection settings are incomplete")
        settings.update(projection_settings)
        key = self._layer_projection_key(definition, settings)
        runtime = self._projection_runtimes.get(key)
        prepared = self._projection_prepared
        if runtime is None:
            candidate, noise_floor_dbfs = _projection_candidate(definition, settings)
            creator = getattr(self._owner, "create_projection", None)
            if not callable(creator):
                raise AnalyzerRegistryError(
                    "analyzer runtime does not support per-layer projections"
                )
            runtime = creator(candidate, noise_floor_dbfs)
            self._validate_runtime(runtime, definition)
            self._projection_runtimes[key] = runtime
            if prepared is not None:
                prepared._created_keys.append(key)
        if prepared is not None:
            prepared._used_keys.add(key)
        return runtime

    def _remove_projection_runtime(self, key: object) -> None:
        if key == self._primary_projection_key:
            return
        runtime = self._projection_runtimes.pop(key, None)
        if runtime is None:
            return
        remover = getattr(self._owner, "remove_projection", None)
        if callable(remover):
            remover(runtime)

    def _commit_projection_prepare(
        self,
        prepared: PreparedProjectionPlan,
    ) -> None:
        if self._projection_prepared is not prepared:
            raise RuntimeError("prepared layer projection plan is not active")
        keep = frozenset(prepared._used_keys)
        for key in tuple(self._projection_runtimes):
            if key not in keep and key != self._primary_projection_key:
                self._remove_projection_runtime(key)
        self._projection_prepared = None

    def _rollback_projection_prepare(
        self,
        prepared: PreparedProjectionPlan,
    ) -> None:
        if self._projection_prepared is not prepared:
            return
        for key in reversed(prepared._created_keys):
            self._remove_projection_runtime(key)
        self._projection_prepared = None

    def resolve(self, definition: CompiledAnalyzerDefinition) -> _AnalyzerRuntime:
        """Resolve once during assembly; callers retain the returned direct object."""

        if not isinstance(definition, CompiledAnalyzerDefinition):
            raise AnalyzerRegistryError(
                "analyzer reference must be a CompiledAnalyzerDefinition"
            )
        if (
            definition.canonical_slot != 0
            or definition.canonical_key != self._primary_key
        ):
            raise AnalyzerRegistryError(
                f"analyzer {definition.analyzer_id!r} is not owned by this registry"
            )
        return self._owner

    def setup(self) -> int:
        """Set up the one physical analyzer exactly once."""

        if self._state != "created":
            raise RuntimeError(f"cannot set up analyzer registry from {self._state!r}")
        try:
            sample_rate = self._owner.setup()
        except BaseException:
            self._state = "failed"
            raise
        self._state = "setup"
        return sample_rate

    def start(self) -> None:
        """Start the one physical capture/analysis runtime exactly once."""

        if self._state != "setup":
            raise RuntimeError(f"cannot start analyzer registry from {self._state!r}")
        try:
            self._owner.start()
        except BaseException:
            self._state = "failed"
            try:
                self._owner.stop()
            except BaseException as cleanup_error:
                raise RuntimeError(
                    "analyzer startup and cleanup both failed"
                ) from cleanup_error
            raise
        self._state = "running"

    def stop(self) -> None:
        """Stop the owned analyzer once; repeated shutdown remains harmless."""

        if self._state == "stopped":
            return
        if self._state == "created":
            self._state = "stopped"
            return
        try:
            self._owner.stop()
        finally:
            self._state = "stopped"

    def __enter__(self) -> "AnalyzerRegistry":
        self.setup()
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        self.stop()
        return False


__all__ = [
    "AnalyzerFactory",
    "AnalyzerRegistry",
    "AnalyzerRegistryError",
    "STANDARD_ANALYZER_ID",
    "STANDARD_ANALYZER_TYPE",
    "compile_standard_analyzer_plan",
    "standard_analyzer_capabilities",
    "standard_analyzer_definition",
]
