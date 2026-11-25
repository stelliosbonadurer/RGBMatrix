"""The FFT page's catalog file (web-control-refactor D-049, M5 slice 1 criterion 1).

``matrix_controller/static/fft_catalog.json`` lets the FFT page edit layers while
FFT is stopped. It is generated from the FFT code, because the web service imports
no FFT code (D-016, D-025). It has the shape of the running snapshot's
``structure`` (analyzer facts, the default layer, the capabilities with the
current-bars settings descriptor), so the page reads one shape from either source.

Regenerate it with ``python -m tests.test_fft_catalog --write``.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import unittest

try:
    import numpy  # noqa: F401
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("the FFT catalog tests require NumPy") from exc


ROOT = Path(__file__).resolve().parents[1]
FFT_ROOT = ROOT / "my_programs" / "fft_program"
CATALOG = ROOT / "matrix_controller" / "static" / "fft_catalog.json"
if str(FFT_ROOT) not in sys.path:
    sys.path.insert(0, str(FFT_ROOT))

# Both sources ran at 44.1 kHz (bluetooth-audio-input); the real rate arrives with
# the snapshot after launch, and the page marks this one "assumed until launch".
ASSUMED_SAMPLE_RATE = 44_100
ANALYZER_FACTS = ("sample_rate", "fft_size", "block_size")

_sounddevice = ModuleType("sounddevice")
_prior_sounddevice = sys.modules.get("sounddevice")
sys.modules["sounddevice"] = _sounddevice
try:
    from config.settings import Settings  # noqa: E402
    from core.layer_plan import (  # noqa: E402
        current_bars_runtime_capabilities,
        current_bars_runtime_definition,
        current_bars_settings_from_runtime,
    )
    from core.output import DIRECT_OUTPUT_PATH  # noqa: E402
    from core.runtime_graph import (  # noqa: E402
        runtime_capabilities_to_dict,
        runtime_definition_to_dict,
    )
    from structural_control import (  # noqa: E402
        STRUCTURE_SET_ACTION,
        bin_layout_snapshot,
    )
finally:
    if _prior_sounddevice is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = _prior_sounddevice


def build_catalog() -> dict:
    settings = Settings()
    settings.audio.resolved_sample_rate = ASSUMED_SAMPLE_RATE
    definition = current_bars_runtime_definition(
        audio_settings=settings.audio,
        frequency_settings=settings.frequency,
        sensitivity_settings=settings.sensitivity,
        num_bins=64,
        output_backend=DIRECT_OUTPUT_PATH,
        layer_settings=current_bars_settings_from_runtime(settings),
    )
    public = runtime_definition_to_dict(definition)
    # As StructuralRuntimeCoordinator.structure_snapshot publishes them.
    layers = public["layers"]
    for layer in layers:
        layer.pop("schema_version", None)
    analyzers = [
        {
            "analyzer_id": analyzer["analyzer_id"],
            "analyzer_type": analyzer["analyzer_type"],
            "settings": {name: analyzer["settings"][name] for name in ANALYZER_FACTS},
        }
        for analyzer in public["analyzers"]
    ]
    capabilities = runtime_capabilities_to_dict(current_bars_runtime_capabilities())
    capabilities["actions"] = [STRUCTURE_SET_ACTION]
    return {
        "about": "Generated from the FFT code by tests/test_fft_catalog.py "
        "(python -m tests.test_fft_catalog --write). The FFT page edits layers "
        "from it while FFT is stopped (web-control-refactor D-049).",
        "assumed_until_launch": ["sample_rate"],
        "structure": {
            "output_backend": public["output_backend"],
            "analyzers": analyzers,
            "layers": layers,
            "available_analyzer_ids": [analyzer["analyzer_id"] for analyzer in analyzers],
            "bin_layout": bin_layout_snapshot(definition),
            "capabilities": capabilities,
        },
    }


def write_catalog() -> None:
    CATALOG.write_text(json.dumps(build_catalog(), indent=1) + "\n", encoding="utf-8")


class CatalogTests(unittest.TestCase):
    def test_tracked_catalog_matches_the_fft_code(self) -> None:
        stored = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(
            stored,
            build_catalog(),
            "regenerate with: python -m tests.test_fft_catalog --write",
        )

    def test_catalog_holds_what_the_page_needs(self) -> None:
        structure = build_catalog()["structure"]
        capabilities = structure["capabilities"]
        self.assertEqual(capabilities["limits"], {"max_layers": 8})
        self.assertEqual(capabilities["output_backends"], ["direct", "pillow-bulk"])
        self.assertEqual(
            structure["analyzers"][0]["settings"],
            {"sample_rate": 44_100, "fft_size": 8192, "block_size": 512},
        )
        (layer_type,) = capabilities["layer_types"]
        self.assertEqual(layer_type["layer_type"], "current-bars")
        descriptor = layer_type["settings"]
        self.assertIn("groups", descriptor)
        self.assertIn("pillow-bulk", descriptor["backend_constraints"])
        (layer,) = structure["layers"]
        self.assertEqual(
            set(layer), {"layer_id", "layer_type", "analyzer_id", "visible", "settings"}
        )
        self.assertEqual(layer["analyzer_id"], structure["available_analyzer_ids"][0])
        # The default layer is unchanged by D-050: ocean, no gradient, overflow on,
        # and every setting at its descriptor default.
        defaults = {field["name"]: field["default"] for field in descriptor["fields"]}
        self.assertEqual(layer["settings"], defaults)
        self.assertEqual(
            (layer["settings"]["theme"], layer["settings"]["gradient"], layer["settings"]["overflow"]),
            ("ocean", False, True),
        )

    def test_the_controller_imports_no_fft_code(self) -> None:
        # D-049: the page gets the descriptor from this file, not from the service.
        code = (
            "import sys\n"
            "import matrix_controller.web_service, matrix_controller.registry\n"
            "import matrix_controller.supervisor, matrix_controller.presets\n"
            "roots = {'core', 'config', 'themes', 'visualizers', 'utils',\n"
            "         'structural_control', 'runtime_control', 'numpy', 'sounddevice'}\n"
            "print(sorted(name for name in sys.modules if name.split('.')[0] in roots))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(result.stdout.strip(), "[]")


if __name__ == "__main__":
    if "--write" in sys.argv:
        write_catalog()
        print(f"wrote {CATALOG.relative_to(ROOT)}")
    else:
        unittest.main()
