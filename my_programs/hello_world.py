#!/usr/bin/env python
from tkinter import font
from samplebase import SampleBase
from rgbmatrix import graphics

class HelloWorld(SampleBase):
    def __init__(self, *args, **kwargs):
        super(HelloWorld, self).__init__(*args, **kwargs)

    def run(self):

        font = graphics.Font()
        font.LoadFont("../../../fonts/5x7.bdf")
        #font.LoadFont("/home/stelliosbonadurer/rpi-rgb-led-matrix/fonts/6x12.bdf")  # Path to a BDF font file
        color = graphics.Color(138, 112, 207)

        
        offset_canvas = self.matrix.CreateFrameCanvas()
        while True:
            for x in range(0, self.matrix.width):
                for y in range(0, self.matrix.height - x):
                    offset_canvas.SetPixel(x, y, 154, 29, 57)

            graphics.DrawText(offset_canvas, font, 4, 9, color, "Hello")
            graphics.DrawText(offset_canvas, font, 4, 22, color, "World!")



            offset_canvas = self.matrix.SwapOnVSync(offset_canvas)


# Main function
if __name__ == "__main__":
    hello_world = HelloWorld()
    if (not hello_world.process()):
        hello_world.print_help()
