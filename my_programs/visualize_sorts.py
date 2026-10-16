#!/usr/bin/env python
"""Interactive and service-safe sorting visualizations for the RGB matrix."""

from __future__ import annotations

import random
import signal
import sys
import time
from pathlib import Path


# This program historically lived beside the upstream samples and imported
# ``samplebase`` by filename. In this repository it lives in my_programs/, so make
# the existing untracked driver checkout explicit until the runtime owns its own
# matrix adapter. The application code itself remains in this repository.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROGRAM_DIRECTORY = Path(__file__).resolve().parent
UPSTREAM_SAMPLE_CANDIDATES = (
    PROJECT_ROOT / "rpi-rgb-led-matrix" / "bindings" / "python" / "samples",
    Path.home() / "rpi-rgb-led-matrix" / "bindings" / "python" / "samples",
)
for import_root in (PROJECT_ROOT, PROGRAM_DIRECTORY, *UPSTREAM_SAMPLE_CANDIDATES):
    import_root_string = str(import_root)
    if import_root_string not in sys.path:
        sys.path.insert(0, import_root_string)

from matrix_controller.definitions import application_parameters
from samplebase import SampleBase
import sorts

# Registry of available sorts: (stable ID, display name, method name). The stable ID
# is shared with the CLI/web parameter contract; display text can change safely.
SORTS = [
    ("insertion", "Insertion", "do_insertion_sort"),
    ("selection", "Selection", "do_selection_sort"),
    ("bubble", "Bubble", "do_bubble_sort"),
    ("quick", "Quick", "do_quick_sort"),
]


class VisualizeSorts(SampleBase):
    def __init__(self, *args, **kwargs):
        super(VisualizeSorts, self).__init__(*args, **kwargs)

        # ``mode=None`` preserves the existing terminal menu. A service always passes
        # an explicit allowlisted mode, and a non-TTY direct launch safely falls back
        # to continuous random sorts instead of blocking forever on input().
        direct_defaults = {
            "mode": None,
            "sort": "quick",
            "delay": 0.05,
            "seed": None,
            "max_runs": 0,
        }
        for parameter in application_parameters("sorts"):
            parameter.add_to_argparse(
                self.parser,
                argument_default=direct_defaults[parameter.name],
            )

    def run(self):
        """Select the interactive menu or a non-interactive service mode."""

        self._offset_canvas = self.matrix.CreateFrameCanvas()
        length = self.matrix.width
        rng = random.Random(self.args.seed)

        # Keep clearing in this program rather than relying on process teardown. It
        # guarantees a controlled stop or finite one-shot sort does not leave the last
        # frame displayed indefinitely.
        try:
            mode = self.args.mode
            if mode is None:
                mode = "interactive" if sys.stdin.isatty() else "random"

            if mode == "interactive":
                self._run_interactive(length, rng)
            else:
                self._run_automatic(mode, length, rng)
        finally:
            self._offset_canvas.Clear()
            self._offset_canvas = self.matrix.SwapOnVSync(self._offset_canvas)

    def _run_interactive(self, length: int, rng: random.Random) -> None:
        """Preserve the original numbered terminal menu for direct CLI users."""

        while True:
            sort_count = len(SORTS)
            numbered = [
                f"{index + 1} for {display_name.lower()}"
                for index, (_, display_name, _) in enumerate(SORTS)
            ]
            extra = [
                f"{sort_count+1} to loop through all sorts",
                f"{sort_count+2} for continuous random sorts",
                "0 to quit",
            ]
            print("Choose sort: " + ", ".join(numbered + extra) + ": ", end='', flush=True)
            try:
                user_input = int(input().strip())
            except ValueError:
                print("Invalid input. Please enter a number.")
                continue
            if user_input == 0:
                print("Exiting...")
                break
            if 1 <= user_input <= sort_count:
                sort_id, _, _ = SORTS[user_input - 1]
                self._run_named_sort(sort_id, length, rng)
            elif user_input == sort_count + 1:
                for sort_id, _, _ in SORTS:
                    self._run_named_sort(sort_id, length, rng)
            elif user_input == sort_count + 2:
                # Continuous random sorts
                try:
                    while True:
                        sort_id, _, _ = rng.choice(SORTS)
                        self._run_named_sort(sort_id, length, rng)
                except KeyboardInterrupt:
                    print("\nStopped continuous random sorts.")
            else:
                print("Invalid input. Please try again.")

    def _run_automatic(self, mode: str, length: int, rng: random.Random) -> None:
        """Run without stdin so systemd and the web supervisor cannot deadlock.

        ``max_runs`` counts completed individual sorts. Zero means continuous for
        ``all`` and ``random``. ``single`` intentionally performs exactly one sort so
        it can be used as a finite diagnostic command.
        """

        if mode == "single":
            self._run_named_sort(self.args.sort, length, rng)
            return

        completed = 0
        while self.args.max_runs == 0 or completed < self.args.max_runs:
            if mode == "all":
                candidates = [item[0] for item in SORTS]
            elif mode == "random":
                candidates = [rng.choice(SORTS)[0]]
            else:  # argparse/manifest validation should make this unreachable.
                raise ValueError(f"unsupported sort mode: {mode}")

            for sort_id in candidates:
                if self.args.max_runs and completed >= self.args.max_runs:
                    return
                self._run_named_sort(sort_id, length, rng)
                completed += 1

    def _run_named_sort(
        self,
        sort_id: str,
        length: int,
        rng: random.Random,
    ) -> None:
        """Run one allowlisted sort with a fresh array and shared draw settings."""

        try:
            _, _, method_name = next(item for item in SORTS if item[0] == sort_id)
        except StopIteration as exc:
            raise ValueError(f"unknown sort: {sort_id}") from exc
        array = generate_array(length, rng)
        getattr(self, method_name)(array, length)

    def _draw_frame(self, array, groups) -> None:
        """Draw one frame and retain the back buffer returned by VSync.

        The rgbmatrix binding returns the canvas that should be used for the next
        frame. Keeping it on the visualization object avoids callbacks accidentally
        drawing into a stale/front buffer after the first swap.
        """

        self._offset_canvas = draw_array(
            array,
            self._offset_canvas,
            self.matrix.width,
            self.matrix,
            groups=groups,
            sleep_delay=self.args.delay,
        )

    def do_insertion_sort(self, array, length):
        sorts.insertion_sort(
            array,
            self._draw_frame,
        )

    def do_bubble_sort(self, array, length):
        sorts.bubble_sort(
            array,
            self._draw_frame,
        )

    def do_selection_sort(self, array, length):
        sorts.selection_sort(
            array,
            self._draw_frame,
        )

    def do_quick_sort(self, array, length):
        sorts.quick_sort(
            array,
            self._draw_frame,
        )


def generate_array(length: int, rng: random.Random | None = None) -> list[int]:
    """Generate a sortable array using an injectable RNG for repeatable runs."""

    source = rng or random
    return [source.randint(1, length) for _ in range(length)]


def draw_array(
    arr,
    canvas,
    length,
    matrix,
    groups=None,
    base_color=(150, 5, 5),
    sleep_delay=0.05,
):
    """
      - groups: list of tuples (indices_list, (r, g, b)).
                Each indices_list is a list of column indices to color with the given RGB.
                If an index appears in multiple groups, the last group's color wins.
                Out-of-bounds indices are ignored.
    """
    # Clear previous frame to avoid ghosting between updates.
    canvas.Clear()

    # Build an index -> color map from groups so we can do O(1) lookups per column.
    # Note: if the same index appears in multiple groups, the later group's color overrides earlier ones.
    # Out-of-range indices are ignored to avoid errors.
    index_color = {}
    if groups:
        for idx_list, color in groups:
            for idx in idx_list:
                if 0 <= idx < length:
                    index_color[idx] = color

    for i in range(length):
        # Pick the color for this column; fall back to base_color if not highlighted.
        color = index_color.get(i, base_color)
        # Clamp to the canvas height to prevent drawing past the panel (arr[i] might exceed panel height).
        col_height = min(arr[i], canvas.height)
        for j in range(col_height):
            # Draw bottom-up so j=0 is the bottom row (y = height-1), increasing upwards.
            canvas.SetPixel(i, canvas.height - 1 - j, *color)
    time.sleep(sleep_delay)
    canvas = matrix.SwapOnVSync(canvas)
    return canvas

def _request_service_exit(_signum, _frame) -> None:
    """Route supervisor SIGTERM through VisualizeSorts' ``finally`` cleanup.

    ``SystemExit`` is intentional: the interactive random-mode menu catches
    ``KeyboardInterrupt`` so Ctrl+C can return to its menu, but a service stop must
    exit rather than become stuck waiting for the next menu selection.
    """

    raise SystemExit(0)


if __name__ == "__main__":
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _request_service_exit)
    visualize_sorts = VisualizeSorts()
    try:
        if not visualize_sorts.process():
            visualize_sorts.print_help()
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
