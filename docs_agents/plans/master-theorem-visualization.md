# Master Theorem Visualization Plan

Status: initial 64x64 visualization implemented; classroom validation and refinement remain

## Goal

Create a visualization for the recurrence

```text
T(n) = aT(n / b) + Theta(n^k)
```

that reinforces why the Master Theorem compares `n^k` with
`n^(log_b(a))`. The matrix should build the recursion tree from top to bottom,
show the number and relative size of subproblems at each level, and identify
whether the root, every level, or the leaves dominate the total work.

The first target is one 64x64 HUB75 panel. The mathematical model and renderer
must remain independent of `rgbmatrix` so calculations and frames can be tested
without Raspberry Pi hardware.

## Mathematical model

For level `i`, calculate and retain:

```text
node count     = a^i
problem size   = n / b^i
work per node  = (n / b^i)^k
total work     = a^i * (n / b^i)^k
```

The ratio between consecutive levels is `a / b^k`:

- less than one: work decreases and the root dominates;
- equal to one: every level contributes equally; and
- greater than one: work increases and the leaves dominate.

Use the standard case numbering:

- Case 1: `k < log_b(a)`, giving `Theta(n^(log_b(a)))`;
- Case 2: `k = log_b(a)`, giving `Theta(n^k log n)`; and
- Case 3: `k > log_b(a)`, giving `Theta(n^k)`.

## 64x64 visual language

- Reserve rows 0-4 for compact `a`, `b`, `k`, and case labels.
- Start the recursion tree at row 6.
- Give every displayed level a six-pixel band: one connector row and five work rows.
- Distribute nodes horizontally across the panel.
- Scale each node's work block from one to five rows using its work relative to
  the other levels. Thus linear/constant work remains line-like while quadratic
  work begins as a filled box and shrinks with the subproblem size.
- Preserve exact node counts in the model. Once individual nodes are too narrow,
  render an aggregated density band rather than creating off-screen objects.
- Use a stable case color and white highlighting to sweep through level totals.
- In the final frame, highlight the root, all levels, or leaves according to the
  theorem result.

The supported initial input range keeps common binary examples within the nine
six-pixel level bands available on a 64x64 panel. The renderer still handles a
deeper model by showing the earliest levels and the final leaf level.

## Animation

1. Reveal the tree one level at a time.
2. Sweep a white highlight from the root to the leaves to compare total work.
3. Hold a result frame with the dominant region highlighted.
4. Repeat continuously, or stop after a configured number of cycles.

Initial presets are binary search, merge sort, root-heavy quadratic work,
Strassen multiplication, and a leaf-heavy four-way recurrence. Custom `a`, `b`,
`n`, and `k` values remain the default mode.

## Implementation boundary

```text
my_programs/dsa_visualizations/
|-- model.py             recurrence validation and Master Theorem analysis
|-- rendering.py         hardware-independent 64x64 layout and drawing
|-- native_matrix.py     thin native output adapter using shared matrix flags
`-- master_theorem.py    CLI, presets, animation, and native matrix adapter
```

Register the program in the managed controller only through typed, bounded
parameters. It has no live-control endpoint in the first release; changing a
recurrence starts a new supervised run.

## Testing

- Verify all three theorem cases and asymptotic result labels.
- Verify node count, problem size, per-node work, and total work at every level.
- Reject invalid recurrence parameters.
- Render against a recording canvas and assert that every pixel remains in bounds.
- Verify progressive reveal, aggregation, dominant-level highlighting, and
  deterministic frame output.
- Verify controller parameter validation and fixed argv construction.
- On the Pi, confirm legibility, pacing, clean stop/clear behavior, and the visual
  distinction between root-dominant, balanced, and leaf-dominant examples.

## Acceptance criteria

- A student can see subproblems multiply and shrink from top to bottom.
- The combined work trend agrees with the Master Theorem classification.
- Quadratic work is visibly block-like rather than indistinguishable from a line.
- The program runs directly and from the managed web controller on a 64x64 panel.
- Model and renderer tests run without `rgbmatrix` or matrix hardware.

## Follow-up decisions

- Whether classroom use benefits from live parameter controls instead of restart-on-change.
- Whether to add tiny formula/result text beyond the compact case label.
- Whether later DSA visualizations should share this renderer, timing loop, and
  software-canvas test support as a common package.
