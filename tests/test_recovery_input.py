from __future__ import annotations

import unittest

from matrix_network.recovery_input import LongPressDetector, parse_event


class RecoveryInputTests(unittest.TestCase):
    def test_five_second_hold_is_required(self) -> None:
        detector = LongPressDetector(5.0)
        self.assertFalse(detector.process(0, 10.0))
        self.assertFalse(detector.process(1, 14.99))
        self.assertFalse(detector.process(0, 20.0))
        self.assertTrue(detector.process(1, 25.0))

    def test_duplicate_and_out_of_order_edges_fail_safe(self) -> None:
        detector = LongPressDetector(5.0)
        self.assertFalse(detector.process(1, 1.0))
        self.assertFalse(detector.process(0, 2.0))
        self.assertFalse(detector.process(0, 9.0))
        self.assertTrue(detector.process(1, 10.0))
        self.assertFalse(detector.process(1, 20.0))

    def test_gpiomon_event_parser_is_strict(self) -> None:
        self.assertEqual(parse_event("0 12 500000000\n"), (0, 12.5))
        self.assertIsNone(parse_event("falling edge"))
        self.assertIsNone(parse_event("2 12 0"))
        self.assertIsNone(parse_event("0 12 1000000000"))


if __name__ == "__main__":
    unittest.main()
