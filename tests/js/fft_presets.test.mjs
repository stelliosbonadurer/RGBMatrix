// Music FFT preset rules (web-control-refactor M5 slice 3), run by tests/test_web_pages.py.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

import * as fft from '../../matrix_controller/static/js/programs/fft.js';
import * as presets from '../../matrix_controller/static/js/programs/fft_presets.js';

const catalog = JSON.parse(readFileSync(new URL('../../matrix_controller/static/fft_catalog.json', import.meta.url)));

// A made-up layer type and structure: no name below is one the FFT uses (D-061).
const descriptor = {
  groups: [{id: 'shape', label: 'Shape'}],
  fields: [
    {name: 'tint', label: 'Tint', group: 'shape', type: 'choice', choices: ['red', 'blue'], default: 'red'},
    {name: 'width', label: 'Width', group: 'shape', type: 'integer', min: 1, max: 8, default: 2},
    {name: 'glow', label: 'Glow', group: 'shape', type: 'boolean', default: true},
    {name: 'marks', label: 'Marks', group: 'shape', type: 'points', default: [{position: 0, weight: 1}]},
  ],
  backend_constraints: {'slow-path': {glow: false}},
};
const info = {
  output_backend: 'fast-path',
  available_analyzer_ids: ['ear'],
  capabilities: {
    layer_types: [{layer_type: 'made-up', settings: descriptor}],
    output_backends: ['fast-path', 'slow-path'],
    limits: {max_layers: 8},
  },
};
const descriptorOf = type => fft.descriptorFor(info, type);
const sources = {choices: ['mic', 'line'], default: 'mic'};
const stored = (settings, extra = {}) => ({
  layer_id: 'one', layer_type: 'made-up', analyzer_id: 'ear', visible: true, settings, ...extra,
});
const full = {tint: 'blue', width: 3, glow: true, marks: [{position: 0, weight: 2}]};

test('preset names follow the store rules, and Default is the built-in name', () => {
  const list = [{preset_id: 'a', name: 'Warm bass'}, {preset_id: 'b', name: 'Treble'}];
  assert.equal(presets.nameError('  ', list), 'Give the preset a name.');
  assert.equal(presets.nameError('x'.repeat(64), list), null);
  assert.match(presets.nameError('x'.repeat(65), list), /at most 64/);
  assert.equal(presets.nameError('😀'.repeat(64), list), null); // characters, not UTF-16 units
  assert.match(presets.nameError('bad\u0007name', list), /control/);
  assert.match(presets.nameError('bad\u0085name', list), /control/); // C1, as unicodedata Cc
  assert.equal(presets.nameError(' WARM BASS ', list), 'Another preset has this name.');
  assert.equal(presets.nameError('warm bass', list, {excludeId: 'a'}), null);
  assert.match(presets.nameError('default', list), /built-in/);
  assert.match(presets.nameError('ab', list, {max: 1}), /at most 1/);
});

test('descriptions allow line breaks and at most 200 characters', () => {
  assert.equal(presets.descriptionError(''), null);
  assert.equal(presets.descriptionError(' Low end\nfor the kick '), null);
  assert.equal(presets.descriptionError(`${'x'.repeat(200)}  `), null);
  assert.match(presets.descriptionError('x'.repeat(201)), /at most 200/);
  assert.match(presets.descriptionError('one\ttwo'), /line breaks/);
  assert.match(presets.descriptionError('one\rtwo'), /line breaks/);
});

test('reconcile fills, drops, falls back, locks, and says what changed (D-064)', () => {
  const content = {
    source: 'radio',
    backend: 'old-path',
    layers: [
      stored({tint: 'blue', retired: 1, gone: 2}, {analyzer_id: 'old-ear'}),
      stored(full, {layer_id: 'two', layer_type: 'sparkles'}),
      stored(full, {layer_id: 'three', visible: false}),
    ],
  };
  const result = presets.reconcile(content, info, sources);
  assert.equal(result.backend, 'fast-path');
  assert.equal(result.source, 'mic');
  assert.deepEqual(result.layers.map(layer => layer.layer_id), ['one', 'three']);
  assert.deepEqual(result.layers[0].settings, {tint: 'blue', width: 2, glow: true, marks: [{position: 0, weight: 1}]});
  assert.equal(result.layers[0].analyzer_id, 'ear');
  assert.equal(result.layers[1].visible, false);
  assert.deepEqual(result.changes.filled, ['Width', 'Glow', 'Marks']);
  assert.deepEqual(result.changes.dropped, ['retired', 'gone']);
  assert.deepEqual(result.changes.droppedTypes, ['sparkles']);
  assert.deepEqual(result.changes.backend, {from: 'old-path', to: 'fast-path'});
  assert.deepEqual(result.changes.source, {from: 'radio', to: 'mic'});
  assert.equal(result.changes.analyzer, 'ear');
  const note = presets.reconcileNote('Warm bass', result.changes, {saveTo: 'Warm bass'});
  assert.match(note, /^Warm bass predates 3 settings \(Width, Glow, Marks\); they use their defaults\./);
  assert.match(note, /retired, gone are no longer settings/);
  assert.match(note, /unknown type \(sparkles\)/);
  assert.match(note, /Save to Warm bass to keep them\.$/);
  assert.doesNotMatch(presets.reconcileNote('Default', result.changes), /Save to/);
  // Nothing to say, nothing said.
  const clean = presets.reconcile({source: 'line', backend: 'slow-path', layers: [stored({...full, glow: false})]}, info, sources);
  assert.equal(presets.hasChanges(clean.changes), false);
  assert.equal(presets.reconcileNote('Warm bass', clean.changes, {saveTo: 'Warm bass'}), '');
});

test('a loaded output sets its fixed values and keeps the replaced ones (D-061)', () => {
  const result = presets.reconcile({source: 'mic', backend: 'slow-path', layers: [stored(full)]}, info, sources);
  assert.equal(result.layers[0].settings.glow, false);
  assert.deepEqual(result.replaced, [{glow: true}]);
  assert.deepEqual(result.changes.fixed, ['Glow']);
  assert.match(presets.reconcileNote('X', result.changes, {backend: 'slow-path'}), /slow-path fixes Glow, so it was set\./);
  const layer = presets.reconcileLayer(stored(full), info, 'fast-path');
  assert.deepEqual([layer.layer.settings.glow, layer.fixed], [true, []]);
  assert.deepEqual(presets.reconcileLayer(stored(full, {layer_type: 'x'}), info, 'fast-path'), {layer: null, layerType: 'x'});
  // A layer type whose descriptor lists no fields keeps no settings (review finding 8).
  const bare = {...info, capabilities: {...info.capabilities, layer_types: [{layer_type: 'made-up', settings: {}}]}};
  const result2 = presets.reconcileLayer(stored({tint: 'red'}), bare, 'fast-path');
  assert.deepEqual([result2.layer.settings, result2.dropped], [{}, ['tint']]);
});

test('the catalog and a snapshot shaped like it reconcile unchanged', () => {
  const structure = catalog.structure;
  const content = {source: 'usb-mic', backend: structure.output_backend, layers: structure.layers};
  const result = presets.reconcile(content, structure, {choices: ['usb-mic', 'bluetooth'], default: 'usb-mic'});
  assert.equal(presets.hasChanges(result.changes), false);
  assert.deepEqual(result.layers, structure.layers);
});

test('a layer saved before seconds-based timing drops its per-call keys and fills the new ones (fft-level-controls D-016, D-023)', () => {
  const structure = catalog.structure;
  const current = structure.layers[0].settings;
  const retired = {peak_fall_speed: 0.2, peak_hold_frames: 20, smoothing_rise: 0.6, smoothing_fall: 0.3};
  const renamed = ['peak_hold_seconds', 'peak_fall_rate', 'smoothing_rise_seconds', 'smoothing_fall_seconds',
    'auto_level', 'auto_level_headroom', 'auto_level_attack_seconds', 'auto_level_release_seconds'];
  const old = Object.fromEntries(Object.entries(current).filter(([name]) => !renamed.includes(name)));
  const saved = {...structure.layers[0], settings: {...old, ...retired, gain: 2}};
  const content = {source: 'usb-mic', backend: structure.output_backend, layers: [saved]};
  const result = presets.reconcile(content, structure, {choices: ['usb-mic'], default: 'usb-mic'});
  assert.deepEqual([...result.changes.dropped].sort(), Object.keys(retired).sort());
  const labels = new Map(fft.descriptorFor(structure, 'current-bars').fields.map(field => [field.name, field.label]));
  assert.deepEqual([...result.changes.filled].sort(), renamed.map(name => labels.get(name)).sort());
  const settings = result.layers[0].settings;
  for (const name of renamed) assert.deepEqual(settings[name], current[name]); // the defaults
  assert.equal(settings.gain, 2); // kept
  const note = presets.reconcileNote('Warm bass', result.changes);
  assert.match(note, /smoothing_rise/);
});

test('Default is the catalog layer, output, and source (D-070)', () => {
  const preset = presets.defaultPreset(catalog, 'usb-mic');
  assert.equal(preset.preset_id, presets.DEFAULT_ID);
  assert.equal(preset.builtin, true);
  assert.deepEqual(preset.parameters, {audio_source: 'usb-mic'});
  assert.equal(preset.structure.output_backend, catalog.structure.output_backend);
  assert.deepEqual(preset.structure.layers, catalog.structure.layers);
  assert.notEqual(preset.structure.layers, catalog.structure.layers); // a copy
  assert.deepEqual(presets.defaultPreset(catalog, null).parameters, {});
});

test('content keys ignore point order, key order, names\' spaces, and page handles', () => {
  const draft = {
    source: 'mic',
    backend: 'fast-path',
    layers: [{uid: 'u1', ...stored({...full, marks: [{weight: 1, position: 1}, {position: 0, weight: 2}]}, {layer_id: ' one '})}],
  };
  const body = presets.presetBody({name: ' Warm ', description: ' low\n', ...draft});
  assert.deepEqual([body.name, body.description, body.parameters], ['Warm', 'low', {audio_source: 'mic'}]);
  assert.equal(body.structure.layers[0].layer_id, 'one');
  assert.equal('uid' in body.structure.layers[0], false);
  // The child returns points sorted; the reply still matches the preset.
  const reply = JSON.parse(JSON.stringify(body));
  reply.structure.layers[0].settings.marks.reverse();
  const saved = presets.storedContent(body, 'mic');
  assert.equal(presets.contentKey(draft, descriptorOf), presets.contentKey(saved, descriptorOf));
  assert.equal(presets.contentKey(presets.storedContent(reply, 'mic'), descriptorOf), presets.contentKey(saved, descriptorOf));
  const edited = {...draft, layers: [{...draft.layers[0], settings: {...draft.layers[0].settings, width: 4}}]};
  assert.notEqual(presets.contentKey(edited, descriptorOf), presets.contentKey(saved, descriptorOf));
  assert.notEqual(presets.contentKey({...draft, source: 'line'}, descriptorOf), presets.contentKey(saved, descriptorOf));
  // No source saved: the default one.
  assert.equal(presets.storedContent({parameters: {}, structure: {layers: []}}, 'mic').source, 'mic');
  assert.deepEqual(presets.presetBody({name: 'x', source: null, backend: 'b', layers: []}).parameters, {});
});

test('a copied layer keeps its name unless another layer has it', () => {
  assert.equal(presets.copyName('bars 1', ['bars 2']), 'bars 1');
  assert.equal(presets.copyName(' bars 1 ', ['bars 1']), 'bars 1 2');
  assert.equal(presets.copyName('bars 1', ['bars 1', 'bars 1 2']), 'bars 1 3');
  assert.equal(presets.copyName('', ['layer']), 'layer 2');
  const long = 'x'.repeat(64);
  const named = presets.copyName(long, [long]);
  assert.equal(named, `${'x'.repeat(62)} 2`);
  assert.equal(fft.nameError(named, [long]), null);
});

test('the selector, the save guard, and the failure messages', () => {
  const entry = {preset_id: 'a', name: 'Warm bass'};
  assert.equal(presets.optionLabel(entry, {loadedId: 'a', edited: true}), 'Warm bass · edited');
  assert.equal(presets.optionLabel(entry, {loadedId: 'a', edited: false}), 'Warm bass');
  assert.equal(presets.optionLabel(entry, {loadedId: 'b', edited: true}), 'Warm bass');

  assert.equal(presets.saveBlocked({}), null);
  assert.equal(presets.saveBlocked({draftValid: false}), 'Fix the marked fields before saving.');
  assert.match(presets.saveBlocked({creating: true, count: 32, limit: 32}), /32 of 32/);
  assert.equal(presets.saveBlocked({creating: false, count: 32, limit: 32}), null);
  assert.match(presets.saveBlocked({storeError: 'x'}), /unavailable/);

  assert.match(presets.failureText({status: 404}, 'Warm bass', 'load'), /deleted on another device/);
  assert.equal(presets.failureText({status: 409, code: 'revision_conflict'}, 'Warm bass', 'save'),
    'Warm bass was changed on another device. Reload the page to see it; nothing was saved.');
  assert.match(presets.failureText({status: 400, code: 'limit_exceeded'}, 'X', 'save'), /Delete one/);
  assert.equal(presets.failureText({status: 503, message: 'preset file can\'t be read'}, 'X', 'delete'),
    'Could not delete X: preset file can\'t be read');
});
