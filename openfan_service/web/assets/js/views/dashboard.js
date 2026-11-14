// Dashboard: alerts, fan cards, sensors, events and per-fan history.
import { api } from '../api.js';
import { renderHistory } from '../chart.js';
import { busy, chip, confirmDialog, esc, fmtAge, fmtTemp, fmtTime, toast } from '../ui.js';

const RANGES = [['1', '1 h'], ['6', '6 h'], ['24', '24 h'], ['168', '7 d']];
const COMBINE = { max: 'highest', avg: 'average', min: 'lowest' };
let root = null;
let selected = null;
let hours = '24';
let lastChartAt = 0;
let lastChartKey = '';

function load(key, def) { try { return localStorage.getItem(key) || def; } catch (e) { return def; } }
function store(key, v) { try { localStorage.setItem(key, v); } catch (e) { /* ignore */ } }

export async function render(el) {
  root = el;
  selected = load('openfan.historyFan', null);
  hours = load('openfan.historyHours', '24');
  lastChartKey = '';
  el.innerHTML = `
    <div id="d-alerts" class="stack"></div>
    <section class="tiles" id="d-tiles" aria-label="Summary"></section>
    <div class="row">
      <section class="card grow" aria-label="Fans">
        <div class="card-head"><h2>Fans</h2>
          <div class="inline"><button type="button" class="btn small" id="d-override">Set all to 100%</button></div></div>
        <div id="d-fans"></div>
      </section>
      <div class="side">
        <section class="card" aria-label="Sensors">
          <div class="card-head"><h2>Sensors</h2><span class="small muted" id="d-sensor-count"></span></div>
          <div class="table-wrap" id="d-sensors"></div>
        </section>
        <section class="card" aria-label="Recent events">
          <div class="card-head"><h2>Recent events</h2>
            <button type="button" class="btn small" id="d-clear-events" title="Remove all events from the list">Clear</button></div>
          <ul class="events" id="d-events"></ul>
        </section>
      </div>
    </div>
    <section class="card" aria-label="History">
      <div class="card-head">
        <h2 id="d-hist-title">History</h2>
        <div class="legend">
          <span id="d-legend"></span>
          <div class="seg" role="group" aria-label="Time range" id="d-range">
            ${RANGES.map(([v, l]) => `<button type="button" data-h="${v}" aria-pressed="${v === hours}">${l}</button>`).join('')}
          </div>
        </div>
      </div>
      <div id="d-chart"></div>
      <div class="small muted" style="padding:0 18px 14px">Solid line: the temperature the fan's curve uses (left axis). Dashed line: fan speed (right axis). Shaded = fail-safe. Click a fan above to see its history.</div>
    </section>`;

  el.querySelector('#d-fans').addEventListener('click', (e) => {
    const card = e.target.closest('[data-fan]');
    if (!card) return;
    selected = card.dataset.fan;
    store('openfan.historyFan', selected);
    root.querySelectorAll('.fan').forEach((f) => {
      const on = f.dataset.fan === selected;
      f.classList.toggle('selected', on);
      f.setAttribute('aria-pressed', on);
    });
    lastChartKey = '';
    drawHistory(window.__openfanState);
  });
  el.querySelector('#d-range').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-h]');
    if (!b) return;
    hours = b.dataset.h;
    store('openfan.historyHours', hours);
    el.querySelectorAll('#d-range button').forEach((x) => x.setAttribute('aria-pressed', x.dataset.h === hours));
    lastChartKey = '';
    drawHistory(window.__openfanState);
  });
  el.querySelector('#d-override').addEventListener('click', async (e) => {
    const st = window.__openfanState;
    if (st && st.engine.override) {
      await busy(e.target, () => api.del('/api/override'));
      toast('Override cancelled; fans are back on their curves');
      return;
    }
    const yes = await confirmDialog('Set all fans to 100%?',
      'Every fan runs at full speed for 10 minutes, then returns to its curve. You can cancel it any time.', 'Run at 100%');
    if (yes) { await busy(e.target, () => api.post('/api/override', { pwm: 100, minutes: 10 })); toast('All fans at 100% for 10 minutes'); }
  });
  el.querySelector('#d-clear-events').addEventListener('click', async (e) => {
    if (!(await confirmDialog('Clear recent events?',
      'All events are removed from the list. This does not change fan control or any active fail-safe.', 'Clear events'))) return;
    await busy(e.target, async () => {
      await api.del('/api/events');
      root.querySelector('#d-events').innerHTML = '<li class="muted">Nothing yet.</li>';
      toast('Events cleared');
    });
  });
  el.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-cancel-override]');
    if (b) { await busy(b, () => api.del('/api/override')); toast('Override cancelled'); }
  });
}

function sensorText(f) {
  if (!f.curve) return '—';
  if (!f.sensors.length) return 'no sensors';
  const n = f.sensors.length;
  const who = f.sensor ? f.sensor.split(':').slice(1).join(':') : (n === 1 ? f.sensors[0].split(':').slice(1).join(':') : f.curve_name);
  return n > 1 ? `${who} (${COMBINE[f.combine] || f.combine} of ${n})` : who;
}

function fanCard(f) {
  const off = f.state === 'unmanaged' || f.state === 'offline';
  const bad = f.state === 'failsafe' || f.state === 'offline';
  let temp = fmtTemp(f.temp);
  let tempCls = '';
  if (f.state === 'failsafe') { temp = f.temp === null ? 'fault' : temp; tempCls = 'bad'; }
  if (f.state === 'unmanaged' || (f.state === 'offline' && f.temp === null)) { temp = '—'; tempCls = 'off'; }
  const pwm = f.pwm === null || f.pwm === undefined ? '—' : `${f.pwm}%`;
  const rpm = f.rpm === null || f.rpm === undefined ? '— RPM' : `${f.rpm} RPM`;
  const title = f.reasons.length ? f.reasons.join('\n') : (f.note || `Show history for ${f.name}`);
  return `<button type="button" class="fan ${f.state} ${f.key === selected ? 'selected' : ''}" data-fan="${esc(f.key)}"
      aria-pressed="${f.key === selected}" title="${esc(title)}">
    <span class="top"><span style="min-width:0"><span class="name">${esc(f.name)}</span>
      <span class="small muted">Fan ${f.index}${f.controller !== 'main' ? ` · ${esc(f.controller)}` : ''} · ${esc(rpm)}</span></span>${chip(f.state)}</span>
    <span class="vals">
      <span><span class="lbl">Speed</span><span class="big ${off ? 'off' : ''}">${pwm}</span></span>
      <span><span class="lbl">Temp</span><span class="big ${tempCls}">${esc(temp)}</span></span>
    </span>
    <span class="bar ${bad ? 'bad' : ''}"><span style="width:${off && f.pwm === null ? 0 : (f.pwm || 0)}%"></span></span>
    <span class="meta"><span style="display:block">Curve: <b>${esc(f.curve_name || (f.curve ? `${f.curve} (deleted)` : '—'))}</b></span>
      <span style="display:block">Sensor: <b>${esc(sensorText(f))}</b></span></span>
  </button>`;
}

function alerts(st) {
  const out = [];
  for (const w of st.warnings) out.push(`<div class="banner warn"><div class="grow">${esc(w)}</div></div>`);
  const fs = st.fans.filter((f) => f.state === 'failsafe');
  if (fs.length) {
    const reasons = fs.map((f) => `<li><b>${esc(f.name)}</b>: ${esc(f.reasons.join('; '))}</li>`).join('');
    out.push(`<section class="banner danger" aria-label="Fail-safe alert">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--danger)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4"/><path d="M12 17.5v.01"/></svg>
      <div class="grow"><strong>Fail-safe active: ${fs.length} fan${fs.length > 1 ? 's' : ''} forced to ${st.fans.find((f) => f.state === 'failsafe').pwm}%</strong>
      <div class="body"><ul style="margin:4px 0 0;padding-left:18px">${reasons}</ul>
      They return to their curves automatically once the problem is fixed.${st.notifications.enabled ? ' A notification was sent.' : ''}</div></div>
      <a class="btn small" href="#/sensors">View sensors</a></section>`);
  }
  for (const c of st.controllers.filter((x) => !x.connected)) {
    out.push(`<section class="banner danger"><div class="grow"><strong>Controller "${esc(c.name)}" is not connected</strong>
      <div class="body">${esc(c.target || '')} — ${esc(c.error || 'connecting…')}. Its fans cannot be controlled until it is back.</div></div>
      <a class="btn small" href="#/controllers">Controllers</a></section>`);
  }
  if (!st.controllers.length) {
    out.push(`<div class="banner info"><div class="grow"><strong>Welcome!</strong> Add your OpenFAN controller to get started.</div><a class="btn small primary" href="#/controllers">Add controller</a></div>`);
  } else if (!st.sources.length) {
    out.push(`<div class="banner info"><div class="grow"><strong>Next step:</strong> add a sensor source (Unraid disks, CPU, script...) so curves have temperatures to follow.</div><a class="btn small primary" href="#/sensors">Add sensors</a></div>`);
  }
  if (st.engine.override) {
    const left = Math.max(0, Math.round((st.engine.override.until - st.time) / 60));
    out.push(`<div class="banner warn"><div class="grow"><strong>Manual override:</strong> all fans at ${st.engine.override.pwm}% for ${left} more minute${left === 1 ? '' : 's'}.</div>
      <button type="button" class="btn small" data-cancel-override>Cancel override</button></div>`);
  }
  if (st.engine.error) out.push(`<div class="banner warn"><div class="grow">Last control cycle error: ${esc(st.engine.error)}</div></div>`);
  return out.join('');
}

export function onState(st) {
  if (!root || !root.isConnected) return;
  window.__openfanState = st;
  root.querySelector('#d-alerts').innerHTML = alerts(st);

  const managed = st.fans.filter((f) => f.curve);
  const fs = st.fans.filter((f) => f.state === 'failsafe');
  const since = fs.length ? Math.min(...fs.map((f) => f.failsafe_since || st.time)) : null;
  const staleCount = st.sensors.filter((s) => ['stale', 'error', 'missing'].includes(s.status)).length;
  root.querySelector('#d-tiles').innerHTML = `
    <div class="card tile"><div class="k">Fans under control</div><div class="v">${managed.length} <small>/ ${st.fans.length}</small></div>
      <div class="s">${st.fans.length - managed.length} not assigned to a curve</div></div>
    <div class="card tile"><div class="k">Fail-safe</div><div class="v ${fs.length ? 'bad' : ''}">${fs.length ? `${fs.length} fan${fs.length > 1 ? 's' : ''}` : 'None'}</div>
      <div class="s">${fs.length ? `Since ${esc(fmtTime(since))} · see the reasons above` : (staleCount ? `${staleCount} sensor${staleCount === 1 ? '' : 's'} with problems (not used by any fan)` : 'All curves have valid readings')}</div></div>
    <div class="card tile"><div class="k">Control loop</div><div class="v">${st.engine.starting ? 'Starting' : (st.engine.running ? 'Running' : 'Stalled')}</div>
      <div class="s">Every ${st.engine.interval} s · last cycle ${fmtAge(st.engine.last_cycle_age)} ago</div></div>`;

  if (!st.fans.length) {
    root.querySelector('#d-fans').innerHTML = '<div class="empty" style="height:140px">No fans found. Check the controller connection on the Controllers page.</div>';
  } else {
    if (!selected || !st.fans.some((f) => f.key === selected)) selected = st.fans[0].key;
    root.querySelector('#d-fans').innerHTML = `<div class="fan-grid">${st.fans.map(fanCard).join('')}</div>`;
  }
  root.querySelector('#d-override').textContent = st.engine.override ? 'Cancel 100% override' : 'Set all to 100%';

  // Ignored sensors are hidden here; they are listed (and can be restored) on the Sensors page.
  const shown = st.sensors.filter((s) => !s.ignored);
  const nIgnored = st.sensors.length - shown.length;
  root.querySelector('#d-sensor-count').textContent = `${st.sources.length} source${st.sources.length === 1 ? '' : 's'} · ${shown.length} sensors` +
    (nIgnored ? ` · ${nIgnored} ignored` : '');
  root.querySelector('#d-sensors').innerHTML = shown.length ? `<table>
    <thead><tr><th>Sensor</th><th>Source</th><th class="num">Value</th><th class="num">Age</th></tr></thead>
    <tbody>${shown.map((s) => {
      const bad = ['stale', 'error', 'missing'].includes(s.status);
      const val = s.status === 'sleeping' ? (s.value === null ? 'asleep' : `${fmtTemp(s.value)} (*)`) : fmtTemp(s.value);
      return `<tr class="${bad ? 'bad' : ''}" title="${esc(s.message || s.label || '')}"><td class="mono small">${esc(s.id)}</td><td class="muted">${esc(s.source)}</td>
        <td class="num"><b>${esc(val)}</b></td><td class="num age muted">${bad ? esc(s.status) : esc(fmtAge(s.age))}</td></tr>`;
    }).join('')}</tbody></table>` : `<div class="card-body muted">${nIgnored ? 'All sensors are ignored.' : 'No sensors yet.'} <a href="#/sensors">Sensors page</a>.</div>`;

  root.querySelector('#d-events').innerHTML = st.events.length ? st.events.slice(0, 15).map((ev) =>
    `<li class="${esc(ev.kind)} ${esc(ev.level)}"><time class="mono">${esc(fmtTime(ev.ts))}</time><span>${ev.level === 'error' ? '<b>●</b> ' : ''}${esc(ev.message)}</span></li>`).join('')
    : '<li class="muted">Nothing yet.</li>';

  drawHistory(st);
}

async function drawHistory(st) {
  if (!st || !root) return;
  const chart = root.querySelector('#d-chart');
  const title = root.querySelector('#d-hist-title');
  const legend = root.querySelector('#d-legend');
  const fan = st.fans.find((f) => f.key === selected);
  if (!fan) {
    title.textContent = 'History';
    legend.innerHTML = '';
    chart.innerHTML = '<div class="empty">No fans data</div>';
    lastChartKey = 'none';
    return;
  }
  title.textContent = `History – ${fan.name}`;
  if (!fan.curve) {
    legend.innerHTML = '';
    chart.innerHTML = `<div class="empty">No history for ${esc(fan.name)}: it is not assigned to a curve.</div>`;
    lastChartKey = `${fan.key}:none`;
    return;
  }
  const key = `${fan.key}:${hours}`;
  const now = Date.now();
  if (key === lastChartKey && now - lastChartAt < 30000) return;
  lastChartKey = key;
  lastChartAt = now;
  const tempLabel = fan.sensors.length > 1 ? `${fan.curve_name || 'curve'} input °C (${COMBINE[fan.combine]} of ${fan.sensors.length})`
    : `${(fan.sensors[0] || '').split(':').slice(1).join(':') || 'input'} °C`;
  legend.innerHTML = `<span><span class="sw temp"></span>${esc(tempLabel)}</span><span><span class="sw pwm"></span>Fan speed %</span>`;
  try {
    const data = await api.get(`/api/history?fan=${encodeURIComponent(fan.key)}&hours=${hours}&points=400`);
    if (lastChartKey !== key) return;  // selection changed while loading
    const hasData = (data.series.temp || []).length || (data.series.pwm || []).length;
    if (!hasData) { chart.innerHTML = '<div class="empty">No history recorded yet for this fan. It appears after a minute or so.</div>'; return; }
    chart.innerHTML = '<div class="chart-wrap"></div>';
    renderHistory(chart.firstChild, { ...data, tempLabel });
  } catch (e) {
    chart.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

export function onTheme() { lastChartKey = ''; drawHistory(window.__openfanState); }
