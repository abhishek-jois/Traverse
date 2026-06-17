"""Self-contained interactive HTML graph viewer (vis-network).

Produces a single ``graph.html`` you can open in any browser to *see the graph
structure*: nodes coloured by file type and sized by connectivity, edges whose
thickness encodes the 1-10 dependency weight, a searchable side panel with each
file's metadata, and optional highlighting of a query's selected files.

The vis-network library is inlined when available (fully offline); otherwise a
CDN ``<script>`` tag is used as a fallback.
"""

from __future__ import annotations

import json
import os
import urllib.request

import networkx as nx

_VENDOR = os.path.join(os.path.dirname(__file__), "templates", "vis-network.min.js")
_CDN = "https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"

# Colour per semantic file type (matches the legend).
TYPE_COLORS = {
    "controller": "#e74c3c",
    "route": "#e67e22",
    "model": "#2980b9",
    "schema": "#16a085",
    "middleware": "#8e44ad",
    "service": "#2ecc71",
    "repository": "#27ae60",
    "component": "#3498db",
    "view": "#9b59b6",
    "hook": "#1abc9c",
    "util": "#7f8c8d",
    "auth": "#c0392b",
    "database": "#34495e",
    "api": "#d35400",
    "client": "#f39c12",
    "types": "#95a5a6",
    "state": "#f1c40f",
    "config": "#e84393",
    "test": "#bdc3c7",
    "entrypoint": "#000000",
    "doc": "#dfe6e9",
    "other": "#b2bec3",
}


def _vis_lib_tag() -> str:
    """Inline the vis-network bundle, downloading + vendoring it once if needed."""
    if not os.path.exists(_VENDOR):
        try:
            os.makedirs(os.path.dirname(_VENDOR), exist_ok=True)
            with urllib.request.urlopen(_CDN, timeout=15) as resp:
                data = resp.read().decode("utf-8")
            with open(_VENDOR, "w", encoding="utf-8") as fh:
                fh.write(data)
        except Exception:
            return f'<script src="{_CDN}"></script>'
    try:
        with open(_VENDOR, "r", encoding="utf-8") as fh:
            return f"<script>{fh.read()}</script>"
    except OSError:
        return f'<script src="{_CDN}"></script>'


def _build_dataset(g: nx.DiGraph, highlight: set[str] | None) -> tuple[list, list, list]:
    highlight = highlight or set()
    degrees = [g.nodes[n].get("degree", 0) for n in g.nodes] or [0]
    max_deg = max(degrees) or 1

    nodes = []
    for n in g.nodes:
        d = g.nodes[n]
        ftype = d.get("file_type", "other")
        deg = d.get("degree", 0)
        is_hl = n in highlight
        nodes.append({
            "id": n,
            "label": os.path.basename(n),
            "group": ftype,
            "value": 1 + deg,
            "color": {
                "background": TYPE_COLORS.get(ftype, "#b2bec3"),
                "border": "#f1c40f" if is_hl else "#2d3436",
            },
            "borderWidth": 4 if is_hl else 1,
            "title": f"{n}\n{d.get('description', '')}",
            "meta": {
                "path": n,
                "type": ftype,
                "description": d.get("description", ""),
                "language": d.get("language", ""),
                "mtime": d.get("mtime", 0),
                "size": d.get("size", 0),
                "degree": deg,
                "always_include": d.get("always_include", False),
                "symbols": d.get("symbols", []),
            },
        })

    edges = []
    for u, v, e in g.edges(data=True):
        w = e.get("weight", 1)
        strong = w >= 7
        edges.append({
            "from": u,
            "to": v,
            "value": w,
            "title": f"{', '.join(e.get('relations', []))} (w={w}, {e.get('confidence','')})",
            "color": {"color": "#2d3436" if strong else "#b2bec3",
                      "opacity": 0.9 if strong else 0.5},
            "arrows": "to",
        })

    legend = sorted({g.nodes[n].get("file_type", "other") for n in g.nodes})
    legend_items = [{"type": t, "color": TYPE_COLORS.get(t, "#b2bec3")} for t in legend]
    return nodes, edges, legend_items


def export_html(g: nx.DiGraph, out_path: str, *,
                title: str = "Dependency Graph Retrieval",
                highlight: set[str] | None = None,
                query: str | None = None) -> str:
    nodes, edges, legend = _build_dataset(g, highlight)
    payload = json.dumps({"nodes": nodes, "edges": edges, "legend": legend})
    lib_tag = _vis_lib_tag()
    subtitle = f"Query: {query}" if query else "Every file visible · only what matters loaded"

    html = _TEMPLATE.replace("__LIB__", lib_tag) \
                    .replace("__DATA__", payload) \
                    .replace("__TITLE__", _esc(title)) \
                    .replace("__SUBTITLE__", _esc(subtitle))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out_path


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>__TITLE__</title>
__LIB__
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; font-family: -apple-system, Segoe UI, Roboto, sans-serif; }
  #app { display: flex; flex-direction: column; height: 100%; }
  header { background: #1e3a5f; color: #fff; padding: 8px 16px; }
  header h1 { margin: 0; font-size: 15px; font-weight: 600; }
  header p { margin: 2px 0 0; font-size: 11px; opacity: .8; }
  #main { flex: 1; display: flex; min-height: 0; }
  #netwrap { flex: 1; position: relative; min-width: 0; }
  #net { width: 100%; height: 100%; background: #fafbfc; }
  #side { width: 360px; border-left: 1px solid #e1e4e8; padding: 14px; overflow-y: auto; background: #fff; flex-shrink: 0; }
  #side h2 { font-size: 12px; text-transform: uppercase; color: #57606a; margin: 14px 0 6px; }
  #side h2:first-child { margin-top: 0; }
  .row { font-size: 13px; margin: 5px 0; word-break: break-all; }
  .row b { color: #24292f; }
  .pill { display: inline-block; padding: 1px 8px; border-radius: 10px; color: #fff; font-size: 11px; }
  .toolbar { display: flex; gap: 8px; align-items: center; padding: 8px 16px; border-bottom: 1px solid #e1e4e8; background: #fff; flex-wrap: wrap; }
  .toolbar input { padding: 7px 10px; border: 1px solid #d0d7de; border-radius: 6px; font-size: 13px; }
  #query { flex: 2; min-width: 200px; border-color: #1e3a5f; }
  #search { flex: 1; min-width: 120px; }
  .toolbar button { padding: 7px 12px; border: 1px solid #d0d7de; background: #f6f8fa; border-radius: 6px; cursor: pointer; font-size: 13px; white-space: nowrap; }
  .toolbar button:hover { background: #eaeef2; }
  #ask { background: #1e3a5f; color: #fff; border-color: #1e3a5f; }
  #ask:hover { background: #2c5282; }
  #legend { display: flex; flex-wrap: wrap; gap: 5px 12px; }
  #legend span { font-size: 11px; color: #444; display: flex; align-items: center; gap: 5px; }
  .dot { width: 11px; height: 11px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
  .muted { color: #8b949e; font-size: 12px; }
  .nbr { font-size: 12px; cursor: pointer; color: #0969da; word-break: break-all; }
  .nbr:hover { text-decoration: underline; }
  .result { border: 1px solid #e1e4e8; border-radius: 8px; padding: 8px 10px; margin: 8px 0; cursor: pointer; }
  .result:hover { background: #f6f8fa; }
  .result .path { font-size: 12px; font-weight: 600; color: #0969da; word-break: break-all; }
  .result .why { font-size: 11px; color: #57606a; margin-top: 3px; word-break: break-all; }
  .savings { background: #dafbe1; border: 1px solid #aceebb; border-radius: 8px; padding: 8px 10px; font-size: 12px; color: #116329; margin: 8px 0; }
  #loading { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; background: #fafbfcee; z-index: 5; gap: 10px; }
  #loadbar { width: 240px; height: 8px; background: #e1e4e8; border-radius: 4px; overflow: hidden; }
  #loadbar > div { height: 100%; width: 0; background: #1e3a5f; transition: width .2s; }
  @media (max-width: 820px) {
    #main { flex-direction: column; }
    #side { width: 100%; height: 45%; border-left: none; border-top: 1px solid #e1e4e8; }
  }
</style>
</head>
<body>
<div id="app">
  <header>
    <h1>__TITLE__</h1>
    <p>__SUBTITLE__</p>
  </header>
  <div class="toolbar">
    <input id="query" placeholder="Ask the graph: e.g. where is authentication handled?" />
    <button id="ask">Query</button>
    <input id="search" placeholder="Filter by name…" />
    <button id="clear">Clear</button>
    <button id="fit">Fit</button>
    <button id="physics">Physics: on</button>
  </div>
  <div id="main">
    <div id="netwrap">
      <div id="net"></div>
      <div id="loading"><div>Laying out the graph…</div><div id="loadbar"><div></div></div></div>
    </div>
    <div id="side">
      <div id="results"></div>
      <h2>Node details</h2>
      <div id="detail"><p class="muted">Type a question above and hit Query — the graph is
      traversed right here in the browser (metadata scoring + weighted edges) and the chosen
      files light up. Or click any node. Edge thickness = dependency weight (1–10).</p></div>
      <h2>Legend</h2>
      <div id="legend"></div>
    </div>
  </div>
</div>
<script>
const DATA = __DATA__;
const N = DATA.nodes.length;
const BIG = N > 800;

const nodes = new vis.DataSet(DATA.nodes);
const edges = new vis.DataSet(DATA.edges);
const container = document.getElementById('net');
const options = {
  nodes: { shape: 'dot', scaling: { min: 8, max: 40 },
           font: { size: 12, face: 'Inter, sans-serif' } },
  edges: { scaling: { min: 1, max: 10 },
           smooth: BIG ? false : { type: 'continuous' } },
  layout: { improvedLayout: !BIG },
  physics: {
    stabilization: { iterations: BIG ? 250 : 1000, updateInterval: 25 },
    barnesHut: { gravitationalConstant: BIG ? -9000 : -4000,
                 springLength: 130, springConstant: 0.03 },
    maxVelocity: 50, minVelocity: 1,
  },
  interaction: { hover: true, tooltipDelay: 120,
                 hideEdgesOnDrag: BIG, hideEdgesOnZoom: BIG },
};
const network = new vis.Network(container, { nodes, edges }, options);

// Loading overlay with real stabilization progress.
const loading = document.getElementById('loading');
const loadbar = document.querySelector('#loadbar > div');
network.on('stabilizationProgress', p => { loadbar.style.width = (100 * p.iterations / p.total) + '%'; });
let physicsOn = true;
network.once('stabilizationIterationsDone', () => {
  loading.style.display = 'none';
  if (BIG) {  // freeze physics on big graphs so panning stays smooth
    physicsOn = false;
    network.setOptions({ physics: { enabled: false } });
    document.getElementById('physics').textContent = 'Physics: off';
  }
});

// Legend
const legend = document.getElementById('legend');
DATA.legend.forEach(l => {
  const s = document.createElement('span');
  s.innerHTML = `<span class="dot" style="background:${l.color}"></span>${l.type}`;
  legend.appendChild(s);
});

// ------------------------------------------------------------------
// In-browser query engine — a JS port of retrieve.py: metadata scoring,
// weighted propagation with adaptive cutoff, top-K selection.
// ------------------------------------------------------------------
const FIELD_W = { filename: 3.0, symbols: 2.0, description: 1.0, type: 1.0 };
const CUTOFF = N <= 200 ? 0.12 : Math.min(0.28, 0.12 + 0.035 * Math.log2(N / 200));
const FACTOR_CAP = 0.95;
const MAX_FILES = 5;
// Question/grammar words carry no file-selection signal ("what is gym" must
// rank on "gym", not on files whose symbols contain "is").
const STOPWORDS = new Set(('a an and are as at be been being but by can could did do does for from had has ' +
  'have he her here him his how i if in into is it its may me might must my no not ' +
  'of on or our shall she should so than that the their them then there these they ' +
  'this those to us was we were what when where which who why will with would yes ' +
  'you your please show find tell explain about').split(' '));

function tokenize(text) {
  const out = [];
  for (const raw of (text.match(/[A-Za-z0-9]+/g) || [])) {
    out.push(raw.toLowerCase());
    for (const p of (raw.match(/[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])/g) || []))
      out.push(p.toLowerCase());
  }
  return out;
}

const FIELDS = DATA.nodes.map(n => ({
  id: n.id,
  filename: tokenize(n.id),
  symbols: tokenize((n.meta.symbols || []).join(' ')),
  description: tokenize(n.meta.description || ''),
  type: tokenize(n.meta.type || ''),
}));
const DF = {};
for (const f of FIELDS) {
  const seen = new Set([...f.filename, ...f.symbols, ...f.description, ...f.type]);
  for (const t of seen) DF[t] = (DF[t] || 0) + 1;
}
const idf = t => Math.log(1 + N / (1 + (DF[t] || 0)));

// adjacency: id -> [{other, w, rels, dir}]
const ADJ = {};
for (const e of DATA.edges) {
  (ADJ[e.from] = ADJ[e.from] || []).push({ other: e.to, w: e.value, dir: '→' });
  (ADJ[e.to] = ADJ[e.to] || []).push({ other: e.from, w: e.value, dir: '←' });
}

function runQuery(q) {
  const allTerms = new Set(tokenize(q));
  let qTerms = new Set([...allTerms].filter(t => !STOPWORDS.has(t)));
  if (!qTerms.size) qTerms = allTerms;  // query was entirely stopwords
  if (!qTerms.size) return null;

  // 1. metadata-only scoring
  const raw = {};
  for (const f of FIELDS) {
    let s = 0;
    for (const [fname, toks] of [['filename', f.filename], ['symbols', f.symbols],
                                 ['description', f.description], ['type', f.type]]) {
      const counts = {};
      for (const t of toks) counts[t] = (counts[t] || 0) + 1;
      for (const term of qTerms) {
        if (counts[term]) s += FIELD_W[fname] * counts[term] * idf(term);
        else if (fname === 'filename' && Object.keys(counts).some(t => t.includes(term)))
          s += 0.4 * FIELD_W[fname] * idf(term);
      }
    }
    if (s > 0) raw[f.id] = s;
  }
  const ids = Object.keys(raw);
  if (!ids.length) return { selected: [], cutoff: CUTOFF };
  const top = Math.max(...ids.map(i => raw[i]));

  // 2. seeds
  const seeds = ids.map(i => [i, raw[i] / top])
    .sort((a, b) => b[1] - a[1]).slice(0, 8);

  // 3. weighted propagation with cutoff
  const best = {}, why = {};
  const pq = [];
  for (const [id, r] of seeds) { best[id] = r; why[id] = 'direct metadata match'; pq.push([r, id]); }
  while (pq.length) {
    pq.sort((a, b) => a[0] - b[0]);
    const [rel, cur] = pq.pop();
    if (rel < (best[cur] || 0)) continue;
    for (const { other, w, dir } of (ADJ[cur] || [])) {
      const prop = rel * Math.min(FACTOR_CAP, w / 10);
      if (prop < CUTOFF) continue;
      if (prop > (best[other] || 0) + 1e-9) {
        best[other] = prop;
        why[other] = `linked ${dir} ${cur.split('/').pop()} (w=${w})`;
        pq.push([prop, other]);
      }
    }
  }

  // 4. top-K + always-include config neighbours
  let chosen = Object.entries(best).sort((a, b) => b[1] - a[1])
    .slice(0, MAX_FILES).map(([id]) => id);
  const set = new Set(chosen);
  let extra = 0;  // cap config fan-out so it never dwarfs the real selection
  for (const id of [...chosen]) {
    for (const { other } of (ADJ[id] || [])) {
      if (extra >= 3) break;
      const m = nodes.get(other);
      if (m && m.meta.always_include && !set.has(other)) {
        set.add(other); chosen.push(other); extra++;
        why[other] = 'cross-cutting config (always included)';
        best[other] = best[other] || 0;
      }
    }
  }
  return {
    selected: chosen.map(id => ({ id, rel: best[id], why: why[id], meta: nodes.get(id).meta })),
    cutoff: CUTOFF,
  };
}

// ------------------------------------------------------------------
// Highlighting — always batched (one redraw), never per-node updates.
// ------------------------------------------------------------------
function setHighlight(selectedIds) {
  const sel = new Set(selectedIds);
  const updates = DATA.nodes.map(n => sel.size
    ? { id: n.id, opacity: sel.has(n.id) ? 1 : 0.12,
        borderWidth: sel.has(n.id) ? 4 : 1,
        color: { background: n.color.background,
                 border: sel.has(n.id) ? '#f1c40f' : '#2d3436' } }
    : { id: n.id, opacity: 1, borderWidth: 1,
        color: { background: n.color.background, border: '#2d3436' } });
  nodes.update(updates);
  if (sel.size) {
    network.selectNodes([...sel].filter(id => nodes.get(id)));
    network.fit({ nodes: [...sel], animation: true });
  }
}

function showResults(q, res) {
  const box = document.getElementById('results');
  if (!res || !res.selected.length) {
    box.innerHTML = `<h2>Query</h2><p class="muted">No relevant files found for “${esc(q)}”. Try different words.</p>`;
    return;
  }
  const totalTok = DATA.nodes.reduce((a, n) => a + Math.max(1, Math.round(n.meta.size / 4)), 0);
  const selTok = res.selected.reduce((a, s) => a + Math.max(1, Math.round(s.meta.size / 4)), 0);
  const pct = totalTok ? (100 * (1 - selTok / totalTok)).toFixed(1) : '0';
  box.innerHTML = `<h2>Query results</h2>
    <div class="row muted">“${esc(q)}” → ${res.selected.length} of ${N} files (cutoff ${res.cutoff.toFixed(2)})</div>
    ${res.selected.map(s => `
      <div class="result" data-id="${esc(s.id)}">
        <div class="path">${esc(s.id)}</div>
        <div class="why">[${esc(s.meta.type)}] ${esc(s.meta.description || '')}</div>
        <div class="why">why: ${esc(s.why)} · ~${Math.max(1, Math.round(s.meta.size / 4))} tok</div>
      </div>`).join('')}
    <div class="savings">≈ ${selTok.toLocaleString()} tokens loaded instead of ${totalTok.toLocaleString()}
      — <b>${pct}% saved</b></div>`;
  box.querySelectorAll('.result').forEach(el => el.onclick = () => {
    const id = el.dataset.id;
    network.focus(id, { scale: 1.1, animation: true });
    network.selectNodes([id]);
    showDetail(id);
  });
}

function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }

function doQuery() {
  const q = document.getElementById('query').value.trim();
  if (!q) return;
  const res = runQuery(q);
  showResults(q, res);
  setHighlight(res ? res.selected.map(s => s.id) : []);
}
document.getElementById('ask').onclick = doQuery;
document.getElementById('query').addEventListener('keydown', e => { if (e.key === 'Enter') doQuery(); });
document.getElementById('clear').onclick = () => {
  document.getElementById('query').value = '';
  document.getElementById('search').value = '';
  document.getElementById('results').innerHTML = '';
  setHighlight([]);
  network.unselectAll();
};

// ------------------------------------------------------------------
// Node detail panel
// ------------------------------------------------------------------
function fmtTime(t) {
  if (!t) return 'n/a';
  try { return new Date(t * 1000).toLocaleString(); } catch (e) { return 'n/a'; }
}
function fmtSize(b) { return b < 1024 ? b + ' B' : (b / 1024).toFixed(1) + ' KB'; }

function showDetail(id) {
  const n = nodes.get(id); if (!n) return;
  const m = n.meta;
  const color = (n.color && n.color.background) || '#999';
  const rows = network.getConnectedEdges(id).map(eid => {
    const e = edges.get(eid);
    const other = e.from === id ? e.to : e.from;
    const dir = e.from === id ? '→' : '←';
    return `<div class="row"><span class="nbr" data-id="${esc(other)}">${dir} ${esc(other)}</span> <span class="muted">w=${e.value}</span></div>`;
  }).join('');
  document.getElementById('detail').innerHTML = `
    <div class="row"><span class="pill" style="background:${color}">${esc(m.type)}</span></div>
    <div class="row"><b>${esc(m.path)}</b></div>
    <div class="row">${esc(m.description || '') || '<span class="muted">no description</span>'}</div>
    <div class="row muted">${esc(m.language)} · ${fmtSize(m.size)} · ${m.degree} links${m.always_include ? ' · always-include' : ''}</div>
    <div class="row muted">modified: ${fmtTime(m.mtime)}</div>
    <h2 style="margin-top:12px">Connections</h2>${rows || '<p class="muted">none</p>'}`;
  document.querySelectorAll('.nbr').forEach(el =>
    el.onclick = () => { network.selectNodes([el.dataset.id]); network.focus(el.dataset.id, { scale: 1.2, animation: true }); showDetail(el.dataset.id); });
}
network.on('selectNode', p => showDetail(p.nodes[0]));

// ------------------------------------------------------------------
// Filter box — debounced and batched so it stays fast on big graphs.
// ------------------------------------------------------------------
let searchTimer = null;
document.getElementById('search').addEventListener('input', e => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    const q = e.target.value.trim().toLowerCase();
    if (!q) { setHighlight([]); return; }
    const matches = DATA.nodes
      .filter(n => (n.id + ' ' + (n.meta.description || '')).toLowerCase().includes(q))
      .map(n => n.id);
    setHighlight(matches.slice(0, 200));
  }, 250);
});

document.getElementById('fit').onclick = () => network.fit({ animation: true });
document.getElementById('physics').onclick = ev => {
  physicsOn = !physicsOn;
  network.setOptions({ physics: { enabled: physicsOn } });
  ev.target.textContent = 'Physics: ' + (physicsOn ? 'on' : 'off');
};
</script>
</body>
</html>
"""
