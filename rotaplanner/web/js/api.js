// Talks to the server. Every call carries the session token, which the
// server checks so that no other web page can use this API.

const TOKEN_KEY = 'rota-planner-token';

let unproven = false;  // a token from the address is kept only once the server accepted it

function readToken() {
  const url = new URL(window.location.href);
  const fromUrl = url.searchParams.get('t');
  if (fromUrl) {
    unproven = true;
    url.searchParams.delete('t');
    history.replaceState(null, '', url.pathname + url.search + url.hash);
    return fromUrl;
  }
  try { return localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; }
}

let token = readToken();
let refused = null;  // called when a change was refused because of the pen, or newer data arrived
/** (status, payload) for 423 "another computer is editing" and 409 "newer data was just taken". */
export function onRefused(callback) { refused = callback; }

export class ApiError extends Error {
  constructor(status, payload) {
    super((payload && payload.error) || `The program answered with error ${status}.`);
    this.status = status;
    this.payload = payload || {};
  }
}

async function request(method, path, body) {
  const headers = { 'X-Rota-Token': token };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, { error: 'Rota Planner is not running. Start it again, then reload this page.' });
  }
  let payload = null;
  try { payload = await response.json(); } catch { /* empty or non-JSON body */ }
  if (unproven && response.status === 403) {
    // The address held a wrong token (an old link, or another page's doing): use the one kept before.
    unproven = false;
    let kept = '';
    try { kept = localStorage.getItem(TOKEN_KEY) || ''; } catch { /* storage may be blocked */ }
    if (kept && kept !== token) { token = kept; return request(method, path, body); }
  } else if (unproven && response.ok) {
    unproven = false;
    try { localStorage.setItem(TOKEN_KEY, token); } catch { /* storage may be blocked */ }
  }
  if (refused && (response.status === 423 || (response.status === 409 && payload?.updated))) refused(response.status, payload || {});
  if (!response.ok) throw new ApiError(response.status, payload);
  return payload;
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body = {}) => request('POST', path, body),
};

export const monthPath = (monthId, rest = '') => `/api/months/${encodeURIComponent(monthId)}${rest}`;

/**
 * Address of a printable document, opened in its own tab. A new tab cannot
 * send the token as a header, so it travels in the address, as for the
 * program's own address.
 */
export const printUrl = (monthId, kind, options = {}) =>
  `/print/${encodeURIComponent(monthId)}/${kind}?${new URLSearchParams({ ...options, t: token })}`;
