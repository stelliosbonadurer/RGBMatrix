"""Hardware-free tests for the non-executing compiled FFT runtime graph."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import inspect
from pathlib import Path
import sys
import unittest


FFT_ROOT = Path(__file__).resolve().parent.parent / "my_programs" / "fft_program"
CORE_ROOT = FFT_ROOT / "core"
MAIN_PATH = FFT_ROOT / "main.py"
CORE_INIT_PATH = CORE_ROOT / "__init__.py"
ANALYZER_REGISTRY_PATH = CORE_ROOT / "analyzer_registry.py"
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))


from runtime_graph import (  # noqa: E402
    AnalyzerCapability,
    AnalyzerDefinition,
    CompiledRuntimePlan,
    DIRECT_OUTPUT_BACKEND,
    FrozenSettings,
    LayerCapability,
    LayerDefinition,
    PILLOW_BULK_OUTPUT_BACKEND,
    RuntimeCapabilities,
    RuntimeDefinition,
    RuntimeGraphValidationError,
    compile_runtime_graph,
)


def _reject_invalid_setting(settings: FrozenSettings) -> None:
    if any(key == "invalid" for key, _value in settings.items):
        raise ValueError("the 'invalid' setting is not supported")


def _reject_invalid_layer_setting(
    settings: FrozenSettings,
    output_backend: str,
) -> None:
    _reject_invalid_setting(settings)
    if (
        output_backend == PILLOW_BULK_OUTPUT_BACKEND
        and any(key == "direct_only" for key, _value in settings.items)
    ):
        raise ValueError("the 'direct_only' setting requires direct output")


def _capabilities(
    *,
    bars_backends=frozenset({DIRECT_OUTPUT_BACKEND, PILLOW_BULK_OUTPUT_BACKEND}),
    bars_analyzers=frozenset({"spectrum"}),
) -> RuntimeCapabilities:
    return RuntimeCapabilities(
        analyzers=(
            AnalyzerCapability(
                analyzer_type="spectrum",
                result_dtypes=frozenset({"float32"}),
                result_ranks=frozenset({1}),
                settings_validator=_reject_invalid_setting,
            ),
            AnalyzerCapability(
                analyzer_type="envelope",
                result_dtypes=frozenset({"float32"}),
                result_ranks=frozenset({1}),
            ),
        ),
        layers=(
            LayerCapability(
                layer_type="bars",
                analyzer_types=bars_analyzers,
                output_backends=bars_backends,
                settings_validator=_reject_invalid_layer_setting,
            ),
        ),
    )


def _analyzer(
    analyzer_id="music",
    *,
    analyzer_type="spectrum",
    settings=None,
    result_shape=(64,),
    result_dtype="float32",
    schema_version=1,
) -> AnalyzerDefinition:
    return AnalyzerDefinition(
        analyzer_id=analyzer_id,
        analyzer_type=analyzer_type,
        result_shape=result_shape,
        result_dtype=result_dtype,
        settings={"fft_size": 8192} if settings is None else settings,
        schema_version=schema_version,
    )


def _layer(
    layer_id="bars",
    *,
    analyzer_id="music",
    layer_type="bars",
    visible=True,
    settings=None,
    schema_version=1,
) -> LayerDefinition:
    return LayerDefinition(
        layer_id=layer_id,
        layer_type=layer_type,
        analyzer_id=analyzer_id,
        visible=visible,
        settings={"theme": "ocean"} if settings is None else settings,
        schema_version=schema_version,
    )


def _definition(
    *,
    analyzers=None,
    layers=None,
    output_backend=DIRECT_OUTPUT_BACKEND,
    schema_version=1,
) -> RuntimeDefinition:
    return RuntimeDefinition(
        analyzers=(_analyzer(),) if analyzers is None else analyzers,
        layers=(_layer(),) if layers is None else layers,
        output_backend=output_backend,
        schema_version=schema_version,
    )


class RuntimeGraphCompilationTests(unittest.TestCase):
    def test_compilation_is_deterministic_and_settings_are_canonical(self) -> None:
        first = _definition(
            analyzers=(
                _analyzer(
                    settings={
                        "fft_size": 8192,
                        "weights": {"low": 0.45, "high": 10.0},
                        "bands": [60, 14000],
                    }
                ),
            ),
            layers=(
                _layer(settings={"theme": "ocean", "colors": [1, 2, 3]}),
            ),
        )
        second = _definition(
            analyzers=(
                _analyzer(
                    settings={
                        "bands": (60, 14000),
                        "weights": {"high": 10.0, "low": 0.45},
                        "fft_size": 8192,
                    }
                ),
            ),
            layers=(
                _layer(settings={"colors": (1, 2, 3), "theme": "ocean"}),
            ),
        )

        first_plan = compile_runtime_graph(first, _capabilities())
        second_plan = compile_runtime_graph(second, _capabilities())

        self.assertEqual(first_plan, second_plan)
        self.assertIsInstance(first_plan, CompiledRuntimePlan)
        self.assertIsInstance(first_plan.analyzers, tuple)
        self.assertIsInstance(first_plan.layers, tuple)
        self.assertIsInstance(first_plan.analyzers[0].settings, FrozenSettings)

    def test_layer_order_and_visibility_are_preserved_exactly_bottom_to_top(self) -> None:
        definition = _definition(
            layers=(
                _layer("background", visible=True),
                _layer("middle", visible=False),
                _layer("foreground", visible=True),
            )
        )

        plan = compile_runtime_graph(definition, _capabilities())

        self.assertEqual(
            tuple(layer.layer_id for layer in plan.layers),
            ("background", "middle", "foreground"),
        )
        self.assertEqual(
            tuple(layer.visible for layer in plan.layers),
            (True, False, True),
        )

    def test_stable_ids_resolve_to_direct_analyzer_objects(self) -> None:
        definition = _definition(
            analyzers=(
                _analyzer("left", settings={"channel": 0}),
                _analyzer("right", settings={"channel": 1}),
            ),
            layers=(
                _layer("bottom", analyzer_id="right"),
                _layer("top", analyzer_id="left"),
            ),
        )

        plan = compile_runtime_graph(definition, _capabilities())

        self.assertEqual(tuple(item.analyzer_id for item in plan.analyzers), ("left", "right"))
        self.assertIs(plan.layers[0].analyzer, plan.analyzers[1])
        self.assertIs(plan.layers[1].analyzer, plan.analyzers[0])

    def test_equivalent_analyzers_share_key_and_canonical_slot(self) -> None:
        definition = _definition(
            analyzers=(
                _analyzer("primary", settings={"bands": [60, 14000], "fft": 8192}),
                _analyzer("alias", settings={"fft": 8192, "bands": (60, 14000)}),
            ),
            layers=(
                _layer("bottom", analyzer_id="primary"),
                _layer("top", analyzer_id="alias"),
            ),
        )

        plan = compile_runtime_graph(definition, _capabilities())

        primary, alias = plan.analyzers
        self.assertEqual(primary.canonical_key, alias.canonical_key)
        self.assertEqual(primary.canonical_slot, alias.canonical_slot)
        self.assertEqual(primary.canonical_slot, 0)
        self.assertEqual(plan.canonical_analyzer_keys, (primary.canonical_key,))
        self.assertNotEqual(primary.analyzer_id, alias.analyzer_id)

    def test_both_existing_manual_output_values_compile(self) -> None:
        direct = compile_runtime_graph(
            _definition(output_backend=DIRECT_OUTPUT_BACKEND),
            _capabilities(),
        )
        pillow = compile_runtime_graph(
            _definition(output_backend=PILLOW_BULK_OUTPUT_BACKEND),
            _capabilities(),
        )

        self.assertEqual(direct.output_backend, "direct")
        self.assertEqual(pillow.output_backend, "pillow-bulk")

    def test_compiled_plan_snapshots_settings_and_is_immutable(self) -> None:
        analyzer_settings = {"fft_size": 8192}
        layer_settings = {"theme": "ocean"}
        plan = compile_runtime_graph(
            _definition(
                analyzers=(_analyzer(settings=analyzer_settings),),
                layers=(_layer(settings=layer_settings),),
            ),
            _capabilities(),
        )
        original_key = plan.analyzers[0].canonical_key
        original_layer_settings = plan.layers[0].settings

        analyzer_settings["fft_size"] = 4096
        layer_settings["theme"] = "warm"

        self.assertEqual(plan.analyzers[0].canonical_key, original_key)
        self.assertEqual(plan.layers[0].settings, original_layer_settings)
        with self.assertRaises(FrozenInstanceError):
            plan.output_backend = PILLOW_BULK_OUTPUT_BACKEND  # type: ignore[misc]

    def test_failed_compilation_cannot_mutate_or_replace_valid_plan(self) -> None:
        active_plan = compile_runtime_graph(_definition(), _capabilities())
        original_analyzer = active_plan.analyzers[0]
        original_layer = active_plan.layers[0]

        with self.assertRaisesRegex(RuntimeGraphValidationError, "missing analyzer"):
            compile_runtime_graph(
                _definition(layers=(_layer(analyzer_id="missing"),)),
                _capabilities(),
            )

        self.assertIs(active_plan.analyzers[0], original_analyzer)
        self.assertIs(active_plan.layers[0], original_layer)
        self.assertIs(active_plan.layers[0].analyzer, original_analyzer)


class RuntimeGraphValidationTests(unittest.TestCase):
    def test_duplicate_analyzer_and_layer_ids_are_rejected(self) -> None:
        duplicate_analyzers = _definition(
            analyzers=(_analyzer("same"), _analyzer("same")),
            layers=(),
        )
        duplicate_layers = _definition(
            layers=(_layer("same"), _layer("same")),
        )

        with self.assertRaisesRegex(RuntimeGraphValidationError, "duplicate analyzer ID"):
            compile_runtime_graph(duplicate_analyzers, _capabilities())
        with self.assertRaisesRegex(RuntimeGraphValidationError, "duplicate layer ID"):
            compile_runtime_graph(duplicate_layers, _capabilities())

    def test_missing_analyzer_reference_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeGraphValidationError, "missing analyzer"):
            compile_runtime_graph(
                _definition(layers=(_layer(analyzer_id="absent"),)),
                _capabilities(),
            )

    def test_invalid_analyzer_definitions_are_rejected(self) -> None:
        invalid_cases = (
            ("empty ID", _analyzer("")),
            ("unsupported schema", _analyzer(schema_version=2)),
            ("empty shape", _analyzer(result_shape=())),
            ("invalid dimension", _analyzer(result_shape=(0,))),
            ("unsupported dtype", _analyzer(result_dtype="float64")),
            ("invalid settings value", _analyzer(settings={"value": object()})),
            ("capability settings failure", _analyzer(settings={"invalid": True})),
            ("unsupported type", _analyzer(analyzer_type="unknown")),
        )

        for name, analyzer in invalid_cases:
            with self.subTest(name=name), self.assertRaises(RuntimeGraphValidationError):
                compile_runtime_graph(
                    _definition(analyzers=(analyzer,), layers=()),
                    _capabilities(),
                )

    def test_recursive_settings_are_rejected(self) -> None:
        recursive = {}
        recursive["self"] = recursive

        with self.assertRaisesRegex(RuntimeGraphValidationError, "cycle"):
            compile_runtime_graph(
                _definition(analyzers=(_analyzer(settings=recursive),), layers=()),
                _capabilities(),
            )

    def test_invalid_layer_definitions_are_rejected(self) -> None:
        invalid_cases = (
            ("empty ID", _layer("")),
            ("unsupported schema", _layer(schema_version=2)),
            ("non-boolean visibility", _layer(visible=1)),
            ("invalid settings", _layer(settings={"value": {1, 2}})),
            ("capability settings failure", _layer(settings={"invalid": True})),
            ("unsupported type", _layer(layer_type="unknown")),
        )

        for name, layer in invalid_cases:
            with self.subTest(name=name), self.assertRaises(RuntimeGraphValidationError):
                compile_runtime_graph(
                    _definition(layers=(layer,)),
                    _capabilities(),
                )

    def test_incompatible_analyzer_type_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeGraphValidationError, "does not support analyzer"):
            compile_runtime_graph(
                _definition(
                    analyzers=(_analyzer(analyzer_type="envelope"),),
                ),
                _capabilities(),
            )

    def test_unsupported_layer_backend_is_rejected_before_activation(self) -> None:
        with self.assertRaisesRegex(RuntimeGraphValidationError, "does not support output"):
            compile_runtime_graph(
                _definition(output_backend=PILLOW_BULK_OUTPUT_BACKEND),
                _capabilities(bars_backends=frozenset({DIRECT_OUTPUT_BACKEND})),
            )

    def test_backend_specific_layer_settings_are_rejected_before_activation(self) -> None:
        direct_definition = _definition(
            layers=(_layer(settings={"direct_only": True}),),
            output_backend=DIRECT_OUTPUT_BACKEND,
        )
        pillow_definition = _definition(
            layers=(_layer(settings={"direct_only": True}),),
            output_backend=PILLOW_BULK_OUTPUT_BACKEND,
        )

        direct = compile_runtime_graph(direct_definition, _capabilities())
        self.assertEqual(direct.output_backend, DIRECT_OUTPUT_BACKEND)
        with self.assertRaisesRegex(RuntimeGraphValidationError, "requires direct"):
            compile_runtime_graph(pillow_definition, _capabilities())

    def test_automatic_or_renamed_output_values_are_rejected(self) -> None:
        for output_backend in ("auto", "setimage", "pillow_bulk", ""):
            with self.subTest(output_backend=output_backend), self.assertRaisesRegex(
                RuntimeGraphValidationError,
                "output backend",
            ):
                compile_runtime_graph(
                    _definition(output_backend=output_backend),
                    _capabilities(),
                )

    def test_unordered_definition_collections_are_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeGraphValidationError, "bottom-to-top"):
            compile_runtime_graph(
                _definition(layers=set()),
                _capabilities(),
            )

    def test_invalid_capability_catalog_is_rejected(self) -> None:
        duplicate_analyzer_type = RuntimeCapabilities(
            analyzers=(
                AnalyzerCapability("spectrum", frozenset({"float32"}), frozenset({1})),
                AnalyzerCapability("spectrum", frozenset({"float32"}), frozenset({1})),
            ),
            layers=(),
        )
        unknown_backend = RuntimeCapabilities(
            analyzers=(
                AnalyzerCapability("spectrum", frozenset({"float32"}), frozenset({1})),
            ),
            layers=(
                LayerCapability(
                    "bars",
                    frozenset({"spectrum"}),
                    frozenset({"automatic"}),
                ),
            ),
        )

        for capabilities in (duplicate_analyzer_type, unknown_backend):
            with self.subTest(capabilities=capabilities), self.assertRaises(
                RuntimeGraphValidationError
            ):
                compile_runtime_graph(_definition(layers=()), capabilities)


class RuntimeGraphBoundaryTests(unittest.TestCase):
    def test_step3_connects_graph_through_the_ordered_layer_plan(self) -> None:
        main_source = MAIN_PATH.read_text(encoding="utf-8")
        core_init_source = CORE_INIT_PATH.read_text(encoding="utf-8")
        registry_source = ANALYZER_REGISTRY_PATH.read_text(encoding="utf-8")
        layer_plan_source = (
            FFT_ROOT / "core" / "layer_plan.py"
        ).read_text(encoding="utf-8")
        compiler_source = inspect.getsource(compile_runtime_graph)

        self.assertNotIn("runtime_graph", main_source)
        self.assertNotIn("compile_runtime_graph", main_source)
        self.assertNotIn("runtime_graph", core_init_source)
        self.assertIn("AnalyzerRegistry", main_source)
        self.assertIn("compile_current_bars_graph", main_source)
        self.assertIn("compile_current_bars_layer_plan", main_source)
        self.assertIn("LayerDefinition(", layer_plan_source)
        self.assertIn("draw_peaks", layer_plan_source)
        self.assertIn("layers=()", registry_source)
        self.assertNotIn("visualizers", registry_source)
        self.assertNotIn("draw_peaks", registry_source)
        self.assertNotIn("main", compiler_source)
        self.assertNotIn("draw", compiler_source)
        self.assertNotIn("execute", compiler_source)


if __name__ == "__main__":
    unittest.main()
