"""Boot-code pairing for browser visualization controls.

Pairing sessions intentionally authorize only visualization run/stop operations in
the HTTP layer. They are never forwarded as NetworkManager administration tokens.
Temporary sessions and throttling are process-local, so a web-service restart
revokes browser access without rotating the separate boot-scoped code file.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Callable


Clock = Callable[[], float]
TokenFactory = Callable[[], str]


class PairingError(RuntimeError):
    """Safe pairing failure with an HTTP status and optional retry interval."""

    def __init__(self, status: int, message: str, *, retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class PairingSession:
    token: str
    expires_in: int


class PairingManager:
    """Exchange one boot-scoped code for bounded temporary bearer sessions safely.

    One lock covers attempt accounting and session issuance. This matters under
    ``ThreadingHTTPServer`` because limits and the session cap must remain exact.
    """

    def __init__(
        self,
        boot_code: str,
        *,
        session_ttl_seconds: int = 4 * 60 * 60,
        attempt_window_seconds: int = 60,
        max_attempts_per_client: int = 5,
        max_attempts_per_code: int = 20,
        max_sessions: int = 32,
        clock: Clock = time.monotonic,
        token_factory: TokenFactory | None = None,
    ):
        values = (
            session_ttl_seconds,
            attempt_window_seconds,
            max_attempts_per_client,
            max_attempts_per_code,
            max_sessions,
        )
        if any(value <= 0 for value in values):
            raise ValueError("pairing limits must be positive")
        if re.fullmatch(r"\d{6}", boot_code) is None:
            raise ValueError("boot pairing code must contain exactly six digits")
        self.session_ttl_seconds = session_ttl_seconds
        self.attempt_window_seconds = attempt_window_seconds
        self.max_attempts_per_client = max_attempts_per_client
        self.max_attempts_per_code = max_attempts_per_code
        self.max_sessions = max_sessions
        self._clock = clock
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._lock = threading.RLock()
        self._code_digest = self._digest(boot_code)
        self._global_attempts: deque[float] = deque()
        self._client_attempts: dict[str, deque[float]] = {}
        self._sessions: OrderedDict[str, float] = OrderedDict()

    def exchange(self, code: object, *, client_id: str) -> PairingSession:
        """Exchange the boot code for one temporary visualization token."""

        now = self._clock()
        with self._lock:
            attempts = self._client_attempts.setdefault(client_id, deque())
            cutoff = now - self.attempt_window_seconds
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= self.max_attempts_per_client:
                raise PairingError(
                    429,
                    "too many pairing attempts; try again later",
                    retry_after=self.attempt_window_seconds,
                )
            attempts.append(now)

            while self._global_attempts and self._global_attempts[0] <= cutoff:
                self._global_attempts.popleft()
            if len(self._global_attempts) >= self.max_attempts_per_code:
                raise PairingError(
                    429,
                    "too many pairing attempts; try again later",
                    retry_after=self.attempt_window_seconds,
                )
            self._global_attempts.append(now)

            valid_shape = type(code) is str and re.fullmatch(r"\d{6}", code) is not None
            valid_code = (
                valid_shape
                and hmac.compare_digest(self._digest(code), self._code_digest)
            )
            if not valid_code:
                raise PairingError(401, "invalid pairing code")

            self._prune_sessions_locked(now)
            token = self._token_factory()
            if len(token) < 22:  # about 128 bits even with URL-safe base64 encoding
                raise RuntimeError("pairing token generator returned an unsafe token")
            self._sessions[token] = now + self.session_ttl_seconds
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)
            return PairingSession(token=token, expires_in=self.session_ttl_seconds)

    def is_session_token(self, supplied_token: object) -> bool:
        if type(supplied_token) is not str:
            return False
        now = self._clock()
        with self._lock:
            self._prune_sessions_locked(now)
            expires_at = self._sessions.get(supplied_token)
            return expires_at is not None and now < expires_at

    def revoke_session(self, supplied_token: object) -> bool:
        """Revoke only the presented temporary session; permanent tokens are external."""

        if type(supplied_token) is not str:
            return False
        with self._lock:
            return self._sessions.pop(supplied_token, None) is not None

    @staticmethod
    def _digest(code: str) -> bytes:
        return hashlib.sha256(code.encode("ascii")).digest()

    def _prune_sessions_locked(self, now: float) -> None:
        expired = [token for token, expiry in self._sessions.items() if now >= expiry]
        for token in expired:
            self._sessions.pop(token, None)
