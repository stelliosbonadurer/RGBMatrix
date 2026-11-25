// Music FFT Bluetooth rules (web-control-refactor M5 slice 4), run by tests/test_web_pages.py.
import test from 'node:test';
import assert from 'node:assert/strict';

import * as bt from '../../matrix_controller/static/js/programs/fft_bluetooth.js';

// Made-up statuses in the GET /api/v1/bluetooth shape (D-073 fields included).
const phone = (name, overrides = {}) => ({
  device_id: `id-${name}`, name, paired: true, trusted: true, connected: false, streaming: false, ...overrides,
});
const status = (overrides = {}) => ({
  available: true,
  reason: null,
  adapter: {powered: true, discoverable: false, pairable: false, name: 'Test Pi'},
  window: {open: false, seconds_remaining: null, duration_seconds: 120},
  pending_request: null,
  devices: [],
  ...overrides,
});
const unavailable = {...status(), available: false, reason: 'BlueZ is not running', adapter: null};
const openWindow = (seconds = 95) => ({open: true, seconds_remaining: seconds, duration_seconds: 120});

test('polls every second while pairing is open, every 5 s while the row is open, else never (D-065)', () => {
  assert.equal(bt.pollDelay({windowOpen: true, rowOpen: false}), 1000);
  assert.equal(bt.pollDelay({windowOpen: true, rowOpen: true}), 1000);
  assert.equal(bt.pollDelay({windowOpen: false, rowOpen: true}), 5000);
  assert.equal(bt.pollDelay({windowOpen: false, rowOpen: false}), null);
  assert.equal(bt.pollDelay({windowOpen: true, rowOpen: true, hidden: true}), null);
  assert.equal(bt.pollDelay(), null);
});

test('an older poll never overwrites a newer reply', () => {
  const order = bt.createOrdering();
  const first = order.start();
  const second = order.start();
  assert.equal(order.applies(second), true);
  assert.equal(order.applies(first), false); // started earlier, came back later
  const during = order.start(); // a poll started while an action runs
  order.settle(); // the action's reply
  assert.equal(order.applies(during), false);
  assert.equal(order.applies(order.start()), true);
});

test('the window length reads in minutes or seconds', () => {
  assert.equal(bt.windowSpan(120), '2 minutes');
  assert.equal(bt.windowSpan(60), '1 minute');
  assert.equal(bt.windowSpan(90), '90 seconds');
  assert.equal(bt.windowSpan(null), 'a short time');
  assert.equal(bt.windowSpan(0), 'a short time');
});

test('countdown reads m:ss and never goes below zero', () => {
  assert.equal(bt.countdown(120), '2:00');
  assert.equal(bt.countdown(65), '1:05');
  assert.equal(bt.countdown(9), '0:09');
  assert.equal(bt.countdown(0.2), '0:01');
  assert.equal(bt.countdown(-3), '0:00');
  assert.equal(bt.countdown(null), '0:00');
});

test('only paired phones are listed; the pending phone shows in its request', () => {
  const pending = {request_id: 'r1', kind: 'confirmation', device_id: 'id-new', name: 'New', code: '123456'};
  const value = status({
    devices: [phone('A'), phone('New', {paired: false, trusted: false})],
    pending_request: pending,
  });
  assert.deepEqual(bt.pairedPhones(value).map(device => device.name), ['A']);
  assert.deepEqual(bt.pairedPhones(unavailable), []);
  assert.deepEqual(bt.pairedPhones(null), []);
});

test('the summary names the streaming phone and an open window', () => {
  assert.deepEqual(bt.summaryParts(null), []);
  assert.deepEqual(bt.summaryParts(unavailable), []);
  assert.deepEqual(bt.summaryParts(status({devices: [phone('A', {connected: true})]})), []);
  assert.deepEqual(
    bt.summaryParts(status({devices: [phone('A', {connected: true}), phone('B', {connected: true, streaming: true})]})),
    ['B streaming']);
  assert.deepEqual(bt.summaryParts(status({window: openWindow()})), ['pairing open']);
});

test('the window line counts down and names the Pi when the adapter has a name (D-073)', () => {
  assert.equal(bt.windowText(status()), null);
  assert.equal(bt.windowText(unavailable), null);
  assert.deepEqual(bt.windowText(status({window: openWindow(95)})), {
    line: 'Pairing open · 1:35 left',
    hint: 'On the phone, open Bluetooth settings and pick Test Pi.',
  });
  const nameless = status({window: openWindow(5), adapter: {...status().adapter, name: null}});
  assert.equal(bt.windowText(nameless).hint, 'On the phone, open Bluetooth settings and pick this Pi.');
});

test('a confirmation shows its code with Pair; an authorization asks to Allow (D-028)', () => {
  assert.equal(bt.requestView(null), null);
  const confirmation = bt.requestView({request_id: 'r1', kind: 'confirmation', name: 'New', code: '042917'});
  assert.equal(confirmation.title, 'New wants to pair.');
  assert.equal(confirmation.code, '042917');
  assert.match(confirmation.hint, /same code/);
  assert.deepEqual([confirmation.accept, confirmation.reject], [
    {action: 'request.confirm', label: 'Pair'}, {action: 'request.reject', label: 'Reject'}]);
  const authorization = bt.requestView({request_id: 'r2', kind: 'authorization', name: 'New'});
  assert.equal(authorization.code, null);
  assert.equal(authorization.accept.label, 'Allow');
  assert.equal(authorization.accept.action, 'request.confirm');
});

test('each phone offers the opposite of its trust and link state, then Forget', () => {
  const fresh = bt.phoneView(phone('A', {trusted: false}));
  assert.equal(fresh.state, 'not trusted · not connected');
  assert.match(fresh.note, /Trust/);
  assert.deepEqual(fresh.actions.map(item => item.action), ['device.trust', 'device.connect', 'device.forget']);
  const streaming = bt.phoneView(phone('B', {connected: true, streaming: true}));
  assert.equal(streaming.state, 'trusted · streaming');
  assert.equal(streaming.note, null);
  assert.deepEqual(streaming.actions.map(item => item.action), ['device.untrust', 'device.disconnect', 'device.forget']);
  assert.equal(bt.phoneView(phone('C', {connected: true})).state, 'trusted · connected');
  assert.equal(streaming.actions[2].confirm, true);
  assert.equal(bt.forgetQuestion('B'), 'Forget B? It has to pair again before it can stream.');
  assert.equal(bt.workingLabel('device.connect'), 'Connecting…');
  assert.equal(bt.workingLabel('something.else'), 'Working…');
});

test('failures say what happened without guessing', () => {
  assert.match(bt.failureText({status: 404, code: 'device_not_found'}, {action: 'device.trust', name: 'A'}),
    /^A is no longer paired.*refreshed\.$/);
  assert.match(bt.failureText({status: 404}, {action: 'device.trust'}), /^That phone is no longer paired/);
  assert.match(bt.failureText({status: 409, code: 'stale_request'}, {action: 'request.confirm'}), /request is over/);
  assert.equal(bt.failureText({status: 503, code: 'bluetooth_unavailable', message: 'Bluetooth is unavailable: x'},
    {action: 'window.open'}), 'Bluetooth is unavailable: x');
  assert.equal(
    bt.failureText({status: 502, code: 'bluetooth_failed', message: 'Bluetooth connect failed (timeout)'},
      {action: 'device.connect', name: 'A'}),
    'Could not connect A: Bluetooth connect failed (timeout). Check that the phone is nearby with Bluetooth on.');
  assert.equal(bt.failureText({status: 0, message: "Can't reach the controller."}, {action: 'window.open'}),
    "Could not open pairing: Can't reach the controller.");
  // A network failure is the browser's, so Connect doesn't point at the phone.
  assert.equal(bt.failureText({status: 0, message: "Can't reach the controller."}, {action: 'device.connect', name: 'A'}),
    "Could not connect A: Can't reach the controller.");
});

test('the source note: bridge, then which phone streams, never blocking (D-029)', () => {
  const missing = bt.sourceNote({source: 'usb-mic', bridgePresent: false});
  assert.equal(missing.bluetoothDisabled, true);
  assert.equal(missing.tone, 'note');
  const missingChosen = bt.sourceNote({source: 'bluetooth', bridgePresent: false, status: status()});
  assert.deepEqual([missingChosen.bluetoothDisabled, missingChosen.tone], [true, 'warning']);
  assert.match(missingChosen.text, /launch fails/);

  assert.deepEqual(bt.sourceNote({source: 'usb-mic', bridgePresent: true, status: status()}),
    {bluetoothDisabled: false, tone: null, text: ''});
  assert.equal(bt.sourceNote({source: 'bluetooth', bridgePresent: null, status: null}).text, '');
  assert.equal(bt.sourceNote({source: 'bluetooth', bridgePresent: true, status: unavailable}).text,
    "Can't see the phones: BlueZ is not running.");
  assert.equal(bt.sentence('Done.'), 'Done.');
  assert.equal(bt.sentence('BlueZ timed out'), 'BlueZ timed out.');
  assert.equal(bt.sourceNote({source: 'bluetooth', bridgePresent: true,
    status: status({devices: [phone('A', {connected: true}), phone('B', {connected: true, streaming: true})]})}).text,
  'B is streaming.');
  assert.match(bt.sourceNote({source: 'bluetooth', bridgePresent: true,
    status: status({devices: [phone('A', {connected: true})]})}).text, /^A is connected but not playing/);
  const none = bt.sourceNote({source: 'bluetooth', bridgePresent: true, status: status({devices: [phone('A')]})});
  assert.deepEqual([none.tone, none.bluetoothDisabled], ['note', false]);
  assert.match(none.text, /^No phone is connected/);
});

test('the running line names the running source from /status', () => {
  assert.equal(bt.runningText({visualization_id: 'fft', state: 'running', parameters: {audio_source: 'bluetooth'}}),
    'Running with Bluetooth phone.');
  assert.equal(bt.runningText({visualization_id: 'fft', state: 'starting', parameters: {audio_source: 'usb-mic'}}),
    'Running with USB mic.');
  assert.equal(bt.runningText({visualization_id: 'fft', state: 'idle', parameters: {audio_source: 'usb-mic'}}), '');
  assert.equal(bt.runningText({visualization_id: 'sorting', state: 'running', parameters: {}}), '');
  assert.equal(bt.runningText(null), '');
});
