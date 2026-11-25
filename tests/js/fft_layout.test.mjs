// Music FFT layer graph rules (web-control-refactor M5 slice 2), run by
// tests/test_web_pages.py.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

import * as fft from '../../matrix_controller/static/js/programs/fft.js';
import * as rules from '../../matrix_controller/static/js/programs/fft_layout.js';

const read = path => JSON.parse(readFileSync(new URL(path, import.meta.url)));
const fixture = read('../fixtures/projection_layout_vectors.json');
const catalog = read('../../matrix_controller/static/fft_catalog.json');
const close = (actual, expected, tolerance, message) =>
  assert.ok(Math.abs(actual - expected) <= tolerance, `${message}: ${actual} vs ${expected}`);

test('projectionLayout matches the Python formula on every fixture case (criterion 1)', () => {
  assert.ok(fixture.cases.length >= 4);
  for (const item of fixture.cases) {
    const input = item.input;
    const layout = rules.projectionLayout(input.min_freq, input.max_freq, input.weight_points,
      input.sample_rate, input.fft_size, input.num_bars);
    assert.equal(layout.edges.length, item.edges.length, input.name);
    layout.edges.forEach((edge, index) =>
      close(edge, item.edges[index], 1e-12 * item.edges[index], `${input.name} edge ${index}`));
    layout.weights.forEach((weight, index) =>
      close(weight, item.weights[index], 1e-12, `${input.name} weight ${index}`));
    assert.deepEqual(layout.binCounts, item.bin_counts, input.name);
  }
});

test('the narrow fixture range has bars with no FFT bin', () => {
  const item = fixture.cases.find(entry => entry.input.name === 'narrow-range-with-empty-bars');
  const input = item.input;
  const layout = rules.projectionLayout(input.min_freq, input.max_freq, input.weight_points,
    input.sample_rate, input.fft_size, input.num_bars);
  assert.equal(rules.emptyBars(layout), item.bin_counts.filter(count => count === 0).length);
  assert.ok(rules.emptyBars(layout) > 0);
});

test('projectionLayout refuses inputs that make no layout', () => {
  const points = [{position: 0, weight: 1}];
  assert.equal(rules.projectionLayout(500, 500, points, 44100, 8192, 64), null);
  assert.equal(rules.projectionLayout(0, 500, points, 44100, 8192, 64), null);
  assert.equal(rules.projectionLayout(100, 500, [], 44100, 8192, 64), null);
  assert.equal(rules.projectionLayout(100, 500, points, null, 8192, 64), null);
});

test('binContext uses the snapshot while it is the current run, else assumes (criterion 2)', () => {
  const catalogLayout = catalog.structure.bin_layout;
  const snapshotLayout = {...catalogLayout, sample_rate: 48000, num_bars: 128};
  const live = rules.binContext({snapshotLayout, catalogLayout, assumedUntilLaunch: catalog.assumed_until_launch});
  assert.deepEqual([live.sampleRate, live.fftSize, live.numBars, live.assumed.size], [48000, 8192, 128, 0]);

  const switched = rules.binContext({snapshotLayout, catalogLayout, sourceChanged: true});
  assert.equal(switched.sampleRate, 44100);
  assert.deepEqual([...switched.assumed], ['sample_rate']);

  const before = rules.binContext({catalogLayout, assumedUntilLaunch: catalog.assumed_until_launch,
    matrixValues: {led_cols: 32, led_chain: 3}});
  assert.deepEqual([before.sampleRate, before.fftSize, before.numBars], [44100, 8192, 96]);
  assert.deepEqual([...before.assumed].sort(), ['num_bars', 'sample_rate']);

  // Before a run the rate is assumed even if the catalog stopped saying so.
  assert.ok(rules.binContext({catalogLayout}).assumed.has('sample_rate'));
  for (const matrixValues of [null, {led_cols: 64}, {led_cols: 0, led_chain: 1}, {led_cols: '64', led_chain: 1}]) {
    const fallback = rules.binContext({catalogLayout, assumedUntilLaunch: catalog.assumed_until_launch, matrixValues});
    assert.equal(fallback.numBars, catalogLayout.num_bars, JSON.stringify(matrixValues));
  }
  assert.equal(rules.nyquistOf(before), 22050);
  assert.equal(rules.nyquistOf(rules.binContext()), null);
});

test('the scales invert', () => {
  const strip = rules.stripScale(400, 100, 6300, 22050);
  assert.deepEqual([strip.low, strip.high], [20, 22050]);
  for (const frequency of [20, 100, 1234, 22050]) close(strip.f(strip.x(frequency)), frequency, 1e-9 * frequency, 'strip');
  const wide = rules.stripScale(400, 10, 30000, 22050);
  assert.deepEqual([wide.low, wide.high], [10, 30000]);

  const detail = rules.detailScale(400);
  for (const weight of [0.1, 0.45, 1, 10, 100]) close(detail.w(detail.y(weight)), weight, 1e-9 * weight, 'detail');
  assert.equal(detail.w(detail.y(0)), 0);
  assert.equal(detail.y(0.05), detail.logBottom);
  close(detail.p(detail.x(0.37)), 0.37, 1e-12, 'position');

  const floor = rules.floorScale(400);
  for (const value of [0.01, 0.3, 1, 100]) close(floor.v(floor.x(value)), value, 1e-9 * value, 'floor');
  assert.equal(floor.v(floor.x(0)), 0);
});

test('range ends drag within 20 Hz to Nyquist at 3 significant figures (criterion 3)', () => {
  const range = {minFreq: 100, maxFreq: 6300, top: 22050};
  assert.deepEqual(rules.setMin(123.456, range), {min_freq: 123});
  assert.deepEqual(rules.setMin(5, range), {min_freq: 20});
  assert.deepEqual(rules.setMin(9000, range), {min_freq: 6000});
  assert.deepEqual(rules.setMax(30000, range), {max_freq: 22050}); // never rounded up past Nyquist
  assert.deepEqual(rules.setMax(50, range), {max_freq: 105});
  assert.deepEqual(rules.setMax(7777, range), {max_freq: 7780});
  // The order wins over the strip's ends.
  assert.deepEqual(rules.setMin(10, {minFreq: 5, maxFreq: 18}), {min_freq: 17.1});
  assert.equal(rules.setMax(30000, {minFreq: 22000, top: 22050}).max_freq, 22050);
  // Rounding never breaks the 1.05 ratio: it rounds inward at the bound.
  assert.deepEqual(rules.setMax(10, {minFreq: 1003, top: 22050}), {max_freq: 1060});
  assert.deepEqual(rules.setMin(99999, {maxFreq: 6310}), {min_freq: 6000});
  // `top` is the field's maximum when that is below Nyquist (a 96 kHz source).
  assert.deepEqual(rules.setMax(60000, {minFreq: 100, top: 24000}), {max_freq: 24000});
  // Min Hz at or above `top` leaves Max Hz alone.
  assert.deepEqual(rules.setMax(30000, {minFreq: 23000, top: 22050}), {});
  assert.deepEqual(rules.setMax(30000, {minFreq: 1000}), {max_freq: 30000});
});

test('sig3Toward rounds inward past floating-point noise only', () => {
  assert.equal(rules.sig3Toward(6009.52, -1), 6000);
  assert.equal(rules.sig3Toward(1053.15, 1), 1060);
  assert.equal(rules.sig3Toward(100 * 1.05, 1), 105); // 105.00000000000001
  assert.equal(rules.sig3Toward(17.1428, -1), 17.1);
  assert.equal(rules.sig3Toward(99.96, -1), 99.9);
});

test('the band slides at a constant ratio and stops at the strip ends', () => {
  const range = {minFreq: 100, maxFreq: 1000, top: 22050};
  assert.deepEqual(rules.slideRange(Math.log10(2), range), {min_freq: 200, max_freq: 2000});
  assert.deepEqual(rules.slideRange(-3, range), {min_freq: 20, max_freq: 200});
  const top = rules.slideRange(3, range);
  assert.equal(top.max_freq, 22050);
  close(top.min_freq, 2205, 5, 'min at the top');
  // A range already outside may stay but not move further out.
  assert.deepEqual(rules.slideRange(-1, {minFreq: 10, maxFreq: 100, top: 22050}), {min_freq: 10, max_freq: 100});
  assert.deepEqual(rules.slideRange(1, {minFreq: 1000, maxFreq: 24000, top: 22050}), {min_freq: 1000, max_freq: 24000});
});

test('arrow keys nudge range ends by a semitone, or an octave with Shift', () => {
  const range = {minFreq: 100, maxFreq: 6300, top: 22050};
  assert.deepEqual(rules.nudgeRange('min', true, false, range), {min_freq: 106});
  assert.deepEqual(rules.nudgeRange('min', false, true, range), {min_freq: 50});
  assert.deepEqual(rules.nudgeRange('max', true, true, range), {max_freq: 12600});
  assert.deepEqual(rules.nudgeRange('band', true, true, range), {min_freq: 200, max_freq: 12600});
  assert.deepEqual(rules.nudgeRange('other', true, true, range), {});
});

test('the floor drags and nudges on its own scale (criterion 5)', () => {
  const floor = rules.floorScale(400);
  assert.equal(rules.dragFloor(floor.zeroLeft, floor), 0);
  assert.equal(rules.dragFloor(floor.right + 50, floor), 100);
  assert.equal(rules.nudgeFloor(0, true, false), 0.01);
  assert.equal(rules.nudgeFloor(0.01, false, false), 0);
  assert.equal(rules.nudgeFloor(1, true, false), 1.25);
  assert.equal(rules.nudgeFloor(1, false, true), 0.5);
  assert.equal(rules.nudgeFloor(80, true, true), 100);
});

test('a dragged point stays between its neighbors (criterion 6)', () => {
  const points = [{id: 1, position: 0, weight: 1}, {id: 2, position: 0.5, weight: 2}, {id: 3, position: 0.4, weight: 3}];
  assert.deepEqual(rules.pointBounds(points, 2), {low: 0.401, high: 1});
  assert.deepEqual(rules.pointBounds(points, 3), {low: 0.001, high: 0.499});
  assert.deepEqual(rules.pointBounds(points, 1), {low: 0, high: 0.399});
  const bounds = rules.pointBounds(points, 3);
  assert.deepEqual(rules.dragPoint(0.9, 3.14159, bounds), {position: 0.499, weight: 3.14});
  assert.deepEqual(rules.dragPoint(0.12345, 0, bounds), {position: 0.123, weight: 0});
  assert.deepEqual(rules.dragPoint(-1, 500, bounds), {position: 0.001, weight: 100});
  assert.deepEqual(rules.dragPoint(0.2, 0.01, bounds), {position: 0.2, weight: 0.1});
  // Neighbors typed under 0.002 apart leave no room: the point keeps its place.
  const tight = [{id: 1, position: 0.5}, {id: 2, position: 0.5005}, {id: 3, position: 0.501}];
  const tightBounds = rules.pointBounds(tight, 2);
  assert.ok(tightBounds.low > tightBounds.high);
  assert.deepEqual(rules.dragPoint(0.7, 1, tightBounds, 0.5005), {position: 0.5005, weight: 1});
  assert.deepEqual(rules.nudgePoint({position: 0.5005, weight: 1}, 'ArrowRight', false, tightBounds),
    {position: 0.5005, weight: 1});
});

test('arrow keys nudge points; other keys are not used', () => {
  const bounds = {low: 0, high: 0.5};
  const point = {position: 0.3, weight: 1};
  assert.deepEqual(rules.nudgePoint(point, 'ArrowRight', false, bounds), {position: 0.31, weight: 1});
  assert.deepEqual(rules.nudgePoint(point, 'ArrowRight', true, bounds), {position: 0.4, weight: 1});
  assert.deepEqual(rules.nudgePoint({position: 0.45, weight: 1}, 'ArrowRight', true, bounds), {position: 0.5, weight: 1});
  assert.deepEqual(rules.nudgePoint(point, 'ArrowLeft', true, bounds), {position: 0.2, weight: 1});
  assert.deepEqual(rules.nudgePoint(point, 'ArrowUp', false, bounds), {position: 0.3, weight: 1.1});
  assert.deepEqual(rules.nudgePoint(point, 'ArrowDown', true, bounds), {position: 0.3, weight: 0.5});
  assert.deepEqual(rules.nudgePoint({position: 0.3, weight: 0}, 'ArrowUp', false, bounds), {position: 0.3, weight: 0.1});
  assert.deepEqual(rules.nudgePoint({position: 0.3, weight: 0.105}, 'ArrowDown', false, bounds), {position: 0.3, weight: 0});
  assert.deepEqual(rules.nudgePoint({position: 0.3, weight: 90}, 'ArrowUp', true, bounds), {position: 0.3, weight: 100});
  assert.equal(rules.nudgePoint(point, 'Enter', false, bounds), null);
});

test('Max Hz above Nyquist is an error with a known rate and a warning when assumed (D-069)', () => {
  const known = {sampleRate: 44100, assumed: new Set()};
  const assumed = {sampleRate: 44100, assumed: new Set(['sample_rate'])};
  assert.equal(fft.nyquistIssue(22050, known), null); // the server refuses only a higher value
  assert.equal(fft.nyquistIssue(6300, assumed), null);
  const error = fft.nyquistIssue(22051, known, 'Max Hz');
  assert.equal(error.level, 'error');
  assert.match(error.message, /Max Hz must be at most 22,050 Hz/);
  const warning = fft.nyquistIssue(24000, assumed);
  assert.equal(warning.level, 'warning');
  assert.match(warning.message, /22,050 Hz.*assumed 44\.1 kHz/);
  assert.equal(fft.nyquistIssue(Number.NaN, known), null);
  assert.equal(fft.nyquistIssue(24000, {sampleRate: null, assumed: new Set()}), null);
});

test('axis ticks are 1-2-5 steps', () => {
  assert.deepEqual(rules.NICE_HZ.slice(0, 6), [1, 2, 5, 10, 20, 50]);
  assert.equal(rules.NICE_HZ.at(-1), 50000);
  assert.deepEqual([20, 500, 1000, 20000].map(rules.hzTick), ['20', '500', '1k', '20k']);
});
