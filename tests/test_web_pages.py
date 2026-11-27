"""Checks on the web pages' files and their JavaScript (web-control-refactor M4).

- The flag-string fixture the page JavaScript is tested against must match
  ``ParameterSpec.cli_arguments``. Regenerate it with
  ``python -m tests.test_web_pages --write-fixture``.
- Every page asset has a literal route, and every module import resolves to one.
- No page mentions the fixed pairing code (D-013) or carries inline script/style.
- ``node --test`` runs the JavaScript tests under ``tests/js``; skipped without
  Node 22 or newer.
- Custom FFT theme documents the page builds agree with the FFT's parser
  (fft-themes M3 criterion 2); also skipped without Node.
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


# Builds documents the way the themes editor does and says whether the page
# accepts each; the parser must agree (fft-themes D-008, D-012, M3 criterion 2).
_THEME_CASES_SCRIPT = r"""
const themes = await import(process.argv[1]);
const full = () => {
  let list = [{position: 0, color: [255, 0, 0]}, {position: 1, color: [0, 0, 255]}];
  while (list.length < themes.MAX_STOPS) list = themes.addStop(list);
  return list;
};
const theme = changes => ({...themes.newTheme([]), ...changes});
const many = (count, changes = {}) => Array.from({length: count}, (_unused, index) =>
  theme({name: `my_t${index}`, ...changes}));
const cases = {
  new_theme: {themes: [themes.newTheme([])]},
  empty: {themes: []},
  longest_name_and_description: {themes: [theme({name: `my_${'a'.repeat(29)}`, description: '\u00e9'.repeat(80)})]},
  full_lists: {themes: [theme({axis: 'column', stops: full(), overflow: [full(), full(), full(), full()]})]},
  thirty_two: {themes: many(32)},
  near_the_cap: {themes: many(10, {stops: full(), overflow: [full(), full(), full(), full()]})},
  over_the_cap: {themes: many(32, {stops: full(), overflow: [full(), full(), full(), full()]})},
  thirty_three: {themes: many(33)},
  name_too_long: {themes: [theme({name: `my_${'a'.repeat(30)}`})]},
  duplicate: {themes: [theme({name: 'my_a'}), theme({name: 'my_a'})]},
  description_too_long: {themes: [theme({description: 'x'.repeat(81)})]},
  decreasing: {themes: [theme({stops: [{position: 0, color: [0, 0, 0]}, {position: 0.7, color: [0, 0, 0]},
    {position: 0.3, color: [0, 0, 0]}, {position: 1, color: [0, 0, 0]}]})]},
  five_overflow: {themes: [theme({overflow: Array.from({length: 5}, full)})]},
};
const out = Object.fromEntries(Object.entries(cases).map(([name, document]) =>
  [name, {document, ok: Object.keys(themes.themeErrors(document)).length === 0}]));
out.limits = {
  MAX_THEMES: themes.MAX_THEMES, MIN_STOPS: themes.MIN_STOPS, MAX_STOPS: themes.MAX_STOPS,
  MAX_OVERFLOW_LAYERS: themes.MAX_OVERFLOW, MAX_DESCRIPTION: themes.MAX_DESCRIPTION,
  MAX_DOCUMENT_BYTES: themes.MAX_DOCUMENT_BYTES, AXES: themes.AXES,
};
process.stdout.write(JSON.stringify(out));
"""


class CustomThemeDocumentTests(unittest.TestCase):
    def test_page_documents_agree_with_the_fft_parser(self) -> None:
        from my_programs.fft_program.themes import custom

        node = _node()
        if node is None:
            self.skipTest("Node 22 or newer is not installed")
        module = (STATIC / "js" / "programs" / "fft_themes.js").as_uri()
        result = subprocess.run(
            [node, "--input-type=module", "-e", _THEME_CASES_SCRIPT, module],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        cases = json.loads(result.stdout)
        limits = cases.pop("limits")
        for name in ("MAX_THEMES", "MIN_STOPS", "MAX_STOPS", "MAX_OVERFLOW_LAYERS",
                     "MAX_DESCRIPTION", "MAX_DOCUMENT_BYTES"):
            self.assertEqual(limits[name], getattr(custom, name), name)
        self.assertEqual(tuple(limits["AXES"]), custom.AXES)
        expected = {"new_theme", "empty", "longest_name_and_description", "full_lists",
                    "thirty_two", "near_the_cap"}
        self.assertEqual({name for name, case in cases.items() if case["ok"]}, expected)
        for name, case in cases.items():
            with self.subTest(case=name):
                try:
                    custom.parse_custom_themes(case["document"])
                    accepted = True
                except custom.CustomThemeError:
                    accepted = False
                self.assertEqual(accepted, case["ok"])


if __name__ == "__main__":
    if "--write-fixture" in sys.argv:
        write_fixture()
        print(f"wrote {FIXTURE.relative_to(ROOT)}")
    else:
        unittest.main()
