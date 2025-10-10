# my_programs/sorts.py
#Various sorting algorithms with visualization callbacks
#Used with visualize_sorts.py

def insertion_sort(arr, draw_callback):
    print("Performing insertion sort")
    #Because the key is stored outside of the array during sorting,
    #we need to create a fictitious array to show where the key would be
    #to create a more intuitive visualization
    for i in range(1, len(arr)):
        key = arr[i]
        j = i - 1
        while j >= 0 and key < arr[j]:
            arr[j + 1] = arr[j]
            # Create a fictitious array with the key at its intended position
            fictitious = arr.copy()
            fictitious[j] = key
            # Highlight item being inserted as the pivot (blue)
            draw_callback(fictitious, groups=[([j], (0, 0, 255))])
            j -= 1
        arr[j + 1] = key
        #draw_callback(arr)
    return arr

#TODO: Still looks a little awkward
def bubble_sort(arr, draw_callback):
    print("Performing bubble sort")
    n = len(arr)
    for i in range(n):
        for j in range(0, n - i - 1):
            if arr[j] > arr[j + 1]:
                draw_callback(arr, groups=[([j, j+1], (0, 255, 0))])
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
                draw_callback(arr, groups=[([j, j+1], (0, 255, 0))])
                # The item at j+1 is the one being bubbled up
                #draw_callback(arr, pivot_index=j+1)


def selection_sort(arr, draw_callback):
    print("Performing selection sort")
    n = len(arr)
    for i in range(n):
        min_idx = i
        for j in range(i + 1, n):
            if arr[j] < arr[min_idx]:
                min_idx = j
        # Draw the array before and after the swap, highlighting the two indices being swapped (blue)
        draw_callback(arr, groups=[([i, min_idx], (0, 0, 255))])
        arr[i], arr[min_idx] = arr[min_idx], arr[i]
        draw_callback(arr, groups=[([i, min_idx], (0, 0, 255))])
    return arr

#Quick Sort Using Hoare's partitioning scheme
def quick_sort(arr, draw_callback):
    _qs(arr, 0, len(arr) - 1, draw_callback)

def _qs(arr, lo, hi, draw_callback):
    if lo >= hi: 
        return
    j = qs_partition(arr, lo, hi, draw_callback)
    _qs(arr, lo, j - 1, draw_callback)   # fix: pivot at j is done
    _qs(arr, j + 1, hi, draw_callback)


def qs_partition(arr, lo, hi, draw_callback):
    pivot = arr[lo]
    i = lo
    j = hi + 1
    while(True):
        
        # scan i right, stopping at >= pivot or at hi
        i += 1
        while i <= hi and arr[i] < pivot:
            i += 1

        # scan j left, stopping at <= pivot or at lo
        j -= 1
        while j >= lo and arr[j] > pivot:
            j -= 1
        #If pointers cross, break
        if i >= j:
            break
        draw_callback(arr, groups=[([i, j], (255, 255, 0))])
        # swap out-of-place pair
        arr[i], arr[j] = arr[j], arr[i]

        draw_callback(arr, groups=[([i, j], (255, 255, 0))])

    # place pivot into its final spot
    arr[lo], arr[j] = arr[j], arr[lo]
    return j