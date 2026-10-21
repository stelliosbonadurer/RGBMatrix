from __future__ import annotations

import unittest

from matrix_controller.connection_info import connection_details, text_positions


class SystemInfoTests(unittest.TestCase):
    def test_lan_details_use_active_network_and_address(self) -> None:
        pages = connection_details(
            {"ssid": "Studio", "ipv4": "192.168.1.20", "ap": {"active": False}},
            "012345",
        )
        self.assertEqual(pages[0], ("CONNECT WIFI", "Studio"))
        self.assertEqual(pages[1], ("OPEN URL", "192.168.1.20:8080"))
        self.assertEqual(pages[2], ("PAIR CODE", "012345"))

    def test_setup_ap_details_take_precedence(self) -> None:
        pages = connection_details(
            {
                "ssid": "old-profile",
                "ipv4": None,
                "ap": {"active": True, "ssid": "RGB-Matrix-ABCD", "address": "192.168.50.1"},
            },
            "654321",
        )
        self.assertEqual(pages[0][1], "RGB-Matrix-ABCD")
        self.assertEqual(pages[1][1], "192.168.50.1:8080")

    def test_short_text_is_centered_and_long_text_wraps_continuously(self) -> None:
        self.assertEqual(
            text_positions("123456", glyph_width=10, viewport_width=64, elapsed=99),
            (2,),
        )
        positions = text_positions(
            "192.168.1.204:8080",
            glyph_width=4,
            viewport_width=64,
            elapsed=1.0,
        )
        self.assertEqual(len(positions), 2)
        self.assertGreater(positions[1], positions[0])


if __name__ == "__main__":
    unittest.main()
