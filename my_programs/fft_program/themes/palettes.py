"""
Built-in color-stop themes (fft-themes D-002, D-003).

The ``vice_*`` themes take the GTA VI Miami-sunset palette. The hex values are
approximations, meant to be tuned on the panel. The four single-color themes each
stay within one hue from bottom to top, so stacked layers stay distinct; their
overflow layers brighten toward white within the same hue.
"""
from .color_stops import color_stop_theme, wash_toward_white

WHITE = (255, 255, 255)

PINK = (255, 46, 136)      # #FF2E88
ORANGE = (255, 122, 46)    # #FF7A2E
PURPLE = (122, 44, 224)    # #7A2CE0
TEAL = (18, 200, 200)      # #12C8C8


def _single_hue(name, label, dark, main, light, pale):
    return color_stop_theme(
        name,
        f"{label}: dark (low) -> {label.lower()} -> light (high)",
        ((0.0, dark), (0.6, main), (1.0, light)),
        overflow=(
            ((0.0, light), (1.0, pale)),
            ((0.0, pale), (1.0, WHITE)),
        ),
    )


VicePinkTheme = _single_hue("vice_pink", "Hot pink", (110, 0, 60), PINK, (255, 150, 200), (255, 210, 230))
ViceOrangeTheme = _single_hue("vice_orange", "Sunset orange", (140, 40, 0), ORANGE, (255, 190, 130), (255, 225, 190))
VicePurpleTheme = _single_hue("vice_purple", "Violet", (40, 10, 110), PURPLE, (190, 150, 255), (225, 205, 255))
ViceTealTheme = _single_hue("vice_teal", "Ocean teal", (0, 70, 80), TEAL, (140, 245, 240), (200, 255, 250))

ViceCityTheme = color_stop_theme(
    "vice_city",
    "Miami sunset by height: purple (low) -> pink -> orange -> peach (high)",
    (
        (0.0, (60, 20, 140)),
        (0.35, (160, 30, 200)),
        (0.6, PINK),
        (0.82, ORANGE),
        (1.0, (255, 200, 120)),
    ),
    overflow=(
        ((0.0, (255, 200, 120)), (1.0, (255, 230, 170))),
        ((0.0, (255, 230, 170)), (1.0, WHITE)),
    ),
)

_SKYLINE = ((0.0, TEAL), (0.35, PURPLE), (0.68, PINK), (1.0, ORANGE))
ViceSkylineTheme = color_stop_theme(
    "vice_skyline",
    "Miami skyline across columns: teal (left) -> purple -> pink -> orange (right)",
    _SKYLINE,
    axis="column",
    overflow=(wash_toward_white(_SKYLINE, 0.35), wash_toward_white(_SKYLINE, 0.65)),
)

PALETTE_THEMES = (
    VicePinkTheme,
    ViceOrangeTheme,
    VicePurpleTheme,
    ViceTealTheme,
    ViceCityTheme,
    ViceSkylineTheme,
)
