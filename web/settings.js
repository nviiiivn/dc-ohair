// Disk City settings page, login screen and first-run wizard.
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
  if (me.auth === 'oidc') {
    const next = encodeURIComponent(location.pathname + location.search);
    const sso = el(`<div class="overlay" role="dialog" aria-modal="true" aria-labelledby="loginTitle"><div class="sheet small">
      <img src="/logo.svg" alt="" width="56" height="56"><h2 id="loginTitle">Disk City</h2>
      <p class="muted">Sign in to see your drives.</p>
      <a class="primary button" href="/auth/login?next=${next}">Sign in with ${esc(me.login_name)}</a></div></div>`);
    document.body.append(sso);
    return;
  }
  const box = el(`<div class="overlay" role="dialog" aria-modal="true" aria-labelledby="loginTitle">
    <form class="sheet small">
      <img src="/logo.svg" alt="" width="56" height="56">
      <h2 id="loginTitle">Disk City</h2>
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
    <p>Tighter: run this <b>on ${esc(dev.name || 'the device')}</b>. The key it adds can only run Disk City's read-only scanner, nothing else.</p>${copyBox(onDevice)}
    <p class="muted">LibreELEC keeps root's keys in /storage/.ssh. After an update to Disk City, the tighter line needs replacing.</p></details>`;
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

// Each system's line downloads dc-agent (agent-go/), one file with nothing to install, for its
// processor; the line under it is the same with Python instead (push.py), for a box the hub's
// download can't reach. sh -c keeps the line working when the device's shell is fish.
function pushCommands(hub, token) {
  const bin = (os) => `sh -c 'cd ~ && a=$(uname -m); case $a in x86_64|amd64) a=amd64;; aarch64|arm64) a=arm64;; arm*) a=arm;; esac; `
    + `{ curl -fsSLo dc-agent ${hub}/dl/dc-agent-${os}-$a || wget -qO dc-agent ${hub}/dl/dc-agent-${os}-$a; } && chmod +x dc-agent && ./dc-agent install --hub ${hub} --token ${token}'`;
  const sh = `cd ~ && python3 -c "import urllib.request as u; u.urlretrieve('${hub}/push.py', 'dc-push.py')" && python3 dc-push.py install --hub ${hub} --token ${token}`;
  return [
    ['Linux', bin('linux'), 'Nothing to install first. Adds a daily systemd timer, or a cron job.', sh, 'Needs python3.'],
    ['macOS', bin('darwin'), 'Nothing to install first. Adds a launchd job that keeps the Mac awake while it scans.', sh,
      "Needs Apple's python3 (run xcode-select --install once)."],
    ['LibreELEC / Kodi', bin('linux'), 'Run it over SSH as root. Turn on cron in LibreELEC Settings → Services so the daily run happens.', sh, ''],
    ['NAS', bin('linux'), 'Run it over SSH. Nothing to install first, Synology included.', sh, 'On Synology, install Python 3 from Package Center first.'],
    ['Windows', `$a = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {'arm64'} else {'amd64'}; iwr ${hub}/dl/dc-agent-windows-$a.exe -OutFile $env:USERPROFILE\\dc-agent.exe; & $env:USERPROFILE\\dc-agent.exe install --hub ${hub} --token ${token}`,
      'Paste it into PowerShell. Nothing to install first. Adds a Task Scheduler job.',
      `iwr ${hub}/push.py -OutFile $env:USERPROFILE\\dc-push.py; python $env:USERPROFILE\\dc-push.py install --hub ${hub} --token ${token}`,
      "Needs Python (winget install Python.Python.3.12); if 'python' isn't found, use 'py -3'."],
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
    box.querySelector('.oscmd').innerHTML = copyBox(cmds[i][1]) + `<p class="muted">${esc(cmds[i][2])}</p>`
      + `<details class="alt"><summary>Or with Python instead</summary>${copyBox(cmds[i][3])}<p class="muted">${esc(cmds[i][4] || 'Needs python3.')}</p></details>`;
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

// ---------- opt-in network search ----------

const nice = (h) => (h.name || '').replace(/\.(local|lan|home|localdomain)$/i, '') || h.ip;

function uniqueId(devices, name) {
  const base = slug(name);
  let id = base, n = 2;
  while (devices.some((d) => d.id === id)) id = `${base}-${n++}`;
  return id;
}

function discoverBox() {
  return `<details class="help find"><summary>Find devices on my network</summary>
    <p>This looks through the hub's own local network for machines that answer on SSH or file sharing (ports 22, 445, 548 and 5000).
      It only knocks on those ports. It doesn't log in or read anything.</p>
    <div class="row"><input data-net placeholder="network, e.g. 192.168.1.0/24 (empty: the hub's own)" class="mono">
      <button type="button" class="primary" data-find>Look for devices</button></div>
    <div class="found-hosts" aria-live="polite"></div></details>`;
}

// a yes/no checklist of what answered; ticked ones become devices (SSH, or "sends to the hub" without SSH)
function bindDiscover(root, devices, onAdded) {
  root.addEventListener('click', async (e) => {
    const out = root.querySelector('.found-hosts');
    const find = e.target.closest('[data-find]');
    if (find) {
      find.disabled = true;
      out.innerHTML = '<p class="waiting">Knocking on doors…</p>';
      try {
        const r = await api('/api/discover', { network: root.querySelector('[data-net]').value.trim() });
        out.hosts = r.hosts;
        out.innerHTML = r.hosts.length ? `<p class="muted">${r.hosts.length} answered on ${esc(r.network)}. Tick the ones to add.</p>
          <div class="found-list">${r.hosts.map((h, i) => `<label class="found"><input type="checkbox" data-h="${i}" ${h.known ? 'checked disabled' : ''}>
            <b>${esc(nice(h))}</b> <span class="mono">${esc(h.ip)}</span>
            <span class="muted">${esc([h.ports.join(' · '), h.os, h.vendor].filter(Boolean).join(' · '))}</span>
            ${h.known ? `<small class="ok">already added as ${esc(h.known)}</small>` : !h.ssh ? '<small class="warn">No SSH, so it will send its own reports instead.</small>' : ''}</label>`).join('')}</div>
          <div class="row"><button type="button" class="primary" data-add-hosts>Add ticked devices</button>
            <button type="button" class="ghost" data-add data-new>+ Add your own</button></div>`
          : `<p class="muted">Nothing answered on ${esc(r.network)}.</p><div class="row"><button type="button" class="ghost" data-add data-new>+ Add your own</button></div>`;
      } catch (err) {
        out.innerHTML = `<p class="err">${esc(err.message)}</p>`;
      } finally {
        find.disabled = false;
      }
      return;
    }
    if (e.target.closest('[data-add-hosts]')) {
      const added = [];
      out.querySelectorAll('input[data-h]:checked:not(:disabled)').forEach((c) => {
        const h = out.hosts[c.dataset.h];
        const dev = { id: uniqueId(devices, nice(h)), name: nice(h), drives: [] };
        if (h.ssh) Object.assign(dev, { host: h.ip, user: '' }); else dev.push = true;
        devices.push(dev);
        added.push(dev);
      });
      onAdded(added);
    }
  });
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

const TABS = [['devices', 'Devices'], ['scanning', 'Scanning'], ['alerts', 'Alerts'], ['health', 'Health'], ['look', 'Look'], ['security', 'Security'], ['backup', 'Backup']];
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

function channelForm(c, i) {
  const f = (label, key, ph, extra = '') => `<label>${label} <input data-ch="${i}" data-ck="${key}" value="${esc(list(c[key]))}" placeholder="${esc(ph)}" class="mono" ${extra}></label>`;
  const body = {
    ntfy: f('Topic URL', 'url', 'https://ntfy.sh/my-disks') + f('…or a command that prints it', 'url_cmd', ''),
    discord: f('Webhook URL', 'url', 'https://discord.com/api/webhooks/…') + f('…or a command that prints it', 'url_cmd', 'pass show discord/hook'),
    webhook: f('URL (gets a JSON POST)', 'url', 'https://example.com/hook'),
    email: `<div class="grid3">${f('SMTP server', 'smtp_host', 'mail.example.com')}${f('Port', 'smtp_port', '587', 'inputmode="numeric"')}${f('User', 'smtp_user', 'me@example.com')}</div>`
      + f('Command that prints the SMTP password', 'smtp_password_cmd', 'pass show mail') + `<div class="grid2">${f('From', 'from', 'hub@example.com')}${f('To', 'to', 'me@example.com')}</div>`,
  }[c.type] || '';
  const name = { ntfy: 'ntfy', discord: 'Discord', email: 'Email', webhook: 'Webhook' }[c.type] || c.type;
  return `<div class="channel"><div class="row"><b>${esc(name)}</b><span class="spacer"></span>
    <button type="button" class="ghost" data-testch="${i}">Send a test</button><button type="button" class="danger" data-dropch="${i}">Remove</button></div>
    ${body}<p class="result" data-chres="${i}" aria-live="polite"></p></div>`;
}

// writes the VoidAuth side of either setup, ready to paste; nothing here talks to VoidAuth itself
function voidauthHelper() {
  return `<details class="help voidauth"><summary>Set up VoidAuth for me</summary>
    <p class="muted"><a href="https://github.com/voidauth/voidauth" rel="noopener">VoidAuth</a> is a small self-hosted login service. Fill in two addresses and copy what this writes.</p>
    <div class="grid2"><label>This page's address <input data-va="dc" value="${esc(location.origin)}" class="mono"></label>
      <label>VoidAuth's address <input data-va="auth" placeholder="https://auth.example.com" class="mono"></label></div>
    <fieldset class="seg3"><legend>How</legend>
      <label><input type="radio" name="va" value="oidc" checked> Sign in with OIDC</label>
      <label><input type="radio" name="va" value="proxy"> VoidAuth guards the whole page (forward auth)</label></fieldset>
    <div class="row"><button type="button" data-va-go>Write it</button></div>
    <div class="va-out"></div></details>`;
}

function voidauthText(dc, auth, how) {
  const host = (u) => u.replace(/^https?:\/\//, '').replace(/\/.*$/, '');
  const secret = Array.from(crypto.getRandomValues(new Uint8Array(24)), (b) => b.toString(16).padStart(2, '0')).join('');
  if (how === 'oidc') {
    return [
      ['1. Add to the voidauth service in its compose.yml (environment:), then docker compose up -d', `OIDC_dcohair_CLIENT_SECRET: "${secret}"
OIDC_dcohair_CLIENT_REDIRECT_URLS: "${dc}/auth/callback"
OIDC_dcohair_CLIENT_AUTH_METHOD: "client_secret_post"
OIDC_dcohair_CLIENT_DISPLAY_NAME: "Disk City"`],
      ["2. Give the same secret to the hub (systemd: Environment= line; Docker: environment:), then restart it", `DC_OIDC_SECRET=${secret}`],
      ['3. Then fill in above: Issuer, Client ID and the variable, and press Apply', `Issuer: ${auth}/oidc
Client ID: dcohair
Secret from this environment variable: DC_OIDC_SECRET`],
    ];
  }
  return [
    ['1. Caddyfile: VoidAuth guards the page; push agents keep their own way in with their tokens', `${host(dc)} {
  @agents path /agent.py /push.py /dl/* /api/push/spec /api/report
  handle @agents {
    reverse_proxy dc-ohair:7171
  }
  handle {
    forward_auth voidauth:3000 {
      uri /api/authz/forward-auth
      copy_headers Remote-User Remote-Groups Remote-Name Remote-Email
    }
    reverse_proxy dc-ohair:7171
  }
}`],
    ['2. Then choose "My reverse proxy handles it" above, trust Caddy\'s address, keep the header Remote-User, and press Apply. Make sure only Caddy can reach the hub\'s port.', `Proxy addresses to trust: <Caddy's IP, e.g. 172.18.0.2 in Docker, or 127.0.0.1>
Header that names the user: Remote-User`],
  ];
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
      <div class="row"><button type="button" data-new>+ Add a device</button><button type="button" class="ghost" data-wizard>Run the setup wizard</button></div>
      ${discoverBox()}`;
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
    if (hub.alert_webhook) {  // the older single URL becomes an ntfy channel
      hub.notify = (hub.notify || []).concat([{ type: 'ntfy', url: hub.alert_webhook }]);
      delete hub.alert_webhook;
      S.dirty = true;
    }
    const al = hub.alerts || {};
    const chans = hub.notify || [];
    html = `<div class="grid2">${field('A drive is "almost full" at (% used)', 'hub.alert_percent', hub.alert_percent, 'inputmode="numeric"')}</div>
      <p class="muted">Almost-full drives get a banner on the page. Notifications below are optional, and only go out when something changes: a drive crosses the line, its health turns, or it's back to normal.</p>
      <label class="tick"><input type="checkbox" data-k="hub.alerts.health" ${al.health === false ? '' : 'checked'}> Notify about drive health (when health is set up)</label>
      <label class="tick"><input type="checkbox" data-k="hub.alerts.offline" ${al.offline ? 'checked' : ''}> Notify when a device can't be scanned (noisy if your laptops sleep)</label>
      <h4>Send alerts to</h4>
      ${chans.map((c, i) => channelForm(c, i)).join('') || '<p class="muted">Nowhere yet.</p>'}
      <div class="row"><button type="button" data-addch="ntfy">+ ntfy</button><button type="button" data-addch="discord">+ Discord</button>
        <button type="button" data-addch="email">+ Email</button><button type="button" data-addch="webhook">+ Webhook</button></div>
      <p class="muted">Secrets don't have to live in the config: any URL or password can come from a command that prints it, such as <span class="mono">pass show discord/hook</span>.</p>`;
  } else if (S.tab === 'health') {
    const hc = hub.health || {};
    const mode = hc.mode || 'off';
    const radio = (v, label) => `<label><input type="radio" name="hmode" value="${v}" data-k="hub.health.mode" ${mode === v ? 'checked' : ''}> ${label}</label>`;
    html = `<p class="muted">Drive health stays out of sight until someone presses <b>Health</b> in the toolbar. The one exception is a small red dot on a drive that's actually failing.</p>
      <fieldset class="seg3"><legend>Where health comes from</legend>${radio('off', 'Nowhere')}${radio('scrutiny', 'My Scrutiny')}${radio('smart', 'Built-in SMART')}</fieldset>
      <div class="h-scrutiny" ${mode === 'scrutiny' ? '' : 'hidden'}>
        ${field('Scrutiny address', 'hub.health.scrutiny_url', hc.scrutiny_url || '', 'placeholder="http://scrutiny.local:8080" class="mono"')}
        ${field('Command that prints an API token (optional, for a Scrutiny behind a login)', 'hub.health.scrutiny_token_cmd', list(hc.scrutiny_token_cmd), 'class="mono"')}
        <div class="row"><button type="button" data-htest>Test</button></div><p class="result" aria-live="polite"></p>
        <p class="muted">Disks are matched to drives by WWN or serial number, which the scanner reads from /dev/disk/by-id on Linux. Rescan once after turning this on.</p>
      </div>
      <div class="h-smart" ${mode === 'smart' ? '' : 'hidden'}>
        <p>Every scan also runs <span class="mono">smartctl -a -j</span> on the disk under each drive (with <span class="mono">-d sat</span> for USB enclosures). Each device needs the smartmontools package, and smartctl needs root. If the scanning user isn't root, add this rule on the device with <span class="mono">sudo visudo -f /etc/sudoers.d/dc-ohair</span> (put the user's name first, and check the path with <span class="mono">command -v smartctl</span>):</p>
        ${copyBox('scanuser ALL=(root) NOPASSWD: /usr/sbin/smartctl')}
        <p class="muted">That lets the user run smartctl as root, and nothing else.</p>
      </div>
      <label class="tick"><input type="checkbox" data-k="hub.health.failing_dot" ${hc.failing_dot === false ? '' : 'checked'}> Show a small red dot on a failing drive, even with Health off</label>`;
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
        <label><input type="radio" name="sec" value="proxy" ${auth === 'proxy' ? 'checked' : ''}> My reverse proxy handles it</label>
        <label><input type="radio" name="sec" value="oidc" ${auth === 'oidc' ? 'checked' : ''}> Sign in with OIDC</label></fieldset>
      ${S.me.exposed ? '<p class="warn">No login is on, and the hub listens on your network: anyone who can open this page can see file names and change devices. Turn on a login, or put it behind a reverse proxy.</p>' : ''}
      <div class="sec-pass" ${auth === 'password' ? '' : 'hidden'}>
        ${S.me.password_set ? '<label>Current password <input type="password" name="old" autocomplete="current-password"></label>' : ''}
        <label>${S.me.password_set ? 'New password (leave empty to keep it)' : 'Choose a password'} <input type="password" name="new" autocomplete="new-password" minlength="8"></label></div>
      <div class="sec-proxy" ${auth === 'proxy' ? '' : 'hidden'}>
        <label>Proxy addresses to trust <input name="proxies" value="${esc(list(hub.trusted_proxies || ['127.0.0.1', '::1']))}" class="mono"></label>
        <label>Header that names the user <input name="header" value="${esc(hub.proxy_user_header || 'Remote-User')}" class="mono"></label>
        <p class="muted">Only requests from those addresses that carry the header get in. Works with Authelia, Authentik, VoidAuth or oauth2-proxy (see docs/security.md).</p></div>
      <div class="sec-oidc" ${auth === 'oidc' ? '' : 'hidden'}>
        <p class="muted">Sign in through your own login service: VoidAuth, Authelia, Authentik, Keycloak, Google and the like. Register Disk City there with this redirect URL:</p>
        ${copyBox(location.origin + '/auth/callback')}
        <div class="grid2">
          <label>Issuer <input name="o_issuer" value="${esc((hub.oidc || {}).issuer || '')}" placeholder="https://auth.example.com/oidc" class="mono"></label>
          <label>Client ID <input name="o_client" value="${esc((hub.oidc || {}).client_id || '')}" placeholder="dcohair" class="mono"></label>
          <label>Secret from this environment variable <input name="o_env" value="${esc((hub.oidc || {}).client_secret_env || '')}" placeholder="DC_OIDC_SECRET" class="mono"></label>
          <label>…or from this command <input name="o_cmd" value="${esc(list((hub.oidc || {}).client_secret_cmd))}" placeholder="pass show oidc/dcohair" class="mono"></label>
        </div>
        <label>Who may sign in (emails or user names) <input name="o_users" value="${esc(list((hub.oidc || {}).allowed_users))}" placeholder="me@example.com" class="mono"></label>
        <label>Name on the sign-in button <input name="o_name" value="${esc((hub.oidc || {}).name || '')}" placeholder="VoidAuth"></label>
        <p class="muted">If a setting is wrong and you get locked out, run <span class="mono">python3 hub.py --auth-off</span> on the hub.</p>
      </div>
      ${voidauthHelper()}
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
  if (S.tab === 'devices') {
    bindDiscover(fresh, S.cfg.devices, (added) => {
      if (!added.length) return;
      S.dirty = true;
      S.editing = S.cfg.devices.indexOf(added[0]);
      render(dlg);
    });
  }
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
    if (t.dataset.ch !== undefined) {
      const ch = S.cfg.hub.notify[t.dataset.ch];
      const v = t.dataset.ck === 'smtp_port' ? (parseInt(t.value, 10) || '') : t.value.trim();
      if (v === '') delete ch[t.dataset.ck]; else ch[t.dataset.ck] = v;
      return;
    }
    if (!t.dataset.k) return;
    let v = t.value;
    if (t.type === 'checkbox') v = t.checked;
    else if (t.dataset.list || t.dataset.k === 'hub.schedule') v = unlist(v);
    else if (/percent|days|depth/.test(t.dataset.k)) v = v === '' ? '' : Number(v);
    setPath(S.cfg, t.dataset.k, v);
  });
  body.addEventListener('change', (e) => {
    if (e.target.tagName === 'SELECT') setPath(S.cfg, e.target.dataset.k, e.target.value);
    if (e.target.name === 'hmode') {
      body.querySelector('.h-scrutiny').hidden = e.target.value !== 'scrutiny';
      body.querySelector('.h-smart').hidden = e.target.value !== 'smart';
    }
    if (e.target.name === 'sec') {
      body.querySelector('.sec-pass').hidden = e.target.value !== 'password';
      body.querySelector('.sec-proxy').hidden = e.target.value !== 'proxy';
      body.querySelector('.sec-oidc').hidden = e.target.value !== 'oidc';
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
    if (t.closest('[data-va-go]')) {
      const dc = body.querySelector('[data-va="dc"]').value.trim().replace(/\/$/, '');
      const auth = body.querySelector('[data-va="auth"]').value.trim().replace(/\/$/, '');
      const box = body.querySelector('.va-out');
      if (!/^https:\/\//.test(auth)) { box.innerHTML = '<p class="err">VoidAuth\'s address should start with https://</p>'; return; }
      const how = body.querySelector('input[name=va]:checked').value;
      box.innerHTML = voidauthText(dc, auth, how).map(([h, text]) => `<p>${esc(h)}</p>${copyBox(text)}`).join('');
      if (how === 'oidc') {
        body.querySelector('[name=o_issuer]').value = auth + '/oidc';
        body.querySelector('[name=o_client]').value = 'dcohair';
        body.querySelector('[name=o_env]').value = 'DC_OIDC_SECRET';
        if (!body.querySelector('[name=o_name]').value) body.querySelector('[name=o_name]').value = 'VoidAuth';
      }
      return;
    }
    if (t.closest('[data-sec]')) {
      const mode = body.querySelector('input[name=sec]:checked').value;
      try {
        const v = (n) => body.querySelector(`[name=${n}]`).value.trim();
        await api('/api/security', {
          auth: mode, password: body.querySelector('[name=new]')?.value || '', old: body.querySelector('[name=old]')?.value || '',
          trusted_proxies: unlist(v('proxies')), proxy_user_header: v('header'),
          oidc: { issuer: v('o_issuer'), client_id: v('o_client'), client_secret_env: v('o_env'), client_secret_cmd: v('o_cmd'),
            allowed_users: unlist(v('o_users')), name: v('o_name') },
        });
        S.cfg.hub.auth = mode;
        out.textContent = { off: 'Login is off.', password: 'Login is on. You stay logged in on this browser for 30 days.',
          proxy: 'Trusting your reverse proxy now.', oidc: 'Sign-in is on. This browser stays signed in; others go through your login service.' }[mode];
        out.className = 'result ok';
        S.me = await api('/api/me');
      } catch (err) { out.textContent = err.message; out.className = 'result err'; }
    }
    const addch = t.closest('[data-addch]');
    if (addch) {
      S.cfg.hub.notify = (S.cfg.hub.notify || []).concat([{ type: addch.dataset.addch }]);
      S.dirty = true;
      return render(dlg);
    }
    const dropch = t.closest('[data-dropch]');
    if (dropch) {
      S.cfg.hub.notify.splice(+dropch.dataset.dropch, 1);
      S.dirty = true;
      return render(dlg);
    }
    const testch = t.closest('[data-testch]');
    if (testch) {
      const res = body.querySelector(`[data-chres="${testch.dataset.testch}"]`);
      res.textContent = 'Sending…';
      res.className = 'result muted';
      try {
        const r = await api('/api/notify/test', { channel: S.cfg.hub.notify[testch.dataset.testch] });
        res.textContent = r.ok ? 'Sent. Check that it arrived.' : r.error;
        res.className = 'result ' + (r.ok ? 'ok' : 'err');
      } catch (err) { res.textContent = err.message; res.className = 'result err'; }
      return;
    }
    if (t.closest('[data-htest]')) {
      out.textContent = 'Asking Scrutiny…';
      out.className = 'result muted';
      try {
        const r = await api('/api/health/test', { health: S.cfg.hub.health || {} });
        out.textContent = r.ok ? `Scrutiny knows ${r.disks} disk${r.disks === 1 ? '' : 's'}; ${r.matched} of your drives matched so far.` : r.error;
        out.className = 'result ' + (r.ok ? 'ok' : 'err');
      } catch (err) { out.textContent = err.message; out.className = 'result err'; }
      return;
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
      <header><img src="/logo.svg" alt="" width="40" height="40"><div><h2 id="wizTitle">Welcome to Disk City</h2>
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
      fresh.innerHTML = `<p>Add your other computers and boxes. The hub reaches them over SSH with its own key, so nothing gets installed on them.
        Devices it can't reach can send their own reports instead.</p>
        ${discoverBox()}
        <ul class="devlist">${others.map((d) => `<li><button type="button" data-open="${W.cfg.devices.indexOf(d)}"><b>${esc(d.name || 'new device')}</b>
          <span class="muted">${d.push ? 'sends its own reports' : esc((d.user ? d.user + '@' : '') + (d.host || ''))} · ${d.drives.length} drive${d.drives.length === 1 ? '' : 's'}</span></button></li>`).join('')}</ul>
        <div class="row"><button type="button" data-add>+ Add your own</button></div><div class="editor"></div>
        <p class="muted">You can always add more later in Settings → Devices.</p>`;
      const editor = fresh.querySelector('.editor');
      bindDiscover(fresh, W.cfg.devices, (added) => {
        if (added.length) draw().then(() => box.querySelector(`[data-open="${W.cfg.devices.indexOf(added[0])}"]`)?.click());
      });
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
