// D.C. O'HAIR front end: the drives from /api/city drawn as a 3D city.
//   City   - nested treemap; folders are raised blocks, files are towers
//            stacked by category.
//   Circle - fsv's TreeV idea: rings of platforms around the current folder.
//   EPCOT  - the hub is a mainframe in the middle, devices sit on a ring
//            around it, and every drive is a little round village.
// Tapping or clicking a block makes it the new root ("goes inside").
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';
import { api as request, openSettings, openWizard, showLogin } from './settings.js';

const CAT_NAMES = ['Music', 'Games', 'Series', 'Movies', 'Other'];
const THEMES = ['dark', 'light', 'sepia', 'cyberpunk', 'matrix', 'ocean'];
const VIEWS = ['city', 'circle', 'epcot'];
const W = 1000;             // City: the current root always fills a W x W square
const CITY_DEPTH = 5;
const CIRCLE_DEPTH = 4;
const RING = 108;           // Circle: distance between rings
const BIG = 120;            // folders with more children than this get grouped
const VILLAGE_HOUSES = 500;
const MAX_BOXES = 80000;
const MAX_LABELS = 40;
const BASE_VIEW = { pos: new THREE.Vector3(60, 640, 860), target: new THREE.Vector3(0, 0, 40) };
const TiB = 1024 ** 4, GiB = 1024 ** 3, MiB = 1024 ** 2;

const $ = (s) => document.querySelector(s);
const sum = (a) => a.reduce((x, y) => x + y, 0);
const zero = () => [0, 0, 0, 0, 0];
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

let units = 'binary';
function fmt(b) {
  if (b == null) return '—';
  const k = units === 'decimal' ? 1000 : 1024, x = units === 'decimal' ? 'B' : '';
  if (b >= k ** 4) return (b / k ** 4).toFixed(2) + 'T' + x;
  if (b >= 10 * k ** 3) return Math.round(b / k ** 3) + 'G' + x;
  if (b >= k ** 3) return (b / k ** 3).toFixed(1) + 'G' + x;
  if (b >= k ** 2) return Math.round(b / k ** 2) + 'M' + x;
  return b > 0 ? '<1M' + x : '0';
}

function toDate(iso) {
  return new Date(String(iso).replace(/([+-]\d\d)(\d\d)$/, '$1:$2'));
}

function clock(iso) {
  if (!iso) return '';
  const d = toDate(iso);
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return d.toDateString() === new Date().toDateString() ? time
    : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + time;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---------- preferences ----------

const prefs = (() => { try { return JSON.parse(localStorage.getItem('dc-prefs')) || {}; } catch { return {}; } })();
function savePrefs() { try { localStorage.setItem('dc-prefs', JSON.stringify(prefs)); } catch { /* private mode */ } }
let view = VIEWS.includes(prefs.view) ? prefs.view : 'city';
let sortBy = ['type', 'size', 'newest', 'oldest', 'name'].includes(prefs.sort) ? prefs.sort : 'size';
let showFree = prefs.free !== false;
// drive health stays hidden until someone turns it on; only a failing drive gets a small dot
let showHealth = prefs.health === true;
const HEALTH_LABEL = { ok: 'OK', warning: 'Warning', failing: 'Failing', unknown: 'Unknown' };
const healthOf = (n) => (n && n.kind === 'drive' && n.drive && n.drive.health) || null;
const failing = (n) => { const h = healthOf(n); return !!h && h.state === 'failing' && !(api && api.ui && api.ui.failing_dot === false); };
const healthColor = (h) => (h ? { ok: T.green, warning: '#f5a524', failing: T.red }[h.state] : null);
// category filter: the legend chips switch Music/Games/Series/Movies/Other on and off everywhere
let catsOn = Array.isArray(prefs.cats) && prefs.cats.length === 5 ? prefs.cats.map(Boolean) : [true, true, true, true, true];

// ---------- theme ----------

let T = {};
function readTheme() {
  const cs = getComputedStyle(document.documentElement);
  const v = (n) => cs.getPropertyValue(n).trim();
  T = { bg: v('--bg'), bg2: v('--bg2'), surface: v('--surface'), border: v('--border'), text: v('--text'),
    text2: v('--text2'), accent: v('--accent'), cats: [0, 1, 2, 3, 4].map((i) => v('--cat-' + i)),
    free: v('--free'), unseen: v('--unseen'), green: v('--green'), red: v('--red') };
  T.light = new THREE.Color(T.bg).getHSL({}).l > 0.5;
}

function mix(a, b, t) {
  return '#' + new THREE.Color(a).lerp(new THREE.Color(b), t).getHexString();
}

const slabColor = (depth) => mix(T.surface, T.text, 0.05 + Math.min(depth, 6) * 0.035);

// ---------- data ----------

let api = null;
let all = null;          // root of everything
let root = null;         // what the city currently shows
let selected = null;     // tapped leaf shown in the card
let layout = null;
let history = {};
let lastText = '';

function wrap(t, parent, drive) {
  const node = { n: t.n, c: t.c, f: t.f || 0, m: t.m || 0, s: sum(t.c), k: [], p: parent, drive,
    kind: t.t === 'f' ? 'file' : t.x ? 'rest' : 'dir' };
  for (const kid of t.k || []) node.k.push(wrap(kid, node, drive));
  return node;
}

function prep(data) {
  const top = { n: 'All devices', kind: 'all', c: zero(), s: 0, f: 0, m: 0, free: 0, k: [], p: null };
  for (const dev of data.devices) {
    const dn = { n: dev.name, kind: 'device', dev, c: zero(), s: 0, f: 0, m: 0, free: 0, k: [], p: top };
    for (const dr of dev.drives) {
      const t = dr.tree || { c: zero(), f: 0 };
      const node = { n: dr.name, kind: 'drive', dev, drive: dr, c: t.c.slice(), f: t.f || 0, m: t.m || 0,
        free: dr.fs ? dr.fs.free : 0, k: [], p: dn };
      for (const kid of t.k || []) node.k.push(wrap(kid, node, dr));
      const seen = sum(node.c);
      const unseen = dr.fs ? Math.max(0, dr.fs.used - seen) : 0;
      node.s = seen;
      if (unseen > Math.max(GiB, dr.fs.used * 0.01)) {
        node.k.push({ n: seen ? 'not seen by the scan' : 'not broken down yet', kind: 'unseen',
          c: zero(), s: unseen, f: 0, m: 0, k: [], p: node, drive: dr });
        node.s += unseen;
      }
      dr.node = node;
      dn.k.push(node);
      for (let i = 0; i < 5; i++) dn.c[i] += node.c[i];
      dn.s += node.s; dn.f += node.f; dn.free += node.free; dn.m = Math.max(dn.m, node.m);
    }
    top.k.push(dn);
    for (let i = 0; i < 5; i++) top.c[i] += dn.c[i];
    top.s += dn.s; top.f += dn.f; top.free += dn.free; top.m = Math.max(top.m, dn.m);
  }
  return top;
}

const hasFree = (n) => n.kind === 'all' || n.kind === 'device' || n.kind === 'drive';
const allOn = () => catsOn.every(Boolean);
// the size a node shows with the category filter applied; unseen and free space only show unfiltered
const eff = (n) => {
  if (allOn()) return n.s;
  let v = 0;
  for (let i = 0; i < 5; i++) if (catsOn[i]) v += n.c[i];
  return v;
};
const lsz = (n) => eff(n) + (showFree && allOn() && hasFree(n) ? n.free : 0);
const isLeaf = (n) => n.kind === 'file' || n.kind === 'rest' || n.kind === 'unseen' || n.kind === 'free';
const canDrill = (n) => !!n && !isLeaf(n) && n.kind !== 'loose' && n.k.length > 0;
const dominant = (n) => (n.kind === 'unseen' ? 5 : n.c.indexOf(Math.max(...n.c)));

const ORDER = {
  size: (a, b) => lsz(b) - lsz(a),
  type: (a, b) => dominant(a) - dominant(b) || lsz(b) - lsz(a),
  newest: (a, b) => (b.m || 0) - (a.m || 0),
  oldest: (a, b) => (a.m || 9e15) - (b.m || 9e15),
  name: (a, b) => collator.compare(a.n, b.n),
};

function group(parent, name, kids) {
  const g = { n: name, kind: 'group', c: zero(), s: 0, f: 0, m: 0, k: kids, p: parent, drive: parent.drive };
  for (const k of kids) {
    for (let i = 0; i < 5; i++) g.c[i] += k.c[i];
    g.s += k.s; g.f += k.f; g.m = Math.max(g.m, k.m || 0);
  }
  for (const k of kids) k.shownIn = g;
  return g;
}

// big flat folders get split into blocks that follow the sort: letters,
// years, size bands, or Music/Games/Series/Movies/Other districts
function bandOf(n) {
  if (sortBy === 'name') { const ch = n.n.trim().charAt(0).toUpperCase(); return /[A-Z]/.test(ch) ? ch : '#'; }
  if (sortBy === 'newest' || sortBy === 'oldest') return n.m ? String(new Date(n.m * 1000).getFullYear()) : 'unknown';
  const g = n.s / GiB;
  return g >= 20 ? '20G and up' : g >= 8 ? '8–20G' : g >= 3 ? '3–8G' : g >= 1 ? '1–3G' : 'under 1G';
}

function orderKids(node) {
  const key = sortBy + showFree + catsOn.join('');
  if (node.orderKey === key) return node.ordered;
  let ks = node.k.filter((k) => lsz(k) > 0);
  if (node.kind !== 'group') for (const k of ks) k.shownIn = null;
  const tail = ks.filter((k) => k.kind === 'rest' || k.kind === 'unseen').sort((a, b) => lsz(b) - lsz(a));
  ks = ks.filter((k) => k.kind !== 'rest' && k.kind !== 'unseen').sort(ORDER[sortBy]);
  if (node.kind === 'drive' || node.kind === 'dir') {
    let buckets = null;
    if (sortBy === 'type' && ks.length >= 4) {
      buckets = new Map();
      for (const k of ks) { const d = CAT_NAMES[dominant(k)] || 'Other'; if (!buckets.has(d)) buckets.set(d, []); buckets.get(d).push(k); }
    } else if (ks.length > BIG) {
      buckets = new Map();
      for (const k of ks) { const b = bandOf(k); if (!buckets.has(b)) buckets.set(b, []); buckets.get(b).push(k); }
    }
    if (buckets && buckets.size > 1) {
      ks = [...buckets].map(([name, kids]) => group(node, name, kids));
      if (sortBy === 'size' || sortBy === 'type') ks.sort((a, b) => lsz(b) - lsz(a));
    }
  }
  node.ordered = ks.concat(tail);
  node.orderKey = key;
  return node.ordered;
}

function kidsFor(node) {
  const ks = orderKids(node).slice();
  if (showFree && allOn() && node.kind === 'drive' && node.free > 0) {
    node.lot = node.lot || { n: 'free space', kind: 'free', c: zero(), s: 0, f: 0, m: 0, k: [], p: node, drive: node.drive };
    node.lot.s = node.free;
    ks.push(node.lot);
  }
  return ks;
}

const parentOf = (n) => n.shownIn || n.p;

// the real folders a node lives in (no sort/group blocks), for lists
function realPath(node) {
  const names = [];
  for (let n = node.p; n && n.kind !== 'all'; n = n.p) names.unshift(n.n);
  return names.join(' › ');
}

function pathOf(node) {
  const names = [];
  for (let n = parentOf(node); n && n.kind !== 'all'; n = parentOf(n)) names.unshift(n.n);
  return names.join(' › ');
}

// ---------- layout ----------

function newLayout() {
  return { items: [], lines: [], rings: [], paths: [], labels: [], bounds: new Map(), extent: W / 2 };
}

function box(L, node, cx, cz, w, d, y, h, color, extra) {
  L.items.push(Object.assign({ shape: 'box', mat: 'solid', node, cx, cz, w, d, y, h, color, rot: 0 }, extra));
}

function cyl(L, node, cx, cz, r, y, h, color, extra) {
  L.items.push(Object.assign({ shape: 'cyl', mat: 'solid', node, cx, cz, w: 2 * r, d: 2 * r, y, h, color, rot: 0 }, extra));
}

function catParts(node) {
  if (node.kind === 'unseen') return allOn() ? [[T.unseen, node.s]] : [];
  return T.cats.map((c, i) => [c, catsOn[i] ? node.c[i] : 0]).filter((p) => p[1] > 0);
}

// a tower stacked by category, bottom to top in legend order
function column(L, node, cx, cz, w, d, y, H, extra) {
  const parts = catParts(node);
  const total = sum(parts.map((p) => p[1])) || 1;
  let yy = y;
  for (const [color, v] of parts) {
    const h = (H * v) / total;
    (extra && extra.shape === 'cyl' ? cyl(L, node, cx, cz, w / 2, yy, h, color, extra) : box(L, node, cx, cz, w, d, yy, h, color, extra));
    yy += h;
  }
}

function label(L, node, x, y, z, r, depth) {
  const dot = failing(node) ? '<i class="fail" title="This drive is failing">●</i>' : '';
  L.labels.push({ node, x, y, z, r, depth, html: `${dot}<b>${esc(node.n)}</b><span>${fmt(eff(node))}</span>` });
}

// with Health on, a drive's block sits on a glowing green, amber or red rim
function healthRim(L, node, cx, cz, w, d, y, rot = 0) {
  const h = showHealth && healthOf(node);
  if (!h || !healthColor(h)) return;
  const pad = Math.max(1, Math.min(w, d) * 0.02);
  box(L, null, cx, cz, w + 2 * pad, d + 2 * pad, Math.max(0, y - 0.3), 0.9, healthColor(h), { mat: 'glow', rot });
}

// squarified treemap: values -> rects inside (x, z, w, d), same order as values
function squarify(values, x, z, w, d) {
  const total = sum(values);
  const out = new Array(values.length);
  if (total <= 0 || w <= 0 || d <= 0) return out;
  const scale = (w * d) / total;
  const order = values.map((v, i) => [v * scale, i]).filter((a) => a[0] > 0);
  let row = [];
  const worst = (r, side) => {
    const s = sum(r.map((a) => a[0]));
    let mx = 0, mn = Infinity;
    for (const [a] of r) { mx = Math.max(mx, a); mn = Math.min(mn, a); }
    return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn));
  };
  const flush = () => {
    const s = sum(row.map((a) => a[0]));
    if (w >= d) {
      const cw = s / d;
      let zz = z;
      for (const [a, i] of row) { const h = a / cw; out[i] = { x, z: zz, w: cw, d: h }; zz += h; }
      x += cw; w -= cw;
    } else {
      const ch = s / w;
      let xx = x;
      for (const [a, i] of row) { const ww = a / ch; out[i] = { x: xx, z, w: ww, d: ch }; xx += ww; }
      z += ch; d -= ch;
    }
    row = [];
  };
  for (let i = 0; i < order.length;) {
    const side = Math.min(w, d);
    const next = row.concat([order[i]]);
    if (!row.length || worst(row, side) >= worst(next, side)) { row = next; i++; } else flush();
  }
  if (row.length) flush();
  return out;
}

function layoutCity(top) {
  const L = newLayout();
  placeCity(L, top, -W / 2, -W / 2, W, W, 0, 0);
  return L;
}

function placeCity(L, node, x, z, w, d, y, depth) {
  const side = Math.min(w, d);
  if (side < 0.2) return;
  if (node.kind === 'free') {
    const g = side * 0.04;
    box(L, node, x + w / 2, z + d / 2, w - 2 * g, d - 2 * g, y, 0.15, T.free, { mat: 'glass' });
    L.bounds.set(node, { cx: x + w / 2, cz: z + d / 2, w: w - 2 * g, d: d - 2 * g, y0: y, y1: y + 0.15 });
    if (side > 30) label(L, node, x + w / 2, y + 0.2, z + d / 2, side / 2, depth + 1);
    return;
  }
  const kids = depth < CITY_DEPTH && side > 6 && L.items.length < MAX_BOXES ? kidsFor(node) : [];
  if (!kids.length) {
    const g = side * 0.1;
    const fw = w - 2 * g, fd = d - 2 * g;
    if (eff(node) <= 0 || fw <= 0 || fd <= 0) return;
    const H = Math.min(Math.sqrt(fw * fd) * 0.85, W * 0.09);
    healthRim(L, node, x + w / 2, z + d / 2, fw, fd, y);
    column(L, node, x + w / 2, z + d / 2, fw, fd, y, H);
    L.bounds.set(node, { cx: x + w / 2, cz: z + d / 2, w: fw, d: fd, y0: y, y1: y + H });
    label(L, node, x + w / 2, y + H, z + d / 2, side / 2, depth);
    return;
  }
  const gap = side * 0.025;
  const th = Math.min(Math.max(side * 0.012, 0.25), 6);
  healthRim(L, node, x + w / 2, z + d / 2, w - 2 * gap, d - 2 * gap, y);
  box(L, node, x + w / 2, z + d / 2, w - 2 * gap, d - 2 * gap, y, th, slabColor(depth));
  L.bounds.set(node, { cx: x + w / 2, cz: z + d / 2, w: w - 2 * gap, d: d - 2 * gap, y0: y, y1: y + th });
  if (depth > 0) label(L, node, x + w / 2, y + th, z + gap, side / 2, depth);
  const pad = gap + side * 0.03;
  const rects = squarify(kids.map(lsz), x + pad, z + pad, w - 2 * pad, d - 2 * pad);
  kids.forEach((k, i) => rects[i] && placeCity(L, k, rects[i].x, rects[i].z, rects[i].w, rects[i].d, y + th, depth + 1));
}

function layoutCircle(top) {
  const L = newLayout();
  placeCircle(L, top, 0, -Math.PI / 2, Math.PI * 1.5, Math.max(eff(top), 1), null);
  for (let r = 1; r <= CIRCLE_DEPTH; r++) L.rings.push(r * RING);
  L.extent = CIRCLE_DEPTH * RING + 60;
  return L;
}

function placeCircle(L, node, depth, a0, a1, maxS, from) {
  const r = depth * RING;
  const a = (a0 + a1) / 2;
  const cx = r * Math.cos(a), cz = r * Math.sin(a);
  const side = depth === 0 ? RING * 0.62 : Math.min(r * (a1 - a0) * 0.72, RING * 0.5);
  if (side < 0.5) return;
  const rot = -a;
  if (from) L.lines.push(from.x, 0.4, from.z, cx, 0.4, cz);
  if (node.kind === 'free') {
    box(L, node, cx, cz, side, side, 0, 0.5, T.free, { mat: 'glass', rot });
    L.bounds.set(node, { cx, cz, w: side, d: side, y0: 0, y1: 0.5, rot });
    return;
  }
  const th = 1.2;
  healthRim(L, node, cx, cz, side * 1.12, side * 1.12, 0, rot);
  box(L, node, cx, cz, side * 1.12, side * 1.12, 0, th, slabColor(depth + 1), { rot });
  const H = eff(node) > 0 ? 4 + 92 * Math.sqrt(eff(node) / maxS) : 0;
  if (H) column(L, node, cx, cz, side * 0.7, side * 0.7, th, H, { rot });
  L.bounds.set(node, { cx, cz, w: side * 1.12, d: side * 1.12, y0: 0, y1: th + H, rot });
  if (depth > 0 || node.kind !== 'all') label(L, node, cx, th + H, cz, side / 2, depth);
  if (depth >= CIRCLE_DEPTH) return;
  const kids = kidsFor(node);
  const total = sum(kids.map(lsz));
  let t = a0;
  for (const k of kids) {
    const span = ((a1 - a0) * lsz(k)) / total;
    placeCircle(L, k, depth + 1, t, t + span, maxS, { x: cx, z: cz });
    t += span;
  }
}

// ----- EPCOT -----

function beam(L, x1, z1, x2, z2, width = 1.8) {
  const dx = x2 - x1, dz = z2 - z1, len = Math.hypot(dx, dz);
  if (len < 1) return;
  L.items.push({ shape: 'box', mat: 'glow', node: null, cx: (x1 + x2) / 2, cz: (z1 + z2) / 2, w: len, d: width,
    y: 0.05, h: 0.35, color: T.accent, rot: -Math.atan2(dz, dx) });
  L.paths.push([x2, z2, x1, z1, len]);
}

function tower(L, node, x, z, r, H, go) {
  cyl(L, node, x, z, r * 1.25, 0, 1.6, slabColor(1), { go });
  column(L, node, x, z, 2 * r, 2 * r, 1.6, H, { shape: 'cyl', go });
  for (const f of [0.3, 0.6, 0.9]) cyl(L, null, x, z, r * 1.12, 1.6 + H * f, Math.max(1, H * 0.012), T.accent, { mat: 'glow' });
  cyl(L, null, x, z, r * 0.35, 1.6 + H, r * 0.25, T.accent, { mat: 'glow' });
  L.bounds.set(node, { cx: x, cz: z, w: 2.5 * r, d: 2.5 * r, y0: 0, y1: 1.6 + H + r * 0.25 });
}

// a round plate with the node's children as houses, biggest in the middle
function village(L, node, x, z, r, depth) {
  if (node.kind === 'free') {
    cyl(L, node, x, z, r, 0, 0.6, T.free, { mat: 'glass' });
    L.bounds.set(node, { cx: x, cz: z, w: 2 * r, d: 2 * r, y0: 0, y1: 0.6 });
    label(L, node, x, 0.6, z, r, depth);
    return;
  }
  const go = canDrill(node) ? node : null;
  cyl(L, null, x, z, r + (showHealth && healthOf(node) ? 3 : 1.4), 0, 0.5, (showHealth && healthColor(healthOf(node))) || T.accent, { mat: 'glow' });
  cyl(L, node, x, z, r, 0, 1.4, slabColor(depth), { go });
  const houses = isLeaf(node) ? [node] : (node.kind === 'loose' ? node.k : kidsFor(node).filter((k) => k.kind !== 'free')).slice(0, VILLAGE_HOUSES);
  const smax = Math.max(1, ...houses.map(lsz));
  const n = houses.length;
  const R = r * 0.84;
  const spacing = n > 1 ? R * Math.sqrt(Math.PI / n) : r;
  let top = 1.4;
  houses.forEach((h, i) => {
    const rr = n === 1 ? 0 : R * Math.sqrt((i + 0.5) / n);
    const th = i * 2.399963;
    const share = Math.sqrt(lsz(h) / smax);
    const side = Math.min(r * 0.9, spacing * 0.74 * (0.45 + 0.55 * share));
    const H = 1 + side * (0.6 + 2.6 * share);
    const hx = x + rr * Math.cos(th), hz = z + rr * Math.sin(th);
    column(L, h, hx, hz, side, side, 1.4, H, { go });
    if (h !== node) L.bounds.set(h, { cx: hx, cz: hz, w: side, d: side, y0: 1.4, y1: 1.4 + H });
    top = Math.max(top, 1.4 + H);
  });
  L.bounds.set(node, { cx: x, cz: z, w: 2 * r, d: 2 * r, y0: 0, y1: top });
  label(L, node, x, top, z, r, depth);
}

// villages packed around a circle, each getting arc space for its diameter
function ringOf(L, nodes, cx, cz, rOf, minR, depth, from) {
  if (!nodes.length) return 0;
  const gap = 16;
  const arcs = nodes.map((n) => 2 * rOf(n) + gap);
  const R = Math.max(minR, sum(arcs) / (2 * Math.PI));
  let a = -Math.PI / 2 - arcs[0] / R / 2;
  nodes.forEach((n, i) => {
    const span = arcs[i] / R;
    const mid = a + span / 2;
    const x = cx + R * Math.cos(mid), z = cz + R * Math.sin(mid);
    if (from) beam(L, from.x, from.z, x, z);
    village(L, n, x, z, rOf(n), depth);
    a += span;
  });
  return R + Math.max(...nodes.map(rOf));
}

// at folder level, loose files share one village instead of one each
function villagesOf(node) {
  const kids = kidsFor(node);
  const folders = kids.filter((k) => !(k.kind === 'file' || k.kind === 'rest' || k.kind === 'unseen'));
  const loose = kids.filter((k) => k.kind === 'file' || k.kind === 'rest' || k.kind === 'unseen');
  if (loose.length) {
    const v = group(node, folders.length ? 'files here' : node.n, loose);
    v.kind = 'loose';
    for (const k of loose) k.shownIn = null;
    folders.push(v);
  }
  return folders;
}

function layoutEpcot(top) {
  const L = newLayout();
  const hubDev = top.kind === 'all' ? top.k.find((d) => d.dev && d.dev.hub) : null;
  const inner = hubDev ? kidsFor(hubDev) : top.kind === 'all' ? [] : villagesOf(top);
  const stations = top.kind === 'all' ? kidsFor(top).filter((d) => d !== hubDev) : [];
  const fans = stations.map((dev) => kidsFor(dev));
  const sized = inner.concat(...fans);
  const maxS = Math.max(1, ...sized.map(lsz));
  const vmax = top.kind === 'all' ? 64 : 80;
  const rOf = (n) => Math.max(top.kind === 'all' ? 14 : 10, Math.sqrt(lsz(n) / maxS) * vmax);

  const coreR = 30;
  tower(L, hubDev || top, 0, 0, coreR, 120, hubDev);
  label(L, hubDev || top, 0, 128, 0, 60, 0);
  L.labels[L.labels.length - 1].html = `<b>${esc((hubDev || top).n)}</b><span>${hubDev ? 'hub' : fmt(top.s)}</span>`;
  let extent = ringOf(L, inner, 0, 0, rOf, coreR + 40 + Math.max(0, ...inner.map(rOf)), 1, { x: 0, z: 0 });

  if (stations.length) {
    // every device gets a fan of its drives pointing away from the mainframe
    const fanR = fans.map((ds) => Math.max(70, ...ds.map((d) => 40 + rOf(d) * 1.4)));
    const widths = fans.map((ds, i) => Math.max(2 * fanR[i] * 0.9, sum(ds.map((d) => 2 * rOf(d) + 14)) * 0.8));
    const R1 = Math.max(extent + 160, sum(widths) / (2 * Math.PI) + 60);
    let a = -Math.PI / 2 + (inner.length ? Math.PI / stations.length : 0) - widths[0] / R1 / 2;
    stations.forEach((dev, i) => {
      const span = widths[i] / R1;
      const mid = a + span / 2;
      a += span;
      const x = R1 * Math.cos(mid), z = R1 * Math.sin(mid);
      beam(L, 0, 0, x, z, 2.6);
      tower(L, dev, x, z, 14, 38, dev);
      label(L, dev, x, 46, z, 30, 1);
      const ds = fans[i];
      const arcs = ds.map((d) => (2 * rOf(d) + 14) / fanR[i]);
      let b = mid - sum(arcs) / 2;
      ds.forEach((d, j) => {
        const m = b + arcs[j] / 2;
        b += arcs[j];
        const vx = x + fanR[i] * Math.cos(m), vz = z + fanR[i] * Math.sin(m);
        beam(L, x, z, vx, vz);
        village(L, d, vx, vz, rOf(d), 2);
      });
      extent = Math.max(extent, R1 + fanR[i] + Math.max(0, ...ds.map(rOf)));
    });
  }
  L.extent = Math.max(260, extent) * 1.05;
  return L;
}

// ---------- three.js scene ----------

const canvas = $('#city');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
const scene = new THREE.Scene();
scene.background = new THREE.Color();
scene.fog = new THREE.Fog('#000', 1400, 5200);
const camera = new THREE.PerspectiveCamera(50, 1, 0.5, 14000);
camera.position.copy(BASE_VIEW.pos);
const controls = new OrbitControls(camera, canvas);
controls.target.copy(BASE_VIEW.target);
controls.enableDamping = true;
controls.dampingFactor = 0.12;
controls.zoomToCursor = true;
controls.screenSpacePanning = false;
controls.maxPolarAngle = Math.PI * 0.49;
controls.minDistance = 2;
controls.maxDistance = 6000;

const hemi = new THREE.HemisphereLight('#a9c4ff', '#0b0d18', 1.1);
scene.add(hemi);
const sun = new THREE.DirectionalLight('#ffffff', 1.6);
sun.position.set(0.6, 1.4, 0.9);
scene.add(sun);
const ground = new THREE.Mesh(new THREE.PlaneGeometry(12000, 12000), new THREE.MeshBasicMaterial());
ground.rotation.x = -Math.PI / 2;
ground.position.y = -0.05;
scene.add(ground);
let grid = null;

const unitBox = new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0);
const unitCyl = new THREE.CylinderGeometry(0.5, 0.5, 1, 40).translate(0, 0.5, 0);
const MATS = {
  solid: new THREE.MeshLambertMaterial({ color: '#ffffff' }),
  glass: new THREE.MeshLambertMaterial({ color: '#ffffff', transparent: true, opacity: 0.6 }),
  glow: new THREE.MeshBasicMaterial({ color: '#ffffff', transparent: true, opacity: 0.9, depthWrite: false }),
};
const lineMat = new THREE.LineBasicMaterial();
const ringMat = new THREE.LineBasicMaterial();
const packetMat = new THREE.MeshBasicMaterial();
const world = new THREE.Group();
scene.add(world);
let pickables = [];
let packets = null;

const hi = new THREE.LineSegments(new THREE.EdgesGeometry(unitBox),
  new THREE.LineBasicMaterial({ transparent: true, opacity: 0.95, depthTest: false }));
hi.renderOrder = 10;
hi.visible = false;
scene.add(hi);

function themeScene() {
  readTheme();
  scene.background.set(T.bg);
  scene.fog.color.set(T.bg);
  ground.material.color.set(T.bg2);
  if (grid) { scene.remove(grid); grid.geometry.dispose(); grid.material.dispose(); }
  grid = new THREE.GridHelper(12000, 240, mix(T.bg2, T.text, 0.16), mix(T.bg2, T.text, 0.07));
  grid.position.y = -0.02;
  scene.add(grid);
  hemi.intensity = T.light ? 1.5 : 1.1;
  hemi.groundColor.set(T.light ? '#8a8577' : '#0b0d18');
  sun.intensity = T.light ? 1.2 : 1.6;
  hi.material.color.set(T.text);
  MATS.glow.blending = T.light ? THREE.NormalBlending : THREE.AdditiveBlending;
  MATS.glow.needsUpdate = true;
  lineMat.color.set(mix(T.border, T.accent, 0.5));
  ringMat.color.set(T.border);
  packetMat.color.set(T.accent);
  document.querySelectorAll('#themes button').forEach((b) =>
    b.setAttribute('aria-pressed', String(b.dataset.theme === document.documentElement.dataset.theme)));
  if (all) rebuild();
  dirty = true;
}

function setTheme(name) {
  document.documentElement.setAttribute('data-theme', name);
  const cfg = window.DC_THEME || { cookie: 'dc-theme', storage: 'dc-theme', domain: '' };
  const bare = (cfg.domain || '').replace(/^\./, '');
  const domain = bare && (location.hostname === bare || location.hostname.endsWith('.' + bare)) ? ';domain=.' + bare : '';
  document.cookie = `${cfg.cookie}=${name}${domain};path=/;max-age=31536000;SameSite=Lax`;
  try { localStorage.setItem(cfg.storage, name); } catch { /* private mode */ }
  themeScene();
}

function instanced(list, geometry, material) {
  const mesh = new THREE.InstancedMesh(geometry, material, Math.max(1, list.length));
  mesh.count = list.length;
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), p = new THREE.Vector3(), s = new THREE.Vector3();
  const up = new THREE.Vector3(0, 1, 0), col = new THREE.Color();
  list.forEach((b, i) => {
    q.setFromAxisAngle(up, b.rot || 0);
    m.compose(p.set(b.cx, b.y, b.cz), q, s.set(Math.max(b.w, 0.01), Math.max(b.h, 0.01), Math.max(b.d, 0.01)));
    mesh.setMatrixAt(i, m);
    mesh.setColorAt(i, col.set(b.color));
  });
  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  mesh.computeBoundingSphere();
  mesh.userData.items = list;
  return mesh;
}

const layouts = { city: layoutCity, circle: layoutCircle, epcot: layoutEpcot };

function rebuild() {
  for (const o of [...world.children]) {
    world.remove(o);
    if (o.isInstancedMesh) o.dispose();
    else if (o.geometry) o.geometry.dispose();
  }
  layout = layouts[view](root);
  pickables = [];
  const bins = {};
  for (const it of layout.items) (bins[it.shape + ':' + it.mat] ||= []).push(it);
  for (const [key, list] of Object.entries(bins)) {
    const [shape, mat] = key.split(':');
    const mesh = instanced(list, shape === 'cyl' ? unitCyl : unitBox, MATS[mat]);
    if (mat === 'glow') mesh.renderOrder = 2;
    else pickables.push(mesh);
    world.add(mesh);
  }
  if (layout.lines.length) {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(layout.lines, 3));
    world.add(new THREE.LineSegments(g, lineMat));
  }
  for (const r of layout.rings) {
    const pts = [];
    for (let i = 0; i <= 256; i++) { const a = (i / 256) * Math.PI * 2; pts.push(new THREE.Vector3(r * Math.cos(a), 0.05, r * Math.sin(a))); }
    world.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), ringMat));
  }
  packets = null;
  if (layout.paths.length) {
    const count = Math.min(layout.paths.length * 2, 260);
    packets = new THREE.InstancedMesh(unitBox, packetMat, count);
    packets.userData.seeds = Array.from({ length: count }, (_, i) => [i % layout.paths.length, Math.random()]);
    world.add(packets);
  }
  layout.labels.sort((a, b) => a.depth - b.depth || eff(b.node) - eff(a.node));
  layout.labels.length = Math.min(layout.labels.length, 900);
  setHover(null);
  dirty = true;
}

// data packets flowing along the EPCOT beams toward the mainframe
const pm = new THREE.Matrix4(), pq = new THREE.Quaternion(), pp = new THREE.Vector3(), ps = new THREE.Vector3(2.2, 2.2, 2.2);
function stepPackets(now) {
  if (!packets) return false;
  packets.userData.seeds.forEach(([pi, off], i) => {
    const [x1, z1, x2, z2, len] = layout.paths[pi];
    const u = (now / 1000 * 70 / len + off) % 1;
    pm.compose(pp.set(x1 + (x2 - x1) * u, 1.2, z1 + (z2 - z1) * u), pq, ps);
    packets.setMatrixAt(i, pm);
  });
  packets.instanceMatrix.needsUpdate = true;
  return true;
}

// ---------- labels ----------

const labelBox = $('#labels');
const pool = [];
const v3 = new THREE.Vector3();

function drawLabels() {
  const wv = canvas.clientWidth, hv = canvas.clientHeight;
  const focal = hv / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2));
  const placed = [];
  let used = 0;
  for (const c of layout ? layout.labels : []) {
    if (used >= MAX_LABELS) break;
    v3.set(c.x, c.y, c.z);
    const dist = v3.distanceTo(camera.position);
    v3.project(camera);
    if (v3.z > 1 || Math.abs(v3.x) > 1.02 || Math.abs(v3.y) > 1.02) continue;
    const px = (c.r / dist) * focal;
    if (px < (c.depth <= 1 ? 14 : 34)) continue;
    const sx = ((v3.x + 1) / 2) * wv, sy = ((1 - v3.y) / 2) * hv;
    const lw = 14 + (c.node.n.length + 6) * 7, lh = 20;
    const rect = [sx - lw / 2, sy - lh - 4, sx + lw / 2, sy - 4];
    if (placed.some((p) => rect[0] < p[2] && rect[2] > p[0] && rect[1] < p[3] && rect[3] > p[1])) continue;
    placed.push(rect);
    let el = pool[used];
    if (!el) { el = document.createElement('div'); el.className = 'label'; labelBox.appendChild(el); pool.push(el); }
    if (el.cand !== c) { el.innerHTML = c.html; el.cand = c; el.classList.toggle('big', c.depth <= 1); }
    el.style.transform = `translate(${sx}px, ${sy - 4}px) translate(-50%, -100%)`;
    el.hidden = false;
    used++;
  }
  for (let i = used; i < pool.length; i++) pool[i].hidden = true;
}

// ---------- info: hover tooltip and the card ----------

function infoHTML(node) {
  const dr = node.drive;
  const parts = [`<h3>${esc(node.n)}</h3>`];
  const path = pathOf(node);
  if (path) parts.push(`<div class="path">${esc(path)}</div>`);
  parts.push(`<div class="size">${fmt(eff(node))}${allOn() ? '' : `<small> shown of ${fmt(node.s)}</small>`}</div>`);
  if (node.kind === 'drive' && dr.fs) {
    const pct = Math.round((100 * dr.fs.used) / dr.fs.total);
    parts.push(`<div class="fs">Used ${fmt(dr.fs.used)} (${pct}%) · Free ${fmt(dr.fs.free)} · Total ${fmt(dr.fs.total)}</div>`);
    const h = dr.health;
    if (h) {
      const bits = [h.model, h.temp != null && `${h.temp}°C`, h.hours != null && `${h.hours.toLocaleString()} hours on`,
        h.realloc && `${h.realloc} reallocated`, h.pending && `${h.pending} pending`, h.used_pct != null && `${h.used_pct}% worn`].filter(Boolean);
      parts.push(`<div class="health ${h.state}"><b>Health: ${HEALTH_LABEL[h.state] || h.state}</b> <span>${esc(bits.join(' · '))}</span>`
        + `${(h.notes || []).length ? `<div>${esc(h.notes.join('; '))}</div>` : h.note ? `<div>${esc(h.note)}</div>` : ''}</div>`);
    }
  } else if (hasFree(node) && node.free) {
    parts.push(`<div class="fs">Free ${fmt(node.free)}</div>`);
  }
  if (node.kind === 'unseen') {
    parts.push('<div class="fs">Used on the drive but not counted by the scan (locked folders, or not broken down yet).</div>');
  } else if (node.kind === 'free') {
    parts.push('<div class="fs">Empty space you can still fill.</div>');
  } else {
    const tot = sum(node.c) || 1;
    node.c.forEach((v, i) => {
      if (!v) return;
      parts.push(`<div class="row"><span>${CAT_NAMES[i]}</span><span class="bar"><i style="width:${(100 * v) / tot}%;background:${T.cats[i]}"></i></span><span class="num">${fmt(v)}</span></div>`);
    });
    const bits = [];
    if (node.f) bits.push(`${node.f.toLocaleString()} ${node.kind === 'rest' ? 'small ' : ''}${node.f === 1 ? 'file' : 'files'}`);
    if (node.m) bits.push((node.kind === 'file' ? 'changed ' : 'newest ') + new Date(node.m * 1000).toLocaleDateString());
    if (bits.length) parts.push(`<div class="src">${bits.join(' · ')}</div>`);
  }
  const src = dr || (node.kind === 'device' && node.dev.drives[0]);
  if (src && src.source) parts.push(`<div class="src">${src.source === 'live' ? 'live scan' : src.source === 'snapshot' ? 'snapshot' : 'no data'} ${clock(src.when)}</div>`);
  return parts.join('');
}

const card = $('#card');
card.classList.add('float', 'info');
function showCard() {
  const node = selected || root;
  if (!node) return;
  card.classList.toggle('min', !!prefs.cardMin);
  if (prefs.cardMin) {
    card.innerHTML = `<button class="grow" type="button" title="Show details">ⓘ <b>${esc(node.n)}</b> ${fmt(eff(node))}</button>`;
    return;
  }
  const close = selected ? '<button class="x" type="button" aria-label="Close">×</button>' : '';
  const go = canDrill(node) && node !== root ? '<button class="goin" type="button">Go inside →</button>' : '';
  card.innerHTML = close + '<button class="x" type="button" data-min aria-label="Hide details" title="Hide details">–</button>' + infoHTML(node) + go;
}
card.addEventListener('click', (e) => {
  if (e.target.closest('[data-min]')) { prefs.cardMin = true; savePrefs(); return showCard(); }
  if (e.target.closest('.grow')) { prefs.cardMin = false; savePrefs(); return showCard(); }
  if (e.target.closest('.x')) { selected = null; showCard(); }
  if (e.target.closest('.goin') && selected) { const n = selected; selected = null; drill(n); }
});

const tip = $('#tip');
tip.classList.add('float', 'info');
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();
let hovered = null;
let pointer = null;
let hoverPending = false;

function setHover(node) {
  hovered = node;
  const b = node && layout && layout.bounds.get(node);
  hi.visible = !!b;
  if (b) {
    hi.position.set(b.cx, b.y0, b.cz);
    hi.rotation.set(0, b.rot || 0, 0);
    hi.scale.set(b.w * 1.02, Math.max(b.y1 - b.y0, 0.2) * 1.02, b.d * 1.02);
  }
  dirty = true;
}

function showTip(item) {
  if (!item || !pointer || pointer.pointerType !== 'mouse') { tip.hidden = true; return; }
  const node = item.node;
  if (tip.node !== node) {
    const target = tapTarget(item);
    const go = canDrill(target) && target !== root ? `<div class="go">click to go inside ${esc(target.n)}</div>` : '';
    tip.innerHTML = infoHTML(node) + go;
    tip.node = node;
  }
  tip.hidden = false;
  const st = $('#stage').getBoundingClientRect();
  let x = pointer.clientX - st.left + 16, y = pointer.clientY - st.top + 16;
  if (x + tip.offsetWidth > st.width - 8) x = pointer.clientX - st.left - tip.offsetWidth - 16;
  if (y + tip.offsetHeight > st.height - 8) y = Math.max(8, st.height - tip.offsetHeight - 8);
  tip.style.left = x + 'px';
  tip.style.top = y + 'px';
}

// what a tap opens: EPCOT opens the village, Circle opens the platform you
// hit, and City goes one level in toward the building you tapped (in City the
// buildings are files several folders deep, so opening the tapped thing
// itself would never move you anywhere)
function tapTarget(item) {
  if (item.go) return item.go;
  if (view === 'circle') return item.node;
  let n = item.node;
  while (n && parentOf(n) && parentOf(n) !== root) n = parentOf(n);
  return n && parentOf(n) === root ? n : item.node;
}

function pick() {
  const r = canvas.getBoundingClientRect();
  mouse.set(((pointer.clientX - r.left) / r.width) * 2 - 1, -((pointer.clientY - r.top) / r.height) * 2 + 1);
  raycaster.setFromCamera(mouse, camera);
  const hit = raycaster.intersectObjects(pickables, false)[0];
  const item = hit && hit.object.userData.items[hit.instanceId];
  return item && item.node ? item : null;
}

canvas.addEventListener('pointermove', (e) => { pointer = e; hoverPending = true; });
canvas.addEventListener('pointerleave', () => { pointer = null; setHover(null); showTip(null); });

let down = null;
let quietUntil = 0;
canvas.addEventListener('pointerdown', (e) => { down = { x: e.clientX, y: e.clientY, t: performance.now() }; });
canvas.addEventListener('pointerup', (e) => {
  const tap = down && Math.hypot(e.clientX - down.x, e.clientY - down.y) < 8 && performance.now() - down.t < 600;
  down = null;
  if (!tap || tween || performance.now() < quietUntil) return;
  pointer = e;
  const item = pick();
  if (!item) return;
  const target = tapTarget(item);
  if (canDrill(target) && target !== root) { selected = null; drill(target); return; }
  selected = item.node === root ? null : item.node;
  showCard();
  focus(item.node);
});

// ---------- camera moves ----------

let tween = null;
let dirty = true;

function overview() {
  const k = layout ? layout.extent / (W / 2) : 1;
  return { pos: BASE_VIEW.pos.clone().multiplyScalar(k), target: BASE_VIEW.target.clone().multiplyScalar(k) };
}

function flyTo(pos, target, ms, done) {
  tween = { p0: camera.position.clone(), t0: controls.target.clone(), p1: pos.clone(), t1: target.clone(), start: performance.now(), ms, done };
  controls.enabled = false;
}

function stepTween(now) {
  if (!tween) return false;
  const k = Math.min(1, (now - tween.start) / tween.ms);
  const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
  camera.position.lerpVectors(tween.p0, tween.p1, e);
  controls.target.lerpVectors(tween.t0, tween.t1, e);
  if (k >= 1) {
    const done = tween.done;
    tween = null;
    controls.enabled = true;
    if (done) done();
  }
  return true;
}

function closeUp(b) {
  const tgt = new THREE.Vector3(b.cx, (b.y0 + b.y1) / 2, b.cz);
  const dir = camera.position.clone().sub(controls.target).normalize();
  return [tgt.clone().add(dir.multiplyScalar(Math.max(b.w, b.d, b.y1 - b.y0) * 1.8 + 6)), tgt];
}

function focus(node) {
  const b = layout.bounds.get(node);
  if (!b) return;
  const [pos, tgt] = closeUp(b);
  flyTo(pos, tgt, 650);
}

function drill(node) {
  quietUntil = performance.now() + 900;
  const b = layout.bounds.get(node);
  if (!b) return setRoot(node);
  const [pos, tgt] = closeUp(b);
  flyTo(pos, tgt, 420, () => setRoot(node));
}

// going down: dive in from close above; going up: start at the folder we came from
function setRoot(node, cameFrom) {
  root = node;
  selected = null;
  rebuild();
  crumbs();
  showCard();
  $('#hint').textContent = root.p ? 'Tap a block to go inside · Back goes up' : 'Tap or click a block to go inside';
  $('#hint').classList.toggle('quiet', !!root.p);
  const ov = overview();
  const b = cameFrom && layout.bounds.get(cameFrom);
  if (b) {
    const [pos, tgt] = closeUp(b);
    camera.position.copy(pos);
    controls.target.copy(tgt);
  } else {
    camera.position.copy(ov.pos).multiplyScalar(0.3);
    controls.target.copy(ov.target);
  }
  flyTo(ov.pos, ov.target, 700);
}

function goUp(to) {
  const up = parentOf(root);
  if (!up) return;
  to = to || up;
  let from = root;
  while (parentOf(from) && parentOf(from) !== to) from = parentOf(from);
  setRoot(to, from);
}

function crumbs() {
  if (listOn) showList();
  const chain = [];
  for (let n = root; n; n = parentOf(n)) chain.unshift(n);
  const nav = $('#crumbs');
  nav.classList.add('float');
  nav.replaceChildren();
  chain.forEach((n, i) => {
    if (i) { const s = document.createElement('span'); s.className = 'sep'; s.textContent = '›'; nav.append(s); }
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = n.n;
    b.disabled = n === root;
    b.onclick = () => goUp(n);
    nav.append(b);
  });
  $('#back').hidden = !parentOf(root);
}
$('#back').addEventListener('click', () => goUp());

// ---------- keyboard flying ----------

const keys = new Set();
const FLY = new Set(['w', 'a', 's', 'd', 'q', 'e', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright', 'shift']);
addEventListener('keydown', (e) => {
  if (e.target.closest && e.target.closest('input, textarea, select')) return;
  const k = e.key.toLowerCase();
  if (k === 'escape' || k === 'backspace') { e.preventDefault(); if (selected) { selected = null; showCard(); } else goUp(); return; }
  if (FLY.has(k) && !e.ctrlKey && !e.metaKey && !e.altKey) { keys.add(k); if (k.startsWith('arrow')) e.preventDefault(); }
});
addEventListener('keyup', (e) => keys.delete(e.key.toLowerCase()));
addEventListener('blur', () => keys.clear());

const fwd = new THREE.Vector3(), right = new THREE.Vector3(), move = new THREE.Vector3();
function stepKeys(dt) {
  if (!keys.size || tween) return false;
  camera.getWorldDirection(fwd);
  fwd.y = 0;
  fwd.normalize();
  right.crossVectors(fwd, camera.up).normalize();
  move.set(0, 0, 0);
  if (keys.has('w') || keys.has('arrowup')) move.add(fwd);
  if (keys.has('s') || keys.has('arrowdown')) move.sub(fwd);
  if (keys.has('d') || keys.has('arrowright')) move.add(right);
  if (keys.has('a') || keys.has('arrowleft')) move.sub(right);
  if (keys.has('e')) move.y += 1;
  if (keys.has('q')) move.y -= 1;
  if (!move.lengthSq()) return false;
  const dist = camera.position.distanceTo(controls.target);
  move.normalize().multiplyScalar(Math.max(dist, 30) * 0.9 * dt * (keys.has('shift') ? 3 : 1));
  if (controls.target.y + move.y < 0) move.y = -controls.target.y;
  camera.position.add(move);
  controls.target.add(move);
  return true;
}

// ---------- loop ----------

function resize() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  const pr = renderer.getPixelRatio();
  if (canvas.width !== Math.floor(w * pr) || canvas.height !== Math.floor(h * pr)) {
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    dirty = true;
  }
}

let last = performance.now();
function frame(now) {
  requestAnimationFrame(frame);
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  resize();
  let moved = stepTween(now);
  if (stepKeys(dt)) moved = true;
  if (controls.update()) moved = true;
  if (stepPackets(now)) moved = true;
  if (hoverPending && pointer && !tween) {
    hoverPending = false;
    const item = pick();
    const node = item ? item.node : null;
    if (node !== hovered) setHover(node);
    showTip(item);
  }
  if (moved || dirty) {
    renderer.render(scene, camera);
    drawLabels();
    dirty = false;
  }
}

// ---------- table ----------

function cell(v, unknown, off) {
  if (unknown || v == null) return '<td class="none">—</td>';
  return `<td class="${v ? '' : 'zero'}${off ? ' off' : ''}">${fmt(v)}</td>`;
}

function miniBar(node, fs) {
  const total = fs ? fs.total : node.s;
  if (!total) return '';
  const segs = node.c.map((v, i) => [`var(--cat-${i})`, v]);
  const unseen = node.k.find((k) => k.kind === 'unseen');
  if (unseen) segs.push(['var(--unseen)', unseen.s]);
  return `<span class="mb" title="what fills the drive">${segs.filter((s) => s[1] > 0).map(([col, v]) => `<i style="width:${(100 * v) / total}%;background:${col}"></i>`).join('')}</span>`;
}

function spark(points) {
  if (!points || points.length < 2) return '';
  const t0 = points[0][0], t1 = points[points.length - 1][0] || t0 + 1;
  const lo = Math.min(...points.map((p) => p[1])), hi = Math.max(...points.map((p) => p[1]));
  const xy = points.map(([t, u]) => `${(((t - t0) / Math.max(1, t1 - t0)) * 54).toFixed(1)},${(13 - ((u - lo) / Math.max(1, hi - lo)) * 12).toFixed(1)}`);
  const grew = points[points.length - 1][1] - points[0][1];
  const days = Math.max(1, Math.round((t1 - t0) / 86400));
  return `<svg viewBox="0 0 54 14" role="img"><title>${grew >= 0 ? '+' : '−'}${fmt(Math.abs(grew))} in ${days} days</title><polyline points="${xy.join(' ')}"/></svg>`;
}

function devState(dev) {
  const live = dev.drives.filter((d) => d.source === 'live');
  const snap = dev.drives.find((d) => d.source === 'snapshot');
  if (dev.status && !dev.status.ok && !live.length) return `<span class="state bad" title="${esc(dev.status.msg)}">can't reach · ${snap ? 'snapshot' : 'no data'}</span>`;
  if (live.length === dev.drives.length) return `<span class="state live">live · ${clock(live[0].when)}</span>`;
  if (live.length) return '<span class="state live">live + snapshot</span>';
  if (snap) return `<span class="state">snapshot · ${clock(snap.when)}</span>`;
  return '<span class="state">no data yet</span>';
}

function table(data) {
  const rows = [];
  const tot = { used: 0, free: 0, total: 0, c: zero() };
  const limit = data.alert_percent || 90;
  data.devices.forEach((dev, di) => {
    const note = dev.note ? `<small>${esc(dev.note)}</small>` : '';
    rows.push(`<tr class="dev"><td colspan="9">${esc(dev.name)}${note}${devState(dev)}</td></tr>`);
    dev.drives.forEach((dr, ri) => {
      const node = dr.node;
      const fs = dr.fs;
      const unknown = !sum(node.c);
      const pct = fs && fs.total ? (100 * fs.used) / fs.total : 0;
      const alert = pct >= limit ? `<span class="alert" title="at or above the ${limit}% alert line">⚠ ${Math.round(pct)}% full</span>` : '';
      const why = dr.why ? ` (live scan: ${dr.why})` : '';
      const title = `${dr.path} · ${dr.source === 'live' ? 'live scan' : dr.source === 'snapshot' ? 'snapshot' : 'no data'} ${clock(dr.when)}${why}`;
      const h = dr.health;
      const dot = failing(node) ? '<i class="fail" title="SMART says this drive is failing">●</i>' : '';
      const hl = showHealth && h ? `<span class="hl ${h.state}"><b>${HEALTH_LABEL[h.state] || h.state}</b>${h.temp != null ? ` ${h.temp}°C` : ''}${h.hours != null ? ` · ${h.hours.toLocaleString()} h` : ''}${(h.notes || [])[0] ? ` · ${esc(h.notes[0])}` : h.note ? ` · ${esc(h.note)}` : ''}</span>` : '';
      rows.push(`<tr class="drv" data-d="${di}" data-r="${ri}" title="${esc(title)}"><td class="name">${dot}${esc(dr.name)}${alert}`
        + `<span class="sub">${miniBar(node, fs)}${spark(history[dev.id + '/' + dr.name])}</span>${hl}</td>`
        + cell(fs && fs.used) + cell(fs && fs.free) + cell(fs && fs.total)
        + node.c.map((v, i) => cell(v, unknown, !catsOn[i])).join('') + '</tr>');
      if (fs) { tot.used += fs.used; tot.free += fs.free; tot.total += fs.total; }
      for (let i = 0; i < 5; i++) tot.c[i] += node.c[i];
    });
  });
  $('#drives tbody').innerHTML = rows.join('');
  const shown = allOn() ? '' : ` <small>showing ${fmt(tot.c.reduce((a, v, i) => a + (catsOn[i] ? v : 0), 0))}</small>`;
  $('#drives tfoot').innerHTML = `<tr><td class="name">All${shown}</td>${cell(tot.used)}${cell(tot.free)}${cell(tot.total)}${tot.c.map((v, i) => cell(v, false, !catsOn[i])).join('')}</tr>`;
  document.querySelectorAll('#drives thead th[data-cat]').forEach((th) => th.classList.toggle('off', !catsOn[th.dataset.cat]));
  $('#updated').textContent = 'updated ' + clock(data.generated);
}

function rowNode(tr) {
  return tr && api.devices[tr.dataset.d].drives[tr.dataset.r].node;
}
const tbody = $('#drives tbody');
tbody.addEventListener('click', (e) => {
  const node = rowNode(e.target.closest('tr.drv'));
  if (!node || node === root) return;
  if (layout.bounds.has(node)) drill(node); else setRoot(node);
});
tbody.addEventListener('mouseover', (e) => {
  const node = rowNode(e.target.closest('tr.drv'));
  if (node && layout.bounds.has(node)) setHover(node);
});
tbody.addEventListener('mouseleave', () => setHover(null));

// ---------- alert banner ----------
// full drives always; failing drives unless the dot is turned off; health
// warnings only while Health is on. Dismissed ones stay hidden this session.

function dismissed() {
  try { return JSON.parse(sessionStorage.getItem('dc-dismissed')) || []; } catch { return []; }
}

function renderBanner() {
  const box = $('#banner');
  const gone = dismissed();
  const show = (api && api.alerts || []).filter((a) => !gone.includes(a.key + ':' + a.level) && (a.kind !== 'health'
    || (a.level === 'critical' && api.ui.failing_dot !== false) || showHealth));
  box.hidden = !show.length;
  if (!show.length) return;
  box.alerts = show;
  box.className = 'float ' + (show.some((a) => a.level === 'critical') ? 'critical' : 'warning');
  box.innerHTML = `<span class="ic" aria-hidden="true">⚠</span><span class="msgs">${show.slice(0, 3).map((a, i) =>
    `<button type="button" data-a="${i}">${esc(a.msg)}</button>`).join('')}${show.length > 3 ? `<small>and ${show.length - 3} more</small>` : ''}</span>
    <button type="button" class="x" data-dismiss aria-label="Hide these alerts">✕</button>`;
}

$('#banner').addEventListener('click', (e) => {
  const box = $('#banner');
  if (e.target.closest('[data-dismiss]')) {
    try { sessionStorage.setItem('dc-dismissed', JSON.stringify(dismissed().concat(box.alerts.map((a) => a.key + ':' + a.level)))); } catch { /* private mode */ }
    box.hidden = true;
    return;
  }
  const b = e.target.closest('[data-a]');
  if (!b) return;
  const a = box.alerts[b.dataset.a];
  const dev = api.devices.find((d) => d.name === a.device);
  const dr = dev && dev.drives.find((d) => d.name === a.drive);
  if (dr && dr.node) goTo(dr.node);
});

// ---------- search ----------
// every folder and file in every drive, searchable by name; the easter egg
// for "door locks" flies to the biggest file in the whole city

let index = [];
function buildIndex() {
  index = [];
  const walk = (n, trail) => {
    for (const k of n.k) {
      if (k.kind === 'rest' || k.kind === 'unseen') continue;
      index.push({ node: k, key: k.n.toLowerCase(), trail });
      if (k.k.length) walk(k, trail ? trail + ' › ' + k.n : k.n);
    }
  };
  if (all) walk(all, '');
}

function biggestFile() {
  let best = null;
  for (const it of index) if (it.node.kind === 'file' && (!best || it.node.s > best.node.s)) best = it;
  return best;
}

function search(q) {
  q = q.trim().toLowerCase();
  if (!q) return [];
  if (/\bdoor ?locks?\b/.test(q)) {
    const big = biggestFile();
    return big ? [Object.assign({ egg: true }, big)] : [];
  }
  const words = q.split(/\s+/);
  const hits = [];
  for (const it of index) {
    if (!words.every((w) => it.key.includes(w))) continue;
    hits.push([it.key === q ? 0 : it.key.startsWith(words[0]) ? 1 : 2, it]);
  }
  hits.sort((a, b) => a[0] - b[0] || b[1].node.s - a[1].node.s);
  return hits.slice(0, 40).map((h) => h[1]);
}

// show the node inside its own folder, then fly the camera to it
function goTo(node) {
  if (!allOn() && !eff(node)) setCats([true, true, true, true, true]);
  const parent = node.p || all;
  root = parent;
  rebuild();
  crumbs();
  const b = layout.bounds.get(node);
  selected = node;
  showCard();
  if (!b) return flyTo(overview().pos, overview().target, 700);
  const ov = overview();
  camera.position.copy(ov.pos).multiplyScalar(0.6);
  controls.target.copy(ov.target);
  const [pos, tgt] = closeUp(b);
  flyTo(pos, tgt, 1100, () => setHover(node));
}

const findBox = $('#find');
const results = $('#results');
let found = [];
let active = 0;

function showResults() {
  results.hidden = !found.length && !findBox.value.trim();
  if (results.hidden) return;
  if (!found.length) { results.innerHTML = '<li class="none">Nothing by that name.</li>'; return; }
  results.innerHTML = found.map((it, i) => {
    const n = it.node;
    const dot = n.kind === 'file' || n.kind === 'dir' ? `<i class="dot" style="background:var(--cat-${Math.min(dominant(n), 4)})"></i>` : '<i class="dot" style="background:var(--text2)"></i>';
    const name = it.egg ? 'the file that resets the door locks' : esc(n.n);
    return `<li role="option" data-i="${i}" aria-selected="${i === active}">${dot}<span class="nm">${name}<small>${esc(it.egg ? n.n : it.trail || (n.kind === 'device' ? 'device' : ''))}</small></span><b>${fmt(n.s)}</b></li>`;
  }).join('');
}

let findTimer = null;
findBox.addEventListener('input', () => {
  clearTimeout(findTimer);
  findTimer = setTimeout(() => { found = search(findBox.value); active = 0; showResults(); }, 120);
});
findBox.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    active = Math.max(0, Math.min(found.length - 1, active + (e.key === 'ArrowDown' ? 1 : -1)));
    showResults();
    results.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
  } else if (e.key === 'Enter' && found[active]) {
    pickResult(active);
  } else if (e.key === 'Escape') {
    findBox.value = ''; found = []; showResults(); findBox.blur();
  }
});
results.addEventListener('mousedown', (e) => {
  const li = e.target.closest('[data-i]');
  if (li) { e.preventDefault(); pickResult(+li.dataset.i); }
});
findBox.addEventListener('blur', () => setTimeout(() => { results.hidden = true; }, 150));
findBox.addEventListener('focus', showResults);
function pickResult(i) {
  const it = found[i];
  results.hidden = true;
  findBox.blur();
  if (listOn) { listOn = false; showList(); }
  goTo(it.node);
}
addEventListener('keydown', (e) => {
  if (e.key === '/' && !(e.target.closest && e.target.closest('input, textarea, select'))) { e.preventDefault(); findBox.focus(); }
});

// ---------- list view: the plain, fast way to see what's eating space ----------

let listOn = false;
let listMode = 'here';
let listSort = 'size';

function filesBelow(node, limit) {
  const out = [];
  const walk = (n) => { for (const k of n.k) { if (k.kind === 'file') out.push(k); else if (k.k.length) walk(k); } };
  walk(node);
  return out.sort((a, b) => eff(b) - eff(a)).slice(0, limit);
}

function showList() {
  const box = $('#list');
  box.hidden = !listOn;
  $('#listBtn').setAttribute('aria-pressed', String(listOn));
  if (!listOn || !root) return;
  let rows = listMode === 'here' ? root.k.filter((k) => eff(k) > 0) : filesBelow(root, 300);
  const by = { size: (a, b) => eff(b) - eff(a), name: (a, b) => collator.compare(a.n, b.n), newest: (a, b) => (b.m || 0) - (a.m || 0) }[listSort];
  rows = rows.slice().sort(by).slice(0, 600);
  const total = Math.max(1, ...rows.map(eff));
  const th = (k, label) => `<th data-sort="${k}" aria-sort="${listSort === k ? 'descending' : 'none'}">${label}</th>`;
  box.innerHTML = `<header><div class="seg float" role="group" aria-label="List">
      <button type="button" data-mode="here" aria-pressed="${listMode === 'here'}">In ${esc(root.n)}</button>
      <button type="button" data-mode="files" aria-pressed="${listMode === 'files'}">Biggest files below here</button></div>
      <button type="button" class="x" data-close aria-label="Close the list">✕</button></header>
    <div class="scroll"><table><thead><tr>${th('name', 'Name')}${th('size', 'Size')}<th></th><th>Files</th>${th('newest', 'Newest')}</tr></thead><tbody>
    ${rows.map((n, i) => `<tr data-i="${i}" class="${canDrill(n) ? 'dir' : ''}"><td class="name"><i class="dot" style="background:${n.kind === 'unseen' ? 'var(--unseen)' : `var(--cat-${Math.min(dominant(n), 4)})`}"></i>${esc(n.n)}${listMode === 'files' ? `<small>${esc(realPath(n))}</small>` : ''}</td>
      <td>${fmt(eff(n))}</td><td class="share"><span><i style="width:${(100 * eff(n)) / total}%"></i></span></td>
      <td>${n.f ? n.f.toLocaleString() : ''}</td><td>${n.m ? new Date(n.m * 1000).toLocaleDateString() : ''}</td></tr>`).join('')}
    </tbody></table>${rows.length ? '' : '<p class="muted">Nothing here.</p>'}</div>`;
  box.rows = rows;
}

$('#list').addEventListener('click', (e) => {
  const t = e.target;
  if (t.closest('[data-close]')) { listOn = false; return showList(); }
  const mode = t.closest('[data-mode]');
  if (mode) { listMode = mode.dataset.mode; return showList(); }
  const sortTh = t.closest('[data-sort]');
  if (sortTh) { listSort = sortTh.dataset.sort; return showList(); }
  const tr = t.closest('tr[data-i]');
  if (!tr) return;
  const n = $('#list').rows[tr.dataset.i];
  if (canDrill(n)) { setRoot(n); showList(); } else { listOn = false; showList(); goTo(n); }
});
$('#listBtn').addEventListener('click', () => { listOn = !listOn; showList(); });

const healthBtn = $('#healthBtn');
healthBtn.setAttribute('aria-pressed', String(showHealth));
healthBtn.addEventListener('click', () => {
  showHealth = prefs.health = !showHealth;
  savePrefs();
  healthBtn.setAttribute('aria-pressed', String(showHealth));
  if (!api) return;
  rebuild();
  table(api);
  renderBanner();
  showCard();
});

// ---------- controls ----------

function markView() {
  document.querySelectorAll('#views button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.view === view)));
}
document.querySelectorAll('#views button').forEach((b) => b.addEventListener('click', () => {
  if (view === b.dataset.view) return;
  view = prefs.view = b.dataset.view;
  savePrefs();
  markView();
  rebuild();
  const ov = overview();
  flyTo(ov.pos, ov.target, 650);
}));
$('#views').classList.add('float');

const sortSel = $('#sort');
sortSel.value = sortBy;
sortSel.closest('label').classList.add('float');
sortSel.addEventListener('change', () => {
  sortBy = prefs.sort = sortSel.value;
  savePrefs();
  // a group made by the old sort no longer exists; step out to its folder
  while (root.kind === 'group' || root.kind === 'loose') root = parentOf(root);
  rebuild();
  crumbs();
  showCard();
});

const freeBox = $('#showFree');
freeBox.checked = showFree;
freeBox.closest('label').classList.add('float');
freeBox.addEventListener('change', () => { showFree = prefs.free = freeBox.checked; savePrefs(); rebuild(); });

const themeBox = $('#themes');
themeBox.classList.add('float');
for (const name of THEMES) {
  const b = document.createElement('button');
  b.type = 'button';
  b.dataset.theme = name;
  b.title = name;
  b.setAttribute('aria-label', name + ' theme');
  themeBox.append(b);
}
// each swatch shows its own theme's background and accent
const probe = document.createElement('div');
probe.hidden = true;
document.body.append(probe);
themeBox.querySelectorAll('button').forEach((b) => {
  probe.setAttribute('data-theme', b.dataset.theme);
  const cs = getComputedStyle(probe);
  b.style.background = cs.getPropertyValue('--bg');
  b.style.setProperty('--sw-accent', cs.getPropertyValue('--accent'));
  b.addEventListener('click', () => setTheme(b.dataset.theme));
});
probe.remove();

// the legend doubles as the category filter: tap a chip to hide or show that
// category everywhere, double-tap to show only it
function legend() {
  const box = $('#legend');
  if (prefs.legendOff) { box.innerHTML = '<button type="button" class="lg" title="Show the color key">colors</button>'; return; }
  const chips = CAT_NAMES.map((c, i) => `<button type="button" class="chip" data-cat="${i}" aria-pressed="${catsOn[i]}"
      title="Tap to hide or show ${c}. Double-tap for only ${c}."><i class="dot" style="background:var(--cat-${i})"></i>${c}</button>`).join('');
  box.innerHTML = chips + (allOn()
    ? '<span><i class="dot" style="background:var(--free)"></i>Free space</span><span><i class="dot" style="background:var(--unseen)"></i>Not seen by the scan</span>'
    : '<button type="button" class="chip all" data-cat="all">Show all</button>')
    + '<button type="button" class="lg" title="Hide the color key">hide</button>';
}

function setCats(next) {
  catsOn = next.some(Boolean) ? next : [true, true, true, true, true];
  prefs.cats = catsOn;
  savePrefs();
  legend();
  if (!all) return;
  while (root.kind === 'group' && !lsz(root)) root = parentOf(root);
  rebuild();
  table(api);
  showCard();
}

let chipTimer = null;
$('#legend').addEventListener('click', (e) => {
  if (e.target.closest('.lg')) { prefs.legendOff = !prefs.legendOff; savePrefs(); return legend(); }
  const chip = e.target.closest('[data-cat]');
  if (!chip) return;
  if (chip.dataset.cat === 'all') return setCats([true, true, true, true, true]);
  const i = +chip.dataset.cat;
  // wait a moment so a double-tap can mean "only this one"
  clearTimeout(chipTimer);
  chipTimer = setTimeout(() => { const next = catsOn.slice(); next[i] = !next[i]; setCats(next); }, 230);
});
$('#legend').addEventListener('dblclick', (e) => {
  const chip = e.target.closest('[data-cat]');
  if (!chip || chip.dataset.cat === 'all') return;
  clearTimeout(chipTimer);
  setCats(CAT_NAMES.map((_, i) => i === +chip.dataset.cat));
});

function setPane(open) {
  document.body.classList.toggle('pane-closed', !open);
  const t = $('#paneToggle');
  t.setAttribute('aria-expanded', String(open));
  t.querySelector('.sr').textContent = open ? 'Hide the drive table' : 'Show the drive table';
  t.title = open ? 'Hide the table' : 'Show the table';
  prefs.pane = open;
  savePrefs();
}
$('#paneToggle').addEventListener('click', () => setPane(document.body.classList.contains('pane-closed')));

// ---------- scans ----------

const scanBtn = $('#scanNow');
async function pollScan() {
  const st = await request('/api/status');
  scanBtn.hidden = st.demo;
  scanBtn.disabled = st.running;
  scanBtn.textContent = st.running ? 'scanning…' : 'Scan now';
  if (st.running) setTimeout(() => pollScan().then(() => scanBtn.disabled || load()).catch(() => {}), 5000);
}
scanBtn.addEventListener('click', async () => {
  scanBtn.disabled = true;
  await request('/api/scan', {}).catch(() => {});
  pollScan().catch(() => {});
});

// ---------- load ----------

let me = null;
async function load() {
  const res = await fetch('/api/city');
  if (res.status === 401) return;
  const text = await res.text();
  if (text === lastText) return;
  lastText = text;
  api = JSON.parse(text);
  units = (api.ui && api.ui.units) || 'binary';
  $('#healthBtn').hidden = !(api.ui && api.ui.health);
  if (!prefs.view && api.ui && VIEWS.includes(api.ui.view) && view !== api.ui.view) { view = api.ui.view; markView(); }
  try { history = await (await fetch('/api/history?days=90')).json(); } catch { history = {}; }
  const path = [];
  for (let n = root; n && parentOf(n); n = parentOf(n)) path.unshift(n.n);
  all = prep(api);
  root = all;
  for (const name of path) {
    const next = orderKids(root).find((k) => k.n === name) || root.k.find((k) => k.n === name);
    if (!next) break;
    root = next;
  }
  table(api);
  renderBanner();
  buildIndex();
  rebuild();
  crumbs();
  showCard();
  const empty = !api.devices.length;
  $('#msg').hidden = !empty;
  if (empty) $('#msg').innerHTML = '<p>No devices yet.<br><button type="button" class="primary" data-open-settings>Set up your first device</button></p>';
}

async function start() {
  me = await request('/api/me');
  $('#gear').hidden = false;
  if (me.auth !== 'off' && !me.admin) { $('#msg').textContent = 'Log in to see your drives.'; return showLogin(me); }
  await load();
  if (me.setup || location.hash === '#setup') openWizard(me);
}
document.addEventListener('dc:changed', () => { lastText = ''; start().catch((e) => { $('#msg').textContent = e.message; }); });
document.addEventListener('click', (e) => { if (e.target.closest('[data-open-settings]')) openWizard(me); });
$('#gear').addEventListener('click', () => openSettings(me));

markView();
legend();
setPane(prefs.pane !== false);
themeScene();
if (!renderer.getContext()) {
  $('#msg').textContent = 'This browser cannot draw 3D (WebGL is off).';
} else {
  start().then(() => {
    const ov = overview();
    camera.position.copy(ov.pos);
    controls.target.copy(ov.target);
  }).catch((e) => { $('#msg').textContent = "Can't reach the hub: " + e.message; });
  pollScan().catch(() => {});
  setInterval(() => load().catch(() => {}), 5 * 60 * 1000);
  requestAnimationFrame(frame);
}
