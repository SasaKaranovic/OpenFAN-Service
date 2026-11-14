// Backup / restore, engine settings and the optional login.
import { api } from '../api.js';
import { busy, confirmDialog, esc, fmtTime, toast } from '../ui.js';

const FIELDS = [
  ['update_interval', 'Control interval (s)', 'How often fan speeds are recalculated.', 1, 60, 0.5],
  ['stale_timeout', 'Sensor stale after (s)', 'A sensor without a valid reading for this long puts its fans in fail-safe.', 5, 3600, 1],
  ['failsafe_pwm', 'Fail-safe speed (%)', 'Speed used when something is wrong.', 50, 100, 1],
  ['exit_pwm', 'Speed when the service stops (%)', 'Set on managed fans when the container stops, so they are never left slow.', 0, 100, 1],
  ['keepalive_interval', 'Re-send speeds every (s)', 'Unchanged speeds are re-sent this often in case the controller was reset.', 2, 300, 1],
  ['history_interval', 'History sample every (s)', '', 2, 600, 1],
  ['history_retention_days', 'Keep history for (days)', '', 1, 365, 1],
  ['backups_to_keep', 'Automatic backups to keep', 'A backup is made every time the configuration is saved.', 1, 500, 1],
];

export async function render(el, { store }) {
  const cfg = await store.refreshConfig();
  const s = cfg.settings;
  el.innerHTML = `
    <div class="page-head"><div><h1>Backup &amp; settings</h1></div></div>
    <section class="card"><div class="card-head"><h2>Backup and restore</h2></div>
      <div class="card-body stack">
        <p class="muted" style="margin:0">The whole configuration (controllers, sensor sources, curves, fans, notifications and settings) is one file. Download it to keep a copy, or restore one to go back. History is not included.</p>
        <div class="inline"><a class="btn primary" href="api/backup" download>Download configuration</a>
          <label class="btn" for="b-file" style="font-weight:400">Restore from file…</label>
          <input id="b-file" type="file" accept=".yaml,.yml,text/yaml" class="sr-only"></div>
      </div>
      <div class="card-head" style="border-top:1px solid var(--border-soft)"><h2>Automatic backups</h2><span class="small muted">stored in /config/backups</span></div>
      <div class="table-wrap" id="b-list"><div class="card-body muted">Loading…</div></div>
    </section>
    <section class="card"><div class="card-head"><h2>Engine</h2></div>
      <div class="card-body stack"><div class="grid2">${FIELDS.map(([k, label, help, min, max, step]) => `
        <div class="field"><label for="st-${k}">${esc(label)}</label>
          <input id="st-${k}" data-k="${k}" type="number" min="${min}" max="${max}" step="${step}" value="${s[k]}">
          ${help ? `<span class="help">${esc(help)}</span>` : ''}</div>`).join('')}</div>
        <div class="inline"><span style="flex:1"></span><button type="button" class="btn primary" id="st-save">Save settings</button></div></div></section>
    <section class="card"><div class="card-head"><h2>Login</h2></div>
      <div class="card-body stack">
        <p class="muted" style="margin:0">Off by default, for a home network behind a firewall. When on, the web UI asks for a password. The push API keeps using its own per-source token.</p>
        <label class="check"><input type="checkbox" id="a-enabled" ${cfg.auth.enabled ? 'checked' : ''}> Require a password</label>
        <div class="field" style="max-width:360px"><label for="a-pass">${cfg.auth.has_password ? 'New password (leave empty to keep the current one)' : 'Password'}</label>
          <input id="a-pass" type="password" autocomplete="new-password" minlength="6"></div>
        <div class="inline"><span style="flex:1"></span>${cfg.auth.enabled ? '<button type="button" class="btn" id="a-logout">Sign out</button>' : ''}
          <button type="button" class="btn primary" id="a-save">Save login settings</button></div></div></section>`;

  const loadBackups = async () => {
    const list = await api.get('/api/backups');
    el.querySelector('#b-list').innerHTML = list.length ? `<table><thead><tr><th>Saved</th><th>File</th><th class="num">Size</th><th></th></tr></thead>
      <tbody>${list.slice(0, 50).map((b) => `<tr><td>${esc(fmtTime(b.modified))}</td><td class="mono small">${esc(b.name)}</td>
        <td class="num">${(b.size / 1024).toFixed(1)} KB</td>
        <td class="num"><a class="btn small" href="api/backups/${encodeURIComponent(b.name)}" download>Download</a>
        <button type="button" class="btn small" data-restore="${esc(b.name)}">Restore</button></td></tr>`).join('')}</tbody></table>`
      : '<div class="card-body muted">No automatic backups yet. One is made the first time you save a change.</div>';
  };
  loadBackups();
  el.querySelector('#b-list').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-restore]');
    if (!b) return;
    if (!(await confirmDialog('Restore this backup?', `The current configuration is backed up first, then replaced by <b>${esc(b.dataset.restore)}</b>.`, 'Restore'))) return;
    await busy(b, async () => {
      await api.post(`/api/backups/${encodeURIComponent(b.dataset.restore)}/restore`);
      await store.refreshConfig(); toast('Backup restored'); render(el, { store });
    });
  });
  el.querySelector('#b-file').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    if (!(await confirmDialog('Restore configuration?', `Replace the current configuration with <b>${esc(file.name)}</b>? The current one is backed up first.`, 'Restore'))) return;
    await busy(null, async () => {
      await api.postText('/api/restore', await file.text());
      await store.refreshConfig(); toast('Configuration restored'); render(el, { store });
    });
  });
  el.querySelector('#st-save').addEventListener('click', (e) => busy(e.target, async () => {
    const body = {};
    el.querySelectorAll('[data-k]').forEach((i) => { body[i.dataset.k] = Number(i.value); });
    await api.put('/api/settings', body);
    await store.refreshConfig(); toast('Settings saved');
  }));
  el.querySelector('#a-save').addEventListener('click', (e) => busy(e.target, async () => {
    await api.post('/api/auth', { enabled: el.querySelector('#a-enabled').checked, password: el.querySelector('#a-pass').value });
    await store.refreshConfig(); toast('Login settings saved'); render(el, { store });
  }));
  el.querySelector('#a-logout')?.addEventListener('click', async () => { await api.post('/api/logout'); location.reload(); });
}
