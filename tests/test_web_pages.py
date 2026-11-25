"""Checks on the web pages' files and their JavaScript (web-control-refactor M4).

- The flag-string fixture the page JavaScript is tested against must match
  ``ParameterSpec.cli_arguments``. Regenerate it with
  ``python -m tests.test_web_pages --write-fixture``.
- Every page asset has a literal route, and every module import resolves to one.
- No page mentions the fixed pairing code (D-013) or carries inline script/style.
- ``node --test`` runs the JavaScript tests under ``tests/js``; skipped without
  Node 22 or newer.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from posixpath import dirname, join, normpath

from matrix_controller.registry import build_default_registry
from matrix_controller.web_service import STATIC_ASSETS


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "matrix_controller" / "static"
FIXTURE = ROOT / "tests" / "fixtures" / "cli_flag_vectors.json"
JS_TESTS = ROOT / "tests" / "js"
PAGES = ("home.html", "program.html")

# Values beyond each parameter's default that exercise every cli_arguments rule:
# both boolean forms, a fixed true value, Python float formatting, and strings
# that need quoting on the page.
EXTRA_VALUES = {
    "led_no_hardware_pulse": (True,),
    "led_show_refresh": (True,),
    "led_pixel_mapper": ("Rotate:90", "U-mapper;Rotate:180"),
    "led_panel_type": ("FM6126A",),
    "delay": (0.0, 2.0, 0.00005, 1.25, 0.1, 0.0001),
    "k": (4.0, 0.5, 2.5),
    "hold": (0.1, 30.0),
    "frame_duration": (0.01, 10.0, 0.333),
    "directed": (True,),
    "connected": (False,),
    "e": (45,),
    "seed": (2_147_483_647,),
}


def build_fixture() -> dict:
    registry = build_default_registry()
    seen: dict[str, object] = {}
    for spec in registry.list():
        for parameter in (*spec.matrix_parameters, *spec.parameters):
            # Two programs share a name (delay, n, seed) with different ranges;
            # keep each distinct contract once.
            key = json.dumps(parameter.as_public_dict(), sort_keys=True)
            seen.setdefault(key, parameter)
    cases = []
    for parameter in seen.values():
        values = (parameter.default, *EXTRA_VALUES.get(parameter.name, ()))
        for value in values:
            try:
                normalized = parameter.normalize(value)
            except ValueError:
                continue  # outside this contract's range (a shared name)
            cases.append(
                {
                    "parameter": parameter.as_public_dict(),
                    "value": normalized,
                    "argv": parameter.cli_arguments(normalized),
                }
            )
    return {
        "about": "ParameterSpec.cli_arguments vectors for the page JavaScript "
        "(tests/test_web_pages.py).",
        "cases": cases,
    }


def write_fixture() -> None:
    FIXTURE.write_text(json.dumps(build_fixture(), indent=1) + "\n", encoding="utf-8")


class FlagFixtureTests(unittest.TestCase):
    def test_fixture_matches_the_python_rules(self) -> None:
        stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(
            stored,
            build_fixture(),
            "regenerate with: python -m tests.test_web_pages --write-fixture",
        )

    def test_fixture_covers_every_rule(self) -> None:
        cases = build_fixture()["cases"]
        argv = [case["argv"] for case in cases]
        self.assertIn([], argv)  # false with no negative flag
        self.assertIn(["--not-connected"], argv)
        self.assertIn(["--led-no-hardware-pulse", "1"], argv)
        self.assertIn(["--delay", "5e-05"], argv)
        self.assertIn(["--k", "1.0"], argv)
        self.assertIn(["--led-pixel-mapper", ""], argv)


class PageAssetTests(unittest.TestCase):
    def test_every_static_file_has_a_route_and_every_route_a_file(self) -> None:
        on_disk = {
            path.relative_to(STATIC).as_posix()
            for path in STATIC.rglob("*")
            if path.is_file() and path.name != ".DS_Store"
        }
        routed = {filename for filename, _content_type in STATIC_ASSETS.values()}
        self.assertEqual(on_disk - routed, {"home.html", "program.html", "setup.html"})
        self.assertEqual(routed - on_disk, set())
        for route, (filename, _content_type) in STATIC_ASSETS.items():
            self.assertEqual(route, f"/static/{filename}")

    def test_module_imports_and_page_references_resolve_to_routes(self) -> None:
        import_pattern = re.compile(r"""(?:from|import)\s*\(?\s*['"]([^'"]+)['"]""")
        found = 0
        for route, (filename, content_type) in STATIC_ASSETS.items():
            if "javascript" not in content_type:
                continue
            for target in import_pattern.findall((STATIC / filename).read_text(encoding="utf-8")):
                found += 1
                with self.subTest(module=filename, target=target):
                    self.assertTrue(target.startswith("./") or target.startswith("../"))
                    self.assertIn(normpath(join(dirname(route), target)), STATIC_ASSETS)
        self.assertGreaterEqual(found, 8)  # the pattern really finds the imports
        reference = re.compile(r"""(?:src|href)="(/static/[^"]+)\"""")
        for page in PAGES:
            for target in reference.findall((STATIC / page).read_text(encoding="utf-8")):
                with self.subTest(page=page, target=target):
                    self.assertIn(target, STATIC_ASSETS)

    def test_no_page_mentions_the_fixed_pairing_code(self) -> None:
        for path in STATIC.rglob("*"):
            if path.is_file() and path.suffix in {".html", ".js", ".css"}:
                with self.subTest(path=path.name):
                    self.assertNotIn("000000", path.read_text(encoding="utf-8"))

    def test_pages_carry_no_inline_script_or_style(self) -> None:
        # The page policy allows only same-origin scripts and styles.
        for page in PAGES:
            text = (STATIC / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertNotRegex(text, r"<script(?![^>]*\bsrc=)")
                self.assertNotIn("<style", text)
                self.assertNotRegex(text, r"\sstyle=")
                self.assertNotRegex(text, r"\son[a-z]+=")


def _node() -> str | None:
    node = shutil.which("node")
    if node is None:
        return None
    try:
        version = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10)
        major = int(version.stdout.strip().lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    # Node 22 loads these .js files as ES modules without a package.json.
    return node if major >= 22 else None


class JavaScriptTests(unittest.TestCase):
    def test_node_suite_passes(self) -> None:
        node = _node()
        if node is None:
            self.skipTest("Node 22 or newer is not installed")
        files = sorted(str(path) for path in JS_TESTS.glob("*.test.mjs"))
        self.assertTrue(files)
        result = subprocess.run(
            [node, "--test", *files], cwd=ROOT, capture_output=True, text=True, timeout=120
        )
        self.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-2000:])


if __name__ == "__main__":
    if "--write-fixture" in sys.argv:
        write_fixture()
        print(f"wrote {FIXTURE.relative_to(ROOT)}")
    else:
        unittest.main()
