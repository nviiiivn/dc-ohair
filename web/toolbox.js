// Disk City's toolbox panel: the Tools button and what it opens. It talks to
// /api/tools/* and reaches the city only through window.dcCity (app.js).
//
// Duplicates first. Two lists: the same film in more than one copy (the best
// copy marked Keep), and files of the same bytes, each group saying how sure
// the match is. Tapping a file flies to it and outlines every copy of it in
// the city, with the panel out of the way (Tools brings it back as it was);
// "Show all" outlines every duplicate at once.

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const city = () => window.dcCity;
const size = (b) => (city() ? city().fmt(b) : `${Math.round(b / 1e9)} GB`);
const MARK = '#ff3b6b';
const SURE = {
  size: ['size only', 'Only the size matches. It may be a coincidence until the files are checked.'],
  name: ['same name', 'The same name and the same size.'],
  partial: ['start and end match', 'The first and last few KB were read and match.'],
  hash: ['checked', 'Every byte was read, and the copies are identical.'],
};

let open = false;
let tab = 'films';
let report = null;
let shown = null;         // the group whose files are outlined
let error = '';
let job = null;           // /api/tools/jobs while a check runs, and its last result
let keyLine = '';

async function getJSON(url, post) {
  const r = await fetch(url, post === undefined ? { credentials: 'same-origin' }
    : { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-DC': '1' }, body: JSON.stringify(post) });
  const body = await r.json().catch(() => ({}));
  if (!r.ok && r.status !== 409) throw new Error(body.error || `${r.status}`);
  return body;
}

function jobHTML() {
  if (!job || !job.devices) return '';
  const rows = Object.entries(job.devices).map(([id, d]) => `<li><b>${esc(id)}</b> ${esc({ waiting: 'waiting', reading: 'reading…', done: `done, ${d.read || 0} of ${d.files} read`, failed: 'could not check' }[d.state] || d.state)}${d.error ? `<small>${esc(d.error)}</small>` : ''}</li>`).join('');
  return `<ul class="job">${rows || '<li>Nothing left to check.</li>'}</ul>`;
}

function checkHTML() {
  const busy = job && job.running;
  return `<div class="check">
    <p>${busy ? `Checking: ${job.kind === 'full' ? 'every byte' : 'the start and end'} of each file, one device at a time…`
      : 'Read the files to be sure. Nothing is changed; the devices only read.'}</p>
    <button type="button" class="primary" data-check="partial" ${busy ? 'disabled' : ''}>Check them</button>
    <button type="button" class="ghost" data-check="full" ${busy ? 'disabled' : ''} title="Reads every byte of every file in these sets: slow for big files.">Check every byte</button>
    <details data-key><summary>How to allow it on a locked-down device</summary>
      <p class="muted">A device set up with the scanner's restricted line lets the hub run only the scanner, which never opens a file. Checking needs this second line in the same authorized_keys. It lets the hub's key run the read-only tools agent and nothing else.</p>
      <pre>${esc(keyLine || '…')}</pre></details>
    ${jobHTML()}</div>`;
}

async function poll() {
  job = await getJSON('/api/tools/jobs').catch(() => job);
  render();
  if (job && job.running) setTimeout(poll, 2000);
  else if (job && job.finished) load();
}

async function check(kind) {
  job = await getJSON('/api/tools/dupes/check', { full: kind === 'full' }).catch((e) => ({ error: e.message }));
  render();
  poll();
}

function where(f) {
  return `${f.device_name} · ${f.drive} · ${f.path}`;
}

function groupsHTML(groups, kind) {
  if (!groups.length) return `<p class="muted">${kind === 'films' ? 'No film or episode is in more than one copy.' : 'No two files are the same size.'}</p>`;
  return groups.map((g, i) => {
    const title = kind === 'films' ? esc(g.title) : esc(g.files[0].path.split('/').pop());
    const sure = kind === 'films' ? `keep: ${esc(g.keep)}` : `<span class="sure s-${g.sure}" title="${esc(SURE[g.sure][1])}">${SURE[g.sure][0]}</span>${g.round ? ' <span class="sure s-size" title="A whole number of MiB: swap files and disk images are made this size on purpose.">round size</span>' : ''}`;
    const open_ = shown && shown.kind === kind && shown.i === i;
    const files = !open_ ? '' : `<ul class="copies">${g.files.map((f, j) => `
      <li data-g="${i}" data-f="${j}" class="${f.keep ? 'keep' : ''}">
        ${kind === 'films' ? `<b class="badge">${f.keep ? 'Keep' : `#${f.rank}`}</b>` : ''}
        <span class="nm">${esc(f.path.split('/').pop())}<small>${esc(where(f))}</small></span>
        <span class="q">${kind === 'films' ? esc(f.same_bytes ? 'same size as Keep' : f.quality) : ''}</span><b>${size(f.size)}</b>
      </li>`).join('')}</ul>`;
    return `<li class="grp${open_ ? ' open' : ''}" data-g="${i}">
      <div class="row"><span class="nm">${title}<small>${g.count} copies · ${sure}</small></span><b>${size(g.wasted)}<small>extra</small></b></div>${files}</li>`;
  }).join('');
}

function render() {
  const box = $('#toolbox');
  box.hidden = !open;
  $('#toolsBtn').setAttribute('aria-pressed', String(open));
  if (!open) return;
  let body;
  if (error) body = `<p class="muted">${esc(error)}</p>`;
  else if (!report) body = '<p class="muted">Looking through every drive…</p>';
  else {
    const films = tab === 'films';
    const total = films ? report.wasted_films : report.wasted_bytes;
    const count = films ? report.groups_films : report.groups_bytes;
    const more = films ? report.more_films : report.more_bytes;
    body = `<p class="sum"><span>${films
      ? `<b>${count.toLocaleString()}</b> films and episodes are on your drives more than once: <b>${size(total)}</b> in the extra copies.`
      : `<b>${count.toLocaleString()}</b> sets of files are the same size: up to <b>${size(total)}</b>, but most are only leads until checked.`}</span>
      <button type="button" class="ghost" data-showall>${shown && shown.all ? 'Hide in city' : 'Show all in city'}</button></p>
      <p class="muted">${report.files.toLocaleString()} files looked at: everything the scans list, which is every file of the scan's minimum size and up. ${report.bytes.some((g) => g.sure === 'partial' || g.sure === 'hash') ? 'The checked sets were read on their devices; nothing was changed.' : 'Nothing on any device was opened or changed.'}</p>
      ${films ? '' : checkHTML()}
      <ul class="groups">${groupsHTML(films ? report.films : report.bytes, tab)}</ul>
      ${more ? `<p class="muted">…and ${more.toLocaleString()} smaller ones.</p>` : ''}`;
  }
  box.innerHTML = `<header><div class="seg float" role="group" aria-label="Duplicates">
      <button type="button" data-tab="films" aria-pressed="${tab === 'films'}">Same film</button>
      <button type="button" data-tab="bytes" aria-pressed="${tab === 'bytes'}">Same bytes</button></div>
      <button type="button" class="x" data-close aria-label="Close the toolbox">✕</button></header>
    <div class="scroll">${body}</div>`;
}

function markGroup(kind, i) {
  const g = (kind === 'films' ? report.films : report.bytes)[i];
  shown = { kind, i };
  city()?.mark(g.files.map((f) => ({ device: f.device, path: f.path })), MARK);
}

function markAll() {
  const groups = tab === 'films' ? report.films : report.bytes;
  shown = { all: true, kind: tab };
  city()?.mark(groups.flatMap((g) => g.files.map((f) => ({ device: f.device, path: f.path }))), MARK);
}

async function load() {
  error = '';
  render();
  try {
    report = await getJSON('/api/tools/dupes');
    if (!job) { job = await getJSON('/api/tools/jobs'); if (job.running) poll(); }
  } catch (e) {
    error = `Could not look for duplicates: ${e.message}`;
  }
  render();
}

// The Tools button only hides and shows the panel, so what is outlined stays
// in the city while she looks at it; the panel's ✕ clears the outlines too.
function toggle() {
  open = !open;
  render();
  if (open && !report) load();
}

function close() {
  open = false;
  shown = null;
  city()?.mark([]);
  render();
}

function setup() {
  if (city()?.inApp) return;       // the phone app's built-in copy has no hub to ask
  const btn = $('#toolsBtn');
  btn.hidden = false;
  btn.addEventListener('click', toggle);
  $('#toolbox').addEventListener('click', (e) => {
    const t = e.target;
    if (t.closest('[data-close]')) return close();
    const ck = t.closest('[data-check]');
    if (ck) return check(ck.dataset.check);
    if (t.closest('[data-key] summary') && !keyLine) {
      getJSON('/api/tools/key').then((r) => { keyLine = r.line; render(); $('#toolbox [data-key]').open = true; }).catch(() => {});
      return;
    }
    if (t.closest('details, pre')) return;
    const tb = t.closest('[data-tab]');
    if (tb) { tab = tb.dataset.tab; shown = null; city()?.mark([]); return render(); }
    if (t.closest('[data-showall]')) {
      if (shown && shown.all) { shown = null; city()?.mark([]); } else markAll();
      return render();
    }
    const file = t.closest('li[data-f]');
    if (file) {
      const g = (tab === 'films' ? report.films : report.bytes)[+file.dataset.g];
      const f = g.files[+file.dataset.f];
      // Out of the way, so the city can be seen; Tools brings the list back as it was.
      open = false;
      render();
      city()?.goTo(f.device, f.path);
      return;
    }
    const grp = t.closest('li.grp');
    if (grp) {
      const i = +grp.dataset.g;
      if (shown && shown.kind === tab && shown.i === i) { shown = null; city()?.mark([]); } else markGroup(tab, i);
      render();
    }
  });
}

setup();
