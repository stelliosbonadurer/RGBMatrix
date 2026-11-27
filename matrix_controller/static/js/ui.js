// DOM helpers shared by the home and program pages: an element builder, the
// labelled field with its help text, messages, and status polling.

import {displayTokens, fieldLabel, humanize, parseValue, rangeText} from './model.js';

export function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'className') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, '');
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

let fieldCounter = 0;

function writeInput(parameter, input, value) {
  if (parameter.type === 'bool') input.checked = Boolean(value);
  else input.value = value === null || value === undefined ? '' : String(value);
}

// A labelled field: label, input, help text (always visible, with the range),
// a changed marker, and an error line. `onInput(name)` fires on every edit.
export function createField(parameter, value, {onInput, readOnly = false} = {}) {
  const id = `field-${parameter.name.replaceAll('_', '-')}-${++fieldCounter}`;
  const defaultHelp = parameter.description || '';
  const range = rangeText(parameter);
  const label = h('label', {className: 'field-label', for: id}, fieldLabel(parameter));
  const marker = h('span', {className: 'changed-marker', title: 'Unsent change', hidden: true}, '●');
  const helpText = h('span', {}, defaultHelp);
  const rangeNote = range ? h('span', {className: 'field-range'}, range) : null;
  const help = h('p', {className: 'field-help', id: `${id}-help`}, helpText, rangeNote ? ' ' : null, rangeNote);
  const error = h('p', {className: 'field-error', id: `${id}-error`, role: 'alert', hidden: true});
  // A warning doesn't block sending (the FFT page's assumed Nyquist, D-069).
  const warning = h('p', {className: 'field-warning', id: `${id}-warning`, hidden: true});

  let input;
  if (parameter.type === 'bool') {
    input = h('input', {type: 'checkbox', id, className: 'switch'});
  } else if (parameter.type === 'enum') {
    input = h('select', {id});
    for (const choice of parameter.choices || []) input.append(h('option', {value: choice}, choice));
  } else {
    input = h('input', {type: 'text', id, autocomplete: 'off', spellcheck: 'false', autocapitalize: 'off'});
    if (parameter.type === 'int') input.inputMode = 'numeric';
    if (parameter.type === 'float') input.inputMode = 'decimal';
    if (parameter.type === 'string' && parameter.maximum_length !== undefined) {
      input.maxLength = parameter.maximum_length;
    }
  }
  input.name = parameter.name;
  input.disabled = readOnly;
  input.setAttribute('aria-describedby', `${id}-help ${id}-error ${id}-warning`);
  writeInput(parameter, input, value);

  const head = h('div', {className: 'field-head'}, label, marker);
  const element = h('div', {className: `field field-${parameter.type}`, 'data-name': parameter.name});
  if (parameter.type === 'bool') {
    head.append(input);
    element.append(head, help, error, warning);
  } else {
    element.append(head, input, help, error, warning);
  }
  if (onInput) {
    const eventName = parameter.type === 'bool' || parameter.type === 'enum' ? 'change' : 'input';
    input.addEventListener(eventName, () => onInput(parameter.name));
  }

  return {
    parameter,
    element,
    input,
    read() {
      return parseValue(parameter, parameter.type === 'bool' ? input.checked : input.value);
    },
    write(next) {
      writeInput(parameter, input, next);
    },
    setChanged(changed) {
      marker.hidden = !changed;
      element.classList.toggle('changed', changed);
    },
    setError(message) {
      error.textContent = message || '';
      error.hidden = !message;
      input.setAttribute('aria-invalid', String(Boolean(message)));
    },
    setWarning(message) {
      warning.textContent = message || '';
      warning.hidden = !message;
    },
    setHidden(hidden) {
      element.hidden = hidden;
    },
    setDisabled(disabled) {
      input.disabled = readOnly || disabled;
    },
    setHelp(text) {
      // A program's rule text (Graph Traversal's edge limits) replaces the
      // description and states its own range.
      helpText.textContent = text || defaultHelp;
      if (rangeNote) rangeNote.hidden = Boolean(text);
    },
    setDisabledChoices(disabled) {
      if (parameter.type !== 'enum') return;
      for (const option of input.options) option.disabled = disabled.includes(option.value);
    },
    // Replaces an enum's choices and keeps the value when it is still one; a
    // value that isn't reads as '' (and fails parseValue) until written.
    setChoices(choices) {
      if (parameter.type !== 'enum') return;
      const value = input.value;
      parameter.choices = [...choices];
      input.replaceChildren(...choices.map(choice => h('option', {value: choice}, choice)));
      input.value = value;
    },
  };
}

// The read-only matrix table on program pages.
export function renderValueTable(list, parameters, values) {
  list.replaceChildren();
  for (const parameter of parameters) {
    const value = values?.[parameter.name];
    let text;
    if (parameter.type === 'bool') text = value ? 'on' : 'off';
    else if (value === '' || value === undefined || value === null) text = '—';
    else text = String(value);
    list.append(h('dt', {}, humanize(parameter.name)), h('dd', {}, text));
  }
}

const messageTimers = new WeakMap();

// Errors stay until the next message; confirmations fade after a few seconds.
export function showMessage(element, text, tone = 'info') {
  clearTimeout(messageTimers.get(element));
  element.textContent = text || '';
  element.dataset.tone = tone;
  element.hidden = !text;
  if (text && tone !== 'error') {
    messageTimers.set(element, setTimeout(() => {
      element.hidden = true;
    }, 6000));
  }
}

// A flag line, one non-wrapping span per argument, so lines break only between
// arguments (a hyphen inside a flag is otherwise a break point). Copying the
// text gives the same string as formatArgv.
export function renderFlags(element, argv, emptyText = '') {
  const tokens = displayTokens(argv);
  element.replaceChildren();
  if (!tokens.length) {
    element.textContent = emptyText;
    return;
  }
  tokens.forEach((token, index) => {
    if (index) element.append(' ');
    element.append(h('span', {className: 'flag-token'}, token));
  });
}

// A banner with a message and optional buttons: [{label, onClick}].
export function showBanner(element, text, actions = []) {
  element.replaceChildren();
  if (!text) {
    element.hidden = true;
    return;
  }
  element.append(h('p', {}, text));
  if (actions.length) {
    element.append(h('div', {className: 'banner-actions'},
      actions.map(action => h('button', {type: 'button', className: 'button secondary small', onClick: action.onClick}, action.label))));
  }
  element.hidden = false;
}

export function setTone(element, tone) {
  element.dataset.tone = tone;
}

export function stamp(element) {
  const time = new Intl.DateTimeFormat([], {hour: 'numeric', minute: '2-digit', second: '2-digit'}).format(new Date());
  element.textContent = `Updated ${time}`;
}

// Runs `task` every `milliseconds` while the page is visible, never overlapping,
// and once more whenever the page becomes visible again. Returns a trigger.
export function poll(milliseconds, task) {
  let busy = false;
  const tick = async () => {
    if (document.hidden || busy) return;
    busy = true;
    try {
      await task();
    } finally {
      busy = false;
    }
  };
  setInterval(tick, milliseconds);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) tick();
  });
  return tick;
}

// Asks before leaving with unsent edits (D-038): a confirm on any link on the
// page, and the browser's prompt on reload or close (iOS Safari ignores that one).
export function guardLeaving(unsentCount) {
  let confirmed = false;
  window.addEventListener('beforeunload', event => {
    if (!confirmed && unsentCount() > 0) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  document.addEventListener('click', event => {
    const link = event.target instanceof Element ? event.target.closest('a[href]') : null;
    if (!link || link.target === '_blank') return;
    const count = unsentCount();
    if (count === 0) return;
    if (window.confirm(`Discard ${plural(count, 'unsent change')}?`)) confirmed = true;
    else event.preventDefault();
  });
}

export function plural(count, word) {
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}
