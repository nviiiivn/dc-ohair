// D.C. O'HAIR settings page, login screen and first-run wizard.
// Everything here talks to the hub's JSON API; app.js listens for the
// "dc:changed" event and reloads the city after a save or a scan.

const THEMES = ['dark', 'light', 'sepia', 'cyberpunk', 'matrix', 'ocean'];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const GiB = 1024 ** 3;
const size = (b) => (b >= 1024 * GiB ? (b / 1024 / GiB).toFixed(2) + 'T' : Math.round(b / GiB) + 'G');
const slug = (s) => String(s).toLowerCase().replace(/[^a-z0-9_.-]+/g, '-').replace(/^[-.]+|-+$/g, '').slice(0, 60) || 'device';
const list = (v) => (Array.isArray(v) ? v.join(', ') : v || '');
const unlist = (v) => String(v).split(',').map((x) => x.trim()).filter(Boolean);
const changed = () => document.dispatchEvent(new CustomEvent('dc:changed'));

export async function api(path, body, method) {
  const opts = body === undefined && !method ? {} : {
    method: method || 'POST', headers: { 'X-DC': '1', 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
  };
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const err = new Error((data && data.error) || res.statusText || 'Something went wrong.');
    err.status = res.status;
    throw err;
  }
  return data;
}

function el(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function copyBox(text, label = 'Copy') {
  return `<div class="copy"><pre>${esc(text)}</pre><button type="button" class="ghost" data-copy="${esc(text)}">${label}</button></div>`;
}

document.addEventListener('click', async (e) => {
  const b = e.target.closest('[data-copy]');
  if (!b) return;
  try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = 'Copied'; } catch { b.textContent = 'Select and copy'; }
  setTimeout(() => { b.textContent = 'Copy'; }, 1600);
});

// ---------- login ----------

export function showLogin(me) {
  const box = el(`<div class="overlay" role="dialog" aria-modal="true" aria-labelledby="loginTitle">
    <form class="sheet small">
      <img src="/logo.svg" alt="" width="56" height="56">
      <h2 id="loginTitle">D.C. O'HAIR</h2>
      <p class="muted">${me.password_set ? 'Log in to see your drives.' : 'Login is on, but no password is set yet. Set one with: python3 hub.py --set-password'}</p>
      ${me.password_set ? '<label>Password <input type="password" name="pw" autocomplete="current-password" required></label><p class="err" role="alert"></p><button class="primary" type="submit">Log in</button>' : ''}
    </form></div>`);
  document.body.append(box);
  const form = box.querySelector('form');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await api('/api/login', { password: form.pw.value });
      box.remove();
      changed();
    } catch (err) {
      form.querySelector('.err').textContent = err.message;
    }
  });
  form.pw && form.pw.focus();
}

// ---------- shared device editor ----------

function authOf(dev) {
  if (dev.local) return 'local';
  if (dev.push) return 'push';
  return dev.password_cmd ? 'cmd' : 'key';
}

function normalise(dev) {
  if (dev.ssh) {
    const [user, host] = dev.ssh.includes('@') ? dev.ssh.split('@') : ['', dev.ssh];
    dev.user = dev.user || user;
    dev.host = dev.host || host;
    delete dev.ssh;
  }
  dev.drives = dev.drives || [];
  return dev;
}

function probeDevice(dev) {
  const d = JSON.parse(JSON.stringify(dev));
  if (!d.port) delete d.port;
  return d;
}

function mountWarnings(m) {
  const out = [];
  if (/ntfs|fuseblk/.test(m.fs) && m.readonly) out.push('Windows drive mounted read-only (hibernated or Fast Startup). Fine for scanning.');
  if (m.fs === 'exfat') out.push('exFAT: unfinished downloads count at their full size.');
  if (m.network) out.push('Network share: scanning it from here reads it over the network.');
  return out.map((w) => `<small class="warn">${esc(w)}</small>`).join('');
}

function keyHelp(dev, payload) {
  if (!payload.pubkey) return `<p class="err">${esc(payload.key_error || 'The hub could not make its SSH key (is ssh-keygen installed?).')}</p>`;
  const target = `${dev.user || 'user'}@${dev.host || 'host'}`;
  const hubCmd = payload.in_docker
    ? `docker compose exec dc-ohair ssh-copy-id -i ${payload.key_path} ${dev.port ? '-p ' + dev.port + ' ' : ''}${target}`
    : `ssh-copy-id -i ${payload.key_path} ${dev.port ? '-p ' + dev.port + ' ' : ''}${target}`;
  const onDevice = `mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys <<'EOF'\n${payload.restricted_line}\nEOF\nchmod 600 ~/.ssh/authorized_keys`;
  return `<details class="help" open><summary>Let the hub in with its own key</summary>
    <p>Easiest: run this <b>on the hub</b>. It asks for ${esc(target)}'s password once.</p>${copyBox(hubCmd)}
    <p>Tighter: run this <b>on ${esc(dev.name || 'the device')}</b>. The key it adds can only run D.C. O'HAIR's read-only scanner, nothing else.</p>${copyBox(onDevice)}
    <p class="muted">LibreELEC keeps root's keys in /storage/.ssh. After an update to D.C. O'HAIR, the tighter line needs replacing.</p></details>`;
}

function deviceForm(dev, payload, opts = {}) {
  const auth = authOf(dev);
  const drives = dev.drives.map((d, i) => `<div class="drive" data-i="${i}">
      <input aria-label="Drive name" data-d="name" value="${esc(d.name)}" placeholder="Name">
      <input aria-label="Path" data-d="path" value="${esc(d.path)}" placeholder="/path" class="mono">
      <input aria-label="Leave out" data-d="exclude" value="${esc(list(d.exclude || d.skip))}" placeholder="leave out, e.g. */node_modules" class="mono">
      <label class="tick" title="Skip it when it isn't plugged in"><input type="checkbox" data-d="mounted" ${d.mounted ? 'checked' : ''}> mount</label>
      <button type="button" class="ghost" data-act="drop-drive" aria-label="Remove ${esc(d.name)}">✕</button></div>`).join('');
  return `<div class="devform">
    <div class="grid2">
      <label>Name <input data-f="name" value="${esc(dev.name)}" placeholder="Living room NAS" required></label>
      <label>Note <input data-f="note" value="${esc(dev.note)}" placeholder="optional"></label>
    </div>
    <fieldset class="seg3"><legend>How the hub reaches it</legend>
      <label><input type="radio" name="auth" value="local" ${auth === 'local' ? 'checked' : ''}> This machine</label>
      <label><input type="radio" name="auth" value="key" ${auth === 'key' ? 'checked' : ''}> SSH key <small>recommended</small></label>
      <label><input type="radio" name="auth" value="cmd" ${auth === 'cmd' ? 'checked' : ''}> password_cmd</label>
      <label><input type="radio" name="auth" value="push" ${auth === 'push' ? 'checked' : ''}> It sends to the hub <small>Windows, or out of reach</small></label>
    </fieldset>
    ${auth === 'push' ? pushHelp(dev) : ''}
    ${auth === 'local' || auth === 'push' ? '' : `<div class="grid3">
      <label>Host <input data-f="host" value="${esc(dev.host)}" placeholder="192.168.1.20 or nas.local" class="mono"></label>
      <label>User <input data-f="user" value="${esc(dev.user)}" placeholder="me" class="mono"></label>
      <label>Port <input data-f="port" value="${esc(dev.port)}" placeholder="22" inputmode="numeric" class="mono"></label>
    </div>`}
    ${auth === 'cmd' ? `<label>password_cmd <input data-f="password_cmd" value="${esc(list(dev.password_cmd))}" placeholder="pass show nas/ssh" class="mono">
      <small class="muted">A command the hub runs at scan time that prints the password. The password itself is never saved.</small></label>` : ''}
    ${auth === 'key' && !opts.compact ? keyHelp(dev, payload) : ''}
    <details class="adv"><summary>More</summary>
      <label>Command prefix <input data-f="command_prefix" value="${esc(dev.command_prefix)}" placeholder="caffeinate -i   (keeps a Mac awake)" class="mono"></label>
      <label>Id <input data-f="id" value="${esc(dev.id)}" class="mono"></label>
    </details>
    <h4>Drives</h4>
    <div class="drives">${drives || (auth === 'push' ? '<p class="muted">Leave this empty and the device picks its own real drives on its first report. You can untick them here afterwards.</p>' : '<p class="muted">No drives yet. Find them, or add a path.</p>')}</div>
    <div class="row">
      ${auth === 'push' ? '' : '<button type="button" data-act="detect">Find drives</button>'}
      <button type="button" class="ghost" data-act="add-drive">+ Add a path</button>
      ${auth === 'push' ? '' : '<button type="button" class="ghost" data-act="test">Test connection</button>'}
      ${opts.saved && auth !== 'push' ? '<button type="button" class="ghost" data-act="scan">Scan now</button>' : ''}
    </div>
    <div class="result" aria-live="polite"></div>
  </div>`;
}

function pushHelp(dev) {
  return `<div class="help push"><p>For a device the hub can't reach (Windows, a laptop on another network, a box behind a firewall).
    You run one command on the device. It scans itself every day and sends the result here, signed with its own token.
    The hub never logs in to it.</p>
    <div class="row"><button type="button" class="primary" data-act="token">${dev.paired ? 'Make a new command (the old one stops working)' : 'Make the install command'}</button></div>
    <div class="pushcmd" aria-live="polite"></div></div>`;
}

function pushCommands(hub, token) {
  const sh = `cd ~ && python3 -c "import urllib.request as u; u.urlretrieve('${hub}/push.py', 'dc-push.py')" && python3 dc-push.py install --hub ${hub} --token ${token}`;
  return [
    ['Linux', sh, 'Needs python3. Adds a daily systemd timer, or a cron job.'],
    ['macOS', sh, "Needs Apple's python3 (run xcode-select --install once). Adds a launchd job that keeps the Mac awake while it scans."],
    ['LibreELEC / Kodi', sh, 'Run it over SSH as root. Turn on cron in LibreELEC Settings → Services so the daily run happens.'],
    ['NAS', sh, 'Run it over SSH. On Synology, install Python 3 from Package Center first.'],
    ['Windows', `iwr ${hub}/push.py -OutFile $env:USERPROFILE\\dc-push.py; python $env:USERPROFILE\\dc-push.py install --hub ${hub} --token ${token}`,
      "Paste it into PowerShell. Needs Python (winget install Python.Python.3.12); if 'python' isn't found, use 'py -3'. Adds a Task Scheduler job."],
  ];
}

function showPushCommand(box, dev, hub, token) {
  const cmds = pushCommands(hub, token);
  box.innerHTML = `<div class="tabs os" role="tablist">${cmds.map(([os], i) => `<button type="button" data-os="${i}" aria-selected="${i === 0}">${esc(os)}</button>`).join('')}</div>
    <div class="oscmd"></div>
    <p class="muted">The token is in the command and is shown only now. Lost it? Make a new command; the old one stops working.</p>
    <p class="waiting">Waiting for ${esc(dev.name || 'the device')} to report in…</p>`;
  const pick = (i) => {
    box.querySelectorAll('[data-os]').forEach((b) => b.setAttribute('aria-selected', String(+b.dataset.os === i)));
    box.querySelector('.oscmd').innerHTML = copyBox(cmds[i][1]) + `<p class="muted">${esc(cmds[i][2])}</p>`;
  };
  box.querySelector('.os').addEventListener('click', (e) => { const b = e.target.closest('[data-os]'); if (b) pick(+b.dataset.os); });
  pick(/Win/.test(navigator.platform) ? 4 : 0);
  const since = Date.now() - 5000;
  const poll = async () => {
    if (!box.isConnected) return;
    try {
      const st = await api('/api/status');
      const s = (st.devices || {})[dev.id];
      if (s && Date.parse(String(s.when).replace(/([+-]\d\d)(\d\d)$/, '$1:$2')) > since) {
        box.querySelector('.waiting').outerHTML = s.ok ? `<p class="ok">✓ ${esc(dev.name)} reported in. It's in the city now.</p>` : `<p class="err">${esc(dev.name)} reported a problem: ${esc(s.msg)}</p>`;
        dev.paired = true;
        changed();
        return;
      }
    } catch { /* keep waiting */ }
    setTimeout(poll, 3000);
  };
  setTimeout(poll, 3000);
}

// wires a deviceForm rendered into `root` to the device object `dev`; persist() saves the config first when needed
function bindDevice(root, dev, payload, rerender, persist) {
  root.addEventListener('input', (e) => {
    const t = e.target;
    if (t.dataset.f) {
      let v = t.value;
      if (t.dataset.f === 'port') v = v ? parseInt(v, 10) || '' : '';
      if (t.dataset.f === 'name' && (!dev.id || dev.id === slug(dev.name || ''))) dev.id = slug(v);
      if (v === '') delete dev[t.dataset.f]; else dev[t.dataset.f] = v;
    }
    const row = t.closest('.drive');
    if (row && t.dataset.d) {
      const d = dev.drives[row.dataset.i];
      if (t.dataset.d === 'mounted') d.mounted = t.checked;
      else if (t.dataset.d === 'exclude') { d.exclude = unlist(t.value); delete d.skip; if (!d.exclude.length) delete d.exclude; }
      else d[t.dataset.d] = t.value;
    }
  });
  root.addEventListener('change', (e) => {
    if (e.target.name !== 'auth') return;
    const v = e.target.value;
    dev.local = v === 'local' || undefined;
    if (!dev.local) delete dev.local;
    if (v === 'push') { dev.push = true; delete dev.host; delete dev.user; delete dev.port; } else delete dev.push;
    if (v !== 'cmd') delete dev.password_cmd;
    if (v === 'cmd' && !dev.password_cmd) dev.password_cmd = '';
    rerender();
  });
  root.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const out = root.querySelector('.result');
    const act = b.dataset.act;
    if (act === 'add-drive') { dev.drives.push({ name: '', path: '' }); return rerender(); }
    if (act === 'drop-drive') { dev.drives.splice(+b.closest('.drive').dataset.i, 1); return rerender(); }
    if (act === 'token') {
      if (!dev.name) { out.innerHTML = '<p class="err">Give the device a name first.</p>'; return; }
      dev.id = dev.id || slug(dev.name);
      b.disabled = true;
      try {
        if (persist) await persist();
        const r = await api('/api/push/token', { id: dev.id });
        showPushCommand(root.querySelector('.pushcmd'), dev, r.hub, r.token);
        out.innerHTML = '';
      } catch (err) { out.innerHTML = `<p class="err">${esc(err.message)}</p>`; } finally { b.disabled = false; }
      return;
    }
    if (act === 'scan') {
      try { await api('/api/scan', { devices: [dev.id] }); out.innerHTML = '<p class="ok">Scanning. The city updates when it finishes.</p>'; watchScan(); } catch (err) { out.innerHTML = `<p class="err">${esc(err.message)}</p>`; }
      return;
    }
    b.disabled = true;
    out.innerHTML = '<p class="muted">Asking the device…</p>';
    try {
      if (act === 'test') {
        const r = await api('/api/test', { device: probeDevice(dev) });
        if (!r.ok) { out.innerHTML = `<p class="err">${esc(r.error)}</p>`; return; }
        const paths = (r.paths || []).map((p) => `<li class="${p.readable ? 'ok' : 'err'}">${esc(p.path)}: ${p.readable ? 'readable' : p.exists ? "can't read it (permission denied). Use a user that can, or leave it out." : "doesn't exist on that device"}</li>`).join('');
        out.innerHTML = `<p class="ok">Connected to ${esc(r.host)} · python ${esc(r.python)}</p>${paths ? `<ul>${paths}</ul>` : ''}`;
      } else if (act === 'detect') {
        const r = await api('/api/detect', { device: probeDevice(dev) });
        if (!r.ok) { out.innerHTML = `<p class="err">${esc(r.error)}</p>`; return; }
        if (!dev.host && r.host && !dev.name) dev.name = r.host;
        const have = new Set(dev.drives.map((d) => d.path));
        const rows = r.mounts.map((m, i) => `<label class="found"><input type="checkbox" data-m="${i}" ${have.has(m.path) ? 'checked disabled' : 'checked'}>
          ${m.label !== m.path ? `<b>${esc(m.label)}</b> ` : ''}<span class="mono">${esc(m.path)}</span> <span class="muted">${esc(m.fs)} · ${size(m.used)} used of ${size(m.total)}</span>${mountWarnings(m)}</label>`).join('');
        out.innerHTML = r.mounts.length ? `<div class="found-list">${rows}</div><button type="button" class="primary" data-add-found>Add ticked drives</button>`
          : '<p class="muted">No drives found besides system ones. Add a path by hand.</p>';
        out.querySelector('[data-add-found]')?.addEventListener('click', () => {
          out.querySelectorAll('input[data-m]:checked:not(:disabled)').forEach((c) => {
            const m = r.mounts[c.dataset.m];
            const name = m.path === '/' || m.path.endsWith('/hostfs') ? (dev.name || 'System') : m.label;
            dev.drives.push({ name, path: m.path, ...(m.path !== '/' && !m.path.endsWith('/hostfs') ? { mounted: true } : {}) });
          });
          rerender();
        });
      }
    } catch (err) {
      out.innerHTML = `<p class="err">${esc(err.message)}</p>`;
    } finally {
      b.disabled = false;
    }
  });
}

function watchScan(onTick) {
  const tick = async () => {
    try {
      const st = await api('/api/status');
      onTick && onTick(st);
      if (st.running) return setTimeout(tick, 2500);
      changed();
    } catch { /* hub restarting */ }
  };
  setTimeout(tick, 1500);
}

// ---------- settings dialog ----------

const TABS = [['devices', 'Devices'], ['scanning', 'Scanning'], ['alerts', 'Alerts'], ['look', 'Look'], ['security', 'Security'], ['backup', 'Backup']];
let S = null;

export async function openSettings(me) {
  let payload;
  try {
    payload = await api('/api/settings');
  } catch (err) {
    if (err.status === 401) return showLogin(me);
    return alertBox(err.message);
  }
  S = { payload, cfg: payload.config, me, tab: 'devices', editing: null, dirty: false };
  S.cfg.devices.forEach(normalise);
  const dlg = el(`<dialog class="settings" aria-labelledby="setTitle">
    <header><h2 id="setTitle">Settings</h2><button type="button" class="ghost close" aria-label="Close settings">✕</button></header>
    <nav class="tabs" role="tablist">${TABS.map(([k, v]) => `<button type="button" role="tab" data-tab="${k}">${v}</button>`).join('')}</nav>
    <section class="body"></section>
    <footer><span class="note" aria-live="polite"></span><button type="button" class="primary save">Save</button></footer>
  </dialog>`);
  document.body.append(dlg);
  dlg.addEventListener('close', () => dlg.remove());
  dlg.querySelector('.close').addEventListener('click', () => {
    if (S.dirty && !dlg.querySelector('.note').dataset.warned) {
      dlg.querySelector('.note').textContent = 'You have unsaved changes. Save, or press close again to drop them.';
      dlg.querySelector('.note').dataset.warned = '1';
      return;
    }
    dlg.close();
  });
  dlg.querySelector('.tabs').addEventListener('click', (e) => {
    const b = e.target.closest('[data-tab]');
    if (b) { S.tab = b.dataset.tab; S.editing = null; render(dlg); }
  });
  dlg.querySelector('.save').addEventListener('click', () => save(dlg));
  dlg.addEventListener('input', () => { S.dirty = true; });
  render(dlg);
  dlg.showModal();
}

// quiet: a save on the way to something else (making a push token); keeps the
// objects being edited, and lets the caller see the error
async function save(dlg, quiet) {
  const note = dlg.querySelector('.note');
  try {
    const cfg = JSON.parse(JSON.stringify(S.cfg));
    for (const d of cfg.devices) {
      d.drives = (d.drives || []).filter((x) => x.name && x.path);
      if (d.local || d.push) { delete d.host; delete d.user; delete d.port; delete d.password_cmd; }
    }
    S.payload = await api('/api/settings', { config: cfg }, 'PUT');
    S.dirty = false;
    if (quiet) return;
    S.cfg = S.payload.config;
    S.cfg.devices.forEach(normalise);
    note.textContent = 'Saved.';
    note.className = 'note ok';
    changed();
  } catch (err) {
    if (quiet) throw err;
    note.textContent = err.message;
    note.className = 'note err';
  }
}

function field(label, key, value, attrs = '') {
  return `<label>${label} <input data-k="${key}" value="${esc(value)}" ${attrs}></label>`;
}

function render(dlg) {
  dlg.querySelectorAll('.tabs button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === S.tab)));
  const body = dlg.querySelector('.body');
  const hub = S.cfg.hub, scan = S.cfg.scan;
  dlg.querySelector('.save').hidden = S.tab === 'security' || S.tab === 'backup';
  if (S.tab === 'devices' && S.editing !== null) {
    const dev = S.cfg.devices[S.editing];
    body.innerHTML = `<button type="button" class="ghost back">← All devices</button>` + deviceForm(dev, S.payload, { saved: !!S.saved?.has(dev.id) })
      + `<div class="row end"><button type="button" class="danger" data-remove>Remove this device</button></div>`;
    const rerender = () => render(dlg);
    const fresh = body.cloneNode(true);
    body.replaceWith(fresh);
    bindDevice(fresh, dev, S.payload, rerender, () => save(dlg, true));
    fresh.querySelector('.back').addEventListener('click', () => { S.editing = null; render(dlg); });
    fresh.querySelector('[data-remove]').addEventListener('click', (e) => {
      if (e.target.dataset.sure) { S.cfg.devices.splice(S.editing, 1); S.editing = null; S.dirty = true; return render(dlg); }
      e.target.dataset.sure = '1';
      e.target.textContent = 'Press again to remove it (then Save)';
    });
    return;
  }
  let html = '';
  if (S.tab === 'devices') {
    S.saved = new Set(S.payload.config.devices.map((d) => d.id));
    html = `<ul class="devlist">${S.cfg.devices.map((d, i) => `<li><button type="button" data-edit="${i}">
        <b>${esc(d.name)}</b> <span class="muted">${d.local ? 'this machine' : d.push ? (d.paired ? 'sends its own reports' : 'waiting to be set up') : esc((d.user ? d.user + '@' : '') + (d.host || ''))} · ${d.drives.length} drive${d.drives.length === 1 ? '' : 's'}</span></button></li>`).join('')}</ul>
      <div class="row"><button type="button" data-new>+ Add a device</button><button type="button" class="ghost" data-wizard>Run the setup wizard</button></div>`;
  } else if (S.tab === 'scanning') {
    const cat = scan.categories || {};
    const def = S.payload.category_defaults || {};
    html = `<div class="grid2">
        ${field('Scan every day at', 'hub.schedule', list(hub.schedule), 'placeholder="04:30, 16:00" class="mono"')}
        ${field('Smallest file or folder to draw', 'scan.min_size', scan.min_size, 'class="mono"')}
        ${field('Folder depth', 'scan.depth', scan.depth, 'inputmode="numeric"')}
        ${field('Keep history for (days)', 'hub.history_days', hub.history_days, 'inputmode="numeric"')}
      </div>
      <h4>What counts as what</h4>
      <p class="muted">Leave a box empty to use the built-in rules. Patterns are matched against the lowercased path.</p>
      <label>Music file endings <input data-k="scan.categories.music" data-list value="${esc(list(cat.music))}" placeholder="${esc(list(def.music))}" class="mono"></label>
      <label>Video file endings <input data-k="scan.categories.video" data-list value="${esc(list(cat.video))}" placeholder="${esc(list(def.video))}" class="mono"></label>
      <label>Series patterns (other big videos are Movies) <input data-k="scan.categories.series" data-list value="${esc(list(cat.series))}" placeholder="${esc(list(def.series))}" class="mono"></label>
      <label>Games patterns <input data-k="scan.categories.games" data-list value="${esc(list(cat.games))}" placeholder="${esc(list(def.games))}" class="mono"></label>`;
  } else if (S.tab === 'alerts') {
    html = `<div class="grid2">${field('Flag drives at or above (% full)', 'hub.alert_percent', hub.alert_percent, 'inputmode="numeric"')}</div>
      ${field('Notify this URL when a drive crosses it (optional)', 'hub.alert_webhook', hub.alert_webhook || '', 'placeholder="https://ntfy.sh/your-topic" class="mono"')}
      <p class="muted">The hub sends a short POST the first time a drive crosses the line. ntfy topics work as-is.</p>`;
  } else if (S.tab === 'look') {
    const ui = hub.ui || {}, th = hub.theme || {};
    const opt = (vals, cur) => vals.map((v) => `<option ${v === cur ? 'selected' : ''}>${v}</option>`).join('');
    html = `<p class="muted">Defaults for people who haven't picked their own.</p><div class="grid3">
      <label>Theme <select data-k="hub.theme.default">${opt(THEMES, th.default || 'dark')}</select></label>
      <label>View <select data-k="hub.ui.view">${opt(['city', 'circle', 'epcot'], ui.view || 'city')}</select></label>
      <label>Units <select data-k="hub.ui.units">${opt(['binary', 'decimal'], ui.units || 'binary')}</select></label></div>
      <p class="muted">binary shows 1.82T for a "2 TB" drive (1024-based); decimal shows 2.00TB.</p>`;
  } else if (S.tab === 'security') {
    const auth = hub.auth || 'off';
    html = `<fieldset class="seg3"><legend>Who can open this page</legend>
        <label><input type="radio" name="sec" value="off" ${auth === 'off' ? 'checked' : ''}> Anyone who can reach it</label>
        <label><input type="radio" name="sec" value="password" ${auth === 'password' ? 'checked' : ''}> Admin login</label>
        <label><input type="radio" name="sec" value="proxy" ${auth === 'proxy' ? 'checked' : ''}> My reverse proxy handles it</label></fieldset>
      ${S.me.exposed ? '<p class="warn">No login is on, and the hub listens on your network: anyone who can open this page can see file names and change devices. Turn on a login, or put it behind a reverse proxy.</p>' : ''}
      <div class="sec-pass" ${auth === 'password' ? '' : 'hidden'}>
        ${S.me.password_set ? '<label>Current password <input type="password" name="old" autocomplete="current-password"></label>' : ''}
        <label>${S.me.password_set ? 'New password (leave empty to keep it)' : 'Choose a password'} <input type="password" name="new" autocomplete="new-password" minlength="8"></label></div>
      <div class="sec-proxy" ${auth === 'proxy' ? '' : 'hidden'}>
        <label>Proxy addresses to trust <input name="proxies" value="${esc(list(hub.trusted_proxies || ['127.0.0.1', '::1']))}" class="mono"></label>
        <label>Header that names the user <input name="header" value="${esc(hub.proxy_user_header || 'Remote-User')}" class="mono"></label>
        <p class="muted">Only requests from those addresses that carry the header get in. Works with Authelia, Authentik, VoidAuth or oauth2-proxy (see docs/security.md).</p></div>
      <div class="row"><button type="button" class="primary" data-sec>Apply</button>${auth === 'password' ? '<button type="button" class="ghost" data-logout>Log out</button>' : ''}</div>
      <p class="result" aria-live="polite"></p>
      <h4>Enrollment tokens</h4>
      ${S.cfg.devices.filter((d) => d.push && d.paired).map((d) => `<div class="row"><b>${esc(d.name)}</b><span class="muted">sends its own reports</span>
        <button type="button" class="danger" data-revoke="${esc(d.id)}">Revoke</button></div>`).join('')
        || '<p class="muted">No device sends its own reports yet.</p>'}
      <p class="muted">Revoking stops that device's reports right away. Make a new command on its device page to let it back in.</p>`;
  } else if (S.tab === 'backup') {
    html = `<p>Download your config (no admin password inside; password_cmd lines are).</p>
      <div class="row"><a class="button" href="/api/settings/export" download>Export config</a></div>
      <h4>Import</h4><p class="muted">Paste a config, or pick a file. Login settings are kept as they are.</p>
      <input type="file" accept=".yml,.yaml,.json" data-file><textarea rows="8" class="mono" data-yaml placeholder="hub: …"></textarea>
      <div class="row"><button type="button" class="primary" data-import>Import</button></div><p class="result" aria-live="polite"></p>
      <p class="muted">Config file on the hub: <span class="mono">${esc(S.payload.config_path || '')}</span></p>`;
  }
  const fresh = el(`<section class="body">${html}</section>`);
  body.replaceWith(fresh);
  wire(fresh, dlg);
}

function setPath(obj, path, value) {
  const keys = path.split('.');
  let o = obj;
  for (const k of keys.slice(0, -1)) o = o[k] = o[k] || {};
  const last = keys[keys.length - 1];
  if (value === '' || (Array.isArray(value) && !value.length)) delete o[last]; else o[last] = value;
}

function wire(body, dlg) {
  body.addEventListener('input', (e) => {
    const t = e.target;
    if (!t.dataset.k) return;
    let v = t.value;
    if (t.dataset.list || t.dataset.k === 'hub.schedule') v = unlist(v);
    else if (/percent|days|depth/.test(t.dataset.k)) v = v === '' ? '' : Number(v);
    setPath(S.cfg, t.dataset.k, v);
  });
  body.addEventListener('change', (e) => {
    if (e.target.tagName === 'SELECT') setPath(S.cfg, e.target.dataset.k, e.target.value);
    if (e.target.name === 'sec') {
      body.querySelector('.sec-pass').hidden = e.target.value !== 'password';
      body.querySelector('.sec-proxy').hidden = e.target.value !== 'proxy';
    }
    if (e.target.dataset.file !== undefined && e.target.files[0]) e.target.files[0].text().then((t) => { body.querySelector('[data-yaml]').value = t; });
  });
  body.addEventListener('click', async (e) => {
    const t = e.target;
    if (t.closest('[data-edit]')) { S.editing = +t.closest('[data-edit]').dataset.edit; return render(dlg); }
    if (t.closest('[data-new]')) {
      S.cfg.devices.push({ id: '', name: '', drives: [] });
      S.editing = S.cfg.devices.length - 1;
      S.dirty = true;
      return render(dlg);
    }
    if (t.closest('[data-wizard]')) { dlg.close(); return openWizard(S.me); }
    const out = body.querySelector('.result');
    if (t.closest('[data-sec]')) {
      const mode = body.querySelector('input[name=sec]:checked').value;
      try {
        await api('/api/security', {
          auth: mode, password: body.querySelector('[name=new]')?.value || '', old: body.querySelector('[name=old]')?.value || '',
          trusted_proxies: unlist(body.querySelector('[name=proxies]').value), proxy_user_header: body.querySelector('[name=header]').value,
        });
        S.cfg.hub.auth = mode;
        out.textContent = mode === 'off' ? 'Login is off.' : mode === 'password' ? 'Login is on. You stay logged in on this browser for 30 days.' : 'Trusting your reverse proxy now.';
        out.className = 'result ok';
        S.me = await api('/api/me');
      } catch (err) { out.textContent = err.message; out.className = 'result err'; }
    }
    const rv = t.closest('[data-revoke]');
    if (rv) {
      try {
        await api('/api/push/revoke', { id: rv.dataset.revoke });
        const d = S.cfg.devices.find((x) => x.id === rv.dataset.revoke);
        if (d) d.paired = false;
        render(dlg);
      } catch (err) { out.textContent = err.message; out.className = 'result err'; }
      return;
    }
    if (t.closest('[data-logout]')) { await api('/api/logout', {}); location.reload(); }
    if (t.closest('[data-import]')) {
      try {
        S.payload = await api('/api/settings/import', { yaml: body.querySelector('[data-yaml]').value });
        S.cfg = S.payload.config;
        S.cfg.devices.forEach(normalise);
        out.textContent = 'Imported and saved.';
        out.className = 'result ok';
        changed();
      } catch (err) { out.textContent = err.message; out.className = 'result err'; }
    }
  });
}

function alertBox(msg) {
  const box = el(`<div class="overlay"><div class="sheet small"><p>${esc(msg)}</p><button class="primary" type="button">OK</button></div></div>`);
  box.querySelector('button').addEventListener('click', () => box.remove());
  document.body.append(box);
}

// ---------- first-run wizard ----------

export async function openWizard(me) {
  let payload;
  try { payload = await api('/api/settings'); } catch (err) { if (err.status === 401) return showLogin(me); return alertBox(err.message); }
  const W = { step: 0, cfg: payload.config, payload, me };
  W.cfg.devices.forEach(normalise);
  let here = W.cfg.devices.find((d) => d.local);
  if (!here) { here = { id: 'this-machine', name: 'This machine', local: true, drives: [] }; W.cfg.devices.unshift(here); }
  const box = el(`<div class="overlay wizard" role="dialog" aria-modal="true" aria-labelledby="wizTitle">
    <div class="sheet">
      <header><img src="/logo.svg" alt="" width="40" height="40"><div><h2 id="wizTitle">Welcome to D.C. O'HAIR</h2>
        <p class="muted">Three steps and your drives become a city.</p></div>
        <button type="button" class="ghost skip">Skip setup</button></header>
      <ol class="steps"><li>This machine</li><li>Your other devices</li><li>First scan</li></ol>
      <div class="bar"><i></i></div>
      <section class="body"></section>
      <footer><button type="button" class="ghost prev">Back</button><span class="note" aria-live="polite"></span><button type="button" class="primary next">Next</button></footer>
    </div></div>`);
  document.body.append(box);
  box.querySelector('.skip').addEventListener('click', () => box.remove());
  box.querySelector('.prev').addEventListener('click', () => { if (W.step > 0) { W.step--; draw(); } });
  box.querySelector('.next').addEventListener('click', async () => {
    if (W.step < 2) { W.step++; return draw(); }
    if (W.done) { box.remove(); return changed(); }
  });

  // the wizard's config as it should be saved: finished devices only
  const finished = () => {
    const cfg = JSON.parse(JSON.stringify(W.cfg));
    cfg.devices = cfg.devices.filter((d) => d.name && (d.local || d.host || d.push)).map((d) => {
      d.id = d.id || slug(d.name);
      d.drives = d.drives.filter((x) => x.name && x.path);
      return d;
    });
    return cfg;
  };
  const persist = () => api('/api/settings', { config: finished() }, 'PUT');

  const draw = async () => {
    box.querySelectorAll('.steps li').forEach((li, i) => li.classList.toggle('on', i <= W.step));
    box.querySelector('.bar i').style.width = `${((W.step + 1) / 3) * 100}%`;
    box.querySelector('.prev').hidden = W.step === 0;
    const next = box.querySelector('.next');
    next.textContent = W.step === 2 ? 'Open the city' : 'Next';
    next.disabled = W.step === 2 && !W.done;
    const body = box.querySelector('.body');
    const fresh = el('<section class="body"></section>');
    body.replaceWith(fresh);
    if (W.step === 0) {
      fresh.innerHTML = `<p>These are the drives on the machine the hub runs on. Untick any you don't want.</p><div class="mine">${deviceForm(here, payload, { compact: true })}</div>`;
      const mine = fresh.querySelector('.mine');
      bindDevice(mine, here, payload, () => draw());
      if (!here.drives.length && !W.detected) { W.detected = true; mine.querySelector('[data-act=detect]').click(); }
    } else if (W.step === 1) {
      const others = W.cfg.devices.filter((d) => !d.local);
      fresh.innerHTML = `<p>Add your other computers and boxes. The hub reaches them over SSH with its own key; nothing gets installed on them.</p>
        <ul class="devlist">${others.map((d) => `<li><button type="button" data-open="${W.cfg.devices.indexOf(d)}"><b>${esc(d.name || 'new device')}</b>
          <span class="muted">${esc((d.user ? d.user + '@' : '') + (d.host || ''))} · ${d.drives.length} drive${d.drives.length === 1 ? '' : 's'}</span></button></li>`).join('')}</ul>
        <div class="row"><button type="button" data-add>+ Add a device</button></div><div class="editor"></div>
        <p class="muted">You can always add more later in Settings → Devices.</p>`;
      const editor = fresh.querySelector('.editor');
      const open = (i) => {
        const dev = W.cfg.devices[i];
        editor.innerHTML = deviceForm(dev, payload);
        const ed = editor.cloneNode(true);
        editor.replaceWith(ed);
        bindDevice(ed, dev, payload, () => { draw().then(() => box.querySelector(`[data-open="${i}"]`)?.click()); }, persist);
      };
      fresh.addEventListener('click', (e) => {
        const o = e.target.closest('[data-open]');
        if (o) open(+o.dataset.open);
        if (e.target.closest('[data-add]')) {
          W.cfg.devices.push({ id: '', name: '', user: '', host: '', drives: [] });
          draw().then(() => box.querySelector(`[data-open="${W.cfg.devices.length - 1}"]`)?.click());
        }
      });
    } else {
      fresh.innerHTML = '<p>Saving and scanning. Big drives take a few minutes; you can open the city while it works.</p><ul class="progress"></ul>';
      const note = box.querySelector('.note');
      try {
        const cfg = finished();
        await api('/api/settings', { config: cfg }, 'PUT');
        await api('/api/scan', {});
        W.done = true;
        next.disabled = false;
        watchScan((st) => {
          const names = Object.fromEntries(cfg.devices.map((d) => [d.id, d.name]));
          fresh.querySelector('.progress').innerHTML = Object.entries(st.progress || {}).map(([id, p]) =>
            `<li class="${p.state}"><b>${esc(names[id] || id)}</b> <span>${p.state === 'scanning' ? 'scanning…' : p.state === 'done' ? 'done' : p.state === 'failed' ? esc(p.msg) : 'waiting'}</span></li>`).join('');
        });
      } catch (err) {
        note.textContent = err.message;
        note.className = 'note err';
      }
    }
  };
  draw();
}
