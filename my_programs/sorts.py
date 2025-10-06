

def insertion_sort(arr, draw_callback):
    for i in range(1, len(arr)):
        key = arr[i]
        j = i - 1
        while j >= 0 and key < arr[j]:
            arr[j + 1] = arr[j]
            j -= 1
            draw_callback(arr)  # Visualize after each insertion
        arr[j + 1] = key
        draw_callback(arr)  # Visualize after placing the key
    return arr