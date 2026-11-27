// One program's page (D-033, D-037, D-038). The fields are a local draft; only
// Launch, Send update, and Terminate reach the controller. Polling refreshes the
// status, the stored matrix settings, and the saved program files, never the draft.
// A program's config file editor (M4b, D-044, D-048) is part of the same draft:
// a submit saves a changed file first, then launches. A program module with
// `createEditor` (Music FFT, M5) owns its own draft and submit instead of the
// options form; this file keeps the shell around it. A module's `fileEditors`
// replace the generic editor for a file; one with `selfSaving` has its own Save
// and Discard and is never saved by a submit (fft-themes D-014, D-016).

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
  hasFileEditor,
  matrixChangedSinceLaunch,
  runKey,
  runningValues,
} from './model.js';
import * as fftEditor from './fft_editor.js';
import * as graphTraversal from './programs/graph_traversal.js';
import {relaunchText} from './programs/fft.js';
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

// Per-program rules (D-042) and the FFT page's editor (M5).
const EXTENSIONS = {[graphTraversal.id]: graphTraversal, [fftEditor.id]: fftEditor};

const $ = id => document.getElementById(id);
const el = {
  message: $('message'),
  name: $('program-name'),
  description: $('program-description'),
  statusDot: $('status-dot'),
  statusText: $('status-text'),
  top: $('top-action'),
  changedBanner: $('changed-banner'),
  editor: $('program-editor'),
  optionsPanel: $('options-panel'),
  options: $('options'),
  noOptions: $('no-options'),
  resetDefaults: $('reset-defaults'),
  files: $('program-files'),
  matrixNote: $('matrix-note'),
  matrixTable: $('matrix-table'),
  flags: $('program-flags'),
  showMatrixFlags: $('show-matrix-flags'),
  dirty: $('dirty-count'),
  reason: $('action-reason'),
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
let fileBusy = false; // a program file removal or own-section save is in flight
let polling = false;
let previousMst = false;
let ruleErrors = {};
let editor = null; // a program module's own draft (Music FFT)
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

// Unsaved edits in a file's section: slots for the generic editor, the
// editor's own count for a self-saving one.
function fileDirtyCount(entry) {
  return entry.editor.selfSaving ? entry.editor.changeCount() : fileChangesOf(entry).length;
}

// Everything leaving the page would lose, including self-saving sections.
function unsentCount() {
  let count = editor ? editor.unsentCount() : changedNames(parameters, readDraft().values, baseline).length;
  for (const entry of files.values()) count += fileDirtyCount(entry);
  return count;
}

// Every snapshot change goes through here so the program's editor follows the
// saved file (the FFT Theme choices, D-013). Returns its message, or ''.
function setSnapshot(name, entry, snapshot) {
  entry.snapshot = snapshot;
  return editor?.noticeFile?.(name, snapshot) || '';
}

// Runs the program's own rules, writes back any value they adjust, and shows
// their hidden fields, disabled choices, and messages.
function applyRules() {
  ruleErrors = {};
  if (!extension?.applyRules) return;
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
  return extension?.applyRules ? extension.applyRules(values, true).mst : false;
}

function render() {
  if (!program) return;
  // The reply reloads the draft (D-038, D-062), so edits can't be typed meanwhile.
  if (editor) editor.setBusy(Boolean(pending));
  const {values: fieldValues, parseErrors} = readDraft();
  const values = editor ? editor.values() : fieldValues;
  const errors = {...ruleErrors, ...parseErrors};
  const changed = new Set(changedNames(parameters, fieldValues, baseline));
  for (const [name, field] of fields) {
    field.setChanged(changed.has(name));
    field.setError(errors[name] || null);
    field.setDisabled(Boolean(pending));
  }
  // The outside-relaunch banner only matters while there are edits to keep.
  if (changed.size === 0 && !el.changedBanner.hidden) showBanner(el.changedBanner, '');
  let fileChangeCount = 0;
  let filesValid = true;
  const savedRevisions = {};
  const changedFileNouns = [];
  for (const [name, entry] of files) {
    if (entry.editor.selfSaving) {
      // Not part of the submit's draft: its edits and errors never hold Launch.
      entry.editor.setSaved(entry.snapshot.document);
      entry.editor.setDisabled({editing: Boolean(entry.snapshot.error), busy: Boolean(pending) || fileBusy});
      entry.editor.setUnusable(entry.snapshot.error);
      if (entry.editor.changeCount() === 0 && !entry.editor.banner.hidden) showBanner(entry.editor.banner, '');
      savedRevisions[name] = entry.snapshot.revision;
      if (filesChangedSinceLaunch(status, programId, {[name]: entry.snapshot.revision})) {
        changedFileNouns.push(entry.text.noun || entry.snapshot.label.toLowerCase());
      }
      continue;
    }
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
  const unsent = (editor ? editor.unsentCount() : changed.size) + fileChangeCount;
  const invalid = new Set(Object.keys(errors));
  // A removal in flight holds the launch buttons too.
  const valid = invalid.size === 0 && filesValid && !fileBusy && (!editor || editor.valid());
  const stored = matrix && !matrix.error ? matrix.values : null;
  const matrixChanged = matrixChangedSinceLaunch(status, matrix?.values);
  const filesChanged = filesChangedSinceLaunch(status, programId, savedRevisions);
  const reasons = editor ? editor.relaunchReasons(status, matrixChanged) : [];
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
    relaunch: reasons.length > 0,
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
  const reason = model.running ? relaunchText(program.name, reasons) : '';
  el.reason.textContent = reason;
  el.reason.hidden = !reason;

  let argv = flagVector(argvOrder, values, invalid);
  if (el.showMatrixFlags.checked && matrix) argv = [...flagVector(matrix.parameters, matrix.values), ...argv];
  renderFlags(el.flags, argv, '(no options of its own)');

  if (matrix) renderValueTable(el.matrixTable, matrix.parameters, matrix.values);
  let note = '';
  if (matrix?.error) note = "The saved matrix settings can't be used, so launches use the Pi config values.";
  else if (model.running && matrixChanged && stored) {
    note = editor
      ? 'This run started with different matrix settings. Relaunch with changes uses the saved ones.'
      : 'This run started with different matrix settings. Send update relaunches with the saved ones.';
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
    if (entry.editor.selfSaving) continue; // its own Discard (D-014)
    entry.editor.write(entry.snapshot.document);
    showBanner(entry.editor.banner, '');
  }
  if (editor) {
    editor.discard();
    render();
  } else {
    writeDraft(baseline);
  }
}

function loadSavedFile(entry) {
  showBanner(entry.editor.banner, '');
  entry.editor.write(entry.snapshot.document);
  render();
}

async function reloadSavedFile(name) {
  const entry = files.get(name);
  let note = '';
  try {
    note = setSnapshot(name, entry, await api.programFile(programId, name));
  } catch (error) {
    showMessage(el.message, `Could not load the saved ${entry.snapshot.label.toLowerCase()}: ${error.message}`, 'error');
    return;
  }
  showMessage(el.message, note, note ? 'error' : 'info');
  loadSavedFile(entry);
}

// Applies a polled program file (criterion 8; the rule is fileUpdateAction).
function noticeFileChange(name, entry, next) {
  const action = fileUpdateAction(entry.snapshot, next, fileDirtyCount(entry) > 0);
  if (action === 'none') return;
  const note = setSnapshot(name, entry, next);
  if (note) showMessage(el.message, note, 'error');
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
      `The saved ${noun} on the Pi can't be used, so your unsent edits to the ${noun} were set aside. Launches use the default.` +
      (note ? ` ${note}` : ''),
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
    if (entry.editor.selfSaving || fileChangesOf(entry).length === 0) continue;
    const {document} = entry.editor.read();
    try {
      setSnapshot(name, entry, await api.saveProgramFile(programId, name, entry.snapshot.revision, document));
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
    setSnapshot(name, entry, await api.removeProgramFile(programId, name));
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

// A self-saving section's Save (D-014): one PUT at the loaded revision. It holds
// the launch buttons and drops overlapping polls, like a removal.
async function saveOwnFile(name) {
  if (fileBusy || pending) return;
  const entry = files.get(name);
  const {document, errors} = entry.editor.read();
  if (Object.keys(errors).length || entry.editor.changeCount() === 0) return;
  fileBusy = true;
  submits += 1;
  render();
  try {
    const note = setSnapshot(name, entry, await api.saveProgramFile(programId, name, entry.snapshot.revision, document));
    loadSavedFile(entry);
    const saved = entry.text.saved || `${entry.snapshot.label} saved.`;
    showMessage(el.message, note ? `${saved} ${note}` : saved, note ? 'error' : 'info');
  } catch (error) {
    const label = entry.snapshot.label.toLowerCase();
    if (error.status === 409) {
      showBanner(entry.editor.banner,
        `${entry.snapshot.label} changed on another device after this page loaded it. Your edits are kept.`,
        [{label: entry.text.load || 'Load the saved version', onClick: () => reloadSavedFile(name)}]);
      showMessage(el.message, `Nothing was saved: the ${label} changed on another device.`, 'error');
    } else {
      showMessage(el.message, `Could not save the ${label}: ${error.message}`, 'error');
    }
  } finally {
    fileBusy = false;
    submits += 1;
    render();
  }
}

function filesHaveErrors() {
  return [...files.values()].some(entry => Object.keys(entry.editor.read().errors).length > 0);
}

// The program module's editor submits its own draft (Music FFT, D-062).
async function submitEditor(kind) {
  if (fileBusy || !editor.valid()) return;
  const relaunch = editor.relaunchReasons(status, matrixChangedSinceLaunch(status, matrix?.values)).length > 0;
  submits += 1;
  pending = kind;
  render();
  try {
    const result = await editor.submit(kind, status, {relaunch});
    showMessage(el.message, result.text, result.tone || 'info');
    // The message sits at the top, far from the bottom bar on a long FFT page.
    if (result.tone === 'error' && !result.placed) el.message.scrollIntoView({block: 'nearest'});
  } catch (error) {
    showMessage(el.message, `Could not ${kind === 'update' ? 'send the update' : 'launch'}: ${error.message}`, 'error');
    el.message.scrollIntoView({block: 'nearest'});
  } finally {
    pending = null;
    await refreshStatus();
  }
}

async function submit(kind) {
  if (editor) {
    await submitEditor(kind);
    return;
  }
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
    if (editor) editor.noticeStatus(status);
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
  if (status && editor) editor.noticeStatus(status);
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
  // D-062: the FFT layers never follow another device; the editor only loads
  // the snapshot of the run it expects.
  if (status && editor) editor.noticeStatus(status);
  else if (status) noticeOutsideChange();
  try {
    matrix = await api.matrixSettings();
  } catch (_error) {
    // Keep the last matrix snapshot; the status line shows when the controller is gone.
  }
  noticeMatrix();
  for (const [name, entry] of files) {
    let next;
    try {
      next = await api.programFile(programId, name);
    } catch (_error) {
      continue;
    }
    if (pending || fileBusy || started !== submits) return;
    noticeFileChange(name, entry, next);
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
    const custom = extension?.fileEditors?.[file.name] || null;
    const entry = {snapshot, text, editor: null};
    entry.editor = (custom || createFileEditor)(snapshot, {
      text,
      onInput: render,
      // D-048: sets the draft to the tracked default; Launch saves it.
      onRestore: () => {
        entry.editor.write(entry.snapshot.default);
        render();
      },
      onRemove: () => removeUnusableFile(file.name),
      onSave: () => saveOwnFile(file.name),
      onDiscard: () => loadSavedFile(entry),
    });
    entry.editor.write(snapshot.document);
    files.set(file.name, entry);
    const note = setSnapshot(file.name, entry, snapshot);
    if (note) showMessage(el.message, note, 'error');
    // D-011, amended by fft-themes D-016: a file with no fields still has an
    // editor, so the draft code needs no special case; a program's own editor
    // is always drawn, so an unusable saved file shows its note and Remove.
    if (custom || hasFileEditor(snapshot.fields)) el.files.append(entry.editor.element);
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

// The FFT editor sizes its graph from the stored panel width before launch.
// Unusable stored settings mean launches use the Pi config, which the page can't see.
function noticeMatrix() {
  editor?.noticeMatrix?.(matrix && !matrix.error ? matrix.values : null);
}

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
  if (extension?.createEditor) {
    editor = extension.createEditor({
      program,
      parameters,
      onChange: render,
      onMessage: (text, tone = 'info') => showMessage(el.message, text, tone),
    });
    el.optionsPanel.hidden = true;
    el.editor.append(editor.element);
    // The files first, so the saved themes are Theme choices before the
    // baseline and the presets are reconciled (fft-themes D-013).
    await loadFiles();
    try {
      await editor.load(status);
    } catch (error) {
      showMessage(el.message, `Could not load the layer settings: ${error.message} Reload the page to try again.`, 'error');
    }
  } else {
    buildFields();
    await loadFiles();
  }
  try {
    matrix = await api.matrixSettings();
  } catch (error) {
    showMessage(el.message, `Could not load the matrix settings: ${error.message}`, 'error');
  }
  noticeMatrix();
  render();
  if (!polling) {
    polling = true;
    poll(1500, refresh);
  }
}

initialize();
