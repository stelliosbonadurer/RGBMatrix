// One program's page (D-033, D-037, D-038). The fields are a local draft; only
// Launch, Send update, and Terminate reach the controller. Polling refreshes the
// status, the stored matrix settings, and the saved program files, never the draft.
// A program's config file editor (M4b, D-044, D-048) is part of the same draft:
// a submit saves a changed file first, then launches.

import {api} from './api.js';
import {createFileEditor} from './file_editor.js';
import {
  buttonModel,
  changedNames,
  defaultsOf,
  changedSinceLaunchText,
  fileChanges,
  fileUpdateAction,
  filesChangedSinceLaunch,
  flagVector,
  matrixChangedSinceLaunch,
  runKey,
  runningValues,
} from './model.js';
import * as graphTraversal from './programs/graph_traversal.js';
import {
  createField,
  guardLeaving,
  plural,
  poll,
  renderFlags,
  renderValueTable,
  setTone,
  showBanner,
  showMessage,
} from './ui.js';

// Per-program rules (D-042). M5 adds the FFT module here.
const EXTENSIONS = {[graphTraversal.id]: graphTraversal};

const $ = id => document.getElementById(id);
const el = {
  message: $('message'),
  name: $('program-name'),
  description: $('program-description'),
  statusDot: $('status-dot'),
  statusText: $('status-text'),
  top: $('top-action'),
  changedBanner: $('changed-banner'),
  options: $('options'),
  noOptions: $('no-options'),
  resetDefaults: $('reset-defaults'),
  files: $('program-files'),
  matrixNote: $('matrix-note'),
  matrixTable: $('matrix-table'),
  flags: $('program-flags'),
  showMatrixFlags: $('show-matrix-flags'),
  dirty: $('dirty-count'),
  discard: $('discard'),
  bottom: $('bottom-action'),
};

const programId = decodeURIComponent(location.pathname.slice('/programs/'.length));
const extension = EXTENSIONS[programId] || null;

let programs = [];
let program = null;
let parameters = []; // display order (a program module may reorder them)
let argvOrder = []; // the server's order, which the launch argv follows
let status = null;
let matrix = null;
let baseline = {};
let knownRunKey = null;
let pending = null; // 'launch' | 'update' | 'stop'
let submits = 0; // counts submits, so a poll that overlapped one is dropped
let fileBusy = false; // a program file removal is in flight
let polling = false;
let previousMst = false;
let ruleErrors = {};
const fields = new Map();
// File name -> {snapshot (the saved document, as last loaded), editor, text}.
const files = new Map();

const nameOf = id => programs.find(item => item.id === id)?.name || id;

function readDraft() {
  const values = {};
  const parseErrors = {};
  for (const [name, field] of fields) {
    const {value, error} = field.read();
    values[name] = value;
    if (error) parseErrors[name] = error;
  }
  return {values, parseErrors};
}

function fileChangesOf(entry) {
  return fileChanges(entry.snapshot.fields, entry.editor.read().document, entry.snapshot.document);
}

function unsentCount() {
  let count = changedNames(parameters, readDraft().values, baseline).length;
  for (const entry of files.values()) count += fileChangesOf(entry).length;
  return count;
}

// Runs the program's own rules, writes back any value they adjust, and shows
// their hidden fields, disabled choices, and messages.
function applyRules() {
  ruleErrors = {};
  if (!extension) return;
  const {values, parseErrors} = readDraft();
  const result = extension.applyRules(values, previousMst);
  previousMst = result.mst;
  for (const [name, field] of fields) {
    if (result.values[name] !== values[name] && !parseErrors[name]) field.write(result.values[name]);
    field.setHidden(result.hidden.has(name));
    field.setDisabledChoices(result.disabledChoices[name] || []);
    field.setHelp(result.help[name] || null);
  }
  ruleErrors = result.errors;
}

// The MST flag for a set of values, without the rules' one-time adjustments.
function mstOf(values) {
  return extension ? extension.applyRules(values, true).mst : false;
}

function render() {
  if (!program) return;
  const {values, parseErrors} = readDraft();
  const errors = {...ruleErrors, ...parseErrors};
  const changed = new Set(changedNames(parameters, values, baseline));
  for (const [name, field] of fields) {
    field.setChanged(changed.has(name));
    field.setError(errors[name] || null);
    // The reply reloads the draft (D-038), so edits can't be typed meanwhile.
    field.setDisabled(Boolean(pending));
  }
  // The outside-relaunch banner only matters while there are edits to keep.
  if (changed.size === 0 && !el.changedBanner.hidden) showBanner(el.changedBanner, '');
  let fileChangeCount = 0;
  let filesValid = true;
  const savedRevisions = {};
  const changedFileNouns = [];
  for (const [name, entry] of files) {
    const {document, errors: fileErrors} = entry.editor.read();
    const changedSlots = new Set(fileChanges(entry.snapshot.fields, document, entry.snapshot.document));
    fileChangeCount += changedSlots.size;
    if (Object.keys(fileErrors).length) filesValid = false;
    entry.editor.setState(changedSlots, fileErrors);
    entry.editor.setDisabled({editing: Boolean(entry.snapshot.error), busy: Boolean(pending) || fileBusy});
    entry.editor.setUnusable(entry.snapshot.error);
    if (changedSlots.size === 0 && !entry.editor.banner.hidden) showBanner(entry.editor.banner, '');
    savedRevisions[name] = entry.snapshot.revision;
    if (filesChangedSinceLaunch(status, programId, {[name]: entry.snapshot.revision})) {
      changedFileNouns.push(entry.text.noun || entry.snapshot.label.toLowerCase());
    }
  }
  const unsent = changed.size + fileChangeCount;
  const invalid = new Set(Object.keys(errors));
  // A removal in flight holds the launch buttons too.
  const valid = invalid.size === 0 && filesValid && !fileBusy;
  const stored = matrix && !matrix.error ? matrix.values : null;
  const matrixChanged = matrixChangedSinceLaunch(status, matrix?.values);
  const filesChanged = filesChangedSinceLaunch(status, programId, savedRevisions);
  const activeId = status?.visualization_id;
  const model = buttonModel({
    status,
    programId,
    programName: program.name,
    otherName: activeId && activeId !== programId ? nameOf(activeId) : null,
    dirty: unsent > 0,
    matrixChanged,
    filesChanged,
    valid,
    pending,
  });

  setTone(el.statusDot, model.tone);
  el.statusText.textContent = model.statusText;
  el.top.textContent = model.top.label;
  el.top.disabled = model.top.disabled;
  el.top.dataset.action = model.top.action || '';
  el.top.classList.toggle('danger', model.top.action === 'stop');
  el.top.classList.toggle('primary', model.top.action !== 'stop');
  el.bottom.textContent = model.bottom.label;
  el.bottom.disabled = model.bottom.disabled;
  el.bottom.dataset.action = model.bottom.action || '';

  if (unsent > 0) el.dirty.textContent = plural(unsent, 'unsent change');
  else if (model.running) el.dirty.textContent = changedSinceLaunchText(matrixChanged, changedFileNouns);
  else el.dirty.textContent = '';
  el.discard.hidden = unsent === 0;

  let argv = flagVector(argvOrder, values, invalid);
  if (el.showMatrixFlags.checked && matrix) argv = [...flagVector(matrix.parameters, matrix.values), ...argv];
  renderFlags(el.flags, argv, '(no options of its own)');

  if (matrix) renderValueTable(el.matrixTable, matrix.parameters, matrix.values);
  let note = '';
  if (matrix?.error) note = "The saved matrix settings can't be used, so launches use the Pi config values.";
  else if (model.running && matrixChanged && stored) {
    note = 'This run started with different matrix settings. Send update relaunches with the saved ones.';
  }
  el.matrixNote.textContent = note;
  el.matrixNote.hidden = !note;
}

function writeDraft(values) {
  for (const [name, field] of fields) field.write(values[name]);
  previousMst = mstOf(values);
  applyRules();
  render();
}

function buildFields() {
  el.options.replaceChildren();
  fields.clear();
  const onInput = () => {
    applyRules();
    render();
  };
  for (const parameter of parameters) {
    const field = createField(parameter, baseline[parameter.name], {onInput});
    fields.set(parameter.name, field);
    el.options.append(field.element);
  }
  el.noOptions.hidden = parameters.length > 0;
  el.resetDefaults.hidden = parameters.length === 0;
  previousMst = mstOf(baseline);
  applyRules();
}

// Notices a relaunch from another device (D-038): a clean draft follows the new
// running values; a dirty one is kept and a banner offers to load them. A stop
// leaves the draft and its baseline as they are.
function noticeOutsideChange() {
  const key = runKey(status, programId);
  if (key === knownRunKey) return;
  knownRunKey = key;
  if (key === null) {
    showBanner(el.changedBanner, '');
    return;
  }
  const running = runningValues(status, programId, parameters);
  // Only option edits are kept here; the files follow their own rule.
  const dirty = changedNames(parameters, readDraft().values, baseline).length > 0;
  baseline = running;
  if (dirty) {
    showBanner(el.changedBanner, `${program.name} was relaunched on another device with other settings. Your edits are kept.`, [
      {label: 'Load running settings', onClick: () => {
        showBanner(el.changedBanner, '');
        writeDraft(baseline);
      }},
    ]);
  } else {
    writeDraft(running);
  }
}

// D-048: Discard returns options and files to their loaded values.
function discardAll() {
  for (const entry of files.values()) {
    entry.editor.write(entry.snapshot.document);
    showBanner(entry.editor.banner, '');
  }
  writeDraft(baseline);
}

function loadSavedFile(entry) {
  showBanner(entry.editor.banner, '');
  entry.editor.write(entry.snapshot.document);
  render();
}

async function reloadSavedFile(name) {
  const entry = files.get(name);
  try {
    entry.snapshot = await api.programFile(programId, name);
  } catch (error) {
    showMessage(el.message, `Could not load the saved ${entry.snapshot.label.toLowerCase()}: ${error.message}`, 'error');
    return;
  }
  showMessage(el.message, '');
  loadSavedFile(entry);
}

// Applies a polled program file (criterion 8; the rule is fileUpdateAction).
function noticeFileChange(entry, next) {
  const action = fileUpdateAction(entry.snapshot, next, fileChangesOf(entry).length > 0);
  if (action === 'none') return;
  entry.snapshot = next;
  if (action === 'follow') {
    loadSavedFile(entry);
    return;
  }
  if (action === 'reset') {
    // The saved file can no longer be used, so these edits can't be saved
    // until it is removed, which resets the section anyway (review finding 1).
    loadSavedFile(entry);
    const noun = entry.text.noun || next.label.toLowerCase();
    showMessage(el.message,
      `The saved ${noun} on the Pi can't be used, so your unsent edits to the ${noun} were set aside. Launches use the default.`,
      'error');
    return;
  }
  showBanner(entry.editor.banner,
    entry.text.outsideChange || `${next.label} was changed on another device. Your edits are kept.`,
    [{label: entry.text.load || 'Load the saved version', onClick: () => loadSavedFile(entry)}]);
}

// Saves each changed file before a launch; nothing is sent for an unchanged one.
// Returns the labels saved, or null when a save failed (nothing is launched).
async function saveChangedFiles() {
  const saved = [];
  for (const [name, entry] of files) {
    if (fileChangesOf(entry).length === 0) continue;
    const {document} = entry.editor.read();
    try {
      entry.snapshot = await api.saveProgramFile(programId, name, entry.snapshot.revision, document);
      showBanner(entry.editor.banner, '');
      saved.push(entry.snapshot.label);
    } catch (error) {
      const label = entry.snapshot.label.toLowerCase();
      if (error.status === 409) {
        showBanner(entry.editor.banner,
          `${entry.snapshot.label} changed on another device after this page loaded it.`,
          [{label: entry.text.load || 'Load the saved version', onClick: () => reloadSavedFile(name)}]);
        showMessage(el.message, `Nothing was sent: the saved ${label} changed on another device.`, 'error');
      } else {
        showMessage(el.message, `Could not save ${label}: ${error.message}`, 'error');
      }
      return null;
    }
  }
  return saved;
}

async function removeUnusableFile(name) {
  if (fileBusy || pending) return;
  const entry = files.get(name);
  // Locks Remove and the launch buttons, and drops any poll that overlaps the
  // request, so a stale "unusable" reply can't come back (review finding 2).
  fileBusy = true;
  submits += 1;
  render();
  try {
    entry.snapshot = await api.removeProgramFile(programId, name);
    loadSavedFile(entry);
    showMessage(el.message, `The unusable saved file was removed. Launches use the default ${entry.snapshot.label.toLowerCase()}.`);
  } catch (error) {
    showMessage(el.message, `Could not remove the saved file: ${error.message}`, 'error');
  } finally {
    fileBusy = false;
    submits += 1;
    render();
  }
}

function filesHaveErrors() {
  return [...files.values()].some(entry => Object.keys(entry.editor.read().errors).length > 0);
}

async function submit(kind) {
  const {values, parseErrors} = readDraft();
  if (fileBusy || Object.keys(parseErrors).length || Object.keys(ruleErrors).length || filesHaveErrors()) return;
  submits += 1;
  pending = kind;
  render();
  let saved = [];
  try {
    saved = await saveChangedFiles();
    if (saved === null) return;
    const result = await api.run(programId, values);
    knownRunKey = `run:${result.run_id}`;
    baseline = {...defaultsOf(parameters), ...result.parameters};
    showBanner(el.changedBanner, '');
    writeDraft(baseline);
    showMessage(el.message, kind === 'update'
      ? `Sent. ${program.name} restarted with your changes.`
      : `${program.name} is running on the matrix.`);
  } catch (error) {
    // D-044: a file saved before a rejected launch stays saved.
    const kept = saved.length ? ` ${saved.join(', ')} saved for the next launch.` : '';
    showMessage(el.message, `Could not ${kind === 'update' ? 'send the update' : 'launch'}: ${error.message}${kept}`, 'error');
  } finally {
    pending = null;
    await refreshStatus();
  }
}

async function terminate() {
  submits += 1;
  pending = 'stop';
  render();
  try {
    status = await api.stop();
    knownRunKey = runKey(status, programId);
    showMessage(el.message, `${program.name} was stopped. The matrix is free.`);
  } catch (error) {
    showMessage(el.message, `Could not terminate: ${error.message}`, 'error');
  } finally {
    pending = null;
    render();
  }
}

async function refreshStatus() {
  try {
    status = await api.status();
  } catch (_error) {
    status = null;
  }
  render();
}

async function refresh() {
  if (pending || fileBusy) return;
  const started = submits;
  let next;
  try {
    next = await api.status();
  } catch (_error) {
    next = null;
  }
  // A submit that began while this poll waited (the supervisor holds its lock
  // through a launch) owns the result; its own reply updates the page.
  if (pending || fileBusy || started !== submits) return;
  status = next;
  if (status) noticeOutsideChange();
  try {
    matrix = await api.matrixSettings();
  } catch (_error) {
    // Keep the last matrix snapshot; the status line shows when the controller is gone.
  }
  for (const [name, entry] of files) {
    let next;
    try {
      next = await api.programFile(programId, name);
    } catch (_error) {
      continue;
    }
    if (pending || fileBusy || started !== submits) return;
    noticeFileChange(entry, next);
  }
  render();
}

async function loadFiles() {
  for (const file of program.files || []) {
    let snapshot;
    try {
      snapshot = await api.programFile(programId, file.name);
    } catch (error) {
      showMessage(el.message, `Could not load ${file.label.toLowerCase()}: ${error.message}`, 'error');
      continue;
    }
    const text = extension?.fileText?.[file.name] || {};
    const entry = {snapshot, text, editor: null};
    entry.editor = createFileEditor(snapshot, {
      text,
      onInput: render,
      // D-048: sets the draft to the tracked default; Launch saves it.
      onRestore: () => {
        entry.editor.write(entry.snapshot.default);
        render();
      },
      onRemove: () => removeUnusableFile(file.name),
    });
    entry.editor.write(snapshot.document);
    files.set(file.name, entry);
    el.files.append(entry.editor.element);
  }
}

function act(action) {
  if (action === 'launch' || action === 'update') submit(action);
  else if (action === 'stop') terminate();
}

el.top.addEventListener('click', () => act(el.top.dataset.action));
el.bottom.addEventListener('click', () => act(el.bottom.dataset.action));
el.discard.addEventListener('click', discardAll);
// D-048: the page-wide reset covers the options only.
el.resetDefaults.addEventListener('click', () => writeDraft(defaultsOf(parameters)));
el.showMatrixFlags.addEventListener('change', render);
el.options.addEventListener('submit', event => event.preventDefault());
guardLeaving(unsentCount);

async function initialize() {
  try {
    const [catalog, current] = await Promise.all([api.visualizations(), api.status()]);
    programs = Array.isArray(catalog.visualizations) ? catalog.visualizations : [];
    status = current;
  } catch (error) {
    showMessage(el.message, `Could not load this program: ${error.message} Retrying…`, 'error');
    setTimeout(initialize, 3000);
    return;
  }
  showMessage(el.message, '');
  program = programs.find(item => item.id === programId) || null;
  if (!program) {
    el.name.textContent = 'Unknown program';
    showMessage(el.message, 'This program is not installed on the controller.', 'error');
    return;
  }
  document.title = `${program.name} · RGB Matrix`;
  el.name.textContent = program.name;
  el.description.textContent = program.description;
  argvOrder = program.parameters || [];
  parameters = extension?.orderParameters ? extension.orderParameters(argvOrder) : argvOrder;
  baseline = runningValues(status, programId, parameters) || defaultsOf(parameters);
  knownRunKey = runKey(status, programId);
  buildFields();
  await loadFiles();
  try {
    matrix = await api.matrixSettings();
  } catch (error) {
    showMessage(el.message, `Could not load the matrix settings: ${error.message}`, 'error');
  }
  render();
  if (!polling) {
    polling = true;
    poll(1500, refresh);
  }
}

initialize();
