// Pure page logic: labels, value parsing, flag strings, draft comparison, and the
// button rules. No DOM access, so `node --test` can import it (D-042).

const ACRONYMS = new Map([
  ['pwm', 'PWM'],
  ['lsb', 'LSB'],
  ['gpio', 'GPIO'],
  ['rgb', 'RGB'],
  ['fft', 'FFT'],
  ['bfs', 'BFS'],
  ['dfs', 'DFS'],
  ['mst', 'MST'],
]);

// 'max_runs' -> 'Max runs'; 'led_pwm_lsb_nanoseconds' -> 'PWM LSB nanoseconds'.
export function humanize(name) {
  const words = String(name).replace(/^led_/, '').split('_').filter(Boolean);
  return words
    .map((word, index) => {
      const acronym = ACRONYMS.get(word.toLowerCase());
      if (acronym) return acronym;
      return index === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word;
    })
    .join(' ');
}

// A field's label: its own (program file fields carry one) or the humanized name.
export function fieldLabel(parameter) {
  return parameter.label || humanize(parameter.name);
}

function formatBound(value) {
  return String(value);
}

export function rangeText(parameter) {
  if (parameter.type !== 'int' && parameter.type !== 'float') return '';
  const hasMin = parameter.minimum !== undefined && parameter.minimum !== null;
  const hasMax = parameter.maximum !== undefined && parameter.maximum !== null;
  if (hasMin && hasMax) return `${formatBound(parameter.minimum)}–${formatBound(parameter.maximum)}`;
  if (hasMin) return `at least ${formatBound(parameter.minimum)}`;
  if (hasMax) return `at most ${formatBound(parameter.maximum)}`;
  return '';
}

// Python's str(float): shortest round trip, a trailing ".0" for whole numbers,
// and scientific notation below 1e-4 or from 1e16, with a two-digit exponent.
export function pythonFloat(value) {
  if (Object.is(value, -0)) return '-0.0';
  const magnitude = Math.abs(value);
  if (magnitude !== 0 && (magnitude < 1e-4 || magnitude >= 1e16)) {
    return value.toExponential().replace(/e([+-])(\d)$/, 'e$10$2');
  }
  if (Number.isInteger(value)) return value.toFixed(1);
  return String(value);
}

function cliValue(parameter, value) {
  if (parameter.type === 'float') return pythonFloat(value);
  return String(value);
}

// Mirrors ParameterSpec.cli_arguments for an already valid value.
export function flagArguments(parameter, value) {
  if (parameter.type === 'bool') {
    if (value) {
      const argv = [parameter.cli_flag];
      if (parameter.true_cli_value !== null && parameter.true_cli_value !== undefined) {
        argv.push(parameter.true_cli_value);
      }
      return argv;
    }
    return parameter.negative_cli_flag ? [parameter.negative_cli_flag] : [];
  }
  return [parameter.cli_flag, cliValue(parameter, value)];
}

// Argument vector for a set of parameters; an invalid value shows as '?'.
export function flagVector(parameters, values, invalid = new Set()) {
  const argv = [];
  for (const parameter of parameters) {
    if (invalid.has(parameter.name)) {
      argv.push(parameter.cli_flag || parameter.name, '?');
    } else {
      argv.push(...flagArguments(parameter, values[parameter.name]));
    }
  }
  return argv;
}

function quoteToken(token) {
  if (token !== '' && !/[\s'"\\$`]/.test(token)) return token;
  return `'${token.replaceAll("'", "'\\''")}'`;
}

// Each argument as the page shows it: quoted when empty or holding spaces/quotes.
export function displayTokens(argv) {
  return argv.map(quoteToken);
}

export function formatArgv(argv) {
  return displayTokens(argv).join(' ');
}

// Turns a field's raw input into a typed value plus an error (or null). Mirrors
// ParameterSpec.normalize so the page can refuse a submit the server would reject.
export function parseValue(parameter, raw) {
  const label = fieldLabel(parameter);
  let value;
  if (parameter.type === 'bool') {
    return {value: Boolean(raw), error: null};
  }
  if (parameter.type === 'int') {
    const text = String(raw).trim();
    if (!/^-?\d+$/.test(text)) return {value: NaN, error: `${label} must be a whole number.`};
    value = Number.parseInt(text, 10);
  } else if (parameter.type === 'float') {
    const text = String(raw).trim();
    value = text === '' ? NaN : Number(text);
    if (!Number.isFinite(value)) return {value: NaN, error: `${label} must be a number.`};
  } else {
    value = String(raw);
    const limit = parameter.maximum_length;
    if (limit !== undefined && value.length > limit) {
      return {value, error: `${label} must be at most ${limit} characters.`};
    }
    if (parameter.type === 'enum' && !(parameter.choices || []).includes(value)) {
      return {value, error: `${label} must be one of: ${(parameter.choices || []).join(', ')}.`};
    }
    return {value, error: null};
  }
  // JSON sends -0 as 0, so the preview must show what the server receives.
  if (Object.is(value, -0)) value = 0;
  if (parameter.minimum !== undefined && parameter.minimum !== null && value < parameter.minimum) {
    return {value, error: `${label} must be at least ${formatBound(parameter.minimum)}.`};
  }
  if (parameter.maximum !== undefined && parameter.maximum !== null && value > parameter.maximum) {
    return {value, error: `${label} must be at most ${formatBound(parameter.maximum)}.`};
  }
  return {value, error: null};
}

export function sameValue(a, b) {
  return a === b || (typeof a === 'number' && typeof b === 'number' && Object.is(a, b));
}

export function changedNames(parameters, draft, baseline) {
  return parameters
    .map(parameter => parameter.name)
    .filter(name => !sameValue(draft[name], baseline[name]));
}

export function defaultsOf(parameters) {
  return Object.fromEntries(parameters.map(parameter => [parameter.name, parameter.default]));
}

// The running values for a program, or null when it isn't the active one.
export function runningValues(status, programId, parameters) {
  if (!status || status.visualization_id !== programId) return null;
  if (status.state !== 'running' && status.state !== 'starting') return null;
  const supplied = status.parameters || {};
  const values = defaultsOf(parameters);
  for (const parameter of parameters) {
    if (Object.prototype.hasOwnProperty.call(supplied, parameter.name)) {
      values[parameter.name] = supplied[parameter.name];
    }
  }
  return values;
}

// True when the active run used different matrix values than the stored ones.
export function matrixChangedSinceLaunch(status, storedValues) {
  const used = status?.matrix_settings;
  if (!used || !storedValues || Object.keys(used).length === 0) return false;
  if (status.state !== 'running' && status.state !== 'starting') return false;
  return Object.keys(storedValues).some(name => !sameValue(used[name], storedValues[name]));
}

// Program config files (M4b, D-039). A file's editor is drawn from the field
// descriptor the server sends; each size, named color, and list slot is one
// "slot" for change counting and markers.

const hexDigits = channel => channel.toString(16).padStart(2, '0');

export function colorToHex(color) {
  return `#${color.map(hexDigits).join('')}`;
}

export function hexToColor(hex) {
  const match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex));
  if (!match) return null;
  return match.slice(1).map(part => Number.parseInt(part, 16));
}

// {slot key: comparable value} for a document: numbers as-is, colors as hex.
export function fileSlots(fields, document) {
  const slots = {};
  for (const field of fields) {
    const value = document?.[field.key];
    if (field.kind === 'int') {
      slots[field.key] = value;
    } else if (field.kind === 'color_map') {
      for (const name of field.names) {
        const color = value?.[name];
        slots[`${field.key}.${name}`] = Array.isArray(color) ? colorToHex(color) : null;
      }
    } else if (field.kind === 'color_list') {
      for (let index = 0; index < field.length; index += 1) {
        const color = value?.[index];
        slots[`${field.key}.${index}`] = Array.isArray(color) ? colorToHex(color) : null;
      }
    }
  }
  return slots;
}

// The slot keys where the draft differs from the saved document.
// A file with no editor fields gets no section on the page; its snapshot still
// drives revisions and "changed since launch" (fft-themes D-011).
export function hasFileEditor(fields) {
  return Array.isArray(fields) && fields.length > 0;
}

export function fileChanges(fields, draft, saved) {
  const draftSlots = fileSlots(fields, draft);
  const savedSlots = fileSlots(fields, saved);
  return Object.keys(draftSlots).filter(key => !sameValue(draftSlots[key], savedSlots[key]));
}

// True when the active run of this program started with a different revision of
// a file than the one saved now. Revision null (an unusable saved file) and 0 (no
// saved file) both mean the tracked default.
export function filesChangedSinceLaunch(status, programId, savedRevisions) {
  if (!status || status.visualization_id !== programId) return false;
  if (status.state !== 'running' && status.state !== 'starting') return false;
  const used = status.program_files || {};
  return Object.entries(savedRevisions).some(([name, revision]) =>
    Object.prototype.hasOwnProperty.call(used, name) && (used[name] ?? 0) !== (revision ?? 0));
}

// What the page does with a polled program file (M4b criterion 8). `current` and
// `next` are snapshots ({revision, error}); `dirty` means unsent edits.
//   none:   nothing changed.
//   follow: load the saved document (a clean editor, or an unusable file shown
//           as the default).
//   keep:   keep the edits and offer the saved version in a banner (as D-038).
//   reset:  the file became unusable under unsent edits, which can't be saved
//           until it is removed: load the default and say so (review finding 1).
export function fileUpdateAction(current, next, dirty) {
  if (next.revision === current.revision && next.error === current.error) return 'none';
  if (next.error) return dirty ? 'reset' : 'follow';
  return dirty ? 'keep' : 'follow';
}

// "Matrix settings and colors changed since launch", or '' when nothing did.
// `fileNouns` names each program file whose saved revision differs from the run's.
export function changedSinceLaunchText(matrixChanged, fileNouns) {
  const parts = [...(matrixChanged ? ['matrix settings'] : []), ...fileNouns];
  if (!parts.length) return '';
  const joined = parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(', ')} and ${parts.at(-1)}`;
  return `${joined.charAt(0).toUpperCase()}${joined.slice(1)} changed since launch`;
}

// Identifies "this program's current run" so the page notices a relaunch.
export function runKey(status, programId) {
  if (!status || status.visualization_id !== programId) return null;
  if (status.state !== 'running' && status.state !== 'starting') return null;
  return `run:${status.run_id}`;
}

// The D-037 state table: what the top toggle and the bottom button do. `relaunch`
// (Music FFT, D-062) says a running update must relaunch rather than go live.
export function buttonModel({status, programId, programName, otherName, dirty, matrixChanged, filesChanged = false, valid, pending, relaunch = false}) {
  const state = status?.state || 'unknown';
  const active = status?.visualization_id === programId;
  const running = active && state === 'running';
  const transitioning = active && (state === 'starting' || state === 'stopping');
  const failedHere = state === 'failed' && status?.last_visualization_id === programId;

  let statusText;
  let tone = 'idle';
  if (running) {
    statusText = `${programName} is running on the matrix.`;
    tone = 'running';
  } else if (active && state === 'starting') {
    statusText = 'Starting…';
    tone = 'starting';
  } else if (active && state === 'stopping') {
    statusText = 'Stopping…';
    tone = 'stopping';
  } else if (failedHere) {
    statusText = `Stopped with an error: ${status.last_error || 'unknown error'}`;
    tone = 'failed';
  } else if (status?.visualization_id && (state === 'running' || state === 'starting')) {
    statusText = `${otherName || 'Another program'} is running. Launch replaces it.`;
  } else if (status === null || status === undefined) {
    statusText = 'Checking the matrix…';
    tone = 'unknown';
  } else {
    statusText = 'Not running.';
  }

  const top = running
    ? {label: 'Terminate', action: 'stop', disabled: Boolean(pending)}
    : transitioning
      ? {label: state === 'starting' ? 'Starting…' : 'Stopping…', action: null, disabled: true}
      : {label: 'Launch', action: 'launch', disabled: Boolean(pending) || !valid};

  let bottom;
  if (running) {
    bottom = {
      label: relaunch ? 'Relaunch with changes' : 'Send update',
      action: 'update',
      disabled: Boolean(pending) || !valid || !(dirty || matrixChanged || filesChanged),
    };
  } else if (transitioning) {
    bottom = {label: 'Send update', action: null, disabled: true};
  } else {
    bottom = {label: 'Launch with these settings', action: 'launch', disabled: Boolean(pending) || !valid};
  }
  if (pending) {
    const busy = {launch: 'Launching…', update: relaunch ? 'Relaunching…' : 'Sending…', stop: 'Stopping…'}[pending] || 'Working…';
    if (pending === 'stop') top.label = busy;
    else bottom.label = busy;
  }
  return {top, bottom, statusText, tone, running, active};
}

// Home strip text for the current status.
export function nowPlaying(status, nameOf) {
  if (!status) return {name: 'Checking the matrix…', detail: '', tone: 'unknown', activeId: null, canStop: false};
  const state = status.state;
  const id = status.visualization_id;
  if (id && (state === 'running' || state === 'starting' || state === 'stopping')) {
    const detail = state === 'running' ? 'Running' : state === 'starting' ? 'Starting…' : 'Stopping…';
    return {name: nameOf(id), detail, tone: state, activeId: id, canStop: state !== 'stopping'};
  }
  if (state === 'failed') {
    const failedId = status.last_visualization_id;
    return {
      name: failedId ? nameOf(failedId) : 'Last program',
      detail: `Stopped with an error: ${status.last_error || 'unknown error'}`,
      tone: 'failed',
      activeId: null,
      failedId: failedId || null,
      canStop: false,
    };
  }
  return {name: 'Nothing running', detail: 'The matrix is free.', tone: 'idle', activeId: null, canStop: false};
}

// Badge for one program row on the home list.
export function rowBadge(status, programId) {
  if (!status) return null;
  if (status.visualization_id === programId) {
    if (status.state === 'running') return {text: 'running', tone: 'running'};
    if (status.state === 'starting') return {text: 'starting', tone: 'starting'};
    if (status.state === 'stopping') return {text: 'stopping', tone: 'stopping'};
  }
  if (status.state === 'failed' && status.last_visualization_id === programId) {
    return {text: 'failed', tone: 'failed'};
  }
  return null;
}

// Common settings first in the server's order, the rest in definition order.
export function matrixGroups(parameters, commonNames) {
  const byName = new Map(parameters.map(parameter => [parameter.name, parameter]));
  const common = commonNames.map(name => byName.get(name)).filter(Boolean);
  const commonSet = new Set(commonNames);
  const advanced = parameters.filter(parameter => !commonSet.has(parameter.name));
  return {common, advanced};
}
