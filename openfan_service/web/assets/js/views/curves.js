// Fan curve list and editor.
import { api } from '../api.js';
import { CurveGraph, evalCurve } from '../chart.js';
import { busy, confirmDialog, esc, fmtTemp, toast } from '../ui.js';

const COMBINE = [['max', 'Highest'], ['avg', 'Average'], ['min', 'Lowest']];
let ctx = null;        // { el, store, navigate }
let curveId = null;    // id being edited, or null for a new curve
let original = '';     // JSON of the saved draft (for dirty tracking)
let draft = null;
let graph = null;
let pendingCopy = null;

const blank = () => ({ name: 'New curve', sensors: [], combine: 'max', type: 'linear',
  points: [[30, 30], [40, 50], [50, 80], [60, 100]], hysteresis: 2, min_pwm: 20, fans: [] });

export function dirty() { return draft !== null && JSON.stringify(draft) !== original; }
export function leave() {
  if (dirty() && !window.confirm('You have unsaved changes to this curve. Leave without saving?')) return false;
  draft = null;
  return true;
}

export async function render(el, { params, store, navigate }) {
  ctx = { el, store, navigate };
  const cfg = await store.refreshConfig();
  const wanted = params[0];
  if (wanted === 'new') {
    curveId = null;
    draft = pendingCopy || blank();
    pendingCopy = null;
    original = JSON.stringify(blank());
  } else {
    const c = cfg.curves.find((x) => x.id === wanted) || cfg.curves[0];
    if (!c) { curveId = null; draft = null; }
    else {
      curveId = c.id;
      draft = { name: c.name, sensors: [...c.sensors], combine: c.combine, type: c.type,
        points: c.points.map((p) => [Number(p[0]), Number(p[1])]), hysteresis: c.hysteresis, min_pwm: c.min_pwm,
        fans: cfg.fans.filter((f) => f.curve === c.id).map((f) => `${f.controller}:${f.index}`).sort() };
      original = JSON.stringify(draft);
    }
  }

  el.innerHTML = `<div class="row">
    <aside class="card" style="flex:1 1 260px;max-width:100%" aria-label="Fan curves">
      <div class="card-head"><h2>Fan curves</h2><a class="btn small primary" href="#/curves/new">+ New</a></div>
      <ul class="curve-list" id="c-list"></ul>
    </aside>
    <div class="grow stack" id="c-editor" style="gap:20px"></div>
  </div>`;
  paintList();
  if (!draft) {
    el.querySelector('#c-editor').innerHTML = `<div class="card card-body"><h2>No fan curves yet</h2>
      <p class="muted">A fan curve turns a temperature into a fan speed. Create one, pick its sensors and the fans that should follow it.</p>
      <a class="btn primary" href="#/curves/new">Create your first curve</a></div>`;
    return;
  }
  paintEditor();
}

function paintList() {
  const { store } = ctx;
  const cfg = store.config;
  const st = store.state;
  const list = ctx.el.querySelector('#c-list');
  const items = cfg.curves.map((c) => {
    const nFans = cfg.fans.filter((f) => f.curve === c.id).length;
    const fs = st && st.fans.some((f) => f.curve === c.id && f.state === 'failsafe');
    const sens = c.sensors.length > 1 ? `${COMBINE.find((x) => x[0] === c.combine)[1].toLowerCase()} of ${c.sensors.length}` : (c.sensors[0] || 'no sensor');
    return `<li><a href="#/curves/${encodeURIComponent(c.id)}" class="${c.id === curveId ? 'active' : ''}">
      <div class="inline" style="font-weight:600">${esc(c.name)} ${fs ? '<span class="chip bad">FAIL-SAFE</span>' : ''}</div>
      <div class="sub">${c.type === 'step' ? 'Step' : 'Linear'} · ${esc(sens)} · ${nFans ? `${nFans} fan${nFans > 1 ? 's' : ''}` : 'unused'}</div></a></li>`;
  });
  if (curveId === null && draft) items.push(`<li><a class="active" href="#/curves/new"><div style="font-weight:600">${esc(draft.name)}</div><div class="sub">not saved yet</div></a></li>`);
  list.innerHTML = items.join('') || '<li class="muted" style="padding:10px 12px">No curves yet.</li>';
}

function sensorInfo(ref) {
  const s = ctx.store.state?.sensors.find((x) => x.ref === ref);
  return s || null;
}

/** Combined input temperature right now, using the same rules as the service. */
function currentInput() {
  const vals = [];
  let problem = null;
  let ignored = 0;
  let asleep = 0;
  for (const ref of draft.sensors) {
    const s = sensorInfo(ref);
    if (s && s.status === 'ignored') { ignored += 1; continue; }
    if (!s || ['stale', 'error', 'missing'].includes(s.status)) { problem = ref; continue; }
    if (s.value !== null && s.value !== undefined) vals.push([s.value, ref]);
    else asleep += 1;
  }
  if (problem) return { problem };
  if (!vals.length && ignored && !asleep) return { allIgnored: true };
  if (!vals.length) return { temp: null };
  const nums = vals.map((v) => v[0]);
  const temp = draft.combine === 'min' ? Math.min(...nums) : draft.combine === 'avg' ? nums.reduce((a, b) => a + b, 0) / nums.length : Math.max(...nums);
  const top = draft.combine === 'avg' ? null : vals.find((v) => v[0] === temp)[1];
  return { temp, top };
}

function paintEditor() {
  const { el, store } = ctx;
  const ed = el.querySelector('#c-editor');
  const cfg = store.config;
  ed.innerHTML = `
    <div class="page-head">
      <div class="field" style="flex:1 1 280px"><label for="c-name" class="small muted" style="text-transform:uppercase;letter-spacing:.04em">Curve name</label>
        <input id="c-name" class="name-input" type="text" maxlength="80" value="${esc(draft.name)}"></div>
      <div class="inline">
        <span class="chip warn" id="c-dirty" style="visibility:hidden" title="Fans keep running on the last saved version until you press Save &amp; apply.">Unsaved changes</span>
        ${curveId ? '<button type="button" class="btn" id="c-dup">Duplicate</button><button type="button" class="btn danger" id="c-del">Delete</button>' : ''}
        <button type="button" class="btn" id="c-discard">Discard changes</button>
        <button type="button" class="btn primary" id="c-save">${curveId ? 'Save &amp; apply' : 'Create curve'}</button>
      </div>
    </div>
    <div class="row">
      <section class="card grow" style="flex:999 1 520px" aria-label="Curve">
        <div class="card-head"><h2>Curve</h2>
          <div class="seg" role="group" aria-label="Curve type" id="c-type">
            <button type="button" data-v="linear" aria-pressed="${draft.type === 'linear'}">Linear</button>
            <button type="button" data-v="step" aria-pressed="${draft.type === 'step'}">Step</button></div></div>
        <div style="padding:16px 18px 4px" id="c-graph"></div>
        <div class="small muted" style="padding:4px 18px 14px">Drag points on the graph (or use the arrow keys), or edit them in the table. Click an empty spot to add a point.</div>
        <div class="table-wrap" style="border-top:1px solid var(--border-soft)">
          <table class="points"><thead><tr><th>#</th><th>Temperature (°C)</th><th>Fan speed (PWM %)</th><th><span class="sr-only">Remove</span></th></tr></thead>
          <tbody id="c-points"></tbody></table>
          <div style="padding:10px 18px 14px"><button type="button" class="btn small" id="c-addpt">+ Add point</button></div>
        </div>
      </section>
      <div class="side" style="flex:1 1 320px">
        <section class="card" aria-label="Input sensors">
          <div class="card-head"><h2>Input</h2></div>
          <div class="card-body stack">
            <div><div style="font-weight:600;font-size:13px;margin-bottom:8px">Sensors</div>
              <div class="sensor-chips" id="c-sensors"></div>
              <label for="c-addsensor" class="sr-only">Add sensor</label>
              <select id="c-addsensor" style="margin-top:10px;width:100%"></select></div>
            <div><div style="font-weight:600;font-size:13px;margin-bottom:8px">Combine multiple sensors using</div>
              <div class="seg" role="group" aria-label="Combine sensors" id="c-combine">
                ${COMBINE.map(([v, l]) => `<button type="button" data-v="${v}" aria-pressed="${draft.combine === v}">${l}</button>`).join('')}</div></div>
            <div class="now" id="c-now"></div>
          </div>
        </section>
        <section class="card" aria-label="Behaviour">
          <div class="card-head"><h2>Behaviour</h2></div>
          <div class="card-body grid2">
            <div class="field"><label for="c-hyst">Hysteresis</label>
              <div class="inline"><input id="c-hyst" type="number" min="0" max="20" step="0.5" value="${draft.hysteresis}" style="width:90px"><span class="muted">°C</span></div></div>
            <div class="field"><label for="c-min">Minimum speed</label>
              <div class="inline"><input id="c-min" type="number" min="0" max="100" step="1" value="${draft.min_pwm}" style="width:90px"><span class="muted">%</span></div></div>
            <p class="small muted" style="grid-column:1/-1;margin:0" id="c-hyst-help"></p>
          </div>
        </section>
      </div>
    </div>
    <section class="card" aria-label="Assigned fans">
      <div class="card-head"><h2>Assigned fans</h2><span class="small muted">A fan follows one curve. Picking a fan used by another curve moves it here.</span></div>
      <div class="card-body"><div class="fan-pick" id="c-fans"></div></div>
    </section>`;

  graph = new CurveGraph(ed.querySelector('#c-graph'), {
    points: draft.points, type: draft.type, minPwm: draft.min_pwm, hysteresis: draft.hysteresis, current: null,
    onChange: (pts) => { draft.points = pts; paintPoints(); markDirty(); },
  });
  paintPoints(); paintSensors(); paintFans(); paintHystHelp(); markDirty(); onState(store.state);

  ed.querySelector('#c-name').addEventListener('input', (e) => { draft.name = e.target.value; markDirty(); });
  ed.querySelector('#c-type').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-v]'); if (!b) return;
    draft.type = b.dataset.v;
    ed.querySelectorAll('#c-type button').forEach((x) => x.setAttribute('aria-pressed', x.dataset.v === draft.type));
    graph.update({ type: draft.type }); markDirty(); onState(store.state);
  });
  ed.querySelector('#c-combine').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-v]'); if (!b) return;
    draft.combine = b.dataset.v;
    ed.querySelectorAll('#c-combine button').forEach((x) => x.setAttribute('aria-pressed', x.dataset.v === draft.combine));
    markDirty(); paintSensors(); onState(store.state);
  });
  ed.querySelector('#c-hyst').addEventListener('input', (e) => {
    draft.hysteresis = e.target.value === '' ? 0 : Number(e.target.value);
    graph.update({ hysteresis: draft.hysteresis }); paintHystHelp(); markDirty();
  });
  ed.querySelector('#c-min').addEventListener('input', (e) => {
    draft.min_pwm = e.target.value === '' ? 0 : Number(e.target.value);
    graph.update({ minPwm: draft.min_pwm }); markDirty(); onState(store.state);
  });
  ed.querySelector('#c-points').addEventListener('change', (e) => {
    const inp = e.target.closest('input[data-i]'); if (!inp) return;
    const i = Number(inp.dataset.i), k = Number(inp.dataset.k);
    draft.points[i][k] = Number(inp.value);
    draft.points.sort((a, b) => a[0] - b[0]);
    graph.o.points = draft.points; graph.computeRange(); graph.render(); paintPoints(); markDirty();
  });
  ed.querySelector('#c-points').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-rm]'); if (!b) return;
    if (draft.points.length <= 1) { toast('A curve needs at least one point', 'err'); return; }
    draft.points.splice(Number(b.dataset.rm), 1);
    graph.update({ points: draft.points }); paintPoints(); markDirty();
  });
  ed.querySelector('#c-addpt').addEventListener('click', () => {
    const last = draft.points[draft.points.length - 1] || [30, 30];
    draft.points.push([Math.min(150, last[0] + 5), Math.min(100, last[1] + 10)]);
    graph.o.points = draft.points; graph.computeRange(); graph.render(); paintPoints(); markDirty();
  });
  ed.querySelector('#c-sensors').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-rm]'); if (!b) return;
    draft.sensors = draft.sensors.filter((r) => r !== b.dataset.rm);
    paintSensors(); markDirty(); onState(store.state);
  });
  ed.querySelector('#c-addsensor').addEventListener('change', (e) => {
    if (!e.target.value) return;
    draft.sensors.push(e.target.value);
    paintSensors(); markDirty(); onState(store.state);
  });
  ed.querySelector('#c-fans').addEventListener('change', (e) => {
    const cb = e.target.closest('input[data-key]'); if (!cb) return;
    const set = new Set(draft.fans);
    if (cb.checked) set.add(cb.dataset.key); else set.delete(cb.dataset.key);
    draft.fans = [...set].sort();
    paintFans(); markDirty();
  });
  ed.querySelector('#c-discard').addEventListener('click', () => {
    if (curveId === null) { draft = null; ctx.navigate('#/curves'); return; }
    draft = JSON.parse(original); paintEditor();
  });
  ed.querySelector('#c-save').addEventListener('click', (e) => busy(e.target, save));
  ed.querySelector('#c-dup')?.addEventListener('click', () => {
    pendingCopy = { ...JSON.parse(JSON.stringify(draft)), name: `${draft.name} (copy)`, fans: [] };
    draft = null;
    ctx.navigate('#/curves/new');
  });
  ed.querySelector('#c-del')?.addEventListener('click', async (e) => {
    const used = cfg.fans.filter((f) => f.curve === curveId).length;
    const body = used
      ? `<b>${used} fan${used > 1 ? 's' : ''}</b> use this curve. They will run in <b>fail-safe at 100%</b> until you assign them another curve.`
      : 'No fans use this curve.';
    if (!(await confirmDialog(`Delete "${draft.name}"?`, body, 'Delete curve', true))) return;
    await busy(e.target, async () => {
      await api.del(`/api/curves/${encodeURIComponent(curveId)}`);
      draft = null;
      toast('Curve deleted');
      ctx.navigate('#/curves');
    });
  });
}

function paintHystHelp() {
  const h = Number(draft.hysteresis) || 0;
  ctx.el.querySelector('#c-hyst-help').textContent = h > 0
    ? `Fans speed up as soon as the temperature rises, but only slow down once it drops ${h} °C below the value that raised them. This stops fans hunting up and down.`
    : 'Hysteresis is off: fans follow every small temperature change.';
}

function paintPoints() {
  ctx.el.querySelector('#c-points').innerHTML = draft.points.map(([t, p], i) => `<tr>
    <td class="muted">${i + 1}</td>
    <td><input type="number" step="1" min="-50" max="200" value="${t}" data-i="${i}" data-k="0" aria-label="Point ${i + 1} temperature"></td>
    <td><input type="number" step="1" min="0" max="100" value="${p}" data-i="${i}" data-k="1" aria-label="Point ${i + 1} fan speed"></td>
    <td class="num"><button type="button" class="btn small" data-rm="${i}" aria-label="Remove point ${i + 1}">×</button></td></tr>`).join('');
}

function paintSensors() {
  const { el, store } = ctx;
  const cur = currentInput();
  el.querySelector('#c-sensors').innerHTML = draft.sensors.map((ref) => {
    const s = sensorInfo(ref);
    const bad = !s || ['stale', 'error', 'missing'].includes(s.status);
    const ign = s && s.status === 'ignored';
    const val = !s ? 'not found' : ign ? 'ignored (left out)' : bad ? s.status : (s.value === null ? 'asleep' : fmtTemp(s.value));
    return `<span class="schip ${bad ? 'bad' : ''} ${ign ? 'ignored' : ''} ${cur.top === ref ? 'top' : ''}" title="${esc(ign ? 'Ignored on the Sensors page, so this curve does not use it' : (s?.label || s?.message || ''))}">
      <span class="mono">${esc(ref)}</span><span class="muted">${esc(val)}</span>
      <button type="button" data-rm="${esc(ref)}" aria-label="Remove ${esc(ref)}">×</button></span>`;
  }).join('') || '<span class="muted small">No sensors yet. A curve without sensors runs its fans in fail-safe.</span>';
  const options = (store.state?.sensors || []).filter((s) => !draft.sensors.includes(s.ref) && !s.ignored);
  el.querySelector('#c-addsensor').innerHTML = `<option value="">+ Add sensor…</option>` + options.map((s) =>
    `<option value="${esc(s.ref)}">${esc(s.ref)}${s.label ? ` – ${esc(s.label)}` : ''} · ${esc(s.value === null ? s.status : fmtTemp(s.value))}</option>`).join('');
}

function paintFans() {
  const { el, store } = ctx;
  const cfg = store.config;
  const fans = store.state?.fans || [];
  el.querySelector('#c-fans').innerHTML = fans.map((f) => {
    const on = draft.fans.includes(f.key);
    const assigned = cfg.fans.find((x) => `${x.controller}:${x.index}` === f.key)?.curve;
    const where = on ? 'this curve' : assigned && assigned !== curveId
      ? (cfg.curves.find((c) => c.id === assigned)?.name || `${assigned} (deleted)`) : 'not assigned';
    return `<label class="${on ? 'on' : ''}"><input type="checkbox" data-key="${esc(f.key)}" ${on ? 'checked' : ''}>
      <span><b>${esc(f.name)}</b><small>Fan ${f.index}${f.controller !== 'main' ? ` · ${esc(f.controller)}` : ''} · ${esc(where)}</small></span></label>`;
  }).join('') || '<span class="muted">No fans found. Check the controller on the Controllers page.</span>';
}

function markDirty() {
  // visibility (not display) so the layout never shifts while a point is being dragged
  const box = ctx.el.querySelector('#c-dirty');
  if (box) box.style.visibility = dirty() || curveId === null ? 'visible' : 'hidden';
}

async function save() {
  const body = { ...draft, name: draft.name.trim() };
  if (!body.name) throw new Error('Give the curve a name');
  let id = curveId;
  if (curveId) await api.put(`/api/curves/${encodeURIComponent(curveId)}`, body);
  else id = (await api.post('/api/curves', body)).id;
  original = JSON.stringify(draft);
  await ctx.store.refreshConfig();
  toast(curveId ? 'Curve saved and applied' : 'Curve created');
  curveId = id;
  if (location.hash !== `#/curves/${id}`) { draft = null; ctx.navigate(`#/curves/${encodeURIComponent(id)}`); }
  else { paintList(); markDirty(); }
}

export function onState(st) {
  if (!ctx || !draft || !ctx.el.isConnected || !ctx.el.querySelector('#c-now')) return;
  const cur = currentInput();
  const now = ctx.el.querySelector('#c-now');
  if (cur.allIgnored) {
    now.innerHTML = '<span style="color:var(--danger)">All sensors of this curve are ignored → fans would run in <b>fail-safe</b>.</span>';
    graph.update({ current: null });
  } else if (cur.problem) {
    now.innerHTML = `<span style="color:var(--danger)">Sensor <b>${esc(cur.problem)}</b> has a problem → fans would run in <b>fail-safe</b>.</span>`;
    graph.update({ current: null });
  } else if (cur.temp === null) {
    now.innerHTML = draft.sensors.length ? 'All sensors are asleep → lowest point of the curve.' : '<span class="muted">Add a sensor to see the live value.</span>';
    graph.update({ current: null });
  } else {
    const pwm = Math.round(Math.max(draft.min_pwm || 0, evalCurve(draft.points, cur.temp, draft.type)));
    now.innerHTML = `Input now: <b>${esc(fmtTemp(Math.round(cur.temp * 10) / 10))}</b>${cur.top ? ` from ${esc(cur.top.split(':').slice(1).join(':'))}` : ''} → <b>${pwm}% PWM</b>`;
    graph.update({ current: cur.temp });
  }
  // Refresh chip values without touching the form.
  const chips = ctx.el.querySelector('#c-sensors');
  if (chips && document.activeElement?.id !== 'c-addsensor') paintSensors();
  if (st) paintListSoon();
}

let listTimer = null;
function paintListSoon() { clearTimeout(listTimer); listTimer = setTimeout(() => ctx && ctx.el.isConnected && paintList(), 50); }
