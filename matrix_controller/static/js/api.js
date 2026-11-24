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

export const api = {
  visualizations: () => request('GET', '/api/v1/visualizations'),
  status: () => request('GET', '/api/v1/status'),
  matrixSettings: () => request('GET', '/api/v1/matrix-settings'),
  saveMatrixSettings: (expectedRevision, values) =>
    request('PUT', '/api/v1/matrix-settings', {expected_revision: expectedRevision, values}),
  run: (visualizationId, parameters) =>
    request('POST', '/api/v1/run', {visualization_id: visualizationId, parameters}),
  stop: () => request('POST', '/api/v1/stop', {}),
};
