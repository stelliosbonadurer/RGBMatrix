import math
#!/usr/bin/env python
from tkinter import font
from samplebase import SampleBase
from rgbmatrix import graphics
import random
import time

class LGrid(SampleBase):
    def __init__(self, *args, **kwargs):
        super(LGrid, self).__init__(*args, **kwargs)

    def run(self):
        
        offset_canvas = self.matrix.CreateFrameCanvas()
        while True:

            # graphics.DrawText(offset_canvas, font, 5, 15, color, "Hello, World!")
            # for x in range(0, self.matrix.width):
            #     for y in range(0, x):
            #         offset_canvas.SetPixel(x, y, 100, 0, 0)


            recurse(0,0,offset_canvas.width,offset_canvas.height,random.randint(0,offset_canvas.width-1),random.randint(0,offset_canvas.height-1),offset_canvas)
            offset_canvas = self.matrix.SwapOnVSync(offset_canvas)
            # for x in range (0,offset_canvas.width):
            #     for y in range(0,offset_canvas.height):
            #         recurse(0,0,offset_canvas.width-1,offset_canvas.height-1,x,y,offset_canvas)

            #         offset_canvas = self.matrix.SwapOnVSync(offset_canvas)
            #         time.sleep(1000)

            
            time.sleep(1000)



def recurse(x1,y1,x2,y2,a,b,canvas):
    midX = math.floor((x1+x2)/2)
    midY = math.floor((y1+y2)/2)    
    #print("Midpoint of region: ", (x1,y1), " to ", (x2,y2), "is ", (midX,midY))

    #Base case, if the region is 2x2 or smaller, fill in the square
    currentColor = (random.randint(0,255),random.randint(0,255),random.randint(0,255))
    if (x2 - x1 <= 1):
        print("Base case reached, filling in square with bounds ", (x1,y1), " to ", (x2,y2))
        #currentColor = (random.randint(0,255),random.randint(0,255),random.randint(0,255))
        for i in range(x1,x2+1):
            for j in range(y1,y2+1):
                if not (i == a and j == b):
                    canvas.SetPixel(i,j,*currentColor)
        return

    currentColor = (random.randint(0,255),random.randint(0,255),random.randint(0,255))

    # Recurse into all four quadrants, always
    recurse(x1, y1, midX, midY, a, b, canvas)  # Top left
    recurse(midX + 1, y1, x2, midY, a, b, canvas)  # Top right
    recurse(x1, midY + 1, midX, y2, a, b, canvas)  # Bottom left
    recurse(midX + 1, midY + 1, x2, y2, a, b, canvas)  # Bottom right

    #which quadrant is (a,b) in?
    ab_in_quad = -1
    #if a,b is in the top left quadrant
    # if a >= x1 and a <= midX and b >= y1 and b <= midY:
    #     ab_in_quad = 0
    #     #if a, b is in the top left quadrant, recurse with a,b continuing
    #     recurse(x1,y1,midX,midY,a,b,canvas)
        
    # #if a,b is in the top right quadrant
    # elif a > midX and a <= x2 and b >= y1 and b <= midY:
    #     ab_in_quad = 1
    #     #if a, b is in the top right quadrant, recurse with a,b continuing
    #     recurse(midX+1,y1,x2,midY,a,b,canvas)
    # #if a,b is in the bottom left quadrant
    # elif a >= x1 and a <= midX and b > midY and b <= y2:
    #     ab_in_quad = 2
    #     #if a, b is in the bottom left quadrant, recurse with a,b continuing
    #     recurse(x1,midY+1,midX,y2,a,b,canvas)
    # #if a,b is in the bottom right quadrant
    # elif a > midX and a <= x2 and b > midY and b <= y2:
    #     ab_in_quad = 3
    #     #if a, b is in the bottom right quadrant, recurse with a,b continuing
    #     recurse(midX+1,midY+1,x2,y2,a,b,canvas)


    # #Do recursion calls on each other quadrant, with the midpoint of the region as the new a,b
    # if not ab_in_quad == 0:
    #     #Top Left Quadrant
    #     recurse(x1,y1,midX,midY,midX,midY,canvas)
    #     print("Recurse top left on square with bounds  ", (x1,y1), " to ", (midX,midY), " with a,b = ", (midX,midY))
    # if not ab_in_quad == 1:
    #     #Top Right Quadrant
    #     recurse(midX+1,y1,x2,midY,midX+1,midY,canvas)
    # if not ab_in_quad == 2:
    #     #Bottom Left Quadrant
    #     recurse(x1,midY+1,midX,y2,midX,midY+1,canvas)
    # if not ab_in_quad == 3:
    #     #Bottom Right Quadrant
    #     recurse(midX+1,midY+1,x2,y2,midX+1,midY+1,canvas)   


# Main function
if __name__ == "__main__":
    l_grid = LGrid()
    if not l_grid.process():
        l_grid.print_help()
