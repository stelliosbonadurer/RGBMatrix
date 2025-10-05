import numpy
import scipy
import struct
import pyaudio
import threading
import pylab
import struct

class AudioAnalyzer:


   def __init__(self):
      self.RATE=32000
      self.BUFFERSIZE=1024
      self.secToRecord=.1
      self.threadsDieNow=False
      self.newAudio = False
      self.CHANNELS = 1

   def setup(self):
      print("...")
      self.buffersToRecord=int(self.RATE*self.secToRecord/self.BUFFERSIZE)
      if self.buffersToRecord==0: self.buffersToRecord=1
      self.samplesToRecord=int(self.BUFFERSIZE*self.buffersToRecord)
      self.chunksToRecord=int(self.samplesToRecord/self.BUFFERSIZE)
      self.secPerPoint=1.0/self.RATE
      print "  init PyAudio"
      self.p = pyaudio.PyAudio()
      print "  make Stream"
      self.inStream = self.p.open(format=pyaudio.paInt16,channels=self.CHANNELS,rate=self.RATE,input=True,frames_per_buffer=self.BUFFERSIZE)
     
      self.xsBuffer=numpy.arange(self.BUFFERSIZE)*self.secPerPoint
      self.xs=numpy.arange(self.chunksToRecord*self.BUFFERSIZE)*self.secPerPoint
      self.audio=numpy.empty((self.chunksToRecord*self.BUFFERSIZE),dtype=numpy.int16) 

   def downsample(self,data,mult):
      """Given 1D data, return the binned average."""
      overhang=len(data)%mult
      if overhang: data=data[:-overhang]
      data=numpy.reshape(data,(len(data)/mult,mult))
      data=numpy.average(data,1)
      return data    

   def close(self):
      self.p.close(self.inStream)

   #get a single buffer worth of audio as array
   def getAudio(self):
      audioString=self.inStream.read(self.BUFFERSIZE)
      return numpy.fromstring(audioString,dtype=numpy.int16)

   #record secToRecord seconds of audio
   def record(self,forever=True):
      while True:
         if self.threadsDieNow: break
         for i in range(self.chunksToRecord):
            self.audio[i*self.BUFFERSIZE:(i+1)*self.BUFFERSIZE]=self.getAudio()
         self.newAudio=True
         if forever==False: break

   def continuousStart(self):
      self.t = threading.Thread(target=self.record)
      self.t.start()

   def continuousEnd(self):
      self.threadsDieNow=True

   def fft(self,data=None,trimBy=10,logScale=False,divBy=100):
      if data==None:
         data=self.audio.flatten()
      left,right = numpy.split(numpy.abs(numpy.fft.fft(data)),2)
      print "51"
      ys=numpy.add(left,right[::-1])
      if logScale:
         ys = numpy.multiply(20,numpy.log10(ys))
      xs = numpy.arange(self.BUFFERSIZE/2,dtype=float)
      if trimBy:
         i = int((self.BUFFERSIZE/2)/trimBy)
         ys=ys[:i]
         xs=xs[:i]*self.RATE/self.BUFFERSIZE
      if divBy:
         ys=ys/float(divBy)
      return xs,ys

   