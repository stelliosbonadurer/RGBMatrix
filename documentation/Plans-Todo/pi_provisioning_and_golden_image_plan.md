# Stable, One-Line Pi Setup (No Docker)

Last updated: 2026-06-19
Status: Planning approved, implementation not started

## 1. Goal
Get a new Pi (Zero 2 W or otherwise) from freshly-flashed to running the matrix
software with a single command, without re-discovering the dependency issues
hit during the manual Pi Zero 2 bring-up:
- `distutils` missing (Python 3.12+ removed it from stdlib; `setup.py` still imports it).
- `cython3` from `apt` too old to generate code compatible with newer CPython
  (`PyLongObject.ob_digit` / `_PyLong_AsByteArray` signature changes in 3.12+).
- `samplebase.py` not importable unless `PYTHONPATH` includes
  `rpi-rgb-led-matrix/bindings/python/samples`.
- Plain `sudo` drops environment (`PYTHONPATH`, etc.); needs `sudo -E`.

## 2. Why Not Docker
Considered and rejected for the runtime piece:
- The library needs raw `/dev/mem` access and tight bit-banged GPIO timing for
  panel refresh, so a container would need `--privileged` anyway, losing most
  of the isolation benefit.
- Docker adds real RAM/storage/IO overhead on a Pi Zero 2 W, which only has
  512MB RAM.

Decision: pin exact dependency versions in a provisioning script, then bake a
finished SD card image once it works. Flashing that image becomes the
"one-line install" for future Pis, with zero runtime overhead.

(Docker may still be useful later purely as a *build* environment on the
Mac/CI to cross-build/test against a matching aarch64 + Python version before
baking the image — not for running on the Pi itself.)

## 3. Provisioning Script
Create `provision.sh` at the repo root. Idempotent, safe to re-run. Steps it
automates (all manual steps discovered this session):

1. `apt update && apt full-upgrade -y`
2. Disable onboard audio (`dtparam=audio=off` in `/boot/firmware/config.txt`).
3. Remove conflicting services: `bluez bluez-firmware pi-bluetooth triggerhappy pigpio`.
4. Install build deps: `git build-essential python3-dev python3-pip`.
5. Pin and verify a working Cython:
   - Remove `apt`'s `cython3` package.
   - `pip3 install --upgrade "cython>=3.0" --break-system-packages`.
   - Symlink `cython3` -> the pip-installed `cython` binary.
   - Fail loudly (don't continue) if `cython3 -V` reports < 3.0.
6. Ensure `setuptools` provides the `distutils` shim:
   - `pip3 install --upgrade setuptools --break-system-packages`.
7. Build the matrix library + Python bindings from a clean state:
   - Remove any stale generated `core.cpp` / `graphics.cpp` before building,
     so a previously-generated (incompatible) file is never silently reused.
   - `make build-python && sudo make install-python`.
8. Record the exact versions used (Python, Cython, OS release) to a file in
   `documentation/` for future debugging if something drifts.
9. Print a final smoke-test command the user can run immediately
   (`sudo ./examples-api-use/demo -D0 --led-rows=32 --led-cols=32 --led-gpio-mapping=adafruit-hat`).

Script should exit non-zero with a clear message on any step failure, rather
than silently continuing (this is how the distutils/Cython issues went
unnoticed until the Python import step, several layers downstream of the
actual cause).

## 4. Golden Image
Once `provision.sh` succeeds cleanly on a fresh OS flash:
1. Run it on a known-good Pi Zero 2 W with the target OS release.
2. Verify the smoke test (demo binary + at least one Python sample) runs
   without flicker for a few minutes.
3. Shut the Pi down cleanly, pull the SD card, and image it
   (`dd` or Raspberry Pi Imager's "custom image" flow) to a `.img` file.
4. Store the image somewhere durable (external drive / cloud storage) with a
   filename that includes the OS release and date, e.g.
   `rgb-matrix-bookworm-2026-06-19.img`.
5. For a new Pi: flash this image directly instead of stock Raspberry Pi OS —
   this *is* the one-line install (no script execution needed at all).

## 5. Maintenance
- Re-bake the golden image whenever the OS image upstream changes in a way
  that breaks a pinned assumption (e.g. Python version bump again).
- Treat `provision.sh` as the source of truth; the image is just a cached
  result of running it. If the script changes, re-bake.
- Move this file to `Plans-In Progress` when work starts, and to
  `Plans-Completed` once a golden image has been baked and verified on a
  second, independent Pi (not just the one used to build it).
