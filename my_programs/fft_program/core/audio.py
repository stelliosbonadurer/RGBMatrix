"""
Audio processing and FFT computation for FFT visualizer.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # type: ignore
from dataclasses import dataclass
import threading
import time
from typing import Optional
import sounddevice as sd  # type: ignore

from config.settings import AudioSettings, FrequencySettings, SensitivitySettings
from core.analysis_plan import (
    StandardAnalysisCandidate,
    StandardAnalysisPlan,
    compile_standard_analysis_plan,
)


@dataclass
class FFTData:
    """Processed FFT data passed to visualizers."""
    bars: np.ndarray              # Raw bar values after noise floor
    smoothed_bars: np.ndarray     # After smoothing applied
    normalized_bars: np.ndarray   # Normalized 0-1 (or >1 for overflow)
    peak_heights: np.ndarray      # Peak indicator positions (0-1)
    raw_magnitudes: np.ndarray    # Raw FFT magnitudes
    current_scale: float          # Current normalization scale
    sample_rate: int              # Audio sample rate
    num_bins: int                 # Number of frequency bins


class _LatestAnalysisTransport:
    """Fixed two-slot, latest-value transport for standard analysis."""

    def __init__(self, num_bins: int) -> None:
        self.slots = (
            np.zeros(num_bins, dtype=np.float32),
            np.zeros(num_bins, dtype=np.float32),
        )
        self._lock = threading.Lock()
        self._active_epoch = 0
        self._writing_index: Optional[int] = None
        self._published_index: Optional[int] = None
        self._published_epoch = 0
        self._leased_index: Optional[int] = None
        self._slot_sequences = [0, 0]
        self._slot_published_time_ns = [0, 0]
        self.published_sequence = 0
        self.published_time_ns = 0
        self.transport_drop_count = 0
        self.last_acquired_sequence = 0
        self.last_acquired_time_ns = 0

    def activate_epoch(self, epoch: int) -> None:
        """Reject future publications from every earlier worker state."""
        with self._lock:
            self._active_epoch = epoch

    def claim_write(self) -> Optional[np.ndarray]:
        """Claim one safe inactive slot without waiting or allocating."""
        with self._lock:
            if self._writing_index is not None:
                raise RuntimeError("analysis transport already has a claimed write slot")
            for index, slot in enumerate(self.slots):
                if index not in (self._published_index, self._leased_index):
                    self._writing_index = index
                    return slot
            self.transport_drop_count += 1
            return None

    def publish_claimed(self, epoch: int) -> bool:
        """Publish the completely filled slot if its worker epoch is still active."""
        with self._lock:
            index = self._writing_index
            if index is None:
                raise RuntimeError("analysis transport has no claimed write slot")
            self._writing_index = None
            if epoch != self._active_epoch:
                self.transport_drop_count += 1
                return False
            self.published_sequence += 1
            self._published_index = index
            self._published_epoch = epoch
            self.published_time_ns = time.monotonic_ns()
            self._slot_sequences[index] = self.published_sequence
            self._slot_published_time_ns[index] = self.published_time_ns
            return True

    def abort_claimed(self) -> None:
        """Release a producer claim after analysis fails."""
        with self._lock:
            self._writing_index = None

    def acquire_latest(self, epoch: int) -> Optional[np.ndarray]:
        """Lease the newest complete slot while the caller owns the active epoch."""
        with self._lock:
            if self._leased_index is not None:
                raise RuntimeError("analysis transport slot is already leased")
            if (
                self._published_index is None
                or epoch != self._active_epoch
            ):
                return None
            self._leased_index = self._published_index
            self.last_acquired_sequence = self._slot_sequences[self._leased_index]
            self.last_acquired_time_ns = self._slot_published_time_ns[
                self._leased_index
            ]
            return self.slots[self._leased_index]

    def release_latest(self) -> None:
        """Release the consumer lease after scaling finishes reading the slot."""
        with self._lock:
            if self._leased_index is None:
                raise RuntimeError("analysis transport has no leased slot")
            self._leased_index = None


@dataclass(slots=True)
class _ProjectionRecord:
    """Mutable control-plane owner for one fixed projection transport."""

    candidate: StandardAnalysisCandidate
    noise_floor: float
    transport: _LatestAnalysisTransport
    plan: Optional[StandardAnalysisPlan] = None
    epoch: int = 0


@dataclass(frozen=True, slots=True)
class _WorkerProjectionState:
    """One immutable projection snapshot consumed by the worker."""

    token: object
    plan: StandardAnalysisPlan
    noise_floor: float
    transport: _LatestAnalysisTransport
    epoch: int


@dataclass(frozen=True, slots=True)
class _WorkerAnalysisState:
    """One transform plus all projections for a sample generation."""

    fft_size: int
    window: np.ndarray
    projections: tuple[_WorkerProjectionState, ...]

    @property
    def plan(self) -> StandardAnalysisPlan:
        """Compatibility view of the primary projection plan."""
        return self.projections[0].plan

    @property
    def noise_floor(self) -> float:
        """Compatibility view of the primary projection noise floor."""
        return self.projections[0].noise_floor

    @property
    def epoch(self) -> int:
        """Compatibility view of the primary projection epoch."""
        return self.projections[0].epoch


class AudioProjection:
    """A lightweight 64-bin view backed by one shared capture/FFT owner."""

    def __init__(self, owner: "AudioProcessor", token: object) -> None:
        self._owner = owner
        self._token = token

    def setup(self) -> int:
        return self._owner.sample_rate

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def get_fft_magnitudes(self) -> Optional[np.ndarray]:
        self._owner._raise_worker_failure()
        record = self._owner._projection_record(self._token)
        if record.plan is None:
            raise RuntimeError("Call setup() before get_fft_magnitudes()")
        return record.transport.acquire_latest(record.epoch)

    def release_fft_magnitudes(self) -> None:
        self._owner._projection_record(self._token).transport.release_latest()

    @property
    def analysis_sequence(self) -> int:
        return self._owner._projection_record(
            self._token
        ).transport.published_sequence

    @property
    def transport_drop_count(self) -> int:
        return self._owner._projection_record(
            self._token
        ).transport.transport_drop_count

    @property
    def latest_analysis_publish_time_ns(self) -> int:
        return self._owner._projection_record(
            self._token
        ).transport.published_time_ns

    @property
    def last_acquired_analysis_sequence(self) -> int:
        return self._owner._projection_record(
            self._token
        ).transport.last_acquired_sequence

    @property
    def last_acquired_analysis_publish_time_ns(self) -> int:
        return self._owner._projection_record(
            self._token
        ).transport.last_acquired_time_ns


class AudioProcessor:
    """
    Handles audio capture and FFT processing.
    
    Responsible for:
    - Audio input stream management
    - FFT computation
    - Frequency binning with logarithmic distribution
    - Noise floor subtraction
    """
    
    def __init__(
        self,
        audio_settings: AudioSettings,
        freq_settings: FrequencySettings,
        sensitivity_settings: SensitivitySettings,
        num_bins: int
    ):
        """
        Initialize audio processor.
        
        Args:
            audio_settings: Audio device and buffer configuration
            freq_settings: Frequency range configuration
            sensitivity_settings: Noise floor and frequency weighting
            num_bins: Number of frequency bins (typically matrix width)
        """
        self.audio_settings = audio_settings
        self.freq_settings = freq_settings
        self.sensitivity_settings = sensitivity_settings
        self.num_bins = num_bins
        
        # Audio state
        self.latest_samples: Optional[np.ndarray] = None
        self.have_data: bool = False
        self.sample_rate: int = 0
        self.sample_generation: int = 0
        self.callback_status_count: int = 0
        self.last_analyzed_generation: int = 0
        self.skipped_generation_count: int = 0
        self.worker_failure_count: int = 0
        self._last_seen_generation: int = 0
        self._sample_lock = threading.Lock()
        self._analysis_wake = threading.Event()
        self._analysis_stop = threading.Event()
        self._analysis_thread: Optional[threading.Thread] = None
        self._worker_failure: Optional[BaseException] = None
        self._worker_state: Optional[_WorkerAnalysisState] = None
        
        # The standard projection is validated now and compiled after setup learns
        # the device sample rate.  The hot path reads only ``active_plan``.
        self.analysis_candidate = StandardAnalysisCandidate.from_settings(
            audio_settings,
            freq_settings,
            sensitivity_settings,
            num_bins,
        )
        self.active_plan: Optional[StandardAnalysisPlan] = None
        self._primary_projection_token = object()
        self._projection_records: dict[object, _ProjectionRecord] = {
            self._primary_projection_token: _ProjectionRecord(
                candidate=self.analysis_candidate,
                noise_floor=float(sensitivity_settings.noise_floor),
                transport=_LatestAnalysisTransport(num_bins),
            )
        }
        # Kept as a compatibility alias for focused diagnostics and telemetry.
        self._analysis_transport = self._projection_records[
            self._primary_projection_token
        ].transport

        # Stream (initialized in start())
        self._stream: Optional[sd.InputStream] = None

    @staticmethod
    def _transform_key(candidate: StandardAnalysisCandidate) -> tuple[int, int]:
        """Identify FFT math independently from projection-only settings."""
        return (candidate.block_size, candidate.fft_size)

    def _projection_record(self, token: object) -> _ProjectionRecord:
        try:
            return self._projection_records[token]
        except KeyError as exc:
            raise RuntimeError("projection is no longer active") from exc

    def create_projection(
        self,
        candidate: StandardAnalysisCandidate,
        noise_floor: float,
    ) -> AudioProjection:
        """Create one projection without opening another capture stream."""
        if self._transform_key(candidate) != self._transform_key(
            self.analysis_candidate
        ):
            raise ValueError(
                "separate FFT transforms are not active yet; projection must share "
                "block_size and fft_size"
            )
        token = object()
        record = _ProjectionRecord(
            candidate=candidate,
            noise_floor=float(noise_floor),
            transport=_LatestAnalysisTransport(candidate.num_bins),
        )
        self._projection_records[token] = record
        try:
            if self.sample_rate > 0:
                record.plan = self.compile_analysis_candidate(candidate)
                self._publish_worker_state({token})
        except BaseException:
            self._projection_records.pop(token, None)
            raise
        return AudioProjection(self, token)

    def remove_projection(self, projection: AudioProjection) -> None:
        """Retire a view after no active layer plan can reference it."""
        if not isinstance(projection, AudioProjection) or projection._owner is not self:
            raise ValueError("projection is not owned by this audio processor")
        if projection._token == self._primary_projection_token:
            raise ValueError("the primary projection cannot be removed")
        record = self._projection_records.pop(projection._token, None)
        if record is None:
            return
        record.transport.activate_epoch(record.epoch + 1)
        if self.sample_rate > 0:
            self._publish_worker_state(set())
    
    def setup(self) -> int:
        """
        Initialize FFT parameters based on audio device.
        
        Returns:
            Sample rate of the audio device
        
        Raises:
            RuntimeError: If audio device not found
        """
        # Query device to get native sample rate
        try:
            device_info = sd.query_devices(self.audio_settings.device, 'input')
        except ValueError as e:
            raise RuntimeError(f"Audio device '{self.audio_settings.device}' not found: {e}")
        
        detected_sample_rate = int(device_info['default_samplerate'])
        resolved_sample_rate = self.audio_settings.resolved_sample_rate
        if (
            resolved_sample_rate is not None
            and detected_sample_rate != resolved_sample_rate
        ):
            raise RuntimeError(
                "Audio device sample rate changed after source resolution: "
                f"{resolved_sample_rate} -> {detected_sample_rate}"
            )
        self.sample_rate = detected_sample_rate
        print(f"Using device: {device_info['name']} at {self.sample_rate} Hz")
        
        # Report frequency range
        candidate = self.analysis_candidate
        freq_min = candidate.min_freq
        freq_max = candidate.max_freq
        mode = "ZOOM MODE" if self.freq_settings.zoom_mode else "Full range"
        print(f"{mode}: {freq_min} Hz - {freq_max} Hz")
        
        # Initialize FFT parameters
        self.latest_samples = np.zeros(self.audio_settings.block_size, dtype=np.float32)
        plan = self.compile_analysis_candidate(candidate)
        primary = self._projection_record(self._primary_projection_token)
        primary.candidate = candidate
        primary.noise_floor = float(self.sensitivity_settings.noise_floor)
        primary.plan = plan
        self.active_plan = plan
        changed_tokens = {self._primary_projection_token}
        for token, record in self._projection_records.items():
            if token == self._primary_projection_token:
                continue
            record.plan = self.compile_analysis_candidate(record.candidate)
            changed_tokens.add(token)
        self._publish_worker_state(changed_tokens)
        
        # Check bin coverage
        empty_count = sum(len(indices) == 0 for indices in plan.bin_indices)
        if empty_count > 0:
            print(f"Warning: {empty_count} bins have no frequency coverage. "
                  f"Consider increasing FFT_SIZE.")
        
        return self.sample_rate
    
    def compile_analysis_candidate(
        self,
        candidate: StandardAnalysisCandidate,
    ) -> StandardAnalysisPlan:
        """Compile a candidate without changing the active runtime plan."""
        if self.sample_rate <= 0:
            raise RuntimeError("Call setup() before compiling an analysis candidate")
        return compile_standard_analysis_plan(candidate, self.sample_rate)

    def _publish_worker_state(self, changed_tokens: set[object]) -> None:
        """Atomically expose one transform and every compiled projection."""
        projections: list[_WorkerProjectionState] = []
        transform_key: Optional[tuple[int, int]] = None
        window: Optional[np.ndarray] = None
        fft_size = 0
        for token, record in self._projection_records.items():
            plan = record.plan
            if plan is None:
                continue
            candidate_key = self._transform_key(record.candidate)
            if transform_key is None:
                transform_key = candidate_key
                window = plan.window
                fft_size = plan.fft_size
            elif candidate_key != transform_key:
                raise RuntimeError("worker projections do not share one FFT transform")
            if token in changed_tokens:
                record.epoch += 1
                record.transport.activate_epoch(record.epoch)
            projections.append(
                _WorkerProjectionState(
                    token=token,
                    plan=plan,
                    noise_floor=record.noise_floor,
                    transport=record.transport,
                    epoch=record.epoch,
                )
            )
        if not projections or window is None:
            self._worker_state = None
            return
        self._worker_state = _WorkerAnalysisState(
            fft_size=fft_size,
            window=window,
            projections=tuple(projections),
        )
        self._analysis_wake.set()
    
    @staticmethod
    def _analyze_into(
        samples: np.ndarray,
        plan: StandardAnalysisPlan,
        noise_floor: float,
        destination: np.ndarray,
    ) -> None:
        """Run the accepted standard FFT/projection directly into one fixed slot."""
        x = samples * plan.window
        X = np.fft.rfft(x, n=plan.fft_size)
        magnitudes = np.abs(X)

        AudioProcessor._project_into(
            magnitudes,
            plan,
            noise_floor,
            destination,
        )

    @staticmethod
    def _project_into(
        magnitudes: np.ndarray,
        plan: StandardAnalysisPlan,
        noise_floor: float,
        destination: np.ndarray,
    ) -> None:
        """Map one shared full spectrum into one configured 64-bin view."""

        destination.fill(0)
        for index, bin_indices in enumerate(plan.bin_indices):
            if len(bin_indices) > 0:
                destination[index] = (
                    np.mean(magnitudes[bin_indices]) * plan.bin_weights[index]
                )
        np.subtract(destination, noise_floor, out=destination)
        np.maximum(destination, 0, out=destination)

    def _process_latest_samples_once(self) -> bool:
        """Analyze at most one newest sample generation without waiting."""
        with self._sample_lock:
            samples = self.latest_samples
            generation = self.sample_generation

        state = self._worker_state
        if samples is None or state is None or generation <= self._last_seen_generation:
            return False

        self.skipped_generation_count += max(
            0,
            generation - self._last_seen_generation - 1,
        )
        self._last_seen_generation = generation

        claimed: list[tuple[_WorkerProjectionState, np.ndarray]] = []
        for projection in state.projections:
            destination = projection.transport.claim_write()
            if destination is None:
                continue
            claimed.append((projection, destination))
        if not claimed:
            self.skipped_generation_count += 1
            return False

        try:
            if len(claimed) == 1:
                projection, destination = claimed[0]
                self._analyze_into(
                    samples,
                    projection.plan,
                    projection.noise_floor,
                    destination,
                )
            else:
                x = samples * state.window
                spectrum = np.abs(np.fft.rfft(x, n=state.fft_size))
                for projection, destination in claimed:
                    self._project_into(
                        spectrum,
                        projection.plan,
                        projection.noise_floor,
                        destination,
                    )
        except BaseException:
            for projection, _destination in claimed:
                projection.transport.abort_claimed()
            raise

        self.last_analyzed_generation = generation
        published = False
        for projection, _destination in claimed:
            published = projection.transport.publish_claimed(
                projection.epoch
            ) or published
        return published

    def _analysis_worker(self) -> None:
        """Consume only the newest callback generation and publish latest results."""
        try:
            while not self._analysis_stop.is_set():
                self._analysis_wake.wait()
                self._analysis_wake.clear()
                if self._analysis_stop.is_set():
                    break
                self._process_latest_samples_once()
        except BaseException as exc:
            self.worker_failure_count += 1
            self._worker_failure = exc
            self._analysis_stop.set()

    def _start_analysis_worker(self) -> None:
        if self._analysis_thread is not None:
            raise RuntimeError("analysis worker is already running")
        self._analysis_stop.clear()
        self._analysis_wake.clear()
        self._worker_failure = None
        worker = threading.Thread(
            target=self._analysis_worker,
            name="fft-analysis",
            daemon=True,
        )
        self._analysis_thread = worker
        try:
            worker.start()
        except BaseException:
            self._analysis_thread = None
            raise

    def _stop_analysis_worker(self, timeout: float = 1.0) -> None:
        worker = self._analysis_thread
        if worker is None:
            return
        self._analysis_stop.set()
        self._analysis_wake.set()
        worker.join(timeout=timeout)
        if worker.is_alive():
            raise RuntimeError("analysis worker did not stop within 1.0 seconds")
        self._analysis_thread = None

    def _raise_worker_failure(self) -> None:
        failure = self._worker_failure
        if failure is not None:
            raise RuntimeError("analysis worker failed") from failure

    def _audio_callback(self, indata, frames, time_info, status):
        """Copy and publish only the newest input block, then wake analysis."""
        samples = indata[:, self.audio_settings.channel].copy()
        with self._sample_lock:
            self.latest_samples = samples
            self.sample_generation += 1
            if status:
                self.callback_status_count += 1
            self.have_data = True
        self._analysis_wake.set()
    
    def start(self) -> None:
        """Start the audio input stream."""
        self._start_analysis_worker()
        try:
            self._stream = sd.InputStream(
                device=self.audio_settings.device,
                channels=self.audio_settings.capture_channels,
                samplerate=self.sample_rate,
                blocksize=self.audio_settings.block_size,
                dtype=self.audio_settings.capture_dtype,
                callback=self._audio_callback
            )
            self._stream.start()
        except BaseException:
            stream = self._stream
            self._stream = None
            if stream is not None:
                try:
                    stream.close()
                finally:
                    self._stop_analysis_worker()
            else:
                self._stop_analysis_worker()
            raise
        print("Listening... Press CTRL-C to stop")
    
    def stop(self) -> None:
        """Stop the audio input stream."""
        stream = self._stream
        self._stream = None
        stream_failure = None
        if stream is not None:
            try:
                stream.stop()
            except BaseException as exc:
                stream_failure = exc
            finally:
                try:
                    stream.close()
                except BaseException as exc:
                    if stream_failure is None:
                        stream_failure = exc
        self._stop_analysis_worker()
        if stream_failure is not None:
            raise stream_failure
    
    def get_fft_magnitudes(self) -> Optional[np.ndarray]:
        """Lease the latest complete standard result for direct NumPy access."""
        self._raise_worker_failure()
        record = self._projection_record(self._primary_projection_token)
        if record.plan is None:
            raise RuntimeError("Call setup() before get_fft_magnitudes()")
        return record.transport.acquire_latest(record.epoch)

    def release_fft_magnitudes(self) -> None:
        """Release the standard result after render-thread scaling consumes it."""
        self._analysis_transport.release_latest()

    @property
    def analysis_sequence(self) -> int:
        """Number of complete standard frames published by the worker."""
        return self._analysis_transport.published_sequence

    @property
    def transport_drop_count(self) -> int:
        """Number of stale-epoch or no-writable-slot results dropped."""
        return sum(
            record.transport.transport_drop_count
            for record in self._projection_records.values()
        )

    @property
    def latest_analysis_publish_time_ns(self) -> int:
        """Monotonic publication time for opt-in result-age measurement."""
        return self._analysis_transport.published_time_ns

    @property
    def last_acquired_analysis_sequence(self) -> int:
        """Sequence of the exact result slot most recently leased by rendering."""
        return self._analysis_transport.last_acquired_sequence

    @property
    def last_acquired_analysis_publish_time_ns(self) -> int:
        """Publication time of the exact result slot most recently leased."""
        return self._analysis_transport.last_acquired_time_ns
    
    def __enter__(self):
        """Context manager entry."""
        self.setup()
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()
        return False
