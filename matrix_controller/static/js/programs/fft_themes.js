// Custom FFT themes on the page (fft-themes M3): the rules behind the themes
// editor (fft_themes_editor.js) and the Theme choices (fft_editor.js). The FFT's
// own parser, my_programs/fft_program/themes/custom.py, is the final check
// (D-047); these limits mirror it (D-008, D-009, D-012) so the page can say what
// is wrong before sending.

import {colorToHex, hexToColor} from '../model.js';

export const FILE_NAME = 'themes';
export const PREFIX = 'my_';
export const SUFFIX_MAX = 29;
export const MAX_THEMES = 32;
export const MIN_STOPS = 2;
export const MAX_STOPS = 16;
export const MAX_OVERFLOW = 4;
export const MAX_DESCRIPTION = 80;
export const MAX_DOCUMENT_BYTES = 48 * 1024;
export const AXES = ['height', 'column'];
export const AXIS_LABELS = {height: 'By height (bottom to top)', column: 'By column (left to right)'};

const NAME_PATTERN = /^my_[a-z0-9_]{1,29}$/;
const SUFFIX_PATTERN = /^[a-z0-9_]{1,29}$/;
const NEW_STOPS = [{position: 0, color: [0, 64, 255]}, {position: 1, color: [255, 0, 128]}];

export const isCustomName = name => typeof name === 'string' && NAME_PATTERN.test(name);

const copy = value => JSON.parse(JSON.stringify(value));

// ---- Theme choices (D-013, D-014) ----------------------------------------------

// The names launches would register: the saved file's, or none when it can't be
// used (launches then use the tracked default, which has none of its own).
export function savedThemeNames(snapshot) {
  if (!snapshot || snapshot.error) return [];
  const themes = snapshot.document?.themes;
  return Array.isArray(themes) ? themes.map(theme => theme?.name).filter(isCustomName) : [];
}

function themeField(descriptor) {
  return (descriptor?.fields || []).find(field => field.name === 'theme' && field.type === 'choice') || null;
}

// The structure with every layer type's Theme choices extended by `names`, so the
// fields, reconcile (D-064), and presets all see one list. The structure's own
// choices come first: the catalog's built-ins, or a running FFT's loaded themes.
export function withThemeChoices(structure, names) {
  const types = structure?.capabilities?.layer_types;
  if (!Array.isArray(types) || !names.length) return structure;
  return {
    ...structure,
    capabilities: {
      ...structure.capabilities,
      layer_types: types.map(type => {
        const field = themeField(type?.settings);
        if (!field) return type;
        const choices = [...field.choices, ...names.filter(name => !field.choices.includes(name))];
        return {
          ...type,
          settings: {
            ...type.settings,
            fields: type.settings.fields.map(other => (other === field ? {...field, choices} : other)),
          },
        };
      }),
    },
  };
}

// The structure's Theme choices without custom names: a stopped run's loaded
// themes are not what the next launch registers (review finding 1).
function withoutCustomThemes(structure) {
  const types = structure?.capabilities?.layer_types;
  if (!Array.isArray(types)) return structure;
  return {
    ...structure,
    capabilities: {
      ...structure.capabilities,
      layer_types: types.map(type => {
        const field = themeField(type?.settings);
        if (!field || !field.choices.some(isCustomName)) return type;
        const choices = field.choices.filter(name => !isCustomName(name));
        return {
          ...type,
          settings: {
            ...type.settings,
            fields: type.settings.fields.map(other => (other === field ? {...field, choices} : other)),
          },
        };
      }),
    },
  };
}

// The structure the page draws from: the saved names added to its Theme
// choices, and a run's loaded custom themes kept only while that run is `live`.
export function themeInfo(structure, saved, live) {
  return withThemeChoices(live ? structure : withoutCustomThemes(structure), saved);
}

// The Theme choices of one layer type in `structure`, or null without a theme field.
export function themeChoices(structure, layerType) {
  const types = structure?.capabilities?.layer_types || [];
  const type = types.find(item => item.layer_type === layerType);
  return themeField(type?.settings)?.choices || null;
}

export function themeDefault(structure, layerType) {
  const types = structure?.capabilities?.layer_types || [];
  const type = types.find(item => item.layer_type === layerType);
  return themeField(type?.settings)?.default ?? null;
}

// D-018: a run started with a different saved themes file and a layer uses a
// custom theme, so only a relaunch shows it (FFT loads themes at launch, D-005).
export function themesNeedRelaunch(themesChanged, layers) {
  return Boolean(themesChanged) && layers.some(layer => isCustomName(layer.settings?.theme));
}

const listText = items => (items.length === 1 ? items[0]
  : `${items.slice(0, -1).join(', ')} and ${items.at(-1)}`);

// The page message for layers that use the default theme instead of theirs:
// after a launch (the child's theme_fallbacks, D-013) or after a save removed a
// theme an unsent layer used (D-017). Each item is {layer_id, theme}.
export function fallbackNote(items, fallback = 'ocean') {
  if (!Array.isArray(items) || !items.length) return '';
  const byTheme = new Map();
  for (const item of items) {
    const theme = String(item.theme);
    if (!byTheme.has(theme)) byTheme.set(theme, []);
    byTheme.get(theme).push(String(item.layer_id || '').trim() || '(no name)');
  }
  return [...byTheme].map(([theme, layers]) =>
    `${theme} isn't saved on the Pi, so ${layers.length === 1 ? 'layer' : 'layers'} ${listText(layers)} ` +
    `${layers.length === 1 ? 'uses' : 'use'} ${fallback}.`).join(' ');
}

// ---- The document (D-008, D-015) ---------------------------------------------------

export function newTheme(taken) {
  const used = new Set(taken);
  let number = 1;
  while (used.has(`${PREFIX}theme_${number}`)) number += 1;
  return {name: `${PREFIX}theme_${number}`, description: '', axis: 'height', stops: copy(NEW_STOPS), overflow: []};
}

// A copy of `document` in the editor's shape. Anything unexpected becomes
// something the editor can draw; the parser still checks what is saved.
export function editable(document) {
  const themes = Array.isArray(document?.themes) ? document.themes : [];
  const stopsOf = list => (Array.isArray(list) ? list : []).map(stop => ({
    position: typeof stop?.position === 'number' ? stop.position : 0,
    color: Array.isArray(stop?.color) && stop.color.length === 3 ? stop.color.map(Number) : [0, 0, 0],
  }));
  return {
    themes: themes.map(theme => ({
      name: typeof theme?.name === 'string' ? theme.name : '',
      description: typeof theme?.description === 'string' ? theme.description : '',
      axis: AXES.includes(theme?.axis) ? theme.axis : 'height',
      stops: stopsOf(theme?.stops),
      overflow: (Array.isArray(theme?.overflow) ? theme.overflow : []).map(stopsOf),
    })),
  };
}

// The suffix the name input shows (the my_ prefix is drawn in front of it).
export const nameSuffix = name => (String(name).startsWith(PREFIX) ? String(name).slice(PREFIX.length) : String(name));

// A new stop in the widest gap, colored as the gradient is there. Returns the
// list unchanged when it is full.
export function addStop(stops) {
  if (stops.length >= MAX_STOPS) return stops;
  let gap = 0;
  for (let index = 1; index < stops.length; index += 1) {
    if (stops[index].position - stops[index - 1].position > stops[gap + 1].position - stops[gap].position) gap = index - 1;
  }
  const low = stops[gap];
  const high = stops[gap + 1];
  const position = Math.round(((low.position + high.position) / 2) * 1000) / 1000;
  const color = low.color.map((channel, index) => Math.round((channel + high.color[index]) / 2));
  return [...stops.slice(0, gap + 1), {position, color}, ...stops.slice(gap + 1)];
}

// The ends are fixed at 0 and 1 (themes/color_stops.py), so only inner stops go.
export const canRemoveStop = (stops, index) => stops.length > MIN_STOPS && index > 0 && index < stops.length - 1;

// The CSS gradient a stop list previews as: linear in RGB, like build_lut.
// `axis` 'height' runs bottom to top, 'column' left to right.
export function gradientCss(stops, axis = 'column') {
  const parts = stops
    .filter(stop => Number.isFinite(stop.position) && Array.isArray(stop.color))
    .map(stop => `${colorToHex(stop.color.map(channel => Math.max(0, Math.min(255, Math.round(channel) || 0))))} ` +
      `${Math.max(0, Math.min(1, stop.position)) * 100}%`);
  if (parts.length < 2) return 'none';
  return `linear-gradient(${axis === 'height' ? 'to top' : 'to right'}, ${parts.join(', ')})`;
}

export {colorToHex, hexToColor};

// Python's json.dumps(separators=(",", ":")) escapes every UTF-16 unit outside
// space..~ as \uXXXX (six bytes); JSON.stringify leaves most of them raw. The
// short escapes (\n, \t, ...) are the same length in both.
export function compactSize(document) {
  const text = JSON.stringify(document);
  let extra = 0;
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    if (code >= 0x7f) extra += 5;
  }
  return text.length + extra;
}

function stopListError(stops, where) {
  if (!Array.isArray(stops) || stops.length < MIN_STOPS || stops.length > MAX_STOPS) {
    return `${where} needs ${MIN_STOPS} to ${MAX_STOPS} colors.`;
  }
  let previous = -Infinity;
  for (const stop of stops) {
    if (typeof stop.position !== 'number' || !Number.isFinite(stop.position) || stop.position < 0 || stop.position > 1) {
      return `${where}: each position must be a number from 0 to 1.`;
    }
    if (stop.position < previous) return `${where}: positions must not decrease from top to bottom.`;
    previous = stop.position;
    if (!Array.isArray(stop.color) || stop.color.length !== 3 ||
        stop.color.some(channel => !Number.isInteger(channel) || channel < 0 || channel > 255)) {
      return `${where}: each color must be a valid color.`;
    }
  }
  if (stops[0].position !== 0 || stops.at(-1).position !== 1) return `${where} must start at 0 and end at 1.`;
  return null;
}

// The draft's problems, keyed `index.name`, `index.description`, `index.stops`,
// `index.overflow.n` (n from 0), and `document` for the whole file.
export function themeErrors(document) {
  const errors = {};
  const themes = document.themes;
  if (themes.length > MAX_THEMES) errors.document = `At most ${MAX_THEMES} themes.`;
  const seen = new Set();
  themes.forEach((theme, index) => {
    const suffix = nameSuffix(theme.name);
    if (!theme.name.startsWith(PREFIX) || !SUFFIX_PATTERN.test(suffix)) {
      errors[`${index}.name`] = `Use 1 to ${SUFFIX_MAX} lowercase letters, digits, or underscores after ${PREFIX}.`;
    } else if (seen.has(theme.name)) {
      errors[`${index}.name`] = `Another theme is already called ${theme.name}.`;
    }
    seen.add(theme.name);
    // Python counts code points, not UTF-16 units.
    if ([...theme.description].length > MAX_DESCRIPTION) {
      errors[`${index}.description`] = `At most ${MAX_DESCRIPTION} characters.`;
    }
    const stopsError = stopListError(theme.stops, 'Colors');
    if (stopsError) errors[`${index}.stops`] = stopsError;
    if (theme.overflow.length > MAX_OVERFLOW) errors[`${index}.overflow`] = `At most ${MAX_OVERFLOW} overflow color lists.`;
    theme.overflow.forEach((stops, layer) => {
      const error = stopListError(stops, `Overflow ${layer + 1}`);
      if (error) errors[`${index}.overflow.${layer}`] = error;
    });
  });
  if (!errors.document) {
    const size = compactSize(document);
    if (size > MAX_DOCUMENT_BYTES) {
      errors.document = `The themes are too large to save (${size} of ${MAX_DOCUMENT_BYTES} bytes). Remove some colors or themes.`;
    }
  }
  return errors;
}

// How many themes differ from the saved file: one per edited, added, or removed theme.
export function changedThemeCount(draft, saved) {
  const a = draft?.themes || [];
  const b = editable(saved).themes;
  let count = Math.abs(a.length - b.length);
  for (let index = 0; index < Math.min(a.length, b.length); index += 1) {
    if (JSON.stringify(a[index]) !== JSON.stringify(b[index])) count += 1;
  }
  return count;
}
