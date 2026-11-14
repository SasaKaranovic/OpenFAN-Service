// Thin wrapper around fetch for the service API.
let onLoginRequired = () => {};
export function setLoginHandler(fn) { onLoginRequired = fn; }

export class ApiError extends Error {}

async function request(method, path, body, raw = false) {
  const opts = { method, headers: {}, credentials: 'same-origin' };
  if (body !== undefined) {
    if (raw) { opts.body = body; opts.headers['Content-Type'] = 'text/plain'; }
    else { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
  }
  let res;
  try {
    // Relative URL so the UI also works behind a reverse proxy on a sub-path.
    res = await fetch(path.replace(/^\//, ''), opts);
  } catch (e) {
    throw new ApiError('Cannot reach the OpenFAN service. Is the container running?');
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* not JSON */ }
  if (res.status === 401 && data && data.login) {
    onLoginRequired();
    throw new ApiError('Login required');
  }
  if (!res.ok || !data || data.ok === false) {
    throw new ApiError((data && data.error) || `Request failed (${res.status})`);
  }
  return data.data;
}

export const api = {
  get: (p) => request('GET', p),
  post: (p, b = {}) => request('POST', p, b),
  put: (p, b = {}) => request('PUT', p, b),
  del: (p) => request('DELETE', p),
  postText: (p, text) => request('POST', p, text, true),
};
