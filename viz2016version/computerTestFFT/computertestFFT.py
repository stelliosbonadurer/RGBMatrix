import pyaudio

import scipy
import scipy.fftpack
from scipy import fft, arange, ifft
from numpy import sin, linspace, pi
import numpy as np
import pylab
import time

import matplotlib.pyplot as plt


RATE = 44100
CHUNK = int(RATE/20)

def main():
   p = pyaudio.PyAudio()
   stream=p.open(format=pyaudio.paInt16,channels=1,rate=RATE,input=True,
              frames_per_buffer=CHUNK)

   for i in range(10):
      soundplot(stream)



   stream.stop_stream()
   stream.close()
   p.terminate()

def soundplot(stream):
   t1 = time.time()
   data = np.fromstring(stream.read(CHUNK),dtype=np.int16)
   pylab.plot(data)
   
   pylab.grid()
   pylab.axis([0,len(data),-2**16/2,2**16/2])
   pylab.savefig("03.png",dpi=50)
   pylab.close('all')
   print("took %.02f ms"%((time.time()-t1)*1000))


if __name__ == "__main__": main()