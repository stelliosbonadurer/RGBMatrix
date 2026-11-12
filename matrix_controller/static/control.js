'use strict';

const elements = {
  grid: document.querySelector('#visualization-grid'),
  count: document.querySelector('#visualization-count'),
  currentName: document.querySelector('#current-name'),
  currentDetail: document.querySelector('#current-detail'),
  statusDot: document.querySelector('#status-dot'),
  quitButton: document.querySelector('#quit-button'),
  quitLabel: document.querySelector('#quit-button .button-label'),
  livePanel: document.querySelector('#live-controls-panel'),
  liveDetail: document.querySelector('#live-controls-detail'),
  liveConnection: document.querySelector('#live-controls-connection'),
  liveGroups: document.querySelector('#live-controls-groups'),
  pairingCode: document.querySelector('#pairing-code'),
  pairButton: document.querySelector('#pair-button'),
  accessStatus: document.querySelector('#access-status'),
  forgetButton: document.querySelector('#forget-button'),
  message: document.querySelector('#message'),
  lastUpdated: document.querySelector('#last-updated'),
};

let visualizations = [];
let currentStatus = null;
let currentRuntime = null;
let stopPending = false;
let launchPendingId = null;
let runtimeCommandPending = false;
let expandedVisualizationId = null;
let authorizationStatus = null;

elements.pairingCode.addEventListener('input', () => {
  elements.pairingCode.value = elements.pairingCode.value.replace(/\D/g, '').slice(0, 6);
  clearMessage();
});

elements.pairingCode.addEventListener('keydown', event => {
  if (event.key === 'Enter') elements.pairButton.click();
});

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
  elements.message.hidden = true;
  elements.message.classList.remove('error');
}

function renderAuthorization() {
  const authorized = Boolean(authorizationStatus?.authorized);
  if (authorized && authorizationStatus.temporary_session) {
    elements.accessStatus.textContent = 'This phone is paired for visualization controls.';
  } else if (authorized) {
    elements.accessStatus.textContent = 'This browser has control access.';
  } else if (authorizationStatus === null) {
    elements.accessStatus.textContent = 'Checking this browser session…';
  } else {
    elements.accessStatus.textContent = 'Enter the six-digit code shown on the matrix.';
  }
  elements.forgetButton.hidden = !authorizationStatus?.temporary_session;
}

async function refreshAuthorization() {
  try {
    authorizationStatus = await readJson(await fetch('/api/v1/pairing/status', {
      credentials: 'same-origin',
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
  wrapper.dataset.parameterName = parameter.name;

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

function configureGraphTraversalForm(item, form) {
  if (item.id !== 'graph_traversal') return;

  const control = name => form.elements.namedItem(name);
  const wrapper = name => form.querySelector(`[data-parameter-name="${name}"]`);
  const nInput = control('n');
  const eInput = control('e');
  const connectedInput = control('connected');
  const cyclesInput = control('allow_cycles');
  const displayMode = control('display_mode');
  const traversalInput = control('traverse');
  const directedInput = control('directed');
  const ensurePathInput = control('ensure_path');
  const directedConnectivity = control('directed_connectivity');
  const componentType = control('component_type');
  const launchButton = form.querySelector('.launch-button');
  if (!nInput || !eInput || !connectedInput || !cyclesInput || !displayMode ||
      !traversalInput || !directedInput || !ensurePathInput || !directedConnectivity ||
      !componentType) return;

  const edgeDescription = wrapper('e')?.querySelector('small');
  let previousMst = false;

  const setOptionAvailable = (select, value, available) => {
    const option = Array.from(select.options).find(candidate => candidate.value === value);
    if (option) option.disabled = !available;
  };

  const update = () => {
    const n = Number.parseInt(nInput.value, 10);
    const split = displayMode.value !== 'single';
    const mst = displayMode.value === 'prim_kruskal_split' || (
      !split && (traversalInput.value === 'prim' || traversalInput.value === 'kruskal')
    );
    if (mst && !previousMst) cyclesInput.checked = true;
    previousMst = mst;
    if (mst) {
      directedInput.checked = false;
      connectedInput.checked = true;
    }
    const directed = directedInput.checked;
    const allowCycles = cyclesInput.checked;
    const connected = connectedInput.checked;
    const traversingComponents = !split && traversalInput.value === 'components';

    wrapper('traverse').hidden = split;
    wrapper('connected').hidden = directed || mst;
    wrapper('directed').hidden = mst;
    wrapper('ensure_path').hidden = !directed || traversingComponents;
    wrapper('directed_connectivity').hidden = !directed;
    wrapper('component_type').hidden = !traversingComponents;
    wrapper('step_mode').hidden = mst;

    const strongGraphAvailable = directed && allowCycles && n >= 3;
    setOptionAvailable(directedConnectivity, 'strong', strongGraphAvailable);
    if (directedConnectivity.value === 'strong' && !strongGraphAvailable) {
      directedConnectivity.value = 'weak';
    }

    setOptionAvailable(componentType, 'strong', directed);
    if (componentType.value === 'strong' && !directed) componentType.value = 'weak';

    if (!Number.isInteger(n) || n < 2 || n > 32) {
      eInput.setCustomValidity('Choose n from 2 through 32 first.');
      if (edgeDescription) edgeDescription.textContent = 'Choose a valid node count to calculate edge limits.';
      if (launchButton) launchButton.disabled = true;
      return;
    }

    const guaranteed = directed
      ? directedConnectivity.value === 'strong' || directedConnectivity.value === 'weak'
      : connected;
    const strong = directed && directedConnectivity.value === 'strong';
    const maximum = allowCycles || directed ? (n === 2 ? 1 : 3 * n - 6) : n - 1;
    const connectivityMinimum = strong ? n : guaranteed ? n - 1 : 0;
    const pathMinimum = directed && !traversingComponents && ensurePathInput.checked ? 1 : 0;
    const minimum = Math.max(connectivityMinimum, pathMinimum);
    const automatic = allowCycles || directed
      ? Math.min(Math.max(Math.ceil(n * 1.5), minimum), maximum)
      : n - 1;
    const edgeCount = Number.parseInt(eInput.value, 10);

    eInput.max = String(maximum);
    const validEdgeCount = edgeCount === 0 || (
      Number.isInteger(edgeCount) && edgeCount >= minimum && edgeCount <= maximum
    );
    eInput.setCustomValidity(
      validEdgeCount ? '' : `Use 0 for automatic, or choose ${minimum} through ${maximum} edges.`
    );
    if (edgeDescription) {
      edgeDescription.textContent = `0 = automatic (${automatic}). Explicit edge range: ${minimum}–${maximum}.`;
    }
    if (launchButton) launchButton.disabled = launchPendingId === item.id || !form.checkValidity();
  };

  form.addEventListener('input', update);
  form.addEventListener('change', update);
  update();
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
  if (!authorizationStatus?.authorized) {
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
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({visualization_id: item.id, parameters}),
    }));
    expandedVisualizationId = null;
    showMessage(`${item.name} is now running on the matrix.`);
  } catch (error) {
    if (error.status === 401) {
      authorizationStatus = {authorized: false, temporary_session: false};
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

    const cardActions = document.createElement('div');
    cardActions.className = 'card-actions';

    // FFT has enough live controls to deserve a focused screen. Keep launch-time
    // settings available here, but make the primary Configure action navigate to
    // the dedicated music page instead of opening an easy-to-miss inline panel.
    if (item.id === 'fft') {
      const liveConfigureLink = document.createElement('a');
      liveConfigureLink.className = 'configure-button primary-configure';
      liveConfigureLink.href = '/config/fft';
      liveConfigureLink.textContent = 'Configure';
      cardActions.append(liveConfigureLink);
    }

    const launchSettingsButton = document.createElement('button');
    launchSettingsButton.className = 'configure-button';
    launchSettingsButton.type = 'button';
    launchSettingsButton.textContent = expandedVisualizationId === item.id
      ? 'Close launch settings'
      : item.id === 'fft' ? 'Launch settings' : 'Configure';
    launchSettingsButton.setAttribute('aria-expanded', String(expandedVisualizationId === item.id));
    launchSettingsButton.setAttribute('aria-controls', `launch-form-${item.id}`);
    launchSettingsButton.addEventListener('click', () => {
      expandedVisualizationId = expandedVisualizationId === item.id ? null : item.id;
      renderVisualizations();
    });
    cardActions.append(launchSettingsButton);

    const form = document.createElement('form');
    form.id = `launch-form-${item.id}`;
    form.className = 'launch-form';
    form.hidden = expandedVisualizationId !== item.id;

    let applicationParameters = (item.parameters || []).filter(parameter => !parameter.name.startsWith('led_'));
    if (item.id === 'graph_traversal') {
      applicationParameters = [
        ...applicationParameters.filter(parameter => !['n', 'e'].includes(parameter.name)),
        ...applicationParameters.filter(parameter => ['n', 'e'].includes(parameter.name)),
      ];
    }
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
    configureGraphTraversalForm(item, form);
    form.addEventListener('submit', event => {
      event.preventDefault();
      launchVisualization(item, form);
    });

    card.append(top, title, description, footer, cardActions, form);
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

function runtimeControlPath(control) {
  return control.path || control.id || control.name || '';
}

function runtimeControlValue(control) {
  const path = runtimeControlPath(control);
  const stateValue = valueAtPath(currentRuntime?.state, path);
  return stateValue === undefined ? control.value : stateValue;
}

function runtimeControlAvailable(control) {
  return control.available !== false && control.enabled !== false;
}

function runtimeControlLabel(control) {
  return control.label || control.name || runtimeControlPath(control).replaceAll('_', ' ');
}

function runtimeControlDescription(control) {
  const mode = control.apply_mode || control.applyMode;
  const suffix = mode && mode !== 'live' ? ` · ${mode} applies` : '';
  return `${control.description || ''}${suffix}`.trim();
}

function runtimeValueForInput(control, input) {
  if (control.type === 'bool' || control.type === 'boolean') return input.checked;
  if (control.type === 'int' || control.type === 'integer') return Number.parseInt(input.value, 10);
  if (control.type === 'float' || control.type === 'number') return Number.parseFloat(input.value);
  return input.value;
}

async function applyRuntimeChange(control, input) {
  const path = runtimeControlPath(control);
  if (!path || !currentRuntime || currentRuntime.run_id === null || currentRuntime.run_id === undefined) {
    showMessage('Live controls are not connected to the active visualization.', true);
    return;
  }
  const value = runtimeValueForInput(control, input);
  if ((control.type === 'int' || control.type === 'integer' || control.type === 'float' || control.type === 'number') && !Number.isFinite(value)) {
    showMessage(`${runtimeControlLabel(control)} must be a number.`, true);
    renderLiveControls();
    return;
  }
  runtimeCommandPending = true;
  renderLiveControls();
  try {
    const payload = {
      run_id: currentRuntime.run_id,
      changes: [{path, value}],
    };
    if (Number.isInteger(currentRuntime.revision)) payload.expected_revision = currentRuntime.revision;
    currentRuntime = await readJson(await fetch('/api/v1/runtime', {
      method: 'PATCH',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    }));
    clearMessage();
    renderLiveControls();
  } catch (error) {
    if (error.status === 401) {
      authorizationStatus = null;
      renderAuthorization();
    }
    if (error.status === 409 || error.code === 'stale_run' || error.code === 'revision_conflict') {
      await refreshRuntime({reportErrors: false});
      showMessage('The visualization changed elsewhere. Live controls were refreshed.', true);
    } else if (error.status === 501 || error.code === 'unsupported') {
      await refreshRuntime({reportErrors: false});
      showMessage('This visualization does not support live controls yet.', true);
    } else {
      showMessage(`Could not change ${runtimeControlLabel(control)}: ${error.message}`, true);
      await refreshRuntime({reportErrors: false});
    }
  } finally {
    runtimeCommandPending = false;
    renderLiveControls();
  }
}

async function invokeRuntimeAction(control) {
  if (!currentRuntime || currentRuntime.run_id === null || currentRuntime.run_id === undefined) return;
  runtimeCommandPending = true;
  renderLiveControls();
  try {
    const payload = {
      run_id: currentRuntime.run_id,
      action: control.action || runtimeControlPath(control),
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
    if (error.status === 409 || error.code === 'stale_run' || error.code === 'revision_conflict') {
      await refreshRuntime({reportErrors: false});
      showMessage('The visualization changed elsewhere. Live controls were refreshed.', true);
    } else {
      showMessage(`Could not run ${runtimeControlLabel(control)}: ${error.message}`, true);
      await refreshRuntime({reportErrors: false});
    }
  } finally {
    runtimeCommandPending = false;
    renderLiveControls();
  }
}

function createRuntimeControl(control) {
  const path = runtimeControlPath(control);
  const wrapper = document.createElement('label');
  wrapper.className = `runtime-control${runtimeControlAvailable(control) ? '' : ' unavailable'}`;
  const title = document.createElement('span');
  title.className = 'runtime-control-label';
  title.textContent = runtimeControlLabel(control);
  const description = document.createElement('small');
  description.textContent = runtimeControlDescription(control);
  const type = control.type || control.kind || 'string';
  const isAction = type === 'action' || Boolean(control.action);
  let input;
  if (isAction) {
    input = document.createElement('button');
    input.type = 'button';
    input.className = 'runtime-action-button';
    input.textContent = control.button_label || runtimeControlLabel(control);
    input.addEventListener('click', () => invokeRuntimeAction(control));
    wrapper.append(input, description);
  } else if (type === 'bool' || type === 'boolean') {
    input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = Boolean(runtimeControlValue(control));
    input.addEventListener('change', () => applyRuntimeChange(control, input));
    wrapper.append(input, title, description);
  } else if (type === 'enum' || Array.isArray(control.choices)) {
    input = document.createElement('select');
    (control.choices || []).forEach(choice => {
      const option = document.createElement('option');
      option.value = choice;
      option.textContent = choice;
      option.selected = choice === runtimeControlValue(control);
      input.append(option);
    });
    input.addEventListener('change', () => applyRuntimeChange(control, input));
    wrapper.append(title, input, description);
  } else {
    input = document.createElement('input');
    input.type = type === 'int' || type === 'integer' || type === 'float' || type === 'number' ? 'number' : 'text';
    const value = runtimeControlValue(control);
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
  input.disabled = runtimeCommandPending || !runtimeControlAvailable(control);
  input.dataset.runtimePath = path;
  return wrapper;
}

function renderLiveControls() {
  if (!elements.livePanel) return;
  const controls = runtimeControls();
  const supported = Boolean(currentRuntime?.visualization_id)
    && currentRuntime?.supported !== false
    && (currentRuntime?.supported === true || controls.length > 0);
  const connected = supported && currentRuntime.connected !== false;
  elements.livePanel.hidden = !supported;
  elements.liveConnection.textContent = connected ? 'Connected' : 'Offline';
  elements.liveConnection.className = `connection-badge ${connected ? 'connected' : 'offline'}`;
  elements.liveDetail.textContent = supported
    ? `${visualizationName(currentRuntime.visualization_id)} · revision ${currentRuntime.revision ?? '—'}`
    : 'The active visualization does not expose live controls.';
  elements.liveGroups.replaceChildren();
  elements.liveGroups.setAttribute('aria-busy', 'false');
  if (!supported || controls.length === 0) return;
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

async function refreshRuntime({reportErrors = false} = {}) {
  if (!authorizationStatus?.authorized) {
    currentRuntime = null;
    renderLiveControls();
    return;
  }
  try {
    currentRuntime = await readJson(await fetch('/api/v1/runtime', {
      credentials: 'same-origin',
      cache: 'no-store',
    }));
    renderLiveControls();
  } catch (error) {
    currentRuntime = null;
    renderLiveControls();
    if (reportErrors && error.status !== 401) {
      showMessage(`Could not read live controls: ${error.message}`, true);
    }
  }
}

async function refreshStatus({reportErrors = false} = {}) {
  try {
    currentStatus = await readJson(await fetch('/api/v1/status', {cache: 'no-store'}));
    renderStatus();
    await refreshRuntime({reportErrors: false});
  } catch (error) {
    elements.statusDot.className = 'status-dot failed';
    elements.currentName.textContent = 'Controller unavailable';
    elements.currentDetail.textContent = 'Trying again automatically.';
    elements.quitButton.disabled = true;
    currentRuntime = null;
    renderLiveControls();
    if (reportErrors) showMessage(`Could not read status: ${error.message}`, true);
  }
}

elements.quitButton.addEventListener('click', async () => {
  if (!authorizationStatus?.authorized) {
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
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
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
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-RGB-Matrix-Session': 'cookie',
      },
      body: JSON.stringify({code}),
    }));
    elements.pairingCode.value = '';
    await refreshAuthorization();
    await refreshRuntime({reportErrors: false});
    showMessage(`Phone paired. Access expires in ${Math.round(result.expires_in / 3600)} hours or when the service restarts. The displayed code can pair another device.`);
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
  renderLiveControls();
  showMessage('Access was removed from this browser.');
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
  if (!document.hidden && !stopPending && !runtimeCommandPending) refreshStatus();
}, 1500);
