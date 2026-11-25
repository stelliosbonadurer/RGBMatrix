// The editor for one program config file (M4b, D-039, D-045), drawn from the
// field descriptor the server sends. It holds part of the page's single draft:
// editing sends nothing; program.js saves it before a launch (D-044).

import {colorToHex, fileSlots, hexToColor, humanize} from './model.js';
import {createField, h} from './ui.js';

let editorCounter = 0;
const FALLBACK_COLOR = [0, 0, 0];

function colorInput(id, label, onInput) {
  const input = h('input', {type: 'color', id, className: 'color-input', 'aria-label': label});
  input.addEventListener('input', onInput);
  return input;
}

// `text` overrides the generic wording: {restore}. `onInput` fires on every edit,
// `onRestore` and `onRemove` on the section's buttons.
export function createFileEditor(snapshot, {text = {}, onInput, onRestore, onRemove}) {
  const number = ++editorCounter;
  const titleId = `file-title-${number}`;
  const fields = snapshot.fields || [];
  const numbers = new Map(); // key -> createField result
  const colors = new Map(); // slot key -> {input, element}
  const controls = [];

  const banner = h('div', {className: 'banner', hidden: true});
  const note = h('p', {className: 'note', hidden: true});
  const body = h('div', {className: 'file-body'});

  for (const field of fields) {
    if (field.kind === 'int') {
      const parameter = {
        name: field.key,
        label: field.label,
        type: 'int',
        minimum: field.minimum,
        maximum: field.maximum,
        description: field.help || '',
      };
      const control = createField(parameter, null, {onInput});
      numbers.set(field.key, control);
      controls.push(control.input);
      let group = body.lastElementChild;
      if (!group || !group.classList.contains('fields')) {
        group = h('div', {className: 'fields'});
        body.append(group);
      }
      group.append(control.element);
    } else if (field.kind === 'color_map') {
      const grid = h('div', {className: 'color-grid'});
      for (const name of field.names) {
        const id = `file-${number}-${field.key}-${name}`;
        const label = humanize(name);
        const input = colorInput(id, label, onInput);
        const marker = h('span', {className: 'changed-marker', title: 'Unsent change', hidden: true}, '●');
        const element = h('label', {className: 'color-field', for: id}, input, h('span', {className: 'color-name'}, label), marker);
        colors.set(`${field.key}.${name}`, {input, element, marker});
        controls.push(input);
        grid.append(element);
      }
      body.append(h('h3', {className: 'subsection-title'}, field.label), grid);
    } else if (field.kind === 'color_list') {
      const grid = h('div', {className: 'component-grid'});
      for (let index = 0; index < field.length; index += 1) {
        const id = `file-${number}-${field.key}-${index}`;
        const input = colorInput(id, `${field.label} ${index + 1}`, onInput);
        const element = h('label', {className: 'component-swatch', for: id, title: `${field.label} ${index + 1}`},
          input, h('span', {}, String(index + 1)));
        colors.set(`${field.key}.${index}`, {input, element, marker: null});
        controls.push(input);
        grid.append(element);
      }
      body.append(
        h('h3', {className: 'subsection-title'}, field.label),
        field.help ? h('p', {className: 'field-help'}, field.help) : null,
        grid,
      );
    }
  }

  const restore = h('button', {type: 'button', className: 'button ghost small', onClick: onRestore},
    text.restore || 'Restore defaults');
  const remove = h('button', {type: 'button', className: 'button secondary small', hidden: true, onClick: onRemove},
    'Remove the saved file');
  controls.push(restore);

  const element = h('section', {className: 'panel file-section', 'aria-labelledby': titleId},
    h('div', {className: 'section-head'},
      h('h2', {className: 'section-title', id: titleId}, snapshot.label),
      h('span', {className: 'section-hint'}, 'saved on the Pi')),
    banner,
    note,
    body,
    h('div', {className: 'file-actions'}, restore, remove),
  );

  return {
    element,
    banner,
    // The draft as a document plus any field errors, by slot key.
    read() {
      const document = {};
      const errors = {};
      for (const field of fields) {
        if (field.kind === 'int') {
          const {value, error} = numbers.get(field.key).read();
          document[field.key] = value;
          if (error) errors[field.key] = error;
        } else if (field.kind === 'color_map') {
          document[field.key] = Object.fromEntries(field.names.map(name =>
            [name, hexToColor(colors.get(`${field.key}.${name}`).input.value)]));
        } else if (field.kind === 'color_list') {
          document[field.key] = Array.from({length: field.length}, (_unused, index) =>
            hexToColor(colors.get(`${field.key}.${index}`).input.value));
        }
      }
      return {document, errors};
    },
    write(document) {
      const slots = fileSlots(fields, document);
      for (const [key, control] of numbers) control.write(slots[key]);
      for (const [key, {input}] of colors) input.value = slots[key] || colorToHex(FALLBACK_COLOR);
    },
    // `changed` is a Set of slot keys; `errors` maps slot keys to messages.
    setState(changed, errors) {
      for (const [key, control] of numbers) {
        control.setChanged(changed.has(key));
        control.setError(errors[key] || null);
      }
      for (const [key, {element: swatch, marker}] of colors) {
        swatch.classList.toggle('changed', changed.has(key));
        if (marker) marker.hidden = !changed.has(key);
      }
    },
    // `editing` locks the draft; `busy` (a request in flight) also locks removal.
    setDisabled({editing, busy}) {
      for (const control of controls) control.disabled = editing || busy;
      remove.disabled = busy;
    },
    // A saved file the program can't use (D-047): say so and offer removal.
    setUnusable(error) {
      const sentence = error ? error.charAt(0).toUpperCase() + error.slice(1) : '';
      note.textContent = error
        ? `${sentence}. Launches use the default until the saved file is removed.`
        : '';
      note.hidden = !error;
      remove.hidden = !error;
    },
  };
}
