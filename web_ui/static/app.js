// ===========================================================================
// Stratus QA — v4 enterprise (Carbon-inspired)
// Hash routes: #/dashboard #/new #/run #/runs #/catalog #/env #/help
// ===========================================================================

const $  = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => Array.from(root.querySelectorAll(s));

const fmt = {
  pct: n => Math.round(n * 100),
  shortTime: ts => {
    if (!ts) return "—";
    const d = new Date(typeof ts === "number" ? ts * 1000 : ts);
    return d.toLocaleString(undefined, {
      year: "2-digit", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: false,
    });
  },
  duration: s => {
    if (s == null) return "—";
    if (s < 60) return Math.round(s) + "s";
    const m = Math.floor(s / 60), sec = Math.round(s % 60);
    return `${m}m ${sec}s`;
  },
  ago: ts => {
    if (!ts) return "—";
    const sec = (Date.now() - new Date(ts).getTime()) / 1000;
    if (sec < 60)    return Math.round(sec) + "s ago";
    if (sec < 3600)  return Math.round(sec / 60) + "m ago";
    if (sec < 86400) return Math.round(sec / 3600) + "h ago";
    return Math.round(sec / 86400) + "d ago";
  },
};

const state = {
  catalog: null,
  history: [],
  profiles: {},
  activeEnv: null,
  selectedScreens: new Set(),
  evtSource: null,
  runStartedAt: null,
  runTimer: null,
  liveCounts: { pass: 0, fail: 0, total: 0 },
};

// ===========================================================================
// Router
// ===========================================================================
function currentRoute() {
  const h = location.hash.replace(/^#\/?/, "") || "dashboard";
  const [path, query = ""] = h.split("?");
  return { path, query: Object.fromEntries(new URLSearchParams(query)) };
}
function navigate(hash) { location.hash = hash; }

const ROUTE_LABEL = {
  dashboard: "Overview",
  new:       "New run",
  run:       "Live run",
  runs:      "Run history",
  catalog:   "Screen catalog",
  converter: "YAML converter",
  env:       "Environments",
  help:      "Mode glossary",
};

function renderView() {
  const { path, query } = currentRoute();
  $$(".view").forEach(v => v.classList.add("hidden"));
  $$(".side-link, .tab").forEach(a => a.classList.remove("active"));
  $$(`.side-link[data-route="${path}"], .tab[data-tab="${path}"]`).forEach(a => a.classList.add("active"));
  const view = $(`.view[data-view="${path}"]`) || $(`.view[data-view="dashboard"]`);
  view.classList.remove("hidden");
  $("#crumb-now").textContent = ROUTE_LABEL[path] || "Overview";
  switch (path) {
    case "dashboard": renderDashboard(); break;
    case "new":       renderNew(query);  break;
    case "run":       renderRun();        break;
    case "runs":      renderHistory();   break;
    case "catalog":   renderCatalog();   break;
    case "converter": renderConverter(); break;
    case "env":       renderEnv();       break;
    case "help":      renderHelp();      break;
  }
}
window.addEventListener("hashchange", renderView);

// ===========================================================================
// Status bar
// ===========================================================================
function updateStatusBar() {
  const now = new Date();
  $("#sb-clock").textContent = now.toISOString().substring(11, 19);
  if (state.catalog) {
    $("#sb-catalog").textContent = (state.catalog.screens || []).length || 0;
    $("#sb-built").textContent = state.catalog.built_at ? fmt.ago(state.catalog.built_at) : "never";
  }
  $("#sb-env").textContent = state.activeEnv || "none";
}
setInterval(updateStatusBar, 1000);

// ===========================================================================
// Env chip
// ===========================================================================
async function refreshEnvChip() {
  await loadProfiles();
  const chip = $("#env-chip");
  const name = $("#env-chip-name");
  if (state.activeEnv && state.profiles[state.activeEnv]) {
    name.textContent = state.activeEnv;
    chip.classList.add("connected");
  } else {
    name.textContent = "No environment";
    chip.classList.remove("connected");
  }
  updateStatusBar();
}
$("#env-chip").addEventListener("click", () => navigate("#/env"));

// ===========================================================================
// Loaders
// ===========================================================================
async function loadCatalog(force=false) {
  if (state.catalog && !force) return state.catalog;
  const r = await fetch("/api/catalog");
  state.catalog = await r.json();
  $("#catalog-count").textContent = (state.catalog.screens || []).length || "—";
  updateStatusBar();
  return state.catalog;
}
async function loadHistory() {
  const r = await fetch("/api/history");
  state.history = await r.json();
  $("#runs-count").textContent = state.history.length || "—";
  return state.history;
}
async function loadProfiles() {
  const r = await fetch("/api/profiles");
  const list = await r.json();
  state.profiles = {};
  (Array.isArray(list) ? list : []).forEach(p => { if (p && p.name) state.profiles[p.name] = p; });
  if (!state.activeEnv && Object.keys(state.profiles).length) {
    state.activeEnv = Object.keys(state.profiles)[0];
  }
}

// ===========================================================================
// View: Dashboard
// ===========================================================================
async function renderDashboard() {
  await Promise.all([loadCatalog(), loadHistory()]);
  const screens = state.catalog.screens || [];
  const history = state.history;
  const recent = history.slice(0, 20).reverse();
  const prev20 = history.slice(20, 40);

  // ---- KPI 1: Catalog screens ----
  $("#k-screens").textContent = screens.length;
  $("#k-screens-meta").innerHTML = state.catalog.built_at
    ? `<span class="muted">Built ${fmt.ago(state.catalog.built_at)}</span>`
    : `<span class="muted">Not yet built</span>`;
  drawTypeBar($("#spark-screens"), screens);

  // ---- KPI 2: Total runs ----
  $("#k-runs").textContent = history.length;
  const today = history.filter(r => {
    const d = new Date(r.ts), n = new Date();
    return d.toDateString() === n.toDateString();
  });
  $("#k-runs-meta").innerHTML = `<span class="muted">${today.length} today</span>`;
  drawSparkline($("#spark-runs"), recent.map(r => 1), "var(--c-info)");

  // ---- KPI 3: Pass rate ----
  const passed = recent.filter(r => r.passed).length;
  const rate = recent.length ? passed / recent.length : 0;
  $("#k-pass").innerHTML = (recent.length ? fmt.pct(rate) : "—") + `<span class="u">%</span>`;
  const prevPass = prev20.length ? prev20.filter(r => r.passed).length / prev20.length : null;
  if (prevPass != null && recent.length) {
    const delta = (rate - prevPass) * 100;
    $("#k-pass-meta").innerHTML = deltaPill(delta);
  } else {
    $("#k-pass-meta").innerHTML = `<span class="muted">${passed} pass · ${recent.length - passed} fail</span>`;
  }
  drawSparkline($("#spark-pass"), recent.map(r => r.passed ? 1 : 0), "var(--c-success)");

  // ---- KPI 4: Avg duration ----
  const times = recent.filter(r => r.duration_s > 0).map(r => r.duration_s);
  const avg = times.length ? times.reduce((a, b) => a + b, 0) / times.length : 0;
  $("#k-time").textContent = fmt.duration(avg);
  const prevTimes = prev20.filter(r => r.duration_s > 0).map(r => r.duration_s);
  if (prevTimes.length && times.length) {
    const prevAvg = prevTimes.reduce((a, b) => a + b, 0) / prevTimes.length;
    const delta = ((avg - prevAvg) / prevAvg) * 100;
    $("#k-time-meta").innerHTML = deltaPill(-delta);
  } else {
    $("#k-time-meta").innerHTML = `<span class="muted">${times.length} timed runs</span>`;
  }
  drawSparkline($("#spark-time"), recent.map(r => r.duration_s || 0), "var(--c-brand)");

  // ---- KPI 5: Coverage ----
  const tested = new Set();
  history.forEach(r => {
    const sn = (r.config || {}).single_screenname;
    if (sn) tested.add(sn);
  });
  const cov = screens.length ? tested.size / screens.length : 0;
  $("#k-cov").innerHTML = fmt.pct(cov) + `<span class="u">%</span>`;
  $("#k-cov-meta").innerHTML = `<span class="muted">${tested.size}/${screens.length} screens</span>`;
  let seen = new Set(), covSeries = [];
  history.slice().reverse().forEach(r => {
    const sn = (r.config || {}).single_screenname;
    if (sn) seen.add(sn);
    covSeries.push(seen.size);
  });
  drawSparkline($("#spark-cov"), covSeries.slice(-20), "var(--c-success)");

  // ---- Pass-rate line chart ----
  drawLineChart($("#chart-passrate"), history.slice(0, 30).reverse());

  // ---- Insights ----
  renderInsights(screens, history, tested);

  // ---- Mode donut ----
  renderModeDonut(history);

  // ---- Coverage by type ----
  renderCoverageByType(screens, tested);

  // ---- Recent runs table ----
  $("#dash-runs-table").innerHTML = renderRunsTable(history.slice(0, 10));
}

function deltaPill(delta) {
  const d = Math.round(delta * 10) / 10;
  if (Math.abs(d) < 0.5) return `<span class="muted">No change</span>`;
  const cls = d > 0 ? "up" : "dn";
  const arrow = d > 0
    ? `<svg viewBox="0 0 12 12" width="10" height="10" fill="currentColor"><polygon points="6,2 11,9 1,9"/></svg>`
    : `<svg viewBox="0 0 12 12" width="10" height="10" fill="currentColor"><polygon points="6,10 11,3 1,3"/></svg>`;
  return `<span class="delta ${cls}">${arrow}${Math.abs(d)}%</span><span class="muted">vs prior</span>`;
}

// ---- Insights ----
function renderInsights(screens, history, tested) {
  const target = $("#insights-list");
  const items = [];
  const untested = screens.length - tested.size;
  if (untested > 0 && screens.length) {
    items.push({ kind: untested > screens.length * 0.5 ? "warn" : "info", icon: "shield",
      title: `${untested.toLocaleString()} screen${untested === 1 ? "" : "s"} never tested`,
      detail: `${Math.round((untested / screens.length) * 100)}% of catalog has no Single-Screen test.`,
      cta: "Browse catalog", href: "#/catalog" });
  }
  let streak = 0;
  for (const r of history) { if (r.passed) streak++; else break; }
  if (streak >= 3) {
    items.push({ kind: "success", icon: "check",
      title: `${streak} runs passing in a row`,
      detail: "All recent runs green. Keep it up.",
      cta: "View runs", href: "#/runs" });
  } else if (streak === 0 && history[0] && !history[0].passed) {
    items.push({ kind: "danger", icon: "alert",
      title: "Latest run failed",
      detail: `${history[0].steps_failed}/${history[0].steps_total} steps failed in the most recent run.`,
      cta: "Open report", href: "/report.html" });
  }
  if (state.catalog.built_at) {
    const days = (Date.now() - new Date(state.catalog.built_at).getTime()) / 86400000;
    if (days > 30) {
      items.push({ kind: "warn", icon: "refresh",
        title: "Catalog is stale",
        detail: `Last built ${Math.round(days)} days ago.`,
        cta: "Rebuild now", href: "#/new?preset=catalog" });
    }
  } else {
    items.push({ kind: "info", icon: "info",
      title: "No catalog yet",
      detail: "Build the catalog to unlock Single / Bulk modes.",
      cta: "Build catalog", href: "#/new?preset=catalog" });
  }
  const bySingle = {};
  history.forEach(r => {
    const sn = (r.config || {}).single_screenname;
    if (sn && r.duration_s > 0) (bySingle[sn] ||= []).push(r.duration_s);
  });
  for (const [sn, times] of Object.entries(bySingle)) {
    if (times.length >= 3) {
      const recent3 = times.slice(0, 3), earlier = times.slice(3, 8);
      if (earlier.length) {
        const r3 = recent3.reduce((a, b) => a + b, 0) / 3;
        const er = earlier.reduce((a, b) => a + b, 0) / earlier.length;
        if (r3 > er * 1.3) {
          items.push({ kind: "warn", icon: "clock",
            title: `${sn} runtime increased`,
            detail: `Recent runs ${Math.round(((r3 - er) / er) * 100)}% slower (${fmt.duration(er)} → ${fmt.duration(r3)}).`,
            cta: "Investigate", href: `#/new?preset=single&screen=${sn}` });
          break;
        }
      }
    }
  }
  if (!items.length) {
    target.innerHTML = `<div class="empty-state"><h3>All clear</h3><p>No issues to surface.</p></div>`;
    return;
  }
  target.innerHTML = items.slice(0, 5).map(it => `
    <div class="insight ${it.kind}">
      <div class="ic">${iIcon(it.icon)}</div>
      <div class="body">
        <div class="t">${it.title}</div>
        <div class="d">${it.detail}</div>
      </div>
      <a class="cta" href="${it.href}">${it.cta} →</a>
    </div>`).join("");
}
function iIcon(name) {
  const m = {
    shield:  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
    check:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>`,
    alert:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
    refresh: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>`,
    info:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`,
    clock:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
  };
  return m[name] || m.info;
}

// ---- Coverage by type bars ----
function renderCoverageByType(screens, tested) {
  const types = ["list", "detail", "report", "wizard", "other"];
  const target = $("#cov-by-type");
  const rows = types.map(t => {
    const all = screens.filter(s => s.type === t);
    const ts = all.filter(s => tested.has(s.screenname));
    return { type: t, total: all.length, tested: ts.length };
  }).filter(r => r.total > 0);
  target.innerHTML = rows.map(r => {
    const pct = r.total ? (r.tested / r.total) * 100 : 0;
    return `<div style="margin-bottom:10px">
      <div class="flex items-center justify-between" style="font-size:12px; margin-bottom:4px">
        <span style="font-weight:500; text-transform:capitalize">${r.type}</span>
        <span class="muted tnum">${r.tested} / ${r.total} · ${Math.round(pct)}%</span>
      </div>
      <div style="height:6px; background:var(--c-surface-3); position:relative">
        <div style="position:absolute; left:0; top:0; bottom:0; width:${pct}%; background:var(--c-brand)"></div>
      </div>
    </div>`;
  }).join("") || `<div class="muted text-xs">No catalog</div>`;
}

// ---- Mode donut ----
function renderModeDonut(history) {
  const tally = { single: 0, bulk: 0, catalog: 0, api: 0, crawl: 0, diagnose: 0, full: 0, readonly: 0 };
  history.forEach(r => {
    const c = r.config || {};
    if (c.catalog_mode) tally.catalog++;
    else if (c.bulk_mode) tally.bulk++;
    else if (c.single_mode) tally.single++;
    else if (c.crawl_mode) tally.crawl++;
    else if (c.api_mode) tally.api++;
    else if (c.diagnose) tally.diagnose++;
    else if (c.read_only) tally.readonly++;
    else tally.full++;
  });
  const total = Object.values(tally).reduce((a, b) => a + b, 0);
  // IBM-style ordered palette
  const entries = [
    { name: "Single",    value: tally.single,   color: "#0f62fe" },
    { name: "Bulk",      value: tally.bulk,     color: "#ff832b" },
    { name: "Catalog",   value: tally.catalog,  color: "#198038" },
    { name: "API",       value: tally.api,      color: "#0072c3" },
    { name: "Crawl",     value: tally.crawl,    color: "#491d8b" },
    { name: "Diagnose",  value: tally.diagnose, color: "#f1c21b" },
    { name: "Full",      value: tally.full,     color: "#6f6f6f" },
    { name: "Read-only", value: tally.readonly, color: "#a8a8a8" },
  ].filter(e => e.value > 0);
  if (!total) {
    $("#donut").innerHTML = "";
    $("#donut-legend").innerHTML = `<div class="muted text-xs">No runs yet</div>`;
    return;
  }
  drawDonut($("#donut"), entries, total);
  $("#donut-legend").innerHTML = entries.map(e => `
    <div class="row">
      <span class="sw" style="background:${e.color}"></span>
      <span class="name">${e.name}</span>
      <span class="val">${e.value} · ${Math.round((e.value / total) * 100)}%</span>
    </div>`).join("");
}

// ===========================================================================
// SVG helpers
// ===========================================================================
function drawSparkline(svg, values, color) {
  const W = svg.clientWidth || 200, H = 28;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  if (!values.length) { svg.innerHTML = ""; return; }
  const max = Math.max(...values, 1), min = Math.min(...values, 0);
  const range = max - min || 1;
  const step = (W - 2) / Math.max(values.length - 1, 1);
  const points = values.map((v, i) => `${1 + i * step},${H - 2 - ((v - min) / range) * (H - 4)}`).join(" ");
  svg.innerHTML = `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="1.5"/>`;
}

function drawTypeBar(svg, screens) {
  const W = svg.clientWidth || 200, H = 6;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  if (!screens.length) { svg.innerHTML = ""; return; }
  const counts = { list: 0, detail: 0, report: 0, wizard: 0, other: 0 };
  screens.forEach(s => { counts[s.type] = (counts[s.type] || 0) + 1; });
  const colors = { list: "#0f62fe", detail: "#0072c3", report: "#198038", wizard: "#ff832b", other: "#a8a8a8" };
  let x = 0;
  const total = screens.length;
  svg.innerHTML = Object.entries(counts).filter(([_, v]) => v > 0).map(([t, v]) => {
    const w = (v / total) * W;
    const seg = `<rect x="${x}" y="0" width="${w}" height="${H}" fill="${colors[t]}"/>`;
    x += w;
    return seg;
  }).join("");
  svg.style.height = "6px";
  svg.style.marginTop = "8px";
}

function drawDonut(svg, entries, total) {
  const SIZE = 140, R = 56, STROKE = 14;
  svg.setAttribute("viewBox", `0 0 ${SIZE} ${SIZE}`);
  const cx = SIZE / 2, cy = SIZE / 2;
  const C = 2 * Math.PI * R;
  let offset = 0;
  const segs = entries.map(e => {
    const len = (e.value / total) * C;
    const el = `<circle cx="${cx}" cy="${cy}" r="${R}" fill="none"
                stroke="${e.color}" stroke-width="${STROKE}"
                stroke-dasharray="${len} ${C - len}"
                stroke-dashoffset="${-offset}"
                transform="rotate(-90 ${cx} ${cy})"/>`;
    offset += len;
    return el;
  }).join("");
  svg.innerHTML = `
    <circle cx="${cx}" cy="${cy}" r="${R}" fill="none" stroke="var(--c-surface-3)" stroke-width="${STROKE}"/>
    ${segs}
    <text x="${cx}" y="${cy - 2}" text-anchor="middle" font-size="22" font-weight="300" fill="var(--c-text)">${total}</text>
    <text x="${cx}" y="${cy + 14}" text-anchor="middle" font-size="11" fill="var(--c-text-2)">runs</text>`;
}

// ---- Real line chart with axes (passrate over time) ----
function drawLineChart(svg, runs) {
  const W = svg.clientWidth || 600, H = 220;
  const PAD_L = 32, PAD_R = 12, PAD_T = 16, PAD_B = 24;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  if (!runs.length) {
    svg.innerHTML = `<text x="${W/2}" y="${H/2}" text-anchor="middle" fill="var(--c-text-3)" font-size="12">No data</text>`;
    return;
  }
  const innerW = W - PAD_L - PAD_R, innerH = H - PAD_T - PAD_B;
  const step = innerW / Math.max(runs.length - 1, 1);
  const yFor = v => PAD_T + (1 - v) * innerH;
  // Y axis grid + labels
  const yTicks = [0, 0.25, 0.5, 0.75, 1];
  let grid = yTicks.map(t => `
    <line x1="${PAD_L}" y1="${yFor(t)}" x2="${W - PAD_R}" y2="${yFor(t)}" stroke="var(--c-border)" stroke-width="1"/>
    <text x="${PAD_L - 6}" y="${yFor(t) + 3}" text-anchor="end" fill="var(--c-text-3)" font-size="10">${Math.round(t * 100)}%</text>
  `).join("");
  // 5-run rolling pass-rate line
  const series = runs.map((_, i) => {
    const window = runs.slice(Math.max(0, i - 4), i + 1);
    return window.filter(r => r.passed).length / window.length;
  });
  const pts = series.map((v, i) => `${PAD_L + i * step},${yFor(v)}`).join(" ");
  // Area under line
  const area = `${PAD_L},${yFor(0)} ${pts} ${PAD_L + (series.length - 1) * step},${yFor(0)}`;
  // Individual points colored by pass/fail
  const dots = runs.map((r, i) =>
    `<circle cx="${PAD_L + i * step}" cy="${yFor(r.passed ? 1 : 0)}" r="2.5"
       fill="${r.passed ? '#198038' : '#da1e28'}"/>`).join("");
  // X axis labels (first / mid / last)
  const xLabels = [0, Math.floor(runs.length / 2), runs.length - 1].map(i => {
    if (!runs[i]) return "";
    const x = PAD_L + i * step;
    return `<text x="${x}" y="${H - 6}" text-anchor="middle" fill="var(--c-text-3)" font-size="10">${fmt.ago(runs[i].ts)}</text>`;
  }).join("");
  svg.innerHTML = `
    <defs>
      <linearGradient id="cg" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#0f62fe" stop-opacity=".18"/>
        <stop offset="100%" stop-color="#0f62fe" stop-opacity="0"/>
      </linearGradient>
    </defs>
    ${grid}
    <polygon points="${area}" fill="url(#cg)"/>
    <polyline points="${pts}" fill="none" stroke="#0f62fe" stroke-width="1.5"/>
    ${dots}
    ${xLabels}`;
}

// ===========================================================================
// View: Run history
// ===========================================================================
let runsSort = { key: "ts", dir: -1 };
async function renderHistory() {
  await loadHistory();
  const draw = () => {
    const q = ($("#runs-search").value || "").toLowerCase();
    let rows = state.history.filter(r => {
      const cfg = r.config || {};
      return !q
        || (cfg.url || "").toLowerCase().includes(q)
        || pickModeText(cfg).toLowerCase().includes(q);
    });
    rows.sort((a, b) => {
      const k = runsSort.key, d = runsSort.dir;
      let av = a[k], bv = b[k];
      if (k === "ts") { av = new Date(av).getTime(); bv = new Date(bv).getTime(); }
      return (av > bv ? 1 : av < bv ? -1 : 0) * d;
    });
    $("#runs-table").innerHTML = rows.length
      ? renderRunsTable(rows, true)
      : `<div class="empty-state"><h3>No matches</h3></div>`;
    $("#runs-count-label").textContent = `${rows.length} of ${state.history.length}`;
    // Wire sort headers
    $$("#runs-table th.sort").forEach(th => {
      th.onclick = () => {
        const k = th.dataset.key;
        if (runsSort.key === k) runsSort.dir = -runsSort.dir;
        else { runsSort.key = k; runsSort.dir = -1; }
        draw();
      };
    });
  };
  draw();
  $("#runs-search").oninput = draw;
}

function renderRunsTable(rows, sortable=false) {
  const cols = [
    { k: "passed",      l: "Status",   w: 110 },
    { k: "ts",          l: "When",     w: 180 },
    { k: "url",         l: "URL" },
    { k: "mode",        l: "Mode",     w: 140 },
    { k: "steps_total", l: "Steps",    w: 80,  num: true },
    { k: "duration_s",  l: "Duration", w: 100, num: true },
    { k: "passfail",    l: "Pass/Fail", w: 100, num: true },
    { k: "actions",     l: "",         w: 80 },
  ];
  const head = cols.map(c => sortable
    ? `<th class="sort ${runsSort.key === c.k ? "sorted" : ""}" data-key="${c.k}" style="${c.w ? `width:${c.w}px` : ''}">${c.l}<span class="sortic">${runsSort.key === c.k ? (runsSort.dir > 0 ? "▲" : "▼") : "↕"}</span></th>`
    : `<th style="${c.w ? `width:${c.w}px` : ''}">${c.l}</th>`).join("");
  const body = rows.map(r => {
    const cfg = r.config || {};
    const status = r.passed
      ? `<span class="status success"><span class="dot"></span>Pass</span>`
      : (r.steps_passed > 0
          ? `<span class="status warning"><span class="dot"></span>Partial</span>`
          : `<span class="status danger"><span class="dot"></span>Fail</span>`);
    const shortUrl = (cfg.url || "").replace(/^https?:\/\//, "").slice(0, 50);
    return `<tr>
      <td>${status}</td>
      <td><span class="tnum">${fmt.shortTime(r.ts)}</span></td>
      <td><span class="mono text-xs">${shortUrl}</span></td>
      <td>${pickModeTag(cfg)}</td>
      <td class="tnum">${r.steps_total}</td>
      <td class="tnum">${fmt.duration(r.duration_s)}</td>
      <td class="tnum">${r.steps_passed}/${r.steps_total}</td>
      <td><div class="row-actions"><a href="/report.html" target="_blank" class="btn btn-sm btn-ghost">Report →</a></div></td>
    </tr>`;
  }).join("");
  return `<table class="dt"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function pickModeTag(cfg) {
  if (cfg.catalog_mode) return `<span class="tag tag-green">Catalog</span>`;
  if (cfg.bulk_mode)    return `<span class="tag tag-purp">Bulk</span>`;
  if (cfg.single_mode)  return `<span class="tag tag-blue">Single · ${(cfg.single_screenname || "?").slice(0, 16)}</span>`;
  if (cfg.crawl_mode)   return `<span class="tag tag-mag">Crawl</span>`;
  if (cfg.api_mode)     return `<span class="tag tag-cyan">API</span>`;
  if (cfg.diagnose)     return `<span class="tag tag-warm">Diagnose</span>`;
  if (cfg.read_only)    return `<span class="tag">Read-only</span>`;
  return `<span class="tag">Full</span>`;
}
function pickModeText(cfg) {
  if (cfg.catalog_mode) return "catalog";
  if (cfg.bulk_mode) return "bulk";
  if (cfg.single_mode) return "single " + (cfg.single_screenname || "");
  if (cfg.crawl_mode) return "crawl";
  if (cfg.api_mode) return "api";
  if (cfg.diagnose) return "diagnose";
  if (cfg.read_only) return "readonly";
  return "full";
}

// ===========================================================================
// View: New test
// ===========================================================================
function renderNew(query={}) {
  if (state.activeEnv && state.profiles[state.activeEnv]) {
    const p = state.profiles[state.activeEnv];
    $("#url").value = p.url || "";
    $("#user").value = p.user || "";
    $("#machine_id").value = p.machine_id || "";
  }
  const sel = $("#env-select");
  sel.innerHTML = `<option value="">— select a saved env —</option>` +
    Object.keys(state.profiles).map(k => `<option value="${k}" ${k===state.activeEnv?"selected":""}>${k}</option>`).join("");
  if (query.preset) {
    const radio = $(`input[name="mode"][value="${query.preset}"]`);
    if (radio) radio.checked = true;
  } else if (!$('input[name="mode"]:checked')) {
    $('input[name="mode"][value="single"]').checked = true;
  }
  if (query.screen) screenCombo.load().then(() => screenCombo.setValue(query.screen));
  applyModeUI();
  $$('input[name="mode"]').forEach(r => r.onchange = applyModeUI);
}
function applyModeUI() {
  const mode = ($('input[name="mode"]:checked') || {}).value;
  $$(".mode-tile").forEach(t => t.classList.toggle("selected",
    t.querySelector('input[type="radio"]')?.checked));
  $("#opts-single").style.display  = (mode === "single") ? "" : "none";
  $("#opts-bulk").style.display    = (mode === "bulk" || mode === "crawl") ? "" : "none";
  $("#opts-catalog").style.display = (mode === "catalog") ? "" : "none";
  if (mode === "single") { screenCombo.load(); syncYamlButtons(); }
  if (mode === "bulk" || mode === "crawl") loadScreensPicker();
}

// ---------- Searchable screen combobox ----------
// Replaces a native <select> with 239 options. Wraps a hidden input
// (#single_screenname) so the rest of the form/submit code is unchanged.
const screenCombo = {
  initted: false,
  isOpen: false,
  active: 0,
  items: [],          // full catalog (filtered by type)
  filtered: [],       // after search query

  init() {
    if (this.initted) return;
    this.btn    = $("#single-combo-btn");
    this.panel  = $("#single-combo-panel");
    this.search = $("#single-combo-search");
    this.list   = $("#single-combo-list");
    this.value  = $("#single_screenname");
    this.label  = $("#single-combo-value");
    this.count  = $("#single-combo-count");

    this.btn.addEventListener("click", e => { e.stopPropagation(); this.toggle(); });
    this.search.addEventListener("input", () => this.refresh());
    this.search.addEventListener("keydown", e => {
      if (e.key === "ArrowDown") { e.preventDefault(); this.move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); this.move(-1); }
      else if (e.key === "Enter") { e.preventDefault(); this.choose(); }
      else if (e.key === "Escape") this.close();
    });
    // Close when clicking outside the combo
    document.addEventListener("click", e => {
      if (this.isOpen && !this.panel.contains(e.target) && !this.btn.contains(e.target)) {
        this.close();
      }
    });
    this.initted = true;
  },

  async load() {
    this.init();
    await loadCatalog();
    const filter = $("#single_type_filter").value;
    this.items = (state.catalog.screens || []).filter(s => !filter || s.type === filter);
    // If the currently selected value isn't in the filtered list, clear it
    if (this.value.value && !this.items.find(s => s.screenname === this.value.value)) {
      this.setValue("");
    }
    this.refresh();
  },

  setValue(name) {
    this.value.value = name || "";
    const s = this.items.find(x => x.screenname === name);
    if (s) {
      this.label.textContent = `${s.screenname}${s.label ? " — " + s.label : ""} (${s.type})`;
      this.label.classList.add("has-value");
    } else {
      this.label.textContent = "— choose screen —";
      this.label.classList.remove("has-value");
    }
    // Notify any listeners that the value changed (mimics native <select>)
    this.value.dispatchEvent(new Event("change", { bubbles: true }));
  },

  toggle() { this.isOpen ? this.close() : this.open(); },

  open() {
    this.isOpen = true;
    this.panel.classList.remove("hidden");
    this.btn.classList.add("open");
    this.search.value = "";
    this.refresh();
    // Pre-highlight the currently selected screen, if any
    const idx = this.items.findIndex(s => s.screenname === this.value.value);
    if (idx >= 0) this.active = idx;
    this.refresh();
    setTimeout(() => this.search.focus(), 10);
  },

  close() {
    this.isOpen = false;
    this.panel.classList.add("hidden");
    this.btn.classList.remove("open");
  },

  refresh() {
    const q = (this.search.value || "").toLowerCase().trim();
    this.filtered = q
      ? this.items.filter(s =>
          s.screenname.toLowerCase().includes(q) ||
          (s.label || "").toLowerCase().includes(q))
      : this.items;
    if (this.active >= this.filtered.length) this.active = 0;
    this.count.textContent = q
      ? `${this.filtered.length} of ${this.items.length}`
      : `${this.items.length} screens`;

    if (!this.filtered.length) {
      this.list.innerHTML = `<div class="combo-empty">No screens match "${escapeHTML(q)}"</div>`;
      return;
    }
    const cur = this.value.value;
    this.list.innerHTML = this.filtered.slice(0, 300).map((s, i) => {
      const selected = s.screenname === cur ? "selected" : "";
      const active = i === this.active ? "active" : "";
      const name = q ? highlight(s.screenname, q) : escapeHTML(s.screenname);
      const lbl  = s.label ? (q ? highlight(s.label, q) : escapeHTML(s.label)) : "";
      return `<div class="combo-item ${selected} ${active}" data-screen="${escapeHTML(s.screenname)}" data-i="${i}">
        <span class="name">${name}</span>
        ${lbl ? `<span class="lbl">${lbl}</span>` : `<span></span>`}
        <span class="type">${s.type}</span>
      </div>`;
    }).join("");
    this.list.querySelectorAll(".combo-item").forEach(el => {
      el.addEventListener("click", () => {
        this.setValue(el.dataset.screen);
        this.close();
      });
      el.addEventListener("mouseenter", () => {
        this.list.querySelectorAll(".combo-item.active").forEach(x => x.classList.remove("active"));
        el.classList.add("active");
        this.active = +el.dataset.i;
      });
    });
    // Scroll active into view
    const activeEl = this.list.querySelector(".combo-item.active");
    if (activeEl) activeEl.scrollIntoView({ block: "nearest" });
  },

  move(delta) {
    if (!this.filtered.length) return;
    this.active = Math.max(0, Math.min(this.filtered.length - 1, this.active + delta));
    this.list.querySelectorAll(".combo-item").forEach((el, i) =>
      el.classList.toggle("active", i === this.active));
    const activeEl = this.list.querySelector(".combo-item.active");
    if (activeEl) activeEl.scrollIntoView({ block: "nearest" });
  },

  choose() {
    if (this.filtered[this.active]) {
      this.setValue(this.filtered[this.active].screenname);
      this.close();
    }
  },
};

// Highlight matching substring for combobox search results
function highlight(text, q) {
  const s = String(text);
  const i = s.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return escapeHTML(s);
  return escapeHTML(s.slice(0, i)) + "<mark>" + escapeHTML(s.slice(i, i + q.length)) + "</mark>" + escapeHTML(s.slice(i + q.length));
}

// Re-load combobox when the type filter changes
document.addEventListener("change", e => {
  if (e.target.id === "single_type_filter") screenCombo.load();
});

// ---------- YAML download / edit / upload for the picked screen ----------
// Enable the download + "load into editor" buttons only once a screen is chosen.
function syncYamlButtons() {
  const sn = ($("#single_screenname") || {}).value || "";
  const dl   = $("#btn-download-yaml");
  const load = $("#btn-load-downloaded");
  const hint = $("#yaml-hint");
  const ready = !!sn;
  if (dl)   dl.disabled = !ready;
  if (load) load.disabled = !ready;
  if (hint) hint.textContent = ready
    ? `Download ${sn}'s ${(window._lastYamlSafe === false) ? "" : ""}auto-tests as YAML, edit, then re-upload below.`
    : "Pick a screen first — then download its tests, edit, and re-upload below.";
}
// The combobox dispatches a `change` on #single_screenname when a value is set.
document.addEventListener("change", e => {
  if (e.target && e.target.id === "single_screenname") syncYamlButtons();
});

// Download the auto-generated YAML for the picked screen.
document.addEventListener("click", e => {
  if (e.target.closest && e.target.closest("#btn-download-yaml")) {
    const sn = ($("#single_screenname") || {}).value;
    if (!sn) return;
    const safe = ($("#single_safe") || {}).checked ? "1" : "0";
    // Plain navigation triggers the file download (Content-Disposition).
    window.location.href = `/api/catalog/${encodeURIComponent(sn)}/yaml?safe=${safe}`;
  }
});

// Load the picked screen's auto-tests straight into the editor textarea.
document.addEventListener("click", async e => {
  if (e.target.closest && e.target.closest("#btn-load-downloaded")) {
    const sn = ($("#single_screenname") || {}).value;
    if (!sn) return;
    const safe = ($("#single_safe") || {}).checked ? "1" : "0";
    const btn = $("#btn-load-downloaded");
    btn.disabled = true; const orig = btn.textContent; btn.textContent = "Loading…";
    try {
      const r = await fetch(`/api/catalog/${encodeURIComponent(sn)}/yaml?safe=${safe}`);
      const text = await r.text();
      $("#single_custom_yaml").value = text;
    } catch (err) {
      alert("Could not load YAML: " + err.message);
    } finally {
      btn.disabled = false; btn.textContent = orig;
    }
  }
});

// Read an uploaded .yaml/.yml file into the editor textarea.
document.addEventListener("change", e => {
  if (e.target && e.target.id === "yaml-upload") {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => { $("#single_custom_yaml").value = reader.result; };
    reader.readAsText(f);
  }
});

// Clear the editor.
document.addEventListener("click", e => {
  if (e.target.closest && e.target.closest("#btn-clear-yaml")) {
    $("#single_custom_yaml").value = "";
    const up = $("#yaml-upload"); if (up) up.value = "";
  }
});

// Cache the last uploaded xlsx (as a File) so we can re-send it to the
// ZIP endpoint without making the user pick the file again.
let _lastXlsxImport = null;

// Upload an Excel test-case file → server returns YAML.
// Two output shapes:
//   • Template format: `per_screen` map covers many screens → offer ZIP download
//   • Legacy prose:    single YAML → land it in the editor for the picked screen
document.addEventListener("change", async e => {
  if (e.target && e.target.id === "xlsx-import") {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    _lastXlsxImport = f;
    const screen = ($("#single_screenname") || {}).value || "yourscreen";
    const status = $("#xlsx-status");
    status.innerHTML = `<span class="muted">Importing ${escapeHTML(f.name)}…</span>`;
    try {
      const form = new FormData();
      form.append("file", f); form.append("screen", screen);
      const r = await fetch("/api/import-testcases", { method: "POST", body: form });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
      $("#single_custom_yaml").value = data.yaml;

      if (data.layout === "template") {
        // Fast path — every step mapped directly. If the template covers
        // many screens, surface a ZIP download.
        const nScr = (data.screens || []).length || 1;
        const zipBtn = nScr > 1
          ? ` <button class="btn btn-sm" id="btn-xlsx-zip" type="button">⬇ Download ZIP (${nScr} screen YAMLs)</button>`
          : "";
        status.innerHTML = `<b style="color:var(--c-success)">✓ Template imported.</b> ` +
          `<b>${data.n_tests}</b> test${data.n_tests === 1 ? "" : "s"}, ` +
          `<b>${data.n_steps_total}</b> steps across ` +
          `<b>${nScr}</b> screen${nScr === 1 ? "" : "s"} ` +
          `<span style="color:var(--c-success)">(100% mapped, no <code>todo</code> markers)</span>${zipBtn}`;
      } else {
        // Legacy path — partial translation
        status.innerHTML = `<b style="color:var(--c-warning)">Imported (legacy).</b> ` +
          `Layout: <b>${data.layout}</b> · ` +
          `${data.n_tests} test${data.n_tests === 1 ? "" : "s"} · ` +
          `${data.n_steps_translated}/${data.n_steps_total} steps auto-translated ` +
          `(<b>${data.pct_translated}%</b>) — edit any <code>action: todo</code> rows. ` +
          `<a href="/api/template/xlsx" style="color:var(--c-brand)">Use the official template</a> for 100% clean import.`;
      }
    } catch (err) {
      status.innerHTML = `<b style="color:var(--c-danger)">Failed:</b> ${escapeHTML(err.message)}`;
    }
    // Allow re-selecting the same file
    e.target.value = "";
  }
});

// ===========================================================================
// View: YAML converter — standalone Excel → YAML pipeline (no screen needed)
// ===========================================================================
let _convLastFile = null;

function renderConverter() {
  // First render: wire the drop zone + file picker exactly once.
  const drop = $("#conv-drop");
  if (!drop || drop._wired) return;
  drop._wired = true;

  drop.onclick = () => $("#conv-file").click();
  $("#conv-file").onchange = e => {
    const f = e.target.files && e.target.files[0];
    if (f) convertExcel(f);
    e.target.value = "";
  };
  // Native drag-and-drop
  drop.addEventListener("dragover", e => {
    e.preventDefault(); drop.style.borderColor = "var(--c-brand)";
  });
  drop.addEventListener("dragleave", () => { drop.style.borderColor = "var(--c-border-2)"; });
  drop.addEventListener("drop", e => {
    e.preventDefault(); drop.style.borderColor = "var(--c-border-2)";
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) convertExcel(f);
  });

  $("#btn-print-guide").onclick = () => {
    // Open a clean print window containing only the format guide
    const css = `body{font-family:Arial,sans-serif;margin:24px;color:#161616;font-size:13px}
                  h1{margin:0 0 4px}h3{margin:18px 0 6px}
                  table{border-collapse:collapse;width:100%;margin-bottom:12px}
                  th,td{border:1px solid #d0d5dd;padding:6px 10px;text-align:left;vertical-align:top}
                  th{background:#f4f4f5}
                  code{font-family:Menlo,Consolas,monospace;font-size:12px;background:#f4f4f5;padding:1px 4px}
                  pre{background:#f4f4f5;padding:10px;border-left:3px solid #6d28d9;font-size:11px;white-space:pre-wrap}`;
    const w = window.open("", "_blank");
    w.document.write(`<!doctype html><html><head><title>Stratus QA — Test-case format</title>
      <style>${css}</style></head><body>
      <h1>Stratus QA — Test-case format guide</h1>
      ${$("#format-guide").innerHTML}
      </body></html>`);
    w.document.close();
    setTimeout(() => w.print(), 300);
  };
}

async function convertExcel(file) {
  _convLastFile = file;
  const result = $("#conv-result");
  result.style.display = "";
  $("#conv-result-title").textContent = `Converting ${file.name}…`;
  $("#conv-result-sub").textContent = "";
  $("#conv-result-actions").innerHTML = "";
  $("#conv-yaml").value = "";

  try {
    const form = new FormData();
    form.append("file", file);
    form.append("screen", "yourscreen");          // legacy fallback default
    const r = await fetch("/api/import-testcases", { method: "POST", body: form });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));

    $("#conv-yaml").value = data.yaml;
    const n = (data.screens || []).length || 1;

    if (data.layout === "template") {
      $("#conv-result-title").innerHTML =
        `✓ <b style="color:var(--c-success)">Template</b> — 100% mapped`;
      $("#conv-result-sub").innerHTML =
        `<b>${data.n_tests}</b> tests · <b>${data.n_steps_total}</b> steps · ` +
        `<b>${n}</b> screen${n === 1 ? "" : "s"}` +
        (data.screens?.length ? ` (${data.screens.join(", ")})` : "");
    } else {
      $("#conv-result-title").innerHTML =
        `⚠ <b style="color:var(--c-warning)">Legacy</b> — partial translation (${data.pct_translated}%)`;
      $("#conv-result-sub").innerHTML =
        `<b>${data.n_tests}</b> tests · ` +
        `<b>${data.n_steps_translated}/${data.n_steps_total}</b> steps auto-translated · ` +
        `${data.n_steps_total - data.n_steps_translated} <code>todo</code> markers. ` +
        `<a href="/api/template/xlsx" style="color:var(--c-brand)">Use the official template</a> for 100% accuracy.`;
    }

    // Build action buttons: download combined YAML always; ZIP if multi-screen.
    const acts = [];
    acts.push(`<button class="btn btn-sm" id="conv-dl-yaml" type="button">⬇ Download YAML</button>`);
    if (n > 1) acts.push(`<button class="btn btn-sm btn-primary" id="conv-dl-zip" type="button">⬇ Download ZIP (${n} files)</button>`);
    acts.push(`<button class="btn btn-sm btn-ghost" id="conv-copy" type="button">📋 Copy</button>`);
    $("#conv-result-actions").innerHTML = acts.join(" ");

    $("#conv-dl-yaml").onclick = () => downloadText(
      data.yaml,
      (file.name.replace(/\.[^.]+$/, "") || "testcases") + ".yaml",
      "application/x-yaml");
    const zipBtn = $("#conv-dl-zip");
    if (zipBtn) zipBtn.onclick = downloadZip;
    $("#conv-copy").onclick = async () => {
      await navigator.clipboard.writeText(data.yaml);
      $("#conv-copy").textContent = "✓ Copied";
      setTimeout(() => { $("#conv-copy").innerHTML = "📋 Copy"; }, 1500);
    };
  } catch (err) {
    $("#conv-result-title").innerHTML =
      `<b style="color:var(--c-danger)">Failed</b>`;
    $("#conv-result-sub").textContent = err.message;
  }
}

function downloadText(text, name, mime) {
  const blob = new Blob([text], { type: mime || "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

async function downloadZip() {
  if (!_convLastFile) return;
  const form = new FormData();
  form.append("file", _convLastFile);
  form.append("screen", "yourscreen");
  const r = await fetch("/api/import-testcases/zip", { method: "POST", body: form });
  if (!r.ok) { alert("ZIP failed: HTTP " + r.status); return; }
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "testcases.zip";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

// ZIP-of-YAMLs download (only shown after a multi-screen template import)
document.addEventListener("click", async e => {
  if (e.target && e.target.id === "btn-xlsx-zip") {
    if (!_lastXlsxImport) { alert("Re-upload the Excel — the previous file was lost."); return; }
    const form = new FormData();
    form.append("file", _lastXlsxImport);
    form.append("screen", ($("#single_screenname") || {}).value || "yourscreen");
    const r = await fetch("/api/import-testcases/zip", { method: "POST", body: form });
    if (!r.ok) { alert("ZIP download failed: HTTP " + r.status); return; }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "testcases.zip"; document.body.appendChild(a);
    a.click(); a.remove(); URL.revokeObjectURL(url);
  }
});

async function loadScreensPicker() {
  await loadCatalog();
  renderScreensPicker();
  $("#screens_search").oninput = renderScreensPicker;
  $$(".crawl-type").forEach(cb => cb.onchange = renderScreensPicker);
  $("#btn_select_all").onclick  = () => { visibleScreens().forEach(s => state.selectedScreens.add(s.screenname)); renderScreensPicker(); };
  $("#btn_select_none").onclick = () => { state.selectedScreens.clear(); renderScreensPicker(); };
  $("#btn_select_invert").onclick = () => {
    visibleScreens().forEach(s => {
      if (state.selectedScreens.has(s.screenname)) state.selectedScreens.delete(s.screenname);
      else state.selectedScreens.add(s.screenname);
    });
    renderScreensPicker();
  };
}
function visibleScreens() {
  const q = ($("#screens_search").value || "").toLowerCase();
  const types = new Set($$(".crawl-type:checked").map(c => c.value));
  return (state.catalog.screens || [])
    .filter(s => types.has(s.type))
    .filter(s => !q || (s.screenname.toLowerCase().includes(q) || (s.label || "").toLowerCase().includes(q)));
}
function renderScreensPicker() {
  const list = visibleScreens();
  const target = $("#screens-picker");
  if (!list.length) { target.innerHTML = `<div class="empty-state"><p>No screens match.</p></div>`; return; }
  target.innerHTML = list.map(s => {
    const checked = state.selectedScreens.has(s.screenname) ? "checked" : "";
    return `<label class="sp-item">
      <input type="checkbox" class="sp-cb" value="${s.screenname}" ${checked}>
      <span class="name">${s.screenname}</span>
      <span class="type">${s.type}</span>
      <span class="muted text-xs">${s.field_count}f · ${s.button_count}b</span>
    </label>`;
  }).join("");
  target.querySelectorAll(".sp-cb").forEach(cb => {
    cb.onchange = () => {
      if (cb.checked) state.selectedScreens.add(cb.value);
      else state.selectedScreens.delete(cb.value);
    };
  });
}

// Test connection
$("#btn-test-conn").addEventListener("click", async () => {
  const url = $("#url").value.trim();
  if (!url) { $("#conn-status").textContent = "URL is empty"; return; }
  $("#conn-status").textContent = "Testing…";
  const out = $("#conn-result");
  try {
    const r = await fetch("/api/test-connection", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await r.json();
    out.innerHTML = `<div class="conn-card ${data.ok ? "ok" : "fail"}">` +
      (data.checks || []).map(c => `
        <div class="conn-check">
          <span>${c.ok ? "✓" : "✗"}</span>
          <span class="name">${c.name}</span>
          <span class="muted">${c.detail || ""}</span>
        </div>`).join("") + `</div>`;
    $("#conn-status").textContent = data.ok ? "OK" : "Failed";
  } catch (e) {
    $("#conn-status").textContent = "Error";
    out.innerHTML = `<div class="conn-card fail">${e.message}</div>`;
  }
});

// Start
$("#btn-start").addEventListener("click", async () => {
  const mode = ($('input[name="mode"]:checked') || {}).value;
  if (!mode) { alert("Pick a mode first."); return; }
  const body = {
    url: $("#url").value.trim(),
    user: $("#user").value.trim(),
    password: $("#password").value,
    machine_id: $("#machine_id").value.trim(),
    api_mode:     mode === "api",
    crawl_mode:   mode === "crawl",
    single_mode:  mode === "single",
    catalog_mode: mode === "catalog",
    bulk_mode:    mode === "bulk",
    read_only:    mode === "readonly",
    diagnose:     mode === "diagnose",
    single_screenname: $("#single_screenname").value,
    single_safe:       $("#single_safe").checked,
    crawl_types:       $$(".crawl-type:checked").map(c => c.value),
    crawl_scope:       ($("#catalog_scope") || {}).value || "",
    crawl_max:         parseInt(($("#catalog_max") || {}).value) || parseInt(($("#bulk_max") || {}).value) || 0,
    selected_screens:  Array.from(state.selectedScreens),
    custom_tests_yaml: ($("#single_custom_yaml") || {}).value || "",
  };
  if (!body.url || !body.user || !body.password) { alert("URL, username and password are required."); return; }
  const r = await fetch("/api/run", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (r.status === 409) { alert("A run is already in progress."); return; }
  if (!r.ok) { alert("Failed to start: " + r.status); return; }
  navigate("#/run");
});

// ===========================================================================
// View: Run progress
// ===========================================================================
function renderRun() {
  $("#live-log").innerHTML = "";
  state.liveCounts = { pass: 0, fail: 0, total: 0 };
  updateLiveStats();
  state.runStartedAt = Date.now();
  $("#run-status-pill").className = "status running";
  $("#run-title").textContent = "Test running…";
  $("#run-shot-card").style.display = "none";

  if (state.runTimer) clearInterval(state.runTimer);
  state.runTimer = setInterval(() => {
    $("#rs-elapsed").textContent = fmt.duration((Date.now() - state.runStartedAt) / 1000);
  }, 1000);

  if (state.evtSource) state.evtSource.close();
  state.evtSource = new EventSource("/api/events");
  state.evtSource.onmessage = e => appendLog(JSON.parse(e.data));
  state.evtSource.addEventListener("end", async () => {
    state.evtSource.close();
    clearInterval(state.runTimer);
    const r = await fetch("/api/status");
    const res = (await r.json()).result || {};
    $("#run-status-pill").className = `status ${res.passed ? "success" : "danger"}`;
    $("#run-title").textContent = res.passed ? `Test passed — ${res.steps_passed}/${res.steps_total} · ${fmt.duration(res.duration_s)}` : `Test failed — ${res.steps_passed}/${res.steps_total} · ${fmt.duration(res.duration_s)}`;
  });
}

function appendLog(evt) {
  const log = $("#live-log");
  const ts = new Date().toLocaleTimeString(undefined, { hour12: false });
  let cls = "log-info", icon = "·";
  switch (evt.type) {
    case "ok":   cls = "log-ok";   icon = "✓"; state.liveCounts.pass++; state.liveCounts.total++; break;
    case "fail": cls = "log-fail"; icon = "✗"; state.liveCounts.fail++; state.liveCounts.total++; break;
    case "warn": cls = "log-warn"; icon = "!"; break;
    case "info": cls = "log-info"; icon = "·"; break;
    case "section":
      log.insertAdjacentHTML("beforeend", `<div class="log-section">${escapeHTML(evt.text || "")}</div>`);
      log.scrollTop = log.scrollHeight; return;
  }
  const shotHTML = evt.screenshot
    ? `<div style="margin-top:6px"><a href="${evt.screenshot}" target="_blank"><img src="${evt.screenshot}" style="max-width:240px; border:1px solid var(--c-border)"></a></div>` : "";
  log.insertAdjacentHTML("beforeend",
    `<div class="log-row ${cls}"><span class="t">${ts}</span><span class="i">${icon}</span><span class="m">${escapeHTML(evt.text || "")}${shotHTML}</span></div>`);
  log.scrollTop = log.scrollHeight;
  updateLiveStats();
  if (evt.screenshot) showLatestShot(evt);
}
function updateLiveStats() {
  $("#rs-steps").textContent = state.liveCounts.total;
  $("#rs-pass").textContent  = state.liveCounts.pass;
  $("#rs-fail").textContent  = state.liveCounts.fail;
}
function showLatestShot(evt) {
  $("#run-shot-card").style.display = "";
  $("#run-shot-body").innerHTML = `
    <a href="${evt.screenshot}" target="_blank">
      <img src="${evt.screenshot}" style="width:100%; border:1px solid var(--c-border)">
    </a>
    <div class="muted text-xs mt-2 truncate">${escapeHTML(evt.text || "")}</div>`;
}
$("#btn-stop").addEventListener("click", () => {
  if (state.evtSource) state.evtSource.close();
  if (state.runTimer) clearInterval(state.runTimer);
  $("#run-title").textContent = "Stopped (server may still be running)";
});

// ===========================================================================
// View: Catalog
// ===========================================================================
let catSort = { key: "screenname", dir: 1 };
async function renderCatalog() {
  await loadCatalog();
  const cat = state.catalog;
  $("#catalog-meta").textContent = cat.built_at
    ? `${(cat.screens || []).length} screens · built ${fmt.ago(cat.built_at)}`
    : "Catalog not built yet — run Rebuild catalog first.";
  const draw = () => {
    const q = ($("#catalog-search").value || "").toLowerCase();
    const types = new Set($$(".cat-type:checked").map(c => c.value));
    let rows = (cat.screens || [])
      .filter(s => types.has(s.type))
      .filter(s => !q || s.screenname.toLowerCase().includes(q) || (s.label || "").toLowerCase().includes(q));
    rows.sort((a, b) => {
      const k = catSort.key, d = catSort.dir;
      let av = a[k], bv = b[k];
      if (typeof av === "string") return av.localeCompare(bv) * d;
      return (av > bv ? 1 : av < bv ? -1 : 0) * d;
    });
    $("#catalog-count-label").textContent = `${rows.length} of ${(cat.screens || []).length}`;
    if (!rows.length) { $("#catalog-table").innerHTML = `<div class="empty-state"><h3>No matches</h3></div>`; return; }
    const sortable = (k, l, w, num) => `<th class="sort ${catSort.key === k ? "sorted" : ""}" data-key="${k}" style="${w ? `width:${w}px` : ''}">${l}<span class="sortic">${catSort.key === k ? (catSort.dir > 0 ? "▲" : "▼") : "↕"}</span></th>`;
    $("#catalog-table").innerHTML = `<table class="dt">
      <thead><tr>
        ${sortable("screenname", "Screen")}
        ${sortable("type", "Type", 120)}
        ${sortable("field_count", "Fields", 100, true)}
        ${sortable("button_count", "Buttons", 100, true)}
        <th style="width:100px"></th>
      </tr></thead>
      <tbody>${rows.map(s => `
        <tr>
          <td><strong>${s.screenname}</strong>${s.label ? `<div class="muted text-xs">${escapeHTML(s.label)}</div>` : ""}</td>
          <td>${typeTag(s.type)}</td>
          <td class="tnum">${s.field_count}</td>
          <td class="tnum">${s.button_count}</td>
          <td><div class="row-actions"><a href="#/new?preset=single&screen=${encodeURIComponent(s.screenname)}" class="btn btn-sm btn-ghost">Test →</a></div></td>
        </tr>`).join("")}</tbody></table>`;
    $$("#catalog-table th.sort").forEach(th => {
      th.onclick = () => {
        const k = th.dataset.key;
        if (catSort.key === k) catSort.dir = -catSort.dir;
        else { catSort.key = k; catSort.dir = 1; }
        draw();
      };
    });
  };
  draw();
  $("#catalog-search").oninput = draw;
  $$(".cat-type").forEach(cb => cb.onchange = draw);
}
function typeTag(t) {
  const m = { list: "tag-blue", detail: "tag-cyan", report: "tag-green", wizard: "tag-mag", other: "" };
  return `<span class="tag ${m[t] || ''}">${t}</span>`;
}

// ===========================================================================
// View: Environments
// ===========================================================================
async function renderEnv() {
  await loadProfiles();
  const target = $("#env-table");
  const keys = Object.keys(state.profiles);
  if (!keys.length) {
    target.innerHTML = `<div class="empty-state"><h3>No saved environments</h3><p>Open New Run, enter URL/user/password, click "Save current as…"</p></div>`;
    return;
  }
  target.innerHTML = `<table class="dt">
    <thead><tr><th>Name</th><th>URL</th><th>User</th><th>Machine ID</th><th></th></tr></thead>
    <tbody>${keys.map(k => {
      const p = state.profiles[k];
      const active = k === state.activeEnv ? ` <span class="tag tag-green">Active</span>` : "";
      return `<tr>
        <td><strong>${k}</strong>${active}</td>
        <td class="mono text-xs">${p.url || "—"}</td>
        <td>${p.user || "—"}</td>
        <td>${p.machine_id || "—"}</td>
        <td><div class="row-actions">
          <button class="btn btn-sm btn-ghost" data-act="use" data-name="${k}">Use</button>
          <button class="btn btn-sm btn-ghost" data-act="del" data-name="${k}">Delete</button>
        </div></td>
      </tr>`;
    }).join("")}</tbody></table>`;
  target.querySelectorAll("button[data-act]").forEach(b => {
    b.onclick = async () => {
      const { act, name } = b.dataset;
      if (act === "del") {
        if (!confirm(`Delete "${name}"?`)) return;
        await fetch(`/api/profiles/${encodeURIComponent(name)}`, { method: "DELETE" });
        if (state.activeEnv === name) state.activeEnv = null;
        await refreshEnvChip(); renderEnv();
      } else if (act === "use") {
        state.activeEnv = name;
        await refreshEnvChip(); renderEnv();
      }
    };
  });
}
$("#env-save").addEventListener("click", () => {
  $("#env-name").value = "";
  $("#env-modal").classList.remove("hidden");
});
$("#env-cancel").addEventListener("click", () => $("#env-modal").classList.add("hidden"));
$("#env-modal-close").addEventListener("click", () => $("#env-modal").classList.add("hidden"));
$("#env-confirm").addEventListener("click", async () => {
  const name = $("#env-name").value.trim();
  if (!name) return;
  await fetch("/api/profiles", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, url: $("#url").value, user: $("#user").value, machine_id: $("#machine_id").value }),
  });
  state.activeEnv = name;
  $("#env-modal").classList.add("hidden");
  await refreshEnvChip();
});
$("#env-select").addEventListener("change", e => {
  state.activeEnv = e.target.value;
  refreshEnvChip();
  renderNew(currentRoute().query);
});

// ===========================================================================
// View: Help
// ===========================================================================
function renderHelp() {
  const modes = [
    { name: "One screen",          tag: "Recommended", tagcls: "tag-blue",
      what: "Pick any catalog screen. Tool auto-generates ~10–25 tests and runs them.",
      when: "Developer changed one area. Deep verification of a single screen.",
      example: "Ravi tweaked customer-search — run all 23 tests on customerlist." },
    { name: "Many screens",        tag: "Recommended", tagcls: "tag-blue",
      what: "Loop through every (or selected) catalog screen and run all auto-tests.",
      when: "Nightly regression. Pre-release smoke. Post-migration.",
      example: "Friday night — test all 239 screens, send Monday report." },
    { name: "Rebuild catalog",     tag: "Setup", tagcls: "tag-green",
      what: "Visits every screen once and writes down what's on it.",
      when: "First install. After a Stratus release.",
      example: "Stratus shipped v6.4 with loyaltyrewardlist — re-scan." },
    { name: "API smoke",           tag: "For CI", tagcls: "tag-cyan",
      what: "No browser. Hits /stratus JSON endpoint directly via HTTP.",
      when: "5-sec \"is the API up?\" in your CI pipeline.",
      example: "Jenkins runs after every deploy. Fails build on error." },
    { name: "Crawl from menu",     tag: "", tagcls: "",
      what: "Like Bulk but discovers screens from live menu (not catalog).",
      when: "Catalog is stale and you don't want to rebuild first.",
      example: "Added screen 5 min ago — crawl finds it." },
    { name: "Diagnose",            tag: "Debug", tagcls: "tag-warm",
      what: "No assertions — just logs in, navigates, dumps HTML + screenshots.",
      when: "Test mysteriously failing; you want raw evidence.",
      example: "Says 'Search not found' — Diagnose grabs the HTML." },
    { name: "Full (legacy)",       tag: "Legacy", tagcls: "",
      what: "Original 8-step Customer-only demo.",
      when: "Backward compat only.",
      example: "—" },
    { name: "Read-only (legacy)",  tag: "Legacy", tagcls: "",
      what: "Same as Full but skips Save/Delete.",
      when: "Backward compat.",
      example: "—" },
  ];
  $("#help-body").innerHTML = `<div class="mode-grid">${modes.map(m => `
    <div class="mode-tile" style="cursor:default">
      <div class="name">${m.name} ${m.tag ? `<span class="tag ${m.tagcls}">${m.tag}</span>` : ""}</div>
      <div class="desc mt-3"><b>What.</b> ${m.what}</div>
      <div class="desc"><b>When.</b> ${m.when}</div>
      <div class="desc"><b>Example.</b> ${m.example}</div>
    </div>`).join("")}</div>`;
}

// ===========================================================================
// Command palette
// ===========================================================================
const palette = {
  index: [], active: 0,
  open() { $("#palette-overlay").classList.remove("hidden"); $("#palette-input").value = ""; $("#palette-input").focus(); this.refresh(); },
  close() { $("#palette-overlay").classList.add("hidden"); },
  build() {
    const items = [];
    items.push({ section: "Navigate", label: "Overview",            hint: "Home",            href: "#/dashboard" });
    items.push({ section: "Navigate", label: "New test run",         hint: "Start a run",     href: "#/new" });
    items.push({ section: "Navigate", label: "Run history",          hint: "Past runs",       href: "#/runs" });
    items.push({ section: "Navigate", label: "Screen catalog",       hint: "All screens",     href: "#/catalog" });
    items.push({ section: "Navigate", label: "YAML converter",        hint: "Excel → YAML",    href: "#/converter" });
    items.push({ section: "Navigate", label: "Environments",         hint: "Saved profiles",  href: "#/env" });
    items.push({ section: "Navigate", label: "Mode glossary",        hint: "Help",            href: "#/help" });
    items.push({ section: "Quick action", label: "Start: Single screen",  hint: "Pick + run", href: "#/new?preset=single" });
    items.push({ section: "Quick action", label: "Start: Bulk auto-test", hint: "Many screens",href: "#/new?preset=bulk" });
    items.push({ section: "Quick action", label: "Start: Rebuild catalog", hint: "~10–15 min", href: "#/new?preset=catalog" });
    items.push({ section: "Quick action", label: "Start: API smoke (5s)",  hint: "CI-friendly", href: "#/new?preset=api" });
    (state.catalog?.screens || []).forEach(s => {
      items.push({ section: "Screens", label: s.screenname,
        hint: `${s.type} · ${s.field_count}f · ${s.button_count}b`,
        href: `#/new?preset=single&screen=${encodeURIComponent(s.screenname)}` });
    });
    this.index = items;
  },
  refresh() {
    const q = ($("#palette-input").value || "").toLowerCase();
    const matches = q
      ? this.index.filter(it => it.label.toLowerCase().includes(q) || it.hint.toLowerCase().includes(q)).slice(0, 60)
      : this.index.slice(0, 12);
    if (this.active >= matches.length) this.active = 0;
    let html = "", section = null;
    matches.forEach((it, i) => {
      if (it.section !== section) { html += `<div class="palette-section">${it.section}</div>`; section = it.section; }
      html += `<div class="palette-item ${i === this.active ? "active" : ""}" data-href="${it.href}" data-i="${i}">
        <div class="t">${it.label}</div><div class="s">${it.hint}</div></div>`;
    });
    if (!matches.length) html = `<div class="palette-section">No matches</div>`;
    $("#palette-list").innerHTML = html;
    $("#palette-list").querySelectorAll(".palette-item").forEach(el => {
      el.onmouseenter = () => { palette.active = +el.dataset.i; palette.refresh(); };
      el.onclick = () => { navigate(el.dataset.href); palette.close(); };
    });
  },
  navigate(d) {
    const items = $$("#palette-list .palette-item");
    if (!items.length) return;
    this.active = (this.active + d + items.length) % items.length;
    this.refresh();
  },
  enter() {
    const items = $$("#palette-list .palette-item");
    if (items[this.active]) { navigate(items[this.active].dataset.href); this.close(); }
  },
};
$("#open-palette").addEventListener("click", () => palette.open());
$("#palette-input").addEventListener("input", () => palette.refresh());
$("#palette-overlay").addEventListener("click", e => { if (e.target.id === "palette-overlay") palette.close(); });
document.addEventListener("keydown", e => {
  if ((e.metaKey || e.ctrlKey) && e.key === "k") { e.preventDefault(); palette.open(); return; }
  if (!$("#palette-overlay").classList.contains("hidden")) {
    if (e.key === "Escape") palette.close();
    if (e.key === "ArrowDown") { e.preventDefault(); palette.navigate(1); }
    if (e.key === "ArrowUp")   { e.preventDefault(); palette.navigate(-1); }
    if (e.key === "Enter")     { e.preventDefault(); palette.enter(); }
  }
});

// ===========================================================================
// Helpers
// ===========================================================================
function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]));
}

// ===========================================================================
// Bootstrap
// ===========================================================================
(async function init() {
  await refreshEnvChip();
  await loadCatalog();
  await loadHistory();
  palette.build();
  updateStatusBar();
  if (!location.hash) location.hash = "#/dashboard";
  renderView();
})();
