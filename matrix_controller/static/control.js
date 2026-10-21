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
    throw new Error(body.error || `Request failed (${response.status}).`);
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

    card.append(top, title, description, footer);
    elements.grid.append(card);
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
  renderVisualizations();
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
