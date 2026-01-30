"""
Matrix application wrapper for FFT visualizer.

Handles LED matrix initialization and main loop orchestration.
"""
import sys
import os
import time
import argparse
from typing import Optional

# Add path to find rgbmatrix (adjust path as needed for your installation)
# This will be set up properly when running on the Pi


class MatrixApp:
    """
    Main application class that wraps the RGB matrix.
    
    Handles:
    - Command line argument parsing for matrix options
    - Matrix initialization
    - Main render loop orchestration
    """
    
    def __init__(self):
        """Initialize the matrix application."""
        self.parser = argparse.ArgumentParser()
        self._setup_arguments()
        
        self.args = None
        self.matrix = None
        self.canvas = None
    
    def _setup_arguments(self):
        """Set up command line arguments for matrix configuration."""
        # LED Matrix arguments (matching samplebase.py)
        self.parser.add_argument(
            "-r", "--led-rows",
            action="store",
            help="Display rows. 16 for 16x32, 32 for 32x32. Default: 32",
            default=32,
            type=int
        )
        self.parser.add_argument(
            "--led-cols",
            action="store",
            help="Panel columns. Typically 32 or 64. (Default: 32)",
            default=32,
            type=int
        )
        self.parser.add_argument(
            "-c", "--led-chain",
            action="store",
            help="Daisy-chained boards. Default: 1.",
            default=1,
            type=int
        )
        self.parser.add_argument(
            "-P", "--led-parallel",
            action="store",
            help="For Plus-models or RPi2: parallel chains. 1..3. Default: 1",
            default=1,
            type=int
        )
        self.parser.add_argument(
            "-p", "--led-pwm-bits",
            action="store",
            help="Bits used for PWM. Something between 1..11. Default: 11",
            default=11,
            type=int
        )
        self.parser.add_argument(
            "-b", "--led-brightness",
            action="store",
            help="Sets brightness level. Default: 100. Range: 1..100",
            default=100,
            type=int
        )
        self.parser.add_argument(
            "-m", "--led-gpio-mapping",
            help="Hardware Mapping: regular, adafruit-hat, adafruit-hat-pwm",
            choices=['regular', 'regular-pi1', 'adafruit-hat', 'adafruit-hat-pwm'],
            type=str
        )
        self.parser.add_argument(
            "--led-scan-mode",
            action="store",
            help="Progressive or interlaced scan. 0 Progressive, 1 Interlaced (default)",
            default=1,
            choices=range(2),
            type=int
        )
        self.parser.add_argument(
            "--led-pwm-lsb-nanoseconds",
            action="store",
            help="Base time-unit for the on-time in the lowest significant bit in nanoseconds. Default: 130",
            default=130,
            type=int
        )
        self.parser.add_argument(
            "--led-show-refresh",
            action="store_true",
            help="Shows the current refresh rate of the LED panel"
        )
        self.parser.add_argument(
            "--led-slowdown-gpio",
            action="store",
            help="Slow down writing to GPIO. Range: 0..4. Default: 1",
            default=1,
            type=int
        )
        self.parser.add_argument(
            "--led-no-hardware-pulse",
            action="store",
            help="Don't use hardware pin-pulse generation"
        )
        self.parser.add_argument(
            "--led-rgb-sequence",
            action="store",
            help="Switch if your matrix has led colors swapped. Default: RGB",
            default="RGB",
            type=str
        )
        self.parser.add_argument(
            "--led-pixel-mapper",
            action="store",
            help="Apply pixel mappers. e.g \"Rotate:90\"",
            default="",
            type=str
        )
        self.parser.add_argument(
            "--led-row-addr-type",
            action="store",
            help="0 = default; 1=AB-addressed panels; 2=row direct; 3=ABC-addressed panels; 4 = ABC Shift + DE direct",
            default=0,
            type=int,
            choices=[0, 1, 2, 3, 4]
        )
        self.parser.add_argument(
            "--led-multiplexing",
            action="store",
            help="Multiplexing type: 0=direct; 1=strip; 2=checker; 3=spiral; 4=ZStripe; 5=ZnMirrorZStripe; 6=coreman; 7=Kaler2Scan; 8=ZStripeUneven... (Default: 0)",
            default=0,
            type=int
        )
        self.parser.add_argument(
            "--led-panel-type",
            action="store",
            help="Needed to initialize special panels. Supported: 'FM6126A'",
            default="",
            type=str
        )
        self.parser.add_argument(
            "--led-no-drop-privs",
            dest="drop_privileges",
            help="Don't drop privileges from 'root' after initializing the hardware.",
            action='store_false'
        )
        self.parser.set_defaults(drop_privileges=True)
        
        # Application-specific arguments
        self.parser.add_argument(
            "--settings",
            action="store",
            help="Path to JSON settings file",
            default=None,
            type=str
        )
    
    def add_argument(self, *args, **kwargs):
        """Add custom argument to parser."""
        self.parser.add_argument(*args, **kwargs)
    
    def process_args(self) -> argparse.Namespace:
        """
        Parse command line arguments.
        
        Returns:
            Parsed arguments namespace
        """
        self.args = self.parser.parse_args()
        return self.args
    
    def initialize_matrix(self) -> bool:
        """
        Initialize the RGB matrix with parsed arguments.
        
        Returns:
            True if successful, False otherwise
        """
        if self.args is None:
            self.process_args()
        
        try:
            from rgbmatrix import RGBMatrix, RGBMatrixOptions
        except ImportError:
            print("Error: rgbmatrix module not found.")
            print("Make sure you're running on a Raspberry Pi with the library installed.")
            return False
        
        options = RGBMatrixOptions()
        
        if self.args.led_gpio_mapping is not None:
            options.hardware_mapping = self.args.led_gpio_mapping
        
        options.rows = self.args.led_rows
        options.cols = self.args.led_cols
        options.chain_length = self.args.led_chain
        options.parallel = self.args.led_parallel
        options.row_address_type = self.args.led_row_addr_type
        options.multiplexing = self.args.led_multiplexing
        options.pwm_bits = self.args.led_pwm_bits
        options.brightness = self.args.led_brightness
        options.pwm_lsb_nanoseconds = self.args.led_pwm_lsb_nanoseconds
        options.led_rgb_sequence = self.args.led_rgb_sequence
        options.pixel_mapper_config = self.args.led_pixel_mapper
        options.panel_type = self.args.led_panel_type
        
        if self.args.led_show_refresh:
            options.show_refresh_rate = 1
        
        if self.args.led_slowdown_gpio is not None:
            options.gpio_slowdown = self.args.led_slowdown_gpio
        
        if self.args.led_no_hardware_pulse:
            options.disable_hardware_pulsing = True
        
        if not self.args.drop_privileges:
            options.drop_privileges = False
        
        self.matrix = RGBMatrix(options=options)
        self.canvas = self.matrix.CreateFrameCanvas()
        
        return True
    
    @property
    def width(self) -> int:
        """Get matrix width."""
        return self.matrix.width if self.matrix else 0
    
    @property
    def height(self) -> int:
        """Get matrix height."""
        return self.matrix.height if self.matrix else 0
    
    def swap_canvas(self):
        """Swap frame buffer on vsync."""
        self.canvas = self.matrix.SwapOnVSync(self.canvas)
    
    def clear(self):
        """Clear the canvas."""
        if self.canvas:
            self.canvas.Clear()
    
    def print_help(self):
        """Print help message."""
        self.parser.print_help()
