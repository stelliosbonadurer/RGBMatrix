// The FFT page's custom themes section (fft-themes M3, D-014–D-016). program.js
// keeps the saved snapshot, its polling, and Remove for an unusable file, and
// draws this in place of the generic file editor. Unlike that editor it saves on
// its own (Save themes), so Launch and Send update never send it. The rules are
// in programs/fft_themes.js.

import * as themes from './programs/fft_themes.js';
import {h} from './ui.js';

let editorCounter = 0;

// `onSave` and `onDiscard` are the section's own buttons; `onInput` fires on
// every edit so program.js can redraw; `onRemove` removes an unusable saved file.
export function createThemesEditor(snapshot, {onInput, onRemove, onSave, onDiscard}) {
  const number = ++editorCounter;
  const titleId = `themes-title-${number}`;
  let draft = themes.editable(snapshot.document);
  let saved = snapshot.document;
  let open = new Set(); // theme objects whose body is shown
  let locked = false; // an unusable saved file: nothing can be saved over it
  let busy = false;
  let errors = {};

  const banner = h('div', {className: 'banner', hidden: true});
  const note = h('p', {className: 'note', hidden: true});
  const intro = h('p', {className: 'field-help'},
    'Color themes for the layers\' Theme setting. Saved themes can be picked right away; ' +
    'Music FFT loads them when it launches.');
  const list = h('ol', {className: 'layer-list theme-list'});
  const empty = h('p', {className: 'meta empty-themes', hidden: true}, 'No custom themes yet.');
  const documentError = h('p', {className: 'field-error', role: 'alert', hidden: true});
  const status = h('span', {className: 'meta'});
  const add = h('button', {type: 'button', className: 'button secondary small', onClick: addTheme}, '+ New theme');
  const save = h('button', {type: 'button', className: 'button primary small', onClick: () => onSave?.()}, 'Save themes');
  const discard = h('button', {type: 'button', className: 'button ghost small', onClick: () => onDiscard?.()},
    'Discard theme edits');
  const remove = h('button', {type: 'button', className: 'button secondary small', hidden: true, onClick: onRemove},
    'Remove the saved file');

  const element = h('section', {className: 'panel file-section themes-section', 'aria-labelledby': titleId},
    h('div', {className: 'section-head'},
      h('h2', {className: 'section-title', id: titleId}, snapshot.label),
      h('span', {className: 'section-hint'}, 'saved on the Pi')),
    banner,
    note,
    intro,
    list,
    empty,
    documentError,
    h('div', {className: 'file-actions'}, add, h('span', {className: 'spacer'}), status, discard, save, remove),
  );

  // Every control the lock or a request in flight disables. `data-off` holds a
  // control's own reason to be off (a full list, nothing to save).
  const controls = () => [...element.querySelectorAll('input, select, button')].filter(control => control !== remove);

  function changed() {
    update();
    onInput?.();
  }

  // ---- One stop list: the main colors or an overflow layer's (D-015) --------------

  // The preview runs left to right, position 0 on the left, whatever the axis.
  function stopList(stops, setStops, {label}) {
    const preview = h('div', {className: 'theme-preview', role: 'img', 'aria-label': `${label} preview`});
    const rows = h('ol', {className: 'stop-list'});
    const error = h('p', {className: 'field-error', role: 'alert', hidden: true});
    const addStop = h('button', {type: 'button', className: 'button secondary small'}, '+ Add color');
    addStop.addEventListener('click', () => {
      setStops(themes.addStop(stops()));
      renderList();
      changed();
    });
    const drawRows = () => {
      rows.replaceChildren(...stops().map((stop, index) => {
        const count = stops().length;
        const end = index === 0 || index === count - 1;
        const color = h('input', {type: 'color', className: 'color-input', value: themes.colorToHex(stop.color),
          'aria-label': `${label} color ${index + 1}`});
        color.addEventListener('input', () => {
          stop.color = themes.hexToColor(color.value) || stop.color;
          changed();
        });
        const position = h('input', {type: 'number', className: 'stop-position', min: '0', max: '1', step: '0.01',
          inputMode: 'decimal', value: String(stop.position), 'aria-label': `${label} position ${index + 1}`});
        // The ends are fixed at 0 and 1 (themes/color_stops.py).
        position.readOnly = end;
        position.addEventListener('input', () => {
          stop.position = position.value.trim() === '' ? NaN : Number(position.value);
          changed();
        });
        const drop = h('button', {type: 'button', className: 'icon-button danger-text',
          'aria-label': `Remove ${label.toLowerCase()} color ${index + 1}`}, '✕');
        drop.dataset.off = themes.canRemoveStop(stops(), index) ? '' : '1';
        drop.addEventListener('click', () => {
          setStops(stops().filter((_stop, other) => other !== index));
          renderList();
          changed();
        });
        return h('li', {className: 'stop-row'}, color, position,
          h('span', {className: 'meta'}, end ? (index === 0 ? 'start' : 'end') : ''), drop);
      }));
    };
    drawRows();
    return {
      element: h('div', {className: 'stop-editor'}, preview, rows, error, addStop),
      update(message) {
        preview.style.background = themes.gradientCss(stops());
        error.textContent = message || '';
        error.hidden = !message;
        addStop.dataset.off = stops().length >= themes.MAX_STOPS ? '1' : '';
      },
    };
  }

  // ---- One theme ------------------------------------------------------------------

  function themeRow(theme, index) {
    const id = `theme-${number}-${index}`;
    const swatch = h('span', {className: 'theme-swatch', 'aria-hidden': 'true'});
    const title = h('button', {type: 'button', className: 'layer-title', 'aria-expanded': String(open.has(theme)),
      'aria-controls': id});
    const summary = h('span', {className: 'layer-summary'});
    const chevron = h('button', {type: 'button', className: 'icon-button layer-chevron',
      'aria-expanded': String(open.has(theme)), 'aria-controls': id}, open.has(theme) ? '▾' : '▸');
    const del = h('button', {type: 'button', className: 'icon-button danger-text'}, '✕');
    const toggle = () => {
      if (open.has(theme)) open.delete(theme);
      else open.add(theme);
      renderList();
      update();
    };
    title.addEventListener('click', toggle);
    chevron.addEventListener('click', toggle);
    del.addEventListener('click', () => {
      if (!window.confirm(`Delete theme ${theme.name}? Layers that use it switch to the default after you save.`)) return;
      draft.themes = draft.themes.filter(other => other !== theme);
      open.delete(theme);
      renderList();
      changed();
    });

    const nameId = `${id}-name`;
    const nameInput = h('input', {type: 'text', id: nameId, autocomplete: 'off', spellcheck: 'false',
      autocapitalize: 'off', value: themes.nameSuffix(theme.name)});
    nameInput.addEventListener('input', () => {
      theme.name = `${themes.PREFIX}${nameInput.value}`;
      changed();
    });
    const nameError = h('p', {className: 'field-error', role: 'alert', hidden: true});
    const descriptionId = `${id}-description`;
    const descriptionInput = h('input', {type: 'text', id: descriptionId, autocomplete: 'off', value: theme.description});
    descriptionInput.addEventListener('input', () => {
      theme.description = descriptionInput.value;
      changed();
    });
    const descriptionError = h('p', {className: 'field-error', role: 'alert', hidden: true});
    const axisId = `${id}-axis`;
    const axisSelect = h('select', {id: axisId},
      ...themes.AXES.map(axis => h('option', {value: axis}, themes.AXIS_LABELS[axis])));
    axisSelect.value = theme.axis;
    axisSelect.addEventListener('change', () => {
      theme.axis = axisSelect.value;
      changed();
    });

    const main = stopList(() => theme.stops, next => {
      theme.stops = next;
    }, {label: 'Colors'});
    const overflowEditors = theme.overflow.map((_stops, layer) => {
      const editor = stopList(() => theme.overflow[layer], next => {
        theme.overflow[layer] = next;
      }, {label: `Overflow ${layer + 1}`});
      const drop = h('button', {type: 'button', className: 'button ghost small'}, `Remove overflow ${layer + 1}`);
      drop.addEventListener('click', () => {
        theme.overflow = theme.overflow.filter((_other, other) => other !== layer);
        renderList();
        changed();
      });
      return {editor, element: h('div', {className: 'overflow-list'},
        h('h4', {className: 'field-label'}, `Overflow ${layer + 1}`), editor.element, drop)};
    });
    const addOverflow = h('button', {type: 'button', className: 'button secondary small'}, '+ Add overflow colors');
    addOverflow.addEventListener('click', () => {
      const last = theme.overflow.at(-1) || theme.stops;
      theme.overflow = [...theme.overflow, JSON.parse(JSON.stringify(last))];
      renderList();
      changed();
    });
    const overflowError = h('p', {className: 'field-error', role: 'alert', hidden: true});

    const body = h('div', {className: 'layer-body', id, hidden: !open.has(theme)},
      h('div', {className: 'fields'},
        h('div', {className: 'field'},
          h('div', {className: 'field-head'}, h('label', {className: 'field-label', for: nameId}, 'Name')),
          h('div', {className: 'prefixed-input'}, h('span', {className: 'input-prefix'}, themes.PREFIX), nameInput),
          h('p', {className: 'field-help'}, 'Lowercase letters, digits, and underscores. This is the name in the Theme list.'),
          nameError),
        h('div', {className: 'field'},
          h('div', {className: 'field-head'}, h('label', {className: 'field-label', for: descriptionId}, 'Description')),
          descriptionInput,
          h('p', {className: 'field-help'}, `Optional, at most ${themes.MAX_DESCRIPTION} characters.`),
          descriptionError),
        h('div', {className: 'field'},
          h('div', {className: 'field-head'}, h('label', {className: 'field-label', for: axisId}, 'Colors run')),
          axisSelect,
          h('p', {className: 'field-help'}, 'By height colors each bar from its bottom to the panel top; by column colors the bars from left to right.'))),
      h('h3', {className: 'subsection-title'}, 'Colors'),
      h('p', {className: 'field-help'}, 'Positions run from 0 (the start) to 1 (the end) and must not decrease down the list.'),
      main.element,
      h('h3', {className: 'subsection-title'}, 'Overflow colors'),
      h('p', {className: 'field-help'},
        `For bars taller than the panel. Overflow layer 1 uses the first list and later layers reuse the last; ` +
        `with none, overflow repeats the colors above. At most ${themes.MAX_OVERFLOW}.`),
      ...overflowEditors.map(item => item.element),
      overflowError,
      addOverflow);

    const element = h('li', {className: `layer-row theme-row${open.has(theme) ? ' open' : ''}`},
      h('div', {className: 'layer-line'}, swatch, title, chevron),
      h('div', {className: 'layer-line layer-line-secondary'}, summary, del),
      body);

    return {
      element,
      update() {
        const name = theme.name || themes.PREFIX;
        title.textContent = name;
        chevron.setAttribute('aria-label', `${open.has(theme) ? 'Close' : 'Open'} ${name}`);
        del.setAttribute('aria-label', `Delete ${name}`);
        swatch.style.background = themes.gradientCss(theme.stops);
        const overflow = theme.overflow.length ? ` · ${theme.overflow.length} overflow` : '';
        summary.textContent = `${theme.axis === 'height' ? 'by height' : 'by column'} · ${theme.stops.length} colors${overflow}`;
        const rowErrors = Object.keys(errors).filter(key => key === String(index) || key.startsWith(`${index}.`));
        element.classList.toggle('has-error', rowErrors.length > 0);
        nameError.textContent = errors[`${index}.name`] || '';
        nameError.hidden = !errors[`${index}.name`];
        descriptionError.textContent = errors[`${index}.description`] || '';
        descriptionError.hidden = !errors[`${index}.description`];
        main.update(errors[`${index}.stops`]);
        overflowEditors.forEach((item, layer) => item.editor.update(errors[`${index}.overflow.${layer}`]));
        overflowError.textContent = errors[`${index}.overflow`] || '';
        overflowError.hidden = !errors[`${index}.overflow`];
        addOverflow.dataset.off = theme.overflow.length >= themes.MAX_OVERFLOW ? '1' : '';
      },
    };
  }

  let rows = [];

  function renderList() {
    rows = draft.themes.map(themeRow);
    list.replaceChildren(...rows.map(row => row.element));
  }

  function addTheme() {
    if (draft.themes.length >= themes.MAX_THEMES) return;
    const theme = themes.newTheme(draft.themes.map(other => other.name));
    draft.themes = [...draft.themes, theme];
    open.add(theme);
    renderList();
    changed();
    rows.at(-1)?.element.scrollIntoView({block: 'nearest'});
  }

  function changeCount() {
    return themes.changedThemeCount(draft, saved);
  }

  function update() {
    errors = themes.themeErrors(draft);
    for (const row of rows) row.update();
    empty.hidden = draft.themes.length > 0;
    documentError.textContent = errors.document || '';
    documentError.hidden = !errors.document;
    const count = changeCount();
    const problems = Object.keys(errors).length;
    status.textContent = locked ? '' : problems ? 'Fix the marked fields to save.'
      : count ? `${count} unsaved ${count === 1 ? 'theme' : 'themes'}` : 'Saved';
    add.dataset.off = draft.themes.length >= themes.MAX_THEMES ? '1' : '';
    save.dataset.off = count === 0 || problems > 0 ? '1' : '';
    discard.dataset.off = count === 0 ? '1' : '';
    for (const control of controls()) control.disabled = locked || busy || control.dataset.off === '1';
    remove.disabled = busy;
  }

  renderList();
  update();

  return {
    element,
    banner,
    selfSaving: true,
    read() {
      return {document: JSON.parse(JSON.stringify(draft)), errors: {...errors}};
    },
    write(document) {
      // Themes stay open by their place in the list (a save or a reload makes new objects).
      const openIndexes = draft.themes.map((theme, index) => (open.has(theme) ? index : -1)).filter(index => index >= 0);
      draft = themes.editable(document);
      saved = document;
      open = new Set(openIndexes.map(index => draft.themes[index]).filter(Boolean));
      renderList();
      update();
    },
    // program.js passes the saved document each redraw; edits are counted against it.
    setSaved(document) {
      saved = document;
      update();
    },
    changeCount,
    setState() {
      // The generic editor's slot markers; this section marks its own state.
    },
    setDisabled({editing, busy: next}) {
      locked = editing;
      busy = next;
      update();
    },
    // A saved file the program can't use (D-047): say so and offer removal.
    setUnusable(error) {
      const sentence = error ? error.charAt(0).toUpperCase() + error.slice(1) : '';
      note.textContent = error
        ? `${sentence}. Launches use no custom themes until the saved file is removed.`
        : '';
      note.hidden = !error;
      remove.hidden = !error;
      intro.hidden = Boolean(error);
    },
  };
}
