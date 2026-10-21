'use strict';

const elements = {
  grid: document.querySelector('#visualization-grid'),
  count: document.querySelector('#visualization-count'),
  currentName: document.querySelector('#current-name'),
  currentDetail: document.querySelector('#current-detail'),
  statusDot: document.querySelector('#status-dot'),
  quitButton: document.querySelector('#quit-button'),
  quitLabel: document.querySelector('#quit-button .button-label'),
  pairingCode: document.querySelector('#pairing-code'),
  pairButton: document.querySelector('#pair-button'),
  accessStatus: document.querySelector('#access-status'),
  forgetButton: document.querySelector('#forget-button'),
  message: document.querySelector('#message'),
  lastUpdated: document.querySelector('#last-updated'),
};

let visualizations = [];
let currentStatus = null;
let stopPending = false;
let launchPendingId = null;
let expandedVisualizationId = null;
let authorizationStatus = null;
let controlToken = '';

// Session storage is intentionally used instead of local storage. The bearer token
// survives an accidental refresh but disappears when this browser tab is closed.
try {
  controlToken = sessionStorage.getItem('rgb-matrix-api-token') || '';
} catch (_error) {
  // Privacy modes may disable storage; the page remains usable with the input alone.
}

elements.pairingCode.addEventListener('input', () => {
  elements.pairingCode.value = elements.pairingCode.value.replace(/\D/g, '').slice(0, 6);
  clearMessage();
});

elements.pairingCode.addEventListener('keydown', event => {
  if (event.key === 'Enter') elements.pairButton.click();
});

function storeToken(token) {
  try {
    if (token) {
      sessionStorage.setItem('rgb-matrix-api-token', token);
    } else {
      sessionStorage.removeItem('rgb-matrix-api-token');
    }
  } catch (_error) {
    // Do not block controls when browser storage is unavailable.
  }
}

function authorizationHeaders() {
  return controlToken ? {'Authorization': `Bearer ${controlToken}`} : {};
}

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
  elements.message.hidden = true;
  elements.message.classList.remove('error');
}

function renderAuthorization() {
  const hasToken = Boolean(controlToken);
  const authorized = Boolean(authorizationStatus?.authorized);
  if (authorized && authorizationStatus.temporary_session) {
    elements.accessStatus.textContent = 'This phone is paired for visualization controls.';
  } else if (authorized) {
    elements.accessStatus.textContent = 'This browser has control access.';
  } else if (hasToken && authorizationStatus !== null) {
    elements.accessStatus.textContent = 'This browser session is no longer valid. Enter the code again.';
  } else {
    elements.accessStatus.textContent = 'Enter the six-digit code shown on the matrix.';
  }
  elements.forgetButton.hidden = !hasToken;
}

async function refreshAuthorization() {
  try {
    authorizationStatus = await readJson(await fetch('/api/v1/pairing/status', {
      headers: authorizationHeaders(),
      cache: 'no-store',
    }));
  } catch (_error) {
    authorizationStatus = null;
  }
  renderAuthorization();
}

function visualizationName(id) {
  return visualizations.find(item => item.id === id)?.name || id || 'No visualization running';
}

function parameterValue(item, parameter) {
  if (
    currentStatus?.visualization_id === item.id &&
    currentStatus.parameters &&
    Object.prototype.hasOwnProperty.call(currentStatus.parameters, parameter.name)
  ) {
    return currentStatus.parameters[parameter.name];
  }
  return parameter.default;
}

function createParameterField(item, parameter) {
  const wrapper = document.createElement('label');
  wrapper.className = `parameter-field${parameter.type === 'bool' ? ' boolean-field' : ''}`;

  const label = document.createElement('span');
  label.className = 'parameter-label';
  label.textContent = parameter.name.replaceAll('_', ' ');

  const description = document.createElement('small');
  description.textContent = parameter.description || '';

  const value = parameterValue(item, parameter);
  let control;
  if (parameter.type === 'bool') {
    control = document.createElement('input');
    control.type = 'checkbox';
    control.checked = Boolean(value);
    wrapper.append(control, label, description);
  } else if (parameter.type === 'enum') {
    control = document.createElement('select');
    control.required = true;
    (parameter.choices || []).forEach(choice => {
      const option = document.createElement('option');
      option.value = choice;
      option.textContent = choice;
      option.selected = choice === value;
      control.append(option);
    });
    wrapper.append(label, control, description);
  } else {
    control = document.createElement('input');
    control.type = parameter.type === 'int' || parameter.type === 'float' ? 'number' : 'text';
    control.value = value ?? '';
    if (parameter.type === 'int') {
      control.step = '1';
      control.required = true;
    }
    if (parameter.type === 'float') {
      control.step = 'any';
      control.required = true;
    }
    if (parameter.minimum !== undefined) control.min = String(parameter.minimum);
    if (parameter.maximum !== undefined) control.max = String(parameter.maximum);
    if (parameter.maximum_length !== undefined) control.maxLength = parameter.maximum_length;
    wrapper.append(label, control, description);
  }

  control.name = parameter.name;
  control.dataset.parameterType = parameter.type;
  control.disabled = launchPendingId === item.id;
  return wrapper;
}

function readLaunchParameters(form, item) {
  const parameters = {};
  for (const parameter of item.parameters || []) {
    const control = form.elements.namedItem(parameter.name);
    if (!control) throw new Error(`Missing control for ${parameter.name}.`);
    if (parameter.type === 'bool') {
      parameters[parameter.name] = control.checked;
    } else if (parameter.type === 'int') {
      parameters[parameter.name] = Number.parseInt(control.value, 10);
    } else if (parameter.type === 'float') {
      parameters[parameter.name] = Number.parseFloat(control.value);
    } else {
      parameters[parameter.name] = control.value;
    }
  }
  return parameters;
}

async function launchVisualization(item, form) {
  if (!controlToken) {
    showMessage('Enter the six-digit pairing code before launching a visualization.', true);
    elements.pairingCode.focus();
    return;
  }
  if (!form.reportValidity()) return;

  launchPendingId = item.id;
  clearMessage();
  renderVisualizations();
  try {
    const parameters = readLaunchParameters(form, item);
    await readJson(await fetch('/api/v1/run', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${controlToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({visualization_id: item.id, parameters}),
    }));
    expandedVisualizationId = null;
    showMessage(`${item.name} is now running on the matrix.`);
  } catch (error) {
    if (error.status === 401) {
      authorizationStatus = null;
      renderAuthorization();
      elements.pairingCode.focus();
    }
    showMessage(`Could not launch ${item.name}: ${error.message}`, true);
  } finally {
    launchPendingId = null;
    await refreshStatus();
    renderVisualizations();
  }
}

function renderVisualizations() {
  elements.grid.replaceChildren();
  elements.grid.setAttribute('aria-busy', 'false');
  elements.count.textContent = String(visualizations.length);

  if (visualizations.length === 0) {
    const empty = document.createElement('p');
    empty.textContent = 'No visualizations are currently installed.';
    elements.grid.append(empty);
    return;
  }

  visualizations.forEach((item, index) => {
    const active = currentStatus?.visualization_id === item.id;
    const card = document.createElement('article');
    card.className = `visualization-card${index % 2 === 0 ? '' : ' accent-cyan'}`;
    card.dataset.active = String(active);
    card.dataset.visualizationId = item.id;

    const top = document.createElement('div');
    top.className = 'card-top';
    const icon = document.createElement('span');
    icon.className = 'card-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = item.id === 'fft' ? '♪' : item.id === 'sorts' ? '⇅' : '◆';
    const badge = document.createElement('span');
    badge.className = `card-badge${active ? ' active' : ''}`;
    badge.textContent = active ? 'Running' : 'Available';
    top.append(icon, badge);

    const title = document.createElement('h3');
    title.textContent = item.name;
    const description = document.createElement('p');
    description.textContent = item.description;

    const footer = document.createElement('div');
    footer.className = 'card-footer';
    const parameterCount = document.createElement('span');
    const count = Array.isArray(item.parameters) ? item.parameters.length : 0;
    parameterCount.textContent = `${count} configurable parameter${count === 1 ? '' : 's'}`;
    footer.append(parameterCount);
    if (item.requires_audio) {
      const audio = document.createElement('span');
      audio.className = 'audio-label';
      audio.textContent = 'Audio input';
      footer.append(audio);
    }

    const configureButton = document.createElement('button');
    configureButton.className = 'configure-button';
    configureButton.type = 'button';
    configureButton.textContent = expandedVisualizationId === item.id ? 'Close controls' : 'Configure';
    configureButton.setAttribute('aria-expanded', String(expandedVisualizationId === item.id));
    configureButton.setAttribute('aria-controls', `launch-form-${item.id}`);
    configureButton.addEventListener('click', () => {
      expandedVisualizationId = expandedVisualizationId === item.id ? null : item.id;
      renderVisualizations();
    });

    const form = document.createElement('form');
    form.id = `launch-form-${item.id}`;
    form.className = 'launch-form';
    form.hidden = expandedVisualizationId !== item.id;

    const applicationParameters = (item.parameters || []).filter(parameter => !parameter.name.startsWith('led_'));
    const matrixParameters = (item.parameters || []).filter(parameter => parameter.name.startsWith('led_'));
    const addGroup = (name, parameters, open) => {
      if (parameters.length === 0) return;
      const group = document.createElement('details');
      group.className = 'parameter-group';
      group.open = open;
      const summary = document.createElement('summary');
      summary.textContent = `${name} (${parameters.length})`;
      const fields = document.createElement('div');
      fields.className = 'parameter-fields';
      parameters.forEach(parameter => fields.append(createParameterField(item, parameter)));
      group.append(summary, fields);
      form.append(group);
    };
    addGroup('Visualization settings', applicationParameters, true);
    addGroup('Matrix hardware', matrixParameters, applicationParameters.length === 0);

    const launchButton = document.createElement('button');
    launchButton.className = 'launch-button';
    launchButton.type = 'submit';
    launchButton.disabled = launchPendingId === item.id;
    launchButton.textContent = launchPendingId === item.id
      ? 'Launching…'
      : active ? 'Restart with these settings' : 'Launch visualization';
    form.append(launchButton);
    form.addEventListener('submit', event => {
      event.preventDefault();
      launchVisualization(item, form);
    });

    card.append(top, title, description, footer, configureButton, form);
    elements.grid.append(card);
  });
}

function updateCardStates() {
  elements.grid.querySelectorAll('.visualization-card[data-visualization-id]').forEach(card => {
    const active = currentStatus?.visualization_id === card.dataset.visualizationId;
    card.dataset.active = String(active);
    const badge = card.querySelector('.card-badge');
    if (badge) {
      badge.classList.toggle('active', active);
      badge.textContent = active ? 'Running' : 'Available';
    }
  });
}

function renderStatus() {
  if (currentStatus === null) return;

  const state = currentStatus.state || 'unknown';
  const active = Boolean(currentStatus.visualization_id);
  elements.statusDot.className = `status-dot ${state}`;
  elements.currentName.textContent = visualizationName(currentStatus.visualization_id);

  if (state === 'running') {
    elements.currentDetail.textContent = 'Running on the matrix now.';
  } else if (state === 'failed') {
    elements.currentDetail.textContent = currentStatus.last_error || 'The last visualization stopped unexpectedly.';
  } else if (state === 'idle') {
    elements.currentDetail.textContent = 'The display is ready for a visualization.';
  } else {
    elements.currentDetail.textContent = `${state.charAt(0).toUpperCase()}${state.slice(1)}…`;
  }

  elements.quitButton.disabled = stopPending || !active;
  elements.quitButton.setAttribute('aria-busy', String(stopPending));
  elements.quitLabel.textContent = stopPending ? 'Stopping…' : 'Quit current';
  elements.lastUpdated.textContent = `Updated ${new Intl.DateTimeFormat([], {
    hour: 'numeric', minute: '2-digit', second: '2-digit'
  }).format(new Date())}`;
  updateCardStates();
}

async function loadVisualizations() {
  try {
    const body = await readJson(await fetch('/api/v1/visualizations', {cache: 'no-store'}));
    visualizations = Array.isArray(body.visualizations) ? body.visualizations : [];
    renderVisualizations();
  } catch (error) {
    elements.grid.setAttribute('aria-busy', 'false');
    elements.grid.replaceChildren();
    elements.count.textContent = '!';
    showMessage(`Could not load visualizations: ${error.message}`, true);
  }
}

async function refreshStatus({reportErrors = false} = {}) {
  try {
    currentStatus = await readJson(await fetch('/api/v1/status', {cache: 'no-store'}));
    renderStatus();
  } catch (error) {
    elements.statusDot.className = 'status-dot failed';
    elements.currentName.textContent = 'Controller unavailable';
    elements.currentDetail.textContent = 'Trying again automatically.';
    elements.quitButton.disabled = true;
    if (reportErrors) showMessage(`Could not read status: ${error.message}`, true);
  }
}

elements.quitButton.addEventListener('click', async () => {
  if (!controlToken) {
    showMessage('Enter the six-digit pairing code before quitting the visualization.', true);
    elements.pairingCode.focus();
    return;
  }

  stopPending = true;
  clearMessage();
  renderStatus();
  try {
    currentStatus = await readJson(await fetch('/api/v1/stop', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${controlToken}`,
        'Content-Type': 'application/json',
      },
      body: '{}',
    }));
    showMessage('The visualization was stopped and the matrix is ready.');
  } catch (error) {
    showMessage(`Could not stop the visualization: ${error.message}`, true);
  } finally {
    stopPending = false;
    await refreshStatus();
  }
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
    const result = await readJson(await fetch('/api/v1/pairing/exchange', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({code}),
    }));
    controlToken = result.token;
    storeToken(result.token);
    elements.pairingCode.value = '';
    await refreshAuthorization();
    showMessage(`Phone paired. Access expires in ${Math.round(result.expires_in / 3600)} hours or when the service restarts. The displayed code can pair another device.`);
  } catch (error) {
    showMessage(`Could not pair this phone: ${error.message}`, true);
  } finally {
    elements.pairButton.disabled = false;
  }
});

elements.forgetButton.addEventListener('click', async () => {
  if (controlToken && authorizationStatus?.temporary_session) {
    try {
      await readJson(await fetch('/api/v1/pairing/logout', {
        method: 'POST',
        headers: {...authorizationHeaders(), 'Content-Type': 'application/json'},
        body: '{}',
      }));
    } catch (_error) {
      // Local removal still protects this browser tab if the controller is offline.
    }
  }
  controlToken = '';
  storeToken('');
  authorizationStatus = null;
  renderAuthorization();
  showMessage('Access was removed from this browser tab.');
});

async function initialize() {
  renderAuthorization();
  await Promise.all([
    loadVisualizations(),
    refreshStatus({reportErrors: true}),
    refreshAuthorization(),
  ]);
}

initialize();
setInterval(() => {
  if (!document.hidden && !stopPending) refreshStatus();
}, 3000);
