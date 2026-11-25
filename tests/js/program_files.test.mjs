// Program config file logic (web-control-refactor M4b), run by tests/test_web_pages.py.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

import {
  buttonModel,
  changedSinceLaunchText,
  colorToHex,
  fieldLabel,
  fileChanges,
  fileSlots,
  fileUpdateAction,
  filesChangedSinceLaunch,
  hexToColor,
  parseValue,
} from '../../matrix_controller/static/js/model.js';
import {fileText} from '../../matrix_controller/static/js/programs/graph_traversal.js';

const tracked = JSON.parse(readFileSync(
  new URL('../../my_programs/dsa_visualizations/graph_traversal_config.json', import.meta.url)));

// The shape GRAPH_TRAVERSAL_RENDER_FIELDS sends (test_program_files pins it).
const fields = [
  {key: 'node_size', kind: 'int', label: 'Node size', minimum: 1, maximum: 2},
  {key: 'endpoint_node_size', kind: 'int', label: 'Start and target node size', minimum: 1, maximum: 2},
  {key: 'direction_marker_pixels', kind: 'int', label: 'Direction marker length', minimum: 1, maximum: 8},
  {key: 'colors', kind: 'color_map', label: 'Colors', names: Object.keys(tracked.colors)},
  {key: 'component_colors', kind: 'color_list', label: 'Component colors', length: 32},
];

const copy = value => JSON.parse(JSON.stringify(value));

test('colors convert to and from the picker hex form', () => {
  assert.equal(colorToHex([255, 0, 16]), '#ff0010');
  assert.deepEqual(hexToColor('#FF0010'), [255, 0, 16]);
  assert.equal(hexToColor('red'), null);
  assert.equal(hexToColor('#12345'), null);
  for (const color of tracked.component_colors) assert.deepEqual(hexToColor(colorToHex(color)), color);
});

test('every size, named color, and component slot counts once', () => {
  const slots = fileSlots(fields, tracked);
  assert.equal(Object.keys(slots).length, 3 + 16 + 32);
  assert.equal(slots.node_size, 1);
  assert.equal(slots['colors.target_node'], '#ff0000');
  assert.deepEqual(fileChanges(fields, tracked, copy(tracked)), []);

  const draft = copy(tracked);
  draft.node_size = 2;
  draft.colors.background = [1, 2, 3];
  draft.component_colors[31] = [9, 9, 9];
  assert.deepEqual(fileChanges(fields, draft, tracked), ['node_size', 'colors.background', 'component_colors.31']);
});

test('size fields use the descriptor label in messages', () => {
  const parameter = {name: 'endpoint_node_size', label: 'Start and target node size', type: 'int', minimum: 1, maximum: 2};
  assert.equal(fieldLabel(parameter), 'Start and target node size');
  assert.equal(fieldLabel({name: 'max_runs'}), 'Max runs');
  assert.equal(parseValue(parameter, '3').error, 'Start and target node size must be at most 2.');
  assert.deepEqual(parseValue(parameter, '2'), {value: 2, error: null});
});

test('colors changed since launch compares this run with the saved revision', () => {
  const running = {state: 'running', visualization_id: 'graph_traversal', program_files: {render: 2}};
  assert.equal(filesChangedSinceLaunch(running, 'graph_traversal', {render: 2}), false);
  assert.equal(filesChangedSinceLaunch(running, 'graph_traversal', {render: 3}), true);
  assert.equal(filesChangedSinceLaunch(running, 'sorts', {render: 3}), false);
  assert.equal(filesChangedSinceLaunch({...running, state: 'idle'}, 'graph_traversal', {render: 3}), false);
  // An unusable file (null) and no file (0) both mean the tracked default.
  const unusable = {...running, program_files: {render: null}};
  assert.equal(filesChangedSinceLaunch(unusable, 'graph_traversal', {render: 0}), false);
  assert.equal(filesChangedSinceLaunch(unusable, 'graph_traversal', {render: 1}), true);
  assert.equal(filesChangedSinceLaunch({...running, program_files: {}}, 'graph_traversal', {render: 1}), false);
});

test('Send update is enabled by a colors-only change or a newer saved file', () => {
  const status = {state: 'running', visualization_id: 'graph_traversal'};
  const base = {status, programId: 'graph_traversal', programName: 'Graph Traversal', valid: true, pending: null};
  assert.equal(buttonModel({...base, dirty: false}).bottom.disabled, true);
  assert.equal(buttonModel({...base, dirty: true}).bottom.disabled, false);
  assert.equal(buttonModel({...base, dirty: false, filesChanged: true}).bottom.disabled, false);
  assert.equal(buttonModel({...base, dirty: true, valid: false}).bottom.disabled, true);
});

test('a polled file is followed, kept behind a banner, or reset', () => {
  const saved = {revision: 2, error: null};
  assert.equal(fileUpdateAction(saved, {revision: 2, error: null}, true), 'none');
  assert.equal(fileUpdateAction(saved, {revision: 3, error: null}, false), 'follow');
  assert.equal(fileUpdateAction(saved, {revision: 3, error: null}, true), 'keep');
  // Review finding 1: edits can't be saved over an unusable file.
  const unusable = {revision: null, error: 'the saved colors & rendering file is not valid JSON'};
  assert.equal(fileUpdateAction(saved, unusable, false), 'follow');
  assert.equal(fileUpdateAction(saved, unusable, true), 'reset');
  assert.equal(fileUpdateAction(unusable, {...unusable}, false), 'none');
  // Removed: revision 0, no error.
  assert.equal(fileUpdateAction(unusable, {revision: 0, error: null}, false), 'follow');
});

test('changed-since-launch text names everything that changed', () => {
  assert.equal(changedSinceLaunchText(false, []), '');
  assert.equal(changedSinceLaunchText(true, []), 'Matrix settings changed since launch');
  assert.equal(changedSinceLaunchText(false, [fileText.render.noun]), 'Colors changed since launch');
  assert.equal(changedSinceLaunchText(true, ['colors']), 'Matrix settings and colors changed since launch');
  assert.equal(changedSinceLaunchText(true, ['a', 'b']), 'Matrix settings, a and b changed since launch');
});

test('Graph Traversal names its colors section', () => {
  assert.equal(fileText.render.restore, 'Restore default colors');
  assert.equal(fileText.render.load, 'Load saved colors');
});
