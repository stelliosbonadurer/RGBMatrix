import numpy as np # type: ignore
import sounddevice as sd # type: ignore
import time
import os
from blessed import Terminal # type: ignore

# ---------- CONFIG ----------
DEVICE = None      # set to int if needed
SAMPLE_RATE = 48000
BLOCK_SIZE = 2048
CHANNEL = 0
NUM_BINS = 32       # number of bars
MIN_FREQ = 50
MAX_FREQ = 12000
# ---------------------------

term = Terminal()

def freq_to_bin(freqs, fmin, fmax, n):
    edges = np.logspace(np.log10(fmin), np.log10(fmax), n + 1)
    bins = []
    for i in range(n):
        mask = (freqs >= edges[i]) & (freqs < edges[i+1])
        bins.append(mask)
    return bins

latest = np.zeros(BLOCK_SIZE, dtype=np.float32)
have = False

def callback(indata, frames, time_info, status):
    global latest, have
    latest = indata[:, CHANNEL].copy()
    have = True

def main():
    freqs = np.fft.rfftfreq(BLOCK_SIZE, 1 / SAMPLE_RATE)
    bin_masks = freq_to_bin(freqs, MIN_FREQ, MAX_FREQ, NUM_BINS)
    window = np.hanning(BLOCK_SIZE)

    with sd.InputStream(
        device=DEVICE,
        channels=1,
        samplerate=SAMPLE_RATE,
        blocksize=BLOCK_SIZE,
        dtype="float32",
        callback=callback
    ):
        print("Listening... Ctrl+C to stop")
        time.sleep(0.5)

        with term.fullscreen(), term.cbreak(), term.hidden_cursor():
            while True:
                if not have:
                    time.sleep(0.01)
                    continue

                x = latest * window
                X = np.fft.rfft(x)
                mag = np.abs(X)

                bars = []
                for mask in bin_masks:
                    val = np.mean(mag[mask]) if np.any(mask) else 0
                    bars.append(val)

                # normalize
                max_val = max(bars) + 1e-9
                bars = [int((b / max_val) * (term.height - 2)) for b in bars]

                print(term.clear)
                for h in range(term.height - 2, 0, -1):
                    line = ""
                    for b in bars:
                        line += "█" if b >= h else " "
                    print(line)

                time.sleep(0.03)

if __name__ == "__main__":
    main()
