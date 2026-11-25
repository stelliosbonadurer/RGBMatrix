// The Music FFT page's draft (web-control-refactor M5 slice 1): the analyzer row,
// the output selector, and the layer list with each layer's settings. program.js
// keeps the shell (status line, Launch/Terminate, the bottom bar) and asks this
// editor for its unsent count, validity, and submit (D-062). The rules live in
// programs/fft.js; this file only draws them.

import {api} from './api.js';
import {parseValue} from './model.js';
import * as fft from './programs/fft.js';
import {createField, h} from './ui.js';

export const id = fft.id;

const SNAPSHOT_WAIT_MS = 15000;
const SNAPSHOT_POLL_MS = 300;
const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
const sameJson = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object, key);

let editorCounter = 0;

// `parameters` are the program's launch options (only audio_source, D-050).
// `onChange` fires after every edit so program.js can redraw the bottom bar;
// `onMessage(text, tone)` reports a launch that finished after its wait.
export function createEditor({program, parameters, onChange, onMessage = () => {}}) {
  const number = ++editorCounter;
  const sourceSpec = parameters.find(parameter => parameter.name === 'audio_source') ||
    {name: 'audio_source', choices: [], default: null};
  let uidCounter = 0;
  const nextUid = () => `layer-${++uidCounter}`;

  let catalog = null; // {structure, assumed_until_launch}
  let info = null; // the structure whose capabilities the page draws from
  let liveStructure = null; // the snapshot's structure for `snapshot`'s run
  let snapshot = null; // {run_id, revision} of the run this page may update
  let expectedRun = null; // the run whose snapshot this page may load (D-062)
  let fetching = false;
  let lateLaunch = null; // {runId, document, sentUids} when a launch outlived its wait
  let baseline = {source: sourceSpec.default, backend: null, layers: []};
  let source = sourceSpec.default;
  let backend = null;
  let order = []; // layer uids in document (draw) order
  const rows = new Map(); // uid -> row
  let stash = {}; // uid -> values the backend lock replaced (D-061)
  let openUid = null;
  let busy = false;
  let serverError = null; // {target, uid, key, message}
  let lockNote = '';
  let lastStatus = null;
  let loaded = false; // nothing can be launched before the catalog loads
  let revealPending = false; // a server error to scroll to once inputs are enabled

  // ---- Analyzer row (D-065; Bluetooth arrives in slice 4) -------------------

  const analyzerSummary = h('span', {className: 'summary-hint'});
  const sourceMarker = h('span', {className: 'changed-marker', title: 'Unsent change', hidden: true}, '●');
  const sourceInputs = [];
  const sourceChoices = h('div', {className: 'choice-row', role: 'radiogroup', 'aria-labelledby': `fft-source-${number}`});
  for (const choice of sourceSpec.choices || []) {
    const input = h('input', {type: 'radio', name: `fft-source-${number}`, value: choice});
    input.addEventListener('change', () => {
      source = input.value;
      edited();
    });
    sourceInputs.push(input);
    sourceChoices.append(h('label', {className: 'choice'}, input, h('span', {}, fft.sourceLabel(choice))));
  }
  const sourceField = h('div', {className: 'field'},
    h('div', {className: 'field-head'},
      h('span', {className: 'field-label', id: `fft-source-${number}`}, 'Source'), sourceMarker),
    sourceChoices,
    h('p', {className: 'field-help'}, `${sourceSpec.description || 'Audio input.'} Changing it relaunches ${program.name}.`));
  const factsTable = h('dl', {className: 'value-table'});
  const analyzerRow = h('details', {className: 'panel analyzer-row'},
    h('summary', {}, h('span', {className: 'summary-title'}, 'Analyzer'), analyzerSummary),
    h('div', {className: 'fields'}, sourceField),
    h('h3', {className: 'subsection-title'}, 'Analysis · read-only'),
    factsTable);

  // ---- Output backend (D-061) -------------------------------------------------

  const outputId = `fft-output-${number}`;
  const outputSelect = h('select', {id: outputId});
  const outputMarker = h('span', {className: 'changed-marker', title: 'Unsent change', hidden: true}, '●');
  const outputError = h('p', {className: 'field-error', role: 'alert', hidden: true});
  const outputNote = h('p', {className: 'note', hidden: true});
  outputSelect.addEventListener('change', () => switchOutput(outputSelect.value));
  const outputPanel = h('section', {className: 'panel'},
    h('div', {className: 'field output-field'},
      h('div', {className: 'field-head'},
        h('label', {className: 'field-label', for: outputId}, 'Output'), outputMarker),
      outputSelect,
      h('p', {className: 'field-help'}, 'How frames reach the panel. Changing it is a live update.'),
      outputError),
    outputNote);

  // ---- Layer list (D-052, D-054–D-057) ------------------------------------------

  const typeSelect = h('select', {'aria-label': 'Layer type'});
  const addButton = h('button', {type: 'button', className: 'button secondary small', onClick: addLayer}, '+ Add layer');
  const layerCount = h('span', {className: 'meta'});
  const costNote = h('p', {className: 'note', hidden: true});
  const listError = h('p', {className: 'field-error', role: 'alert', hidden: true});
  const emptyNote = h('p', {className: 'meta empty-layers', hidden: true},
    'No layers. The panel stays dark until you add one.');
  const list = h('ol', {className: 'layer-list'});
  const layersPanel = h('section', {className: 'panel layers-panel', 'aria-labelledby': `fft-layers-${number}`},
    h('div', {className: 'section-head'},
      h('h2', {className: 'section-title', id: `fft-layers-${number}`}, 'Layers'),
      h('span', {className: 'section-hint'}, 'the top layer draws over the ones below')),
    costNote,
    h('div', {className: 'layer-add'}, typeSelect, addButton, h('span', {className: 'spacer'}), layerCount),
    listError,
    list,
    emptyNote);

  const element = h('div', {className: 'fft-editor'}, analyzerRow, outputPanel, layersPanel);

  // ---- Reading the draft --------------------------------------------------------

  const descriptorOf = layerType => fft.descriptorFor(info, layerType);

  function readLayer(row) {
    const settings = {...row.extraSettings};
    const errors = {};
    for (const field of row.descriptor?.fields || []) {
      if (field.type === 'points') {
        const result = row.points.read();
        settings[field.name] = result.value;
        Object.assign(errors, result.errors);
      } else {
        const {value, error} = row.fields.get(field.name).read();
        settings[field.name] = value;
        if (error) errors[field.name] = error;
      }
    }
    const labels = new Map((row.descriptor?.fields || []).map(field => [field.name, field.label || field.name]));
    for (const [name, message] of Object.entries(fft.crossFieldErrors(settings, name => labels.get(name) || name))) {
      errors[name] ||= message;
    }
    return {
      layer: {
        uid: row.uid,
        layer_id: row.nameField.read().value,
        layer_type: row.layer_type,
        analyzer_id: row.analyzer_id,
        visible: row.visibleInput.checked,
        settings,
      },
      errors,
    };
  }

  function readDraft() {
    const layers = [];
    const errors = new Map();
    for (const uid of order) {
      const {layer, errors: layerErrors} = readLayer(rows.get(uid));
      layers.push(layer);
      errors.set(uid, layerErrors);
    }
    layers.forEach((layer, index) => {
      const others = layers.filter((_other, otherIndex) => otherIndex !== index).map(other => other.layer_id);
      const error = fft.nameError(layer.layer_id, others);
      if (error) errors.get(layer.uid).layer_id = error;
    });
    return {source, backend, layers, errors};
  }

  // ---- Rows ---------------------------------------------------------------------

  function createPointsEditor(field) {
    const items = Object.fromEntries((field.item_fields || []).map(item => [item.name, item]));
    const positionSpec = fft.settingParameter(items.position || {name: 'position', type: 'number'});
    const weightSpec = fft.settingParameter(items.weight || {name: 'weight', type: 'number'});
    const minItems = field.min_items ?? 1;
    const maxItems = field.max_items ?? Infinity;
    const marker = h('span', {className: 'changed-marker', title: 'Unsent change', hidden: true}, '●');
    const table = h('div', {className: 'points-list'});
    const error = h('p', {className: 'field-error', role: 'alert', hidden: true});
    const add = h('button', {type: 'button', className: 'button secondary small'}, '+ Add point');
    let entries = [];
    let range = null;
    let locked = false;

    function entry(point, index) {
      const position = h('input', {type: 'text', inputMode: 'decimal', autocomplete: 'off', spellcheck: 'false',
        'aria-label': `Point ${index + 1} ${positionSpec.label || 'position'}`});
      const weight = h('input', {type: 'text', inputMode: 'decimal', autocomplete: 'off', spellcheck: 'false',
        'aria-label': `Point ${index + 1} ${weightSpec.label || 'weight'}`});
      position.value = String(point.position);
      weight.value = String(point.weight);
      const hint = h('span', {className: 'point-hint'});
      const remove = h('button', {type: 'button', className: 'icon-button', 'aria-label': `Remove point ${index + 1}`}, '✕');
      const message = h('p', {className: 'field-error point-error', role: 'alert', hidden: true});
      const item = {position, weight, hint, remove, message,
        element: h('div', {className: 'point-row'}, position, hint, weight, remove, message)};
      position.addEventListener('input', edited);
      weight.addEventListener('input', edited);
      remove.addEventListener('click', () => {
        if (entries.length <= minItems) return;
        entries.splice(entries.indexOf(item), 1);
        item.element.remove();
        relabel();
        edited();
      });
      return item;
    }

    function relabel() {
      entries.forEach((item, index) => {
        item.position.setAttribute('aria-label', `Point ${index + 1} ${positionSpec.label || 'position'}`);
        item.weight.setAttribute('aria-label', `Point ${index + 1} ${weightSpec.label || 'weight'}`);
        item.remove.setAttribute('aria-label', `Remove point ${index + 1}`);
      });
    }

    function write(points) {
      entries = fft.sortedPoints(points || []).map(entry);
      table.replaceChildren(
        h('div', {className: 'point-row point-head', 'aria-hidden': 'true'},
          h('span', {}, positionSpec.label || 'Position'), h('span', {}, '≈'),
          h('span', {}, weightSpec.label || 'Weight'), h('span', {})),
        ...entries.map(item => item.element));
    }

    function read() {
      const value = [];
      const errors = {};
      entries.forEach((item, index) => {
        const position = parseValue(positionSpec, item.position.value);
        const weight = parseValue(weightSpec, item.weight.value);
        value.push({position: position.value, weight: weight.value});
        if (position.error) errors[`${field.name}[${index}].position`] = position.error;
        if (weight.error) errors[`${field.name}[${index}].weight`] = weight.error;
      });
      for (const index of fft.duplicatePositions(value.map(point => point.position))) {
        errors[`${field.name}[${index}].position`] ||= 'Two points can\'t share a position.';
      }
      if (entries.length < minItems || entries.length > maxItems) {
        errors[field.name] = `Use ${minItems} to ${maxItems} points.`;
      }
      return {value, errors};
    }

    add.addEventListener('click', () => {
      const {value, errors} = read();
      if (Object.keys(errors).length || entries.length >= maxItems) return;
      const {points, index} = fft.addPoint(value, positionSpec.minimum ?? 0, positionSpec.maximum ?? 1);
      write(points);
      edited();
      entries[index]?.weight.focus();
    });

    const element = h('div', {className: 'field field-points', 'data-name': field.name},
      h('div', {className: 'field-head'}, h('span', {className: 'field-label'}, field.label || field.name), marker),
      table,
      h('div', {className: 'point-actions'}, add),
      h('p', {className: 'field-help'}, field.help || ''),
      error);

    return {
      element,
      read,
      write,
      setRange(minHz, maxHz) {
        range = Number.isFinite(minHz) && Number.isFinite(maxHz) && minHz > 0 && maxHz > minHz ? [minHz, maxHz] : null;
      },
      // `errors` holds this field's keys: the list, and `name[i].position`.
      setState({changed, errors, disabled}) {
        marker.hidden = !changed;
        element.classList.toggle('changed', changed);
        error.textContent = errors[field.name] || '';
        error.hidden = !errors[field.name];
        const {value} = read();
        entries.forEach((item, index) => {
          const positionError = errors[`${field.name}[${index}].position`];
          const weightError = errors[`${field.name}[${index}].weight`];
          const text = [positionError, weightError].filter(Boolean).join(' ');
          item.message.textContent = text;
          item.message.hidden = !text;
          item.position.setAttribute('aria-invalid', String(Boolean(positionError)));
          item.weight.setAttribute('aria-invalid', String(Boolean(weightError)));
          const position = value[index].position;
          item.hint.textContent = range && Number.isFinite(position) && position >= 0 && position <= 1
            ? fft.formatHz(fft.positionHz(range[0], range[1], position))
            : '';
          for (const control of [item.position, item.weight, item.remove]) control.disabled = disabled || locked;
          item.remove.disabled = item.remove.disabled || entries.length <= minItems;
        });
        add.disabled = disabled || locked || entries.length >= maxItems ||
          Object.keys(errors).some(key => key.startsWith(`${field.name}[`));
      },
      setLocked(next) {
        locked = next;
      },
      focus(key) {
        const match = /\[(\d+)\](?:\.(position|weight))?$/.exec(key || '');
        const item = match ? entries[Number(match[1])] : entries[0];
        (match?.[2] === 'weight' ? item?.weight : item?.position)?.focus();
      },
    };
  }

  function createRow(layer) {
    const uid = layer.uid;
    const descriptor = descriptorOf(layer.layer_type);
    const known = new Set((descriptor?.fields || []).map(field => field.name));
    const extraSettings = Object.fromEntries(Object.entries(layer.settings || {}).filter(([name]) => !known.has(name)));
    const bodyId = `fft-layer-${number}-${uid}`;

    const handle = h('button', {type: 'button', className: 'drag-handle', 'aria-label': 'Drag to reorder'}, '≡');
    const title = h('button', {type: 'button', className: 'layer-title', 'aria-expanded': 'false', 'aria-controls': bodyId});
    const unsent = h('span', {className: 'badge layer-unsent', hidden: true});
    const visibleInput = h('input', {type: 'checkbox', className: 'switch', 'aria-label': 'Show this layer'});
    visibleInput.checked = Boolean(layer.visible);
    const chevron = h('button', {type: 'button', className: 'icon-button layer-chevron', 'aria-expanded': 'false',
      'aria-controls': bodyId, 'aria-label': 'Open layer'}, '▸');
    const summary = h('span', {className: 'layer-summary'});
    const up = h('button', {type: 'button', className: 'icon-button', 'aria-label': 'Move up'}, '▲');
    const down = h('button', {type: 'button', className: 'icon-button', 'aria-label': 'Move down'}, '▼');
    const remove = h('button', {type: 'button', className: 'icon-button danger-text', 'aria-label': 'Remove layer'}, '✕');
    const layerError = h('p', {className: 'field-error', role: 'alert', hidden: true});

    const nameField = createField(
      // No input maxLength: browsers count UTF-16 units; nameError counts characters.
      {name: 'layer_id', label: 'Name', type: 'string',
        description: `Unique among the layers, at most ${fft.NAME_MAX} characters.`},
      layer.layer_id,
      {onInput: edited},
    );
    const fields = new Map();
    let points = null;
    const groups = [];
    const body = h('div', {className: 'layer-body', id: bodyId, hidden: true},
      h('div', {className: 'fields'}, nameField.element), layerError);
    for (const group of fft.settingGroups(descriptor)) {
      const container = h('div', {className: 'fields'});
      const heading = h('h3', {className: 'subsection-title'}, group.label);
      for (const field of group.fields) {
        if (field.type === 'points') {
          points = createPointsEditor(field);
          points.write(layer.settings?.[field.name]);
          container.append(points.element);
        } else {
          const control = createField(fft.settingParameter(field), layer.settings?.[field.name], {onInput: edited});
          fields.set(field.name, control);
          container.append(control.element);
        }
      }
      groups.push({heading, fields: group.fields.map(field => field.name)});
      body.append(heading, container);
    }

    const element = h('li', {className: 'layer-row', 'data-uid': uid},
      h('div', {className: 'layer-line'}, handle, title, unsent, visibleInput, chevron),
      h('div', {className: 'layer-line layer-line-secondary'}, summary, up, down, remove),
      body);

    const row = {
      uid, layer_type: layer.layer_type, analyzer_id: layer.analyzer_id, descriptor, extraSettings,
      element, handle, title, unsent, visibleInput, chevron, summary, up, down, remove, body, layerError,
      nameField, fields, points, groups,
    };
    title.addEventListener('click', () => setOpen(openUid === uid ? null : uid));
    chevron.addEventListener('click', () => setOpen(openUid === uid ? null : uid));
    visibleInput.addEventListener('change', edited);
    up.addEventListener('click', () => move(uid, 1, up));
    down.addEventListener('click', () => move(uid, -1, down));
    remove.addEventListener('click', () => removeLayer(uid));
    handle.addEventListener('pointerdown', event => startDrag(event, row));
    rows.set(uid, row);
    return row;
  }

  function placeRows() {
    list.append(...fft.displayRows(order).map(uid => rows.get(uid).element));
  }

  function buildRows(layers) {
    list.replaceChildren();
    rows.clear();
    for (const layer of layers) createRow(layer);
    order = layers.map(layer => layer.uid);
    placeRows();
  }

  // One row open at a time (D-052); closing keeps its edits.
  function setOpen(uid) {
    openUid = uid;
    for (const row of rows.values()) {
      const open = row.uid === uid;
      row.body.hidden = !open;
      row.element.classList.toggle('open', open);
      row.title.setAttribute('aria-expanded', String(open));
      row.chevron.setAttribute('aria-expanded', String(open));
      row.chevron.setAttribute('aria-label', open ? 'Close layer' : 'Open layer');
      row.chevron.textContent = open ? '▾' : '▸';
    }
  }

  function move(uid, delta, button) {
    order = fft.moveLayer(order, order.indexOf(uid), delta);
    placeRows();
    edited();
    if (!button.disabled) button.focus();
  }

  function removeLayer(uid) {
    const name = String(rows.get(uid).nameField.read().value).trim() || 'this layer';
    if (!window.confirm(`Remove layer ${name}?`)) return;
    order = order.filter(other => other !== uid);
    rows.get(uid).element.remove();
    rows.delete(uid);
    delete stash[uid];
    if (openUid === uid) openUid = null;
    edited();
  }

  function addLayer() {
    const layerType = typeSelect.value;
    const descriptor = descriptorOf(layerType);
    if (!descriptor || order.length >= fft.maxLayers(info)) return;
    const names = readDraft().layers.map(layer => layer.layer_id);
    const {settings, replaced} = fft.lockLayer(fft.defaultSettings(descriptor), descriptor, backend);
    const uid = nextUid();
    if (Object.keys(replaced).length) stash[uid] = replaced;
    createRow({
      uid,
      layer_id: fft.freeName(layerType, names),
      layer_type: layerType,
      analyzer_id: info?.available_analyzer_ids?.[0] ?? info?.analyzers?.[0]?.analyzer_id,
      visible: true,
      settings,
    });
    order.push(uid); // appended to the document: the top row (D-054)
    placeRows();
    setOpen(uid);
    edited();
    rows.get(uid).element.scrollIntoView({block: 'nearest'});
  }

  // Drag from the handle only (D-056), with pointer events for mouse and touch.
  // Only the neighbors move in the DOM, so the dragged row keeps pointer capture.
  function startDrag(event, row) {
    if (busy || (event.pointerType === 'mouse' && event.button !== 0)) return;
    event.preventDefault();
    if (openUid === row.uid) setOpen(null);
    const handle = row.handle;
    handle.setPointerCapture(event.pointerId);
    row.element.classList.add('dragging');
    const middle = node => {
      const box = node.getBoundingClientRect();
      return box.top + box.height / 2;
    };
    const onMove = moveEvent => {
      for (let guard = 0; guard < rows.size; guard += 1) {
        const next = row.element.nextElementSibling;
        const previous = row.element.previousElementSibling;
        if (next && moveEvent.clientY > middle(next)) row.element.before(next);
        else if (previous && moveEvent.clientY < middle(previous)) row.element.after(previous);
        else break;
      }
      if (moveEvent.clientY < 48) window.scrollBy(0, -12);
      else if (moveEvent.clientY > window.innerHeight - 110) window.scrollBy(0, 12);
    };
    const onEnd = () => {
      handle.removeEventListener('pointermove', onMove);
      handle.removeEventListener('pointerup', onEnd);
      handle.removeEventListener('pointercancel', onEnd);
      handle.removeEventListener('lostpointercapture', onEnd);
      row.element.classList.remove('dragging');
      const shown = [...list.children].map(child => child.dataset.uid);
      order = shown.reverse();
      edited();
    };
    handle.addEventListener('pointermove', onMove);
    handle.addEventListener('pointerup', onEnd);
    handle.addEventListener('pointercancel', onEnd);
    handle.addEventListener('lostpointercapture', onEnd);
  }

  // ---- Backend lock (D-061) --------------------------------------------------------

  function lockedLabels(descriptor, name) {
    const labels = new Map((descriptor?.fields || []).map(field => [field.name, field.label || field.name]));
    return [...fft.lockedNames(descriptor, name)].map(field => labels.get(field) || field);
  }

  function switchOutput(to) {
    const draft = readDraft();
    const hadStash = Object.keys(stash).length > 0;
    const result = fft.switchBackend(draft.layers, descriptorOf, stash, to);
    stash = result.stash;
    for (const change of result.changes) rows.get(change.uid)?.fields.get(change.name)?.write(change.to);
    backend = to;
    const fixed = [...new Set(draft.layers.flatMap(layer => lockedLabels(descriptorOf(layer.layer_type), to)))];
    if (fixed.length) {
      lockNote = `${to} fixes ${fixed.join(', ')} on every layer, so they are set and locked.` +
        (result.changes.length ? ' Switch back before sending to get your earlier values back.' : '');
    } else if (hadStash) {
      lockNote = 'Restored the values the previous output had fixed.';
    } else {
      lockNote = '';
    }
    edited();
  }

  // ---- Loading -----------------------------------------------------------------------

  function writeChoices() {
    const types = fft.layerTypes(info).map(item => item.layer_type);
    typeSelect.replaceChildren(...types.map(type => h('option', {value: type}, type)));
    const backends = fft.outputBackends(info);
    outputSelect.replaceChildren(...backends.map(name => h('option', {value: name}, name)));
  }

  // Replaces the draft with the baseline (a load, a reply, or Discard). The open
  // row stays open by its place in the list, since a reply's layers are new rows.
  function loadDraft() {
    const openIndex = order.indexOf(openUid);
    source = baseline.source;
    backend = baseline.backend;
    stash = {};
    lockNote = '';
    serverError = null;
    writeChoices();
    buildRows(baseline.layers.map(layer => ({...layer, settings: JSON.parse(JSON.stringify(layer.settings))})));
    setOpen(openIndex >= 0 && openIndex < order.length ? order[openIndex] : null);
    refresh();
  }

  function setBaselineFrom(structure) {
    info = structure;
    baseline = {
      source: baseline.source,
      backend: structure.output_backend,
      layers: fft.layersFromStructure(structure, nextUid),
    };
  }

  function adopt(reply, {reloadDraft}) {
    snapshot = {run_id: reply.run_id, revision: reply.revision};
    liveStructure = reply.structure;
    setBaselineFrom(reply.structure);
    if (reloadDraft) loadDraft();
    else refresh();
  }

  async function fetchSnapshot() {
    try {
      const reply = await api.runtime();
      if (reply && reply.structure && reply.connected !== false) return reply;
    } catch (_error) {
      // Not connected yet, or FFT stopped; the caller decides what that means.
    }
    return null;
  }

  // The snapshot of the run this page expects, once its control channel connects.
  async function maybeLoadSnapshot(status) {
    if (fetching || !expectedRun || snapshot) return;
    if (status?.visualization_id !== fft.id || String(status.run_id) !== expectedRun) return;
    if (status.state !== 'running' || !status.runtime_control?.connected) return;
    fetching = true;
    try {
      const reply = await fetchSnapshot();
      if (!reply || String(reply.run_id) !== expectedRun || snapshot) return;
      const draft = readDraft();
      if (lateLaunch?.runId === expectedRun &&
          JSON.stringify(fft.structureDocument(draft.backend, draft.layers)) === lateLaunch.document) {
        // Nothing was edited since the launch: finish it as submit would have.
        const {text, tone} = finishLaunch(reply, lateLaunch.sentUids, 'launch');
        onMessage(text, tone);
      } else if (lateLaunch?.runId === expectedRun && reply.structure?.launch_error) {
        // Edited since the launch: keep the edits, but still say the Pi refused it.
        const launchError = reply.structure.launch_error;
        adopt(reply, {reloadDraft: false});
        placeError(launchError.path, launchError.message, lateLaunch.sentUids);
        refresh();
        requestReveal();
        onMessage(`Launched with the default layer: ${launchError.message}`, 'error');
      } else {
        adopt(reply, {reloadDraft: fft.draftChanges(baseline, draft).total === 0});
      }
      lateLaunch = null;
    } finally {
      fetching = false;
    }
  }

  // After a launch's snapshot arrives: reload the draft, or keep it and mark the
  // field when the Pi refused the structure and runs the default layer (D-062).
  function finishLaunch(reply, sentUids, kind) {
    const launchError = reply.structure?.launch_error;
    if (launchError) {
      adopt(reply, {reloadDraft: false});
      placeError(launchError.path, launchError.message, sentUids);
      refresh();
      requestReveal();
      return {text: `Launched with the default layer: ${launchError.message}`, tone: 'error'};
    }
    adopt(reply, {reloadDraft: true});
    return {
      text: kind === 'update'
        ? `Sent. ${program.name} restarted with your changes.`
        : `${program.name} is running on the matrix.`,
    };
  }

  async function waitForSnapshot(runId) {
    const deadline = Date.now() + SNAPSHOT_WAIT_MS;
    while (Date.now() < deadline) {
      let status = null;
      try {
        status = await api.status();
      } catch (_error) {
        status = null;
      }
      if (status) {
        if (status.state === 'failed') return {failed: status.last_error || 'it stopped'};
        if (status.visualization_id !== fft.id || String(status.run_id) !== String(runId)) return {gone: true};
        if (status.state === 'running' && status.runtime_control?.connected) {
          const reply = await fetchSnapshot();
          if (reply && String(reply.run_id) === String(runId)) return {reply};
        }
      }
      await sleep(SNAPSHOT_POLL_MS);
    }
    return {timeout: true};
  }

  // ---- Server errors (D-052, D-062) ----------------------------------------------

  function placeError(path, message, sentUids) {
    const where = fft.locateError(path);
    if (where.target === 'backend' || where.target === 'list') {
      serverError = {target: where.target, message};
      return true;
    }
    if (where.target !== 'layer') return false;
    const uid = sentUids[where.index];
    if (!rows.has(uid)) return false;
    let key = where.field;
    if (key && where.point !== null && where.point !== undefined) {
      key = `${key}[${where.point}]${where.pointField ? `.${where.pointField}` : ''}`;
    }
    serverError = {target: 'layer', uid, key, message};
    setOpen(uid);
    return true;
  }

  // Inputs are disabled while a submit is in flight, so focus waits for setBusy(false).
  function requestReveal() {
    if (busy) revealPending = true;
    else revealServerError();
  }

  function revealServerError() {
    if (serverError?.target !== 'layer') return;
    const row = rows.get(serverError.uid);
    const fieldName = String(serverError.key || '').replace(/\[.*$/, '');
    const control = fieldName === 'layer_id' ? row?.nameField : row?.fields.get(fieldName);
    if (control) {
      control.element.scrollIntoView({block: 'center'});
      control.input.focus({preventScroll: true});
    } else if (row?.points && fieldName === row.points.element.dataset.name) {
      row.points.element.scrollIntoView({block: 'center'});
      row.points.focus(serverError.key);
    } else {
      row?.element.scrollIntoView({block: 'center'});
    }
  }

  // ---- Drawing ----------------------------------------------------------------------

  function edited() {
    serverError = null;
    refresh();
  }

  function analyzerStructure() {
    const live = snapshot && lastStatus?.visualization_id === fft.id &&
      String(lastStatus.run_id) === String(snapshot.run_id) &&
      (lastStatus.state === 'running' || lastStatus.state === 'starting');
    return live && liveStructure
      ? {structure: liveStructure, assumed: []}
      : {structure: catalog?.structure, assumed: catalog?.assumed_until_launch || []};
  }

  function refreshAnalyzer() {
    for (const input of sourceInputs) {
      input.checked = input.value === source;
      input.disabled = busy;
    }
    sourceMarker.hidden = source === baseline.source;
    const {structure, assumed} = analyzerStructure();
    const facts = fft.analyzerFacts(structure, assumed);
    const rate = facts.sampleRate ? fft.formatHz(facts.sampleRate) : 'unknown';
    const rateText = facts.assumed.has('sample_rate') ? `${rate} (assumed until launch)` : rate;
    factsTable.replaceChildren(
      h('dt', {}, 'Sample rate'), h('dd', {}, rateText),
      h('dt', {}, 'FFT size'), h('dd', {}, facts.fftSize ?? '—'),
      h('dt', {}, 'Block'), h('dd', {}, facts.blockSize ? `${facts.blockSize} samples` : '—'));
    analyzerSummary.textContent = [
      fft.sourceLabel(source),
      facts.assumed.has('sample_rate') ? `${rate} assumed` : rate,
      facts.fftSize ? `FFT ${facts.fftSize}` : null,
    ].filter(Boolean).join(' · ');
  }

  function refresh() {
    const draft = readDraft();
    const changes = fft.draftChanges(baseline, draft);
    const baseByUid = new Map(baseline.layers.map(layer => [layer.uid, layer]));
    const count = draft.layers.length;
    const limit = fft.maxLayers(info);

    refreshAnalyzer();

    outputSelect.value = backend ?? '';
    outputSelect.disabled = busy;
    outputMarker.hidden = backend === baseline.backend;
    const backendError = serverError?.target === 'backend' ? serverError.message : '';
    outputError.textContent = backendError;
    outputError.hidden = !backendError;
    outputNote.textContent = lockNote;
    outputNote.hidden = !lockNote;

    const listMessage = serverError?.target === 'list' ? serverError.message : '';
    listError.textContent = listMessage;
    listError.hidden = !listMessage;
    const note = fft.costNote(draft.layers);
    costNote.textContent = note;
    costNote.hidden = !note;
    emptyNote.hidden = count > 0;
    layerCount.textContent = Number.isFinite(limit) ? `${count} of ${limit}` : `${count}`;
    addButton.disabled = busy || count >= limit || !typeSelect.value;
    typeSelect.disabled = busy;

    draft.layers.forEach((layer, index) => {
      const row = rows.get(layer.uid);
      const base = baseByUid.get(layer.uid) || null;
      const errors = {...draft.errors.get(layer.uid)};
      if (serverError?.target === 'layer' && serverError.uid === layer.uid) {
        if (serverError.key) errors[serverError.key] = serverError.message;
      }
      const rowMessage = serverError?.target === 'layer' && serverError.uid === layer.uid && !serverError.key
        ? serverError.message : '';
      const locked = fft.lockedNames(row.descriptor, backend);

      const name = String(layer.layer_id).trim();
      row.title.textContent = name || '(no name)';
      row.chevron.setAttribute('aria-label', `${openUid === layer.uid ? 'Close' : 'Open'} ${name || 'layer'}`);
      row.handle.setAttribute('aria-label', `Drag ${name || 'layer'} to reorder`);
      row.visibleInput.setAttribute('aria-label', `Show ${name || 'this layer'}`);
      row.up.setAttribute('aria-label', `Move ${name || 'layer'} up`);
      row.down.setAttribute('aria-label', `Move ${name || 'layer'} down`);
      row.remove.setAttribute('aria-label', `Remove ${name || 'layer'}`);
      row.summary.textContent = fft.layerSummary(layer);
      const rowChanges = changes.rows[layer.uid];
      row.unsent.hidden = !rowChanges;
      row.unsent.textContent = rowChanges === 'new' ? 'new' : `${rowChanges}`;
      row.unsent.title = rowChanges === 'new' ? 'Not sent yet' : `${rowChanges} unsent changes`;
      row.element.classList.toggle('has-error', Object.keys(errors).length > 0 || Boolean(rowMessage));
      row.element.classList.toggle('hidden-layer', !layer.visible);
      row.visibleInput.disabled = busy;
      row.handle.disabled = busy;
      row.up.disabled = busy || index === count - 1;
      row.down.disabled = busy || index === 0;
      row.remove.disabled = busy;
      row.layerError.textContent = rowMessage;
      row.layerError.hidden = !rowMessage;

      row.nameField.setChanged(Boolean(base) && name !== base.layer_id);
      row.nameField.setError(errors.layer_id || null);
      row.nameField.setDisabled(busy);

      const shownByName = {};
      for (const field of row.descriptor?.fields || []) {
        const fieldErrors = Object.fromEntries(Object.entries(errors).filter(([key]) =>
          key === field.name || key.startsWith(`${field.name}[`)));
        // A field with an error shows even when its condition hides it.
        const shown = fft.isShown(field, layer.settings) || Object.keys(fieldErrors).length > 0;
        shownByName[field.name] = shown;
        const changed = Boolean(base) && !sameJson(base.settings?.[field.name], layer.settings[field.name]);
        if (field.type === 'points') {
          row.points.setRange(layer.settings.min_freq, layer.settings.max_freq);
          row.points.setLocked(locked.has(field.name));
          row.points.setState({changed, errors: fieldErrors, disabled: busy});
          row.points.element.hidden = !shown;
          continue;
        }
        const control = row.fields.get(field.name);
        control.setChanged(changed);
        control.setError(fieldErrors[field.name] || null);
        control.setHidden(!shown);
        if (locked.has(field.name)) {
          control.setDisabled(true);
          control.setHelp(`Fixed by ${backend}.`);
        } else {
          control.setDisabled(busy);
          control.setHelp(null);
        }
      }
      for (const group of row.groups) {
        group.heading.hidden = !group.fields.some(fieldName => shownByName[fieldName]);
        group.heading.nextElementSibling.hidden = group.heading.hidden;
      }
    });
    onChange();
  }

  // ---- What program.js calls -----------------------------------------------------------

  return {
    element,

    async load(status) {
      lastStatus = status;
      catalog = await api.fftCatalog();
      info = catalog.structure;
      const running = status?.visualization_id === fft.id && (status.state === 'running' || status.state === 'starting');
      baseline = {
        source: running && hasOwn(status.parameters || {}, 'audio_source') ? status.parameters.audio_source : sourceSpec.default,
        backend: null,
        layers: [],
      };
      setBaselineFrom(catalog.structure);
      if (running) {
        expectedRun = String(status.run_id);
        if (status.state === 'running' && status.runtime_control?.connected) {
          const reply = await fetchSnapshot();
          if (reply && String(reply.run_id) === expectedRun) {
            snapshot = {run_id: reply.run_id, revision: reply.revision};
            liveStructure = reply.structure;
            setBaselineFrom(reply.structure);
          }
        }
      }
      loadDraft();
      loaded = true;
      onChange();
    },

    // Each /status poll. Loads the snapshot of the run this page expects once it
    // connects; other runs' layers are never followed (D-062).
    noticeStatus(status) {
      lastStatus = status;
      refreshAnalyzer();
      maybeLoadSnapshot(status);
    },

    unsentCount() {
      return fft.draftChanges(baseline, readDraft()).total;
    },

    valid() {
      return loaded && [...readDraft().errors.values()].every(errors => Object.keys(errors).length === 0);
    },

    values() {
      return {audio_source: source};
    },

    relaunchReasons(status, matrixChanged) {
      if (status?.visualization_id !== fft.id || status.state !== 'running') return [];
      return fft.relaunchReasons({status, source, matrixChanged});
    },

    setBusy(next) {
      if (busy === next) return;
      busy = next;
      refresh();
      if (!busy && revealPending) {
        revealPending = false;
        revealServerError();
      }
    },

    discard() {
      loadDraft();
    },

    // Returns {text, tone} for the page message. Throws only when /run itself
    // fails, which program.js reports as a failed launch.
    async submit(kind, status, {relaunch = false} = {}) {
      const draft = readDraft();
      const sentUids = draft.layers.map(layer => layer.uid);
      const document = fft.structureDocument(draft.backend, draft.layers);
      serverError = null;
      if (kind === 'update' && !relaunch) {
        if (!snapshot || String(snapshot.run_id) !== String(status?.run_id)) {
          refresh();
          return {text: fft.STALE_MESSAGE, tone: 'error'};
        }
        let reply;
        try {
          reply = await api.runtimeAction(snapshot.run_id, snapshot.revision, 'structure.set', document);
        } catch (error) {
          if (error.status === 409) return {text: fft.STALE_MESSAGE, tone: 'error'};
          placeError(error.body?.path, error.message, sentUids);
          refresh();
          requestReveal();
          return {text: `Nothing was sent: ${error.message}`, tone: 'error'};
        }
        adopt(reply, {reloadDraft: true});
        return {text: 'Sent. The panel shows your layers.'};
      }

      // Launch, or relaunch for a source or matrix change: layers ride along (D-016).
      const result = await api.run(program.id, {audio_source: draft.source}, document);
      baseline = {...baseline, source: result.parameters?.audio_source ?? draft.source};
      expectedRun = String(result.run_id);
      snapshot = null;
      liveStructure = null;
      lateLaunch = null;
      const waited = await waitForSnapshot(result.run_id);
      if (waited.failed) {
        refresh();
        return {text: `${program.name} stopped: ${waited.failed}`, tone: 'error'};
      }
      if (waited.gone) {
        refresh();
        return {text: `${program.name} was stopped or replaced before its layer control connected.`, tone: 'error'};
      }
      if (!waited.reply) {
        // The next status poll that sees it connected finishes the launch.
        lateLaunch = {runId: expectedRun, document: JSON.stringify(document), sentUids};
        refresh();
        return {
          text: `${program.name} is starting, but its layer control hasn't connected yet. The layers load when it does.`,
          tone: 'error',
        };
      }
      return finishLaunch(waited.reply, sentUids, kind);
    },
  };
}
