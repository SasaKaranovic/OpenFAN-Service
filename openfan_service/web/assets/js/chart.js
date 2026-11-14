// SVG charts: the dual-axis history chart and the draggable fan-curve editor graph.
import { esc, fmtTime } from './ui.js';

function niceTempRange(values) {
  if (!values.length) return [20, 60];
  let lo = Math.floor((Math.min(...values) - 3) / 10) * 10;
  let hi = Math.ceil((Math.max(...values) + 3) / 10) * 10;
  lo = Math.max(lo, -20);
  if (hi - lo < 20) hi = lo + 20;
  // Use a range divisible into 4 equal steps.
  const span = Math.ceil((hi - lo) / 20) * 20;
  return [lo, lo + span];
}

/** Turn [[ts, v], ...] into an SVG path, breaking the line across gaps. */
function pathFor(points, x, y, gap) {
  let d = '';
  let prev = null;
  for (const [ts, v] of points) {
    if (v === null || v === undefined) { prev = null; continue; }
    const cmd = prev === null || ts - prev > gap ? 'M' : 'L';
    d += `${cmd}${x(ts).toFixed(1)},${y(v).toFixed(1)} `;
    prev = ts;
  }
  return d.trim();
}

/**
 * Render a history chart into `el`.
 * series: { temp: [[ts,v]], pwm: [[ts,v]], fs: [[ts,0|1]] }
 */
export function renderHistory(el, { since, until, series, tempLabel }) {
  // On a fresh install there is less history than the chosen range: start at the first sample.
  const firstTs = Math.min(...[...(series.temp || []), ...(series.pwm || [])].map((p) => p[0]));
  if (Number.isFinite(firstTs) && firstTs > since) since = Math.max(since, firstTs - 30);
  if (until - since < 300) since = until - 300;
  const W = 1000, H = 210, TOP = 10, BOT = 200;
  const temps = (series.temp || []).map((p) => p[1]).filter((v) => v !== null);
  const [tLo, tHi] = niceTempRange(temps);
  const x = (ts) => ((ts - since) / (until - since)) * W;
  const yT = (v) => BOT - ((v - tLo) / (tHi - tLo)) * (BOT - TOP);
  const yP = (v) => BOT - (v / 100) * (BOT - TOP);
  const all = [...(series.temp || []), ...(series.pwm || [])].map((p) => p[0]).sort((a, b) => a - b);
  let step = 10;
  for (let i = 1; i < all.length; i++) { const d = all[i] - all[i - 1]; if (d > 0) { step = d; break; } }
  const gap = Math.max(step * 3, 60);

  // Fail-safe shading
  let shade = '';
  let start = null;
  const fs = series.fs || [];
  fs.forEach(([ts, v], i) => {
    if (v >= 1 && start === null) start = ts;
    const end = v < 1 || i === fs.length - 1;
    if (start !== null && end) {
      const stop = v < 1 ? ts : Math.min(until, ts + step);
      shade += `<rect x="${x(start).toFixed(1)}" y="0" width="${Math.max(2, x(stop) - x(start)).toFixed(1)}" height="${H}" fill="var(--danger-shade)"/>`;
      start = null;
    }
  });

  const grid = [0, 1, 2, 3, 4].map((i) => {
    const y = TOP + (i * (BOT - TOP)) / 4;
    return `<line x1="0" y1="${y}" x2="${W}" y2="${y}" stroke="${i === 4 ? 'var(--border)' : 'var(--border-soft)'}" vector-effect="non-scaling-stroke"/>`;
  }).join('');
  const tempPath = pathFor(series.temp || [], x, yT, gap);
  const pwmPath = pathFor(series.pwm || [], x, yP, gap);
  const tTicks = [4, 3, 2, 1, 0].map((i) => `<span>${Math.round(tLo + (i * (tHi - tLo)) / 4)}</span>`).join('');
  const pTicks = [100, 75, 50, 25, 0].map((v) => `<span>${v}%</span>`).join('');
  const xTicks = [0, 1, 2, 3, 4, 5, 6].map((i) => `<span>${esc(fmtTime(since + (i * (until - since)) / 6))}</span>`).join('');

  el.innerHTML = `
    <div class="chart-axes"><span>Temperature (°C)</span><span>Fan speed (PWM %)</span></div>
    <div class="chart-row">
      <div class="yaxis left">${tTicks}</div>
      <div style="flex:1 1 auto;min-width:0;position:relative">
        <svg class="chart-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img"
          aria-label="${esc(`History. Left axis: ${tempLabel || 'temperature'}. Right axis: fan speed.`)}">
          ${shade}${grid}
          <path d="${tempPath}" fill="none" stroke="var(--orange)" stroke-width="2.5" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
          <path d="${pwmPath}" fill="none" stroke="var(--line2)" stroke-width="2" stroke-dasharray="6 4" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
          <line class="cursor" x1="-10" x2="-10" y1="0" y2="${H}" stroke="var(--accent)" vector-effect="non-scaling-stroke"/>
        </svg>
        <div class="xaxis">${xTicks}</div>
      </div>
      <div class="yaxis">${pTicks}</div>
    </div>`;

  // Hover read-out
  const svg = el.querySelector('svg');
  const cursor = svg.querySelector('.cursor');
  let tip = null;
  const nearest = (arr, ts) => {
    let best = null;
    for (const p of arr || []) { if (best === null || Math.abs(p[0] - ts) < Math.abs(best[0] - ts)) best = p; }
    return best && Math.abs(best[0] - ts) <= gap ? best : null;
  };
  svg.addEventListener('pointermove', (e) => {
    const r = svg.getBoundingClientRect();
    const ts = since + ((e.clientX - r.left) / r.width) * (until - since);
    const t = nearest(series.temp, ts), p = nearest(series.pwm, ts), rpm = nearest(series.rpm, ts);
    if (!t && !p) { if (tip) tip.hidden = true; return; }
    const at = (t || p)[0];
    cursor.setAttribute('x1', x(at)); cursor.setAttribute('x2', x(at));
    if (!tip) { tip = document.createElement('div'); tip.className = 'chart-tip'; document.body.appendChild(tip); }
    tip.hidden = false;
    tip.innerHTML = `<b>${esc(fmtTime(at))}</b><br>${t && t[1] !== null ? `${t[1].toFixed(1)} °C` : '—'} · ${p && p[1] !== null ? `${Math.round(p[1])}%` : '—'}${rpm && rpm[1] !== null ? ` · ${Math.round(rpm[1])} RPM` : ''}`;
    tip.style.left = `${Math.min(window.innerWidth - 180, e.clientX + 12)}px`;
    tip.style.top = `${e.clientY + 12}px`;
  });
  svg.addEventListener('pointerleave', () => { if (tip) tip.hidden = true; cursor.setAttribute('x1', -10); cursor.setAttribute('x2', -10); });
  // Remove stray tooltip when the chart is re-rendered.
  const obs = new MutationObserver(() => { if (!el.contains(svg)) { tip && tip.remove(); obs.disconnect(); } });
  obs.observe(el, { childList: true });
}

// ---------------------------------------------------------------------------
/** Evaluate a curve exactly like the service does (curves.py). */
export function evalCurve(points, temp, type = 'linear') {
  if (!points.length) return 0;
  if (temp <= points[0][0]) return points[0][1];
  if (temp >= points[points.length - 1][0]) return points[points.length - 1][1];
  if (type === 'step') {
    let v = points[0][1];
    for (const [t, p] of points) { if (temp >= t) v = p; else break; }
    return v;
  }
  for (let i = 1; i < points.length; i++) {
    const [t1, v1] = points[i - 1], [t2, v2] = points[i];
    if (temp <= t2) return v1 + ((v2 - v1) * (temp - t1)) / (t2 - t1);
  }
  return points[points.length - 1][1];
}

/**
 * Interactive curve editor graph.
 * opts: { points, type, minPwm, hysteresis, current (°C or null), onChange(points) }
 */
export class CurveGraph {
  constructor(el, opts) {
    this.el = el;
    this.o = opts;
    this.W = 640; this.H = 300; this.L = 46; this.R = 620; this.T = 12; this.B = 256;
    this.computeRange();
    this.render();
    this.bind();
  }

  computeRange() {
    const temps = this.o.points.map((p) => p[0]);
    if (this.o.current !== null && this.o.current !== undefined) temps.push(this.o.current);
    this.tMin = Math.min(20, Math.floor((Math.min(...temps, 20) - 5) / 10) * 10);
    this.tMax = Math.max(70, Math.ceil((Math.max(...temps, 60) + 5) / 10) * 10);
  }

  x(t) { return this.L + ((t - this.tMin) / (this.tMax - this.tMin)) * (this.R - this.L); }
  y(p) { return this.B - (p / 100) * (this.B - this.T); }
  tAt(px) { return this.tMin + ((px - this.L) / (this.R - this.L)) * (this.tMax - this.tMin); }
  pAt(py) { return ((this.B - py) / (this.B - this.T)) * 100; }

  update(opts) { Object.assign(this.o, opts); this.render(); }

  linePoints() {
    const pts = this.o.points;
    if (!pts.length) return '';
    if (this.o.type === 'step') {
      let d = `${this.L},${this.y(pts[0][1])}`;
      pts.forEach(([t, p], i) => {
        if (i > 0) d += ` ${this.x(t)},${this.y(pts[i - 1][1])}`;
        d += ` ${this.x(t)},${this.y(p)}`;
      });
      return d + ` ${this.R},${this.y(pts[pts.length - 1][1])}`;
    }
    return [`${this.L},${this.y(pts[0][1])}`, ...pts.map(([t, p]) => `${this.x(t)},${this.y(p)}`),
      `${this.R},${this.y(pts[pts.length - 1][1])}`].join(' ');
  }

  render() {
    const { L, R, T, B } = this;
    const span = this.tMax - this.tMin;
    const stepT = span > 80 ? 20 : 10;
    let grid = '';
    for (let p = 0; p <= 100; p += 25) {
      grid += `<line x1="${L}" y1="${this.y(p)}" x2="${R}" y2="${this.y(p)}" stroke="var(--border-soft)"/>`;
      grid += `<text x="${L - 8}" y="${this.y(p) + 4}" font-size="11" fill="var(--axis)" text-anchor="end">${p}%</text>`;
    }
    for (let t = Math.ceil(this.tMin / stepT) * stepT; t <= this.tMax; t += stepT) {
      grid += `<line x1="${this.x(t)}" y1="${T}" x2="${this.x(t)}" y2="${B}" stroke="var(--border-soft)"/>`;
      grid += `<text x="${this.x(t)}" y="${B + 18}" font-size="11" fill="var(--axis)" text-anchor="middle">${t} °C</text>`;
    }
    let extras = '';
    const minP = Number(this.o.minPwm) || 0;
    if (minP > 0) {
      extras += `<line x1="${L}" y1="${this.y(minP)}" x2="${R}" y2="${this.y(minP)}" stroke="var(--faint)" stroke-dasharray="4 4"/>
        <text x="${R - 4}" y="${this.y(minP) - 5}" font-size="11" fill="var(--muted)" text-anchor="end">min ${minP}%</text>`;
    }
    const cur = this.o.current;
    if (cur !== null && cur !== undefined && Number.isFinite(cur)) {
      const h = Number(this.o.hysteresis) || 0;
      const xc = this.x(Math.max(this.tMin, Math.min(this.tMax, cur)));
      const pw = Math.max(minP, evalCurve(this.o.points, this.o.effTemp ?? cur, this.o.type));
      if (h > 0) extras += `<rect x="${Math.max(L, this.x(cur - h))}" y="${T}" width="${Math.max(0, xc - Math.max(L, this.x(cur - h)))}" height="${B - T}" fill="var(--accent-soft)"/>`;
      const label = `${cur.toFixed(1)} °C → ${Math.round(pw)}%`;
      const lx = xc + 120 > R ? xc - 124 : xc + 8;
      const ly = this.y(pw) - 30 < T ? this.y(pw) + 10 : this.y(pw) - 30;  // keep the label inside the plot
      extras += `<line x1="${xc}" y1="${T}" x2="${xc}" y2="${B}" stroke="var(--orange)" stroke-width="2"/>
        <circle cx="${xc}" cy="${this.y(pw)}" r="6" fill="var(--orange)"/>
        <rect x="${lx}" y="${ly}" width="116" height="22" rx="4" fill="var(--text)"/>
        <text x="${lx + 58}" y="${ly + 15}" font-size="12" font-weight="600" fill="var(--bg)" text-anchor="middle">${esc(label)}</text>`;
    }
    const handles = this.o.points.map(([t, p], i) =>
      `<circle class="pt" data-i="${i}" tabindex="0" role="slider" aria-label="Point ${i + 1}: ${t} °C, ${p}%"
        aria-valuenow="${p}" cx="${this.x(t)}" cy="${this.y(p)}" r="8" fill="var(--surface)" stroke="var(--accent)" stroke-width="2.5"/>`).join('');

    this.el.innerHTML = `<svg class="curve-graph" viewBox="0 0 ${this.W} ${this.H}" role="group" aria-label="Fan curve graph. Drag the points; click an empty spot to add one.">
      <rect class="bg" x="${L}" y="${T}" width="${R - L}" height="${B - T}" fill="transparent"/>
      ${extras}${grid}
      <line x1="${L}" y1="${B}" x2="${R}" y2="${B}" stroke="var(--faint)"/><line x1="${L}" y1="${T}" x2="${L}" y2="${B}" stroke="var(--faint)"/>
      <polyline fill="none" stroke="var(--accent)" stroke-width="3" stroke-linejoin="round" points="${this.linePoints()}" pointer-events="none"/>
      ${handles}
      ${cur !== null && cur !== undefined && Number(this.o.hysteresis) > 0 ? `<text x="${(L + R) / 2}" y="${this.H - 4}" font-size="11" fill="var(--muted)" text-anchor="middle">shaded = ${Number(this.o.hysteresis)} °C hysteresis band</text>` : ''}
    </svg>`;
  }

  svgPoint(e) {
    const svg = this.el.querySelector('svg');
    const r = svg.getBoundingClientRect();
    return [((e.clientX - r.left) / r.width) * this.W, ((e.clientY - r.top) / r.height) * this.H];
  }

  setPoint(i, t, p) {
    const pts = this.o.points.map((q) => [...q]);
    const lo = i > 0 ? pts[i - 1][0] + 1 : this.tMin;
    const hi = i < pts.length - 1 ? pts[i + 1][0] - 1 : this.tMax;
    pts[i] = [Math.round(Math.max(lo, Math.min(hi, t))), Math.round(Math.max(0, Math.min(100, p)))];
    this.o.points = pts;
    this.o.onChange(pts);
    this.render();
  }

  bind() {
    // Listeners live on the container, which survives re-renders during a drag.
    const el = this.el;
    let drag = null;
    el.addEventListener('pointerdown', (e) => {
      if (!e.target.closest('svg')) return;
      const c = e.target.closest('.pt');
      if (c) {
        drag = Number(c.dataset.i);
        el.setPointerCapture(e.pointerId);
        e.preventDefault();
        return;
      }
      const [px, py] = this.svgPoint(e);
      if (px < this.L || px > this.R || py < this.T || py > this.B) return;
      const t = Math.round(this.tAt(px));
      if (this.o.points.some((q) => q[0] === t) || this.o.points.length >= 32) return;
      const pts = [...this.o.points.map((q) => [...q]), [t, Math.round(Math.max(0, Math.min(100, this.pAt(py))))]]
        .sort((a, b) => a[0] - b[0]);
      this.o.points = pts;
      this.o.onChange(pts);
      this.render();
    });
    el.addEventListener('pointermove', (e) => {
      if (drag === null) return;
      const [px, py] = this.svgPoint(e);
      this.setPoint(drag, this.tAt(px), this.pAt(py));
    });
    const end = () => { drag = null; };
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
    el.addEventListener('keydown', (e) => {
      const c = e.target.closest('.pt');
      if (!c) return;
      const i = Number(c.dataset.i);
      const [t, p] = this.o.points[i];
      const d = { ArrowUp: [0, 1], ArrowDown: [0, -1], ArrowLeft: [-1, 0], ArrowRight: [1, 0] }[e.key];
      if (!d) return;
      e.preventDefault();
      this.setPoint(i, t + d[0], p + d[1] * (e.shiftKey ? 5 : 1));
      this.el.querySelector(`.pt[data-i="${i}"]`)?.focus();
    });
  }
}

