import time
# import pyaudio
from rgbmatrix import Adafruit_RGBmatrix
import Image
import ImageDraw
import random

matrix = Adafruit_RGBmatrix(32,1)

def main():
   
   matrix.Clear()

   array = []
   generateArray(array)
   # drawArray(matrix,array)

   # time.sleep(5)


   image = Image.new("1", (32,32))
   draw = ImageDraw.Draw(image)


   draw.rectangle((5,5,25,25), fill=1)
   matrix.Clear()
   matrix.SetImage(image.im.id,0,0)
   time.sleep(5)

   # matrix.Clear()
   # image = Image.new("1",(32,32))
   # draw = ImageDraw.Draw(image)
   # draw.line((3,3,27,27), fill=1)
   # matrix.SetImage(image.im.id,0,0)
   # time.sleep(1)

   matrix.Fill(0x000000)
   drawLine(10,10)
   time.sleep(2)
   
   

   matrix.Clear()
   
def drawLine(x,y):
   for i in range(y):
      matrix.SetPixel(x,i,255,255,255)



def generateArray(array):
   #array = []


   for x in range(32):
      #print(x)
      array.append(int(32*random.random()))
      #array.append(15)
   return array

   # #Sorts Tests
   # print("TESTING SORTS")
   # array = [ 3, 5, 2, 7, 9, 8, 1, 4]
   # print("insertion Sort")
   # print(array)
   # insertionSort(array)
   # print(array)
   # print("selection sort")
   # array = [ 3, 5, 2, 7, 9, 8, 1, 4]
   # print(array)
   # selectionSort(array)
   # print(array)
   # print("bubble sort")
   # array = [ 3, 5, 2, 7, 9, 8, 1, 4]
   # print(array)
   # bubbleSort2(array)
   # print(array)
   # print("quickSort")
   # array = [ 3, 5, 2, 7, 9, 8, 1, 4]
   # print(array)
   # quickSort(array,0,len(array)-1)
   # print(array)
   
   

# def drawArray(matrix,array):
#    if len(array) > 32:
#       raise ValueError('Cannot draw array longer than 32')

#    image = Image.new("1",(32,32))
#    draw = ImageDraw.Draw(image)
#    matrix.Clear()

#    draw.rectangle((5,5,15,15),fill=1)

#    for x in range(32):
#       draw.line((x,0,x,array[x]),fill=0)
#       print("drawing "+str(x)+","+str(array[x]))

#    matrix.SetImage(image.im.id,0,0)





def insertionSort(array):

   for i in range(len(array)):
      x = array[i]
      j = i-1
      while j >= 0 and array[j] > x:
         array[j+1] = array[j]
         j -= 1
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
         temp = array[j]
         array[j] = array[m]
         array[m] = temp

def bubbleSort2(array):
   n = len(array)

   while True:
      newN = 0
      for i in range(1,n):
         if(array[i-1] > array[i]):
            temp = array[i]
            array[i] = array[i-1]
            array[i-1] = temp
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
         swap(array,i,j)
         i += 1
         j -= 1

   if first < j:
      quickSort(array,first,j)
   if i < last:
      quickSort(array,i,last)





def swap(array, one, two):
   temp = array[one]
   array[one] = array[two]
   array[two] = temp



if __name__ == "__main__": main()