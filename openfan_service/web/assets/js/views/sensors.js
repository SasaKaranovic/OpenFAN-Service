// Sensor sources (plugins) and the sensors they report.
import { api } from '../api.js';
import { busy, confirmDialog, esc, fmtAge, fmtTemp, optionFields, readOptions, toast } from '../ui.js';

let root = null;
let ctx = null;
let editing = null; // null | 'new' | source id

export async function render(el, { store }) {
  root = el;
  ctx = { store };
  editing = null;
  await store.refreshConfig();
  el.innerHTML = `
    <div class="page-head"><div><h1>Sensors</h1>
      <p>Sensor sources feed temperatures into the service. Each source is a plugin: Unraid disks, Linux hwmon, a command, a file, a URL or values pushed by your own script.</p></div>
      <button type="button" class="btn primary" id="s-add">+ Add sensor source</button></div>
    <div id="s-form"></div>
    <div id="s-sources" class="stack" style="gap:20px"></div>`;
  el.querySelector('#s-add').addEventListener('click', () => { editing = 'new'; paintForm(); });
  paintSources();
}

function paintSources() {
  const { store } = ctx;
  const cfg = store.config;
  const st = store.state;
  const box = root.querySelector('#s-sources');
  if (!cfg.sources.length) {
    box.innerHTML = '<div class="card card-body muted">No sensor sources yet. Click <b>Add sensor source</b> to add one.</div>';
    return;
  }
  box.innerHTML = cfg.sources.map((src) => {
    const live = st?.sources.find((s) => s.id === src.id);
    const type = store.meta.sensor_types.find((t) => t.type === src.type);
    const sensors = (st?.sensors || []).filter((s) => s.source === src.id);
    const used = cfg.curves.filter((c) => c.sensors.some((r) => r.startsWith(`${src.id}:`))).map((c) => c.name);
    const status = !src.enabled ? '<span class="chip">DISABLED</span>' : live?.error ? '<span class="chip bad">ERROR</span>' : '<span class="chip ok">OK</span>';
    const pushHelp = src.type === 'push' ? `<div class="card-body" style="border-top:1px solid var(--border-soft)">
      <div class="small muted" style="margin-bottom:6px">Send values from your script (they go stale after ${cfg.settings.stale_timeout} s without updates):</div>
      <pre>curl -X POST ${esc(location.origin)}/api/push/${esc(src.id)} \\
  -H 'Content-Type: application/json'${src.options.token ? ` \\\n  -H 'X-OpenFAN-Token: &lt;your token&gt;'` : ''} \\
  -d '{"cpu": 52.5, "gpu": 61}'</pre></div>` : '';
    return `<section class="card" data-source="${esc(src.id)}">
      <div class="card-head"><div><h2 class="inline">${esc(src.name)} ${status}</h2>
        <div class="small muted">${esc(type?.title || src.type)} · id <span class="mono">${esc(src.id)}</span>${type && !type.push ? ` · every ${src.poll_interval} s` : ''}${used.length ? ` · used by ${esc(used.join(', '))}` : ''}</div></div>
        <div class="inline"><button type="button" class="btn small" data-edit="${esc(src.id)}">Edit</button>
          <button type="button" class="btn small danger" data-del="${esc(src.id)}">Delete</button></div></div>
      ${live?.error ? `<div class="banner danger" style="margin:12px 18px 0"><div class="grow"><span class="body">${esc(live.error)}</span></div></div>` : ''}
      <div class="table-wrap">${sensors.length ? `<table><thead><tr><th>Sensor</th><th>Label</th><th class="num">Value</th><th>Status</th><th class="num">Age</th><th><span class="sr-only">Ignore</span></th></tr></thead>
        <tbody>${sensors.map((s) => {
          const bad = ['stale', 'error', 'missing'].includes(s.status);
          const usedBy = cfg.curves.filter((c) => c.sensors.includes(s.ref)).map((c) => c.name);
          return `<tr class="${bad ? 'bad' : ''} ${s.ignored ? 'ignored' : ''}"><td class="mono small">${esc(s.ref)}</td><td class="muted">${esc(s.label || '')}</td>
          <td class="num"><b>${s.value === null || s.ignored ? '—' : esc(fmtTemp(s.value))}</b></td>
          <td>${s.ignored ? 'ignored' : esc(s.status)}${!s.ignored && s.message ? ` <span class="muted small">– ${esc(s.message)}</span>` : ''}</td>
          <td class="num age">${s.ignored ? '' : esc(fmtAge(s.age))}</td>
          <td class="num"><button type="button" class="btn small" data-ignore="${esc(s.ref)}" data-on="${s.ignored ? '0' : '1'}"
            data-used="${esc(usedBy.join(', '))}" title="${s.ignored ? 'Show and use this sensor again' : 'Hide this sensor and never report problems for it'}">${s.ignored ? 'Restore' : 'Ignore'}</button></td></tr>`;
        }).join('')}</tbody></table>` : `<div class="card-body muted">${src.enabled ? 'No sensors reported yet.' : 'Disabled.'}</div>`}</div>
      ${pushHelp}</section>`;
  }).join('');
  box.querySelectorAll('[data-edit]').forEach((b) => b.addEventListener('click', () => { editing = b.dataset.edit; paintForm(); }));
  box.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => remove(b)));
  box.querySelectorAll('[data-ignore]').forEach((b) => b.addEventListener('click', () => toggleIgnore(b)));
}

async function toggleIgnore(btn) {
  const { store } = ctx;
  const ref = btn.dataset.ignore;
  const ignore = btn.dataset.on === '1';
  if (ignore) {
    const used = btn.dataset.used;
    const body = used
      ? `<b>${esc(ref)}</b> is used by <b>${esc(used)}</b>. Ignored sensors are left out of curves; a curve whose sensors are <i>all</i> ignored runs its fans in fail-safe.`
      : `<b>${esc(ref)}</b> is hidden from the dashboard and never reports problems. You can restore it here at any time.`;
    if (!(await confirmDialog('Ignore this sensor?', body, 'Ignore sensor'))) return;
  }
  await busy(btn, async () => {
    await api.post('/api/sensors/ignore', { ref, ignored: ignore });
    await store.refreshConfig();
    toast(ignore ? `${ref} ignored` : `${ref} restored`);
  });
}

async function remove(btn) {
  const { store } = ctx;
  const id = btn.dataset.del;
  const used = store.config.curves.filter((c) => c.sensors.some((r) => r.startsWith(`${id}:`)));
  const body = used.length
    ? `Curves <b>${used.map((c) => esc(c.name)).join(', ')}</b> use sensors from this source. Their fans will run in <b>fail-safe at 100%</b> until you pick other sensors.`
    : 'No curve uses this source.';
  if (!(await confirmDialog('Delete sensor source?', body, 'Delete', true))) return;
  await busy(btn, async () => {
    await api.del(`/api/sources/${encodeURIComponent(id)}`);
    await store.refreshConfig();
    toast('Sensor source deleted');
    paintSources();
  });
}

function paintForm() {
  const { store } = ctx;
  const box = root.querySelector('#s-form');
  const src = editing === 'new' ? null : store.config.sources.find((s) => s.id === editing);
  const types = store.meta.sensor_types;
  const typeName = src?.type || types[0].type;
  box.innerHTML = `<section class="card" aria-label="Sensor source settings">
    <div class="card-head"><h2>${src ? `Edit "${esc(src.name)}"` : 'Add sensor source'}</h2></div>
    <div class="card-body stack">
      <div class="grid2">
        <div class="field"><label for="sf-type">Type</label>
          <select id="sf-type" ${src ? 'disabled' : ''}>${types.map((t) => `<option value="${esc(t.type)}" ${t.type === typeName ? 'selected' : ''}>${esc(t.title)}</option>`).join('')}</select></div>
        <div class="field"><label for="sf-name">Name</label><input id="sf-name" type="text" maxlength="60" value="${esc(src?.name || '')}"></div>
        ${src ? '' : `<div class="field"><label for="sf-id">Id <span class="help">(used in sensor names, e.g. <code>unraid:disk1</code>)</span></label>
          <input id="sf-id" type="text" maxlength="48" placeholder="auto"></div>`}
        <div class="field" id="sf-interval-box"><label for="sf-interval">Read every (s)</label><input id="sf-interval" type="number" min="0.5" step="0.5" value="${src?.poll_interval ?? ''}"></div>
        <div class="field"><label class="check" style="margin-top:28px"><input id="sf-enabled" type="checkbox" ${src?.enabled === false ? '' : 'checked'}> Enabled</label></div>
      </div>
      <p class="small muted" id="sf-desc" style="margin:0"></p>
      <div class="grid2" id="sf-options"></div>
      <div id="sf-test"></div>
      <div class="inline"><button type="button" class="btn" id="sf-test-btn">Test</button>
        <span style="flex:1"></span><button type="button" class="btn" id="sf-cancel">Cancel</button>
        <button type="button" class="btn primary" id="sf-save">${src ? 'Save' : 'Add source'}</button></div>
    </div></section>`;
  const paintType = () => {
    const t = types.find((x) => x.type === box.querySelector('#sf-type').value);
    box.querySelector('#sf-desc').textContent = t.description;
    box.querySelector('#sf-options').innerHTML = optionFields(t.options, src && src.type === t.type ? src.options : {}, 'sf');
    box.querySelector('#sf-interval-box').hidden = t.push;
    box.querySelector('#sf-test-btn').hidden = t.push;
    const interval = box.querySelector('#sf-interval');
    if (!src) interval.value = t.default_poll_interval;
    if (!src) box.querySelector('#sf-name').value = t.title;
    box.querySelector('#sf-test').innerHTML = '';
  };
  paintType();
  box.querySelector('#sf-type').addEventListener('change', paintType);
  box.querySelector('#sf-cancel').addEventListener('click', () => { editing = null; box.innerHTML = ''; });
  const body = () => ({
    type: box.querySelector('#sf-type').value,
    name: box.querySelector('#sf-name').value.trim(),
    id: box.querySelector('#sf-id')?.value.trim() || undefined,
    poll_interval: Number(box.querySelector('#sf-interval').value) || undefined,
    enabled: box.querySelector('#sf-enabled').checked,
    options: readOptions(box.querySelector('#sf-options')),
  });
  box.querySelector('#sf-test-btn').addEventListener('click', (e) => busy(e.target, async () => {
    const out = box.querySelector('#sf-test');
    out.innerHTML = '<span class="muted">Testing…</span>';
    try {
      const res = await api.post('/api/sources/test', body());
      out.innerHTML = res.readings.length ? `<div class="banner info"><div class="grow"><b>Found ${res.readings.length} sensor${res.readings.length > 1 ? 's' : ''}:</b>
        ${res.readings.map((r) => `<span class="mono small">${esc(r.id)}</span> ${r.value === null ? esc(r.status) : esc(fmtTemp(r.value))}`).join(' · ')}</div></div>`
        : `<div class="banner warn"><div class="grow">${esc(res.message || 'The source answered but reported no sensors.')}</div></div>`;
    } catch (ex) {
      out.innerHTML = `<div class="banner danger"><div class="grow"><span class="body">${esc(ex.message)}</span></div></div>`;
    }
  }));
  box.querySelector('#sf-save').addEventListener('click', (e) => busy(e.target, async () => {
    if (src) await api.put(`/api/sources/${encodeURIComponent(src.id)}`, body());
    else await api.post('/api/sources', body());
    await store.refreshConfig();
    toast(src ? 'Sensor source saved' : 'Sensor source added');
    editing = null;
    box.innerHTML = '';
    paintSources();
  }));
  box.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

export function onState() {
  if (!root || !root.isConnected) return;
  paintSources();
}
