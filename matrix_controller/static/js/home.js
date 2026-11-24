// Home page (D-034, D-035): what's playing, the program list, and the shared
// matrix settings. Polling refreshes status and the stored settings, never the
// matrix draft (D-002).

import {api} from './api.js';
import {
  changedNames,
  flagVector,
  matrixChangedSinceLaunch,
  matrixGroups,
  nowPlaying,
  rowBadge,
} from './model.js';
import {
  createField,
  guardLeaving,
  h,
  plural,
  poll,
  renderFlags,
  setTone,
  showBanner,
  showMessage,
  stamp,
} from './ui.js';

const $ = id => document.getElementById(id);
const el = {
  message: $('message'),
  nowDot: $('now-dot'),
  nowName: $('now-name'),
  nowDetail: $('now-detail'),
  nowOpen: $('now-open'),
  nowStop: $('now-stop'),
  nowMatrixNote: $('now-matrix-note'),
  list: $('program-list'),
  matrixPanel: $('matrix-settings'),
  matrixBanner: $('matrix-banner'),
  matrixError: $('matrix-error'),
  matrixCommon: $('matrix-common'),
  matrixAdvancedFields: $('matrix-advanced-fields'),
  matrixAdvancedCount: $('matrix-advanced-count'),
  matrixFlags: $('matrix-flags'),
  matrixReset: $('matrix-reset'),
  matrixDirty: $('matrix-dirty'),
  matrixDiscard: $('matrix-discard'),
  matrixSave: $('matrix-save'),
  matrixMeta: $('matrix-meta'),
  lastUpdated: $('last-updated'),
};

let programs = [];
let status = null;
let matrix = null; // the last snapshot from GET /api/v1/matrix-settings
let baseline = null; // the stored values the draft is compared with
const fields = new Map();
const rows = new Map();
let stopping = false;
let saving = false;
let announcedRevision = null; // the outside revision the banner already reported
let saves = 0; // counts saves, so a matrix poll that overlapped one is dropped

const nameOf = id => programs.find(program => program.id === id)?.name || id;

function readDraft() {
  const values = {};
  const invalid = new Set();
  for (const [name, field] of fields) {
    const {value, error} = field.read();
    values[name] = value;
    field.setError(error);
    if (error) invalid.add(name);
  }
  return {values, invalid};
}

function unsentCount() {
  if (!matrix || !baseline) return 0;
  return changedNames(matrix.parameters, readDraft().values, baseline).length;
}

function renderNowPlaying() {
  const now = nowPlaying(status, nameOf);
  setTone(el.nowDot, now.tone);
  el.nowName.textContent = now.name;
  el.nowDetail.textContent = now.detail;
  const openId = now.activeId || now.failedId;
  el.nowOpen.hidden = !openId;
  if (openId) el.nowOpen.href = `/programs/${encodeURIComponent(openId)}`;
  el.nowStop.disabled = stopping || !now.canStop;
  el.nowStop.textContent = stopping ? 'Stopping…' : '■ Stop';
  const changed = matrix && !matrix.error && matrixChangedSinceLaunch(status, matrix.values);
  el.nowMatrixNote.hidden = !changed;
  if (changed) {
    el.nowMatrixNote.textContent =
      `${now.name} started with different matrix settings. Relaunch it from its page to apply the saved ones.`;
  }
}

function buildProgramRows() {
  el.list.replaceChildren();
  rows.clear();
  for (const program of programs) {
    const badge = h('span', {className: 'badge', hidden: true});
    const row = h('a', {className: 'program-row', href: `/programs/${encodeURIComponent(program.id)}`},
      h('span', {className: 'row-text'},
        h('strong', {}, program.name),
        h('span', {className: 'row-description'}, program.description)),
      badge,
      h('span', {className: 'chevron', 'aria-hidden': 'true'}, '›'));
    rows.set(program.id, {row, badge});
    el.list.append(row);
  }
  el.list.setAttribute('aria-busy', 'false');
}

function renderBadges() {
  for (const [id, {row, badge}] of rows) {
    const next = rowBadge(status, id);
    badge.hidden = !next;
    row.classList.toggle('active', Boolean(next && next.tone !== 'failed'));
    if (next) {
      badge.textContent = next.text;
      setTone(badge, next.tone);
    }
  }
}

function buildMatrixFields() {
  const {common, advanced} = matrixGroups(matrix.parameters, matrix.common || []);
  const onInput = () => renderMatrix();
  for (const [container, group] of [[el.matrixCommon, common], [el.matrixAdvancedFields, advanced]]) {
    for (const parameter of group) {
      const field = createField(parameter, matrix.values[parameter.name], {onInput});
      fields.set(parameter.name, field);
      container.append(field.element);
    }
  }
  el.matrixAdvancedCount.textContent = String(advanced.length);
}

function writeMatrixDraft(values) {
  for (const [name, field] of fields) field.write(values[name]);
  renderMatrix();
}

function renderMatrix() {
  if (!matrix) return;
  const {values, invalid} = readDraft();
  const changed = new Set(changedNames(matrix.parameters, values, baseline));
  for (const [name, field] of fields) {
    field.setChanged(changed.has(name));
    field.setDisabled(saving);
  }
  renderFlags(el.matrixFlags, flagVector(matrix.parameters, values, invalid));
  const count = changed.size;
  el.matrixDirty.textContent = count ? plural(count, 'unsent change') : '';
  el.matrixDiscard.hidden = count === 0;
  const blocked = Boolean(matrix.error);
  el.matrixSave.disabled = saving || blocked || count === 0 || invalid.size > 0;
  el.matrixSave.textContent = saving ? 'Saving…' : 'Save';
  el.matrixReset.disabled = saving || blocked;
  el.matrixMeta.textContent = matrix.source === 'stored'
    ? `Saved on the Pi · revision ${matrix.revision}. Each program uses these at its next launch.`
    : 'Nothing saved yet: launches use the Pi config values.';
  showBanner(
    el.matrixError,
    blocked
      ? `The saved matrix settings can't be used (${matrix.error}). Launches use the Pi config values until the file is fixed or removed on the Pi.`
      : '',
  );
  renderNowPlaying();
}

function adoptMatrix(snapshot) {
  matrix = snapshot;
  baseline = {...snapshot.values};
  announcedRevision = null;
}

async function loadMatrix({initial = false} = {}) {
  const started = saves;
  const snapshot = await api.matrixSettings();
  if (initial) {
    adoptMatrix(snapshot);
    buildMatrixFields();
    showBanner(el.matrixError, '');
    renderMatrix();
    return;
  }
  // A save that began while this read waited owns the result.
  if (saving || started !== saves) return;
  const moved = snapshot.revision !== matrix.revision || snapshot.error !== matrix.error;
  if (!moved) return;
  if (unsentCount() > 0) {
    // Keep the draft and its old revision, so a Save is refused (409) rather
    // than silently replacing the other device's change.
    if (announcedRevision === snapshot.revision) return;
    announcedRevision = snapshot.revision;
    showBanner(el.matrixBanner, 'The matrix settings were changed on another device. Your edits are kept.', [
      {label: 'Load saved settings', onClick: () => {
        adoptMatrix(snapshot);
        showBanner(el.matrixBanner, '');
        writeMatrixDraft(snapshot.values);
      }},
    ]);
  } else {
    adoptMatrix(snapshot);
    writeMatrixDraft(snapshot.values);
  }
}

async function saveMatrix() {
  const {values, invalid} = readDraft();
  if (invalid.size) return;
  saves += 1;
  saving = true;
  renderMatrix();
  try {
    const snapshot = await api.saveMatrixSettings(matrix.revision, values);
    adoptMatrix(snapshot);
    showBanner(el.matrixBanner, '');
    writeMatrixDraft(snapshot.values);
    showMessage(el.message, 'Matrix settings saved. Each program uses them at its next launch.');
  } catch (error) {
    if (error.code === 'revision_conflict') {
      showBanner(el.matrixBanner, 'The matrix settings changed on another device since you loaded them. Your edits are kept.', [
        {label: 'Load saved settings', onClick: async () => {
          const snapshot = await api.matrixSettings();
          adoptMatrix(snapshot);
          showBanner(el.matrixBanner, '');
          writeMatrixDraft(snapshot.values);
        }},
      ]);
    } else {
      showMessage(el.message, `Could not save the matrix settings: ${error.message}`, 'error');
    }
  } finally {
    saving = false;
    renderMatrix();
  }
}

async function stopCurrent() {
  stopping = true;
  renderNowPlaying();
  try {
    status = await api.stop();
    showMessage(el.message, 'Stopped. The matrix is free.');
  } catch (error) {
    showMessage(el.message, `Could not stop: ${error.message}`, 'error');
  } finally {
    stopping = false;
    renderNowPlaying();
    renderBadges();
  }
}

async function refresh() {
  try {
    status = await api.status();
    stamp(el.lastUpdated);
  } catch (error) {
    status = null;
    el.lastUpdated.textContent = `Controller unreachable · retrying`;
  }
  if (!programs.length && status) await loadPrograms();
  renderNowPlaying();
  renderBadges();
  if (saving) return;
  try {
    // A first load that failed is retried here until it works.
    await loadMatrix({initial: !matrix});
  } catch (_error) {
    // The status line already shows when the controller can't be reached.
  }
}

async function loadPrograms() {
  try {
    const catalog = await api.visualizations();
    programs = Array.isArray(catalog.visualizations) ? catalog.visualizations : [];
    buildProgramRows();
    showMessage(el.message, '');
  } catch (error) {
    el.list.setAttribute('aria-busy', 'false');
    showMessage(el.message, `Could not load the programs: ${error.message} Retrying…`, 'error');
  }
}

el.nowStop.addEventListener('click', stopCurrent);
el.matrixSave.addEventListener('click', saveMatrix);
el.matrixDiscard.addEventListener('click', () => writeMatrixDraft(baseline));
el.matrixReset.addEventListener('click', () => writeMatrixDraft(matrix.config_values));
guardLeaving(unsentCount);

async function initialize() {
  if (location.hash === '#matrix-settings') el.matrixPanel.open = true;
  try {
    status = await api.status();
    stamp(el.lastUpdated);
  } catch (_error) {
    status = null;
  }
  await loadPrograms();
  renderBadges();
  renderNowPlaying();
  try {
    await loadMatrix({initial: true});
  } catch (error) {
    showBanner(el.matrixError, `Could not load the matrix settings: ${error.message} Retrying…`);
  }
  if (el.matrixPanel.open) el.matrixPanel.scrollIntoView({block: 'start'});
  poll(1500, refresh);
}

initialize();
