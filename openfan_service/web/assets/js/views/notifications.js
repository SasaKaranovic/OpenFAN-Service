// Notifications through Apprise.
import { api } from '../api.js';
import { busy, esc, toast } from '../ui.js';

export async function render(el, { store }) {
  const cfg = await store.refreshConfig();
  const n = cfg.notifications;
  el.innerHTML = `
    <div class="page-head"><div><h1>Notifications</h1>
      <p>Get a message when fans go into fail-safe, when they recover and when the controller connection drops.</p></div></div>
    <section class="card"><div class="card-body stack">
      <label class="check"><input type="checkbox" id="n-enabled" ${n.enabled ? 'checked' : ''}> Send notifications</label>
      <div class="field"><label for="n-urls">Notification URLs <span class="help">One per line. Uses <a href="https://github.com/caronc/apprise/wiki" target="_blank" rel="noopener">Apprise</a>, which supports 100+ services.</span></label>
        <textarea id="n-urls" rows="4" placeholder="tgram://bot_token/chat_id">${esc(n.urls.join('\n'))}</textarea></div>
      <fieldset style="border:0;padding:0;margin:0" class="stack"><legend style="font-weight:600;font-size:13px;margin-bottom:8px">Notify me when</legend>
        <label class="check"><input type="checkbox" id="n-fs" ${n.on_failsafe ? 'checked' : ''}> Fans go into fail-safe</label>
        <label class="check"><input type="checkbox" id="n-rec" ${n.on_recovery ? 'checked' : ''}> Fans recover from fail-safe</label>
        <label class="check"><input type="checkbox" id="n-ctrl" ${n.on_controller ? 'checked' : ''}> The controller is unreachable or reconnects</label>
      </fieldset>
      <div class="inline"><button type="button" class="btn" id="n-test">Send test message</button><span style="flex:1"></span>
        <button type="button" class="btn primary" id="n-save">Save</button></div>
    </div></section>
    <section class="card"><div class="card-head"><h2>URL examples</h2></div><div class="card-body small">
      <dl class="kv">
        <dt>Telegram</dt><dd><code>tgram://&lt;bot token&gt;/&lt;chat id&gt;</code></dd>
        <dt>Discord</dt><dd><code>discord://&lt;webhook id&gt;/&lt;webhook token&gt;</code></dd>
        <dt>ntfy</dt><dd><code>ntfys://ntfy.sh/&lt;your topic&gt;</code></dd>
        <dt>Pushover</dt><dd><code>pover://&lt;user key&gt;@&lt;app token&gt;</code></dd>
        <dt>Gotify</dt><dd><code>gotifys://&lt;host&gt;/&lt;app token&gt;</code></dd>
        <dt>E-mail</dt><dd><code>mailtos://&lt;user&gt;:&lt;app password&gt;@gmail.com</code></dd>
        <dt>Slack</dt><dd><code>slack://&lt;token A&gt;/&lt;token B&gt;/&lt;token C&gt;</code></dd>
        <dt>WhatsApp</dt><dd><code>whatsapp://&lt;token&gt;@&lt;from phone id&gt;/&lt;to number&gt;</code> (needs a Meta WhatsApp Business account)</dd>
      </dl></div></section>`;
  const urls = () => el.querySelector('#n-urls').value.split('\n').map((s) => s.trim()).filter(Boolean);
  el.querySelector('#n-test').addEventListener('click', (e) => busy(e.target, async () => {
    await api.post('/api/notifications/test', { urls: urls() });
    toast('Test message sent');
  }));
  el.querySelector('#n-save').addEventListener('click', (e) => busy(e.target, async () => {
    await api.put('/api/notifications', {
      enabled: el.querySelector('#n-enabled').checked, urls: urls(),
      on_failsafe: el.querySelector('#n-fs').checked, on_recovery: el.querySelector('#n-rec').checked,
      on_controller: el.querySelector('#n-ctrl').checked,
    });
    await store.refreshConfig();
    toast('Notification settings saved');
  }));
}
