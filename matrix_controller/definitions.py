"""Code-owned parameter definitions for the initial supported visualizations."""

from __future__ import annotations

from .schema import ParameterSpec


# Matrix parameters are always passed explicitly by the supervisor. This removes the
# historical difference between launcher defaults (64x64/slowdown 2) and the direct
# scripts' defaults (often 32 rows/slowdown 1).
MATRIX_PARAMETERS: tuple[ParameterSpec, ...] = (
    ParameterSpec("led_rows", "--led-rows", "int", 64, "Panel row count.", 1, 256),
    ParameterSpec("led_cols", "--led-cols", "int", 64, "Panel column count.", 1, 256),
    ParameterSpec("led_chain", "--led-chain", "int", 1, "Number of chained panels.", 1, 16),
    ParameterSpec("led_parallel", "--led-parallel", "int", 1, "Parallel panel chains.", 1, 3),
    ParameterSpec("led_pwm_bits", "--led-pwm-bits", "int", 11, "PWM bit depth.", 1, 11),
    ParameterSpec("led_brightness", "--led-brightness", "int", 100, "Panel brightness percentage.", 1, 100),
    ParameterSpec(
        "led_gpio_mapping",
        "--led-gpio-mapping",
        "enum",
        "adafruit-hat",
        "GPIO/HAT hardware mapping.",
        choices=("regular", "regular-pi1", "adafruit-hat", "adafruit-hat-pwm"),
    ),
    ParameterSpec("led_slowdown_gpio", "--led-slowdown-gpio", "int", 2, "GPIO timing slowdown.", 0, 5),
    ParameterSpec(
        "led_scan_mode",
        "--led-scan-mode",
        "int",
        1,
        "Panel scan mode: zero is progressive and one is interlaced.",
        0,
        1,
    ),
    ParameterSpec(
        "led_pwm_lsb_nanoseconds",
        "--led-pwm-lsb-nanoseconds",
        "int",
        130,
        "PWM least-significant-bit duration in nanoseconds.",
        10,
        1000,
    ),
    ParameterSpec(
        "led_show_refresh",
        "--led-show-refresh",
        "bool",
        False,
        "Print the panel refresh rate in service logs.",
    ),
    ParameterSpec(
        "led_no_hardware_pulse",
        "--led-no-hardware-pulse",
        "bool",
        False,
        "Disable hardware-assisted GPIO pulses.",
        # The upstream Python SampleBase declares this legacy flag as a stored
        # value rather than store_true. Supplying a fixed value works for both it
        # and the project's matching MatrixApp parser without exposing raw argv.
        true_cli_value="1",
    ),
    ParameterSpec(
        "led_rgb_sequence",
        "--led-rgb-sequence",
        "enum",
        "RGB",
        "Physical color-channel order.",
        choices=("RGB", "RBG", "GRB", "GBR", "BRG", "BGR"),
    ),
    ParameterSpec(
        "led_pixel_mapper",
        "--led-pixel-mapper",
        "string",
        "",
        "Optional driver pixel-mapper chain, such as Rotate:90.",
        maximum_length=128,
    ),
    ParameterSpec(
        "led_row_addr_type",
        "--led-row-addr-type",
        "int",
        0,
        "Panel row-addressing strategy.",
        0,
        4,
    ),
    ParameterSpec(
        "led_multiplexing",
        "--led-multiplexing",
        "int",
        0,
        "Driver multiplexing mapper index.",
        0,
        17,
    ),
    ParameterSpec(
        "led_panel_type",
        "--led-panel-type",
        "string",
        "",
        "Optional panel chipset name, such as FM6126A.",
        maximum_length=64,
    ),
    ParameterSpec(
        "led_no_drop_privs",
        "--led-no-drop-privs",
        "bool",
        False,
        "Keep root privileges after matrix initialization.",
    ),
)


FFT_PARAMETERS: tuple[ParameterSpec, ...] = (
    *MATRIX_PARAMETERS,
    ParameterSpec(
        "theme",
        "--theme",
        "enum",
        "ocean",
        "Initial FFT color theme.",
        choices=(
            "autumn",
            "dynamic",
            "dynamic_lateral_gradient",
            "forest",
            "ocean",
            "rainbow",
            "warm",
        ),
    ),
    ParameterSpec(
        "gradient",
        "--gradient",
        "bool",
        False,
        "Render each bar with per-pixel gradient colors.",
        negative_cli_flag="--no-gradient",
    ),
    ParameterSpec(
        "overflow",
        "--overflow",
        "bool",
        True,
        "Allow FFT bars to wrap above the matrix height.",
        negative_cli_flag="--no-overflow",
    ),
    ParameterSpec(
        "non_interactive",
        "--non-interactive",
        "bool",
        True,
        "Disable terminal keyboard input for service execution.",
    ),
)


SORT_PARAMETERS: tuple[ParameterSpec, ...] = (
    *MATRIX_PARAMETERS,
    ParameterSpec(
        "mode",
        "--mode",
        "enum",
        "random",
        "Automatic sort sequence to run.",
        choices=("single", "all", "random"),
    ),
    ParameterSpec(
        "sort",
        "--sort",
        "enum",
        "quick",
        "Sort used when mode is single.",
        choices=("insertion", "selection", "bubble", "quick"),
    ),
    ParameterSpec(
        "delay",
        "--delay",
        "float",
        0.05,
        "Seconds to pause after each drawn sorting frame.",
        0.0,
        2.0,
    ),
    ParameterSpec(
        "seed",
        "--seed",
        "int",
        0,
        "Random seed; use the same value to reproduce generated arrays.",
        0,
        2_147_483_647,
    ),
    ParameterSpec(
        "max_runs",
        "--max-runs",
        "int",
        0,
        "Stop after this many sorts; zero means continuous where supported.",
        0,
        1_000_000,
    ),
)


MASTER_THEOREM_PARAMETERS: tuple[ParameterSpec, ...] = (
    *MATRIX_PARAMETERS,
    ParameterSpec(
        "example",
        "--example",
        "enum",
        "custom",
        "Use a named recurrence example instead of the custom a, b, n, and k values.",
        choices=(
            "custom",
            "binary_search",
            "merge_sort",
            "root_heavy",
            "strassen",
            "leaf_heavy",
        ),
    ),
    ParameterSpec("a", "--a", "int", 2, "Recursive calls made by each node.", 1, 8),
    ParameterSpec("b", "--b", "int", 2, "Input-size divisor for each child.", 2, 8),
    ParameterSpec("n", "--n", "int", 32, "Literal root input width in pixels.", 2, 32),
    ParameterSpec(
        "k",
        "--k",
        "float",
        1.0,
        "Exponent in the non-recursive work f(n) = n^k.",
        0.0,
        4.0,
    ),
    ParameterSpec(
        "base_case",
        "--base-case",
        "int",
        1,
        "Problem size at which recursion stops.",
        1,
        64,
    ),
    ParameterSpec(
        "animate",
        "--animate",
        "bool",
        False,
        "Legacy animation switch; the refactored matrix view is static.",
        negative_cli_flag="--no-animate",
    ),
    ParameterSpec(
        "delay",
        "--delay",
        "float",
        0.45,
        "Seconds between level-reveal and comparison frames.",
        0.02,
        5.0,
    ),
    ParameterSpec(
        "hold",
        "--hold",
        "float",
        3.0,
        "Seconds to hold the final Master Theorem result.",
        0.1,
        30.0,
    ),
    ParameterSpec(
        "cycles",
        "--cycles",
        "int",
        0,
        "Animation cycles before exit; zero repeats continuously.",
        0,
        1000,
    ),
)


GRAPH_TRAVERSAL_PARAMETERS: tuple[ParameterSpec, ...] = (
    *MATRIX_PARAMETERS,
    ParameterSpec("n", "--n", "int", 12, "Number of graph nodes.", 2, 32),
    ParameterSpec(
        "e",
        "--e",
        "int",
        0,
        "Number of edges; zero selects a valid automatic count based on the other options.",
        0,
        90,
    ),
    ParameterSpec(
        "display_mode",
        "--display-mode",
        "enum",
        "single",
        "Use the full panel or compare two algorithms in 32x64 viewports.",
        choices=("single", "bfs_dfs_split", "prim_kruskal_split"),
    ),
    ParameterSpec(
        "traverse",
        "--traverse",
        "enum",
        "bfs",
        "Graph traversal algorithm.",
        choices=("bfs", "dfs", "dijkstra", "components", "prim", "kruskal"),
    ),
    ParameterSpec(
        "seed",
        "--seed",
        "int",
        0,
        "Random seed; identical values reproduce the graph and edge weights.",
        0,
        2_147_483_647,
    ),
    ParameterSpec(
        "connected",
        "--connected",
        "bool",
        True,
        "Guarantee connectivity for an undirected graph.",
        negative_cli_flag="--not-connected",
    ),
    ParameterSpec(
        "allow_cycles",
        "--allow-cycles",
        "bool",
        False,
        "Permit cycles in the generated graph.",
        negative_cli_flag="--no-cycles",
    ),
    ParameterSpec(
        "directed",
        "--directed",
        "bool",
        False,
        "Generate directed edges and render orange destination markers.",
        negative_cli_flag="--undirected",
    ),
    ParameterSpec(
        "ensure_path",
        "--ensure-path",
        "bool",
        True,
        "Guarantee that the directed start node can reach the target node.",
        negative_cli_flag="--no-ensure-path",
    ),
    ParameterSpec(
        "directed_connectivity",
        "--directed-connectivity",
        "enum",
        "weak",
        "Connectivity guarantee used when directed is enabled.",
        choices=("strong", "weak", "none"),
    ),
    ParameterSpec(
        "component_type",
        "--component-type",
        "enum",
        "weak",
        "Choose weak or strong components when traverse=components.",
        choices=("weak", "strong"),
    ),
    ParameterSpec(
        "step_mode",
        "--step-mode",
        "enum",
        "edge",
        "Advance once per examined edge or once per processed node.",
        choices=("edge", "node"),
    ),
    ParameterSpec(
        "animate_back_edges",
        "--animate-back-edges",
        "bool",
        False,
        "Animate an undirected discovery edge again when examined from its child.",
        negative_cli_flag="--no-animate-back-edges",
    ),
    ParameterSpec(
        "frame_duration",
        "--frame-duration",
        "float",
        0.2,
        "Seconds each traversal step remains on screen.",
        0.01,
        10.0,
    ),
    ParameterSpec(
        "holding_period",
        "--holding-period",
        "int",
        10,
        "Opening- and final-frame duration measured in traversal frames.",
        1,
        10_000,
    ),
)


def application_parameters(visualization_id: str) -> tuple[ParameterSpec, ...]:
    """Return only parameters owned by an application's parser extension.

    The upstream/FFT matrix bases already define matrix flags. Converted scripts use
    this helper for their additional arguments so CLI choices remain aligned with the
    web manifest without registering duplicate argparse options.
    """

    all_parameters = {
        "fft": FFT_PARAMETERS,
        "graph_traversal": GRAPH_TRAVERSAL_PARAMETERS,
        "master_theorem": MASTER_THEOREM_PARAMETERS,
        "sorts": SORT_PARAMETERS,
    }
    try:
        parameters = all_parameters[visualization_id]
    except KeyError as exc:
        raise KeyError(f"unknown visualization: {visualization_id}") from exc
    matrix_names = {parameter.name for parameter in MATRIX_PARAMETERS}
    return tuple(parameter for parameter in parameters if parameter.name not in matrix_names)
