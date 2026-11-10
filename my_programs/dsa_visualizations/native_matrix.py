"""Small native-matrix adapter using the controller's shared option contract."""

from __future__ import annotations

import argparse

from matrix_controller.definitions import MATRIX_PARAMETERS


class NativeMatrixApp:
    """Own argparse and the rgbmatrix double buffer without upstream SampleBase."""

    def __init__(self) -> None:
        self.parser = argparse.ArgumentParser()
        for parameter in MATRIX_PARAMETERS:
            if parameter.true_cli_value is not None:
                self.parser.add_argument(
                    parameter.cli_flag,
                    dest=parameter.name,
                    nargs="?",
                    const=True,
                    default=parameter.default,
                    type=_legacy_true_value,
                    help=parameter.description,
                )
            else:
                parameter.add_to_argparse(self.parser)
        self.args: argparse.Namespace | None = None
        self.matrix = None
        self.canvas = None

    def process_args(self) -> argparse.Namespace:
        self.args = self.parser.parse_args()
        return self.args

    def initialize_matrix(self) -> bool:
        if self.args is None:
            self.process_args()
        assert self.args is not None
        try:
            from rgbmatrix import RGBMatrix, RGBMatrixOptions
        except ImportError:
            print("Error: rgbmatrix module not found.")
            print("Run this visualization on a Raspberry Pi with the native binding installed.")
            return False

        options = RGBMatrixOptions()
        options.rows = self.args.led_rows
        options.cols = self.args.led_cols
        options.chain_length = self.args.led_chain
        options.parallel = self.args.led_parallel
        options.pwm_bits = self.args.led_pwm_bits
        options.brightness = self.args.led_brightness
        options.hardware_mapping = self.args.led_gpio_mapping
        options.gpio_slowdown = self.args.led_slowdown_gpio
        options.scan_mode = self.args.led_scan_mode
        options.pwm_lsb_nanoseconds = self.args.led_pwm_lsb_nanoseconds
        options.show_refresh_rate = int(self.args.led_show_refresh)
        options.disable_hardware_pulsing = self.args.led_no_hardware_pulse
        options.led_rgb_sequence = self.args.led_rgb_sequence
        options.pixel_mapper_config = self.args.led_pixel_mapper
        options.row_address_type = self.args.led_row_addr_type
        options.multiplexing = self.args.led_multiplexing
        options.panel_type = self.args.led_panel_type
        options.drop_privileges = not self.args.led_no_drop_privs

        self.matrix = RGBMatrix(options=options)
        self.canvas = self.matrix.CreateFrameCanvas()
        return True

    @property
    def width(self) -> int:
        return self.matrix.width if self.matrix is not None else 0

    @property
    def height(self) -> int:
        return self.matrix.height if self.matrix is not None else 0

    def swap_canvas(self) -> None:
        self.canvas = self.matrix.SwapOnVSync(self.canvas)

    def clear(self) -> None:
        if self.canvas is not None:
            self.canvas.Clear()

    def print_help(self) -> None:
        self.parser.print_help()


def _legacy_true_value(raw: str) -> bool:
    """Accept the fixed value emitted for upstream SampleBase compatibility."""

    if raw != "1":
        raise argparse.ArgumentTypeError("value must be 1 when supplied")
    return True
