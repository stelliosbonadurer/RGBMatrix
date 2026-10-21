from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from matrix_controller.boot_code import ensure_boot_code, read_boot_code


class BootCodeTests(unittest.TestCase):
    def test_same_boot_reuses_code_and_new_boot_rotates_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boot-code.json"
            codes = iter(("012345", "987654"))
            first = ensure_boot_code(path, boot_id="boot-a", code_factory=lambda: next(codes))
            same_boot = ensure_boot_code(path, boot_id="boot-a", code_factory=lambda: next(codes))
            next_boot = ensure_boot_code(path, boot_id="boot-b", code_factory=lambda: next(codes))

            self.assertEqual(first, "012345")
            self.assertEqual(same_boot, first)
            self.assertEqual(next_boot, "987654")
            self.assertEqual(read_boot_code(path), next_boot)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_corrupt_state_is_replaced_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boot-code.json"
            path.write_text("not-json", encoding="utf-8")
            code = ensure_boot_code(
                path,
                boot_id="boot-a",
                code_factory=lambda: "000007",
            )
            self.assertEqual(code, "000007")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["boot_id"], "boot-a")

    def test_read_rejects_missing_or_invalid_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boot-code.json"
            with self.assertRaises(RuntimeError):
                read_boot_code(path)
            path.write_text('{"code":"123"}', encoding="utf-8")
            with self.assertRaises(RuntimeError):
                read_boot_code(path)


if __name__ == "__main__":
    unittest.main()
