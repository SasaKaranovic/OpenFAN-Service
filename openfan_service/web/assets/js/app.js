// App shell: routing, live state polling, header status, theme and login.
import { api, setLoginHandler } from './api.js';
import { esc, toast } from './ui.js';
import * as dashboard from './views/dashboard.js';
import * as curves from './views/curves.js';
import * as fans from './views/fans.js';
import * as sensors from './views/sensors.js';
import * as controllers from './views/controllers.js';
import * as notifications from './views/notifications.js';
import * as settings from './views/settings.js';

const VIEWS = { dashboard, curves, fans, sensors, controllers, notifications, settings };
const POLL_MS = 2000;

export const store = {
  state: null,      // /api/state
  config: null,     // /api/config
  meta: null,       // /api/meta
  async refreshConfig() { this.config = await api.get('/api/config'); return this.config; },
};

let current = null;   // { name, mod, params }
let pollTimer = null;

// -- theme -------------------------------------------------------------------
const SUN = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const MOON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
function paintThemeButton() {
  const dark = document.documentElement.dataset.theme !== 'light';
  const btn = document.getElementById('theme-toggle');
  btn.innerHTML = dark ? SUN : MOON;
  btn.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
  btn.title = btn.getAttribute('aria-label');
}
document.getElementById('theme-toggle').addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('openfan.theme', next); } catch (e) { /* private mode */ }
  paintThemeButton();
  if (current && current.mod.onTheme) current.mod.onTheme();
});
paintThemeButton();

// -- login ---------------------------------------------------------------------
const loginBox = document.getElementById('login');
setLoginHandler(() => {
  if (!loginBox.hidden) return;
  loginBox.hidden = false;
  document.getElementById('login-password').focus();
});
document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const err = document.getElementById('login-error');
  try {
    await api.post('/api/login', { password: document.getElementById('login-password').value });
    loginBox.hidden = true;
    err.hidden = true;
    await boot();
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  }
});

// -- header status -----------------------------------------------------------
function paintHeader() {
  const st = store.state;
  const dot = document.getElementById('conn-dot');
  const label = document.getElementById('conn-label');
  const target = document.getElementById('conn-target');
  if (!st) { dot.className = 'dot bad'; label.textContent = 'Service unreachable'; target.textContent = ''; return; }
  const ctrls = st.controllers;
  if (!ctrls.length) { dot.className = 'dot warn'; label.textContent = 'No controller configured'; target.textContent = 'Add one'; return; }
  const c = ctrls[0];
  const kind = c.type === 'openfan_serial' ? 'serial' : (c.type === 'openfan_rest' ? 'REST API' : c.type);
  const upCount = ctrls.filter((x) => x.connected).length;
  dot.className = 'dot ' + (upCount === ctrls.length ? 'ok' : (upCount ? 'warn' : 'bad'));
  label.textContent = `${c.board || c.name} · ${kind} · ${c.connected ? 'connected' : 'disconnected'}` +
    (ctrls.length > 1 ? ` · +${ctrls.length - 1} more` : '');
  target.textContent = c.target || '';
  document.getElementById('conn').title = c.error ? `Error: ${c.error}` : 'Controller settings';
}

// -- polling -----------------------------------------------------------------
async function poll() {
  clearTimeout(pollTimer);
  try {
    store.state = await api.get('/api/state');
    paintHeader();
    if (current && current.mod.onState) current.mod.onState(store.state);
  } catch (e) {
    if (e.message !== 'Login required') { store.state = null; paintHeader(); }
  }
  pollTimer = setTimeout(poll, document.hidden ? POLL_MS * 5 : POLL_MS);
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });

// -- routing -----------------------------------------------------------------
function parseHash() {
  const parts = (location.hash.replace(/^#\/?/, '') || 'dashboard').split('/').map(decodeURIComponent);
  const name = VIEWS[parts[0]] ? parts[0] : 'dashboard';
  return { name, params: parts.slice(1) };
}

export function navigate(hash) { if (location.hash === hash) route(); else location.hash = hash; }

async function route() {
  const { name, params } = parseHash();
  if (current && current.mod.leave && current.mod.leave() === false) {
    history.replaceState(null, '', current.hash);  // stay on the page with unsaved changes
    return;
  }
  document.querySelectorAll('[data-nav]').forEach((a) => a.classList.toggle('active', a.dataset.nav === name));
  const view = document.getElementById('view');
  current = { name, mod: VIEWS[name], params, hash: location.hash || '#/dashboard' };
  try {
    await VIEWS[name].render(view, { params, store, navigate });
    if (store.state && current.mod.onState) current.mod.onState(store.state);
  } catch (e) {
    view.innerHTML = `<div class="banner danger"><div class="grow"><strong>Could not load this page</strong><div class="body">${esc(e.message)}</div></div></div>`;
  }
  view.focus({ preventScroll: true });
}

// Views can warn about unsaved changes.
window.addEventListener('beforeunload', (e) => {
  if (current && current.mod.dirty && current.mod.dirty()) { e.preventDefault(); e.returnValue = ''; }
});

// Footer version ("v2026-11-13 [a1b2c3d]"). /api/health needs no login, so it shows on the login screen too.
async function paintVersion() {
  try {
    const res = await fetch('api/health', { credentials: 'same-origin' });
    const data = await res.json();
    document.getElementById('app-version').textContent = data.version || 'unknown';
  } catch (e) {
    document.getElementById('app-version').textContent = 'unknown';
  }
}

async function boot() {
  try {
    [store.meta, store.config] = await Promise.all([api.get('/api/meta'), api.get('/api/config')]);
  } catch (e) {
    if (e.message !== 'Login required') toast(e.message, 'err', 8000);
    return;
  }
  await poll();
  await route();
}

window.addEventListener('hashchange', route);
paintVersion();
boot();
