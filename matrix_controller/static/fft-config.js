'use strict';

// This page intentionally treats the child process as the source of truth. It
// never guesses that FFT controls exist because of the URL; controls are rendered
// only from the schema returned by the active visualization's runtime endpoint.
const elements = {
  statusDot: document.querySelector('#status-dot'),
  currentName: document.querySelector('#current-name'),
  currentDetail: document.querySelector('#current-detail'),
  startButton: document.querySelector('#start-button'),
  quitButton: document.querySelector('#quit-button'),
  quitLabel: document.querySelector('#quit-button .button-label'),
  pairingCode: document.querySelector('#pairing-code'),
  pairButton: document.querySelector('#pair-button'),
  accessStatus: document.querySelector('#access-status'),
  forgetButton: document.querySelector('#forget-button'),
  message: document.querySelector('#message'),
  liveDetail: document.querySelector('#live-controls-detail'),
  liveConnection: document.querySelector('#live-controls-connection'),
  liveEmpty: document.querySelector('#live-controls-empty'),
  liveGroups: document.querySelector('#live-controls-groups'),
  lastUpdated: document.querySelector('#last-updated'),
};

let authorizationStatus = null;
let currentStatus = null;
let currentRuntime = null;
let fftDefinition = null;
let requestPending = false;

async function readJson(response) {
  let body = {};
  try {
    body = await response.json();
  } catch (_error) {
    throw new Error(`Controller returned an invalid response (${response.status}).`);
  }
  if (!response.ok) {
    const error = new Error(body.error || `Request failed (${response.status}).`);
    error.status = response.status;
    error.code = body.code || '';
    throw error;
  }
  return body;
}

function showMessage(text, isError = false) {
  elements.message.textContent = text;
  elements.message.classList.toggle('error', isError);
  elements.message.hidden = false;
}

function clearMessage() {
  elements.message.textContent = '';
  elements.message.classList.remove('error');
  elements.message.hidden = true;
}

function isFftRunning() {
  return currentStatus?.state === 'running' && currentStatus.visualization_id === 'fft';
}

function renderAuthorization() {
  const authorized = Boolean(authorizationStatus?.authorized);
  if (authorized && authorizationStatus.temporary_session) {
    elements.accessStatus.textContent = 'This phone is paired for music controls.';
  } else if (authorized) {
    elements.accessStatus.textContent = 'This browser has control access.';
  } else if (authorizationStatus === null) {
    elements.accessStatus.textContent = 'Checking this browser session…';
  } else {
    elements.accessStatus.textContent = 'Enter the six-digit code shown on the matrix.';
  }
  elements.forgetButton.hidden = !authorizationStatus?.temporary_session;
  elements.startButton.disabled = requestPending || !authorized || !fftDefinition || isFftRunning();
}

function renderStatus() {
  if (!currentStatus) return;
  const state = currentStatus.state || 'unknown';
  const fftRunning = isFftRunning();
  elements.statusDot.className = `status-dot ${state}`;

  if (fftRunning) {
    elements.currentName.textContent = 'Music visualization';
    elements.currentDetail.textContent = 'Running on the matrix now.';
  } else if (state === 'running') {
    elements.currentName.textContent = 'Another visualization is running';
    elements.currentDetail.textContent = 'Start music to switch the matrix to the audio visualizer.';
  } else if (state === 'failed') {
    elements.currentName.textContent = 'Visualization failed';
    elements.currentDetail.textContent = currentStatus.last_error || 'Check the service log for details.';
  } else {
    elements.currentName.textContent = 'Music visualization is stopped';
    elements.currentDetail.textContent = 'Start it to enable live tuning.';
  }

  elements.startButton.disabled = requestPending || !authorizationStatus?.authorized || !fftDefinition || fftRunning;
  elements.startButton.textContent = requestPending ? 'Working…' : state === 'running' ? 'Switch to music' : 'Start music';
  elements.quitButton.disabled = requestPending || !authorizationStatus?.authorized || !fftRunning;
  elements.quitLabel.textContent = requestPending ? 'Stopping…' : 'Quit music';
  elements.lastUpdated.textContent = `Updated ${new Intl.DateTimeFormat([], {
    hour: 'numeric', minute: '2-digit', second: '2-digit'
  }).format(new Date())}`;
}

function runtimeControls() {
  return Array.isArray(currentRuntime?.controls) ? currentRuntime.controls : [];
}

function valueAtPath(value, path) {
  if (!path) return undefined;
  return path.split('.').reduce((current, part) => {
    if (current === null || current === undefined || typeof current !== 'object') return undefined;
    return current[part];
  }, value);
}

function controlPath(control) {
  return control.path || control.id || control.name || '';
}

function controlLabel(control) {
  return control.label || control.name || controlPath(control).replaceAll('_', ' ');
}

function controlValue(control) {
  const stateValue = valueAtPath(currentRuntime?.state, controlPath(control));
  return stateValue === undefined ? control.value : stateValue;
}

function controlAvailable(control) {
  return control.available !== false && control.enabled !== false;
}

function valueFromInput(control, input) {
  const type = control.type || control.kind || 'string';
  if (type === 'bool' || type === 'boolean') return input.checked;
  if (type === 'int' || type === 'integer') return Number.parseInt(input.value, 10);
  if (type === 'float' || type === 'number') return Number.parseFloat(input.value);
  return input.value;
}

async function applyRuntimeChange(control, input) {
  const path = controlPath(control);
  if (!path || !currentRuntime || currentRuntime.run_id === null || currentRuntime.run_id === undefined) {
    showMessage('Live controls are not connected yet.', true);
    return;
  }
  const value = valueFromInput(control, input);
  if (typeof value === 'number' && !Number.isFinite(value)) {
    showMessage(`${controlLabel(control)} must be a number.`, true);
    renderControls();
    return;
  }

  requestPending = true;
  renderControls();
  renderStatus();
  try {
    const payload = {run_id: currentRuntime.run_id, changes: [{path, value}]};
    if (Number.isInteger(currentRuntime.revision)) payload.expected_revision = currentRuntime.revision;
    currentRuntime = await readJson(await fetch('/api/v1/runtime', {
      method: 'PATCH',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    }));
    clearMessage();
  } catch (error) {
    await recoverRuntimeError(error, `Could not change ${controlLabel(control)}`);
  } finally {
    requestPending = false;
    renderControls();
    renderStatus();
  }
}

async function invokeRuntimeAction(control) {
  if (!currentRuntime || currentRuntime.run_id === null || currentRuntime.run_id === undefined) return;
  requestPending = true;
  renderControls();
  renderStatus();
  try {
    const payload = {
      run_id: currentRuntime.run_id,
      action: control.action || controlPath(control),
    };
    if (control.target) payload.target = control.target;
    if (control.args) payload.args = control.args;
    if (Number.isInteger(currentRuntime.revision)) payload.expected_revision = currentRuntime.revision;
    currentRuntime = await readJson(await fetch('/api/v1/runtime/actions', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    }));
    clearMessage();
  } catch (error) {
    await recoverRuntimeError(error, `Could not run ${controlLabel(control)}`);
  } finally {
    requestPending = false;
    renderControls();
    renderStatus();
  }
}

async function recoverRuntimeError(error, prefix) {
  if (error.status === 401) {
    authorizationStatus = {authorized: false, temporary_session: false};
    renderAuthorization();
  }
  if (error.status === 409 || error.code === 'stale_run' || error.code === 'revision_conflict') {
    await refreshRuntime();
    showMessage('The visualization changed elsewhere. Controls were refreshed.', true);
  } else {
    showMessage(`${prefix}: ${error.message}`, true);
    await refreshRuntime();
  }
}

function createRuntimeControl(control) {
  const type = control.type || control.kind || 'string';
  const isAction = type === 'action' || Boolean(control.action);
  const wrapper = document.createElement(isAction ? 'div' : 'label');
  wrapper.className = `runtime-control${controlAvailable(control) ? '' : ' unavailable'}`;
  const title = document.createElement('span');
  title.className = 'runtime-control-label';
  title.textContent = controlLabel(control);
  const description = document.createElement('small');
  const mode = control.apply_mode || control.applyMode;
  description.textContent = `${control.description || ''}${mode && mode !== 'live' ? ` · ${mode} applies` : ''}`.trim();

  let input;
  if (isAction) {
    input = document.createElement('button');
    input.type = 'button';
    input.className = 'runtime-action-button';
    input.textContent = control.button_label || controlLabel(control);
    input.addEventListener('click', () => invokeRuntimeAction(control));
    wrapper.append(input, description);
  } else if (type === 'bool' || type === 'boolean') {
    input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = Boolean(controlValue(control));
    input.addEventListener('change', () => applyRuntimeChange(control, input));
    wrapper.append(input, title, description);
  } else if (type === 'enum' || Array.isArray(control.choices)) {
    input = document.createElement('select');
    (control.choices || []).forEach(choice => {
      const option = document.createElement('option');
      option.value = choice;
      option.textContent = choice;
      option.selected = choice === controlValue(control);
      input.append(option);
    });
    input.addEventListener('change', () => applyRuntimeChange(control, input));
    wrapper.append(title, input, description);
  } else {
    input = document.createElement('input');
    input.type = ['int', 'integer', 'float', 'number'].includes(type) ? 'number' : 'text';
    const value = controlValue(control);
    input.value = value === undefined || value === null ? '' : value;
    if (type === 'int' || type === 'integer') input.step = '1';
    if (type === 'float' || type === 'number') input.step = control.step || 'any';
    const minimum = control.minimum ?? control.min;
    const maximum = control.maximum ?? control.max;
    if (minimum !== undefined) input.min = String(minimum);
    if (maximum !== undefined) input.max = String(maximum);
    if (control.maximum_length !== undefined) input.maxLength = control.maximum_length;
    input.addEventListener('change', () => applyRuntimeChange(control, input));
    wrapper.append(title, input, description);
  }
  input.disabled = requestPending || !controlAvailable(control);
  input.dataset.runtimePath = controlPath(control);
  return wrapper;
}

function renderControls() {
  const controls = runtimeControls();
  const supported = isFftRunning() && currentRuntime?.supported !== false;
  const connected = supported && currentRuntime?.connected !== false && controls.length > 0;

  elements.liveConnection.textContent = connected ? 'Connected' : supported ? 'Connecting' : 'Offline';
  elements.liveConnection.className = `connection-badge ${connected ? 'connected' : 'offline'}`;
  elements.liveDetail.textContent = connected
    ? `Music visualization · revision ${currentRuntime.revision ?? '—'}`
    : isFftRunning()
      ? 'The music process is starting. Controls will appear automatically.'
      : 'Start the music visualization to load its controls.';
  elements.liveEmpty.hidden = connected;
  elements.liveGroups.replaceChildren();
  elements.liveGroups.setAttribute('aria-busy', String(isFftRunning() && !connected));
  if (!connected) return;

  const grouped = new Map();
  controls.forEach(control => {
    const group = control.group || control.scope || 'Visualization';
    if (!grouped.has(group)) grouped.set(group, []);
    grouped.get(group).push(control);
  });
  grouped.forEach((groupControls, groupName) => {
    const group = document.createElement('details');
    group.className = 'live-control-group';
    group.open = true;
    const summary = document.createElement('summary');
    summary.textContent = groupName;
    const fields = document.createElement('div');
    fields.className = 'runtime-control-fields';
    groupControls.forEach(control => fields.append(createRuntimeControl(control)));
    group.append(summary, fields);
    elements.liveGroups.append(group);
  });
}

async function refreshAuthorization() {
  try {
    authorizationStatus = await readJson(await fetch('/api/v1/pairing/status', {
      credentials: 'same-origin', cache: 'no-store',
    }));
  } catch (_error) {
    authorizationStatus = null;
  }
  renderAuthorization();
}

async function loadFftDefinition() {
  const body = await readJson(await fetch('/api/v1/visualizations', {cache: 'no-store'}));
  fftDefinition = (body.visualizations || []).find(item => item.id === 'fft') || null;
  if (!fftDefinition) showMessage('The music visualization is not installed on this controller.', true);
}

async function refreshRuntime() {
  if (!authorizationStatus?.authorized || !isFftRunning()) {
    currentRuntime = null;
    renderControls();
    return;
  }
  try {
    currentRuntime = await readJson(await fetch('/api/v1/runtime', {
      credentials: 'same-origin', cache: 'no-store',
    }));
  } catch (error) {
    currentRuntime = null;
    if (error.status === 401) {
      authorizationStatus = {authorized: false, temporary_session: false};
      renderAuthorization();
    }
  }
  renderControls();
}

async function refreshState({reportErrors = false} = {}) {
  try {
    currentStatus = await readJson(await fetch('/api/v1/status', {cache: 'no-store'}));
    renderStatus();
    await refreshRuntime();
  } catch (error) {
    elements.statusDot.className = 'status-dot failed';
    elements.currentName.textContent = 'Controller unavailable';
    elements.currentDetail.textContent = 'Trying again automatically.';
    if (reportErrors) showMessage(`Could not read controller status: ${error.message}`, true);
  }
}

async function startMusic() {
  if (!authorizationStatus?.authorized) {
    showMessage('Pair this phone before starting the music visualization.', true);
    elements.pairingCode.focus();
    return;
  }
  if (!fftDefinition) return;
  requestPending = true;
  clearMessage();
  renderStatus();
  try {
    const parameters = Object.fromEntries((fftDefinition.parameters || []).map(parameter => [parameter.name, parameter.default]));
    await readJson(await fetch('/api/v1/run', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({visualization_id: 'fft', parameters}),
    }));
    showMessage('Music visualization started. Connecting live controls…');
  } catch (error) {
    showMessage(`Could not start music: ${error.message}`, true);
  } finally {
    requestPending = false;
    await refreshState();
  }
}

async function stopMusic() {
  requestPending = true;
  clearMessage();
  renderStatus();
  try {
    await readJson(await fetch('/api/v1/stop', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: '{}',
    }));
    showMessage('Music visualization stopped.');
  } catch (error) {
    showMessage(`Could not stop music: ${error.message}`, true);
  } finally {
    requestPending = false;
    await refreshState();
  }
}

elements.pairingCode.addEventListener('input', () => {
  elements.pairingCode.value = elements.pairingCode.value.replace(/\D/g, '').slice(0, 6);
  clearMessage();
});
elements.pairingCode.addEventListener('keydown', event => {
  if (event.key === 'Enter') elements.pairButton.click();
});
elements.pairButton.addEventListener('click', async () => {
  const code = elements.pairingCode.value;
  if (!/^\d{6}$/.test(code)) {
    showMessage('Enter the complete six-digit pairing code.', true);
    elements.pairingCode.focus();
    return;
  }
  elements.pairButton.disabled = true;
  clearMessage();
  try {
    await readJson(await fetch('/api/v1/pairing/exchange', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-RGB-Matrix-Session': 'cookie',
      },
      body: JSON.stringify({code}),
    }));
    elements.pairingCode.value = '';
    await refreshAuthorization();
    await refreshState();
    showMessage('Phone paired. Music controls are ready.');
  } catch (error) {
    showMessage(`Could not pair this phone: ${error.message}`, true);
  } finally {
    elements.pairButton.disabled = false;
  }
});
elements.forgetButton.addEventListener('click', async () => {
  if (authorizationStatus?.temporary_session) {
    try {
      await readJson(await fetch('/api/v1/pairing/logout', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: '{}',
      }));
    } catch (error) {
      showMessage(`Could not remove access: ${error.message}`, true);
      return;
    }
  }
  authorizationStatus = {authorized: false, temporary_session: false};
  currentRuntime = null;
  renderAuthorization();
  renderControls();
  showMessage('Access was removed from this browser.');
});
elements.startButton.addEventListener('click', startMusic);
elements.quitButton.addEventListener('click', stopMusic);

async function initialize() {
  renderAuthorization();
  renderControls();
  try {
    await Promise.all([loadFftDefinition(), refreshAuthorization()]);
  } catch (error) {
    showMessage(`Could not load music controls: ${error.message}`, true);
  }
  await refreshState({reportErrors: true});
}

initialize();
setInterval(() => {
  if (!document.hidden && !requestPending) refreshState();
}, 1500);
