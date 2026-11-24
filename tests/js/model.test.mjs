// Page logic tests (web-control-refactor M4), run by tests/test_web_pages.py.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

import {
  buttonModel,
  changedNames,
  defaultsOf,
  flagArguments,
  flagVector,
  formatArgv,
  humanize,
  matrixChangedSinceLaunch,
  matrixGroups,
  nowPlaying,
  parseValue,
  pythonFloat,
  rangeText,
  rowBadge,
  runKey,
  runningValues,
} from '../../matrix_controller/static/js/model.js';

const fixture = JSON.parse(readFileSync(new URL('../fixtures/cli_flag_vectors.json', import.meta.url)));

test('flag arguments match ParameterSpec.cli_arguments for every fixture case', () => {
  assert.ok(fixture.cases.length > 50);
  for (const {parameter, value, argv} of fixture.cases) {
    assert.deepEqual(flagArguments(parameter, value), argv, `${parameter.name}=${JSON.stringify(value)}`);
  }
});

test('python float formatting', () => {
  assert.equal(pythonFloat(1), '1.0');
  assert.equal(pythonFloat(0), '0.0');
  assert.equal(pythonFloat(-0), '-0.0');
  assert.equal(pythonFloat(0.05), '0.05');
  assert.equal(pythonFloat(0.0001), '0.0001');
  assert.equal(pythonFloat(0.00005), '5e-05');
  assert.equal(pythonFloat(1.5e-7), '1.5e-07');
  assert.equal(pythonFloat(1e16), '1e+16');
  assert.equal(pythonFloat(123456.75), '123456.75');
});

test('flag line quotes empty and spaced tokens and marks invalid values', () => {
  assert.equal(formatArgv(['--led-pixel-mapper', '']), "--led-pixel-mapper ''");
  assert.equal(formatArgv(['--led-pixel-mapper', 'Rotate:90']), '--led-pixel-mapper Rotate:90');
  assert.equal(formatArgv(['--x', 'a b']), "--x 'a b'");
  assert.equal(formatArgv(['--x', "it's"]), "--x 'it'\\''s'");
  const delay = {name: 'delay', type: 'float', cli_flag: '--delay'};
  const seed = {name: 'seed', type: 'int', cli_flag: '--seed'};
  assert.deepEqual(flagVector([delay, seed], {delay: NaN, seed: 3}, new Set(['delay'])), ['--delay', '?', '--seed', '3']);
});

test('labels and ranges', () => {
  assert.equal(humanize('max_runs'), 'Max runs');
  assert.equal(humanize('led_pwm_lsb_nanoseconds'), 'PWM LSB nanoseconds');
  assert.equal(humanize('led_slowdown_gpio'), 'Slowdown GPIO');
  assert.equal(humanize('led_rgb_sequence'), 'RGB sequence');
  assert.equal(humanize('n'), 'N');
  assert.equal(rangeText({type: 'float', minimum: 0, maximum: 2}), '0–2');
  assert.equal(rangeText({type: 'int', minimum: 1}), 'at least 1');
  assert.equal(rangeText({type: 'enum', choices: ['a']}), '');
});

test('parsing mirrors ParameterSpec.normalize', () => {
  const count = {name: 'max_runs', type: 'int', minimum: 0, maximum: 10};
  assert.deepEqual(parseValue(count, ' 4 '), {value: 4, error: null});
  assert.match(parseValue(count, '1.5').error, /whole number/);
  assert.match(parseValue(count, '').error, /whole number/);
  assert.match(parseValue(count, '11').error, /at most 10/);
  assert.match(parseValue(count, '-1').error, /at least 0/);
  const delay = {name: 'delay', type: 'float', minimum: 0, maximum: 2};
  assert.deepEqual(parseValue(delay, '0.05'), {value: 0.05, error: null});
  assert.deepEqual(parseValue(delay, '5e-5'), {value: 0.00005, error: null});
  assert.match(parseValue(delay, 'abc').error, /must be a number/);
  assert.match(parseValue(delay, 'Infinity').error, /must be a number/);
  // -0 travels as 0 in JSON, so it parses (and previews) as 0.
  assert.ok(Object.is(parseValue(delay, '-0').value, 0));
  assert.deepEqual(flagArguments({name: 'delay', type: 'float', cli_flag: '--delay'}, parseValue(delay, '-0').value), ['--delay', '0.0']);
  const mode = {name: 'mode', type: 'enum', choices: ['single', 'all'], maximum_length: 256};
  assert.equal(parseValue(mode, 'all').error, null);
  assert.match(parseValue(mode, 'other').error, /one of: single, all/);
  const mapper = {name: 'led_pixel_mapper', type: 'string', maximum_length: 4};
  assert.match(parseValue(mapper, 'abcde').error, /at most 4 characters/);
  assert.deepEqual(parseValue({name: 'gradient', type: 'bool'}, true), {value: true, error: null});
});

const sorts = [
  {name: 'mode', type: 'enum', default: 'random'},
  {name: 'delay', type: 'float', default: 0.05},
];

test('draft comparison and running values', () => {
  const defaults = defaultsOf(sorts);
  assert.deepEqual(defaults, {mode: 'random', delay: 0.05});
  assert.deepEqual(changedNames(sorts, {mode: 'random', delay: 0.05}, defaults), []);
  assert.deepEqual(changedNames(sorts, {mode: 'all', delay: NaN}, defaults), ['mode', 'delay']);
  const running = {state: 'running', visualization_id: 'sorts', run_id: 4, parameters: {mode: 'all'}};
  assert.deepEqual(runningValues(running, 'sorts', sorts), {mode: 'all', delay: 0.05});
  assert.equal(runningValues(running, 'fft', sorts), null);
  assert.equal(runningValues({...running, state: 'failed'}, 'sorts', sorts), null);
  assert.equal(runKey(running, 'sorts'), 'run:4');
  assert.equal(runKey({...running, state: 'starting'}, 'sorts'), 'run:4');
  assert.equal(runKey({...running, state: 'idle'}, 'sorts'), null);
  assert.equal(runKey(running, 'fft'), null);
});

test('matrix settings changed since launch', () => {
  const stored = {led_brightness: 40, led_rows: 64};
  const status = {state: 'running', matrix_settings: {led_brightness: 40, led_rows: 64}};
  assert.equal(matrixChangedSinceLaunch(status, stored), false);
  assert.equal(matrixChangedSinceLaunch(status, {...stored, led_brightness: 70}), true);
  assert.equal(matrixChangedSinceLaunch({state: 'idle', matrix_settings: {}}, stored), false);
  assert.equal(matrixChangedSinceLaunch({...status, state: 'stopping'}, {...stored, led_brightness: 70}), false);
  assert.equal(matrixChangedSinceLaunch(null, stored), false);
});

function model(status, extra = {}) {
  return buttonModel({
    status,
    programId: 'sorts',
    programName: 'Sorting Algorithms',
    otherName: status?.visualization_id && status.visualization_id !== 'sorts' ? 'Music FFT' : null,
    dirty: false,
    matrixChanged: false,
    valid: true,
    pending: null,
    ...extra,
  });
}

test('D-037 button table', () => {
  const idle = model({state: 'idle', visualization_id: null});
  assert.deepEqual([idle.top.label, idle.top.action, idle.bottom.label, idle.bottom.action],
    ['Launch', 'launch', 'Launch with these settings', 'launch']);
  assert.equal(idle.statusText, 'Not running.');

  const other = model({state: 'running', visualization_id: 'fft'});
  assert.equal(other.top.action, 'launch');
  assert.equal(other.bottom.label, 'Launch with these settings');
  assert.equal(other.statusText, 'Music FFT is running. Launch replaces it.');

  const starting = model({state: 'starting', visualization_id: 'sorts'});
  assert.deepEqual([starting.top.label, starting.top.disabled, starting.bottom.disabled], ['Starting…', true, true]);
  const stopping = model({state: 'stopping', visualization_id: 'sorts'});
  assert.deepEqual([stopping.top.label, stopping.top.disabled, stopping.bottom.disabled], ['Stopping…', true, true]);

  const running = {state: 'running', visualization_id: 'sorts'};
  const clean = model(running);
  assert.deepEqual([clean.top.label, clean.top.action, clean.bottom.label, clean.bottom.disabled],
    ['Terminate', 'stop', 'Send update', true]);
  assert.equal(model(running, {dirty: true}).bottom.disabled, false);
  assert.equal(model(running, {matrixChanged: true}).bottom.disabled, false);
  assert.equal(model(running, {dirty: true, valid: false}).bottom.disabled, true);

  const failed = model({state: 'failed', visualization_id: null, last_visualization_id: 'sorts', last_error: 'exited with code 1'});
  assert.equal(failed.top.label, 'Launch');
  assert.equal(failed.bottom.label, 'Launch with these settings');
  assert.equal(failed.statusText, 'Stopped with an error: exited with code 1');
  assert.equal(failed.tone, 'failed');
  const failedElsewhere = model({state: 'failed', visualization_id: null, last_visualization_id: 'fft'});
  assert.equal(failedElsewhere.statusText, 'Not running.');

  assert.equal(model({state: 'idle'}, {valid: false}).top.disabled, true);
  assert.equal(model({state: 'idle'}, {valid: false}).bottom.disabled, true);
  const launching = model({state: 'idle'}, {pending: 'launch'});
  assert.deepEqual([launching.bottom.label, launching.bottom.disabled, launching.top.disabled], ['Launching…', true, true]);
  const updating = model(running, {pending: 'update', dirty: true});
  assert.equal(updating.bottom.label, 'Sending…');
  const terminating = model(running, {pending: 'stop'});
  assert.deepEqual([terminating.top.label, terminating.top.disabled], ['Stopping…', true]);
  assert.equal(model(null).statusText, 'Checking the matrix…');
});

test('home strip and row badges', () => {
  const nameOf = id => ({fft: 'Music FFT', sorts: 'Sorting Algorithms'}[id] || id);
  assert.deepEqual(nowPlaying({state: 'idle', visualization_id: null}, nameOf),
    {name: 'Nothing running', detail: 'The matrix is free.', tone: 'idle', activeId: null, canStop: false});
  const running = nowPlaying({state: 'running', visualization_id: 'fft'}, nameOf);
  assert.deepEqual([running.name, running.detail, running.activeId, running.canStop], ['Music FFT', 'Running', 'fft', true]);
  assert.equal(nowPlaying({state: 'stopping', visualization_id: 'fft'}, nameOf).canStop, false);
  const failed = nowPlaying({state: 'failed', visualization_id: null, last_visualization_id: 'sorts', last_error: 'boom'}, nameOf);
  assert.deepEqual([failed.name, failed.detail, failed.failedId, failed.canStop],
    ['Sorting Algorithms', 'Stopped with an error: boom', 'sorts', false]);

  assert.deepEqual(rowBadge({state: 'running', visualization_id: 'fft'}, 'fft'), {text: 'running', tone: 'running'});
  assert.equal(rowBadge({state: 'running', visualization_id: 'fft'}, 'sorts'), null);
  assert.deepEqual(rowBadge({state: 'failed', last_visualization_id: 'sorts'}, 'sorts'), {text: 'failed', tone: 'failed'});
  assert.equal(rowBadge(null, 'fft'), null);
});

test('matrix groups put the common settings first', () => {
  const parameters = ['led_rows', 'led_cols', 'led_scan_mode', 'led_brightness'].map(name => ({name}));
  const {common, advanced} = matrixGroups(parameters, ['led_brightness', 'led_rows', 'led_missing']);
  assert.deepEqual(common.map(item => item.name), ['led_brightness', 'led_rows']);
  assert.deepEqual(advanced.map(item => item.name), ['led_cols', 'led_scan_mode']);
});
