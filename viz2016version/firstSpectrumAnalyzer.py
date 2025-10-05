import pyaudio

import scipy
import scipy.fftpack
from scipy import fft, arange, ifft
from numpy import sin, linspace, pi

from random import randint

import Image
import ImageDraw
import time
from rgbmatrix import Adafruit_RGBmatrix
from AudioAnalyzer import *

import sys

def main():
   print "booting: "

   print "testing map 10 f:5,15 t: 1,3"
   print map4(10,5,15,1,3)

   print "initializing matrix"
   matrix = Adafruit_RGBmatrix(32,1)

   image = Image.new("1", (32,32))
   draw = ImageDraw.Draw(image)

   draw.line((0,0,32,32),fill=1)
   matrix.SetImage(image.im.id,0,0)
   time.sleep(1)

   print "matrix initialized"


   print "initialize AudioAnalyzer"
   AA = AudioAnalyzer()
   print "initializaton Complete"
   print "setup AudioAnalyzer"
   AA.setup()
   print "setup Complete:"
   print "Start Continuous Recording"
   AA.continuousStart()



   xs,ys=AA.fft()
   print "blength " +str(len(xs))

   maxy = 0;
   while(True):
   #for i in range(600):
      time.sleep(.03)
      #reset matrix
      matrix.Clear()
      image = Image.new("1", (32,32))
      draw = ImageDraw.Draw(image)
      
      xPrint = ""
      yPrint = ""
      xs,ys=AA.fft()
      
      for x in range(32):
         drawVal = ys[int(len(ys)*x/32)]
         if drawVal > maxy:
            maxy = drawVal
         #maxy*=.99
         yPrint = yPrint+" "+"{:.0f}".format(drawVal)
         xPrint = xPrint+" "+"{:^4d}".format(x)
         #print drawVal
         draw.line((x,0,x,drawVal),fill=1)
      matrix.SetImage(image.im.id,0,0)

      print yPrint
      print xPrint


            

   AA.continuousEnd()

   sys.exit()

   AA.close()
   sys.exit()

   # for i in range(20):
   #    matrix.Clear()
   #    image = Image.new("1",(32,32))
   #    draw = ImageDraw.Draw(image)
   #    for n in range(32):
   #       draw.line((n,32,n,randint(0,32)),fill=1)
   #    matrix.SetImage(image.im.id,0,0)
   #    time.sleep(.1)

#map val from between(f1,f2) to (t1,t2)
def map4(val, f1,f2,t1,t2):
   return ((val-f1)*(t2-t1)/(f2-f1))+t1
def map(val,f,t):
   return (val/f)*t


   

if __name__ == '__main__':
   main()

