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
            print("Press b for bubble sort, s for selection sort, i for insertion sort (or q to quit): ", end='', flush=True)
            user_input = input().strip().lower()
            if user_input == 'q':
                print("Exiting...")
                break
            array = generate_array(length)
            if user_input == 'i':
                sorts.insertion_sort(
                    array,
                    lambda arr, highlight_indices=None, pivot_index=None: draw_array(
                        arr, offset_canvas, length, self.matrix,
                        highlight_indices=highlight_indices, highlight_color=(255, 255, 0),
                        pivot_index=pivot_index, pivot_color=(0, 0, 255)
                    )
                )
            elif user_input == 'b':
                sorts.bubble_sort(
                    array,
                    lambda arr, highlight_indices=None, pivot_index=None: draw_array(
                        arr, offset_canvas, length, self.matrix,
                        highlight_indices=highlight_indices, highlight_color=(0, 255, 0),
                        pivot_index=pivot_index, pivot_color=(0, 0, 255)
                    )
                )
            elif user_input == 's':
                sorts.selection_sort(
                    array,
                    lambda arr, highlight_indices=None, pivot_index=None: draw_array(
                        arr, offset_canvas, length, self.matrix,
                        highlight_indices=highlight_indices, highlight_color=(0, 0, 255),
                        pivot_index=pivot_index, pivot_color=(255, 255, 0)
                    )
                )
            else:
                print("Invalid input. Please try again.")


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



