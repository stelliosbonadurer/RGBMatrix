// Graph Traversal's dependent-field rules, ported from the old page.
import test from 'node:test';
import assert from 'node:assert/strict';

import {applyRules, orderParameters} from '../../matrix_controller/static/js/programs/graph_traversal.js';

const base = {
  n: 12,
  e: 0,
  display_mode: 'single',
  traverse: 'bfs',
  seed: 0,
  connected: true,
  allow_cycles: false,
  directed: false,
  ensure_path: true,
  directed_connectivity: 'weak',
  component_type: 'weak',
  step_mode: 'edge',
  animate_back_edges: false,
  frame_duration: 0.2,
  holding_period: 10,
};

test('n and e move to the end', () => {
  const names = ['n', 'e', 'display_mode', 'traverse', 'seed'].map(name => ({name}));
  assert.deepEqual(orderParameters(names).map(item => item.name), ['display_mode', 'traverse', 'seed', 'n', 'e']);
});

test('undirected BFS: limits and visible fields', () => {
  const result = applyRules(base);
  assert.deepEqual([...result.hidden].sort(), ['component_type', 'directed_connectivity', 'ensure_path']);
  assert.deepEqual(result.errors, {});
  assert.equal(result.help.e, '0 = automatic (11). Explicit edge range: 11–11.');
  assert.equal(result.edgeMaximum, 11);
  assert.equal(result.mst, false);
  assert.deepEqual(result.values, base);
  assert.match(applyRules({...base, e: 5}).errors.e, /choose 11 through 11 edges/);
});

test('entering an MST mode turns cycles on once and forces an undirected connected graph', () => {
  const first = applyRules({...base, traverse: 'prim', directed: true, connected: false}, false);
  assert.equal(first.mst, true);
  assert.equal(first.values.allow_cycles, true);
  assert.equal(first.values.directed, false);
  assert.equal(first.values.connected, true);
  for (const name of ['connected', 'directed', 'step_mode', 'animate_back_edges']) assert.ok(first.hidden.has(name), name);
  // Still in MST: the user's choice to turn cycles off again is kept.
  const second = applyRules({...first.values, allow_cycles: false}, true);
  assert.equal(second.values.allow_cycles, false);
  assert.equal(applyRules({...base, display_mode: 'prim_kruskal_split'}).mst, true);
});

test('strong connectivity needs a directed graph with cycles and n >= 3', () => {
  const directed = {...base, directed: true, directed_connectivity: 'strong'};
  const noCycles = applyRules(directed);
  assert.equal(noCycles.values.directed_connectivity, 'weak');
  assert.deepEqual(noCycles.disabledChoices.directed_connectivity, ['strong']);
  const withCycles = applyRules({...directed, allow_cycles: true});
  assert.equal(withCycles.values.directed_connectivity, 'strong');
  assert.deepEqual(withCycles.disabledChoices.directed_connectivity, []);
  assert.equal(withCycles.help.e, '0 = automatic (18). Explicit edge range: 12–30.');
  assert.match(applyRules({...directed, allow_cycles: true, e: 5}).errors.e,
    /Use 0 for automatic, or choose 12 through 30 edges\./);
  assert.ok(!withCycles.hidden.has('ensure_path'));
  assert.ok(withCycles.hidden.has('connected'));
});

test('strong components need a directed graph', () => {
  const result = applyRules({...base, traverse: 'components', component_type: 'strong'});
  assert.equal(result.values.component_type, 'weak');
  assert.deepEqual(result.disabledChoices.component_type, ['strong']);
  assert.ok(!result.hidden.has('component_type'));
  assert.ok(result.hidden.has('animate_back_edges'));
});

test('values the server ignores are kept, so a running program does not look edited', () => {
  const undirected = applyRules({...base, directed_connectivity: 'strong', component_type: 'strong'});
  assert.equal(undirected.values.directed_connectivity, 'strong');
  assert.equal(undirected.values.component_type, 'strong');
  const split = applyRules({...base, display_mode: 'bfs_dfs_split', traverse: 'components', component_type: 'strong'});
  assert.equal(split.values.component_type, 'strong');
});

test('split modes hide the single traversal choice', () => {
  const result = applyRules({...base, display_mode: 'bfs_dfs_split'});
  assert.ok(result.hidden.has('traverse'));
  assert.ok(!result.hidden.has('animate_back_edges'));
});

test('two nodes with cycles allow exactly one edge', () => {
  const result = applyRules({...base, n: 2, allow_cycles: true});
  assert.equal(result.help.e, '0 = automatic (1). Explicit edge range: 1–1.');
});

test('an invalid node count blocks the edge field', () => {
  for (const n of [1, 33, NaN]) {
    const result = applyRules({...base, n});
    assert.equal(result.errors.e, 'Choose n from 2 through 32 first.');
    assert.equal(result.help.e, 'Choose a valid node count to calculate edge limits.');
  }
});
