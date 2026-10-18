"""Small immutable data objects shared by the network controller."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceSnapshot:
    """The connectivity facts needed by policy; internet access is not required."""

    state_code: int
    connection: str | None
    ipv4: str | None

    @property
    def connected(self) -> bool:
        return self.state_code >= 100 and self.connection is not None and self.ipv4 is not None


@dataclass(frozen=True)
class WifiProfile:
    """Non-secret metadata for one saved NetworkManager Wi-Fi connection."""

    name: str
    uuid: str
    autoconnect: bool
    priority: int
    active: bool = False


@dataclass(frozen=True)
class ScanResult:
    """Deduplicated network data safe to return to a setup browser."""

    ssid: str
    signal: int
    security: str


class NetworkOperationError(RuntimeError):
    """Sanitized operational failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class NetworkValidationError(ValueError):
    """Invalid caller-supplied network configuration."""

