"""
Unified bar graph visualizer for FFT display.

Combines standard and overflow modes with gradient toggle.
Supports multi-layer visualization with independent settings per layer.

Press 'g' to toggle gradient mode, 'o' to toggle overflow mode, 'f' for full mode, 'd' for debug mode.
Press '1'/'2' to select layer for editing, Shift+1/2 to toggle layer visibility.
"""
from typing import Optional, Tuple, List, Dict, Any
from dataclasses import dataclass
import numpy as np  # type: ignore

from .base import BaseVisualizer
from themes import get_theme, list_themes


@dataclass
class LayerState:
    """Runtime state for a single layer."""
    theme: Any = None           # Theme instance
    theme_name: str = 'ocean'   # Theme name
    bars_enabled: bool = True
    gradient_enabled: bool = False
    overflow_enabled: bool = True
    peak_enabled: bool = False
    visible: bool = True
    boost: float = 1.0


class BarsUnifiedVisualizer(BaseVisualizer):
    """
    Unified bar graph visualization with configurable modes and multi-layer support.
    
    Modes:
    - Gradient OFF (uniform): One color per bar based on total height
    - Gradient ON: Per-pixel gradient within each bar
    - Overflow OFF: Bars clamped to display height
    - Overflow ON: Bars can exceed height with layered colors
    - Full: Full screen with gradient scaled by FFT data
    - Debug: Full screen showing static color gradient for each column
    - Layered: Multiple independent frequency layers with their own settings
    """
    
    name = "bars"
    description = "Vertical bars (g=gradient, o=overflow, f=full, d=debug, 1/2=layer select, Shift+1/2=toggle)"
    
    def __init__(self, width: int, height: int, settings):
        super().__init__(width, height, settings)
        
        # Single-layer mode defaults (used when layers not enabled)
        self.gradient_mode = getattr(settings, 'gradient_enabled', False)
        self.overflow_mode = settings.overflow.enabled
        self.bars_enabled = True  # Can be toggled to show only peaks
        self.full_mode = False    # Full mode: entire screen lit, gradient scaled by FFT
        self.debug_mode = False   # Debug mode shows full screen gradient
        
        # Multi-layer mode state
        self.layers_enabled = False
        self.active_layer = 0           # Which layer is being edited (0-indexed)
        self.layer_states: List[LayerState] = []  # Per-layer state
        self.layer_scalers: List[Any] = []        # Per-layer ScalingProcessor (set from main.py)
        self.draw_order: List[int] = []           # Order to draw layers (indices into layer_states)
    
    def toggle_gradient(self) -> bool:
        """Toggle gradient mode for the active layer. Returns new state."""
        if self.layers_enabled and self.layer_states:
            self.layer_states[self.active_layer].gradient_enabled = not self.layer_states[self.active_layer].gradient_enabled
            return self.layer_states[self.active_layer].gradient_enabled
        else:
            self.gradient_mode = not self.gradient_mode
            return self.gradient_mode
    
    def toggle_overflow(self) -> bool:
        """Toggle overflow mode for the active layer. Returns new state."""
        if self.layers_enabled and self.layer_states:
            self.layer_states[self.active_layer].overflow_enabled = not self.layer_states[self.active_layer].overflow_enabled
            return self.layer_states[self.active_layer].overflow_enabled
        else:
            self.overflow_mode = not self.overflow_mode
            return self.overflow_mode
    
    def toggle_peak(self) -> bool:
        """Toggle peak mode for the active layer. Returns new state."""
        if self.layers_enabled and self.layer_states:
            self.layer_states[self.active_layer].peak_enabled = not self.layer_states[self.active_layer].peak_enabled
            return self.layer_states[self.active_layer].peak_enabled
        else:
            # For non-layered mode, return False (handled by global settings.peak.enabled)
            return False
    
    def toggle_bars(self) -> bool:
        """Toggle bar drawing for the active layer. Returns new state."""
        if self.layers_enabled and self.layer_states:
            self.layer_states[self.active_layer].bars_enabled = not self.layer_states[self.active_layer].bars_enabled
            return self.layer_states[self.active_layer].bars_enabled
        else:
            self.bars_enabled = not self.bars_enabled
            return self.bars_enabled
    
    def toggle_full(self) -> bool:
        """Toggle full mode. Returns new state."""
        self.full_mode = not self.full_mode
        return self.full_mode
    
    def toggle_debug(self) -> bool:
        """Toggle debug mode. Returns new state."""
        self.debug_mode = not self.debug_mode
        return self.debug_mode
    
    # =========================================================================
    # Layer Management
    # =========================================================================
    
    def setup_layers(self, layer_configs: List, brightness_boost: float = 1.0) -> None:
        """
        Initialize layer states from layer configurations.
        
        Args:
            layer_configs: List of LayerConfig from settings
            brightness_boost: Brightness multiplier for themes
        """
        self.layer_states = []
        for config in layer_configs:
            state = LayerState(
                theme=get_theme(config.theme_name, brightness_boost=brightness_boost),
                theme_name=config.theme_name,
                bars_enabled=config.bars_enabled,
                gradient_enabled=config.gradient_enabled,
                overflow_enabled=config.overflow_enabled,
                peak_enabled=config.peak_enabled,
                visible=config.visible,
                boost=config.boost
            )
            self.layer_states.append(state)
        
        self.layers_enabled = True
        self.active_layer = 0
        self.draw_order = list(range(len(self.layer_states)))  # Default: [0, 1, ...]
        
        # Also set base theme to layer 0's theme for non-layered methods
        if self.layer_states:
            self.theme = self.layer_states[0].theme
    
    def toggle_layers(self) -> bool:
        """Toggle layered mode. Returns new state."""
        self.layers_enabled = not self.layers_enabled
        return self.layers_enabled
    
    def select_layer(self, layer_index: int) -> bool:
        """
        Select which layer to edit.
        
        Args:
            layer_index: 0-based layer index
            
        Returns:
            True if layer was selected, False if invalid index
        """
        if 0 <= layer_index < len(self.layer_states):
            self.active_layer = layer_index
            return True
        return False
    
    def toggle_layer_visibility(self, layer_index: int) -> bool:
        """
        Toggle visibility of a specific layer.
        
        Args:
            layer_index: 0-based layer index
            
        Returns:
            New visibility state
        """
        if 0 <= layer_index < len(self.layer_states):
            self.layer_states[layer_index].visible = not self.layer_states[layer_index].visible
            return self.layer_states[layer_index].visible
        return False
    
    def swap_draw_order(self, direction: int) -> Optional[Tuple[int, int]]:
        """
        Move the active layer's draw position (z-order).
        
        Args:
            direction: -1 to move toward background (drawn earlier),
                       +1 to move toward foreground (drawn later)
        
        Returns:
            Tuple of (old_position, new_position) in draw order, or None if at boundary
        """
        if not self.layers_enabled or not self.draw_order:
            return None
        
        # Find where active_layer is in draw_order
        try:
            current_pos = self.draw_order.index(self.active_layer)
        except ValueError:
            return None
        
        new_pos = current_pos + direction
        
        # Check bounds
        if new_pos < 0 or new_pos >= len(self.draw_order):
            return None
        
        # Swap positions in draw_order
        self.draw_order[current_pos], self.draw_order[new_pos] = \
            self.draw_order[new_pos], self.draw_order[current_pos]
        
        return (current_pos, new_pos)
    
    def get_layer_draw_position(self, layer_index: int) -> int:
        """Get the draw position (z-order) of a layer. 0=background, higher=foreground."""
        try:
            return self.draw_order.index(layer_index)
        except ValueError:
            return -1
    
    def set_layer_theme(self, theme_name: str, brightness_boost: float = 1.0) -> None:
        """Set theme for the active layer."""
        if self.layers_enabled and self.layer_states:
            self.layer_states[self.active_layer].theme = get_theme(theme_name, brightness_boost=brightness_boost)
            self.layer_states[self.active_layer].theme_name = theme_name
        else:
            self.theme = get_theme(theme_name, brightness_boost=brightness_boost)
    
    def get_active_layer_info(self) -> Dict[str, Any]:
        """Get info about the currently active layer."""
        if self.layers_enabled and self.layer_states:
            state = self.layer_states[self.active_layer]
            return {
                'index': self.active_layer,
                'theme': state.theme_name,
                'bars': state.bars_enabled,
                'gradient': state.gradient_enabled,
                'overflow': state.overflow_enabled,
                'peak': state.peak_enabled,
                'visible': state.visible,
                'boost': state.boost
            }
        return {'index': 0, 'theme': 'default', 'bars': self.bars_enabled, 'gradient': self.gradient_mode, 'overflow': self.overflow_mode, 'peak': False}
    
    def draw(
        self,
        canvas,
        smoothed_bars: np.ndarray,
        peak_heights: Optional[np.ndarray] = None,
        layer_bars: Optional[List[np.ndarray]] = None,
        layer_peaks: Optional[List[np.ndarray]] = None
    ) -> None:
        """
        Draw vertical bars with configurable gradient and overflow.
        
        Args:
            canvas: RGB matrix canvas to draw on
            smoothed_bars: Normalized bar values (0-1) for single-layer mode
            peak_heights: Optional peak indicator positions (0-1) for single-layer mode
            layer_bars: Optional list of bar arrays for multi-layer mode
            layer_peaks: Optional list of peak arrays for multi-layer mode
        """
        if self.theme is None and not self.layers_enabled:
            raise RuntimeError("Theme not set. Call set_theme() before draw().")
        
        canvas.Clear()
        
        height = self.height
        shadow_enabled = self.settings.shadow.enabled and self.shadow_buffer is not None
        
        # Debug mode: draw full screen with static color gradient per column
        if self.debug_mode:
            num_bins = len(smoothed_bars) if layer_bars is None else len(layer_bars[0])
            self._draw_debug(canvas, num_bins, height)
            return
        
        # Full mode: draw full screen with gradient scaled by FFT data
        if self.full_mode:
            num_bins = len(smoothed_bars) if layer_bars is None else len(layer_bars[0])
            bars_to_use = smoothed_bars if layer_bars is None else layer_bars[0]
            self._draw_full(canvas, bars_to_use, num_bins, height)
            return
        
        # Decay shadow buffer once per frame (vectorized)
        if shadow_enabled:
            self.decay_shadow()
            self._draw_shadows(canvas, height)
        
        # Skip bar drawing if disabled (peaks-only mode)
        if not self.bars_enabled:
            return
        
        # Multi-layer mode: draw each layer
        if self.layers_enabled and layer_bars is not None:
            self._draw_layers(canvas, layer_bars, layer_peaks, height, shadow_enabled)
        else:
            # Single-layer mode: original behavior
            num_bins = len(smoothed_bars)
            for i, raw_ratio in enumerate(smoothed_bars):
                if np.isnan(raw_ratio):
                    raw_ratio = 0.0
                
                column_ratio = i / num_bins
                
                if self.overflow_mode:
                    self._draw_bar_overflow(canvas, i, raw_ratio, column_ratio, height, shadow_enabled)
                else:
                    self._draw_bar_standard(canvas, i, raw_ratio, column_ratio, height, shadow_enabled)
    
    def _draw_layers(
        self,
        canvas,
        layer_bars: List[np.ndarray],
        layer_peaks: Optional[List[np.ndarray]],
        height: int,
        shadow_enabled: bool
    ) -> None:
        """
        Draw all visible layers in draw_order (first = background, last = foreground).
        
        Layers are drawn according to self.draw_order, which maps draw position to layer index.
        Later-drawn layers overwrite earlier ones where they have values.
        """
        for draw_pos, layer_idx in enumerate(self.draw_order):
            if layer_idx >= len(layer_bars) or layer_idx >= len(self.layer_states):
                continue
            
            bars = layer_bars[layer_idx]
            state = self.layer_states[layer_idx]
            
            # Skip invisible layers
            if not state.visible:
                continue
            
            num_bins = len(bars)
            
            # Draw bars if enabled for this layer
            if state.bars_enabled:
                for i, raw_ratio in enumerate(bars):
                    if np.isnan(raw_ratio):
                        raw_ratio = 0.0
                    
                    column_ratio = i / num_bins
                    
                    if state.overflow_enabled:
                        self._draw_bar_overflow_layer(
                            canvas, i, raw_ratio, column_ratio, height,
                            state, shadow_enabled
                        )
                    else:
                        self._draw_bar_standard_layer(
                            canvas, i, raw_ratio, column_ratio, height,
                            state, shadow_enabled
                        )
            
            # Draw peaks for this layer if enabled
            if state.peak_enabled and layer_peaks is not None and layer_idx < len(layer_peaks):
                self._draw_layer_peaks(canvas, layer_peaks[layer_idx], state, height)
    
    def _draw_layer_peaks(
        self,
        canvas,
        peak_heights: np.ndarray,
        state: LayerState,
        height: int
    ) -> None:
        """Draw peak indicators for a specific layer."""
        num_bins = len(peak_heights)
        color_mode = self.settings.peak.color_mode
        theme = state.theme
        
        for i, peak_value in enumerate(peak_heights):
            if np.isnan(peak_value):
                peak_value = 0.0
            
            peak_y = max(0, int(min(peak_value, 1.0) * height))
            y = height - 1 - peak_y
            
            if y < 0 or y >= height:
                continue
            
            column_ratio = i / num_bins
            reference_color = theme.get_color(peak_value, column_ratio)
            pr, pg, pb = theme.get_peak_color(color_mode, reference_color, column_ratio)
            
            canvas.SetPixel(i, y, pr, pg, pb)
    
    def _draw_bar_standard_layer(
        self,
        canvas,
        col: int,
        bar_value: float,
        column_ratio: float,
        height: int,
        state: LayerState,
        shadow_enabled: bool
    ) -> None:
        """Draw a standard bar for a specific layer (clamped to display height)."""
        bar_value = min(1.0, max(0.0, bar_value))
        bar_height = int(bar_value * height)
        
        if bar_height <= 0:
            return
        
        theme = state.theme
        
        if state.gradient_enabled:
            # Per-pixel gradient
            for j in range(bar_height):
                y = height - 1 - j
                height_ratio = j / height
                r, g, b = theme.get_color(height_ratio, column_ratio)
                canvas.SetPixel(col, y, r, g, b)
                
                if shadow_enabled:
                    self.shadow_buffer[col, j] = 1.0
                    self.shadow_colors[col, j] = (r, g, b)
        else:
            # Uniform color based on bar height
            r, g, b = theme.get_color(bar_value, column_ratio)
            
            for j in range(bar_height):
                y = height - 1 - j
                canvas.SetPixel(col, y, r, g, b)
                
                if shadow_enabled:
                    self.shadow_buffer[col, j] = 1.0
                    self.shadow_colors[col, j] = (r, g, b)
    
    def _draw_bar_overflow_layer(
        self,
        canvas,
        col: int,
        raw_ratio: float,
        column_ratio: float,
        height: int,
        state: LayerState,
        shadow_enabled: bool
    ) -> None:
        """Draw a bar with overflow stacking for a specific layer."""
        overflow = self.settings.overflow
        total_pixels = int(raw_ratio * height * overflow.multiplier)
        
        if total_pixels <= 0:
            return
        
        theme = state.theme
        
        if state.gradient_enabled:
            visible_pixels = min(total_pixels, height)
            top_layer = (total_pixels - 1) // height
            remainder = total_pixels % height
            boundary = remainder if remainder > 0 else height
            
            for j in range(visible_pixels):
                y = height - 1 - j
                layer_ratio = j / height
                
                if j < boundary:
                    layer = top_layer
                else:
                    layer = top_layer - 1
                
                r, g, b = theme.get_overflow_color(
                    layer, layer_ratio, column_ratio, self.frame_count, raw_ratio
                )
                canvas.SetPixel(col, y, r, g, b)
                
                if shadow_enabled:
                    self.shadow_buffer[col, j] = 1.0
                    self.shadow_colors[col, j] = (r, g, b)
        else:
            top_layer = (total_pixels - 1) // height
            top_position_in_layer = (total_pixels - 1) % height
            top_ratio = top_position_in_layer / height
            
            r, g, b = theme.get_overflow_color(
                top_layer, top_ratio, column_ratio, self.frame_count, raw_ratio
            )
            
            visible_pixels = min(total_pixels, height)
            for j in range(visible_pixels):
                y = height - 1 - j
                canvas.SetPixel(col, y, r, g, b)
                
                if shadow_enabled:
                    self.shadow_buffer[col, j] = 1.0
                    self.shadow_colors[col, j] = (r, g, b)
    
    def _draw_full(self, canvas, smoothed_bars: np.ndarray, num_bins: int, height: int) -> None:
        """
        Draw full mode: entire screen lit with crossfade at bar height.
        
        - Top color from top of screen down to (bar_height + 10)
        - Crossfade zone from (bar_height + 10) to (bar_height - 10)
        - Base color from (bar_height - 10) to bottom
        """
        fade_radius = 10  # pixels above and below bar height for crossfade
        
        # Clean and clamp bar values once
        bar_values = np.nan_to_num(smoothed_bars, nan=0.0)
        bar_values = np.clip(bar_values, 0.0, 1.0)
        
        for col in range(num_bins):
            bar_value = bar_values[col]
            column_ratio = col / num_bins
            
            # Get top and base colors for this column
            top_r, top_g, top_b = self.theme.get_color(1.0, column_ratio)
            base_r, base_g, base_b = self.theme.get_color(0.0, column_ratio)
            
            # Calculate bar height in pixels (j index, 0=bottom)
            bar_height_px = int(bar_value * (height - 1))
            
            # Define crossfade zone boundaries (in j coordinates, 0=bottom)
            fade_top = bar_height_px + fade_radius  # above this = top color
            fade_bottom = bar_height_px - fade_radius  # below this = base color
            
            for j in range(height):
                y = height - 1 - j  # screen y coordinate
                
                if j >= fade_top:
                    # Above crossfade zone: top color
                    r, g, b = top_r, top_g, top_b
                elif j <= fade_bottom:
                    # Below crossfade zone: base color
                    r, g, b = base_r, base_g, base_b
                else:
                    # In crossfade zone: blend between base and top
                    # fade_t goes from 0 (at fade_bottom) to 1 (at fade_top)
                    fade_t = (j - fade_bottom) / (fade_top - fade_bottom)
                    r = int(base_r + (top_r - base_r) * fade_t)
                    g = int(base_g + (top_g - base_g) * fade_t)
                    b = int(base_b + (top_b - base_b) * fade_t)
                
                canvas.SetPixel(col, y, r, g, b)
    
    def _draw_debug(self, canvas, num_bins: int, height: int) -> None:
        """
        Draw debug mode: full screen with static color gradient per column.
        
        Each column shows the full color scale from base color (bottom)
        to top color (top), using non-gradient mode coloring (uniform
        color based on height ratio).
        """
        for col in range(num_bins):
            column_ratio = col / num_bins
            
            for j in range(height):
                y = height - 1 - j
                # height_ratio represents how high this pixel is (0=bottom, 1=top)
                height_ratio = j / (height - 1) if height > 1 else 0.0
                
                # Use the same coloring as non-gradient mode:
                # color is based on total bar height, which equals height_ratio here
                r, g, b = self.theme.get_color(height_ratio, column_ratio)
                canvas.SetPixel(col, y, r, g, b)
    
    def _draw_shadows(self, canvas, height: int) -> None:
        """Draw shadow pixels using sparse iteration."""
        shadow_i, shadow_j = np.nonzero(self.shadow_buffer)
        for idx in range(len(shadow_i)):
            i, j = shadow_i[idx], shadow_j[idx]
            shadow_val = self.shadow_buffer[i, j]
            y = height - 1 - j
            sc = self.shadow_colors[i, j]
            sr = int(sc[0] * shadow_val)
            sg = int(sc[1] * shadow_val)
            sb = int(sc[2] * shadow_val)
            canvas.SetPixel(i, y, sr, sg, sb)
    
    def _draw_bar_standard(
        self,
        canvas,
        col: int,
        bar_value: float,
        column_ratio: float,
        height: int,
        shadow_enabled: bool
    ) -> None:
        """Draw a standard bar (clamped to display height)."""
        # Clamp to 0-1 for standard mode
        bar_value = min(1.0, max(0.0, bar_value))
        bar_height = int(bar_value * height)
        
        if bar_height <= 0:
            return
        
        if self.gradient_mode:
            # Per-pixel gradient
            for j in range(bar_height):
                y = height - 1 - j
                height_ratio = j / height
                r, g, b = self.theme.get_color(height_ratio, column_ratio)
                canvas.SetPixel(col, y, r, g, b)
                
                if shadow_enabled:
                    self.shadow_buffer[col, j] = 1.0
                    self.shadow_colors[col, j] = (r, g, b)
        else:
            # Uniform color based on bar height
            r, g, b = self.theme.get_color(bar_value, column_ratio)
            
            for j in range(bar_height):
                y = height - 1 - j
                canvas.SetPixel(col, y, r, g, b)
                
                if shadow_enabled:
                    self.shadow_buffer[col, j] = 1.0
                    self.shadow_colors[col, j] = (r, g, b)
    
    def _draw_bar_overflow(
        self,
        canvas,
        col: int,
        raw_ratio: float,
        column_ratio: float,
        height: int,
        shadow_enabled: bool
    ) -> None:
        """Draw a bar with overflow stacking."""
        overflow = self.settings.overflow
        total_pixels = int(raw_ratio * height * overflow.multiplier)
        
        if total_pixels <= 0:
            return
        
        if self.gradient_mode:
            # Optimized: draw each pixel exactly once
            # Only at most 2 layers are ever visible on screen
            visible_pixels = min(total_pixels, height)
            
            # Calculate which layers are visible
            top_layer = (total_pixels - 1) // height
            remainder = total_pixels % height
            # boundary = where top layer ends (pixels 0 to boundary-1 are top layer)
            boundary = remainder if remainder > 0 else height
            
            for j in range(visible_pixels):
                y = height - 1 - j
                layer_ratio = j / height
                
                if j < boundary:
                    # This pixel shows the top layer
                    layer = top_layer
                else:
                    # This pixel shows the layer below
                    layer = top_layer - 1
                
                r, g, b = self.theme.get_overflow_color(
                    layer, layer_ratio, column_ratio, self.frame_count, raw_ratio
                )
                canvas.SetPixel(col, y, r, g, b)
                
                if shadow_enabled:
                    self.shadow_buffer[col, j] = 1.0
                    self.shadow_colors[col, j] = (r, g, b)
        else:
            # Uniform color mode: entire bar is one color
            # Color = what the top pixel would be in gradient mode
            # Calculate which layer and position the top pixel is in
            top_layer = (total_pixels - 1) // height
            top_position_in_layer = (total_pixels - 1) % height
            top_ratio = top_position_in_layer / height
            
            # Get the color for the top pixel
            r, g, b = self.theme.get_overflow_color(
                top_layer, top_ratio, column_ratio, self.frame_count, raw_ratio
            )
            
            # Draw entire visible portion with this single color
            visible_pixels = min(total_pixels, height)
            for j in range(visible_pixels):
                y = height - 1 - j
                canvas.SetPixel(col, y, r, g, b)
                
                if shadow_enabled:
                    self.shadow_buffer[col, j] = 1.0
                    self.shadow_colors[col, j] = (r, g, b)
