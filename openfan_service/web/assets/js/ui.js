// Small UI helpers shared by the views.

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

export function fmtTemp(v) {
  return v === null || v === undefined ? '—' : `${Number(v).toFixed(Number.isInteger(v) ? 0 : 1)} °C`;
}
export function fmtAge(s) {
  if (s === null || s === undefined) return '—';
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}
export function fmtTime(ts) {
  const d = new Date(ts * 1000);
  const today = new Date();
  const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return d.toDateString() === today.toDateString() ? hm : `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${hm}`;
}

export function toast(message, kind = 'ok', ms = 4000) {
  const box = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = message;
  box.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

/** Ask a yes/no question in a modal. Resolves true/false. */
export function confirmDialog(title, body, okLabel = 'OK', danger = false) {
  return new Promise((resolve) => {
    const dlg = document.createElement('dialog');
    dlg.innerHTML = `<h2>${esc(title)}</h2><p>${body}</p>
      <div class="actions"><button type="button" class="btn" data-v="0">Cancel</button>
      <button type="button" class="btn ${danger ? 'danger' : 'primary'}" data-v="1">${esc(okLabel)}</button></div>`;
    document.body.appendChild(dlg);
    dlg.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-v]');
      if (b) { dlg.close(); resolve(b.dataset.v === '1'); }
    });
    dlg.addEventListener('cancel', () => resolve(false));
    dlg.addEventListener('close', () => dlg.remove());
    dlg.showModal();
  });
}

/** Wrap an async action: disables the button, shows errors as toasts. */
export async function busy(button, fn) {
  if (button) button.disabled = true;
  try { return await fn(); }
  catch (e) { toast(e.message || String(e), 'err', 7000); return undefined; }
  finally { if (button) button.disabled = false; }
}

export const STATE_CHIP = {
  ok: ['OK', 'ok'], failsafe: ['FAIL-SAFE', 'bad'], unmanaged: ['NOT MANAGED', ''],
  starting: ['STARTING', 'warn'], override: ['OVERRIDE', 'warn'], offline: ['OFFLINE', 'bad'],
};

export function chip(state) {
  const [label, cls] = STATE_CHIP[state] || [state.toUpperCase(), ''];
  return `<span class="chip ${cls}">${esc(label)}</span>`;
}

/** Render a plugin/driver option list as form fields. */
export function optionFields(options, values = {}, prefix = 'opt') {
  return options.map((o) => {
    const id = `${prefix}-${o.name}`;
    const v = values[o.name] ?? o.default ?? '';
    const help = o.help ? `<span class="help">${esc(o.help)}</span>` : '';
    if (o.type === 'select') {
      return `<div class="field"><label for="${id}">${esc(o.label)}</label>
        <select id="${id}" data-opt="${esc(o.name)}">${o.choices.map((c) =>
          `<option value="${esc(c.value)}" ${String(c.value) === String(v) ? 'selected' : ''}>${esc(c.label)}</option>`).join('')}</select>${help}</div>`;
    }
    if (o.type === 'bool') {
      return `<div class="field"><label class="check"><input type="checkbox" id="${id}" data-opt="${esc(o.name)}" ${v ? 'checked' : ''}> ${esc(o.label)}</label>${help}</div>`;
    }
    if (o.type === 'textarea') {
      return `<div class="field" style="grid-column: 1 / -1"><label for="${id}">${esc(o.label)}</label>
        <textarea id="${id}" data-opt="${esc(o.name)}">${esc(v)}</textarea>${help}</div>`;
    }
    const type = o.type === 'number' ? 'number' : (o.type === 'password' ? 'password' : 'text');
    return `<div class="field"><label for="${id}">${esc(o.label)}${o.required ? ' *' : ''}</label>
      <input id="${id}" type="${type}" ${type === 'number' ? 'step="any"' : ''} data-opt="${esc(o.name)}" value="${esc(v)}">${help}</div>`;
  }).join('');
}

export function readOptions(root) {
  const out = {};
  root.querySelectorAll('[data-opt]').forEach((el) => {
    const name = el.dataset.opt;
    if (el.type === 'checkbox') out[name] = el.checked;
    else if (el.type === 'number') out[name] = el.value === '' ? null : Number(el.value);
    else out[name] = el.value;
  });
  return out;
}
