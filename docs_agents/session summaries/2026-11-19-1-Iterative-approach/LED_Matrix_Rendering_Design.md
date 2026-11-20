# LED Matrix Rendering and Layer Compositing

## Context and goals

The project is an audio visualization system targeting a 64 × 64 HUB75 RGB LED matrix. The goal is to reduce Python-level drawing overhead, support vertically varying bar colors, and eventually combine multiple visualization layers with deterministic priority.

## Rendering approaches

Two approaches are considered realistic:

| Approach | Advantages | Drawbacks |
| --- | --- | --- |
| Reusable Pillow RGB image | Stable buffer, compiled bulk drawing, existing `SetImage()` binding, low implementation risk | Full-frame upload and possible limitations for highly dynamic effects |
| Native binding extension | Direct raw RGB upload from a contiguous buffer, no Pillow dependency, greater control over rendering | Requires implementing, rebuilding, deploying, and maintaining the native binding |

Other explored approaches (NumPy plus Pillow, per-pixel canvas adapters, duplicated renderers, and shared geometry plans) were deprioritized because of extra copies, Python call overhead, duplicated logic, or unwanted abstraction changes.

### Current direction: reusable Pillow image

Allocate a single `Image.new("RGB", (64, 64))` and reuse it each frame. Clear it, draw bars using bulk rectangle or paste operations, and upload it with `SetImage()`.

A Pillow RGB image represents each pixel as three 8-bit color channels. Its logical raw RGB payload is 12,288 bytes (12 KiB); its internal storage layout may differ.

### Vertically varying bar colors

The concern is that gradient bars may require touching every visible pixel, reducing the advantage of rectangle-based rendering. However, writing every pixel in compiled code is different from issuing a Python function call for every pixel.

- Fixed vertical gradients: precompute a gradient strip and paste the visible portion into each bar.
- Height-relative gradients: consider cached strips for each possible bar height.
- Dynamic gradients and arbitrary pixel effects: may favor NumPy or native rendering, depending on benchmarks.

The native extension remains a potential longer-term option. Even direct RGB upload must translate colors into the matrix's internal PWM representation; it is not necessarily zero-copy.

## Layer compositing

Future visualization modes will draw multiple layers in the same frame. Each layer should have an explicit priority, with the highest-priority visible pixel determining the final color. Distinguish visual overlap (which must be resolved) from redundant framebuffer writes (a performance concern).

| Method | Behavior | Tradeoffs |
| --- | --- | --- |
| Bottom-up / painter's algorithm | Draw lower-priority layers first and overwrite with higher-priority layers | Simplest; preserves bulk drawing; repeated pixel writes |
| Top-down occupancy mask | Draw highest-priority layers first; lower layers only fill unoccupied pixels | Avoids redundant color writes; mask checks and generation add work; roughly 4 KiB for a NumPy boolean mask |
| Independent layer buffers | Render layers separately into RGB plus coverage masks or RGBA images, then composite | Strong modularity, caching, different update rates, transparency; additional buffers and compositing |
| Per-pixel priority buffer | Accept a pixel write only if the incoming priority exceeds the stored priority | Arbitrary drawing order; extra comparisons and storage (8 KiB for an int16 buffer) |

For top-down compositing with NumPy:

```python
visible = layer_mask & ~occupied
frame[visible] = layer_pixels[visible]
occupied |= visible
```

Use an explicit coverage mask rather than treating black pixels as transparent; black may be an intentional foreground color. Define deterministic ordering for equal-priority layers.

## Recommended next steps

1. Finish the reusable Pillow renderer currently under development.
2. Preserve the existing direct renderer as a benchmark baseline.
3. Benchmark solid bars, fixed gradients, height-dependent gradients, and dynamic effects.
4. Measure clearing, frame drawing, `SetImage()` upload, and total render time independently; measure representative frame times on the target hardware.
5. Begin layer support with explicit priorities and bottom-up drawing. Compare against occupancy masks or separate buffers once overlap and caching become important.
6. Consider a native RGB binding if measurements show a meaningful remaining bottleneck or the desired effects outgrow Pillow's convenient bulk operations.

## Open questions

- How much time is spent constructing frames versus uploading them?
- Do gradient modes benefit sufficiently from precomputed strips?
- How much overlap will typical layers have, and does masking actually outperform overwriting?
- Is the primary need opaque priority-based layering, or will transparency/blending be required?

No final decision to implement the native extension or occupancy mask has been made. The Pillow approach is the current experiment.
