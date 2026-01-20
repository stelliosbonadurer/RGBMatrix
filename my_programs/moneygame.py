#!/usr/bin/env python

from samplebase import SampleBase
import time
import random
import numpy as np


class MoneyGame(SampleBase):
    def __init__(self, *args, **kwargs):
        super(MoneyGame, self).__init__(*args, **kwargs)

    def run(self):
        offset_canvas = self.matrix.CreateFrameCanvas()
        num_people = 32  # Match matrix dimensions
        initial_money = 6
        
        # Initialize people array - everyone starts with 6 money
        people = [initial_money] * num_people
        
        # Tick counter for loading wheel (0-3 for 4 positions)
        tick = 0
        
        # Main game loop
        while True:
            # Perform one money exchange
            people = exchange_money(people)
            
            # Draw the current state
            draw_people(self, people, offset_canvas, tick)
            
            # Increment tick for loading wheel
            tick = (tick + 1) % 4
            
            # Small delay between exchanges
            time.sleep(0.05)


def exchange_money(people):
    """
    One exchange: randomly pick two people, pool their money,
    then split it randomly (one gets random percentage, other gets the rest).
    """
    num_people = len(people)
    person_a = random.randint(0, num_people - 1)
    person_b = random.randint(0, num_people - 1)
    
    # Make sure we pick two different people
    if person_a != person_b:
        # Pool their money
        total = people[person_a] + people[person_b]
        
        # Random split between 0 and 1
        split = random.random()
        
        # Distribute the pooled money
        people[person_a] = total * split
        people[person_b] = total * (1 - split)
    
    return people


def draw_people(self, people, canvas, tick):
    """
    Visualize the money distribution on the matrix.
    Each column represents one person's wealth as a vertical bar.
    """
    canvas.Clear()
    width = self.matrix.width
    height = self.matrix.height
    
    # Draw each person as a column
    for person_idx in range(min(len(people), width)):
        money = people[person_idx]
        
        # Direct mapping: 1 money = 1 pixel height, capped at matrix height
        bar_height = int(min(money, height))
        
        # Draw the bar from bottom up with a single color
        for y in range(height - bar_height, height):
            canvas.SetPixel(person_idx, y, 150, 0, 0)
    
    # Draw loading wheel in top right corner (2x2 area)
    # Positions cycle: top-right -> bottom-right -> bottom-left -> top-left
    wheel_positions = [
        (width - 1, 0),      # Top right
        (width - 1, 1),      # Down one
        (width - 2, 1),      # Left one
        (width - 2, 0)       # Up one
    ]
    x, y = wheel_positions[tick]
    canvas.SetPixel(x, y, 0, 0, 100)  # Blue dot
    
    canvas = self.matrix.SwapOnVSync(canvas)


# Main function
if __name__ == "__main__":
    money_game = MoneyGame()
    if (not money_game.process()):
        money_game.print_help()
