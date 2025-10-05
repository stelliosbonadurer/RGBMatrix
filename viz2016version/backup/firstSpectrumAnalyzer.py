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

   print "initializing matrix"
   matrix = Adafruit_RGBmatrix(32,1)

   image = Image.new("1", (32,32))
   draw = ImageDraw.Draw(image)

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
   print "length " +str(len(xs))


   for i in range(600):
      time.sleep(.1)
      toPrint = ""
      xPrint = ""
      yPrint = ""
      xs,ys=AA.fft()
      for x in range(len(xs)):
         if x%2 == 1:
            toPrint = toPrint+"  "+"{:.0f}".format(xs[x])
            yPrint = yPrint+" "+"{:.0f}".format(ys[x])
            xPrint = xPrint+" "+"{:^4d}".format(x)
      print toPrint
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


   

if __name__ == '__main__':
   main()

