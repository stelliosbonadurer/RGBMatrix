// Bluetooth phones and the audio source on the Music FFT page (web-control-refactor
// M5 slice 4: D-065, D-073; the API is M3's D-026–D-030). Pure, so node --test
// covers it; fft_bluetooth_controls.js draws it.
//
// A status is GET /api/v1/bluetooth: {available, reason, adapter: {powered,
// discoverable, pairable, name}, window: {open, seconds_remaining,
// duration_seconds}, pending_request: {request_id, kind, device_id, name, code?},
// devices: [{device_id, name, paired, trusted, connected, streaming}]}.

import {sourceLabel} from './fft.js';

// D-065: every 1 s while pairing is open, every 5 s while the row is open,
// otherwise only when the page opens. Nothing while the page is hidden.
export const WINDOW_POLL_MS = 1000;
export const ROW_POLL_MS = 5000;

export function pollDelay({windowOpen = false, rowOpen = false, hidden = false} = {}) {
  if (hidden) return null;
  if (windowOpen) return WINDOW_POLL_MS;
  if (rowOpen) return ROW_POLL_MS;
  return null;
}

// Replies are applied newest first. A poll takes a ticket when it starts and
// applies only if nothing newer was applied; an action's reply is the newest
// state by definition, so a poll that started before it finished is dropped.
export function createOrdering() {
  let issued = 0;
  let applied = 0;
  return {
    start: () => ++issued,
    applies(ticket) {
      if (ticket <= applied) return false;
      applied = ticket;
      return true;
    },
    settle() {
      applied = ++issued;
    },
  };
}

// How long a window lasts, for the Pair button's hint (the status's duration).
export function windowSpan(seconds) {
  const total = Math.round(Number(seconds));
  if (!Number.isFinite(total) || total <= 0) return 'a short time';
  if (total % 60 === 0) return total === 60 ? '1 minute' : `${total / 60} minutes`;
  return `${total} seconds`;
}

export function countdown(seconds) {
  const total = Math.max(0, Math.ceil(Number(seconds) || 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

const usable = status => Boolean(status && status.available);

// The Pi's messages and reasons have no final period.
export const sentence = text => (/[.!?]$/.test(String(text)) ? String(text) : `${text}.`);

// Paired phones only: the pending request's unpaired phone shows in the request.
export const pairedPhones = status => (usable(status) ? (status.devices || []).filter(device => device.paired) : []);

export const streamingPhone = status => pairedPhones(status).find(device => device.streaming) || null;
export const connectedPhone = status => pairedPhones(status).find(device => device.connected) || null;

// What the collapsed analyzer row adds to its summary (D-065).
export function summaryParts(status) {
  const parts = [];
  const phone = streamingPhone(status);
  if (phone) parts.push(`${phone.name} streaming`);
  if (usable(status) && status.window?.open) parts.push('pairing open');
  return parts;
}

// The open window's line, and what to pick on the phone (D-073's adapter name).
export function windowText(status) {
  if (!usable(status) || !status.window?.open) return null;
  const name = status.adapter?.name;
  return {
    line: `Pairing open · ${countdown(status.window.seconds_remaining)} left`,
    hint: name
      ? `On the phone, open Bluetooth settings and pick ${name}.`
      : 'On the phone, open Bluetooth settings and pick this Pi.',
  };
}

// The one pending request (D-028): a confirmation carries a six-digit code.
export function requestView(request) {
  if (!request) return null;
  const name = request.name;
  if (request.kind === 'confirmation') {
    return {
      title: `${name} wants to pair.`,
      code: request.code ?? null,
      hint: 'Check that the phone shows the same code, then tap Pair here and on the phone.',
      accept: {action: 'request.confirm', label: 'Pair'},
      reject: {action: 'request.reject', label: 'Reject'},
    };
  }
  return {
    title: `${name} wants to pair.`,
    code: null,
    hint: 'There is no code to compare. Allow it only if you are pairing this phone now.',
    accept: {action: 'request.confirm', label: 'Allow'},
    reject: {action: 'request.reject', label: 'Reject'},
  };
}

// A paired phone's state line, its note, and its actions in display order.
// Trust is a separate step after pairing (B-006): an untrusted phone's profile
// connections are refused while the window is open.
export function phoneView(device) {
  const link = device.streaming ? 'streaming' : device.connected ? 'connected' : 'not connected';
  return {
    state: `${device.trusted ? 'trusted' : 'not trusted'} · ${link}`,
    note: device.trusted ? null : 'Not trusted yet. Tap Trust so it can connect and stream, then Connect.',
    actions: [
      device.trusted
        ? {action: 'device.untrust', label: 'Untrust'}
        : {action: 'device.trust', label: 'Trust'},
      device.connected
        ? {action: 'device.disconnect', label: 'Disconnect'}
        : {action: 'device.connect', label: 'Connect'},
      {action: 'device.forget', label: 'Forget…', confirm: true},
    ],
  };
}

export const forgetQuestion = name => `Forget ${name}? It has to pair again before it can stream.`;

const WORKING = {
  'window.open': 'Opening…',
  'window.close': 'Stopping…',
  'request.confirm': 'Pairing…',
  'request.reject': 'Rejecting…',
  'device.trust': 'Trusting…',
  'device.untrust': 'Untrusting…',
  'device.connect': 'Connecting…',
  'device.disconnect': 'Disconnecting…',
  'device.forget': 'Forgetting…',
};
export const workingLabel = action => WORKING[action] || 'Working…';

const VERBS = {
  'window.open': 'open pairing',
  'window.close': 'stop pairing',
  'request.confirm': 'answer the pairing request',
  'request.reject': 'reject the pairing request',
  'device.trust': 'trust',
  'device.untrust': 'untrust',
  'device.connect': 'connect',
  'device.disconnect': 'disconnect',
  'device.forget': 'forget',
};

// What the page says when an action fails. `name` is the phone's, when it has one.
export function failureText(error, {action, name = null} = {}) {
  const phone = name || 'That phone';
  if (error?.code === 'device_not_found' || error?.status === 404) {
    return `${phone} is no longer paired with the Pi, or the service restarted. The list is refreshed.`;
  }
  if (error?.code === 'stale_request' || error?.status === 409) {
    return 'That pairing request is over: the phone canceled it or the window closed.';
  }
  if (error?.code === 'bluetooth_unavailable' || error?.status === 503) {
    return error?.message || 'Bluetooth is unavailable on the Pi.';
  }
  const verb = VERBS[action] || 'do that';
  const target = action?.startsWith('device.') && name ? ` ${name}` : '';
  const text = `Could not ${verb}${target}: ${sentence(error?.message || 'the request failed')}`;
  // Only BlueZ's own failure suggests the phone; a network error is the browser's.
  const fromBluez = error?.code === 'bluetooth_failed' || error?.status === 502;
  return action === 'device.connect' && fromBluez ? `${text} Check that the phone is nearby with Bluetooth on.` : text;
}

// The source field's notes (D-029, D-065). `bridgePresent` is from
// /api/v1/audio/sources (null until read); `status` is the Bluetooth status (null
// until read). Returns whether the Bluetooth choice is disabled and one line
// with its tone: 'warning' when a launch would fail, 'note' otherwise.
export function sourceNote({source, bridgePresent = null, status = null}) {
  const bridgeMissing = bridgePresent === false;
  if (bridgeMissing) {
    return {
      bluetoothDisabled: true,
      tone: source === 'bluetooth' ? 'warning' : 'note',
      text: source === 'bluetooth'
        ? 'The Pi has no Bluetooth audio bridge, so a Bluetooth launch fails. Choose USB mic, or install the bridge.'
        : 'Bluetooth is off the list: the Pi has no Bluetooth audio bridge installed.',
    };
  }
  if (source !== 'bluetooth' || !status) return {bluetoothDisabled: false, tone: null, text: ''};
  if (!status.available) {
    return {bluetoothDisabled: false, tone: 'note', text: sentence(`Can't see the phones: ${status.reason || 'Bluetooth is unavailable'}`)};
  }
  const streaming = streamingPhone(status);
  if (streaming) return {bluetoothDisabled: false, tone: 'note', text: `${streaming.name} is streaming.`};
  const connected = connectedPhone(status);
  if (connected) {
    return {
      bluetoothDisabled: false,
      tone: 'note',
      text: `${connected.name} is connected but not playing. Start playback on the phone with the Pi as its output.`,
    };
  }
  return {
    bluetoothDisabled: false,
    tone: 'note',
    text: 'No phone is connected. The panel stays dark until a trusted phone plays to the Pi.',
  };
}

// Which source the running FFT uses, from /status (D-029's active_source).
export function runningText(status, fftId = 'fft') {
  const running = status?.visualization_id === fftId && (status.state === 'running' || status.state === 'starting');
  const source = running ? status.parameters?.audio_source : undefined;
  return source ? `Running with ${sourceLabel(source)}.` : '';
}
