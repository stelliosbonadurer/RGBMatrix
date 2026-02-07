"""
Configuration settings for FFT visualizer.

All user-configurable parameters are defined here as dataclasses.
"""
from dataclasses import dataclass, field
from typing import Tuple, Literal


@dataclass
class AudioSettings:
    """Audio input configuration."""
    #device: int = 0  # USB PnP Sound Device (sounddevice index 0)
    device: str = 'iMM-6C'  # Use device name instead of index
    block_size: int = 512   # Actual audio samples per callback (~23ms at 44kHz)
    fft_size: int = 8192    # Zero-pad to this size for better freq resolution
    channel: int = 0
    sleep_delay: float = 0.005  # Delay between frames (lower = smoother but more CPU)


@dataclass
class FrequencySettings:
    """Frequency range configuration."""
    min_freq: int = 60       # Lower bound for full range
    max_freq: int = 14000    # Upper bound for full range
    zoom_mode: bool = True   # True = use zoom frequencies
    zoom_min_freq: int = 100  # Zoom mode lower bound
    zoom_max_freq: int = 6300 # Zoom mode upper bound
    
    @property
    def active_min_freq(self) -> int:
        """Get the currently active minimum frequency."""
        return self.zoom_min_freq if self.zoom_mode else self.min_freq
    
    @property
    def active_max_freq(self) -> int:
        """Get the currently active maximum frequency."""
        return self.zoom_max_freq if self.zoom_mode else self.max_freq


@dataclass
class OverflowSettings:
    """Overflow mode configuration for bars that exceed display height."""
    enabled: bool = True       # True = bars wrap/stack when exceeding height
    multiplier: float = 1.5    # Sensitivity for overflow
    color_2: Tuple[int, int, int] = (0, 50, 255)    # 3rd layer color (cyan)
    color_3: Tuple[int, int, int] = (255, 255, 255)    # 4th+ layer color (magenta)


@dataclass
class PeakSettings:
    """Peak indicator configuration."""
    enabled: bool = False      # True = show floating peak dots above bars
    fall_speed: float = 0.08   # How fast peaks fall (0.01 = slow, 0.2 = fast)
    hold_frames: int = 8       # Frames to hold peak before falling
    color_mode: Literal['white', 'bar', 'contrast', 'peak'] = 'contrast'


@dataclass
class ColorSettings:
    """Color theme configuration."""
    # Available themes: 'warm', 'ocean', 'forest', 'rainbow'
    theme: str = 'ocean'
    brightness_boost: float = 1.0  # Overall brightness multiplier


@dataclass
class SensitivitySettings:
    """Audio sensitivity configuration."""
    noise_floor: float = 0.3      # Subtract from signal (0 = sensitive, 0.5 = cut noise)
    low_freq_weight: float = 0.55  # Multiplier for bass frequencies
    high_freq_weight: float = 10.0 # Multiplier for treble frequencies
    silence_threshold: float = 0.0 # Below this peak = fade to black


@dataclass
class ScalingSettings:
    """Audio scaling/normalization configuration."""
    # Only ONE of these should be True, or all False for instant auto-normalize
    use_fixed_scale: bool = False      # Fixed sensitivity
    use_rolling_rms_scale: bool = True # Scale to average energy + headroom (RECOMMENDED)
    use_rolling_max_scale: bool = False # Scale to max in window
    
    fixed_scale_max: float = 0.3       # For fixed scale: divide signal by this
    rolling_window_seconds: float = 3  # Seconds of history for RMS/max calculation
    headroom_multiplier: float = 2.5   # RMS multiplier for headroom
    attack_speed: float = 0.1          # How fast scale adapts to LOUDER audio
    decay_speed: float = 0.06          # How fast scale adapts to QUIETER audio
    min_scale: float = 0.05            # Minimum scale to prevent infinite gain
    sensitivity_scalar: float = 1.0    # Manual sensitivity boost on top of auto-scaling


@dataclass
class SmoothingSettings:
    """Bar smoothing configuration."""
    rise: float = 0.4   # How fast bars rise (0.3 = smooth/slow, 1.0 = instant)
    fall: float = 0.1  # How fast bars fall (0.2 = slow decay, 0.8 = fast drop)
    
    #rise: float = 0.55   # How fast bars rise (0.3 = smooth/slow, 1.0 = instant)
    #fall: float = 0.125  # How fast bars fall (0.2 = slow decay, 0.8 = fast drop)


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
    from dataclasses import asdict
    
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
            settings.sensitivity = SensitivitySettings(**data['sensitivity'])
        if 'scaling' in data:
            settings.scaling = ScalingSettings(**data['scaling'])
        if 'smoothing' in data:
            settings.smoothing = SmoothingSettings(**data['smoothing'])
        
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
