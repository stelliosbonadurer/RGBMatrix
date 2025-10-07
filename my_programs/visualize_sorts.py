#!/usr/bin/env python
from array import array
from tkinter import font
from samplebase import SampleBase
from rgbmatrix import graphics
import sorts
import random
import time

class VisualizeSorts(SampleBase):
    def __init__(self, *args, **kwargs):
        super(VisualizeSorts, self).__init__(*args, **kwargs)

    #Main Program Run
    def run(self):
        offset_canvas = self.matrix.CreateFrameCanvas()
        length = self.matrix.width

        while True:
            print("Press b for bubble sort, s for selection sort, i for insertion sort, q for quick sort, l to loop through all sorts (or e to quit): ", end='', flush=True)
            user_input = input().strip().lower()
            if user_input == 'e':
                print("Exiting...")
                break
            array = generate_array(length)
            if user_input == 'i':
                self.do_insertion_sort(array, offset_canvas, length)
            elif user_input == 'b':
                self.do_bubble_sort(array, offset_canvas, length)
            elif user_input == 's':
                self.do_selection_sort(array, offset_canvas, length)
            elif user_input == 'q':
                self.do_quick_sort(array, offset_canvas, length)
            elif user_input == 'l':
                self.do_insertion_sort(array.copy(), offset_canvas, length)
                self.do_bubble_sort(array.copy(), offset_canvas, length)
                self.do_selection_sort(array.copy(), offset_canvas, length)
                self.do_quick_sort(array.copy(), offset_canvas, length)
            else:
                print("Invalid input. Please try again.")


    def do_insertion_sort(self, array, offset_canvas, length):
        array = generate_array(length)
        sorts.insertion_sort(
            array,
            lambda arr, highlight_indices=None, pivot_index=None: draw_array(
                arr, offset_canvas, length, self.matrix,
                highlight_indices=highlight_indices, highlight_color=(255, 255, 0),
                pivot_index=pivot_index, pivot_color=(0, 0, 255)
            )
        )

    def do_bubble_sort(self, array, offset_canvas, length):
        array = generate_array(length)
        sorts.bubble_sort(
            array,
            lambda arr, highlight_indices=None, pivot_index=None: draw_array(
                arr, offset_canvas, length, self.matrix,
                highlight_indices=highlight_indices, highlight_color=(0, 255, 0),
                pivot_index=pivot_index, pivot_color=(0, 0, 255)
            )
        )

    def do_selection_sort(self, array, offset_canvas, length):
        array = generate_array(length)
        sorts.selection_sort(
            array,
            lambda arr, highlight_indices=None, pivot_index=None: draw_array(
                arr, offset_canvas, length, self.matrix,
                highlight_indices=highlight_indices, highlight_color=(0, 0, 255),
                pivot_index=pivot_index, pivot_color=(255, 255, 0)
            )
        )
    def do_quick_sort(self, array, offset_canvas, length):
        sorts.quick_sort(
            array,
            0,
            len(array) - 1,
            lambda arr, highlight_indices=None, pivot_index=None: draw_array(
                arr, offset_canvas, length, self.matrix,
                highlight_indices=highlight_indices, highlight_color=(255, 255, 0),
                pivot_index=pivot_index, pivot_color=(0, 0, 255)
            )
        )

def generate_array(length):
    #Generate random array of integers between 1 and matrix size
    return [random.randint(1, length) for _ in range(length)]

def draw_array(arr, canvas, length, matrix, highlight_indices=None, highlight_color=(0, 255, 0), pivot_index=None, pivot_color=(0, 0, 255)):
    #Drawing algorithm to visualize the array on the LED matrix
    #Accepts optional highlight indices and pivot index for better visualization
    canvas.Clear()
    for i in range(length):
        # Default color
        color = (150, 5, 5)
        if highlight_indices and i in highlight_indices:
            color = highlight_color
        if pivot_index is not None and i == pivot_index:
            color = pivot_color
        for j in range(arr[i]):
            canvas.SetPixel(i, canvas.height - 1 - j, *color)
    time.sleep(0.7)
    canvas = matrix.SwapOnVSync(canvas)
    return canvas

# Main function
if __name__ == "__main__":
    visualize_sorts = VisualizeSorts()
    if (not visualize_sorts.process()):
        visualize_sorts.print_help()



