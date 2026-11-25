// Music FFT presets on the page (web-control-refactor M5 slice 3: D-063, D-064,
// D-070–D-072). Pure, so node --test covers it; fft_preset_controls.js draws it.
//
// A stored preset is {preset_id, name, description, revision, parameters,
// structure} (matrix_controller/presets.py). The page reads one shape from it,
// "content": {source, backend, layers}, the same three things a draft holds.

import * as fft from './fft.js';

export const DEFAULT_ID = 'default';
export const DEFAULT_NAME = 'Default';
export const DEFAULT_DESCRIPTION = 'The built-in layer, output, and source.';

// The store's own limits (presets.py); the list reply's `limits` overrides them.
export const NAME_MAX = 64;
export const DESCRIPTION_MAX = 200;
export const PRESET_LIMIT = 32;

// Python's unicodedata category Cc: C0, DEL, and C1.
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/u;
const CONTROL_BUT_NEWLINE = /[\u0000-\u0009\u000b-\u001f\u007f-\u009f]/u;

// The store compares names with casefold(); toLowerCase is close, and the
// store's name_conflict reply covers the rest.
const fold = text => String(text).trim().toLowerCase();
const length = text => [...text].length;
const copy = value => (value === undefined ? undefined : JSON.parse(JSON.stringify(value)));
const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object, key);

// ---- Names and descriptions (presets.py rules) -----------------------------

// `presets` are the list entries; `excludeId` is the preset being renamed. The
// built-in Default keeps its name to itself (a page rule; the store allows it).
export function nameError(name, presets, {excludeId = null, max = NAME_MAX} = {}) {
  const trimmed = String(name ?? '').trim();
  if (!trimmed) return 'Give the preset a name.';
  if (length(trimmed) > max) return `Use at most ${max} characters.`;
  if (CONTROL.test(trimmed)) return 'Remove the control characters from the name.';
  if (fold(trimmed) === fold(DEFAULT_NAME)) return `${DEFAULT_NAME} is the built-in entry. Choose another name.`;
  if (presets.some(preset => preset.preset_id !== excludeId && fold(preset.name) === fold(trimmed))) {
    return 'Another preset has this name.';
  }
  return null;
}

export function descriptionError(text, {max = DESCRIPTION_MAX} = {}) {
  const trimmed = String(text ?? '').trim();
  if (length(trimmed) > max) return `Use at most ${max} characters.`;
  if (CONTROL_BUT_NEWLINE.test(trimmed)) return 'Use only text and line breaks.';
  return null;
}

// ---- The built-in Default (D-070) ----------------------------------------------

// The catalog's default layer and output, and the source's default.
export function defaultPreset(catalog, sourceDefault) {
  const structure = catalog?.structure || {};
  return {
    preset_id: DEFAULT_ID,
    name: DEFAULT_NAME,
    description: DEFAULT_DESCRIPTION,
    revision: 0,
    builtin: true,
    parameters: sourceDefault === undefined || sourceDefault === null ? {} : {audio_source: sourceDefault},
    structure: {output_backend: structure.output_backend, layers: copy(structure.layers || [])},
  };
}

// ---- Content -------------------------------------------------------------------

// What a stored preset holds, in the draft's shape. A preset saved without a
// source uses the default one.
export function storedContent(preset, sourceDefault) {
  const parameters = preset?.parameters || {};
  return {
    source: hasOwn(parameters, 'audio_source') ? parameters.audio_source : sourceDefault ?? null,
    backend: preset?.structure?.output_backend ?? null,
    layers: Array.isArray(preset?.structure?.layers) ? preset.structure.layers : [],
  };
}

// The create/replace body for a draft's content (D-071: name and description).
export function presetBody({name, description, source, backend, layers}) {
  return {
    name: String(name ?? '').trim(),
    description: String(description ?? '').trim(),
    parameters: source === undefined || source === null ? {} : {audio_source: source},
    structure: fft.structureDocument(backend, layers),
  };
}

function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])]));
  }
  return value;
}

// A comparison key for content: names trimmed, object keys sorted, and each
// points setting sorted by position (the FFT child returns them sorted), so a
// send whose reply matches the preset compares equal (criterion 6).
export function contentKey(content, descriptorOf) {
  const layers = (content?.layers || []).map(layer => {
    const settings = {...(layer.settings || {})};
    for (const field of descriptorOf(layer.layer_type)?.fields || []) {
      if (field.type === 'points' && Array.isArray(settings[field.name])) {
        settings[field.name] = fft.sortedPoints(settings[field.name]);
      }
    }
    return {
      layer_id: String(layer.layer_id ?? '').trim(),
      layer_type: layer.layer_type,
      analyzer_id: layer.analyzer_id,
      visible: layer.visible,
      settings,
    };
  });
  return JSON.stringify(stable({source: content?.source ?? null, backend: content?.backend ?? null, layers}));
}

// ---- Reconciling with the descriptor (D-064) -----------------------------------

function analyzerIds(info) {
  const listed = info?.available_analyzer_ids;
  if (Array.isArray(listed) && listed.length) return listed;
  return (info?.analyzers || []).map(analyzer => analyzer.analyzer_id);
}

// One stored layer against `info`'s descriptor, under `backend`'s fixed values.
// Returns {layer: null, layerType} for a layer type `info` doesn't list, else the
// layer (no uid), the values the lock replaced (for the D-061 stash), and what
// changed: settings filled from defaults, settings dropped, fixed values set, and
// the analyzer when its own isn't available.
export function reconcileLayer(stored, info, backend) {
  const descriptor = fft.descriptorFor(info, stored?.layer_type);
  if (!descriptor) return {layer: null, layerType: stored?.layer_type};
  const given = stored.settings && typeof stored.settings === 'object' ? stored.settings : {};
  const fields = descriptor.fields || [];
  const known = new Set(fields.map(field => field.name));
  const settings = {};
  const filled = [];
  for (const field of fields) {
    if (hasOwn(given, field.name)) {
      settings[field.name] = copy(given[field.name]);
    } else {
      settings[field.name] = copy(field.default);
      filled.push(field.label || field.name);
    }
  }
  const dropped = Object.keys(given).filter(name => !known.has(name));
  const before = settings;
  const locked = fft.lockLayer(before, descriptor, backend);
  const labels = new Map(fields.map(field => [field.name, field.label || field.name]));
  const fixed = Object.keys(locked.replaced)
    .filter(name => JSON.stringify(before[name]) !== JSON.stringify(locked.settings[name]))
    .map(name => labels.get(name) || name);
  const ids = analyzerIds(info);
  const analyzer = ids.length && !ids.includes(stored.analyzer_id) ? ids[0] : null;
  return {
    layer: {
      layer_id: String(stored.layer_id ?? ''),
      layer_type: stored.layer_type,
      analyzer_id: analyzer ?? stored.analyzer_id,
      visible: typeof stored.visible === 'boolean' ? stored.visible : true,
      settings: locked.settings,
    },
    replaced: locked.replaced,
    filled,
    dropped,
    fixed,
    analyzer,
  };
}

const unique = items => [...new Set(items)];

// Stored content against `info`: an unknown output falls back to the first
// listed, an unknown source to `sources.default`, and each layer as above.
// `replaced[i]` is the lock's replaced values for `layers[i]`.
export function reconcile(content, info, sources = {}) {
  const backends = fft.outputBackends(info);
  const backend = backends.includes(content?.backend) ? content.backend : backends[0] ?? null;
  const choices = sources.choices || [];
  const source = !choices.length || choices.includes(content?.source) ? content?.source ?? sources.default ?? null
    : sources.default ?? null;
  const layers = [];
  const replaced = [];
  const changes = {
    filled: [], dropped: [], fixed: [], droppedTypes: [], analyzer: null,
    backend: backend === (content?.backend ?? null) ? null : {from: content?.backend ?? null, to: backend},
    source: source === (content?.source ?? null) ? null : {from: content?.source ?? null, to: source},
  };
  for (const stored of content?.layers || []) {
    const result = reconcileLayer(stored, info, backend);
    if (!result.layer) {
      changes.droppedTypes.push(String(result.layerType));
      continue;
    }
    layers.push(result.layer);
    replaced.push(result.replaced);
    changes.filled.push(...result.filled);
    changes.dropped.push(...result.dropped);
    changes.fixed.push(...result.fixed);
    changes.analyzer ||= result.analyzer;
  }
  for (const key of ['filled', 'dropped', 'fixed', 'droppedTypes']) changes[key] = unique(changes[key]);
  return {source, backend, layers, replaced, changes};
}

export function hasChanges(changes) {
  return Boolean(changes.filled.length || changes.dropped.length || changes.fixed.length ||
    changes.droppedTypes.length || changes.analyzer || changes.backend || changes.source);
}

const count = (number, word) => `${number} ${word}${number === 1 ? '' : 's'}`;
const list = items => items.join(', ');

// The note after a load or a copy (D-064): what the page changed, and, for a
// saved preset, how to keep it. `subject` names it ("Warm bass", "bars 1 from Warm bass").
export function reconcileNote(subject, changes, {saveTo = null, backend = null} = {}) {
  const parts = [];
  if (changes.filled.length) {
    const one = changes.filled.length === 1;
    parts.push(`${subject} predates ${count(changes.filled.length, 'setting')} (${list(changes.filled)}); ` +
      `${one ? 'it uses its default' : 'they use their defaults'}.`);
  }
  if (changes.dropped.length) {
    const one = changes.dropped.length === 1;
    parts.push(`${list(changes.dropped)} ${one ? 'is' : 'are'} no longer ${one ? 'a setting' : 'settings'}, ` +
      `so ${one ? 'it was' : 'they were'} dropped.`);
  }
  if (changes.droppedTypes.length) {
    parts.push(`Layers of an unknown type (${list(changes.droppedTypes)}) were left out.`);
  }
  if (changes.backend) {
    parts.push(`Its output ${changes.backend.from ?? '(none)'} isn't available, so it uses ${changes.backend.to}.`);
  }
  if (changes.source) {
    parts.push(`Its source ${changes.source.from ?? '(none)'} isn't available, so it uses ${changes.source.to}.`);
  }
  if (changes.analyzer) parts.push(`It now uses the ${changes.analyzer} analyzer.`);
  if (changes.fixed.length) {
    parts.push(`${backend ?? 'The output'} fixes ${list(changes.fixed)}, so ${changes.fixed.length === 1 ? 'it was' : 'they were'} set.`);
  }
  if (parts.length && saveTo) parts.push(`Save to ${saveTo} to keep them.`);
  return parts.join(' ');
}

// ---- Copying a layer (D-070, D-072) ------------------------------------------------

// The layer's own name, or "name 2", "name 3", … when another layer has it,
// within the layer-name limit.
export function copyName(name, taken) {
  const base = String(name ?? '').trim() || 'layer';
  const used = new Set(taken.map(other => String(other).trim()));
  if (!used.has(base)) return base;
  for (let number = 2; ; number += 1) {
    const suffix = ` ${number}`;
    const stem = [...base].slice(0, fft.NAME_MAX - suffix.length).join('').trimEnd();
    const candidate = `${stem}${suffix}`;
    if (!used.has(candidate)) return candidate;
  }
}

// ---- The selector ------------------------------------------------------------------

// "Warm bass · edited" while the draft differs from the loaded preset (D-063).
export function optionLabel(entry, {loadedId = null, edited = false} = {}) {
  return entry.preset_id === loadedId && edited ? `${entry.name} · edited` : entry.name;
}

// Why saving is refused now, or null. `draftValid` covers the layers being saved.
export function saveBlocked({storeError = null, draftValid = true, creating = false, count = 0, limit = PRESET_LIMIT}) {
  if (storeError) return 'Saved presets are unavailable.';
  if (!draftValid) return 'Fix the marked fields before saving.';
  if (creating && count >= limit) return `${count} of ${limit} presets are saved. Delete one to save another.`;
  return null;
}

// The message for a failed preset request. `kind` is 'load', 'save', 'edit', or 'delete'.
export function failureText(error, name, kind) {
  if (error?.status === 404) return `${name} was deleted on another device. The list is refreshed.`;
  if (error?.code === 'revision_conflict') {
    return `${name} was changed on another device. Reload the page to see it; nothing was saved.`;
  }
  if (error?.code === 'limit_exceeded') return 'The Pi holds the most presets it can. Delete one to save another.';
  const verb = {load: 'load', save: 'save', edit: 'change', delete: 'delete'}[kind] || kind;
  return `Could not ${verb} ${name}: ${error?.message || 'the request failed.'}`;
}
