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
            # Highlight item being inserted as the pivot
            draw_callback(fictitious, pivot_index=j)
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
                draw_callback(arr, highlight_indices=[j, j+1])
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
                draw_callback(arr, highlight_indices=[j, j+1])
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
        #Draw the array before and after the swap, highlighting the two indices being swapped
        draw_callback(arr, highlight_indices=[i, min_idx])
        arr[i], arr[min_idx] = arr[min_idx], arr[i]
        draw_callback(arr, highlight_indices=[i, min_idx])
    return arr

# Implement later
def quick_sort(arr, low, high, draw_callback):
    return arr