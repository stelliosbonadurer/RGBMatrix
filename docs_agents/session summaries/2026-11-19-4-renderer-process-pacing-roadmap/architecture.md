# Architecture direction

No runtime architecture changed in this planning session. The proposed boundary
gives the renderer process exclusive ownership of the matrix and display clock.

## Process ownership

```text
parent process                              renderer child process
--------------                              ----------------------
audio capture                               temporal display state
FFT analysis              shared latest     selected output plan
web/control service  -------------------->  RGBMatrix and canvases
supervision             64-bin frame         SwapOnVSync
                    <-------------------->  control and health channel
```

The high-rate boundary is a fixed-size latest-value mailbox, not a queue. The child
may skip superseded analysis results and may display the newest result more than
once. Neither side accumulates backlog.

The parent never creates or receives a matrix canvas. The child initializes the
native binding after process start and remains its sole owner until shutdown.

## Temporal state ownership

Display-rate effects belong in the child because they advance on displayed time, not
FFT publication count. The child computes `dt` from a monotonic clock and owns
smoothing and peak state. Analysis publishes the newest 64-bin numeric result plus
sequence and timing metadata.

The time conversion preserves the current look at a reference interval while
remaining stable across missed refreshes. Long stalls need an explicit clamp or
reset rule rather than being treated as one ordinary frame.

## Pacing choices

### Fixed-sleep reference

The current loop waits for VSync and then sleeps another five milliseconds. It is
retained only long enough to isolate the process change and provide a comparison.

### VSync-only candidate

The child renders immediately after the prior swap returns. The native call blocks
until a refresh boundary, so no additional application wait is required:

```text
render work | VSync wait | render work | VSync wait
```

This is both display-clock pacing and the maximum safe throughput of the current
double-buffered API. The parent continues analysis independently and does not
participate in the wait.

### Why there is no true no-wait mode

Removing the VSync wait would require live-canvas writes, a nonblocking native swap,
or another output stage. Live writes can tear, and producing frames faster than the
panel can accept them only creates discarded work. The dedicated child already
provides the useful isolation that an extra output thread would otherwise seek.

## Output paths

`direct` and `pillow-bulk` are instantiated inside the child and selected at startup
or a future safe Apply boundary. Direct stays specialized and is not routed through
Pillow or a generic compositor. This roadmap requires current-scene correctness and
health for each path, not feature parity across hypothetical layers.

## Failure boundaries

- Parent supervision detects child startup failure, unexpected exit, and health
  timeout.
- Child errors are reported through a bounded control channel and trigger existing
  service cleanup or restart behavior.
- Shared-memory frames carry schema/epoch and sequence data so the child never treats
  a partially published frame as complete.
