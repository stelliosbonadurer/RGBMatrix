// Music FFT page rules (web-control-refactor M5 slice 1, D-049–D-066). Pure, so
// node --test covers it; fft_editor.js draws the page from it.
//
// A "structure" is the running snapshot's `structure` or the catalog file's
// (static/fft_catalog.json, D-049); both have the same shape. A draft layer is
// {uid, layer_id, layer_type, analyzer_id, visible, settings}; `uid` is the page's
// own handle and is never sent. Layers are kept in document (draw) order: the
// last one draws on top and is the top row of the list (D-054).

import {sameValue} from '../model.js';

export const id = 'fft';

export const NAME_MAX = 64;
export const STALE_MESSAGE =
  'The layers were changed on another device. Reload the page to see them; nothing was sent.';

// ---- The structure's catalog -----------------------------------------------

export function layerTypes(structure) {
  return structure?.capabilities?.layer_types || [];
}

export function descriptorFor(structure, layerType) {
  return layerTypes(structure).find(item => item.layer_type === layerType)?.settings || null;
}

export function outputBackends(structure) {
  const listed = structure?.capabilities?.output_backends;
  if (Array.isArray(listed) && listed.length) return listed;
  return structure?.output_backend ? [structure.output_backend] : [];
}

export function maxLayers(structure) {
  const limit = structure?.capabilities?.limits?.max_layers;
  return Number.isInteger(limit) ? limit : Infinity;
}

// The one analyzer's facts (D-009), and which of them are assumed until launch.
export function analyzerFacts(structure, assumed = []) {
  const settings = structure?.analyzers?.[0]?.settings || {};
  return {
    sampleRate: settings.sample_rate ?? null,
    fftSize: settings.fft_size ?? null,
    blockSize: settings.block_size ?? null,
    assumed: new Set(assumed),
  };
}

const SOURCE_LABELS = {'usb-mic': 'USB mic', bluetooth: 'Bluetooth phone'};
export const sourceLabel = choice => SOURCE_LABELS[choice] || choice;

export function formatHz(hz) {
  if (!Number.isFinite(hz)) return '?';
  if (hz >= 1000) return `${Number((hz / 1000).toPrecision(3))} kHz`;
  return `${Number(hz.toPrecision(3))} Hz`;
}

// ---- Draft layers ------------------------------------------------------------

const copy = value => (value === undefined ? undefined : JSON.parse(JSON.stringify(value)));

// The structure.set document (D-015): draw order, names trimmed, no page handles.
export function structureDocument(backend, layers) {
  return {
    output_backend: backend,
    layers: layers.map(layer => ({
      layer_id: String(layer.layer_id).trim(),
      layer_type: layer.layer_type,
      analyzer_id: layer.analyzer_id,
      visible: layer.visible,
      settings: copy(layer.settings),
    })),
  };
}

// The list shows the top layer first (D-054): row r is document index n - 1 - r.
export const displayRows = layers => [...layers].reverse();
export const indexOfRow = (count, row) => count - 1 - row;

// Moves the layer at document index `index` by `delta` places in the document
// (+1 is one row up the list). Returns a new array; out-of-range moves are no-ops.
export function moveLayer(layers, index, delta) {
  const target = index + delta;
  if (index < 0 || index >= layers.length || target < 0 || target >= layers.length) return [...layers];
  const next = [...layers];
  const [layer] = next.splice(index, 1);
  next.splice(target, 0, layer);
  return next;
}

// ---- Names (D-057; the server's rules, structural_control._name) -----------

const CONTROL_CHARACTER = /[\u0000-\u001f\u007f]/u;

export function nameError(name, otherNames) {
  const trimmed = String(name ?? '').trim();
  if (!trimmed) return 'Give the layer a name.';
  if ([...trimmed].length > NAME_MAX) return `Use at most ${NAME_MAX} characters.`;
  if (CONTROL_CHARACTER.test(trimmed)) return 'Remove the control characters from the name.';
  if (otherNames.some(other => String(other).trim() === trimmed)) return 'Another layer has this name.';
  return null;
}

export const typeWord = layerType =>
  String(layerType).split('-').filter(Boolean).at(-1) || 'layer';

// "bars 2" for a second layer, then the next number no layer uses.
export function freeName(layerType, names) {
  const word = typeWord(layerType);
  const taken = new Set(names.map(name => String(name).trim()));
  for (let number = names.length + 1; ; number += 1) {
    const candidate = `${word} ${number}`;
    if (!taken.has(candidate)) return candidate;
  }
}

// ---- Settings from the descriptor (D-053) ----------------------------------

const PARAMETER_TYPES = {choice: 'enum', boolean: 'bool', number: 'float', integer: 'int'};

// A descriptor field in the ParameterSpec shape that createField and parseValue use.
export function settingParameter(field) {
  return {
    name: field.name,
    label: field.label,
    type: PARAMETER_TYPES[field.type] || 'string',
    choices: field.choices,
    minimum: field.min,
    maximum: field.max,
    description: field.help || '',
    default: field.default,
  };
}

// The descriptor's fields under its groups, in the groups' order. A field whose
// group isn't listed goes under a trailing "Other" heading.
export function settingGroups(descriptor) {
  const groups = (descriptor?.groups || []).map(group => ({id: group.id, label: group.label, fields: []}));
  const byId = new Map(groups.map(group => [group.id, group]));
  const other = {id: '', label: 'Other', fields: []};
  for (const field of descriptor?.fields || []) (byId.get(field.group) || other).fields.push(field);
  return [...groups, other].filter(group => group.fields.length);
}

export function isShown(field, settings) {
  const condition = field.shown_when;
  if (!condition) return true;
  return (condition.values || []).some(value => sameValue(settings?.[condition.field], value));
}

// The one rule between two settings (normalize_current_bars_settings): the range
// must be increasing. Reported on max_freq, as the server does. `labelOf` names a
// field for the message.
export function crossFieldErrors(settings, labelOf = name => name) {
  const {min_freq: minimum, max_freq: maximum} = settings || {};
  if (Number.isFinite(minimum) && Number.isFinite(maximum) && maximum <= minimum) {
    return {max_freq: `${labelOf('max_freq')} must be greater than ${labelOf('min_freq')}.`};
  }
  return {};
}

// The exact frequency with digit grouping, for the Nyquist value (22,050 Hz).
export const exactHz = hz => `${Number(hz.toFixed(2)).toLocaleString('en-US')} Hz`;

// Max Hz against half the sample rate (D-069). `context` is
// fft_layout.binContext's. With a known rate it is an error, as the Pi refuses
// the update; with an assumed rate a warning, and launching stays allowed.
// Exactly Nyquist passes: the server refuses only a higher value.
export function nyquistIssue(maxFreq, context, label = 'Max Hz') {
  const rate = context?.sampleRate;
  if (!Number.isFinite(maxFreq) || !Number.isFinite(rate) || maxFreq <= rate / 2) return null;
  const nyquist = exactHz(rate / 2);
  if (context.assumed?.has('sample_rate')) {
    return {
      level: 'warning',
      message: `Above ${nyquist}, the Nyquist frequency at the assumed ${formatHz(rate)} sample rate. ` +
        `If the source runs at ${formatHz(rate)}, the Pi refuses this layer at launch and runs the default layer.`,
    };
  }
  return {level: 'error', message: `${label} must be at most ${nyquist}, the Nyquist frequency at ${formatHz(rate)}.`};
}

export function defaultSettings(descriptor) {
  return Object.fromEntries((descriptor?.fields || []).map(field => [field.name, copy(field.default)]));
}

// ---- Output backend lock (D-061) -------------------------------------------

export function constraintsFor(descriptor, backend) {
  return descriptor?.backend_constraints?.[backend] || {};
}

export function lockedNames(descriptor, backend) {
  return new Set(Object.keys(constraintsFor(descriptor, backend)));
}

// Sets one layer's fixed values for `backend`. Returns the new settings and the
// values the lock replaced, which a later switch restores.
export function lockLayer(settings, descriptor, backend) {
  const next = {...settings};
  const replaced = {};
  for (const [name, value] of Object.entries(constraintsFor(descriptor, backend))) {
    if (!Object.prototype.hasOwnProperty.call(next, name)) continue;
    replaced[name] = copy(next[name]);
    next[name] = copy(value);
  }
  return {settings: next, replaced};
}

// Moves every layer from `from`'s fixed values to `to`'s. `stash` maps a layer uid
// to the values a lock replaced; leaving a backend restores them first. Returns
// the new layers and stash, and each value that changed: [{uid, name, from, to}].
export function switchBackend(layers, descriptorOf, stash, to) {
  const nextStash = {};
  const changes = [];
  const nextLayers = layers.map(layer => {
    const before = layer.settings;
    const restored = {...before, ...copy(stash[layer.uid] || {})};
    const {settings, replaced} = lockLayer(restored, descriptorOf(layer.layer_type), to);
    if (Object.keys(replaced).length) nextStash[layer.uid] = replaced;
    for (const name of Object.keys(settings)) {
      if (!sameJson(before[name], settings[name])) {
        changes.push({uid: layer.uid, name, from: before[name], to: settings[name]});
      }
    }
    return {...layer, settings};
  });
  return {layers: nextLayers, stash: nextStash, changes};
}

// Apply to all layers (fft-level-controls D-018, D-029): layer `fromUid`'s value
// for `name` goes into every other layer of the same type in the draft, hidden
// ones included, except where `backend` fixes the field. Returns each value that
// changes, [{uid, name, from, to}], and how many layers could take it.
export function applyToAll(layers, fromUid, name, descriptorOf, backend) {
  const source = layers.find(layer => layer.uid === fromUid);
  const changes = [];
  let targets = 0;
  if (!source) return {changes, targets};
  for (const layer of layers) {
    if (layer.uid === fromUid || layer.layer_type !== source.layer_type) continue;
    const descriptor = descriptorOf(layer.layer_type);
    if (!(descriptor?.fields || []).some(field => field.name === name)) continue;
    if (lockedNames(descriptor, backend).has(name)) continue;
    targets += 1;
    if (!sameJson(layer.settings[name], source.settings[name])) {
      changes.push({uid: layer.uid, name, from: copy(layer.settings[name]), to: copy(source.settings[name])});
    }
  }
  return {changes, targets};
}

// ---- Weighting points (D-021, D-060) ---------------------------------------

export const sortedPoints = points => [...points].sort((a, b) => a.position - b.position);

// Linear between points, flat beyond the first and last (config/weighting.py).
export function curveWeight(points, position) {
  const sorted = sortedPoints(points);
  if (!sorted.length) return 1;
  if (position <= sorted[0].position) return sorted[0].weight;
  const last = sorted.at(-1);
  if (position >= last.position) return last.weight;
  for (let index = 1; index < sorted.length; index += 1) {
    const right = sorted[index];
    if (position <= right.position) {
      const left = sorted[index - 1];
      const span = right.position - left.position;
      if (span === 0) return right.weight;
      return left.weight + (right.weight - left.weight) * ((position - left.position) / span);
    }
  }
  return last.weight;
}

const roundPosition = value => Math.round(value * 1000) / 1000;
const roundWeight = value => (value === 0 ? 0 : Number(value.toPrecision(3)));

// A new point in the middle of the widest gap (the range ends count), on the
// current curve (D-060). Returns the sorted points and the new point's index.
export function addPoint(points, minimum = 0, maximum = 1) {
  const sorted = sortedPoints(points);
  const edges = [minimum, ...sorted.map(point => point.position), maximum];
  let best = 0;
  for (let index = 1; index < edges.length - 1; index += 1) {
    if (edges[index + 1] - edges[index] > edges[best + 1] - edges[best]) best = index;
  }
  const position = roundPosition((edges[best] + edges[best + 1]) / 2);
  const point = {position, weight: roundWeight(curveWeight(sorted, position))};
  const next = sortedPoints([...sorted, point]);
  return {points: next, index: next.indexOf(point)};
}

// Indices whose position repeats an earlier point's: the server names the later
// one (canonical_weight_points).
export function duplicatePositions(positions) {
  const seen = new Set();
  const duplicates = new Set();
  positions.forEach((position, index) => {
    if (!Number.isFinite(position)) return;
    if (seen.has(position)) duplicates.add(index);
    seen.add(position);
  });
  return duplicates;
}

export const positionHz = (minHz, maxHz, position) => minHz * (maxHz / minHz) ** position;

// ---- Unsent changes (D-052) -------------------------------------------------

function sameJson(a, b) {
  return sameValue(a, b) || JSON.stringify(a) === JSON.stringify(b);
}

// What differs between a layer and its loaded version: 'layer_id', 'visible',
// and each settings name.
export function layerChanges(base, layer) {
  const changed = [];
  if (String(layer.layer_id).trim() !== base.layer_id) changed.push('layer_id');
  if (layer.visible !== base.visible) changed.push('visible');
  const names = new Set([...Object.keys(base.settings || {}), ...Object.keys(layer.settings || {})]);
  for (const name of names) {
    if (!sameJson(base.settings?.[name], layer.settings?.[name])) changed.push(name);
  }
  return changed;
}

// `baseline` and `draft` are {source, backend, layers}. Each changed source,
// backend, order, added layer, removed layer, and layer field is one change.
export function draftChanges(baseline, draft) {
  const baseByUid = new Map(baseline.layers.map(layer => [layer.uid, layer]));
  const draftUids = new Set(draft.layers.map(layer => layer.uid));
  const rows = {};
  let total = 0;
  let added = 0;
  for (const layer of draft.layers) {
    const base = baseByUid.get(layer.uid);
    if (!base) {
      rows[layer.uid] = 'new';
      added += 1;
      continue;
    }
    rows[layer.uid] = layerChanges(base, layer).length;
    total += rows[layer.uid];
  }
  const removed = baseline.layers.filter(layer => !draftUids.has(layer.uid)).length;
  const kept = draft.layers.filter(layer => baseByUid.has(layer.uid)).map(layer => layer.uid);
  const keptBefore = baseline.layers.filter(layer => draftUids.has(layer.uid)).map(layer => layer.uid);
  const order = kept.some((uid, index) => uid !== keptBefore[index]);
  const source = draft.source !== baseline.source;
  const backend = draft.backend !== baseline.backend;
  total += added + removed + (order ? 1 : 0) + (source ? 1 : 0) + (backend ? 1 : 0);
  return {total, rows, added, removed, order, source, backend};
}

// ---- Bar mappings and their cost (D-066) -----------------------------------

// Layers with the same range, floor, and points share one bucketing of the
// spectrum (core/analyzer_registry.py); hidden layers count too.
export function mappingKey(settings) {
  const points = sortedPoints(settings?.weight_points || []).map(point => [point.position, point.weight]);
  return JSON.stringify([settings?.min_freq, settings?.max_freq, settings?.noise_floor, points]);
}

export function mappingCount(layers) {
  return new Set(layers.map(layer => mappingKey(layer.settings))).size;
}

export function costNote(layers) {
  const count = mappingCount(layers);
  if (count <= 1) return '';
  return `${layers.length} layers use ${count} different bar mappings of the one spectrum. ` +
    'Layers with the same range, floor, and points share one. Each extra one costs frame rate; ' +
    'two were measured at about 35% missed refreshes.';
}

// ---- Submit (D-062) ---------------------------------------------------------

// Why Send update must relaunch: a launch option or the matrix settings changed.
export function relaunchReasons({status, source, matrixChanged}) {
  const reasons = [];
  const launched = status?.parameters?.audio_source;
  if (launched !== undefined && launched !== source) reasons.push('the audio source changed');
  if (matrixChanged) reasons.push('the matrix settings changed since launch');
  return reasons;
}

export function relaunchText(programName, reasons) {
  return reasons.length ? `Relaunches ${programName}: ${reasons.join(' and ')}.` : '';
}

// Where a rejected document's error belongs. `path` is the server's, e.g.
// structure.layers[2].settings.weight_points[1].position; indices are the sent
// document's.
export function locateError(path) {
  const text = String(path || '');
  if (text === 'structure.output_backend') return {target: 'backend'};
  if (text === 'structure.layers') return {target: 'list'};
  const layer = /^structure\.layers\[(\d+)\](?:\.(.+))?$/.exec(text);
  if (!layer) return {target: 'page'};
  const index = Number(layer[1]);
  const rest = layer[2] || '';
  if (rest === 'layer_id') return {target: 'layer', index, field: 'layer_id'};
  const setting = /^settings\.([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\](?:\.([A-Za-z_]+))?)?$/.exec(rest);
  if (setting) {
    return {
      target: 'layer',
      index,
      field: setting[1],
      point: setting[2] === undefined ? null : Number(setting[2]),
      pointField: setting[3] || null,
    };
  }
  return {target: 'layer', index, field: null};
}

// One closed row's summary line (D-052): theme and range, and "hidden".
export function layerSummary(layer) {
  const settings = layer.settings || {};
  const parts = [];
  if (typeof settings.theme === 'string') parts.push(settings.theme);
  if (Number.isFinite(settings.min_freq) && Number.isFinite(settings.max_freq)) {
    parts.push(`${formatHz(settings.min_freq)}–${formatHz(settings.max_freq)}`);
  }
  if (!layer.visible) parts.push('hidden');
  return parts.join(' · ');
}
