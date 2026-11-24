// Graph Traversal's dependent-field rules, ported from the old page's
// configureGraphTraversalForm (web-control-refactor D-042). Pure, so node --test
// covers it; program.js applies the result to the form. The server still runs
// validate_options on every launch, so these rules only guide the draft.

export const id = 'graph_traversal';

// The old page listed n and e last, after the options that decide their limits.
export function orderParameters(parameters) {
  const last = ['n', 'e'];
  return [
    ...parameters.filter(parameter => !last.includes(parameter.name)),
    ...last.map(name => parameters.find(parameter => parameter.name === name)).filter(Boolean),
  ];
}

// `values` is the typed draft; `previousMst` is the MST flag from the last call.
// Returns adjusted values plus what to hide, which choices to disable, and the
// edge-count limits and messages.
export function applyRules(values, previousMst = false) {
  const next = {...values};
  const n = next.n;
  const split = next.display_mode !== 'single';
  const mst = next.display_mode === 'prim_kruskal_split' || (
    !split && (next.traverse === 'prim' || next.traverse === 'kruskal')
  );
  // Entering an MST mode turns cycles on once; the user may turn them off again.
  if (mst && !previousMst) next.allow_cycles = true;
  if (mst) {
    next.directed = false;
    next.connected = true;
  }
  const directed = next.directed;
  const allowCycles = next.allow_cycles;
  const traversingComponents = !split && next.traverse === 'components';
  const backEdgesRelevant = !directed && (
    next.display_mode === 'bfs_dfs_split' || (
      !split && (next.traverse === 'bfs' || next.traverse === 'dfs')
    )
  );

  const hidden = new Set();
  if (split) hidden.add('traverse');
  if (directed || mst) hidden.add('connected');
  if (mst) hidden.add('directed');
  if (!directed || traversingComponents) hidden.add('ensure_path');
  if (!directed) hidden.add('directed_connectivity');
  if (!traversingComponents) hidden.add('component_type');
  if (mst) hidden.add('step_mode');
  if (!backEdgesRelevant) hidden.add('animate_back_edges');

  const validN = Number.isInteger(n) && n >= 2 && n <= 32;
  const strongGraphAvailable = directed && allowCycles && n >= 3;
  const disabledChoices = {
    directed_connectivity: strongGraphAvailable ? [] : ['strong'],
    component_type: directed ? [] : ['strong'],
  };
  // Reset a choice only where the server would reject it (graph_model
  // validate_options): strong connectivity counts only for a directed graph, and
  // strong components only for a single-view components traversal. Elsewhere the
  // value is ignored and kept, so a running program doesn't look edited.
  if (directed && next.directed_connectivity === 'strong' && !strongGraphAvailable) {
    next.directed_connectivity = 'weak';
  }
  if (traversingComponents && next.component_type === 'strong' && !directed) {
    next.component_type = 'weak';
  }

  if (!validN) {
    return {
      values: next,
      mst,
      hidden,
      disabledChoices,
      errors: {e: 'Choose n from 2 through 32 first.'},
      help: {e: 'Choose a valid node count to calculate edge limits.'},
      edgeMaximum: null,
    };
  }

  const guaranteed = directed
    ? next.directed_connectivity === 'strong' || next.directed_connectivity === 'weak'
    : next.connected;
  const strong = directed && next.directed_connectivity === 'strong';
  const maximum = allowCycles || directed ? (n === 2 ? 1 : 3 * n - 6) : n - 1;
  const connectivityMinimum = strong ? n : guaranteed ? n - 1 : 0;
  const pathMinimum = directed && !traversingComponents && next.ensure_path ? 1 : 0;
  const minimum = Math.max(connectivityMinimum, pathMinimum);
  const automatic = allowCycles || directed
    ? Math.min(Math.max(Math.ceil(n * 1.5), minimum), maximum)
    : n - 1;
  const edgeCount = next.e;
  const validEdgeCount = edgeCount === 0 || (
    Number.isInteger(edgeCount) && edgeCount >= minimum && edgeCount <= maximum
  );
  return {
    values: next,
    mst,
    hidden,
    disabledChoices,
    errors: validEdgeCount ? {} : {e: `Use 0 for automatic, or choose ${minimum} through ${maximum} edges.`},
    help: {e: `0 = automatic (${automatic}). Explicit edge range: ${minimum}–${maximum}.`},
    edgeMaximum: maximum,
  };
}
