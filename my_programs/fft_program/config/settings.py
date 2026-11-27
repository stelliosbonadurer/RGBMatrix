"""
Configuration settings for FFT visualizer.

All user-configurable parameters are defined here as dataclasses.
"""
from dataclasses import dataclass, field
from typing import Tuple, Literal

from config.weighting import (
    DEFAULT_HIGH_FREQ_WEIGHT,
    DEFAULT_LOW_FREQ_WEIGHT,
    legacy_weight_points,
    weight_points_document,
)


@dataclass
class AudioSettings:
    """Audio input configuration."""
    #device: int = 0  # USB PnP Sound Device (sounddevice index 0)
    device: str = 'iMM-6C'  # Use device name instead of index
    block_size: int = 512   # Actual audio samples per callback (~23ms at 44kHz)
    fft_size: int = 8192    # Zero-pad to this size for better freq resolution
    channel: int = 0
    sleep_delay: float = 0.005  # Delay between frames (lower = smoother but more CPU)
    # Populated by the startup resolver before matrix/analyzer construction. These
    # defaults preserve direct construction in hardware-free tests and utilities.
    source_id: str = 'usb-mic'
    resolved_device: str | None = None
    resolved_sample_rate: int | None = None
    capture_channels: int = 1
    capture_dtype: str = 'float32'


@dataclass
class FrequencySettings:
    """Frequency range configuration."""
    min_freq: int = 60       # Lower bound for full range
    max_freq: int = 14000    # Upper bound for full range
    zoom_mode: bool = True   # True = use zoom frequencies
    
    # Zoom presets: list of (min, max) tuples - cycle with r/R
    zoom_presets: list = field(default_factory=lambda: [
        (100, 6300),   # Wide range
        (80, 1950),    # Bass/mid focus
        (60,14000),    # Full Range
    ])
    zoom_preset_index: int = 0  # Current preset index
    
    @property
    def zoom_min_freq(self) -> int:
        """Get current zoom minimum frequency from preset."""
        return self.zoom_presets[self.zoom_preset_index][0]
    
    @property
    def zoom_max_freq(self) -> int:
        """Get current zoom maximum frequency from preset."""
        return self.zoom_presets[self.zoom_preset_index][1]
    
    @property
    def active_min_freq(self) -> int:
        """Get the currently active minimum frequency."""
        return self.zoom_min_freq if self.zoom_mode else self.min_freq
    
    @property
    def active_max_freq(self) -> int:
        """Get the currently active maximum frequency."""
        return self.zoom_max_freq if self.zoom_mode else self.max_freq
    
    def next_zoom_preset(self) -> Tuple[int, int]:
        """Cycle to next zoom preset. Returns (min, max) tuple."""
        self.zoom_preset_index = (self.zoom_preset_index + 1) % len(self.zoom_presets)
        return self.zoom_presets[self.zoom_preset_index]
    
    def prev_zoom_preset(self) -> Tuple[int, int]:
        """Cycle to previous zoom preset. Returns (min, max) tuple."""
        self.zoom_preset_index = (self.zoom_preset_index - 1) % len(self.zoom_presets)
        return self.zoom_presets[self.zoom_preset_index]


@dataclass
class OverflowSettings:
    """Overflow mode configuration for bars that exceed display height."""
    enabled: bool = True       # True = bars wrap/stack when exceeding height
    color_2: Tuple[int, int, int] = (0, 50, 255)    # 3rd layer color (cyan)
    color_3: Tuple[int, int, int] = (255, 255, 255)    # 4th+ layer color (magenta)


@dataclass
class PeakSettings:
    """Peak indicator configuration."""
    enabled: bool = False      # True = show floating peak dots above bars
    hold_seconds: float = 0.09 # Seconds a peak holds before falling
    fall_rate: float = 7.0     # Fall speed after the hold, in panel heights per second
    color_mode: Literal['white', 'bar', 'contrast', 'peak'] = 'contrast'


@dataclass
class ColorSettings:
    """Color theme configuration."""
    # Available themes: themes.list_themes() (see docs/themes.md)
    theme: str = 'ocean'
    brightness_boost: float = 1.0  # Overall brightness multiplier


@dataclass
class SensitivitySettings:
    """Audio sensitivity configuration."""
    # Subtracted from each bar before weighting, in dBFS (a full-scale sine is 0).
    # Lower is more sensitive; raise it if bars move with no audio. -65 was kept after
    # the fft-level-controls M5 in-room check (D-033, D-048), not measured.
    noise_floor_dbfs: float = -65.0
    # How the FFT bins in one bar combine: 'mean', 'band_energy' (sqrt of the summed
    # squares), or 'max'. Each reads a full-scale sine as 1.0 (fft-level-controls D-012).
    aggregation: str = 'mean'
    # Frequency weighting: {"position": 0-1 along the log range, "weight": 0-100}.
    # Linear between points, flat beyond the ends. The default approximates the
    # retired low_freq_weight=0.45 / high_freq_weight=10.0 curve.
    weight_points: list = field(default_factory=legacy_weight_points)


_LEGACY_WEIGHT_KEYS = ("low_freq_weight", "high_freq_weight")
# Mirrors core.analysis_plan.AGGREGATIONS, which config can't import.
_AGGREGATIONS = ("mean", "band_energy", "max")


def _sensitivity_from_json(data: dict) -> SensitivitySettings:
    """Build sensitivity settings, converting retired keys (D-022)."""
    data = dict(data)
    if "noise_floor" in data:
        # The old floor was in raw units after weighting; no exact conversion (D-030).
        raise ValueError(
            "sensitivity.noise_floor is retired; set sensitivity.noise_floor_dbfs "
            "(dBFS, -120 to -20, subtracted before weighting) instead"
        )
    if "silence_threshold" in data:
        data.pop("silence_threshold")
        print("Settings note: ignored retired sensitivity.silence_threshold")
    legacy = {name: data.pop(name) for name in _LEGACY_WEIGHT_KEYS if name in data}
    if "weight_points" in data:
        if legacy:
            print(
                "Settings note: sensitivity.weight_points is set, so the retired "
                f"{', '.join(sorted(legacy))} key(s) were ignored"
            )
        data["weight_points"] = weight_points_document(data["weight_points"])
    elif legacy:
        data["weight_points"] = legacy_weight_points(
            legacy.get("low_freq_weight", DEFAULT_LOW_FREQ_WEIGHT),
            legacy.get("high_freq_weight", DEFAULT_HIGH_FREQ_WEIGHT),
        )
        print(
            "Settings note: converted retired "
            f"{', '.join(sorted(legacy))} to sensitivity.weight_points"
        )
    if "aggregation" in data and data["aggregation"] not in _AGGREGATIONS:
        raise ValueError(
            "sensitivity.aggregation must be one of " + ", ".join(_AGGREGATIONS)
        )
    return SensitivitySettings(**data)


@dataclass
class ScalingSettings:
    """Per-layer level: auto level and gain (fft-level-controls M2)."""
    auto_level: bool = True            # Off: divide by a fixed reference instead
    headroom: float = 2.5              # Scale = level x headroom; higher = shorter bars
    attack_seconds: float = 5.5        # Time constant for adapting to LOUDER audio
    release_seconds: float = 2.75      # Time constant for adapting to QUIETER audio
    gain: float = 1.5                  # Multiplies bar height after scaling (2 = twice as tall)
    max_boost_db: float = 68.0         # Most auto level may raise quiet input, in dB re full scale
    response_curve: float = 1.0        # Height = ratio ** curve after gain; < 1 lifts quiet bars


@dataclass
class SmoothingSettings:
    """Bar smoothing configuration, as time constants (0 = instant)."""
    rise_seconds: float = 0.023  # Seconds to rise toward a louder value
    fall_seconds: float = 0.11   # Seconds to fall toward a quieter value
    # Column spread, before rise/fall: each bar lifts neighbors within spread_radius
    # columns to its height x spread_falloff per column of distance (0 = off).
    spread_radius: int = 0
    spread_falloff: float = 0.5


@dataclass
class ShadowSettings:
    """Shadow/trail effect configuration."""
    enabled: bool = False          # True = pixels fade out instead of instant off
    decay_amount: float = 0.4      # Subtract this from shadow each decay step
    decay_interval: int = 3        # Decay every N frames (1 = every frame, 5 = every 5th)


@dataclass
class Settings:
    """Master settings container."""
    audio: AudioSettings = field(default_factory=AudioSettings)
    frequency: FrequencySettings = field(default_factory=FrequencySettings)
    overflow: OverflowSettings = field(default_factory=OverflowSettings)
    peak: PeakSettings = field(default_factory=PeakSettings)
    color: ColorSettings = field(default_factory=ColorSettings)
    sensitivity: SensitivitySettings = field(default_factory=SensitivitySettings)
    scaling: ScalingSettings = field(default_factory=ScalingSettings)
    smoothing: SmoothingSettings = field(default_factory=SmoothingSettings)
    shadow: ShadowSettings = field(default_factory=ShadowSettings)


def get_default_settings() -> Settings:
    """Return default settings configuration."""
    return Settings()


def load_settings(path: str = None) -> Settings:
    """
    Load settings from a JSON file.
    
    Args:
        path: Path to JSON settings file. If None, returns defaults.
    
    Returns:
        Settings object with loaded or default values.
    """
    if path is None:
        return get_default_settings()
    
    import json
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        
        # Build settings from JSON
        settings = Settings()
        
        if 'audio' in data:
            settings.audio = AudioSettings(**data['audio'])
        if 'frequency' in data:
            settings.frequency = FrequencySettings(**data['frequency'])
        if 'overflow' in data:
            # Handle tuple conversion for colors
            if 'color_2' in data['overflow']:
                data['overflow']['color_2'] = tuple(data['overflow']['color_2'])
            if 'color_3' in data['overflow']:
                data['overflow']['color_3'] = tuple(data['overflow']['color_3'])
            settings.overflow = OverflowSettings(**data['overflow'])
        if 'peak' in data:
            settings.peak = PeakSettings(**data['peak'])
        if 'color' in data:
            settings.color = ColorSettings(**data['color'])
        if 'sensitivity' in data:
            settings.sensitivity = _sensitivity_from_json(data['sensitivity'])
        if 'scaling' in data:
            settings.scaling = ScalingSettings(**data['scaling'])
        if 'smoothing' in data:
            settings.smoothing = SmoothingSettings(**data['smoothing'])
        if 'shadow' in data:
            settings.shadow = ShadowSettings(**data['shadow'])

        return settings
    
    except FileNotFoundError:
        print(f"Settings file not found: {path}, using defaults")
        return get_default_settings()
    except json.JSONDecodeError as e:
        print(f"Error parsing settings file: {e}, using defaults")
        return get_default_settings()


def save_settings(settings: Settings, path: str) -> None:
    """
    Save settings to a JSON file.
    
    Args:
        settings: Settings object to save.
        path: Path to save JSON file.
    """
    import json
    from dataclasses import asdict
    
    data = asdict(settings)
    
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
