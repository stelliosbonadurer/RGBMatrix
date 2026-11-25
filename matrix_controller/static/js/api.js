// Requests to the controller. Every call reports failure as ApiError with the
// server's message, so pages can show it next to the action that failed.

export class ApiError extends Error {
  constructor(message, {status = 0, code = '', body = {}} = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.body = body;
  }
}

async function request(method, path, body) {
  const init = {method, credentials: 'same-origin', cache: 'no-store', headers: {}};
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (_error) {
    throw new ApiError("Can't reach the controller. Check that this phone is on the same network.");
  }
  let data;
  try {
    data = await response.json();
  } catch (_error) {
    throw new ApiError(`The controller sent an unreadable reply (${response.status}).`, {status: response.status});
  }
  if (!response.ok) {
    let message = data.error || `Request failed (${response.status}).`;
    if (response.status === 401) {
      // D-036: the pages have no pairing UI; only a controller with pairing
      // switched back on answers 401.
      message = "This controller requires pairing, which these pages don't support yet.";
    }
    throw new ApiError(message, {status: response.status, code: data.code || '', body: data});
  }
  return data;
}

const filePath = (visualizationId, name) =>
  `/api/v1/programs/${encodeURIComponent(visualizationId)}/files/${encodeURIComponent(name)}`;

export const api = {
  visualizations: () => request('GET', '/api/v1/visualizations'),
  status: () => request('GET', '/api/v1/status'),
  matrixSettings: () => request('GET', '/api/v1/matrix-settings'),
  saveMatrixSettings: (expectedRevision, values) =>
    request('PUT', '/api/v1/matrix-settings', {expected_revision: expectedRevision, values}),
  programFile: (visualizationId, name) => request('GET', filePath(visualizationId, name)),
  saveProgramFile: (visualizationId, name, expectedRevision, document) =>
    request('PUT', filePath(visualizationId, name), {expected_revision: expectedRevision, document}),
  // Only clears a saved file the program can't use (D-047).
  removeProgramFile: (visualizationId, name) => request('DELETE', filePath(visualizationId, name)),
  // `structure` (Music FFT only) is the layer structure to launch with (D-016).
  run: (visualizationId, parameters, structure) =>
    request('POST', '/api/v1/run', structure === undefined
      ? {visualization_id: visualizationId, parameters}
      : {visualization_id: visualizationId, parameters, structure}),
  // Music FFT (M5): the layer catalog while stopped (D-049), the running
  // snapshot, and structure.set.
  fftCatalog: () => request('GET', '/static/fft_catalog.json'),
  runtime: () => request('GET', '/api/v1/runtime'),
  runtimeAction: (runId, expectedRevision, action, args) =>
    request('POST', '/api/v1/runtime/actions', {run_id: runId, expected_revision: expectedRevision, action, args}),
  stop: () => request('POST', '/api/v1/stop', {}),
};
