// Fans: rename fans and pick the curve each one follows.
import { api } from '../api.js';
import { busy, chip, confirmDialog, esc, toast } from '../ui.js';

let root = null;
let ctx = null;

export async function render(el, { store }) {
  root = el;
  actionsKey = null;
  ctx = { store };
  await store.refreshConfig();
  el.innerHTML = `
    <div class="page-head"><div><h1>Fans</h1>
      <p>Give each fan a name and choose the curve it follows. A fan without a curve is <b>not managed</b>: the service leaves it alone.</p></div>
      <div class="inline" id="f-actions"></div></div>
    <section class="card"><div class="table-wrap" id="f-table"></div></section>`;
  paint();
  paintActions();
  el.querySelector('#f-actions').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-import]');
    if (b) importNames(b, b.dataset.import);
  });
  el.querySelector('#f-table').addEventListener('change', async (e) => {
    const row = e.target.closest('tr[data-key]');
    if (!row) return;
    const [cid, idx] = [row.dataset.controller, row.dataset.index];
    const body = e.target.matches('select') ? { curve: e.target.value || null } : { name: e.target.value.trim() };
    await busy(e.target, async () => {
      await api.put(`/api/fans/${encodeURIComponent(cid)}/${idx}`, body);
      await store.refreshConfig();
      toast('Saved');
    });
  });
}

// "Import names" is only offered for controllers that can report fan names (the REST API).
// Over USB serial the board does not store names, so no button is shown.
let actionsKey = '';
function paintActions() {
  const ctrls = (ctx.store.state?.controllers || []).filter((c) => c.supports_fan_names);
  const key = ctrls.map((c) => `${c.id}:${c.connected}`).join('|');
  if (key === actionsKey) return;
  actionsKey = key;
  const multi = ctrls.length > 1;
  root.querySelector('#f-actions').innerHTML = ctrls.map((c) => `<button type="button" class="btn" data-import="${esc(c.id)}"
      ${c.connected ? '' : 'disabled'} title="${esc(c.connected ? `Read the fan names saved in the OpenFAN Controller app at ${c.target}` : `${c.name} is not connected`)}">
      Import fan names${multi ? ` from ${esc(c.name)}` : ' from controller'}</button>`).join('');
}

async function importNames(button, cid) {
  const { store } = ctx;
  await busy(button, async () => {
    const { names } = await api.get(`/api/controllers/${encodeURIComponent(cid)}/fan-names`);
    const fans = (store.state?.fans || []).filter((f) => f.controller === cid);
    const rows = Object.entries(names).filter(([idx]) => fans.some((f) => f.index === Number(idx)));
    if (!rows.length) throw new Error('The controller did not report any fan names.');
    const list = rows.map(([idx, name]) => {
      const current = store.config.fans.find((f) => f.controller === cid && f.index === Number(idx))?.name || `Fan ${idx}`;
      return `<tr><td class="muted">#${esc(idx)}</td><td>${esc(current)}</td><td>→</td><td><b>${esc(name)}</b></td></tr>`;
    }).join('');
    const ok = await confirmDialog('Import fan names?',
      `These names are saved in the OpenFAN Controller app. Importing replaces the names here; curves are not changed.
       <div class="table-wrap" style="margin-top:10px;max-height:320px;overflow-y:auto"><table><tbody>${list}</tbody></table></div>`,
      `Import ${rows.length} name${rows.length > 1 ? 's' : ''}`);
    if (!ok) return;
    const res = await api.post(`/api/controllers/${encodeURIComponent(cid)}/fan-names/import`);
    await store.refreshConfig();
    toast(`Imported ${res.imported} fan name${res.imported > 1 ? 's' : ''}`);
    paint();
  });
}

function paint() {
  const { store } = ctx;
  const cfg = store.config;
  const fans = store.state?.fans || [];
  if (!fans.length) {
    root.querySelector('#f-table').innerHTML = '<div class="card-body muted">No fans found. Check the controller on the <a href="#/controllers">Controllers</a> page.</div>';
    return;
  }
  root.querySelector('#f-table').innerHTML = `<table>
    <thead><tr><th>Fan</th><th>Name</th><th>Curve</th><th>State</th><th class="num">Speed</th><th class="num">RPM</th></tr></thead>
    <tbody>${fans.map((f) => {
      const conf = cfg.fans.find((x) => x.controller === f.controller && x.index === f.index);
      const curve = conf?.curve || '';
      const missing = curve && !cfg.curves.some((c) => c.id === curve);
      return `<tr data-key="${esc(f.key)}" data-controller="${esc(f.controller)}" data-index="${f.index}">
        <td class="muted">${esc(f.controller)} #${f.index}</td>
        <td><label class="sr-only" for="fn-${esc(f.key)}">Name of fan ${f.index}</label>
          <input id="fn-${esc(f.key)}" type="text" maxlength="60" value="${esc(conf?.name || '')}" placeholder="Fan ${f.index}"></td>
        <td><label class="sr-only" for="fc-${esc(f.key)}">Curve for fan ${f.index}</label>
          <select id="fc-${esc(f.key)}">
            <option value="">Not managed</option>
            ${missing ? `<option value="${esc(curve)}" selected>${esc(curve)} (deleted – fail-safe)</option>` : ''}
            ${cfg.curves.map((c) => `<option value="${esc(c.id)}" ${c.id === curve ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}
          </select></td>
        <td data-live="state">${chip(f.state)}</td>
        <td class="num" data-live="pwm">${f.pwm ?? '—'}${f.pwm !== null && f.pwm !== undefined ? '%' : ''}</td>
        <td class="num" data-live="rpm">${f.rpm ?? '—'}</td></tr>`;
    }).join('')}</tbody></table>`;
}

export function onState(st) {
  if (!root || !root.isConnected) return;
  paintActions();
  const rows = root.querySelectorAll('tr[data-key]');
  if (rows.length !== st.fans.length) { paint(); return; }
  // Update live columns only, so typing in a name field is never interrupted.
  for (const f of st.fans) {
    const row = root.querySelector(`tr[data-key="${CSS.escape(f.key)}"]`);
    if (!row) { paint(); return; }
    row.querySelector('[data-live=state]').innerHTML = chip(f.state);
    row.querySelector('[data-live=pwm]').textContent = f.pwm === null || f.pwm === undefined ? '—' : `${f.pwm}%`;
    row.querySelector('[data-live=rpm]').textContent = f.rpm ?? '—';
  }
}
