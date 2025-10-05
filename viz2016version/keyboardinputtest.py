import time
import pyaudio
from rgbmatrix import Adafruit_RGBmatrix
from Tkinter import *



main = Tk()
def leftKey(event):
    x--

def rightKey(event):
    x++

frame = Frame(main, width=100, height=100)
main.bind('<Left>', leftKey)
main.bind('<Right>', rightKey)
frame.pack()
main.mainloop()
#import pim

matrix=Adafruit_RGBmatrix(32,1)

x=16
y=16

up = raw_input('w')
down = raw_input('s')
right = raw_input('d')
left = raw_input('a')
for i in range(100):
   matrix.Fill(0x000000)

   if up:
      y -= 1
   if down:
      y += 1
   if left:
      x -= 1
   if right:
      x += 1 



   matrix.SetPixel(x,y,255,255,255)
   time.sleep(.5)
