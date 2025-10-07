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
            A = random.randint(0,offset_canvas.width-1)
            B = random.randint(0,offset_canvas.height-1)
            print("Special pixel at: ", (A,B))
            recurse(0,0,offset_canvas.width-1,offset_canvas.height-1,A,B,offset_canvas)
            offset_canvas.SetPixel(A,B,0,0,0)
            offset_canvas = self.matrix.SwapOnVSync(offset_canvas)
            time.sleep(1000)

def generate_color():
    #return (random.randint(0,255),random.randint(0,255),random.randint(0,255))
    colors = [
        (255, 0, 0),    # Red
        (0, 255, 0),    # Green
        (0, 0, 255),    # Blue
        (255, 255, 0),  # Yellow
        (255, 0, 255)   # Magenta
    ]
    return random.choice(colors)

def recurse(x1,y1,x2,y2,a,b,canvas, priorcolor = (0,0,0)):
    midX = math.floor((x1+x2)/2)
    midY = math.floor((y1+y2)/2)    
    #print("Midpoint of region: ", (x1,y1), " to ", (x2,y2), "is ", (midX,midY))

    #Base case, if the region is 2x2 or smaller, fill in the square
    currentColor = generate_color()
    if (x2 - x1 <= 1):
        #print("Base case reached, filling in square with bounds ", (x1,y1), " to ", (x2,y2))
        currentColor = generate_color()
        for i in range(x1,x2+1):
            for j in range(y1,y2+1):
                if not (i == a and j == b):
                    canvas.SetPixel(i,j,*currentColor)

        canvas.SetPixel(a,b,*priorcolor)
        return
    if(x2-x1 == 4):
        print("Region ", (x1,y1), " to ", (x2,y2))
        return

    currentColor = generate_color()

    # Recurse into all four quadrants, always
    # recurse(x1, y1, midX, midY, a, b, canvas)  # Top left
    # recurse(midX + 1, y1, x2, midY, a, b, canvas)  # Top right
    # recurse(x1, midY + 1, midX, y2, a, b, canvas)  # Bottom left
    # recurse(midX + 1, midY + 1, x2, y2, a, b, canvas)  # Bottom right

    #which quadrant is (a,b) in?
    ab_in_quad = -1
    nextcolor = generate_color()

    #Do quadrant that contains a,b
    #if a,b is in the top left quadrant
    if a >= x1 and a <= midX and b >= y1 and b <= midY:
        ab_in_quad = 0
        #if a, b is in the top left quadrant
        recurse(x1,y1,midX,midY,a,b,canvas,nextcolor)
    #if a,b is in the top right quadrant
    elif a > midX and a <= x2 and b >= y1 and b <= midY:
        ab_in_quad = 1
        #if a, b is in the top right quadrant
        recurse(midX+1,y1,x2,midY,a,b,canvas,nextcolor)
    #if a,b is in the bottom left quadrant
    elif a >= x1 and a <= midX and b > midY and b <= y2:
        ab_in_quad = 2
        #if a, b is in the bottom left quadrant
        recurse(x1,midY+1,midX,y2,a,b,canvas,nextcolor)
    #if a,b is in the bottom right quadrant
    elif a > midX and a <= x2 and b > midY and b <= y2:
        ab_in_quad = 3
        #if a, b is in the bottom right quadrant
        recurse(midX+1,midY+1,x2,y2,a,b,canvas,nextcolor)


    nextcolor= generate_color()
    if not ab_in_quad == 0:
        #Top Left Quadrant
        recurse(x1,y1,midX,midY,midX,midY,canvas,nextcolor)
    if not ab_in_quad == 1:
        #Top Right Quadrant

        recurse(midX+1,y1,x2,midY,midX+1,midY,canvas,nextcolor)
    if not ab_in_quad == 2:
        #Bottom Left Quadrant

        recurse(x1,midY+1,midX,y2,midX,midY+1,canvas,nextcolor)
    if not ab_in_quad == 3:
        #Bottom Right Quadrant

        recurse(midX+1,midY+1,x2,y2,midX+1,midY+1,canvas,nextcolor)   


# Main function
if __name__ == "__main__":
    l_grid = LGrid()
    if not l_grid.process():
        l_grid.print_help()
