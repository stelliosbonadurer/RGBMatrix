// Music FFT page rules (web-control-refactor M5 slice 1), run by tests/test_web_pages.py.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

import {parseValue} from '../../matrix_controller/static/js/model.js';
import * as fft from '../../matrix_controller/static/js/programs/fft.js';

const catalog = JSON.parse(readFileSync(new URL('../../matrix_controller/static/fft_catalog.json', import.meta.url)));

// A made-up layer type: no field name below is one the FFT uses (criterion 8).
const madeUp = {
  groups: [{id: 'shape', label: 'Shape'}, {id: 'color', label: 'Color'}],
  fields: [
    {name: 'tint', label: 'Tint', group: 'color', type: 'choice', choices: ['red', 'blue', 'cycle'], default: 'red'},
    {name: 'cycle_rate', label: 'Cycle rate', group: 'color', type: 'number', min: 0, max: 4, default: 1,
      shown_when: {field: 'tint', values: ['cycle']}},
    {name: 'width', label: 'Width', group: 'shape', type: 'integer', min: 1, max: 8, default: 2},
    {name: 'glow', label: 'Glow', group: 'shape', type: 'boolean', default: true},
    {name: 'loose', label: 'Loose', type: 'boolean', default: false},
  ],
  backend_constraints: {'slow-path': {glow: false, tint: 'blue'}},
};

let uid = 0;
const nextUid = () => `u${++uid}`;
const layer = (name, settings = {}, extra = {}) => ({
  uid: nextUid(), layer_id: name, layer_type: 'made-up', analyzer_id: 'a', visible: true,
  settings: {...fft.defaultSettings(madeUp), ...settings}, ...extra,
});

test('the catalog has the shape the page reads', () => {
  const structure = catalog.structure;
  assert.equal(fft.maxLayers(structure), 8);
  assert.deepEqual(fft.outputBackends(structure), ['direct', 'pillow-bulk']);
  const facts = fft.analyzerFacts(structure, catalog.assumed_until_launch);
  assert.deepEqual([facts.sampleRate, facts.fftSize, facts.blockSize], [44100, 8192, 512]);
  assert.ok(facts.assumed.has('sample_rate'));
  assert.deepEqual(fft.layerTypes(structure).map(item => item.layer_type), ['current-bars']);
  const descriptor = fft.descriptorFor(structure, 'current-bars');
  const groups = fft.settingGroups(descriptor);
  assert.deepEqual(groups.map(group => group.label), ['Range & weighting', 'Level', 'Look', 'Peaks', 'Motion', 'Dynamic theme']);
  assert.deepEqual(groups[0].fields.map(field => field.label), ['Min Hz', 'Max Hz', 'Noise floor (dBFS)', 'Weighting points']);
  assert.deepEqual(groups[1].fields.map(field => field.label), ['Gain', 'Auto level', 'Headroom', 'Attack (s)', 'Release (s)', 'Max auto boost (dB)']);
  // The default layer is every descriptor default.
  assert.deepEqual(structure.layers[0].settings, fft.defaultSettings(descriptor));
});

test('the list shows the top layer first (D-054)', () => {
  const layers = ['bottom', 'middle', 'top'];
  assert.deepEqual(fft.displayRows(layers), ['top', 'middle', 'bottom']);
  assert.equal(fft.indexOfRow(3, 0), 2);
  assert.equal(fft.indexOfRow(3, 2), 0);
  assert.deepEqual(layers, ['bottom', 'middle', 'top']); // not mutated
});

test('moves are one place, and the ends stay put', () => {
  const layers = ['a', 'b', 'c'];
  assert.deepEqual(fft.moveLayer(layers, 0, 1), ['b', 'a', 'c']); // ▲ on the bottom row
  assert.deepEqual(fft.moveLayer(layers, 2, -1), ['a', 'c', 'b']); // ▼ on the top row
  assert.deepEqual(fft.moveLayer(layers, 2, 1), ['a', 'b', 'c']);
  assert.deepEqual(fft.moveLayer(layers, 0, -1), ['a', 'b', 'c']);
  assert.deepEqual(layers, ['a', 'b', 'c']);
});

test('names follow the server rules (D-057)', () => {
  assert.equal(fft.nameError('bass', ['treble']), null);
  assert.equal(fft.nameError('  bass  ', []), null); // sent trimmed
  assert.match(fft.nameError('', []), /name/);
  assert.match(fft.nameError('   ', []), /name/);
  assert.equal(fft.nameError('x'.repeat(64), []), null);
  assert.match(fft.nameError('x'.repeat(65), []), /64/);
  assert.equal(fft.nameError('🎵'.repeat(64), []), null); // characters, not UTF-16 units
  assert.match(fft.nameError('a\u0007b', []), /control/);
  assert.match(fft.nameError('a\u007fb', []), /control/);
  assert.match(fft.nameError('bass', ['bass ']), /Another/);
  assert.equal(fft.nameError('Bass', ['bass']), null); // the server compares exactly
});

test('new layers get a free name from their type', () => {
  assert.equal(fft.typeWord('current-bars'), 'bars');
  assert.equal(fft.freeName('current-bars', ['current-bars']), 'bars 2');
  assert.equal(fft.freeName('current-bars', ['current-bars', 'bars 2']), 'bars 3');
  assert.equal(fft.freeName('current-bars', ['bars 2', 'bars 3']), 'bars 4');
  assert.equal(fft.freeName('current-bars', []), 'bars 1');
});

test('the sent document is in draw order with trimmed names and no page handles', () => {
  const layers = [layer(' low '), layer('high', {}, {visible: false})];
  const document = fft.structureDocument('direct', layers);
  assert.deepEqual(document.layers.map(item => item.layer_id), ['low', 'high']);
  assert.equal(document.output_backend, 'direct');
  assert.equal(document.layers[1].visible, false);
  assert.ok(document.layers.every(item => !('uid' in item)));
  document.layers[0].settings.width = 9;
  assert.equal(layers[0].settings.width, 2); // a copy
});

test('settings come only from the descriptor, under its groups (D-053)', () => {
  const groups = fft.settingGroups(madeUp);
  assert.deepEqual(groups.map(group => [group.label, group.fields.map(field => field.name)]),
    [['Shape', ['width', 'glow']], ['Color', ['tint', 'cycle_rate']], ['Other', ['loose']]]);
  const width = fft.settingParameter(madeUp.fields[2]);
  assert.deepEqual([width.type, width.minimum, width.maximum, width.label], ['int', 1, 8, 'Width']);
  assert.equal(parseValue(width, '9').error, 'Width must be at most 8.');
  assert.equal(fft.settingParameter(madeUp.fields[0]).type, 'enum');
  assert.equal(fft.settingParameter(madeUp.fields[3]).type, 'bool');
  assert.equal(fft.settingParameter(madeUp.fields[1]).type, 'float');
  const cycle = madeUp.fields[1];
  assert.equal(fft.isShown(cycle, {tint: 'red'}), false);
  assert.equal(fft.isShown(cycle, {tint: 'cycle'}), true);
  assert.equal(fft.isShown(madeUp.fields[2], {}), true);
});

test('the range must increase, as the server requires (criterion 9)', () => {
  const labelOf = name => ({min_freq: 'Min Hz', max_freq: 'Max Hz'})[name];
  assert.deepEqual(fft.crossFieldErrors({min_freq: 8000, max_freq: 6300}, labelOf),
    {max_freq: 'Max Hz must be greater than Min Hz.'});
  assert.deepEqual(fft.crossFieldErrors({min_freq: 6300, max_freq: 6300}, labelOf),
    {max_freq: 'Max Hz must be greater than Min Hz.'});
  assert.deepEqual(fft.crossFieldErrors({min_freq: 100, max_freq: 6300}), {});
  // An unparsable field has its own error; no second one is added.
  assert.deepEqual(fft.crossFieldErrors({min_freq: NaN, max_freq: 6300}), {});
  assert.deepEqual(fft.crossFieldErrors({}), {});
});

test('a hidden field keeps its value in the sent document', () => {
  const hidden = layer('a', {tint: 'red', cycle_rate: 3.5});
  assert.equal(fft.isShown(madeUp.fields[1], hidden.settings), false);
  assert.equal(fft.structureDocument('direct', [hidden]).layers[0].settings.cycle_rate, 3.5);
});

test('a constrained backend sets and locks its fields; switching back restores them (D-061)', () => {
  const descriptorOf = () => madeUp;
  assert.deepEqual([...fft.lockedNames(madeUp, 'slow-path')], ['glow', 'tint']);
  assert.equal(fft.lockedNames(madeUp, 'fast-path').size, 0);
  const layers = [layer('a', {glow: true, tint: 'cycle'}), layer('b', {glow: false, tint: 'blue'})];
  const locked = fft.switchBackend(layers, descriptorOf, {}, 'slow-path');
  assert.deepEqual(locked.layers.map(item => [item.settings.glow, item.settings.tint]), [[false, 'blue'], [false, 'blue']]);
  assert.deepEqual(locked.changes.map(change => change.name), ['tint', 'glow']);
  assert.deepEqual(locked.stash[layers[0].uid], {glow: true, tint: 'cycle'});
  assert.equal(locked.layers[0].settings.width, 2); // unconstrained fields untouched

  const back = fft.switchBackend(locked.layers, descriptorOf, locked.stash, 'fast-path');
  assert.deepEqual(back.layers.map(item => [item.settings.glow, item.settings.tint]), [[true, 'cycle'], [false, 'blue']]);
  assert.deepEqual(back.stash, {});

  // After a send the stash is gone, so switching back keeps the fixed values.
  const afterSend = fft.switchBackend(locked.layers, descriptorOf, {}, 'fast-path');
  assert.deepEqual(afterSend.layers[0].settings.tint, 'blue');
  assert.deepEqual(afterSend.changes, []);
});

test('apply to all copies one value into every other same-type layer, not into fixed fields (fft-level-controls D-018, D-029)', () => {
  const other = {...madeUp, fields: [...madeUp.fields]};
  const descriptorOf = type => (type === 'other' ? other : madeUp);
  const from = layer('from', {width: 5, tint: 'cycle'});
  const same = layer('same', {width: 2});
  const hidden = layer('hidden', {width: 3}, {visible: false});
  const already = layer('already', {width: 5});
  const foreign = {...layer('foreign', {width: 1}), layer_type: 'other'};
  const layers = [from, same, hidden, already, foreign];

  const width = fft.applyToAll(layers, from.uid, 'width', descriptorOf, 'fast-path');
  assert.deepEqual(width.changes.map(change => [change.uid, change.from, change.to]),
    [[same.uid, 2, 5], [hidden.uid, 3, 5]]);
  assert.equal(width.targets, 3); // same, hidden, already; never another type
  assert.deepEqual(layers.map(item => item.settings.width), [5, 2, 3, 5, 1]); // not mutated

  // A field the backend fixes is never copied.
  const fixed = fft.applyToAll(layers, from.uid, 'tint', descriptorOf, 'slow-path');
  assert.deepEqual([fixed.changes, fixed.targets], [[], 0]);
  const free = fft.applyToAll(layers, from.uid, 'tint', descriptorOf, 'fast-path');
  assert.equal(free.changes.length, 3);

  // Values are copies, so editing one layer later can't change another.
  const points = {...madeUp, fields: [...madeUp.fields, {name: 'marks', type: 'points', default: []}]};
  const a = layer('a', {marks: [{position: 0, weight: 2}]});
  const b = layer('b', {marks: []});
  const copied = fft.applyToAll([a, b], a.uid, 'marks', () => points, 'fast-path');
  assert.deepEqual(copied.changes[0].to, [{position: 0, weight: 2}]);
  assert.notEqual(copied.changes[0].to, a.settings.marks);
  assert.deepEqual(fft.applyToAll([a], a.uid, 'marks', () => points, 'fast-path'), {changes: [], targets: 0});
});

test('a constraint removed from the descriptor disappears from the page', () => {
  const fewer = {...madeUp, backend_constraints: {'slow-path': {glow: false}}};
  const result = fft.switchBackend([layer('a', {tint: 'cycle'})], () => fewer, {}, 'slow-path');
  assert.equal(result.layers[0].settings.tint, 'cycle');
  assert.deepEqual([...fft.lockedNames(fewer, 'slow-path')], ['glow']);
});

test('a layer added under a constrained backend starts locked and restores to defaults', () => {
  const {settings, replaced} = fft.lockLayer(fft.defaultSettings(madeUp), madeUp, 'slow-path');
  assert.deepEqual([settings.glow, settings.tint], [false, 'blue']);
  assert.deepEqual(replaced, {glow: true, tint: 'red'});
  const added = {...layer('new'), settings};
  const back = fft.switchBackend([added], () => madeUp, {[added.uid]: replaced}, 'fast-path');
  assert.deepEqual([back.layers[0].settings.glow, back.layers[0].settings.tint], [true, 'red']);
});

test('weighting points: curve, add, duplicates (D-021, D-060)', () => {
  const points = [{position: 0.6, weight: 4}, {position: 0, weight: 1}, {position: 1, weight: 10}];
  assert.equal(fft.curveWeight(points, -1), 1);
  assert.equal(fft.curveWeight(points, 0.3), 2.5);
  assert.equal(fft.curveWeight(points, 2), 10);
  const added = fft.addPoint(points);
  assert.deepEqual(added.points.map(point => point.position), [0, 0.3, 0.6, 1]);
  assert.equal(added.index, 1);
  assert.equal(added.points[1].weight, 2.5);
  // The range ends count as gap edges, and positions round to 0.001.
  const single = fft.addPoint([{position: 0.9, weight: 2}]);
  assert.deepEqual(single.points, [{position: 0.45, weight: 2}, {position: 0.9, weight: 2}]);
  const catalogPoints = fft.descriptorFor(catalog.structure, 'current-bars').fields
    .find(field => field.name === 'weight_points').default;
  const third = fft.addPoint(catalogPoints).points[4];
  assert.equal(third.position, 0.8);
  assert.equal(third.weight, Number(fft.curveWeight(catalogPoints, 0.8).toPrecision(3)));
  assert.deepEqual([...fft.duplicatePositions([0, 0.5, 0, NaN, 0.5])], [2, 4]);
  assert.equal(Math.round(fft.positionHz(100, 6400, 0.5)), 800);
});

test('point bounds come from the descriptor', () => {
  const field = fft.descriptorFor(catalog.structure, 'current-bars').fields.find(item => item.name === 'weight_points');
  const [position, weight] = field.item_fields.map(fft.settingParameter);
  assert.equal(parseValue(position, '1.5').error, 'Position must be at most 1.');
  assert.equal(parseValue(position, '-0.1').error, 'Position must be at least 0.');
  assert.equal(parseValue(weight, '100').error, null);
  assert.equal(parseValue(weight, '101').error, 'Weight must be at most 100.');
  assert.deepEqual([field.min_items, field.max_items], [1, 16]);
});

test('unsent changes count each edit once (D-052)', () => {
  const a = layer('a');
  const b = layer('b');
  const baseline = {source: 'usb-mic', backend: 'direct', layers: [a, b]};
  assert.equal(fft.draftChanges(baseline, baseline).total, 0);
  const edited = {...b, layer_id: 'bee', settings: {...b.settings, width: 5, glow: false}};
  const added = layer('c');
  const draft = {source: 'bluetooth', backend: 'slow-path', layers: [edited, added]};
  const changes = fft.draftChanges(baseline, draft);
  assert.deepEqual(changes.rows, {[b.uid]: 3, [added.uid]: 'new'});
  assert.deepEqual([changes.added, changes.removed, changes.order, changes.source, changes.backend],
    [1, 1, false, true, true]);
  assert.equal(changes.total, 3 + 1 + 1 + 1 + 1);
  const swapped = fft.draftChanges(baseline, {...baseline, layers: [b, a]});
  assert.deepEqual([swapped.order, swapped.total], [true, 1]);
  assert.equal(fft.draftChanges(baseline, {...baseline, layers: [a, {...b, visible: false}]}).total, 1);
  assert.equal(fft.draftChanges(baseline, {...baseline, layers: [a, {...b, layer_id: ' b '}]}).total, 0);
});

test('the cost note counts distinct bar mappings over every layer (D-066)', () => {
  const settings = {min_freq: 100, max_freq: 6300, noise_floor_dbfs: -65, weight_points: [{position: 0, weight: 1}, {position: 1, weight: 2}]};
  const same = {...settings, weight_points: [...settings.weight_points].reverse(), theme: 'warm'};
  const hidden = {...settings, min_freq: 2000};
  const layers = [{settings, visible: true}, {settings: same, visible: true}];
  assert.equal(fft.mappingCount(layers), 1);
  assert.equal(fft.costNote(layers), '');
  const more = [...layers, {settings: hidden, visible: false}, {settings: {...settings, noise_floor_dbfs: -50}, visible: true}];
  assert.equal(fft.mappingCount(more), 3);
  assert.match(fft.costNote(more), /^4 layers use 3 different bar mappings of the one spectrum\./);
  assert.match(fft.costNote(more), /35% missed refreshes/);
  assert.equal(fft.costNote([]), '');
});

test('error paths map to the layer and field that holds them', () => {
  assert.deepEqual(fft.locateError('structure.output_backend'), {target: 'backend'});
  assert.deepEqual(fft.locateError('structure.layers'), {target: 'list'});
  assert.deepEqual(fft.locateError('structure'), {target: 'page'});
  assert.deepEqual(fft.locateError(undefined), {target: 'page'});
  assert.deepEqual(fft.locateError('structure.layers[3].layer_id'), {target: 'layer', index: 3, field: 'layer_id'});
  assert.deepEqual(fft.locateError('structure.layers[0].settings.max_freq'),
    {target: 'layer', index: 0, field: 'max_freq', point: null, pointField: null});
  assert.deepEqual(fft.locateError('structure.layers[1].settings.weight_points[2].position'),
    {target: 'layer', index: 1, field: 'weight_points', point: 2, pointField: 'position'});
  assert.deepEqual(fft.locateError('structure.layers[1].settings'), {target: 'layer', index: 1, field: null});
  assert.deepEqual(fft.locateError('structure.layers[1]'), {target: 'layer', index: 1, field: null});
});

test('Send update relaunches only for the source or the matrix settings (D-062)', () => {
  const status = {state: 'running', visualization_id: 'fft', parameters: {audio_source: 'usb-mic'}};
  assert.deepEqual(fft.relaunchReasons({status, source: 'usb-mic', matrixChanged: false}), []);
  const both = fft.relaunchReasons({status, source: 'bluetooth', matrixChanged: true});
  assert.deepEqual(both, ['the audio source changed', 'the matrix settings changed since launch']);
  assert.equal(fft.relaunchText('Music FFT', both),
    'Relaunches Music FFT: the audio source changed and the matrix settings changed since launch.');
  assert.equal(fft.relaunchText('Music FFT', []), '');
});

test('row summaries and Hz formatting', () => {
  assert.equal(fft.layerSummary({visible: true, settings: {theme: 'ocean', min_freq: 100, max_freq: 6300}}), 'ocean · 100 Hz–6.3 kHz');
  assert.equal(fft.layerSummary({visible: false, settings: {theme: 'warm', min_freq: 40, max_freq: 250}}), 'warm · 40 Hz–250 Hz · hidden');
  assert.equal(fft.formatHz(44100), '44.1 kHz');
  assert.equal(fft.formatHz(1234.5), '1.23 kHz');
  assert.equal(fft.sourceLabel('usb-mic'), 'USB mic');
  assert.equal(fft.sourceLabel('other'), 'other');
});
