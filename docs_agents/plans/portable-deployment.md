# Portable Raspberry Pi Deployment Plan

Status: deferred; not part of the current implementation sequence

## Goal

Make a clean checkout install and run predictably on explicitly supported Raspberry
Pi models without copying an already-built virtual environment or native extension
from another Pi.

Containerization is an experiment in this plan, not the assumed solution. The first
future deliverable is a reproducible native install. Existing FFT latency is accepted
as sufficient, so no benchmark is a prerequisite; compare performance only if a new
target or packaging method visibly regresses.

## Dependencies and boundaries

- [Runtime architecture](runtime-architecture.md) defines the application package and
  upstream-driver boundary that must be installed.
- [Web control](web-control-service.md) and [network fallback](network-fallback-ap.md)
  define system services to package after their contracts stabilize.
- This plan owns build/install/upgrade reproducibility, not HTTP or Wi-Fi behavior.

## Why portability currently fails

- `rpi-rgb-led-matrix/` is ignored and absent from a clean clone, but multiple scripts
  depend on its Python binding or sample layout.
- `rgbmatrix` is a native, architecture/Python-specific build rather than a declared
  PyPI dependency.
- Standalone scripts import upstream `samplebase.py` from a particular directory.
- The FFT requirements list only NumPy and `sounddevice`; OS packages, PortAudio,
  build tools, driver revision, and service configuration are not captured.
- Pi models vary in CPU architecture, memory, GPIO timing behavior, and feasible
  Python/NumPy versions. “Pi Zero” must be recorded as the exact board revision; an
  original Zero and a Zero 2 W are not interchangeable deployment targets.

## Supported-platform policy

Create and maintain a compatibility matrix containing:

- Pi model/revision and CPU architecture;
- Raspberry Pi OS release and 32/64-bit choice;
- Python, NumPy, PortAudio/sounddevice, and `rpi-rgb-led-matrix` revisions;
- panel geometry, HAT/mapping, GPIO slowdown, and audio device;
- install result, frame-rate/latency result, and known limitations.

Begin with the currently working Pi as the reference. Add one constrained target at a
time. A failed experiment does not become a supported platform until its dependency
and performance checks pass.

## Recommended native distribution path

Build a versioned installer/provisioner that is safe to rerun:

1. Verify model, architecture, OS release, free space, and required hardware.
2. Install a pinned set of OS packages from named repositories.
3. Fetch a pinned commit/tag of `rpi-rgb-led-matrix` as an explicit dependency.
4. Create a project virtual environment with a supported Python version.
5. Build/install the Python binding for that interpreter and architecture.
6. Install pinned Python dependencies, preferably from a lock/constraints file.
7. Install project configuration/data in stable paths outside the Git checkout.
8. Install and enable the web/runtime systemd services.
9. Run smoke tests for the binding, panel, audio enumeration, and one test frame.

Do not distribute `.venv`, `.so`, or compiled NumPy artifacts copied from another Pi.
For repeat installs, consider an architecture-specific wheelhouse or image artifact,
but produce it from a recorded build and label it by OS, architecture, Python ABI,
and driver revision.

Separate paths by purpose, for example:

- immutable application code under `/opt/rgb-matrix-controller`;
- persistent configuration/state under `/var/lib/rgb-matrix-controller`;
- administrator configuration under `/etc/rgb-matrix-controller`;
- bounded logs through journald.

Final names may change, but runtime state must not be written into the source tree.

## Container feasibility experiment

A container may improve dependency reproducibility, but it does not abstract the
hardware boundary. It will still need an architecture-compatible image and access to
GPIO/memory devices, scheduling, audio devices or the host audio server, and possibly
elevated privileges. Those requirements can reduce both security and portability.

Prototype only after native deployment works:

1. Build multi-architecture images only for architectures proven to support the
   native driver and Python stack.
2. Pin the OS base and compile `rpi-rgb-led-matrix` inside a reproducible builder
   stage; do not copy a host binding blindly.
3. Document every device mount, capability, group, real-time scheduling setting, and
   host dependency. Avoid `--privileged` as an unexplained final configuration.
4. Decide whether audio uses a passed ALSA device or another stable host interface.
5. Run the same latency, jitter, thermal, and failure-recovery suite as native.
6. Test clean stop/clear behavior and access-point/network transitions.

Adopt containers only if they materially improve installs without missing the
performance budget or requiring a privilege model worse than the native services.
It is acceptable for the result to be “native systemd deployment is the supported
path; containers are development-only.”

## Performance baseline

Measure rather than relying only on visible smoothness. Record, per device:

- render-loop frame-time median, p95, p99, and worst case;
- audio callback overruns/dropouts and end-to-display latency where measurable;
- CPU per core, memory, temperature, throttling, and matrix refresh behavior;
- web-service idle load and load while rapidly changing controls;
- boot-to-service-ready and boot-to-first-frame time.

Test the FFT in single and layered mode, because layered mode currently computes the
FFT twice. Also test a non-audio visualization to separate matrix/native cost from
audio/FFT cost. Use the same matrix arguments and workload for native and container
runs.

## Implementation phases

### Phase 1: reproduce and diagnose

1. Capture the working Pi's versions without treating its filesystem as the recipe.
2. Record the failed Pi Zero's exact model, OS, architecture, Python version, install
   command, and first dependency error.
3. Produce the compatibility matrix and choose the first two supported targets.
4. Record that current FFT behavior is acceptable; investigate performance only if
   the constrained target exposes a regression.

### Phase 2: native provisioner

1. Pin upstream native and Python dependency versions.
2. Implement idempotent provisioning with explicit checks and useful failures.
3. Add smoke/health commands that do not require launching the full UI.
4. Test twice on a fresh OS image to prove repeatability and rerun safety.

### Phase 3: release artifact

1. Choose a scripted install, prebuilt OS image, architecture-specific package, or a
   combination based on who will install it.
2. Add checksums, version metadata, upgrade/rollback instructions, and configuration
   migration.
3. Exercise power-loss and interrupted-upgrade recovery.

### Phase 4: optional container comparison

1. Build a minimal prototype with explicit hardware/audio access.
2. Run the identical benchmark suite.
3. Record a keep/reject decision with results for each supported model.

## Acceptance criteria

- A documented fresh supported OS reaches a working test visualization using only
  the provisioner/release artifact and device-specific configuration.
- Re-running installation does not corrupt the driver, environment, or service units.
- Unsupported model/architecture combinations fail early with an actionable message.
- A build artifact cannot be mistaken for a compatible different Python ABI or CPU.
- FFT and matrix performance meet an explicit device-specific budget.
- Upgrade and rollback preserve device configuration and recover after interruption.

## Open decisions

- Exact minimum board (original Pi Zero, Zero 2 W, or a newer baseline).
- Supported Raspberry Pi OS release and 32/64-bit variants.
- Source build versus maintained architecture-specific wheel/package for `rgbmatrix`.
- Provisioning script versus distributable SD-card image for nontechnical recipients.
- Whether the container experiment earns production support.
