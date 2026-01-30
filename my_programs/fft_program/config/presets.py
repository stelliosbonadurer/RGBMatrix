"""
Preset configurations for FFT visualizer.

Named presets for different use cases.
"""
from .settings import Settings, AudioSettings, FrequencySettings, ColorSettings, SensitivitySettings


def get_preset(name: str) -> Settings:
    """
    Get a named preset configuration.
    
    Args:
        name: Preset name
    
    Returns:
        Settings configured for that preset
    """
    presets = {
        'default': _default_preset,
        'bluegrass': _bluegrass_preset,
        'edm': _edm_preset,
        'classical': _classical_preset,
        'podcast': _podcast_preset,
    }
    
    if name not in presets:
        available = ', '.join(sorted(presets.keys()))
        raise KeyError(f"Unknown preset '{name}'. Available: {available}")
    
    return presets[name]()


def list_presets() -> list:
    """Get list of available preset names."""
    return ['default', 'bluegrass', 'edm', 'classical', 'podcast']


def _default_preset() -> Settings:
    """Default balanced settings."""
    return Settings()


def _bluegrass_preset() -> Settings:
    """Optimized for bluegrass/acoustic music with warm colors."""
    settings = Settings()
    
    # Frequency range that captures acoustic instruments well
    settings.frequency.zoom_mode = True
    settings.frequency.zoom_min_freq = 80
    settings.frequency.zoom_max_freq = 8000
    
    # Warm color theme
    settings.color.theme = 'warm'
    settings.color.brightness_boost = 1.0
    
    # Balanced sensitivity for acoustic dynamics
    settings.sensitivity.low_freq_weight = 0.6
    settings.sensitivity.high_freq_weight = 8.0
    settings.sensitivity.noise_floor = 0.25
    
    # Punchy but not too reactive
    settings.scaling.headroom_multiplier = 2.5
    settings.scaling.attack_speed = 0.12
    settings.scaling.decay_speed = 0.05
    
    return settings


def _edm_preset() -> Settings:
    """Optimized for electronic dance music with intense visuals."""
    settings = Settings()
    
    # Full range for electronic bass and synths
    settings.frequency.zoom_mode = True
    settings.frequency.zoom_min_freq = 40
    settings.frequency.zoom_max_freq = 12000
    
    # Fire theme for intensity
    settings.color.theme = 'fire'
    settings.color.brightness_boost = 1.2
    
    # Heavy bass emphasis
    settings.sensitivity.low_freq_weight = 0.8
    settings.sensitivity.high_freq_weight = 6.0
    settings.sensitivity.noise_floor = 0.2
    
    # Very punchy and reactive
    settings.scaling.headroom_multiplier = 3.0
    settings.scaling.attack_speed = 0.15
    settings.scaling.decay_speed = 0.08
    
    # Enable overflow for loud drops
    settings.overflow.enabled = True
    settings.overflow.multiplier = 1.8
    
    # Fast smoothing
    settings.smoothing.rise = 0.9
    settings.smoothing.fall = 0.3
    
    return settings


def _classical_preset() -> Settings:
    """Optimized for classical/orchestral music with smooth visuals."""
    settings = Settings()
    
    # Wide range for orchestral instruments
    settings.frequency.zoom_mode = True
    settings.frequency.zoom_min_freq = 60
    settings.frequency.zoom_max_freq = 10000
    
    # Ocean theme for calm elegance
    settings.color.theme = 'ocean'
    settings.color.brightness_boost = 0.9
    
    # Balanced frequency response
    settings.sensitivity.low_freq_weight = 0.7
    settings.sensitivity.high_freq_weight = 5.0
    settings.sensitivity.noise_floor = 0.15
    
    # Slower adaptation for dynamics
    settings.scaling.headroom_multiplier = 2.0
    settings.scaling.attack_speed = 0.08
    settings.scaling.decay_speed = 0.03
    
    # Smooth movement
    settings.smoothing.rise = 0.6
    settings.smoothing.fall = 0.15
    
    # Disable overflow
    settings.overflow.enabled = False
    
    return settings


def _podcast_preset() -> Settings:
    """Optimized for speech/podcast with focus on vocal frequencies."""
    settings = Settings()
    
    # Voice frequency range
    settings.frequency.zoom_mode = True
    settings.frequency.zoom_min_freq = 100
    settings.frequency.zoom_max_freq = 4000
    
    # Simple green theme
    settings.color.theme = 'mono_green'
    settings.color.brightness_boost = 1.0
    
    # Focus on voice frequencies
    settings.sensitivity.low_freq_weight = 0.4
    settings.sensitivity.high_freq_weight = 3.0
    settings.sensitivity.noise_floor = 0.35
    
    # Stable scaling
    settings.scaling.headroom_multiplier = 2.0
    settings.scaling.attack_speed = 0.1
    settings.scaling.decay_speed = 0.04
    
    # Smooth bars
    settings.smoothing.rise = 0.7
    settings.smoothing.fall = 0.2
    
    settings.overflow.enabled = False
    
    return settings
