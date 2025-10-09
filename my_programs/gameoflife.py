#!/usr/bin/env python

from samplebase import SampleBase
import time
from rgbmatrix import graphics
import random



class GameofLife(SampleBase):
    def __init__(self, *args, **kwargs):
        super(GameofLife, self).__init__(*args, **kwargs)

    def run(self):

        offset_canvas = self.matrix.CreateFrameCanvas()
        width = self.matrix.width
        height = self.matrix.height
        # Initialize a random grid
        grid = [[random.choice([0, 1]) for _ in range(width)] for _ in range(height)]
        while True:
            draw_grid(self,grid,offset_canvas)
            grid = update_grid(self, grid)
            time.sleep(1)
        
        #if grid is empty, reinitialize
        if all(cell == 0 for row in grid for cell in row):
            grid = [[random.choice([0, 1]) for _ in range(width)] for _ in range(height)]

def draw_grid(self, grid, canvas):
    canvas.Clear()
    for y, row in enumerate(grid):
        for x, cell in enumerate(row):
            color = (255, 255, 255) if cell == 1 else (0, 0, 0)
            canvas.SetPixel(x, y, *color)
    canvas = self.matrix.SwapOnVSync(canvas)

def update_grid(self, grid):
    height = len(grid)
    width = len(grid[0])
    new_grid = [[0 for _ in range(width)] for _ in range(height)]

    #Update grid, uses toroidal boundaries. (Standard for Conway's Game of Life)
    for y in range(height):
        for x in range(width):
            alive_neighbors = sum(
                grid[(y + dy) % height][(x + dx) % width]
                for dy in [-1, 0, 1]
                for dx in [-1, 0, 1]
                if (dy != 0 or dx != 0)
            )
            if grid[y][x] == 1:
                if alive_neighbors < 2 or alive_neighbors > 3:
                    new_grid[y][x] = 0
                else:
                    new_grid[y][x] = 1
            else:
                if alive_neighbors == 3:
                    new_grid[y][x] = 1
    return new_grid

def decode_rle(rle_string):
    """
    Decode an RLE (Run-Length Encoded) string into a 2D grid for Game of Life.
    Returns a list of lists where 1 is alive, 0 is dead.
    """
    lines = rle_string.strip().split('$')
    grid = []
    for line in lines:
        row = []
        i = 0
        while i < len(line):
            if line[i].isdigit():
                # Parse the number
                num_str = ''
                while i < len(line) and line[i].isdigit():
                    num_str += line[i]
                    i += 1
                num = int(num_str)
            else:
                num = 1
            if i < len(line):
                char = line[i]
                if char == 'b':
                    row.extend([0] * num)
                elif char == 'o':
                    row.extend([1] * num)
                i += 1
        grid.append(row)
    return grid

def place_pattern_on_grid(grid, pattern, start_x=0, start_y=0):
    """
    Place a decoded RLE pattern onto the grid at the specified position.
    Assumes pattern is smaller than grid.
    """
    for y, row in enumerate(pattern):
        for x, cell in enumerate(row):
            if start_y + y < len(grid) and start_x + x < len(grid[0]):
                grid[start_y + y][start_x + x] = cell
    return grid


# Main function
if __name__ == "__main__":
    gameoflife = GameofLife()
    if (not gameoflife.process()):
        gameoflife.print_help()




