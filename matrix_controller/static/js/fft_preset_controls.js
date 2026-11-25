// The Music FFT page's presets (web-control-refactor M5 slice 3): the Preset line
// (D-063, D-071), "Copy from a preset…" in the Layers header (D-072), and "Save
// layer as preset…" in an open layer (D-070). Preset actions reach the Pi at once
// and aren't part of the draft; Load and Copy only change the draft. The rules
// are in programs/fft_presets.js; fft_editor.js owns the draft and places these.

import {api} from './api.js';
import * as fft from './programs/fft.js';
import * as rules from './programs/fft_presets.js';
import {h, plural} from './ui.js';

let controlsCounter = 0;

// `host` is the editor:
//   info(), catalog()      the structure the page draws from, and the catalog
//   sourceSpec             the audio_source parameter
//   unsentCount(), busy()  the draft's unsent count; a submit in flight
//   submits()              counts the editor's submits, so a reply that
//                          outlived a launch or an update is dropped
//   replaceDraft(result)   Load: rules.reconcile's {source, backend, layers, replaced}
//   addLayer(layer, replaced)  Copy: adds the layer on top and opens it
//   refresh()              redraws the editor, which calls refresh(draft) here
export function createPresetControls(host) {
  const number = ++controlsCounter;
  let entries = []; // the list: {preset_id, name, description, revision}
  let limits = {
    max_presets: rules.PRESET_LIMIT,
    max_name_length: rules.NAME_MAX,
    max_description_length: rules.DESCRIPTION_MAX,
  };
  let storeError = null;
  let selectedId = rules.DEFAULT_ID;
  let loaded = null; // {preset_id, name, description, revision, builtin, content}
  let working = false; // a preset request is in flight
  let edited = false;
  let lastDraft = null;
  let form = null; // the Preset line's open form: {kind, ...}

  const sources = () => ({choices: host.sourceSpec.choices || [], default: host.sourceSpec.default});
  const descriptorOf = layerType => fft.descriptorFor(host.info(), layerType);
  // Built once per catalog: refresh runs on every drag frame.
  let defaultCache = {catalog: undefined, entry: null};
  const defaultEntry = () => {
    const catalog = host.catalog();
    if (defaultCache.catalog !== catalog) {
      defaultCache = {catalog, entry: rules.defaultPreset(catalog, host.sourceSpec.default)};
    }
    return defaultCache.entry;
  };
  const entryOf = id => (id === rules.DEFAULT_ID ? defaultEntry() : entries.find(entry => entry.preset_id === id) || null);
  const busyNow = () => working || host.busy();
  const draftErrors = draft => [...(draft?.errors?.values() || [])];
  const draftValid = () => draftErrors(lastDraft).every(errors => Object.keys(errors).length === 0);
  const contentOf = draft => ({source: draft.source, backend: draft.backend, layers: draft.layers});
  const loadedFrom = preset => ({
    preset_id: preset.preset_id,
    name: preset.name,
    description: preset.description || '',
    revision: preset.revision,
    builtin: Boolean(preset.builtin),
    content: rules.storedContent(preset, host.sourceSpec.default),
  });

  // ---- The Preset line --------------------------------------------------------

  const select = h('select', {'aria-label': 'Preset'});
  const loadButton = h('button', {type: 'button', className: 'button secondary small'}, 'Load');
  const editItem = h('button', {type: 'button', className: 'menu-item', role: 'menuitem'}, 'Edit name and description');
  const deleteItem = h('button', {type: 'button', className: 'menu-item danger-text', role: 'menuitem'}, 'Delete…');
  const menu = h('details', {className: 'preset-menu'},
    h('summary', {className: 'icon-button', 'aria-label': 'More actions for this preset'}, '⋯'),
    h('div', {className: 'menu-popup', role: 'menu'}, editItem, deleteItem));
  const description = h('p', {className: 'field-help preset-description', hidden: true});
  const saveNewButton = h('button', {type: 'button', className: 'button secondary small'}, 'Save as new…');
  const saveToLabel = h('span', {className: 'button-label'});
  const saveToButton = h('button', {type: 'button', className: 'button secondary small', hidden: true}, saveToLabel);
  const saveHint = h('p', {className: 'meta', hidden: true});
  const formSlot = h('div', {className: 'preset-form-slot'});
  const storeLine = h('p', {className: 'field-error', hidden: true});
  const status = h('p', {className: 'meta preset-status', role: 'status', 'aria-live': 'polite', hidden: true});
  const note = h('p', {className: 'note', hidden: true});
  const error = h('p', {className: 'field-error', role: 'alert', hidden: true});
  const line = h('section', {className: 'panel preset-panel', 'aria-labelledby': `fft-presets-${number}`},
    h('div', {className: 'section-head'},
      h('h2', {className: 'section-title', id: `fft-presets-${number}`}, 'Presets'),
      h('span', {className: 'section-hint'}, 'saved on the Pi · Load fills the page, nothing is sent')),
    h('div', {className: 'preset-row'}, select, loadButton, menu),
    description,
    storeLine,
    h('div', {className: 'preset-actions'}, saveNewButton, saveToButton),
    saveHint,
    formSlot,
    status,
    note,
    error);

  function say({text = '', noteText = '', errorText = ''} = {}) {
    status.textContent = text;
    status.hidden = !text;
    note.textContent = noteText;
    note.hidden = !noteText;
    error.textContent = errorText;
    error.hidden = !errorText;
  }

  function writeOptions() {
    const options = [defaultEntry(), ...entries].map(entry => h('option', {value: entry.preset_id}, entry.name));
    select.replaceChildren(...options);
    select.value = selectedId;
  }

  async function reloadList() {
    let listed = false;
    try {
      const reply = await api.presets();
      entries = Array.isArray(reply.presets) ? reply.presets : [];
      limits = {...limits, ...(reply.limits || {})};
      storeError = null;
      listed = true;
    } catch (failure) {
      entries = [];
      storeError = failure.message || 'the request failed.';
    }
    if (!entryOf(selectedId)) selectedId = rules.DEFAULT_ID;
    // Only a list that loaded says a preset is gone; a failed reload keeps them.
    if (listed && loaded && !loaded.builtin && !entries.some(entry => entry.preset_id === loaded.preset_id)) loaded = null;
    if (listed && form?.kind === 'edit' && !entryOf(form.presetId)) closeForm();
    writeOptions();
  }

  // One preset request. A failure is reported on `target` (a form) or the line;
  // a 404 refreshes the list. Returns the reply, or null when it failed.
  async function request(call, name, kind, target = null) {
    working = true;
    say();
    target?.setError('');
    host.refresh();
    try {
      return await call();
    } catch (failure) {
      const text = rules.failureText(failure, name, kind);
      if (failure.code === 'name_conflict' && target) target.setNameError('Another preset has this name.');
      // A 404 refreshes the list, which can close the form, so it is said on the line.
      else if (target && failure.status !== 404) target.setError(text);
      else say({errorText: text});
      if (failure.status === 404) await reloadList();
      return null;
    } finally {
      working = false;
      host.refresh();
    }
  }

  // ---- Inline forms (name and description, D-071) --------------------------------

  let formCounter = 0;

  // `blocked()` says why saving is refused now, or null; `onSubmit(values, form)`
  // returns true when the form should close.
  function createForm({title, submitLabel, name = '', text = '', excludeId = null, blocked, onSubmit, onClose}) {
    const id = `fft-preset-form-${number}-${++formCounter}`;
    const nameInput = h('input', {type: 'text', id: `${id}-name`, autocomplete: 'off', spellcheck: 'false'});
    nameInput.value = name;
    const textInput = h('textarea', {id: `${id}-text`, rows: '2'});
    textInput.value = text;
    const nameError = h('p', {className: 'field-error', role: 'alert', hidden: true});
    const textError = h('p', {className: 'field-error', role: 'alert', hidden: true});
    const reason = h('p', {className: 'meta', hidden: true});
    const formError = h('p', {className: 'field-error', role: 'alert', hidden: true});
    const submit = h('button', {type: 'submit', className: 'button primary small'}, submitLabel);
    const cancel = h('button', {type: 'button', className: 'link-button'}, 'Cancel');
    const element = h('form', {className: 'preset-form', novalidate: true},
      h('p', {className: 'preset-form-title'}, title),
      h('div', {className: 'field'},
        h('label', {className: 'field-label', for: nameInput.id}, 'Name'), nameInput, nameError),
      h('div', {className: 'field'},
        h('label', {className: 'field-label', for: textInput.id}, 'Description'), textInput,
        h('p', {className: 'field-help'}, `Optional, at most ${limits.max_description_length} characters.`), textError),
      reason,
      formError,
      h('div', {className: 'form-actions'}, cancel, submit));
    let serverNameError = null;
    let sending = false;
    let touched = Boolean(name); // an empty new name isn't an error until typed or tried

    const handle = {
      element,
      refresh() {
        const nameMessage = serverNameError ||
          rules.nameError(nameInput.value, entries, {excludeId, max: limits.max_name_length});
        const shownName = touched ? nameMessage : null;
        const textMessage = rules.descriptionError(textInput.value, {max: limits.max_description_length});
        const why = blocked();
        nameError.textContent = shownName || '';
        nameError.hidden = !shownName;
        nameInput.setAttribute('aria-invalid', String(Boolean(shownName)));
        textError.textContent = textMessage || '';
        textError.hidden = !textMessage;
        textInput.setAttribute('aria-invalid', String(Boolean(textMessage)));
        reason.textContent = why || '';
        reason.hidden = !why;
        nameInput.disabled = sending;
        textInput.disabled = sending;
        submit.disabled = sending || busyNow() || Boolean(nameMessage || textMessage || why);
      },
      setNameError(message) {
        serverNameError = message || null;
        handle.refresh();
      },
      setError(message) {
        formError.textContent = message || '';
        formError.hidden = !message;
      },
      focus: () => nameInput.focus(),
    };
    nameInput.addEventListener('input', () => {
      touched = true;
      serverNameError = null;
      handle.setError('');
      handle.refresh();
    });
    textInput.addEventListener('input', () => handle.refresh());
    cancel.addEventListener('click', () => onClose());
    element.addEventListener('submit', async event => {
      event.preventDefault();
      touched = true;
      handle.refresh();
      if (submit.disabled) return;
      sending = true;
      handle.refresh();
      let done = false;
      try {
        done = await onSubmit({name: nameInput.value, description: textInput.value}, handle);
      } finally {
        sending = false;
        if (done) onClose();
        else handle.refresh();
      }
    });
    return handle;
  }

  function closeForm() {
    form = null;
    formSlot.replaceChildren();
    host.refresh();
  }

  function openForm(kind) {
    say();
    if (kind === 'new') {
      form = {kind, ...createForm({
        title: 'Save the page as a new preset',
        submitLabel: 'Save',
        blocked: () => rules.saveBlocked({storeError, draftValid: draftValid(), creating: true,
          count: entries.length, limit: limits.max_presets}),
        onSubmit: saveNew,
        onClose: closeForm,
      })};
    } else {
      const entry = entryOf(selectedId);
      if (!entry || entry.builtin) return;
      form = {kind, presetId: entry.preset_id, ...createForm({
        title: `Edit ${entry.name}`,
        submitLabel: 'Save',
        name: entry.name,
        text: entry.description || '',
        excludeId: entry.preset_id,
        blocked: () => (storeError ? 'Saved presets are unavailable.' : null),
        onSubmit: (values, target) => editEntry(entry.preset_id, values, target),
        onClose: closeForm,
      })};
    }
    formSlot.replaceChildren(form.element);
    host.refresh();
    form.focus();
  }

  // ---- Preset actions (D-063) -------------------------------------------------------

  async function loadSelected() {
    const entry = entryOf(selectedId);
    if (!entry || busyNow()) return;
    const count = host.unsentCount();
    if (count > 0 && !window.confirm(`Replace ${plural(count, 'unsent change')} with ${entry.name}?`)) return;
    let preset = entry;
    if (!entry.builtin) {
      const started = host.submits();
      preset = await request(() => api.preset(entry.preset_id), entry.name, 'load');
      if (!preset) return;
      if (started !== host.submits() || host.busy()) {
        say({errorText: `${entry.name} wasn't loaded: the page sent a launch or an update meanwhile. Load it again.`});
        return;
      }
      const now = host.unsentCount(); // the fields stay editable during the fetch
      if (now > 0 && now !== count && !window.confirm(`Replace ${plural(now, 'unsent change')} with ${entry.name}?`)) return;
    }
    const result = rules.reconcile(rules.storedContent(preset, host.sourceSpec.default), host.info(), sources());
    loaded = loadedFrom(preset);
    host.replaceDraft(result);
    const noteText = rules.reconcileNote(preset.name, result.changes,
      {saveTo: preset.builtin ? null : preset.name, backend: result.backend});
    say({text: `Loaded ${preset.name}. Nothing changes on the panel until you send it with the button below.`, noteText});
    host.refresh();
  }

  async function saveNew(values, target) {
    const body = rules.presetBody({...values, ...contentOf(lastDraft)});
    const created = await request(() => api.createPreset(body), body.name, 'save', target);
    if (!created) return false;
    loaded = loadedFrom(created);
    selectedId = created.preset_id;
    await reloadList();
    say({text: `Saved ${created.name} on the Pi.`});
    return true;
  }

  async function saveTo() {
    if (!loaded || loaded.builtin || busyNow()) return;
    const target = loaded;
    const body = rules.presetBody({name: target.name, description: target.description, ...contentOf(lastDraft)});
    const reply = await request(() => api.replacePreset(target.preset_id, target.revision, body), target.name, 'save');
    if (!reply) return;
    loaded = loadedFrom(reply);
    await reloadList();
    say({text: `Saved the page to ${reply.name}.`});
    host.refresh();
  }

  // Changes only the name and description: the stored content goes back as is,
  // at the revision the list showed, so a change made elsewhere is a 409.
  async function editEntry(presetId, values, target) {
    // The list's entry now, not when the form opened: Save to may have moved it on.
    const entry = entryOf(presetId);
    if (!entry || entry.builtin) {
      target.setError('This preset is no longer on the Pi.');
      return false;
    }
    const current = await request(() => api.preset(entry.preset_id), entry.name, 'edit', target);
    if (!current) return false;
    const body = {
      name: String(values.name).trim(),
      description: String(values.description).trim(),
      parameters: current.parameters,
      structure: current.structure,
    };
    const reply = await request(() => api.replacePreset(entry.preset_id, entry.revision, body), entry.name, 'edit', target);
    if (!reply) return false;
    if (loaded?.preset_id === reply.preset_id) loaded = loadedFrom(reply);
    await reloadList();
    say({text: `Saved the name and description of ${reply.name}.`});
    return true;
  }

  async function deleteSelected() {
    menu.open = false;
    const entry = entryOf(selectedId);
    if (!entry || entry.builtin || busyNow()) return;
    if (!window.confirm(`Delete preset ${entry.name}? This can't be undone.`)) return;
    const reply = await request(() => api.deletePreset(entry.preset_id, entry.revision), entry.name, 'delete');
    if (!reply) return;
    if (loaded?.preset_id === entry.preset_id) loaded = null;
    if (form?.presetId === entry.preset_id) closeForm();
    selectedId = rules.DEFAULT_ID;
    await reloadList();
    say({text: `Deleted ${entry.name}. The page keeps its layers.`});
    host.refresh();
  }

  select.addEventListener('change', () => {
    selectedId = select.value;
    if (form?.kind === 'edit') closeForm();
    host.refresh();
  });
  loadButton.addEventListener('click', loadSelected);
  saveNewButton.addEventListener('click', () => openForm('new'));
  saveToButton.addEventListener('click', saveTo);
  editItem.addEventListener('click', () => {
    menu.open = false;
    openForm('edit');
  });
  deleteItem.addEventListener('click', deleteSelected);
  document.addEventListener('click', event => {
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  });
  menu.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu.open) {
      menu.open = false;
      menu.querySelector('summary').focus();
    }
  });

  // ---- Copy from a preset (D-070, D-072) ---------------------------------------------

  const copyButton = h('button', {type: 'button', className: 'button secondary small', 'aria-expanded': 'false'},
    'Copy from a preset…');
  const copySelect = h('select', {'aria-label': 'Preset with the layer to copy'});
  const copyDescription = h('p', {className: 'field-help preset-description', hidden: true});
  const copyStatus = h('p', {className: 'meta', hidden: true});
  const copyList = h('ul', {className: 'copy-list'});
  const copyCancel = h('button', {type: 'button', className: 'link-button'}, 'Cancel');
  const copyPanel = h('div', {className: 'copy-panel', hidden: true},
    h('p', {className: 'preset-form-title'}, 'Copy a layer from a preset'),
    h('div', {className: 'copy-head'}, copySelect),
    copyDescription,
    copyStatus,
    copyList,
    h('div', {className: 'form-actions'}, copyCancel));
  const copyNote = h('p', {className: 'note', hidden: true});
  const copyArea = h('div', {className: 'copy-area'}, copyPanel, copyNote);
  let copyId = rules.DEFAULT_ID;
  let copyPreset = null; // the picked preset, fetched
  let copyMessage = '';
  let copyToken = 0;
  const copyItems = []; // {button, layer} for the drawn list

  function setCopyNote(text) {
    copyNote.textContent = text;
    copyNote.hidden = !text;
  }

  function openCopy() {
    setCopyNote('');
    copyPanel.hidden = false;
    copyButton.setAttribute('aria-expanded', 'true');
    copyId = entryOf(selectedId) ? selectedId : rules.DEFAULT_ID;
    writeCopyOptions();
    showCopyLayers();
  }

  function writeCopyOptions() {
    if (!entryOf(copyId)) copyId = rules.DEFAULT_ID;
    copySelect.replaceChildren(...[defaultEntry(), ...entries]
      .map(entry => h('option', {value: entry.preset_id}, entry.name)));
    copySelect.value = copyId;
  }

  function closeCopy() {
    copyToken += 1;
    copyPanel.hidden = true;
    copyButton.setAttribute('aria-expanded', 'false');
    copyPreset = null;
    copyList.replaceChildren();
    copyItems.length = 0;
  }

  async function showCopyLayers() {
    const token = ++copyToken;
    const entry = entryOf(copyId);
    copyDescription.textContent = entry?.description || '';
    copyDescription.hidden = !entry?.description;
    copyPreset = null;
    copyList.replaceChildren();
    copyItems.length = 0;
    if (!entry) return;
    if (entry.builtin) {
      copyPreset = entry;
    } else {
      copyMessage = 'Loading its layers…';
      refreshCopy();
      let fetched;
      try {
        fetched = await api.preset(entry.preset_id);
      } catch (failure) {
        if (token !== copyToken) return;
        const text = rules.failureText(failure, entry.name, 'load');
        if (failure.status === 404) {
          await reloadList();
          host.refresh();
          if (token !== copyToken) return;
          // The picked preset is gone: show Default's layers, and say why.
          writeCopyOptions();
          await showCopyLayers();
          copyMessage = text;
        } else {
          copyMessage = text;
        }
        refreshCopy();
        return;
      }
      if (token !== copyToken) return;
      copyPreset = fetched;
    }
    copyMessage = '';
    const layers = Array.isArray(copyPreset.structure?.layers) ? copyPreset.structure.layers : [];
    if (!layers.length) copyMessage = `${copyPreset.name} has no layers.`;
    // Top layer first, as the layer list shows them (D-054).
    for (const layer of fft.displayRows(layers)) {
      const known = Boolean(descriptorOf(layer.layer_type));
      const button = h('button', {type: 'button', className: 'button secondary small'}, 'Copy');
      button.addEventListener('click', () => copyLayer(layer));
      copyItems.push({button, known});
      copyList.append(h('li', {className: 'copy-item'},
        h('span', {className: 'copy-name'},
          h('strong', {}, String(layer.layer_id ?? '')),
          h('span', {}, known ? fft.layerSummary({visible: layer.visible, settings: layer.settings || {}})
            : `${layer.layer_type}: a type this page doesn't know`)),
        button));
    }
    refreshCopy();
  }

  function refreshCopy() {
    const count = lastDraft?.layers.length ?? 0;
    const limit = fft.maxLayers(host.info());
    const full = count >= limit;
    let text = copyMessage;
    if (!text && full) text = `${count} of ${limit} layers. Remove one to copy another.`;
    copyStatus.textContent = text;
    copyStatus.hidden = !text;
    copySelect.disabled = busyNow();
    for (const item of copyItems) item.button.disabled = busyNow() || full || !item.known;
  }

  function copyLayer(stored) {
    if (!lastDraft || busyNow() || lastDraft.layers.length >= fft.maxLayers(host.info())) return;
    const result = rules.reconcileLayer(stored, host.info(), lastDraft.backend);
    if (!result.layer) return;
    const original = String(stored.layer_id ?? '').trim();
    const name = rules.copyName(result.layer.layer_id, lastDraft.layers.map(layer => layer.layer_id));
    const from = copyPreset.name;
    host.addLayer({...result.layer, layer_id: name}, result.replaced);
    closeCopy();
    const changes = {...result, droppedTypes: [], backend: null, source: null};
    const said = rules.reconcileNote(original || name, changes, {backend: lastDraft.backend});
    setCopyNote(`Copied ${original || name} from ${from}${name !== original ? ` as ${name}` : ''}. ` +
      `It is an unsent change, so Discard removes it.${said ? ` ${said}` : ''}`);
  }

  copyButton.addEventListener('click', () => (copyPanel.hidden ? openCopy() : closeCopy()));
  copySelect.addEventListener('change', () => {
    copyId = copySelect.value;
    showCopyLayers();
  });
  copyCancel.addEventListener('click', closeCopy);

  // ---- Save one layer as a preset (D-070) ----------------------------------------------

  function layerSaver(uid) {
    const button = h('button', {type: 'button', className: 'button secondary small'}, 'Save layer as preset…');
    const slot = h('div', {className: 'preset-form-slot'});
    const saved = h('p', {className: 'meta', role: 'status', 'aria-live': 'polite', hidden: true});
    const element = h('div', {className: 'layer-saver'}, button, slot, saved);
    let saverForm = null;
    let layerValid = true;

    const close = () => {
      saverForm = null;
      slot.replaceChildren();
      button.hidden = false;
    };
    button.addEventListener('click', () => {
      const layer = lastDraft?.layers.find(other => other.uid === uid);
      saved.hidden = true;
      saverForm = createForm({
        title: 'Save this layer as a preset',
        submitLabel: 'Save',
        name: String(layer?.layer_id ?? '').trim(),
        blocked: () => rules.saveBlocked({storeError, draftValid: layerValid, creating: true,
          count: entries.length, limit: limits.max_presets}),
        onSubmit: async (values, target) => {
          const layerNow = lastDraft?.layers.find(other => other.uid === uid);
          if (!layerNow) return false;
          // An ordinary preset with one layer, the page's source, and its output.
          const body = rules.presetBody({...values, source: lastDraft.source, backend: lastDraft.backend, layers: [layerNow]});
          const created = await request(() => api.createPreset(body), body.name, 'save', target);
          if (!created) return false;
          await reloadList();
          saved.textContent = `Saved as the preset ${created.name}. Copy from a preset adds it to any page; Load replaces the layers with it.`;
          saved.hidden = false;
          host.refresh();
          return true;
        },
        onClose: close,
      });
      button.hidden = true;
      slot.replaceChildren(saverForm.element);
      saverForm.refresh();
      saverForm.focus();
    });

    return {
      element,
      // `valid`: the layer has no field error (criterion 8).
      refresh({valid}) {
        layerValid = valid;
        button.disabled = busyNow() || Boolean(storeError);
        saverForm?.refresh();
      },
    };
  }

  // ---- What the editor calls ------------------------------------------------------------

  // Nothing works before the editor's first draw (the catalog loads first).
  writeOptions();
  for (const control of [select, loadButton, saveNewButton, copyButton]) control.disabled = true;

  return {
    line,
    copyButton,
    copyArea,
    layerSaver,

    // The list, once when the page opens (D-062: not polled).
    async load() {
      await reloadList();
    },

    // After every draft change: "· edited", Save to, and the busy state.
    refresh(draft) {
      lastDraft = draft;
      const busy = busyNow();
      const valid = draftValid();
      edited = Boolean(loaded && !loaded.builtin &&
        rules.contentKey(contentOf(draft), descriptorOf) !== rules.contentKey(loaded.content, descriptorOf));
      for (const option of select.options) {
        const entry = entryOf(option.value);
        if (entry) option.textContent = rules.optionLabel(entry, {loadedId: loaded?.preset_id, edited});
      }
      select.value = selectedId;
      select.disabled = busy;
      const entry = entryOf(selectedId);
      loadButton.disabled = busy || !entry;
      description.textContent = entry?.description || '';
      description.hidden = !entry?.description;
      const own = Boolean(entry && !entry.builtin);
      menu.hidden = !own;
      if (!own || busy) menu.open = false;
      editItem.disabled = busy || Boolean(storeError);
      deleteItem.disabled = busy || Boolean(storeError);
      storeLine.textContent = storeError ? `Saved presets are unavailable: ${storeError} Default still works.` : '';
      storeLine.hidden = !storeError;
      saveNewButton.disabled = busy || Boolean(storeError) || form?.kind === 'new';
      const saveable = Boolean(loaded && !loaded.builtin);
      saveToButton.hidden = !saveable;
      if (saveable) {
        saveToLabel.textContent = `Save to ${loaded.name}`;
        saveToButton.title = `Save the page to ${loaded.name}`;
      }
      saveToButton.disabled = busy || !edited || !valid || Boolean(storeError);
      const hint = saveable && edited && !valid ? 'Fix the marked fields before saving.' : '';
      saveHint.textContent = hint;
      saveHint.hidden = !hint;
      form?.refresh();
      copyButton.disabled = busy;
      refreshCopy();
    },
  };
}
