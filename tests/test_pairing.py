from __future__ import annotations

import unittest

from matrix_controller.pairing import PairingError, PairingManager


class PairingManagerTests(unittest.TestCase):
    def make_manager(self, clock, **overrides):
        values = {
            "clock": lambda: clock[0],
            "boot_code": "012345",
            "token_factory": lambda: "temporary-token-with-at-least-128-bits",
        }
        values.update(overrides)
        return PairingManager(**values)

    def test_boot_code_can_pair_multiple_devices_and_session_expires(self) -> None:
        clock = [10.0]
        tokens = iter(("a" * 32, "b" * 32))
        manager = self.make_manager(
            clock,
            session_ttl_seconds=100,
            token_factory=lambda: next(tokens),
        )

        first = manager.exchange("012345", client_id="phone")
        second = manager.exchange("012345", client_id="another-phone")
        self.assertTrue(manager.is_session_token(first.token))
        self.assertTrue(manager.is_session_token(second.token))
        clock[0] = 110.0
        self.assertFalse(manager.is_session_token(first.token))

    def test_wrong_and_malformed_codes_share_generic_error(self) -> None:
        clock = [0.0]
        manager = self.make_manager(clock)
        with self.assertRaises(PairingError) as wrong:
            manager.exchange("999999", client_id="phone")
        with self.assertRaises(PairingError) as malformed:
            manager.exchange("not-a-code", client_id="other")
        self.assertEqual(str(wrong.exception), "invalid pairing code")
        self.assertEqual(str(malformed.exception), str(wrong.exception))

    def test_client_rate_limit_and_global_failure_limit(self) -> None:
        clock = [0.0]
        manager = self.make_manager(
            clock,
            max_attempts_per_client=2,
            max_attempts_per_code=3,
        )
        for code in ("111111", "222222"):
            with self.assertRaises(PairingError):
                manager.exchange(code, client_id="phone-a")
        with self.assertRaises(PairingError) as limited:
            manager.exchange("333333", client_id="phone-a")
        self.assertEqual(limited.exception.status, 429)
        self.assertEqual(limited.exception.retry_after, 60)

        with self.assertRaises(PairingError):
            manager.exchange("333333", client_id="phone-b")
        with self.assertRaises(PairingError) as globally_limited:
            manager.exchange("012345", client_id="phone-c")
        self.assertEqual(globally_limited.exception.status, 429)

    def test_session_cap_evicts_oldest_token(self) -> None:
        clock = [0.0]
        tokens = iter(("a" * 32, "b" * 32))
        manager = self.make_manager(
            clock,
            token_factory=lambda: next(tokens),
            max_sessions=1,
        )
        first = manager.exchange("012345", client_id="phone-a")
        second = manager.exchange("012345", client_id="phone-b")
        self.assertFalse(manager.is_session_token(first.token))
        self.assertTrue(manager.is_session_token(second.token))


if __name__ == "__main__":
    unittest.main()
