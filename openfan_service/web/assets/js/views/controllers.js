// Controllers: connection to the OpenFAN board(s).
import { api } from '../api.js';
import { busy, confirmDialog, esc, fmtAge, optionFields, readOptions, toast } from '../ui.js';

let root = null;
let ctx = null;

export async function render(el, { store }) {
  root = el;
  ctx = { store };
  await store.refreshConfig();
  el.innerHTML = `
    <div class="page-head"><div><h1>Controllers</h1>
      <p>How the service talks to your OpenFAN: directly over USB (serial), or through the OpenFAN Controller app's REST API. One controller is normal; add more if you have several boards.</p></div>
      <button type="button" class="btn primary" id="k-add">+ Add controller</button></div>
    <div id="k-form"></div>
    <div id="k-list" class="stack" style="gap:20px"></div>
    <section class="card"><div class="card-head"><h2>USB serial ports seen by the container</h2><button type="button" class="btn small" id="k-scan">Scan again</button></div>
      <div class="table-wrap" id="k-ports"><div class="card-body muted">Scanning…</div></div></section>`;
  el.querySelector('#k-add').addEventListener('click', () => paintForm(null));
  el.querySelector('#k-scan').addEventListener('click', (e) => busy(e.target, scan));
  paintList();
  scan();
}

async function scan() {
  const ports = await api.get('/api/serial-ports');
  root.querySelector('#k-ports').innerHTML = ports.length ? `<table><thead><tr><th>Device</th><th>Description</th><th>USB ID</th><th>Board</th></tr></thead>
    <tbody>${ports.map((p) => `<tr><td class="mono">${esc(p.device)}</td><td>${esc(p.description || '')}</td>
      <td class="mono small">${p.vid !== null ? `${p.vid.toString(16).padStart(4, '0')}:${p.pid.toString(16).padStart(4, '0')}`.toUpperCase() : '—'}</td>
      <td>${p.openfan ? `<span class="chip ok">${esc(p.openfan)}</span>` : ''}</td></tr>`).join('')}</tbody></table>`
    : `<div class="card-body muted">No serial ports found. For USB, pass the device into the container, for example in docker-compose:
      <pre>devices:\n  - /dev/ttyACM0:/dev/ttyACM0</pre></div>`;
}

function paintList() {
  const { store } = ctx;
  const cfg = store.config;
  const st = store.state;
  const box = root.querySelector('#k-list');
  if (!cfg.controllers.length) { box.innerHTML = '<div class="card card-body muted">No controller configured yet.</div>'; return; }
  box.innerHTML = cfg.controllers.map((c) => {
    const live = st?.controllers.find((x) => x.id === c.id);
    const type = store.meta.controller_types.find((t) => t.type === c.type);
    const chipHtml = !c.enabled ? '<span class="chip">DISABLED</span>' : live?.connected ? '<span class="chip ok">CONNECTED</span>' : '<span class="chip bad">NOT CONNECTED</span>';
    return `<section class="card"><div class="card-head"><div><h2 class="inline">${esc(c.name)} ${chipHtml}</h2>
        <div class="small muted">${esc(type?.title || c.type)} · id <span class="mono">${esc(c.id)}</span></div></div>
        <div class="inline"><button type="button" class="btn small" data-edit="${esc(c.id)}">Edit</button>
        <button type="button" class="btn small danger" data-del="${esc(c.id)}">Delete</button></div></div>
      <div class="card-body"><dl class="kv">
        <dt>Connected to</dt><dd class="mono">${esc(live?.target || '—')}</dd>
        <dt>Board</dt><dd>${esc(live?.board || '—')}${live?.fan_count ? ` · ${live.fan_count} fans` : ''}</dd>
        ${live?.firmware ? `<dt>Firmware</dt><dd class="small">${esc(live.firmware)}</dd>` : ''}
        ${live?.error ? `<dt>Error</dt><dd style="color:var(--danger)">${esc(live.error)}${live.down_for ? ` (for ${esc(fmtAge(live.down_for))})` : ''}</dd>` : ''}
      </dl></div></section>`;
  }).join('');
  box.querySelectorAll('[data-edit]').forEach((b) => b.addEventListener('click', () => paintForm(b.dataset.edit)));
  box.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', async () => {
    if (!(await confirmDialog('Delete controller?', 'Its fans disappear from the service and are no longer controlled. Their fan names and curve assignments are removed.', 'Delete', true))) return;
    await busy(b, async () => {
      await api.del(`/api/controllers/${encodeURIComponent(b.dataset.del)}`);
      await store.refreshConfig(); toast('Controller deleted'); paintList();
    });
  }));
}

function paintForm(id) {
  const { store } = ctx;
  const c = id ? store.config.controllers.find((x) => x.id === id) : null;
  const types = store.meta.controller_types.filter((t) => t.type !== 'demo' || c?.type === 'demo' || store.config.controllers.some((x) => x.type === 'demo'));
  const box = root.querySelector('#k-form');
  const typeName = c?.type || 'openfan_serial';
  box.innerHTML = `<section class="card"><div class="card-head"><h2>${c ? `Edit "${esc(c.name)}"` : 'Add controller'}</h2></div>
    <div class="card-body stack">
      <div class="grid2">
        <div class="field"><label for="kf-type">Connection</label><select id="kf-type">${types.map((t) =>
          `<option value="${esc(t.type)}" ${t.type === typeName ? 'selected' : ''}>${esc(t.title)}</option>`).join('')}</select></div>
        <div class="field"><label for="kf-name">Name</label><input id="kf-name" type="text" maxlength="60" value="${esc(c?.name || 'OpenFAN')}"></div>
        <div class="field"><label class="check" style="margin-top:28px"><input id="kf-enabled" type="checkbox" ${c?.enabled === false ? '' : 'checked'}> Enabled</label></div>
      </div>
      <p class="small muted" id="kf-desc" style="margin:0"></p>
      <div class="grid2" id="kf-options"></div>
      <div id="kf-test"></div>
      <div class="inline"><button type="button" class="btn" id="kf-test-btn">Test connection</button><span style="flex:1"></span>
        <button type="button" class="btn" id="kf-cancel">Cancel</button><button type="button" class="btn primary" id="kf-save">${c ? 'Save' : 'Add controller'}</button></div>
      <p class="small muted" style="margin:0">Tip: a serial port can only be used by one program. Testing the port of a controller that is already connected fails; save directly instead.</p>
    </div></section>`;
  const paintType = () => {
    const t = types.find((x) => x.type === box.querySelector('#kf-type').value);
    box.querySelector('#kf-desc').textContent = t.description;
    box.querySelector('#kf-options').innerHTML = optionFields(t.options, c && c.type === t.type ? c.options : {}, 'kf');
  };
  paintType();
  box.querySelector('#kf-type').addEventListener('change', paintType);
  box.querySelector('#kf-cancel').addEventListener('click', () => { box.innerHTML = ''; });
  const body = () => ({ type: box.querySelector('#kf-type').value, name: box.querySelector('#kf-name').value.trim(),
    enabled: box.querySelector('#kf-enabled').checked, options: readOptions(box.querySelector('#kf-options')) });
  box.querySelector('#kf-test-btn').addEventListener('click', (e) => busy(e.target, async () => {
    const out = box.querySelector('#kf-test');
    out.innerHTML = '<span class="muted">Connecting…</span>';
    try {
      const r = await api.post('/api/controllers/test', body());
      out.innerHTML = `<div class="banner info"><div class="grow"><b>Connected:</b> ${esc(r.name)} with ${r.fan_count} fans on <span class="mono">${esc(r.target)}</span></div></div>`;
    } catch (ex) { out.innerHTML = `<div class="banner danger"><div class="grow"><span class="body">${esc(ex.message)}</span></div></div>`; }
  }));
  box.querySelector('#kf-save').addEventListener('click', (e) => busy(e.target, async () => {
    if (c) await api.put(`/api/controllers/${encodeURIComponent(c.id)}`, body());
    else await api.post('/api/controllers', body());
    await store.refreshConfig();
    toast('Controller saved; connecting…');
    box.innerHTML = '';
    paintList();
  }));
  box.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

export function onState() { if (root && root.isConnected) paintList(); }
