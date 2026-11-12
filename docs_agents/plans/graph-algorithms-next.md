# Graph Algorithms Follow-up Plan

Status: implemented in software; physical-panel tuning remains

## Goal

Extend `graph_traversal` in three directions:

1. visualize Prim and Kruskal as separate minimum-spanning-tree algorithms, with an
   optional side-by-side comparison on the same seeded, weighted planar graph; and
2. add a comparison mode that divides the 64x64 matrix into two 32x64 viewports and
   runs BFS and DFS in parallel on the same graph; and
3. promote connected-component detection from a static coloring option to a complete
   traversal mode that classifies every vertex in the graph.

The graph model, algorithm state, and renderer must remain hardware-independent. The
same seed and parameters must continue to produce reproducible points, edges,
directions, and weights.

## Parameter contract

Prefer extending the current typed graph parameters instead of creating a second
program. Proposed additions are:

- `display_mode`: `single`, `bfs_dfs_split`, or `prim_kruskal_split`, default
  `single`;
- `algorithm`: extend the present traversal selection with `components`, `prim`, and
  `kruskal`, or rename the existing `traverse` parameter as part of a
  backward-compatible migration;
- `component_type`: `weak` or `strong` when directed component traversal is enabled;
- an optional MST step mode only if the existing edge/node step behavior cannot
  describe candidate-edge processing clearly.

In either split mode, the individual traversal selection is ignored because the
left/right algorithms are fixed by `display_mode`: BFS/DFS or Prim/Kruskal. The phone
controls should hide irrelevant choices rather than accepting combinations that have
no visible effect. In single mode, `prim` and `kruskal` remain independent algorithm
options so either construction can use the full 64x64 panel.

Minimum spanning trees initially support only connected, undirected graphs. Directed
minimum arborescences are a different algorithm and are explicitly out of scope.
The phone form and server validator must reject or prevent these combinations:

- MST with `directed=true`;
- MST without guaranteed connectivity;
- MST with fewer than `n-1` edges; and
- split mode with an incompatible algorithm-specific option.

Cycles should be permitted by default for MST demonstrations because an input that is
already a tree has no competing edge choices. Keep seeded edge weights in the current
1-through-9 range and define deterministic tie-breaking by edge index.

## Connected-component traversal mode

Replace the current static `component_mode` overlay with a proper `components`
traversal mode. This mode is not a search from a designated beginning node to an end
node. It must visit the entire vertex set and identify every connected component,
including isolated vertices.

For an undirected graph, use the standard outer-loop form of DFS:

1. iterate through vertices in stable index order;
2. when the next vertex is already visited, continue;
3. when it is unvisited, allocate the next prominent component color and begin a DFS
   from that vertex;
4. animate that DFS using the existing node/edge stepping behavior; and
5. retain the assigned color, then continue scanning for another unvisited vertex.

This traversal should reuse and simplify the existing DFS machinery rather than
maintaining a second unrelated recursive search. Extract a reusable DFS walk that can
either stop when the target is reached for normal DFS or exhaust one component and
return control to the component-mode outer loop.

Component traversal has no start or target node. The renderer must therefore suppress
the blue start and red target styling in this mode. The currently examined node and
edge still use their transient colors; once examination moves on, each visited node
returns to its assigned component color. The completed frame shows all nodes colored
by component, with no path or endpoint highlight. The opening and final holding periods
continue to apply.

For directed graphs, expose the component interpretation explicitly:

- `weak`: traverse an undirected view of the directed edges while retaining direction
  markers in the drawing; and
- `strong`: use a real strongly connected component algorithm such as Kosaraju or
  Tarjan rather than treating one ordinary DFS tree as an SCC.

The animation for strong components may require discovery and assignment phases, but
the final colors must agree with the mathematical SCC partition. Component numbering
and palette selection must be deterministic, normalized by the lowest vertex index in
each component. If strong-component animation would make the first implementation too
complex, ship animated undirected/weak components first while retaining the existing
tested SCC model for the final classification.

In component mode, `ensure_path`, the selected start/target pair, and ordinary
connectivity guarantees must not terminate or narrow the traversal. The phone form
should hide endpoint-specific controls and explain that every vertex will be scanned.
Graph generation may still honor a requested connectivity setting, but disconnected
graphs should be the useful default for demonstrating this mode.

## Minimum spanning tree visualization

Implement both common constructions as separate algorithm options:

- Prim: begin at the existing start node, maintain the cut/frontier, examine the
  minimum eligible edge, and add its new endpoint to the tree.
- Kruskal: examine edges in `(weight, edge_index)` order, use a disjoint-set structure,
  accept edges that merge components, and reject edges that would form a cycle.

Retain explicit per-frame sets for:

- unexamined edges;
- the current candidate edge;
- examined and rejected edges;
- accepted MST edges;
- nodes already incorporated into the tree; and
- completion state.

The animation should expose one candidate decision per frame. During construction,
accepted tree edges must be drawn above ordinary and rejected edges. On completion,
only the final MST remains highlighted; every non-tree edge returns to its weight
color, matching the completed Dijkstra visual language.

The renderer should continue using the white-to-yellow weight spectrum. Add separate,
editable colors for an accepted MST edge and an edge rejected for creating a cycle if
the existing predecessor/current colors are not distinct enough on the physical panel.
Prim and Kruskal must produce the same total weight for the same graph, although equal
weight ties may produce different valid edge sets.

### Parallel Prim/Kruskal split mode

Add `prim_kruskal_split` using the same viewport and shared-scheduler infrastructure
as the BFS/DFS comparison. Prim runs in the left 32x64 viewport and Kruskal in the
right 32x64 viewport. Both sides receive the exact same immutable weighted graph,
including node coordinates, edge indices, weights, and deterministic tie-breaking
inputs. They must not generate separate half-panel graphs.

Each animation tick advances one candidate-edge decision on each unfinished side.
Because the algorithms can require different numbers of decisions, the side that
finishes first holds its completed MST while the other continues. The final holding
period begins only when both algorithms are complete. Their completed trees may use
different edges when equal weights permit multiple MSTs, but the total weights must
match.

Prim uses the graph's selected start node as its initial tree vertex. Kruskal has no
start or target semantics, so its panel must not imply that either endpoint controls
its decisions. Neither side uses the graph traversal target as a termination
condition. The comparison should distinguish current candidates, accepted tree edges,
and rejected edges consistently on both panels, while still making the algorithms'
different selection order apparent.

## Parallel BFS/DFS split mode

Split the panel vertically:

```text
0                 31 32                63
+-------------------+-------------------+
|        BFS        |        DFS        |
|     32 x 64       |     32 x 64       |
+-------------------+-------------------+
```

Generate one graph using a logical 32x64 layout, then render that exact point and edge
geometry twice with x offsets of 0 and 32. Do not independently regenerate or mirror
the right side: both algorithms must see identical node positions, edge indices,
directions, weights, start node, and target node.

Add a renderer viewport abstraction with origin, width, height, and clipping. It must
support the current full 64x64 mode without changing its pixels. Avoid reserving a
divider column in the first version because that would make one or both viewports
narrower than 32 pixels; panel testing can determine whether a subtle boundary marker
is needed later.

Run BFS and DFS as independent frame iterators over the same immutable graph. A shared
scheduler advances each unfinished iterator once per animation tick:

- the opening hold applies to both untouched views;
- each tick shows the next BFS state on the left and next DFS state on the right;
- if one traversal finishes first, hold its completed frame while the other continues;
- begin the final holding period only after both traversals have completed; and
- replay both sides together using the same seeded graph.

Use the same `step_mode` and `frame_duration` on both sides initially. State colors
retain their current meanings, which makes the difference in search order visible
without introducing side-specific palettes. Start and target nodes remain the same
fixed colors in both viewports. Directed markers, cycle highlighting, component
traversal colors, and `ensure_path` should work identically on both copies where those
features apply, subject to physical legibility at half width. The first split release
remains specifically BFS versus DFS; component traversal runs in single mode.

## Implementation sequence

1. **Complete:** Refactor DFS into a reusable target-search/component-walk engine and
   implement the endpoint-free connected-component traversal mode, including weak and
   strong directed components.
2. **Complete:** Extract viewport translation/clipping from `GraphRenderer` and prove
   that the default full-panel rendering is pixel-identical.
3. **Complete:** Add a reusable two-iterator scheduler and implement `bfs_dfs_split`
   without changing single-mode traversal behavior.
4. **Complete:** Define a dedicated MST frame model and implement/test Prim and Kruskal
   independently of rendering.
5. **Complete:** Add MST rendering layers and final-frame behavior.
6. **Complete:** Reuse the viewport and scheduler abstractions for
   `prim_kruskal_split`, keeping single-panel Prim and Kruskal available.
7. **Complete:** Extend the typed CLI, controller defaults, server-side dependent
   validation, and conditional phone controls.
8. Tune spacing, colors, node size, direction markers, and timing on the physical panel.

## Testing

- Verify Prim and Kruskal on hand-authored graphs with known minimum weights.
- Verify `prim` and `kruskal` can each run independently in full-panel mode.
- Verify component traversal scans every vertex and assigns every vertex exactly one
  component, including isolated vertices.
- Verify ordinary DFS still stops at its target while the shared component DFS exhausts
  one component and resumes the stable outer vertex scan.
- Verify undirected and directed-weak partitions, plus mathematically correct directed
  strong-component partitions.
- Verify component mode never renders start/target colors and never stops merely because
  the graph's stored target node was encountered.
- Verify component colors remain stable after examination and in the completed frame.
- Assert that a completed MST has `n-1` edges, is connected, is acyclic, and has the
  minimum expected total weight.
- Verify deterministic tie-breaking and seeded replay.
- Reject directed and disconnected MST configurations before stopping an active
  visualization.
- Assert that accepted MST edges render above overlapping rejected/ordinary edges.
- Assert that completed MST frames restore every non-tree edge to its weight color.
- Verify that split mode supplies the same immutable graph to BFS and DFS.
- Verify right-side pixels are exact x+32 translations of the corresponding logical
  graph geometry and that neither viewport writes outside its bounds.
- Verify that BFS and DFS states evolve independently and that the earlier finisher is
  held until the other completes.
- Verify split Prim and Kruskal receive the same immutable weighted graph, advance
  independently, and produce equal total MST weights.
- Verify an early-finishing MST panel holds its completed tree until the other panel
  finishes, including graphs with equal-weight alternative MSTs.
- Verify Kruskal rendering has no start/target semantics and Prim never stops at the
  traversal target.
- Verify opening and final holding periods, continuous replay, and clean interruption.
- Run all model, renderer, registry, CLI, and web tests without `rgbmatrix` hardware.

## Acceptance criteria

- Prim and Kruskal visibly build a valid minimum spanning tree from weighted choices.
- Prim and Kruskal are independently selectable in full-panel mode, and their split
  comparison runs both against identical graph geometry and weights.
- Connected-component mode visibly scans the full vertex set, starts a new colored DFS
  for each unvisited region, and finishes with every node classified.
- Connected-component mode has no visual or algorithmic beginning/end-node behavior.
- The completed MST view contains one highlighted tree and weight-colored non-tree
  edges, with no stale candidate or rejected-edge colors.
- A viewer can compare BFS and DFS progress simultaneously on identical graph geometry.
- A viewer can compare Prim and Kruskal's edge-selection order simultaneously and see
  that both finish with the same minimum total weight.
- Each split viewport is exactly 32x64 and remains legible on the physical panel.
- One side completing early does not restart, disappear, or advance the other side.
- Invalid cross-parameter combinations produce clear phone and API errors before launch.

## Open decisions for panel testing

- Whether rejected Kruskal edges need a persistent color or should immediately return
  to their weight color.
- Whether the split view needs tiny Prim/Kruskal labels or whether left/right placement
  is sufficient.
- Whether the split view needs tiny BFS/DFS labels or whether left/right placement is
  sufficient.
- Whether 2x2 endpoint nodes and two-pixel direction markers are too large at 32-pixel
  width and need split-mode-specific rendering sizes.
- Whether component coloring adds useful context in split mode or creates too much
  visual competition with traversal state.
