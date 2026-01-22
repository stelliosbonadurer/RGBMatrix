# my_programs/sorts.py
# Various sorting algorithms with visualization callbacks
# Used with visualize_sorts.py

# Shared color palette (RGB tuples) for visualizations
COLORS = {
    "base": (150, 5, 5),        # default bar color (should match visualize_sorts base_color)
    "compare": (255, 255, 0),   # yellow: comparing/swapping pair
    "swap": (255, 255, 0),        # green: swap highlight
    "move": (0,0,255),          # blue: The item being put into place
    "pivot": (0, 0, 255),       # blue: pivot or key insertion position
    "min": (0, 0, 255),         # blue: current min (reuse pivot blue)
}

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
            # Highlight item being inserted as the pivot/key
            draw_callback(fictitious, groups=[([j], COLORS["move"])])
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
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
                draw_callback(arr, groups=[[ [j+1], COLORS["move"] ]])
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
        # Draw before and after the swap; highlight i and min_idx
        draw_callback(arr, groups=[[ [i], COLORS["swap"] ], [ [min_idx], COLORS["move"]]])
        arr[i], arr[min_idx] = arr[min_idx], arr[i]
        draw_callback(arr, groups=[[ [min_idx], COLORS["swap"] ], [ [i], COLORS["move"]]])
    return arr

#Quick Sort Using Hoare's partitioning scheme
def quick_sort(arr, draw_callback):
    print("Performing Quick Sort (Hoare's Partition)")
    _qs(arr, 0, len(arr) - 1, draw_callback)

def _qs(arr, lo, hi, draw_callback):
    if lo >= hi: 
        return
    j = _qs_partition(arr, lo, hi, draw_callback)
    _qs(arr, lo, j - 1, draw_callback)   # fix: pivot at j is done
    _qs(arr, j + 1, hi, draw_callback)


def _qs_partition(arr, lo, hi, draw_callback):
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
        draw_callback(arr, groups=[[ [lo], COLORS["move"] ], [ [i, j], COLORS["swap"] ]])
        # swap out-of-place pair
        arr[i], arr[j] = arr[j], arr[i]

        draw_callback(arr, groups=[[ [lo], COLORS["move"] ], [ [i, j], COLORS["swap"] ]])

    # place pivot into its final spot
    draw_callback(arr, groups=[[ [j], COLORS["swap"] ], [ [lo], COLORS["move"]]])
    arr[lo], arr[j] = arr[j], arr[lo]
    draw_callback(arr, groups=[[ [lo], COLORS["swap"] ], [ [j], COLORS["move"]]])
    return j


    def shell_sort(arr, draw_callback):
        """
        Shell sort (gap-based insertion sort).
        Uses the groups-based draw_callback for visualization with COLORS palette.
        """
        print("Performing shell sort")
        n = len(arr)
        gap = n // 2

        while gap > 0:
            for i in range(gap, n):
                temp = arr[i]
                j = i
                # Highlight the current element being positioned
                draw_callback(arr, groups=[([i], COLORS.get("move", (0, 0, 255)))])

                # Gap-based insertion
                while j >= gap and arr[j - gap] > temp:
                    # Show comparison/shift
                    draw_callback(
                        arr,
                        groups=[
                            ([j], COLORS.get("swap", (255, 255, 0))),
                            ([j - gap], COLORS.get("swap", (255, 255, 0))),
                            ([i], COLORS.get("move", (0, 0, 255))),
                        ],
                    )
                    arr[j] = arr[j - gap]
                    # Show the shift result at position j
                    draw_callback(arr, groups=[([j], COLORS.get("move", (0, 0, 255)))])
                    j -= gap

                # Place temp into its correct position for this gap
                arr[j] = temp
                draw_callback(arr, groups=[([j], COLORS.get("move", (0, 0, 255)))])

            gap //= 2

        return arr


