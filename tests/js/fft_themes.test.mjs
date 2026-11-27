// Custom FFT theme rules on the page (fft-themes M3), run by tests/test_web_pages.py.
// tests/test_web_pages.py also feeds documents built here to the FFT's parser.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

import * as fft from '../../matrix_controller/static/js/programs/fft.js';
import * as presets from '../../matrix_controller/static/js/programs/fft_presets.js';
import * as themes from '../../matrix_controller/static/js/programs/fft_themes.js';

const catalog = JSON.parse(readFileSync(new URL('../../matrix_controller/static/fft_catalog.json', import.meta.url)));
const structure = catalog.structure;
const layerType = structure.layers[0].layer_type;
const builtins = themes.themeChoices(structure, layerType);

const theme = (changes = {}) => ({...themes.newTheme([]), ...changes});
const doc = (...list) => ({themes: list});

test('saved names come from a usable snapshot only', () => {
  const snapshot = {error: null, document: doc(theme({name: 'my_a'}), theme({name: 'my_b'}))};
  assert.deepEqual(themes.savedThemeNames(snapshot), ['my_a', 'my_b']);
  assert.deepEqual(themes.savedThemeNames({...snapshot, error: 'the saved custom themes file is not valid JSON'}), []);
  assert.deepEqual(themes.savedThemeNames({error: null, document: {themes: []}}), []);
  assert.deepEqual(themes.savedThemeNames(null), []);
});

test('saved names extend every Theme choice list without changing the structure', () => {
  const before = JSON.stringify(structure);
  const extended = themes.withThemeChoices(structure, ['my_a', builtins[0]]);
  assert.deepEqual(themes.themeChoices(extended, layerType), [...builtins, 'my_a']);
  assert.equal(JSON.stringify(structure), before);
  assert.equal(themes.withThemeChoices(structure, []), structure);
  assert.equal(themes.themeDefault(extended, layerType), 'ocean');
  assert.equal(themes.withThemeChoices(null, ['my_a']), null);
});

test('a run\'s loaded custom themes are choices only while it runs (review finding 1)', () => {
  const live = themes.withThemeChoices(structure, ['my_loaded', 'my_both']);
  assert.deepEqual(themes.themeChoices(themes.themeInfo(live, ['my_both', 'my_new'], true), layerType),
    [...builtins, 'my_loaded', 'my_both', 'my_new']);
  assert.deepEqual(themes.themeChoices(themes.themeInfo(live, ['my_both', 'my_new'], false), layerType),
    [...builtins, 'my_both', 'my_new']);
  assert.deepEqual(themes.themeChoices(themes.themeInfo(structure, [], false), layerType), builtins);
});

test('reconcile sets a theme the choices lack to the default and says so (D-013)', () => {
  const extended = themes.withThemeChoices(structure, ['my_here']);
  const settings = name => ({...structure.layers[0].settings, theme: name});
  const content = {
    source: 'usb-mic',
    backend: structure.output_backend,
    layers: [
      {...structure.layers[0], layer_id: 'a', settings: settings('my_gone')},
      {...structure.layers[0], layer_id: 'b', settings: settings('my_here')},
      {...structure.layers[0], layer_id: 'c', settings: settings('my_gone')},
    ],
  };
  const result = presets.reconcile(content, extended, {choices: ['usb-mic'], default: 'usb-mic'});
  assert.deepEqual(result.layers.map(layer => layer.settings.theme), ['ocean', 'my_here', 'ocean']);
  assert.deepEqual(result.changes.unavailable, [{label: 'Theme', from: 'my_gone', to: 'ocean'}]);
  assert.equal(presets.hasChanges(result.changes), true);
  assert.match(presets.reconcileNote('Party', result.changes, {saveTo: 'Party'}),
    /Its theme my_gone isn't available, so it uses ocean\. Save to Party to keep them\./);
  // A copied layer (reconcileLayer) reports it too.
  const copied = presets.reconcileLayer(content.layers[0], extended, structure.output_backend);
  assert.deepEqual(copied.unavailable, [{label: 'Theme', from: 'my_gone', to: 'ocean'}]);
});

test('the relaunch rule needs both a themes change and a custom-themed layer (D-018)', () => {
  const layers = names => names.map(name => ({settings: {theme: name}}));
  assert.equal(themes.themesNeedRelaunch(true, layers(['ocean', 'my_a'])), true);
  assert.equal(themes.themesNeedRelaunch(true, layers(['ocean', 'warm'])), false);
  assert.equal(themes.themesNeedRelaunch(false, layers(['my_a'])), false);
  const reasons = fft.relaunchReasons({status: {parameters: {}}, source: 'usb-mic', matrixChanged: false, themes: true});
  assert.deepEqual(reasons, ['the custom themes changed since launch']);
});

test('the fallback note names each theme and its layers', () => {
  assert.equal(themes.fallbackNote([]), '');
  assert.equal(themes.fallbackNote(undefined), '');
  assert.equal(themes.fallbackNote([{layer_id: 'bass', theme: 'my_gone'}]),
    "my_gone isn't saved on the Pi, so layer bass uses ocean.");
  assert.equal(themes.fallbackNote([
    {layer_id: 'a', theme: 'my_x'}, {layer_id: 'b', theme: 'my_x'}, {layer_id: 'c', theme: 'my_x'},
    {layer_id: '', theme: 'my_y'},
  ]), "my_x isn't saved on the Pi, so layers a, b and c use ocean. my_y isn't saved on the Pi, so layer (no name) uses ocean.");
});

test('a new theme is valid and takes the next free name', () => {
  assert.equal(themes.newTheme([]).name, 'my_theme_1');
  assert.equal(themes.newTheme(['my_theme_1', 'my_theme_2']).name, 'my_theme_3');
  assert.deepEqual(themes.themeErrors(doc(themes.newTheme([]))), {});
});

test('stops: added in the widest gap, ends fixed, at most 16', () => {
  const two = [{position: 0, color: [0, 0, 0]}, {position: 1, color: [200, 100, 50]}];
  const three = themes.addStop(two);
  assert.deepEqual(three[1], {position: 0.5, color: [100, 50, 25]});
  const four = themes.addStop(three);
  assert.deepEqual(four.map(stop => stop.position), [0, 0.25, 0.5, 1]);
  let full = two;
  for (let index = 0; index < 20; index += 1) full = themes.addStop(full);
  assert.equal(full.length, themes.MAX_STOPS);
  assert.deepEqual(themes.themeErrors(doc(theme({stops: full}))), {});
  assert.equal(themes.canRemoveStop(three, 0), false);
  assert.equal(themes.canRemoveStop(three, 1), true);
  assert.equal(themes.canRemoveStop(three, 2), false);
  assert.equal(themes.canRemoveStop(two, 1), false);
});

test('errors mirror the parser limits', () => {
  const errors = document => themes.themeErrors(document);
  assert.match(errors(doc(theme({name: 'my_'})))['0.name'], /1 to 29/);
  assert.match(errors(doc(theme({name: 'my_Bad'})))['0.name'], /lowercase/);
  assert.match(errors(doc(theme({name: `my_${'a'.repeat(30)}`})))['0.name'], /1 to 29/);
  assert.deepEqual(errors(doc(theme({name: `my_${'a'.repeat(29)}`}))), {});
  assert.match(errors(doc(theme({name: 'my_a'}), theme({name: 'my_a'})))['1.name'], /already called my_a/);
  assert.deepEqual(errors(doc(theme({description: '😀'.repeat(80)}))), {}); // code points, as Python
  assert.match(errors(doc(theme({description: 'x'.repeat(81)})))['0.description'], /80/);
  const stops = list => list.map(([position, color = [1, 2, 3]]) => ({position, color}));
  assert.match(errors(doc(theme({stops: stops([[0]])})))['0.stops'], /2 to 16/);
  assert.match(errors(doc(theme({stops: stops([[0], [0.6], [0.4], [1]])})))['0.stops'], /must not decrease/);
  assert.match(errors(doc(theme({stops: stops([[0.1], [1]])})))['0.stops'], /start at 0 and end at 1/);
  assert.match(errors(doc(theme({stops: stops([[0], [NaN], [1]])})))['0.stops'], /from 0 to 1/);
  assert.match(errors(doc(theme({stops: stops([[0, [0, 0, 256]], [1]])})))['0.stops'], /valid color/);
  assert.match(errors(doc(theme({overflow: [stops([[0]])]})))['0.overflow.0'], /Overflow 1 needs/);
  const five = Array.from({length: 5}, () => stops([[0], [1]]));
  assert.match(errors(doc(theme({overflow: five})))['0.overflow'], /At most 4/);
  const many = Array.from({length: 33}, (_unused, index) => theme({name: `my_t${index}`}));
  assert.match(errors(doc(...many)).document, /At most 32/);
});

test('the size check counts bytes as Python encodes them', () => {
  assert.equal(themes.compactSize({a: 'x'}), '{"a":"x"}'.length);
  assert.equal(themes.compactSize({a: 'é'}), '{"a":"\\u00e9"}'.length);
  assert.equal(themes.compactSize({a: '😀'}), '{"a":"\\ud83d\\ude00"}'.length);
  const big = Array.from({length: 32}, (_unused, index) => {
    let list = [{position: 0, color: [255, 255, 255]}, {position: 1, color: [255, 255, 255]}];
    for (let more = 0; more < 14; more += 1) list = themes.addStop(list);
    return theme({name: `my_t${index}`, stops: list, overflow: [list, list, list, list]});
  });
  assert.match(themes.themeErrors(doc(...big)).document, /too large to save/);
});

test('editable keeps a valid document exactly and counts changed themes', () => {
  const saved = doc(theme({name: 'my_a'}), theme({name: 'my_b', axis: 'column', overflow: [[
    {position: 0, color: [1, 2, 3]}, {position: 1, color: [4, 5, 6]}]]}));
  const draft = themes.editable(saved);
  assert.deepEqual(draft, saved);
  assert.equal(themes.changedThemeCount(draft, saved), 0);
  draft.themes[1].description = 'edited';
  draft.themes.push(theme({name: 'my_c'}));
  assert.equal(themes.changedThemeCount(draft, saved), 2);
  assert.equal(themes.changedThemeCount({themes: []}, saved), 2);
  assert.deepEqual(themes.editable({themes: [{name: 7}]}).themes[0],
    {name: '', description: '', axis: 'height', stops: [], overflow: []});
  assert.deepEqual(themes.editable(null), {themes: []});
});

test('the preview gradient is linear RGB from the stop positions', () => {
  const stops = [{position: 0, color: [255, 0, 0]}, {position: 0.25, color: [0, 255, 0]}, {position: 1, color: [0, 0, 255]}];
  assert.equal(themes.gradientCss(stops), 'linear-gradient(to right, #ff0000 0%, #00ff00 25%, #0000ff 100%)');
  assert.equal(themes.gradientCss(stops.slice(0, 1)), 'none');
  // A cleared position (NaN) is left out, so the preview still draws.
  assert.equal(themes.gradientCss([stops[0], {position: NaN, color: [9, 9, 9]}, stops[2]]),
    'linear-gradient(to right, #ff0000 0%, #0000ff 100%)');
});

test('the name suffix is what follows my_', () => {
  assert.equal(themes.nameSuffix('my_sunset'), 'sunset');
  assert.equal(themes.nameSuffix('sunset'), 'sunset');
  assert.equal(themes.isCustomName('my_sunset'), true);
  assert.equal(themes.isCustomName('ocean'), false);
});
