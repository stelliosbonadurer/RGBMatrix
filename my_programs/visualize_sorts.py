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

        array = generate_array(length)
        while(True):

            offset_canvas = draw_array(array, offset_canvas, length, self.matrix)

            offset_canvas = self.matrix.SwapOnVSync(offset_canvas)

            # Call the insertion sort with a draw callback
            sorts.insertion_sort(array, lambda arr: draw_array(arr, offset_canvas, length, self.matrix))
            


def generate_array(length):
    #Generate random array of integers between 1 and matrix size
    return [random.randint(1, length) for _ in range(length)]

def draw_array(arr, canvas, length, matrix):
    canvas.Clear()
    for i in range(length):
        for j in range(arr[i]):
            canvas.SetPixel(i, canvas.height - 1 - j, 150, 5, 5)
    
    time.sleep(0.1)  # Pause to visualize the current state
    canvas = matrix.SwapOnVSync(canvas)
    return canvas

# Main function
if __name__ == "__main__":
    visualize_sorts = VisualizeSorts()
    if (not visualize_sorts.process()):
        visualize_sorts.print_help()



