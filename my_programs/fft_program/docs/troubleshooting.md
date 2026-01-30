# Troubleshooting Guide

Common issues and their solutions.

## Display Issues

### Horizontal Lines / Artifacts

**Symptom:** Horizontal lines that grow thicker, usually at ~45% and ~60% of display height.

**Cause:** Incorrect panel configuration (multiplexing or row addressing).

**Solutions:**

```bash
# Try different row address types
--led-row-addr-type=1   # For 64x64 AB-addressed panels
--led-row-addr-type=3   # For ABC-addressed panels
--led-row-addr-type=4   # ABC Shift + DE direct

# Try different multiplexing
--led-multiplexing=1    # Through --led-multiplexing=8

# Some panels need special init
--led-panel-type=FM6126A
--led-panel-type=FM6127
```

### Flickering Display

**Symptom:** Visible flicker, especially in photos/video.

**Solutions:**

1. **Increase PWM bits:** `--led-pwm-bits=11`
2. **Check refresh rate:** Add `--led-show-refresh` to see Hz
3. **Reduce chain length** if using many panels
4. **Check power supply** - needs 5V at sufficient amperage

### Colors Wrong / Swapped

**Symptom:** Red appears blue, green appears red, etc.

**Solution:**

```bash
--led-rgb-sequence=RBG  # Try different sequences: RGB, RBG, GRB, GBR, BRG, BGR
```

### Display Too Dim

**Symptom:** LEDs are not bright enough.

**Solutions:**

1. **Increase brightness:** `--led-brightness=100`
2. **Increase brightness_boost** in settings
3. **Check power supply** - insufficient power causes dimming
4. **Reduce PWM bits** for faster refresh: `--led-pwm-bits=7`

### Ghosting / Smearing

**Symptom:** Faint images of previous frames visible.

**Solutions:**

1. **Increase slowdown:** `--led-slowdown-gpio=2` or higher
2. **Check panel type** - some need initialization

---

## Audio Issues

### "Audio device not found"

**Symptom:** Error about device not found.

**Solutions:**

1. **List devices:**

   ```python
   import sounddevice
   print(sounddevice.query_devices())
   ```

2. **Use exact name** from the list in settings
3. **Check USB connection** if using USB mic
4. **Try device index** instead of name (less reliable)

### No Bars Moving / Flat Display

**Symptom:** Bars stay at zero or don't respond to audio.

**Solutions:**

1. **Check audio device** is correct
2. **Lower noise_floor:** Try `0.0` to see raw signal
3. **Increase sensitivity:**
   - Decrease `fixed_scale_max` if using fixed scale
   - Decrease `headroom_multiplier` for RMS scale
4. **Check audio levels** - is the mic picking up sound?

### Bars Always at Maximum

**Symptom:** Everything pegged at full height.

**Solutions:**

1. **Increase noise_floor:** Try `0.3` or higher
2. **Increase scaling:**
   - Increase `fixed_scale_max`
   - Increase `headroom_multiplier`
3. **Check for clipping** in audio input

### Delayed / Laggy Response

**Symptom:** Bars respond slowly to audio.

**Solutions:**

1. **Reduce block_size:** `256` instead of `512`
2. **Reduce rolling_window_seconds:** `1.5` instead of `3`
3. **Increase attack_speed:** `0.2` or higher
4. **Reduce smoothing rise:** `0.9` for more instant response

### Bars Too Twitchy / Noisy

**Symptom:** Bars jump around erratically.

**Solutions:**

1. **Increase noise_floor:** Filter out background noise
2. **Decrease rise smoothing:** `0.6` for smoother movement
3. **Increase rolling_window_seconds:** More stable scaling
4. **Decrease attack_speed:** Less reactive to sudden sounds

---

## Runtime Errors

### "ImportError: No module named 'rgbmatrix'"

**Cause:** Not running on Pi or library not installed.

**Solutions:**

1. Make sure you're on the Raspberry Pi
2. Install the library:

   ```bash
   cd ~/rpi-rgb-led-matrix
   make build-python PYTHON=$(which python3)
   sudo make install-python PYTHON=$(which python3)
   ```

### "Permission denied" or GPIO errors

**Cause:** Need root access for GPIO.

**Solution:** Run with sudo:

```bash
sudo python main.py ...
```

### "ImportError: No module named 'sounddevice'"

**Solution:**

```bash
pip install sounddevice numpy
# Or in venv:
sudo -E ~/path/to/.venv/bin/python main.py ...
```

---

## Performance Issues

### High CPU Usage

**Solutions:**

1. **Increase sleep_delay:** `0.008` instead of `0.004`
2. **Reduce fft_size:** `4096` instead of `8192`
3. **Disable unused features** (peaks if not using)

### Low Frame Rate

**Solutions:**

1. **Reduce chain length** if using multiple panels
2. **Reduce PWM bits:** `--led-pwm-bits=7`
3. **Use parallel chains** instead of long single chain

---

## Quick Diagnostic Commands

```bash
# List audio devices
python -c "import sounddevice; print(sounddevice.query_devices())"

# Test matrix with simple pattern
sudo python -c "
from rgbmatrix import RGBMatrix, RGBMatrixOptions
opt = RGBMatrixOptions()
opt.rows = 64
opt.cols = 64
opt.hardware_mapping = 'adafruit-hat'
m = RGBMatrix(options=opt)
for x in range(64):
    for y in range(64):
        m.SetPixel(x, y, 255 if x < 32 else 0, 255 if y < 32 else 0, 0)
input('Press Enter to exit')
"

# Check Python path
python -c "import sys; print('\n'.join(sys.path))"

# Show available themes/visualizers
python main.py --list-themes
python main.py --list-visualizers
```

## Getting Help

If issues persist:

1. **Check the rpi-rgb-led-matrix documentation:**
   <https://github.com/hzeller/rpi-rgb-led-matrix>

2. **Visit the discourse forum:**
   <https://rpi-rgb-led-matrix.discourse.group/>

3. **Note your hardware:**
   - Raspberry Pi model
   - Panel specs (size, scan rate, brand)
   - HAT/bonnet or direct wiring
   - Power supply specs
