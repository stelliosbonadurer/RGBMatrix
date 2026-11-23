"""In-process ownership and reuse for compiled FFT analyzers.

The registry is a control/lifecycle seam around the existing analyzer runtime.  It
uses the canonical slots produced by :mod:`core.runtime_graph` to construct one
runtime per equivalent definition, then hands callers a direct runtime reference.
It is deliberately not consulted by the per-frame analysis or render paths.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol, TypeAlias

from core.runtime_graph import (
    AnalyzerCapability,
    AnalyzerDefinition,
    CompiledAnalyzerDefinition,
    CompiledRuntimePlan,
    RuntimeCapabilities,
    RuntimeDefinition,
    compile_runtime_graph,
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
            "device": audio_settings.device,
            "channel": audio_settings.channel,
            "block_size": audio_settings.block_size,
            "fft_size": audio_settings.fft_size,
            "min_freq": frequency_settings.active_min_freq,
            "max_freq": frequency_settings.active_max_freq,
            "noise_floor": sensitivity_settings.noise_floor,
            "low_freq_weight": sensitivity_settings.low_freq_weight,
            "high_freq_weight": sensitivity_settings.high_freq_weight,
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
    """Own one in-process runtime for equivalent compiled analyzer definitions.

    Step 2 intentionally permits only one unique canonical analyzer.  Multiple
    public analyzer IDs may alias it, but a second canonical definition would imply
    another source/worker and is deferred beyond this milestone.
    """

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
                "Step 2 supports exactly one canonical analyzer runtime"
            )

        canonical_key = plan.canonical_analyzer_keys[0]
        runtime: _AnalyzerRuntime | None = None
        for definition in plan.analyzers:
            if definition.canonical_slot != 0:
                raise AnalyzerRegistryError(
                    f"analyzer {definition.analyzer_id!r} has invalid canonical slot "
                    f"{definition.canonical_slot!r}"
                )
            if definition.canonical_key != canonical_key:
                raise AnalyzerRegistryError(
                    f"analyzer {definition.analyzer_id!r} canonical key does not "
                    "match its compiled slot"
                )
            if runtime is None:
                factory = factories.get(definition.analyzer_type)
                if factory is None:
                    raise AnalyzerRegistryError(
                        f"no runtime factory for analyzer type "
                        f"{definition.analyzer_type!r}"
                    )
                runtime = factory(definition)
                self._validate_runtime(runtime, definition)

        assert runtime is not None
        self._plan = plan
        self._runtime = runtime
        self._state = "created"

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
        """Number of physical analyzer runtimes owned by this registry."""

        return 1

    def resolve(self, definition: CompiledAnalyzerDefinition) -> _AnalyzerRuntime:
        """Resolve once during assembly; callers retain the returned direct object."""

        if not isinstance(definition, CompiledAnalyzerDefinition):
            raise AnalyzerRegistryError(
                "analyzer reference must be a CompiledAnalyzerDefinition"
            )
        slot = definition.canonical_slot
        if slot != 0 or definition.canonical_key != self._plan.canonical_analyzer_keys[0]:
            raise AnalyzerRegistryError(
                f"analyzer {definition.analyzer_id!r} is not owned by this registry"
            )
        return self._runtime

    def setup(self) -> int:
        """Set up the one physical analyzer exactly once."""

        if self._state != "created":
            raise RuntimeError(f"cannot set up analyzer registry from {self._state!r}")
        try:
            sample_rate = self._runtime.setup()
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
            self._runtime.start()
        except BaseException:
            self._state = "failed"
            try:
                self._runtime.stop()
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
            self._runtime.stop()
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
