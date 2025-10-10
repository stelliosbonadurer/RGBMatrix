#!/usr/bin/env python
from array import array
from tkinter import font
from samplebase import SampleBase
from rgbmatrix import graphics
import sorts
import random
import time

# Registry of available sorts: (Display Name, VisualizeSorts method name)
SORTS = [
    ("Insertion", "do_insertion_sort"),
    ("Bubble", "do_bubble_sort"),
    ("Selection", "do_selection_sort"),
    ("Quick", "do_quick_sort"),
]

class VisualizeSorts(SampleBase):
    def __init__(self, *args, **kwargs):
        super(VisualizeSorts, self).__init__(*args, **kwargs)

    #Main Program Run
    def run(self):
        offset_canvas = self.matrix.CreateFrameCanvas()
        length = self.matrix.width

        while True:
            # Build dynamic menu from SORTS + extra options
            sort_count = len(SORTS)
            numbered = [f"{i+1} for {name.lower()}" for i, (name, _) in enumerate(SORTS)]
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
            array = generate_array(length)
            if 1 <= user_input <= sort_count:
                # Run selected sort
                _, method_name = SORTS[user_input - 1]
                getattr(self, method_name)(array, offset_canvas, length)
            elif user_input == sort_count + 1:
                # Loop through all sorts once
                for _, method_name in SORTS:
                    getattr(self, method_name)(generate_array(length), offset_canvas, length)
            elif user_input == sort_count + 2:
                # Continuous random sorts
                try:
                    while True:
                        _, method_name = random.choice(SORTS)
                        getattr(self, method_name)(generate_array(length), offset_canvas, length)
                except KeyboardInterrupt:
                    print("\nStopped continuous random sorts.")
            else:
                print("Invalid input. Please try again.")


    def do_insertion_sort(self, array, offset_canvas, length):
        array = generate_array(length)
        sorts.insertion_sort(
            array,
            lambda arr, groups: draw_array(
                arr, offset_canvas, length, self.matrix, groups=groups
            )
        )

    def do_bubble_sort(self, array, offset_canvas, length):
        array = generate_array(length)
        sorts.bubble_sort(
            array,
            lambda arr, groups: draw_array(
                arr, offset_canvas, length, self.matrix, groups=groups
            )
        )

    def do_selection_sort(self, array, offset_canvas, length):
        array = generate_array(length)
        sorts.selection_sort(
            array,
            lambda arr, groups: draw_array(
                arr, offset_canvas, length, self.matrix, groups=groups
            )
        )
    def do_quick_sort(self, array, offset_canvas, length):
        array = generate_array(length)
        sorts.quick_sort(
            array,
            lambda arr, groups: draw_array(
                arr, offset_canvas, length, self.matrix, groups=groups
            )
        )

def generate_array(length):
    #Generate random array of integers between 1 and matrix size
    return [random.randint(1, length) for _ in range(length)]

def draw_array(arr, canvas, length, matrix, groups=None, base_color=(150, 5, 5)):
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
    time.sleep(0.7)
    canvas = matrix.SwapOnVSync(canvas)
    return canvas

# Main function
if __name__ == "__main__":
    visualize_sorts = VisualizeSorts()
    if (not visualize_sorts.process()):
        visualize_sorts.print_help()



