import time
# import pyaudio
from rgbmatrix import Adafruit_RGBmatrix
import Image
import ImageDraw
import random

matrix = Adafruit_RGBmatrix(32,1)

def main():
   #testSorts()
   matrix.Clear()
   while True:
      array = []

      array = generateArray(array)
      # drawArray(matrix,array)

      # time.sleep(2)

      # selectionSort(array)
      # time.sleep(5)
      # drawArray(matrix,array)
      # time.sleep(5)

      # array = generateArray(array)
      # drawArray(matrix,array)

      # time.sleep(2)

      # insertionSort(array)
      # time.sleep(5)
      # drawArray(matrix,array)
      # time.sleep(5)

      # array = generateArray(array)
      # drawArray(matrix,array)

      # time.sleep(2)

      # quickSort(array,0,len(array)-1)
      # time.sleep(5)
      # drawArray(matrix,array)
      # time.sleep(5)

      array = generateArray(array)
      drawArray(matrix,array)

      time.sleep(2)

      bubbleSort2(array)
      time.sleep(5)
      drawArray(matrix,array)
      time.sleep(5)
   


   
   matrix.Fill(0x000000)
   
   

   

   matrix.Clear()
   
def drawLineC(x,y,r,g,b):
   for i in range(y):
      matrix.SetPixel(i,x,r,g,b)
   for i in range(y,32):
      matrix.SetPixel(i,x,0,0,0)

def drawLine(x,y):
   for i in range(y):
      matrix.SetPixel(i,x,255,255,255)
   for i in range(y,32):
      matrix.SetPixel(i,x,0,0,0)




def generateArray(array):
   array = []


   for x in range(32):
      #print(x)
      array.append(int(32*random.random()))
      #array.append(15)
   return array


   
   

def drawArray(matrix,array):
   #matrix.Fill(0x000000)
   if len(array) > 32:
      raise ValueError('Cannot draw array longer than 32')

   for x in range(32):
      print(x)
      #print("drawing "+str(x)+","+str(array[x]))
      drawLine(x,array[x])
      

   #matrix.SetImage(image.im.id,0,0)





def insertionSort(array):

   for i in range(len(array)):
      x = array[i]
      j = i-1
      while j >= 0 and array[j] > x:
         array[j+1] = array[j]
         #drawLine(j,array[j],0,0,0)
         drawLineC(j,array[j],255,0,0)
         #drawLine(j+1,array[j+1],0,0,0)
         drawLineC(j+1,array[j+1],0,255,0)
         time.sleep(.05)
         j -= 1
         drawArray(matrix,array)
         time.sleep(.05)
      array[j+1] = x

def selectionSort(array):

   for j in range(len(array)-1):
      #min value
      m = j
      #swap until value is no longer greater than the minimum value
      for i in range(j+1,len(array)):
         if array[i] < array[m]:
            m = i
      #swap minimum and j
      if m != j:
         #drawLine(j,array[j],0,0,0)
         drawLineC(j,array[j],255,0,0)
         drawLineC(m,array[m],0,255,0)
         time.sleep(.1)
         temp = array[j]
         array[j] = array[m]
         array[m] = temp
         drawLine(j,array[j])
         drawLine(m,array[m])

def bubbleSort2(array):
   n = len(array)

   while True:
      newN = 0
      for i in range(1,n):
         if(array[i-1] > array[i]):
            # temp = array[i]
            # array[i] = array[i-1]
            # array[i-1] = temp
            swap(array,i,i-1,0)
            newN = i
      n = newN
      if n == 0:
         break

def quickSort(array, first, last):
   i = first
   j = last

   p = array[first + (last-first)/2]

   while(i <= j):
      #if the value at i is less than pivot increment i
      while array[i] < p:
         i += 1

      #if the value at j is greater than pivot decrement j
      while array[j] > p:
         j -= 1
      #if j is greater than i swap the values and increment/decrement
      if i <= j:
         swap(array,i,j,p)
         
         i += 1
         j -= 1

   if first < j:
      quickSort(array,first,j)
   if i < last:
      quickSort(array,i,last)


def testSorts():
   #Sorts Tests
   print("TESTING SORTS")
   array = []
   array = generateArray(array)
   print("insertion Sort")
   print(array)
   insertionSort(array)
   print(array)
   print("selection sort")
   array = generateArray(array)
   print(array)
   selectionSort(array)
   print(array)
   print("bubble sort")
   array = generateArray(array)
   print(array)
   bubbleSort2(array)
   print(array)
   print("quickSort")
   array = generateArray(array)
   print(array)
   quickSort(array,0,len(array)-1)
   print(array)


def swap(array, one, two, pivot):
   drawLineC(one,array[one],255,0,0)
   drawLineC(two,array[two],0,255,0)
   drawLineC(pivot,array[pivot],255,255,0)
   time.sleep(.1)
   temp = array[one]
   array[one] = array[two]
   array[two] = temp
   drawLine(one,array[one])
   drawLine(two,array[two])
   drawLine(pivot,array[pivot])




if __name__ == "__main__": main()