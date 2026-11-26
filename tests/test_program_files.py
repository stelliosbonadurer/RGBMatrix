"""Program config files saved on the Pi (web-control-refactor M4b, D-043–D-047).

Covers the store (criteria 1–3), the Graph Traversal registration and its editor
descriptor (criterion 2), and the program reading the override (criterion 5).
``FftCustomThemesFileTests`` covers FFT's custom-theme file (fft-themes M2); FFT
reading it is in ``test_fft_themes``. The
HTTP routes and launches are in ``test_web_service.ProgramFilesApiTests``; the
supervisor's environment handling is in ``test_supervisor``.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path

from matrix_controller.registry import (
    GRAPH_TRAVERSAL_COMPONENT_COLORS,
    GRAPH_TRAVERSAL_RENDER_FIELDS,
    build_default_registry,
)
from matrix_controller.program_files import ProgramFileError, ProgramFileStore
from matrix_controller.runtime_transport import PROGRAM_CONFIG_ENVIRONMENT
from matrix_controller.schema import ValidationError
from my_programs.dsa_visualizations import graph_traversal
from my_programs.dsa_visualizations.graph_rendering import (
    DEFAULT_CONFIG_PATH,
    GraphColors,
    RenderConfig,
    load_render_config,
    parse_render_config,
)


TRACKED = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def edited(**changes):
    document = copy.deepcopy(TRACKED)
    for key, value in changes.items():
        if key in GraphColors.__dataclass_fields__:
            document["colors"][key] = value
        else:
            document[key] = value
    return document


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "program-files"
        self.store = ProgramFileStore(build_default_registry(), self.root)
        self.path = self.root / "graph_traversal.render.json"
        self.tracked_hash = hashlib.sha256(DEFAULT_CONFIG_PATH.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        # Criterion 1: the tracked default's bytes never change.
        self.assertEqual(
            hashlib.sha256(DEFAULT_CONFIG_PATH.read_bytes()).hexdigest(), self.tracked_hash
        )

    def save(self, document, revision=0):
        return self.store.replace(
            "graph_traversal", "render", {"expected_revision": revision, "document": document}
        )

    def refused(self, call, *args):
        with self.assertRaises(ProgramFileError) as caught:
            call(*args)
        return caught.exception

    def test_without_a_saved_file_the_tracked_default_is_used(self) -> None:
        snapshot = self.store.snapshot("graph_traversal", "render")
        self.assertEqual(
            (snapshot["revision"], snapshot["source"], snapshot["error"]), (0, "default", None)
        )
        self.assertEqual(snapshot["document"], TRACKED)
        self.assertEqual(snapshot["default"], TRACKED)
        self.assertEqual(snapshot["label"], "Colors & rendering")
        self.assertEqual([field["key"] for field in snapshot["fields"]][0], "node_size")
        self.assertEqual(self.store.launch_files("graph_traversal"), {"render": (0, None)})
        self.assertEqual(self.store.launch_files("sorts"), {})
        self.assertFalse(self.root.exists())

    def test_a_save_writes_one_private_file_and_raises_the_revision(self) -> None:
        document = edited(node_size=2, background=[1, 2, 3])
        snapshot = self.save(document)
        self.assertEqual((snapshot["revision"], snapshot["source"]), (1, "stored"))
        self.assertEqual(snapshot["document"], document)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(
            json.loads(self.path.read_text()),
            {"version": 1, "revision": 1, "document": document},
        )
        self.assertEqual(sorted(os.listdir(self.root)), ["graph_traversal.render.json"])
        self.assertEqual(self.store.launch_files("graph_traversal"), {"render": (1, document)})
        # Restoring the default is a save of the default (D-047).
        snapshot = self.save(snapshot["default"], revision=1)
        self.assertEqual((snapshot["revision"], snapshot["document"]), (2, TRACKED))

    def test_a_stale_revision_is_a_conflict(self) -> None:
        self.save(edited(node_size=2))
        error = self.refused(self.save, TRACKED, 0)
        self.assertEqual(
            (error.status, error.code, error.current_revision),
            (HTTPStatus.CONFLICT, "revision_conflict", 1),
        )

    def test_the_programs_parser_is_the_check_and_names_the_field(self) -> None:
        component_colors = TRACKED["component_colors"]
        cases = (
            (edited(node_size=3), "node_size must be 1 or 2"),
            (edited(direction_marker_pixels=True), "direction_marker_pixels"),
            (edited(start_node=[0, 0, 256]), "color 'start_node'"),
            (edited(component_colors=[*component_colors[:5], [1, 2], *component_colors[6:]]),
             r"component_colors\[5\]"),
            (edited(component_colors=component_colors[:31]), "exactly 32 colors"),
            (edited(component_colors=[*component_colors, [1, 2, 3]]), "exactly 32 colors"),
            ({**TRACKED, "extra": 1}, "must contain only"),
            ({**TRACKED, "colors": {"node": [1, 2, 3]}}, "missing: background"),
            ({**TRACKED, "colors": ["node"]}, "colors must be an object"),
            ([], "must be a JSON object"),
        )
        for document, message in cases:
            with self.subTest(message=message):
                error = self.refused(self.save, document)
                self.assertEqual((error.status, error.code), (HTTPStatus.BAD_REQUEST, "invalid_program_file"))
                self.assertRegex(str(error), message)
        self.assertFalse(self.path.exists())

    def test_the_request_shape_is_exact(self) -> None:
        for payload in (
            {"document": TRACKED},
            {"expected_revision": 0, "document": TRACKED, "extra": 1},
            {"expected_revision": -1, "document": TRACKED},
            {"expected_revision": True, "document": TRACKED},
            [],
        ):
            with self.subTest(payload=str(payload)[:40]):
                error = self.refused(self.store.replace, "graph_traversal", "render", payload)
                self.assertEqual(error.status, HTTPStatus.BAD_REQUEST)

    def test_unknown_programs_and_files_are_not_found(self) -> None:
        for program_id, name in (("sorts", "render"), ("graph_traversal", "colors"), ("../x", "render")):
            with self.subTest(program_id=program_id, name=name):
                error = self.refused(self.store.snapshot, program_id, name)
                self.assertEqual((error.status, error.code), (HTTPStatus.NOT_FOUND, "not_found"))

    def test_an_unusable_file_is_reported_kept_and_only_removable(self) -> None:
        self.root.mkdir()
        valid_layout = {"version": 1, "revision": 4, "document": edited(node_size=7)}
        # The nested case raises RecursionError, not ValueError (review finding 3).
        nested = "[" * 100_000 + "]" * 100_000
        for contents in ("{", nested, json.dumps({"version": 2, "revision": 1, "document": TRACKED}),
                         json.dumps({"revision": 1}), json.dumps(valid_layout)):
            with self.subTest(contents=contents[:30]):
                self.path.write_text(contents)
                snapshot = self.store.snapshot("graph_traversal", "render")
                self.assertTrue(snapshot["error"])
                self.assertEqual(
                    (snapshot["revision"], snapshot["source"], snapshot["document"]),
                    (None, "default", TRACKED),
                )
                self.assertEqual(self.store.launch_files("graph_traversal"), {"render": (None, None)})
                error = self.refused(self.save, TRACKED, 4)
                self.assertEqual(error.status, HTTPStatus.SERVICE_UNAVAILABLE)
                self.assertEqual(self.path.read_text(), contents)
        self.assertIn("node_size must be 1 or 2", snapshot["error"])
        snapshot = self.store.remove_unusable("graph_traversal", "render")
        self.assertEqual((snapshot["revision"], snapshot["error"]), (0, None))
        self.assertFalse(self.path.exists())

    def test_a_usable_or_missing_file_is_not_removed(self) -> None:
        error = self.refused(self.store.remove_unusable, "graph_traversal", "render")
        self.assertEqual((error.status, error.code), (HTTPStatus.CONFLICT, "program_file_usable"))
        self.save(edited(node_size=2))
        self.refused(self.store.remove_unusable, "graph_traversal", "render")
        self.assertTrue(self.path.exists())


class RegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = build_default_registry().get("graph_traversal")
        self.render = self.spec.program_file("render")

    def test_only_graph_traversal_and_fft_register_a_file(self) -> None:
        expected = {
            "graph_traversal": [{"name": "render", "label": "Colors & rendering"}],
            "fft": [{"name": "themes", "label": "Custom themes"}],
        }
        for spec in build_default_registry().list():
            with self.subTest(program=spec.visualization_id):
                public = spec.as_public_dict()["files"]
                self.assertEqual(public, expected.get(spec.visualization_id, []))
        self.assertEqual(self.render.default_path.resolve(), DEFAULT_CONFIG_PATH.resolve())

    def test_a_program_registers_at_most_one_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "at most one program file"):
            dataclasses.replace(self.spec, program_files=(self.render, self.render))

    def test_the_tracked_default_passes_the_check(self) -> None:
        self.render.validate(TRACKED)
        self.assertEqual(len(TRACKED["component_colors"]), GRAPH_TRAVERSAL_COMPONENT_COLORS)

    def test_the_descriptor_names_exactly_the_loaders_fields(self) -> None:
        by_key = {field["key"]: field for field in GRAPH_TRAVERSAL_RENDER_FIELDS}
        self.assertEqual(set(by_key), set(RenderConfig.__dataclass_fields__))
        self.assertEqual(set(by_key), set(TRACKED))
        self.assertEqual(set(by_key["colors"]["names"]), set(GraphColors.__dataclass_fields__))
        self.assertEqual(len(by_key["colors"]["names"]), len(GraphColors.__dataclass_fields__))
        self.assertEqual(by_key["component_colors"]["length"], GRAPH_TRAVERSAL_COMPONENT_COLORS)
        # Each integer range is the loader's: the bounds pass, one beyond fails.
        for key, field in by_key.items():
            if field["kind"] != "int":
                continue
            with self.subTest(key=key):
                parse_render_config(edited(**{key: field["minimum"]}))
                parse_render_config(edited(**{key: field["maximum"]}))
                for outside in (field["minimum"] - 1, field["maximum"] + 1):
                    with self.assertRaisesRegex(ValueError, key):
                        parse_render_config(edited(**{key: outside}))

    def test_the_program_alone_accepts_any_non_empty_component_list(self) -> None:
        document = edited(component_colors=[[1, 2, 3]])
        self.assertEqual(len(parse_render_config(document).component_colors), 1)
        with self.assertRaisesRegex(ValidationError, "exactly 32"):
            self.render.validate(document)


ROOT = Path(__file__).resolve().parents[1]
FFT_THEMES_DEFAULT = ROOT / "my_programs" / "fft_program" / "config" / "custom_themes.json"


def custom_theme(name="my_sunset", **changes):
    theme = {
        "name": name,
        "description": "Purple to gold",
        "axis": "height",
        "stops": [
            {"position": 0.0, "color": [60, 20, 140]},
            {"position": 1.0, "color": [255, 200, 80]},
        ],
        "overflow": [],
    }
    theme.update(changes)
    return theme


class FftCustomThemesFileTests(unittest.TestCase):
    """fft-themes M2 acceptance criteria 1 and 7 (D-007, D-008, D-009)."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = ProgramFileStore(build_default_registry(), Path(self.directory.name))
        self.spec = build_default_registry().get("fft").program_file("themes")

    def save(self, document):
        return self.store.replace(
            "fft", "themes", {"expected_revision": 0, "document": document}
        )

    def test_registration_has_no_editor_fields_and_a_valid_empty_default(self) -> None:
        self.assertEqual((self.spec.label, self.spec.fields), ("Custom themes", ()))
        self.assertEqual(self.spec.default_path.resolve(), FFT_THEMES_DEFAULT.resolve())
        tracked = json.loads(FFT_THEMES_DEFAULT.read_text(encoding="utf-8"))
        self.assertEqual(tracked, {"themes": []})
        self.spec.validate(tracked)
        snapshot = self.store.snapshot("fft", "themes")
        self.assertEqual((snapshot["source"], snapshot["fields"]), ("default", []))

    def test_a_valid_document_is_saved_and_reaches_the_launch(self) -> None:
        document = {"themes": [
            custom_theme(),
            custom_theme("my_skyline", axis="column", overflow=[[
                {"position": 0.0, "color": [255, 255, 255]},
                {"position": 1.0, "color": [255, 0, 0]},
            ]]),
        ]}
        snapshot = self.save(document)
        self.assertEqual((snapshot["revision"], snapshot["source"]), (1, "stored"))
        self.assertEqual(self.store.launch_files("fft"), {"themes": (1, document)})

    def test_the_fft_parser_is_the_check_and_names_the_problem(self) -> None:
        stop = {"position": 0.5, "color": [1, 2, 3]}
        cases = (
            ([], "only themes"),
            ({"themes": [], "extra": 1}, "only themes"),
            ({"themes": {}}, "themes must be an array"),
            ({"themes": [custom_theme(name=f"my_{i}") for i in range(33)]}, "at most 32"),
            ({"themes": [{"name": "my_a"}]}, r"themes\[0\] must contain only"),
            ({"themes": [{**custom_theme(), "extra": 1}]}, "must contain only"),
            ({"themes": [custom_theme(name="sunset")]}, "must be my_"),
            ({"themes": [custom_theme(name="ocean")]}, "must be my_"),
            ({"themes": [custom_theme(name="my_")]}, "must be my_"),
            ({"themes": [custom_theme(name="my_Sunset")]}, "must be my_"),
            ({"themes": [custom_theme(name="my_" + "a" * 30)]}, "must be my_"),
            ({"themes": [custom_theme(), custom_theme()]}, "more than once"),
            ({"themes": [custom_theme(description="x" * 81)]}, "at most 80"),
            ({"themes": [custom_theme(description=None)]}, "description"),
            ({"themes": [custom_theme(axis="diagonal")]}, "axis must be one of"),
            ({"themes": [custom_theme(stops=[stop])]}, "2 to 16 stops"),
            ({"themes": [custom_theme(stops=[stop] * 17)]}, "2 to 16 stops"),
            ({"themes": [custom_theme(stops=[{"position": 0.0}, stop])]}, "only position and color"),
            ({"themes": [custom_theme(stops=[{"position": True, "color": [1, 2, 3]}, stop])]},
             "position must be a number"),
            ({"themes": [custom_theme(stops=[{"position": 10 ** 400, "color": [1, 2, 3]}, stop])]},
             "between 0 and 1"),
            ({"themes": [custom_theme(stops=[{"position": 0.0, "color": [1, 2, True]}, stop])]},
             r"\[r, g, b\] integers"),
            ({"themes": [custom_theme(stops=[{"position": 0.0, "color": [1, 2, 3.5]}, stop])]},
             r"\[r, g, b\] integers"),
            ({"themes": [custom_theme(stops=[{"position": 0.0, "color": [1, 2]}, stop])]},
             r"\[r, g, b\] integers"),
            ({"themes": [custom_theme(stops=[{"position": 0.0, "color": [1, 2, 256]},
                                             {"position": 1.0, "color": [0, 0, 0]}])]},
             "channels must be 0-255"),
            ({"themes": [custom_theme(stops=[{"position": 0.0, "color": [1, 2, 3]},
                                             {"position": 1.5, "color": [0, 0, 0]}])]},
             "between 0 and 1"),
            ({"themes": [custom_theme(stops=[{"position": 0.0, "color": [1, 2, 3]},
                                             {"position": 0.9, "color": [0, 0, 0]}])]},
             "start at 0 and end at 1"),
            ({"themes": [custom_theme(stops=[{"position": 0.0, "color": [1, 2, 3]},
                                             {"position": 0.6, "color": [1, 2, 3]},
                                             {"position": 0.4, "color": [1, 2, 3]},
                                             {"position": 1.0, "color": [0, 0, 0]}])]},
             "must not decrease"),
            ({"themes": [custom_theme(overflow={})]}, "at most 4 stop lists"),
            ({"themes": [custom_theme(overflow=[custom_theme()["stops"]] * 5)]}, "at most 4 stop lists"),
            ({"themes": [custom_theme(overflow=[[stop]])]}, r"overflow\[0\] must be an array"),
        )
        for document, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(ProgramFileError) as caught:
                    self.save(document)
                error = caught.exception
                self.assertEqual(
                    (error.status, error.code), (HTTPStatus.BAD_REQUEST, "invalid_program_file")
                )
                self.assertRegex(str(error), message)
        self.assertEqual(self.store.launch_files("fft"), {"themes": (0, None)})

    def test_the_limits_themselves_are_accepted(self) -> None:
        stops = [{"position": index / 15, "color": [index, index, index]} for index in range(16)]
        stops[-1]["position"] = 1.0
        theme = custom_theme(name="my_" + "a" * 29, description="x" * 80, stops=stops,
                             overflow=[stops] * 4)
        document = {"themes": [theme] + [custom_theme(name=f"my_{i}") for i in range(31)]}
        self.spec.validate(document)

    def test_importing_the_validator_leaves_the_import_path_alone(self) -> None:
        # A fresh interpreter, so earlier tests' imports can't hide a change (D-007).
        script = (
            "import sys; before = list(sys.path)\n"
            "from matrix_controller.registry import build_default_registry\n"
            "build_default_registry().get('fft').program_file('themes').validate({'themes': []})\n"
            "assert 'my_programs.fft_program.themes.custom' in sys.modules\n"
            "assert 'themes' not in sys.modules, 'themes imported as a top-level package'\n"
            "assert sys.path == before, sys.path\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class ProgramReadsTheOverrideTests(unittest.TestCase):
    def test_without_the_variable_the_tracked_file_is_read(self) -> None:
        config = graph_traversal.resolve_render_config({})
        tracked = load_render_config()
        self.assertEqual(
            (config.node_size, config.colors.background, config.component_colors),
            (tracked.node_size, tracked.colors.background, tracked.component_colors),
        )

    def test_the_variable_is_parsed_with_the_same_rules(self) -> None:
        document = edited(node_size=2, background=[9, 8, 7])
        config = graph_traversal.resolve_render_config(
            {PROGRAM_CONFIG_ENVIRONMENT: json.dumps(document)}
        )
        self.assertEqual((config.node_size, config.colors.background), (2, (9, 8, 7)))
        for value, message in (("{", "invalid JSON"), (json.dumps(edited(node_size=5)), "node_size")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                graph_traversal.resolve_render_config({PROGRAM_CONFIG_ENVIRONMENT: value})


if __name__ == "__main__":
    unittest.main()
