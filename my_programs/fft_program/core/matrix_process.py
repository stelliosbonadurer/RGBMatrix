"""Bounded primitives for the opt-in matrix-process experiment.

The experiment keeps one analysis/control parent and one matrix/render child.  This
module contains only the small cross-process seams: a fixed two-slot numeric
transport, a read-only telemetry view, lifecycle messages, and startup validation.
It deliberately contains no renderer restart, structural controls, or frame queue.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import grp
import json
import os
import pwd
import time
from typing import Any, Mapping, Optional

import numpy as np  # type: ignore


MATRIX_PROCESS_EXPERIMENT_ENVIRONMENT = (
    "RGBMATRIX_FFT_MATRIX_PROCESS_EXPERIMENT"
)
MATRIX_PROCESS_RESULT_SHAPE = (64,)
MATRIX_PROCESS_SLOT_COUNT = 2
MAX_LIFECYCLE_MESSAGE_BYTES = 64 * 1024


def matrix_process_experiment_enabled(environ: Mapping[str, str]) -> bool:
    """Return the explicit startup selection, rejecting ambiguous values."""

    value = environ.get(MATRIX_PROCESS_EXPERIMENT_ENVIRONMENT)
    if value is None or value == "0":
        return False
    if value == "1":
        return True
    raise ValueError(
        f"{MATRIX_PROCESS_EXPERIMENT_ENVIRONMENT} must be 0 or 1"
    )


class SharedLatestAnalysisTransport:
    """Two fixed shared slots with complete-publication and lease metadata.

    The parent is the sole producer and the renderer child is the sole consumer.
    The metadata lock is held only while claiming, publishing, leasing, or releasing;
    NumPy analysis and layer preparation use the slot outside the lock.
    """

    _ACTIVE_EPOCH = 0
    _WRITING_INDEX = 1
    _PUBLISHED_INDEX = 2
    _LEASED_INDEX = 3
    _PUBLISHED_SEQUENCE = 4
    _PUBLISHED_TIME_NS = 5
    _DROP_COUNT = 6
    _LAST_ACQUIRED_SEQUENCE = 7
    _LAST_ACQUIRED_TIME_NS = 8
    _SLOT_SEQUENCE_0 = 9
    _SLOT_SEQUENCE_1 = 10
    _SLOT_TIME_0 = 11
    _SLOT_TIME_1 = 12
    _SLOT_SOURCE_GENERATION_0 = 13
    _SLOT_SOURCE_GENERATION_1 = 14
    _LAST_ACQUIRED_SOURCE_GENERATION = 15
    _SLOT_EPOCH_0 = 16
    _SLOT_EPOCH_1 = 17
    _LAST_ACQUIRED_EPOCH = 18
    _METADATA_LENGTH = 19
    records_source_generation = True

    def __init__(self, context: Any, num_bins: int = 64) -> None:
        if num_bins <= 0:
            raise ValueError("num_bins must be positive")
        self.num_bins = int(num_bins)
        self._buffer = context.RawArray(
            ctypes.c_float,
            MATRIX_PROCESS_SLOT_COUNT * self.num_bins,
        )
        self._metadata = context.RawArray(
            ctypes.c_longlong,
            self._METADATA_LENGTH,
        )
        self._lock = context.Lock()
        self._metadata[self._WRITING_INDEX] = -1
        self._metadata[self._PUBLISHED_INDEX] = -1
        self._metadata[self._LEASED_INDEX] = -1
        self._views: Optional[tuple[np.ndarray, np.ndarray]] = None

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        # NumPy views are process-local wrappers over the shared ctypes buffer. Do
        # not pickle/copy them when the transport is passed to the spawned child.
        state["_views"] = None
        return state

    def _slot_views(self) -> tuple[np.ndarray, np.ndarray]:
        views = self._views
        if views is None:
            array = np.frombuffer(self._buffer, dtype=np.float32).reshape(
                MATRIX_PROCESS_SLOT_COUNT,
                self.num_bins,
            )
            views = (array[0], array[1])
            self._views = views
        return views

    @property
    def slots(self) -> tuple[np.ndarray, np.ndarray]:
        """Stable process-local NumPy views used by tests and analysis."""

        return self._slot_views()

    def activate_epoch(self, epoch: int) -> None:
        with self._lock:
            self._metadata[self._ACTIVE_EPOCH] = int(epoch)

    def claim_write(self) -> Optional[np.ndarray]:
        with self._lock:
            if self._metadata[self._WRITING_INDEX] != -1:
                raise RuntimeError("analysis transport already has a claimed write slot")
            published = self._metadata[self._PUBLISHED_INDEX]
            leased = self._metadata[self._LEASED_INDEX]
            for index in range(MATRIX_PROCESS_SLOT_COUNT):
                if index != published and index != leased:
                    self._metadata[self._WRITING_INDEX] = index
                    return self._slot_views()[index]
            self._metadata[self._DROP_COUNT] += 1
            return None

    def publish_claimed(
        self,
        epoch: int,
        source_generation: int = 0,
    ) -> bool:
        with self._lock:
            index = self._metadata[self._WRITING_INDEX]
            if index == -1:
                raise RuntimeError("analysis transport has no claimed write slot")
            self._metadata[self._WRITING_INDEX] = -1
            if int(epoch) != self._metadata[self._ACTIVE_EPOCH]:
                self._metadata[self._DROP_COUNT] += 1
                return False
            sequence = self._metadata[self._PUBLISHED_SEQUENCE] + 1
            published_time_ns = time.monotonic_ns()
            self._metadata[self._PUBLISHED_SEQUENCE] = sequence
            self._metadata[self._PUBLISHED_INDEX] = index
            self._metadata[self._PUBLISHED_TIME_NS] = published_time_ns
            self._metadata[self._SLOT_SEQUENCE_0 + index] = sequence
            self._metadata[self._SLOT_TIME_0 + index] = published_time_ns
            self._metadata[
                self._SLOT_SOURCE_GENERATION_0 + index
            ] = int(source_generation)
            self._metadata[self._SLOT_EPOCH_0 + index] = int(epoch)
            return True

    def abort_claimed(self) -> None:
        with self._lock:
            self._metadata[self._WRITING_INDEX] = -1

    def acquire_latest(self, epoch: int) -> Optional[np.ndarray]:
        with self._lock:
            if self._metadata[self._LEASED_INDEX] != -1:
                raise RuntimeError("analysis transport slot is already leased")
            index = self._metadata[self._PUBLISHED_INDEX]
            if index == -1 or int(epoch) != self._metadata[self._ACTIVE_EPOCH]:
                return None
            self._metadata[self._LEASED_INDEX] = index
            self._metadata[self._LAST_ACQUIRED_SEQUENCE] = self._metadata[
                self._SLOT_SEQUENCE_0 + index
            ]
            self._metadata[self._LAST_ACQUIRED_TIME_NS] = self._metadata[
                self._SLOT_TIME_0 + index
            ]
            self._metadata[self._LAST_ACQUIRED_SOURCE_GENERATION] = self._metadata[
                self._SLOT_SOURCE_GENERATION_0 + index
            ]
            self._metadata[self._LAST_ACQUIRED_EPOCH] = self._metadata[
                self._SLOT_EPOCH_0 + index
            ]
            return self._slot_views()[index]

    def release_latest(self) -> None:
        with self._lock:
            if self._metadata[self._LEASED_INDEX] == -1:
                raise RuntimeError("analysis transport has no leased slot")
            self._metadata[self._LEASED_INDEX] = -1

    @property
    def active_epoch(self) -> int:
        return int(self._metadata[self._ACTIVE_EPOCH])

    @property
    def published_sequence(self) -> int:
        return int(self._metadata[self._PUBLISHED_SEQUENCE])

    @property
    def published_time_ns(self) -> int:
        return int(self._metadata[self._PUBLISHED_TIME_NS])

    @property
    def transport_drop_count(self) -> int:
        return int(self._metadata[self._DROP_COUNT])

    @property
    def last_acquired_sequence(self) -> int:
        return int(self._metadata[self._LAST_ACQUIRED_SEQUENCE])

    @property
    def last_acquired_time_ns(self) -> int:
        return int(self._metadata[self._LAST_ACQUIRED_TIME_NS])

    @property
    def last_acquired_source_generation(self) -> int:
        return int(self._metadata[self._LAST_ACQUIRED_SOURCE_GENERATION])

    @property
    def last_acquired_epoch(self) -> int:
        return int(self._metadata[self._LAST_ACQUIRED_EPOCH])


class SharedAnalyzerCounters:
    """Single-writer lock-free counters read only at telemetry boundaries."""

    _SAMPLE_GENERATION = 0
    _LAST_ANALYZED_GENERATION = 1
    _CALLBACK_STATUS_COUNT = 2
    _SKIPPED_GENERATION_COUNT = 3
    _WORKER_FAILURE_COUNT = 4
    _SAMPLE_RATE = 5
    _LENGTH = 6

    def __init__(self, context: Any) -> None:
        self._values = context.RawArray(ctypes.c_longlong, self._LENGTH)

    def set_sample_rate(self, value: int) -> None:
        self._values[self._SAMPLE_RATE] = int(value)

    def update_callback(self, generation: int, status_count: int) -> None:
        self._values[self._SAMPLE_GENERATION] = int(generation)
        self._values[self._CALLBACK_STATUS_COUNT] = int(status_count)

    def update_analysis(
        self,
        last_generation: int,
        skipped_count: int,
    ) -> None:
        self._values[self._LAST_ANALYZED_GENERATION] = int(last_generation)
        self._values[self._SKIPPED_GENERATION_COUNT] = int(skipped_count)

    def update_worker_failure(self, count: int) -> None:
        self._values[self._WORKER_FAILURE_COUNT] = int(count)

    @property
    def sample_generation(self) -> int:
        return int(self._values[self._SAMPLE_GENERATION])

    @property
    def last_analyzed_generation(self) -> int:
        return int(self._values[self._LAST_ANALYZED_GENERATION])

    @property
    def callback_status_count(self) -> int:
        return int(self._values[self._CALLBACK_STATUS_COUNT])

    @property
    def skipped_generation_count(self) -> int:
        return int(self._values[self._SKIPPED_GENERATION_COUNT])

    @property
    def worker_failure_count(self) -> int:
        return int(self._values[self._WORKER_FAILURE_COUNT])

    @property
    def sample_rate(self) -> int:
        return int(self._values[self._SAMPLE_RATE])


@dataclass(frozen=True, slots=True)
class _SharedAudioSettingsView:
    block_size: int


class SharedAnalyzerView:
    """Renderer-side analyzer lease and telemetry surface."""

    def __init__(
        self,
        transport: SharedLatestAnalysisTransport,
        counters: SharedAnalyzerCounters,
        *,
        block_size: int,
    ) -> None:
        self._transport = transport
        self._counters = counters
        self.audio_settings = _SharedAudioSettingsView(int(block_size))

    def get_fft_magnitudes(self) -> Optional[np.ndarray]:
        return self._transport.acquire_latest(self._transport.active_epoch)

    def release_fft_magnitudes(self) -> None:
        self._transport.release_latest()

    @property
    def sample_generation(self) -> int:
        return self._counters.sample_generation

    @property
    def last_analyzed_generation(self) -> int:
        return self._counters.last_analyzed_generation

    @property
    def analysis_sequence(self) -> int:
        return self._transport.published_sequence

    @property
    def callback_status_count(self) -> int:
        return self._counters.callback_status_count

    @property
    def skipped_generation_count(self) -> int:
        return self._counters.skipped_generation_count

    @property
    def worker_failure_count(self) -> int:
        return self._counters.worker_failure_count

    @property
    def transport_drop_count(self) -> int:
        return self._transport.transport_drop_count

    @property
    def sample_rate(self) -> int:
        return self._counters.sample_rate

    @property
    def last_acquired_analysis_sequence(self) -> int:
        return self._transport.last_acquired_sequence

    @property
    def last_acquired_analysis_publish_time_ns(self) -> int:
        return self._transport.last_acquired_time_ns


class StaticAnalyzerResolver:
    """Resolve the one compiled analyzer to the renderer-side shared view."""

    def __init__(self, analyzer: SharedAnalyzerView) -> None:
        self._analyzer = analyzer

    def resolve(self, definition: Any) -> SharedAnalyzerView:
        if getattr(definition, "canonical_slot", None) != 0:
            raise ValueError("matrix-process experiment supports one analyzer slot")
        return self._analyzer


def send_lifecycle_message(connection: Any, message: Mapping[str, Any]) -> None:
    """Send one bounded JSON lifecycle message."""

    payload = json.dumps(
        dict(message),
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(payload) > MAX_LIFECYCLE_MESSAGE_BYTES:
        raise ValueError("matrix-process lifecycle message is too large")
    connection.send_bytes(payload)


def receive_lifecycle_message(connection: Any) -> dict[str, Any]:
    """Receive and validate one bounded JSON lifecycle message."""

    payload = connection.recv_bytes(MAX_LIFECYCLE_MESSAGE_BYTES)
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("matrix-process lifecycle message must be an object")
    return value


def drop_privileges_like_matrix(
    *,
    user: str = "daemon",
    group: str = "daemon",
) -> None:
    """Mirror the native library's default root-to-daemon transition in parent."""

    if os.geteuid() != 0:
        return
    group_entry = grp.getgrnam(group)
    user_entry = pwd.getpwnam(user)
    os.setresgid(group_entry.gr_gid, group_entry.gr_gid, group_entry.gr_gid)
    os.setresuid(user_entry.pw_uid, user_entry.pw_uid, user_entry.pw_uid)


__all__ = [
    "MATRIX_PROCESS_EXPERIMENT_ENVIRONMENT",
    "MATRIX_PROCESS_RESULT_SHAPE",
    "MATRIX_PROCESS_SLOT_COUNT",
    "MAX_LIFECYCLE_MESSAGE_BYTES",
    "SharedAnalyzerCounters",
    "SharedAnalyzerView",
    "SharedLatestAnalysisTransport",
    "StaticAnalyzerResolver",
    "drop_privileges_like_matrix",
    "matrix_process_experiment_enabled",
    "receive_lifecycle_message",
    "send_lifecycle_message",
]
