// The Music FFT page's Bluetooth phones (web-control-refactor M5 slice 4: D-065,
// D-073): the pairing window, the one pending request, and each paired phone, at
// the end of the analyzer row. Actions reach the Pi at once and aren't part of the
// draft. The rules are in programs/fft_bluetooth.js; fft_editor.js places this and
// reads its status for the source note and the row's summary.

import {api} from './api.js';
import * as rules from './programs/fft_bluetooth.js';
import {h} from './ui.js';

let controlsCounter = 0;

// `host`:
//   rowOpen()   whether the analyzer row is open (the 5 s poll, D-065)
//   changed()   redraws the analyzer row's summary and source note
export function createBluetoothControls(host) {
  const number = ++controlsCounter;
  let current = null; // the last Bluetooth status; null before the first read
  let readError = ''; // why the last read failed, shown while there is no status
  let bridgePresent = null; // from /api/v1/audio/sources; null until read
  let working = null; // {action, key} while an action is in flight
  let confirming = null; // the device_id whose Forget asks first
  let focusKey = null; // a control to focus after the next rebuild
  let timer = 0;
  let reads = 0; // reads in flight, so polls don't pile up
  let drawnKey = '';
  let countdownNode = null;
  let messageTimer = 0;
  const ordering = rules.createOrdering();

  const titleId = `fft-bluetooth-${number}`;
  const body = h('div', {className: 'bt-body'});
  const messageLine = h('p', {className: 'meta', role: 'status', 'aria-live': 'polite', hidden: true});
  const element = h('section', {className: 'bt-section', 'aria-labelledby': titleId},
    h('h3', {className: 'subsection-title', id: titleId},
      'Bluetooth phones', h('span', {className: 'section-hint'}, ' · changes apply right away')),
    h('p', {className: 'field-help'},
      'Pair a phone once, then trust it so it can connect and stream. These act on the Pi at once; ' +
      'they aren\'t part of Send update.'),
    body,
    messageLine);

  // Errors stay until the next action; other messages fade.
  function say(text = '', tone = 'info') {
    clearTimeout(messageTimer);
    if (text && tone !== 'error') messageTimer = setTimeout(() => say(), 8000);
    messageLine.textContent = text;
    messageLine.hidden = !text;
    messageLine.className = tone === 'error' ? 'field-error' : 'meta';
    messageLine.setAttribute('role', tone === 'error' ? 'alert' : 'status');
  }

  // ---- Reading -----------------------------------------------------------------

  async function read() {
    const ticket = ordering.start();
    reads += 1;
    try {
      const reply = await api.bluetooth();
      if (ordering.applies(ticket)) {
        current = reply;
        readError = '';
      }
    } catch (failure) {
      // Keep showing the last status; with none, say why.
      if (ordering.applies(ticket)) readError = failure.message || 'the request failed.';
    } finally {
      reads -= 1;
    }
    draw();
    host.changed();
  }

  async function readSources() {
    try {
      const reply = await api.audioSources();
      const entry = (reply.sources || []).find(source => source.source_id === 'bluetooth');
      bridgePresent = typeof entry?.bridge_present === 'boolean' ? entry.bridge_present : null;
    } catch (_failure) {
      // Keep the last answer; launch validation still reports a missing bridge.
    }
    host.changed();
  }

  const windowOpen = () => Boolean(current?.available && current.window?.open);

  function schedule() {
    clearTimeout(timer);
    timer = 0;
    const delay = rules.pollDelay({windowOpen: windowOpen(), rowOpen: host.rowOpen(), hidden: document.hidden});
    if (delay !== null) timer = setTimeout(tick, delay);
  }

  async function tick() {
    timer = 0;
    if (reads === 0) await read();
    schedule();
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      clearTimeout(timer);
      timer = 0;
    } else if (rules.pollDelay({windowOpen: windowOpen(), rowOpen: host.rowOpen()}) !== null) {
      tick();
    }
  });

  // ---- Actions (D-026, D-028) -----------------------------------------------------

  async function act(action, fields = {}, name = null) {
    if (working) return;
    // The pressed control is disabled while it works; focus returns to its slot.
    const restore = body.contains(document.activeElement) ? document.activeElement.dataset.key : null;
    working = {action, key: fields.device_id ?? fields.request_id ?? null};
    confirming = null;
    say();
    draw();
    try {
      const reply = await api.bluetoothAction({action, ...fields});
      ordering.settle();
      current = reply;
      readError = '';
      if (action === 'request.confirm') say('Accepted here. If the phone asks, accept there too; then tap Trust.');
      else if (action === 'device.forget') say(`Forgot ${name}.`);
    } catch (failure) {
      const text = rules.failureText(failure, {action, name});
      // The phone or request is gone, or Bluetooth went away: show the Pi's state.
      if ([404, 409, 503].includes(failure.status)) await read();
      // A 503 shows as the unavailable view; say it only if Bluetooth answers again.
      if (failure.status !== 503 || current?.available) say(text, 'error');
    } finally {
      working = null;
      focusKey = restore;
      draw();
      schedule();
      host.changed();
    }
  }

  // ---- Drawing --------------------------------------------------------------------

  // Plain data for what the section shows, without the countdown, so a 1 s poll
  // only rebuilds the controls when something besides the time changed.
  function model() {
    if (!current) return {kind: readError ? 'error' : 'loading', readError};
    // A failed refresh keeps the last status on screen and says so.
    if (!current.available) return {kind: 'unavailable', reason: current.reason || '', stale: readError};
    const request = current.pending_request;
    return {
      kind: 'ready',
      stale: readError,
      span: rules.windowSpan(current.window?.duration_seconds),
      window: windowOpen() ? rules.windowText(current).hint : null,
      request: request ? {id: request.request_id, ...rules.requestView(request)} : null,
      phones: rules.pairedPhones(current).map(device => ({id: device.device_id, name: device.name, ...rules.phoneView(device)})),
      working,
      confirming,
    };
  }

  function button(label, {key, className = 'button secondary small', onClick, workingFor = null}) {
    const busyHere = working && workingFor && working.action === workingFor.action && working.key === workingFor.key;
    return h('button', {
      type: 'button', className, 'data-key': key, disabled: Boolean(working), onClick,
    }, busyHere ? rules.workingLabel(working.action) : label);
  }

  function windowPart(view) {
    if (!view.window) {
      return h('div', {className: 'bt-window'},
        button('Pair a new phone', {
          key: 'window.open', workingFor: {action: 'window.open', key: null},
          onClick: () => act('window.open'),
        }),
        h('span', {className: 'meta'}, `opens the Pi to new phones for ${view.span}`));
    }
    countdownNode = h('strong', {className: 'bt-countdown'});
    return h('div', {className: 'bt-window open'},
      h('div', {className: 'bt-window-line'}, countdownNode,
        button('Stop', {
          key: 'window.close', workingFor: {action: 'window.close', key: null},
          onClick: () => act('window.close'),
        })),
      h('p', {className: 'field-help'}, view.window));
  }

  function requestPart(request) {
    const fields = {request_id: request.id};
    return h('div', {className: 'bt-request', role: 'group', 'aria-label': 'Pairing request'},
      h('p', {className: 'bt-request-title'}, request.title),
      request.code
        ? h('p', {className: 'bt-code'},
          h('span', {'aria-hidden': 'true'}, request.code),
          h('span', {className: 'visually-hidden'}, `Code ${request.code.split('').join(' ')}`))
        : null,
      h('p', {className: 'field-help'}, request.hint),
      h('div', {className: 'bt-actions'},
        button(request.reject.label, {
          key: `request-${request.id}-reject`, workingFor: {action: 'request.reject', key: request.id},
          onClick: () => act('request.reject', fields),
        }),
        button(request.accept.label, {
          key: `request-${request.id}-accept`, className: 'button primary small',
          workingFor: {action: 'request.confirm', key: request.id},
          onClick: () => act('request.confirm', fields),
        })));
  }

  function phonePart(phone) {
    const fields = {device_id: phone.id};
    let actions;
    if (confirming === phone.id) {
      actions = h('div', {className: 'bt-confirm'},
        h('p', {}, rules.forgetQuestion(phone.name)),
        h('div', {className: 'bt-actions'},
          button('Cancel', {
            key: `phone-${phone.id}-cancel`, className: 'button ghost small',
            onClick: () => {
              confirming = null;
              focusKey = `phone-${phone.id}-slot2`;
              draw();
            },
          }),
          button('Forget', {
            key: `phone-${phone.id}-confirm`, className: 'button danger small',
            workingFor: {action: 'device.forget', key: phone.id},
            onClick: () => act('device.forget', {...fields, confirm: true}, phone.name),
          })));
    } else {
      // Keyed by slot, so after Trust the focus is on Untrust (D-065's order).
      actions = h('div', {className: 'bt-actions'}, phone.actions.map((item, slot) => button(item.label, {
        key: `phone-${phone.id}-slot${slot}`,
        className: item.confirm ? 'button ghost small danger-text' : 'button secondary small',
        workingFor: {action: item.action, key: phone.id},
        onClick: item.confirm
          ? () => {
            confirming = phone.id;
            focusKey = `phone-${phone.id}-cancel`;
            say();
            draw();
          }
          : () => act(item.action, fields, phone.name),
      })));
    }
    return h('li', {className: 'bt-phone'},
      h('div', {className: 'bt-phone-head'},
        h('strong', {className: 'bt-phone-name'}, phone.name),
        h('span', {className: 'bt-state'}, phone.state)),
      phone.note ? h('p', {className: 'field-help'}, phone.note) : null,
      actions);
  }

  function parts(view) {
    if (view.kind === 'loading') return [h('p', {className: 'meta'}, 'Checking Bluetooth…')];
    if (view.kind === 'error') {
      return [
        h('p', {className: 'field-error'}, `Couldn't read the Bluetooth status: ${view.readError}`),
        button('Try again', {key: 'retry', onClick: () => read().then(schedule)}),
      ];
    }
    const stale = view.stale
      ? h('p', {className: 'field-warning'},
        `Couldn't refresh the Bluetooth status: ${rules.sentence(view.stale)} This is the last state the Pi reported.`)
      : null;
    if (view.kind === 'unavailable') {
      return [stale, h('p', {className: 'note'}, rules.sentence(`Bluetooth is unavailable: ${view.reason || 'the Pi did not say why'}`))];
    }
    return [
      stale,
      windowPart(view),
      view.request ? requestPart(view.request) : null,
      view.phones.length
        ? h('ul', {className: 'bt-phones', 'aria-label': 'Paired phones'}, view.phones.map(phonePart))
        : h('p', {className: 'meta'}, 'No paired phones yet.'),
    ];
  }

  function draw() {
    const view = model();
    const key = JSON.stringify(view);
    if (key !== drawnKey) {
      drawnKey = key;
      const active = document.activeElement;
      const keep = focusKey || (active && body.contains(active) ? active.dataset.key : null);
      countdownNode = null;
      body.replaceChildren(...parts(view).filter(Boolean));
      // A poll can rebuild while the user is elsewhere on the page: don't scroll.
      if (keep) body.querySelector(`[data-key="${CSS.escape(keep)}"]`)?.focus({preventScroll: true});
    }
    // A focus target applies to this draw only, rebuilt or not.
    focusKey = null;
    if (countdownNode) countdownNode.textContent = rules.windowText(current)?.line || '';
  }

  draw();

  return {
    element,

    // Once when the page opens (D-065), with the bridge check.
    async load() {
      await Promise.all([read(), readSources()]);
      schedule();
    },

    // The analyzer row opened or closed: read now when it opens, then poll.
    setRowOpen(open) {
      if (!open) {
        schedule();
        return;
      }
      clearTimeout(timer);
      timer = 0;
      Promise.all([read(), readSources()]).then(schedule);
    },

    status: () => current,
    bridgePresent: () => bridgePresent,
  };
}
