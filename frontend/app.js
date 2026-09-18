/* ThermaSight — vanilla JS application.
 * Talks to the Python (FastAPI) backend, renders every view, and draws the
 * time-series instrument in SVG. Port of the reference React app.
 */
"use strict";

/* ------------------------------ helpers ------------------------------ */

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
const elFrom = (html) => {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
};
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const fmtSig = (v, dp = 1) => {
  if (v === null || v === undefined || !isFinite(v)) return "-";
  return Number(v).toLocaleString("en-US", { maximumFractionDigits: dp, minimumFractionDigits: 0 });
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtTime = (ms) => {
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}, ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
};
const fmtShort = (ms) => {
  const d = new Date(ms);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
};
const fmtAxis = (ms) => {
  const d = new Date(ms);
  return `${MONTHS[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`;
};
const fmtDurationHours = (h) => {
  if (h < 1) return `${Math.round(h * 60)} min`;
  if (h < 48) return `${fmtSig(h, 1)} h`;
  return `${fmtSig(h / 24, 1)} d`;
};
const fmtEnergy = (kwh) => {
  if (Math.abs(kwh) >= 1e6) return `${fmtSig(kwh / 1e6, 2)} GWh`;
  if (Math.abs(kwh) >= 1000) return `${fmtSig(kwh / 1000, 1)} MWh`;
  return `${fmtSig(kwh, 0)} kWh`;
};

const SEVERITY = {
  normal: { label: "Nominal", cls: "ts-chip-ok" },
  watch: { label: "Watch", cls: "ts-chip-warn" },
  alert: { label: "Alert", cls: "ts-chip-alert" },
  action: { label: "Action", cls: "ts-chip-action" },
};
const SEV_CODE = { normal: 0, watch: 1, alert: 2, action: 3 };
const CODE_SEV = ["normal", "watch", "alert", "action"];

const PATTERN_LABEL = {
  contextual_energy_spike: "Energy spike",
  high_consumption_low_load: "Energy up at steady load",
  cooling_water_drift: "Condenser drift",
  flow_imbalance: "Flow imbalance",
  sensor_stuck: "Frozen sensor",
  transient_spike: "Transient",
  degradation_trend: "Degradation trend",
  offhours_standby: "Off-hours draw",
  concurrent_context_shift: "Mode shift",
};
const patternLabel = (t) => PATTERN_LABEL[t] || t.replace(/_/g, " ");

const sevChip = (s) => `<span class="${SEVERITY[s].cls}"><span class="ts-chip-dot"></span>${SEVERITY[s].label}</span>`;
const tagChip = (t) => `<span class="ts-tag">${patternLabel(t)}</span>`;
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ------------------------------ state ------------------------------ */

const state = {
  result: null, // full result payload from /api/result
  resultId: null,
  seriesCache: {}, // eq -> { variable -> payload }
  tab: "overview",
  equipmentId: null,
  variable: "Chiller Energy Consumption",
  selectedEpisodeId: null,
  timeRange: null, // [ms, ms]
  analysing: false,
};

const viewEl = () => $("#view");

async function apiJson(url, opts) {
  const res = await fetch(url, opts);
  const ct = res.headers.get("content-type") || "";
  const body = ct.includes("application/json") ? await res.json() : { detail: await res.text() };
  if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
  return body;
}

async function analyseDemo() {
  const fd = new FormData();
  fd.append("demo", "1");
  await runAnalyse(fd);
}
async function analyseFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  await runAnalyse(fd);
}
async function analyseReal() {
  const fd = new FormData();
  fd.append("dataset", "real");
  await runAnalyse(fd);
}
async function runAnalyse(form) {
  if (state.analysing) return;
  state.analysing = true;
  // a new analysis must never keep the previous dataset's results on screen
  state.result = null;
  state.resultId = null;
  state.seriesCache = {};
  state.equipmentId = null;
  state.selectedEpisodeId = null;
  state.timeRange = null;
  renderHeaderStatus();
  showProgress(true, "");
  try {
    const meta = await apiJson("/api/analyse", { method: "POST", body: form });
    state.resultId = meta.id;
    const result = await apiJson(`/api/result/${meta.id}`);
    state.result = result;
    state.seriesCache = {};
    state.equipmentId = state.equipmentId || result.quality.equipmentIds[0];
    state.selectedEpisodeId = null;
    state.timeRange = null;
    renderHeaderStatus();
    setTab("overview");
  } catch (e) {
    alert(`Analysis failed: ${e.message}`);
  } finally {
    state.analysing = false;
    showProgress(false);
  }
}

async function fetchSeries(eq) {
  const cached = state.seriesCache[eq];
  if (cached && cached[state.variable]) return cached[state.variable];
  const payload = await apiJson(`/api/series/${state.resultId}/${encodeURIComponent(eq)}?variable=${encodeURIComponent(state.variable)}`);
  state.seriesCache[eq] = state.seriesCache[eq] || {};
  state.seriesCache[eq][state.variable] = payload;
  return payload;
}

/* ------------------------------ header ------------------------------ */

function renderHeaderStatus() {
  const s = state.result;
  const h = $("#header-status");
  h.innerHTML = s
    ? `<button class="ts-link ts-home-link" id="btn-home">← home</button>` +
      `<span class="ts-status-chip"><span class="ts-chip-dot ts-chip-dot-ok"></span>${s.quality.equipmentCount} ${s.quality.equipmentCount === 1 ? "unit" : "units"}</span>` +
      `<span class="ts-status-meta mono">${esc(s.quality.fileName)}</span>` +
      `<span class="ts-status-meta mono dim">${s.episodes.length} anomalies</span>`
    : `<span class="ts-status-meta mono dim">no dataset loaded</span>`;
  const hb = $("#btn-home");
  if (hb) {
    hb.onclick = () => {
      state.result = null;
      state.resultId = null;
      state.seriesCache = {};
      state.selectedEpisodeId = null;
      state.timeRange = null;
      renderHeaderStatus();
      setTab("overview");
    };
  }
  const count = $("#tab-anomaly-count");
  if (s && s.episodes.length) {
    count.textContent = s.episodes.length;
    count.hidden = false;
  } else {
    count.hidden = true;
  }
}

function setTab(tab) {
  state.tab = tab;
  $$(".ts-tab").forEach((b) => b.classList.toggle("ts-tab-active", b.dataset.tab === tab));
  renderView();
}

/* ------------------------------ views ------------------------------ */

function renderView() {
  const s = state.result;
  const tab = state.tab;
  if (!s) {
    if (tab === "data") renderData();
    else if (tab === "methodology") renderMethodology();
    else renderHero();
    return;
  }
  if (tab === "overview") renderFleet();
  else if (tab === "explorer") renderExplorer();
  else if (tab === "anomalies") renderAnomalies();
  else if (tab === "methodology") renderMethodology();
  else if (tab === "data") renderData();
}

function renderHero() {
  viewEl().innerHTML = `
    <main class="ts-hero ts-container">
      <div class="grid-hero">
        <div class="max-w-hero">
          <p class="ts-kicker">YUKTHI 2026 · intelligent energy and equipment monitoring</p>
          <h1 class="ts-hero-title">See what your chillers are really doing.</h1>
          <p class="ts-hero-sub">
            Unsupervised ML learns each unit&apos;s normal behaviour from its own history, then flags the deviations
            that matter, with the evidence and the next step.
          </p>
          <div class="ts-hero-cta">
            <button class="ts-btn ts-btn-primary" id="btn-real">Analyse real YUKTHI dataset</button>
            <button class="ts-btn ts-btn-ghost" id="btn-upload">Upload CSV</button>
          </div>
          <p class="ts-hero-note">Run the full 25,003-row YUKTHI development dataset (3 chillers, 10 months), or drop your own CSV into the Data tab.</p>
        </div>
        <div class="ts-schematic-card">
          ${plantSvg()}
          <p class="ts-schematic-caption">The pipeline analyses every unit independently: three chillers, three learned baselines.</p>
        </div>
      </div>
      <section class="ts-how">
        ${howCard("Learn", "Two models per unit: an isolation forest over all measurements, and a network that predicts expected energy from load, water temperatures and ambient conditions.")}
        ${howCard("Detect", "Scores are fused with persistence logic: an isolated blip is noted, a sustained deviation is escalated.")}
        ${howCard("Act", "Each anomaly carries its contributing measurements, a plain-language narrative and evidence-based recommendations for investigation.")}
      </section>
    </main>`;
  $("#btn-real").addEventListener("click", analyseReal);
  $("#btn-upload").addEventListener("click", () => setTab("data"));
}

function howCard(title, body) {
  return `<div class="ts-how-item"><span class="ts-how-icon">●</span><h3 class="ts-how-title">${title}</h3><p class="ts-how-body">${body}</p></div>`;
}

function plantSvg() {
  const lane = (id, y, color) => `
    <path d="M92,${y} H 318" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" opacity="0.5"/>
    <path d="M92,${y} H 318" fill="none" stroke="${color}" stroke-width="5" stroke-dasharray="10 18" opacity="0.8" class="ts-flow"/>
    <rect x="14" y="${y - 16}" width="72" height="32" rx="6" fill="var(--ts-panel2)" stroke="var(--ts-hair)"/>
    <text x="50" y="${y + 4}" text-anchor="middle" class="ts-schematic-label" fill="var(--ts-text)">${id}</text>
    <rect x="322" y="${y - 16}" width="64" height="32" rx="6" fill="var(--ts-panel2)" stroke="var(--ts-hair)"/>
    <text x="354" y="${y + 4}" text-anchor="middle" class="ts-schematic-label" fill="var(--ts-text-dim)">load</text>`;
  return `
    <svg viewBox="0 0 400 236" class="block" style="width:100%;height:auto" aria-hidden="true">
      ${lane("CHILLER-01", 64, "var(--ts-ok)")}
      ${lane("CHILLER-02", 128, "var(--ts-warn)")}
      ${lane("CHILLER-03", 192, "var(--ts-danger)")}
    </svg>`;
}

function renderFleet() {
  const s = state.result;
  const q = s.quality;
  const ids = q.equipmentIds;
  const stat = fleetStats(s);
  const healths = ids.map((eq) => s.equipment[eq].health).filter((h) => h != null);
  const avgHealth = healths.length ? Math.round(healths.reduce((a, b) => a + b, 0) / healths.length) : 0;
  const healthClass = avgHealth >= 75 ? "ts-tx-ok" : avgHealth >= 55 ? "ts-tx-warn" : "ts-tx-danger";
  const riskClass = stat.atRisk.length > 0 ? "ts-tx-danger" : "";
  const queue = [...s.episodes]
    .sort((a, b) => (SEV_CODE[b.severity] - SEV_CODE[a.severity]) || (b.peakScore - a.peakScore))
    .slice(0, 7);

  const kpi = (label, value, sub, extra = "", count = null, fmt = null, cls = "") =>
    `<div class="ts-kpi"><span class="ts-kpi-label">${label}</span><span class="ts-kpi-value mono ${cls}" data-count="${count ?? ""}" data-fmt="${fmt ?? ""}">${value}</span><span class="ts-kpi-sub ${extra}">${sub}</span></div>`;

  const cards = ids
    .map((eq) => {
      const rep = s.equipment[eq];
      const worst = rep.episodes[0];
      return `
      <article class="ts-panel ts-unit-card ts-entrance" data-unit-card="${eq}">
        <div class="flex items-start gap-2">
          <div data-gauge="${eq}"></div>
          <div class="min-w-0 grow">
            <div class="flex items-center gap-2 flex-wrap">
              <h3 class="ts-unit-name mono">${esc(eq)}</h3>
              <span class="ts-unit-dot" style="background:${rep.health == null ? "var(--ts-hair)" : rep.health >= 75 ? "var(--ts-ok)" : rep.health >= 55 ? "var(--ts-warn)" : "var(--ts-danger)"}"></span>
              ${worst ? sevChip(worst.severity) : sevChip("normal")}
            </div>
            <dl class="ts-unit-stats">
              <div><dt>Mean draw</dt><dd class="mono">${fmtSig(rep.energyMeanKwh, 1)} kWh/int</dd></div>
              <div><dt>Anomalies</dt><dd class="mono">${rep.episodes.length}</dd></div>
              <div><dt>Trend</dt><dd class="mono">${rep.degradationTrend > 0.01 ? "+" : ""}${fmtSig(rep.degradationTrend, 2)}</dd></div>
            </dl>
          </div>
        </div>
        <div class="mt-2" data-spark="${eq}"></div>
        <div class="ts-unit-foot">
          <button class="ts-link" data-open-unit="${eq}">Open unit →</button>
          ${worst ? `<button class="ts-link dim" data-open-episode="${worst.id}">${patternLabel(worst.patternTags[0] || "contextual_energy_spike")} · ${fmtTime(worst.startTime)}</button>` : ""}
        </div>
      </article>`;
    })
    .join("");

  const queueRows = queue
    .map(
      (ep) => `
      <li class="ts-entrance"><button class="ts-queue-row ts-queue-${ep.severity}" data-episode-id="${ep.id}">
        <div class="flex items-center gap-2"><span class="mono ts-queue-equip">${esc(ep.equipmentId)}</span>${sevChip(ep.severity)}</div>
        <div class="ts-queue-meta mono">${fmtTime(ep.startTime)} · score ${fmtSig(ep.peakScore, 2)}</div>
        <div class="ts-queue-pattern">${patternLabel(ep.patternTags[0] || "contextual_energy_spike")}</div>
      </button></li>`
    )
    .join("");

  viewEl().innerHTML = `
    <main class="ts-container ts-page">
      <div class="ts-kpi-band">
        ${kpi("Fleet energy", fmtEnergy(stat.energyTotalKwh), "across the observation period", "mono", stat.energyTotalKwh, "fmtEnergy")}
        ${kpi("Anomalies", s.episodes.length,
          `${stat.bySeverity.action ? `<span class="ts-kpi-chip ts-chip-action">${stat.bySeverity.action} action</span>` : ""}${stat.bySeverity.alert ? `<span class="ts-kpi-chip ts-chip-alert">${stat.bySeverity.alert} alert</span>` : ""}${stat.bySeverity.watch ? `<span class="ts-kpi-chip ts-chip-warn">${stat.bySeverity.watch} watch</span>` : ""}`, "", s.episodes.length)}
        ${kpi("Units at risk", `${stat.atRisk.length}<span class="ts-kpi-denom">/${ids.length}</span>`, "health below 60", "mono", stat.atRisk.length, null, riskClass)}
        ${kpi("Fleet health", `${avgHealth}<span class="ts-kpi-denom">/100</span>`, "learned from recent behaviour", "mono", avgHealth, null, healthClass)}
      </div>
      <div class="grid-fleet">
        <div>
          <h2 class="ts-section-title">Equipment</h2>
          <div class="ts-unit-grid">${cards}</div>
        </div>
        <aside>
          <div class="flex items-baseline justify-between">
            <h2 class="ts-section-title">Priority queue</h2>
            <button class="ts-link dim" data-tab-btn="anomalies">view all</button>
          </div>
          <ol class="ts-queue">${queueRows || '<li class="ts-queue-empty mono">no anomalies on record</li>'}</ol>
        </aside>
      </div>
    </main>`;

  $$("[data-count]").forEach((el) => {
    if (el.dataset.count !== "") {
      countUp(el, Number(el.dataset.count), el.dataset.fmt === "fmtEnergy" ? { fmt: fmtEnergy } : {});
    }
  });

  ids.forEach((eq) => {
    Gauge($(`[data-gauge="${eq}"]`), s.equipment[eq].health);
    fetchSeries(eq).then((p) => {
      const holder = $(`[data-spark="${eq}"]`);
      if (holder) Sparkline(holder, p, { color: s.equipment[eq].health != null && s.equipment[eq].health >= 60 ? "var(--ts-accent)" : "var(--ts-warn)" });
    });
  });

  bindEpisodes();
  $$("[data-open-unit]").forEach((b) => b.addEventListener("click", () => openExplorer(b.dataset.openUnit)));
  $$("[data-tab-btn]").forEach((b) => b.addEventListener("click", () => setTab("anomalies")));
}

function fleetStats(s) {
  let energy = 0;
  for (const eq of Object.keys(s.equipment)) energy += s.equipment[eq].energyTotalKwh;
  const bySeverity = { watch: 0, alert: 0, action: 0 };
  const atRisk = [];
  for (const eq of Object.keys(s.equipment)) {
    const rep = s.equipment[eq];
    for (const ep of rep.episodes) bySeverity[ep.severity]++;
    if (rep.health != null && rep.health < 60) atRisk.push(eq);
  }
  return { energyTotalKwh: energy, bySeverity, atRisk };
}

function bindEpisodes() {
  $$("[data-episode-id]").forEach((b) => b.addEventListener("click", () => openEpisode(b.dataset.episodeId)));
}

function openExplorer(eq) {
  state.equipmentId = eq;
  state.timeRange = null;
  setTab("explorer");
}

/* ------------------------------ explorer ------------------------------ */

function renderExplorer() {
  const s = state.result;
  const ids = s.quality.equipmentIds;
  const eq = state.equipmentId || ids[0];
  const rep = s.equipment[eq];
  const variable = state.variable;
  const unit = COLUMN_UNITS[variable] || "";

  const pills = ids
    .map(
      (id) => `
      <button class="ts-pill ${id === eq ? "ts-pill-active" : ""}" data-pill-unit="${id}">
        <span class="ts-pill-dot" style="background:${healthColor(s.equipment[id].health)}"></span>
        <span class="mono">${esc(id)}</span>
      </button>`
    )
    .join("");

  const opts = NUMERIC_COLS.map((c) => `<option value="${c}" ${c === variable ? "selected" : ""}>${c} (${COLUMN_UNITS[c]})</option>`).join("");

  const epRows = rep.episodes.length
    ? rep.episodes
        .map(
          (ep) => `
        <li class="ts-ep-row"><button class="ts-ep-row-main" data-episode-id="${ep.id}">
          ${sevChip(ep.severity)}
          <span class="ts-ep-time mono">${fmtTime(ep.startTime)}</span>
          <span class="ts-ep-detail mono">${fmtDurationHours(ep.durationHours)} · peak ${fmtSig(ep.peakScore, 2)}</span>
          <span class="ts-ep-pattern">${patternLabel(ep.patternTags[0] || "contextual_energy_spike")}</span>
          <span class="ts-ep-chevron">→</span>
        </button></li>`
        )
        .join("")
    : '<p class="ts-empty mono">no anomalies detected for this unit</p>';

  viewEl().innerHTML = `
    <main class="ts-container ts-page">
      <div class="ts-explorer-back">
        <button class="ts-link dim" data-back-overview>← overview</button>
      </div>
      <div class="ts-explorer-controls">
        <div class="ts-pill-group" role="group" aria-label="Equipment">${pills}</div>
        <label class="ts-select-wrap">
          <span class="sr-only">Variable</span>
          <select class="ts-select mono" id="var-select">${opts}</select>
          <span class="ts-select-chevron" aria-hidden="true">⌄</span>
        </label>
        <button class="ts-link dim" id="reset-zoom" hidden>reset zoom</button>
        <span class="ts-utc-note mono">timestamps in UTC</span>
      </div>

      <section class="ts-panel ts-chart-panel">
        <div class="ts-chart-head">
          <div>
            <h2 class="ts-section-title">${esc(variable)} <span class="ts-unit mono">${unit}</span></h2>
            <p class="ts-chart-sub mono">${esc(eq)} · ${fmtSig(rep.count, 0)} observations · anomaly bands drawn from the learned model</p>
          </div>
          <div class="ts-legend">
            <span class="ts-legend-item"><span class="ts-legend-swatch ts-legend-watch"></span> watch</span>
            <span class="ts-legend-item"><span class="ts-legend-swatch ts-legend-alert"></span> alert</span>
            <span class="ts-legend-item"><span class="ts-legend-swatch ts-legend-action"></span> action</span>
          </div>
        </div>
        <div class="ts-chart-wrap" id="chart-holder"><p class="ts-empty mono">loading series…</p></div>
      </section>

      <section class="ts-panel">
        <h2 class="ts-section-title">Anomalies for ${esc(eq)}</h2>
        <ul class="ts-ep-list">${epRows}</ul>
      </section>
    </main>`;

  $$("[data-pill-unit]").forEach((b) =>
    b.addEventListener("click", () => {
      state.equipmentId = b.dataset.pillUnit;
      state.timeRange = null;
      renderExplorer();
    })
  );
  $$("[data-back-overview]").forEach((b) => b.addEventListener("click", () => setTab("overview")));
  $("#var-select").addEventListener("change", (e) => {
    state.variable = e.target.value;
    renderExplorer();
  });
  $("#reset-zoom").addEventListener("click", () => {
    state.timeRange = null;
    renderExplorer();
  });
  bindEpisodes();

  fetchSeries(eq).then((payload) => {
    const holder = $("#chart-holder");
    if (!holder) return;
    holder.innerHTML = "";
    const bands = rep.episodes.map((ep) => ({
      id: ep.id,
      startIndex: ep.startIndex,
      endIndex: ep.endIndex,
      severity: ep.severity,
    }));
    TimeSeriesChart(holder, {
      times: payload.times,
      values: payload.values,
      severityCodes: payload.severityCodes,
      bands,
      range: state.timeRange,
      onRangeChange: (r) => {
        state.timeRange = r;
        $("#reset-zoom").hidden = !r;
      },
      onOpenBand: (id) => openEpisode(id),
      unit,
      label: variable,
    });
  });
}

/* ------------------------------ anomalies ------------------------------ */

function renderAnomalies() {
  const s = state.result;
  const filter = state.anomalyFilter || "all";
  const counts = { all: s.episodes.length, action: 0, alert: 0, watch: 0 };
  for (const e of s.episodes) counts[e.severity]++;
  const list = s.episodes.filter((e) => filter === "all" || e.severity === filter);

  const pills = ["all", "action", "alert", "watch"]
    .map(
      (f) => `
      <button class="ts-pill ${filter === f ? "ts-pill-active" : ""}" data-filter="${f}">
        <span class="mono">${f}</span><span class="ts-pill-count mono">${counts[f]}</span>
      </button>`
    )
    .join("");

  const cards = list
    .map(
      (ep) => `
      <li class="ts-entrance"><button class="ts-anomaly-card" data-episode-id="${ep.id}">
        <div class="flex items-center gap-2"><span class="mono ts-anomaly-equip">${esc(ep.equipmentId)}</span>${sevChip(ep.severity)}</div>
        <div class="ts-anomaly-time mono">${fmtTime(ep.startTime)} · ${fmtDurationHours(ep.durationHours)}</div>
        <div class="ts-anomaly-meta">${ep.patternTags.slice(0, 2).map(tagChip).join("")}<span class="ts-anomaly-score mono">peak ${fmtSig(ep.peakScore, 2)}</span></div>
        <span class="ts-anomaly-chevron">→</span>
      </button></li>`
    )
    .join("");

  viewEl().innerHTML = `
    <main class="ts-container ts-page">
      <div class="flex items-center justify-between gap-2 flex-wrap">
        <h2 class="ts-section-title">Anomalies</h2>
        <div class="ts-pill-group" role="group" aria-label="Filter by severity">${pills}</div>
      </div>
      ${list.length ? `<ul class="ts-anomaly-list mt-3">${cards}</ul>` : '<p class="ts-empty mono">no anomalies with this filter</p>'}
    </main>`;

  $$("[data-filter]").forEach((b) =>
    b.addEventListener("click", () => {
      state.anomalyFilter = b.dataset.filter;
      renderAnomalies();
    })
  );
  bindEpisodes();
}

/* ------------------------------ methodology ------------------------------ */

function renderMethodology() {
  const s = state.result;
  const steps = [
    ["Ingest", "Any CSV conforming to the YUKTHI 2026 data contract. Columns are located by name, records are keyed by (equipment_id, timestamp), and each unit becomes its own chronological series."],
    ["Quality", "Missing values are counted per column, duplicate pairs are detected, irregular gaps are measured. Nothing is hard-coded about row counts or specific timestamps."],
    ["Features", "Short gaps are interpolated, longer gaps carry forward. Time-of-day and day-of-week are encoded as cycles; trailing 24-hour rolling statistics and lags describe recent dynamics without leaking the future."],
    ["Learn", "Two models per unit: an isolation forest finds structurally unusual combinations of measurements, and a small network reconstructs expected energy from operating context."],
    ["Detect", "Isolation score and contextual residual are fused, calibrated per unit, and filtered through persistence logic: isolated blips stay low, sustained deviations escalate."],
    ["Interpret", "Every episode gets its contributing measurements, a pattern classification, a plain-language narrative and evidence-based recommended next steps."],
  ];
  const model = s ? s.model : null;

  viewEl().innerHTML = `
    <main class="ts-container ts-page">
      <h2 class="ts-section-title">How the analysis works</h2>
      <p class="ts-method-lead">
        The pipeline is a reusable data-to-insight chain implemented in Python (numpy). It learns each unit&apos;s own
        normal, so the same code runs unchanged on the demo data or a participant CSV that follows the contract.
      </p>
      <ol class="ts-steps">
        ${steps.map((st, i) => `<li class="ts-step"><span class="ts-step-idx mono">${String(i + 1).padStart(2, "0")}</span><div><h3 class="ts-step-title">${st[0]}</h3><p class="ts-step-body">${st[1]}</p></div></li>`).join("")}
      </ol>
      <section class="ts-panel ts-model-panel">
        <h2 class="ts-section-title">The machine-learning layer</h2>
        <div class="grid-models">
          <article class="ts-model-card">
            <h3 class="ts-model-title">Isolation forest</h3>
            <p class="ts-model-body">
              Grows random partition trees per unit and measures how few splits a row needs to be isolated. Rows that
              isolate quickly are structurally unusual across <em>all</em> measurements at once.
            </p>
            ${model ? `<dl class="ts-model-stats mono"><div><dt>Trees per unit</dt><dd>${model.isolationForest.trees}</dd></div><div><dt>Sample size</dt><dd>${model.isolationForest.maxSamples}</dd></div><div><dt>Features</dt><dd>${model.isolationForest.features}</dd></div></dl>` : ""}
          </article>
          <article class="ts-model-card">
            <h3 class="ts-model-title">Contextual residual network</h3>
            <p class="ts-model-body">
              A small feed-forward network learns expected energy from load, water temperatures, ambient conditions and
              recent dynamics. The residual (actual minus expected) is the contextual anomaly signal.
            </p>
            ${model ? `<dl class="ts-model-stats mono"><div><dt>Hidden units</dt><dd>${model.residualModel.hiddenUnits}</dd></div><div><dt>Features</dt><dd>${model.residualModel.features}</dd></div><div><dt>Epochs</dt><dd>${model.residualModel.epochs}</dd></div></dl>` : ""}
          </article>
        </div>
        ${model ? `<div class="ts-fusion"><span class="ts-fusion-label mono">fusion</span><span class="mono">score = ${fmtSig(model.fusion.ifWeight, 2)} × isolation + ${fmtSig(model.fusion.residualWeight, 2)} × residual, threshold at the ${fmtSig(model.fusion.thresholdQuantile * 100, 1)}th percentile, runs joined within ${model.fusion.joinWindow} intervals</span><span class="ts-fusion-timing mono">runtime ${fmtSig(model.runtimeMs / 1000, 1)} s</span></div>` : ""}
      </section>
      <section class="ts-panel">
        <h2 class="ts-section-title">Honest scope</h2>
        <ul class="ts-scope-list">
          <li>The development dataset has no fault labels, so the system is explicitly <em>unsupervised</em>: it flags deviations from learned normal behaviour. It names the evidence and the likely pattern class; it does not claim a specific physical fault unless the data pattern supports one.</li>
          <li>Detected episodes (like every mechanism here) are per equipment unit, so behavioural differences between units are respected instead of being misread as anomalies.</li>
          <li>Gaps are treated as missing context, not as faults, per the data specification. The model never sees future observations when scoring a point (trailing windows only).</li>
        </ul>
      </section>
    </main>`;
}

/* ------------------------------ data view ------------------------------ */

function renderData() {
  const s = state.result;
  const q = s ? s.quality : null;
  const error = state.dataError || "";

  const dropzoneHtml = `
    <section class="ts-panel ts-dropzone-wrap">
      <div class="ts-dropzone" id="dropzone">
        <span class="ts-dropzone-icon" aria-hidden="true">⬆</span>
        <p class="ts-dropzone-title mono">drop a CSV, or click to browse</p>
        <p class="ts-dropzone-body">
          The parser reads columns by name: timestamp, equipment_id, then the nine measurements from the YUKTHI 2026
          data specification. Blank cells become missing values.
        </p>
        <input type="file" id="file-input" accept=".csv,text/csv,text/plain" class="sr-only" />
        <button class="ts-btn ts-btn-primary" id="btn-browse">Upload CSV</button>
      </div>
      <div class="ts-dropzone-alt">
        <span class="ts-dropzone-body">No file handy?</span>
        <button class="ts-link" id="btn-real-alt">Analyse the bundled YUKTHI development dataset (25,003 rows)</button>
        <button class="ts-link" id="btn-demo-alt">Analyse the built-in demo dataset</button>
      </div>
    </section>`;

  const qualityHtml = q
    ? `
    <section class="ts-panel">
      <div class="flex items-center justify-between gap-2">
        <h2 class="ts-section-title">Quality report</h2>
        <button class="ts-link dim" id="btn-clear">clear dataset</button>
      </div>
      <dl class="ts-q-grid mono">
        <div><dt>File</dt><dd>${esc(q.fileName)}</dd></div>
        <div><dt>Observations</dt><dd>${fmtSig(q.rowCount, 0)}</dd></div>
        <div><dt>Units</dt><dd>${q.equipmentIds.map(esc).join(", ")}</dd></div>
        <div><dt>Period</dt><dd>${fmtShort(q.periodStart)} to ${fmtShort(q.periodEnd)}</dd></div>
        <div><dt>Duplicate pairs</dt><dd>${q.duplicatePairs}</dd></div>
        <div><dt>Gaps</dt><dd>${q.gapCount} (longest ${fmtDurationHours(q.maxGapHours)})</dd></div>
      </dl>
      <h3 class="ts-q-sub">Missing values by column</h3>
      <div class="ts-q-missing">
        ${NUMERIC_COLS.map((c) => {
          const n = q.missingByColumn[c] || 0;
          return `<div class="ts-q-missing-row"><span>${c}</span><span class="mono ${n > 0 ? "ts-tx-warn" : "ts-tx-ok"}">${n > 0 ? `${n} (${((n / q.rowCount) * 100).toFixed(3)}%)` : "0"}</span></div>`;
        }).join("")}
      </div>
      ${q.missingColumns && q.missingColumns.length ? `<div class="ts-q-absent mono ts-tx-warn">⚠ entire columns absent from file (treated as fully missing, excluded from modelling): ${q.missingColumns.join(", ")}</div>` : ""}
      <div class="ts-q-foot mono">
        ${q.duplicatePairs === 0 ? '<span class="ts-tx-ok">✓ record identity clean</span>' : `<span class="ts-tx-warn">⚠ duplicate pairs found, first occurrence kept</span>`}
        ${q.badRows > 0 ? `<span class="ts-tx-warn">⚠ ${q.badRows} rows skipped (invalid timestamp or unit)</span>` : ""}
        <span>nominal interval ${state.result.nominalIntervalMinutes} min</span>
      </div>
      ${q.fileName === "yukthi-demo-chillers.csv" ? `<p class="ts-demo-note">This is the synthetic demo dataset, generated to match the specification (same columns, units, cadence and missing-value rates) with known fault scenarios embedded. Upload the participant CSV in its place; the pipeline does not change.</p>` : ""}
      <button class="ts-link dim mt-3" id="btn-open-explorer">open the explorer with this dataset →</button>
    </section>`
    : "";

  viewEl().innerHTML = `
    <main class="ts-container ts-page">
      <h2 class="ts-section-title">Data source</h2>
      ${dropzoneHtml}
      ${error ? `<div class="ts-alert" role="alert">⚠ <p>${esc(error)}</p></div>` : ""}
      ${qualityHtml}
      <p class="ts-utc-note mono mt-3">all timestamps rendered in the UTC frame of the pipeline</p>
    </main>`;

  const dropzone = $("#dropzone");
  const input = $("#file-input");
  $("#btn-browse").addEventListener("click", () => input.click());
  $("#btn-real-alt").addEventListener("click", analyseReal);
  $("#btn-demo-alt").addEventListener("click", analyseDemo);
  input.addEventListener("change", (e) => {
    const f = e.target.files && e.target.files[0];
    e.target.value = "";
    if (f) readAndAnalyse(f);
  });
  ["dragover", "dragenter"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      dropzone.classList.add("ts-dropzone-over");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      dropzone.classList.remove("ts-dropzone-over");
    })
  );
  dropzone.addEventListener("drop", (e) => {
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) readAndAnalyse(f);
  });
  if ($("#btn-clear")) {
    $("#btn-clear").addEventListener("click", () => {
      state.result = null;
      state.resultId = null;
      state.seriesCache = {};
      state.timeRange = null;
      state.dataError = "";
      renderHeaderStatus();
      setTab("overview");
    });
  }
  if ($("#btn-open-explorer")) {
    $("#btn-open-explorer").addEventListener("click", () => setTab("explorer"));
  }
}

async function readAndAnalyse(file) {
  state.dataError = "";
  try {
    await analyseFile(file);
  } catch (e) {
    state.dataError = e.message;
    renderData();
  }
}

/* ------------------------------ drawer ------------------------------ */

function tsDrawerEsc(e) {
  if (e.key === "Escape") closeDrawer();
}

function openEpisode(episodeId) {
  if (!state.result) return;
  const s = state.result;
  const all = s.episodes;
  let ep = all.find((e) => e.id === episodeId);
  if (!ep) {
    // fall back to per-unit list
    for (const eq of Object.keys(s.equipment)) {
      ep = s.equipment[eq].episodes.find((e) => e.id === episodeId);
      if (ep) break;
    }
  }
  if (!ep) return;
  state.selectedEpisodeId = episodeId;
  $("#drawer-head").innerHTML = `<span class="mono ts-drawer-equip">${esc(ep.equipmentId)}</span>${sevChip(ep.severity)}`;

  const recs = ep.recommendations
    .map(
      (r) => `
      <li class="ts-rec">
        <span class="ts-tag ${r.priority === "high" ? "ts-prio-high" : r.priority === "medium" ? "ts-prio-medium" : "ts-prio-low"}">${r.priority} priority</span>
        <div><p class="ts-rec-action">${esc(r.action)}</p><p class="ts-rec-rationale">${esc(r.rationale)}</p></div>
      </li>`
    )
    .join("");

  const factors = ep.contributors.length
    ? ep.contributors
        .map((c) => {
          const w = Math.max(6, Math.min(100, Math.abs(c.z) * 16));
          const color = Math.abs(c.z) >= 2.5 ? "var(--ts-danger)" : Math.abs(c.z) >= 1.8 ? "var(--ts-warn)" : "var(--ts-accent)";
          return `
          <div class="ts-factor">
            <div class="flex items-baseline justify-between gap-2"><span class="ts-factor-name">${c.column}</span><span class="ts-factor-val mono">${c.direction === "high" ? "+" : "−"}${fmtSig(Math.abs(c.z), 1)} σ</span></div>
            <div class="ts-factor-track"><div class="ts-factor-fill" style="width:${w}%;background:${color}"></div></div>
            <span class="ts-factor-meta mono">${fmtSig(c.value, 2)} ${c.unit} vs baseline ${fmtSig(c.baselineMedian, 2)} ${c.unit}</span>
          </div>`;
        })
        .join("")
    : '<p class="ts-drawer-note">No baseline evidence available for this window.</p>';

  const evidence = ep.evidence.length
    ? `<table class="ts-table">
        <thead><tr><th>Measurement</th><th class="text-right">Observed</th><th class="text-right">Expected</th><th class="text-right">Deviation</th></tr></thead>
        <tbody>
          ${ep.evidence
            .map(
              (e) => `<tr><td>${e.column}</td><td class="mono text-right">${fmtSig(e.observed, 2)}</td><td class="mono text-right">${fmtSig(e.expected, 2)}</td><td class="mono text-right ${Math.abs(e.z) >= 2 ? "ts-tx-danger" : Math.abs(e.z) >= 1.5 ? "ts-tx-warn" : ""}">${e.z > 0 ? "+" : ""}${fmtSig(e.z, 1)} σ</td></tr>`
            )
            .join("")}
        </tbody>
      </table>
      <p class="ts-drawer-note">Expected values are the unit&apos;s own recent baseline (12-hour trailing median), so the comparison is contextual, not absolute.</p>`
    : "";

  $("#drawer-body").innerHTML = `
    <dl class="ts-drawer-meta mono">
      <div><dt>Window</dt><dd>${fmtTime(ep.startTime)} to ${fmtTime(ep.endTime)}</dd></div>
      <div><dt>Duration</dt><dd>${fmtDurationHours(ep.durationHours)}</dd></div>
      <div><dt>Peak score</dt><dd>${fmtSig(ep.peakScore, 3)}</dd></div>
      <div><dt>Severity</dt><dd>${SEVERITY[ep.severity].label}</dd></div>
    </dl>
    <section class="ts-drawer-section">
      <h3 class="ts-drawer-h">What happened</h3>
      <p class="ts-drawer-narrative">${esc(ep.narrative)}</p>
      ${ep.patternTags.length ? `<div class="flex flex-wrap gap-1">${ep.patternTags.map(tagChip).join("")}</div>` : ""}
    </section>
    <section class="ts-drawer-section">
      <h3 class="ts-drawer-h">Contributing measurements</h3>
      <div class="ts-factors">${factors}</div>
    </section>
    ${evidence ? `<section class="ts-drawer-section"><h3 class="ts-drawer-h">Evidence</h3>${evidence}</section>` : ""}
    <section class="ts-drawer-section">
      <h3 class="ts-drawer-h">Recommended next steps</h3>
      <ol class="ts-recs">${recs}</ol>
    </section>`;

  document.addEventListener("keydown", tsDrawerEsc, { once: true });
  $("#drawer-layer").hidden = false;
  document.body.style.overflow = "hidden";
  const cx = $("#drawer-close-x");
  if (cx) cx.focus();
}

function closeDrawer() {
  $("#drawer-layer").hidden = true;
  document.body.style.overflow = "";
  state.selectedEpisodeId = null;
}

/* ------------------------------ progress ------------------------------ */

function countUp(el, to, { dur = 700, fmt } = {}) {
  const target = Number(to);
  if (!Number.isFinite(target)) return;
  const f = fmt || ((v) => v.toLocaleString("en-US"));
  const t0 = performance.now();
  const ease = (t) => 1 - Math.pow(1 - t, 3);
  const tick = (now) => {
    const p = Math.min(1, (now - t0) / dur);
    el.textContent = f(Math.round(target * ease(p)));
    if (p < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function showProgress(show, detail) {
  const layer = $("#progress-layer");
  layer.hidden = !show;
  clearInterval(window.__tsProgressTimer);
  if (show) {
    const log = $("#progress-log");
    const pct = $("#progress-pct");
    const fill = $("#progress-fill");
    if (log) log.innerHTML = "";
    const stages = [
      "parsing csv contract",
      "imputing gaps · building features",
      "training per-unit models",
      "scoring · fusing evidence",
      "interpreting episodes",
      "assembling report",
    ];
    let i = 0;
    const t0 = Date.now();
    window.__tsProgressTimer = setInterval(() => {
      const s = Math.floor((Date.now() - t0) / 1000);
      pct.textContent = `t+${s}s · pipeline server-side`;
    }, 250);
    const pushStage = () => {
      if (i >= stages.length || !log) return;
      const li = document.createElement("li");
      li.textContent = stages[i];
      log.appendChild(li);
      log.scrollTop = log.scrollHeight;
      i++;
      if (fill) fill.style.width = Math.min(96, 8 + i * 15) + "%";
      setTimeout(pushStage, 780 + Math.random() * 420);
    };
    setTimeout(pushStage, 140);
  }
}

/* ------------------------------ chart engine ------------------------------ */

function Sparkline(holder, payload, opts = {}) {
  const width = 260;
  const height = 48;
  const times = payload.times;
  const values = payload.values;
  const ids = payload.severityCodes;
  const color = opts.color || cssVar("--ts-accent");
  const flagColor = cssVar("--ts-danger");
  const t = [];
  const v = [];
  const n = times.length;
  const stride = Math.max(1, Math.ceil(n / width));
  for (let k = 0; k < n; k += stride) {
    let lo = Infinity;
    let hi = -Infinity;
    let tLo = 0;
    let tHi = 0;
    for (let i = k; i < Math.min(n, k + stride); i++) {
      const x = values[i];
      if (x === null || x === undefined) continue;
      if (x < lo) { lo = x; tLo = times[i]; }
      if (x > hi) { hi = x; tHi = times[i]; }
    }
    if (lo !== Infinity) {
      t.push(tLo, tHi);
      v.push(lo, hi);
    }
  }
  if (!t.length) {
    holder.innerHTML = "";
    return;
  }
  const lo = Math.min(...v);
  const hi = Math.max(...v);
  const span = hi - lo || 1;
  const X = (ms) => ((ms - t[0]) / (t[t.length - 1] - t[0] || 1)) * (width - 2) + 1;
  const Y = (val) => 2 + (1 - (val - lo) / span) * (height - 4);
  const path = t.map((ms, i) => `${i ? "L" : "M"}${X(ms).toFixed(1)},${Y(v[i]).toFixed(1)}`).join("");
  const flags = ids
    .map((code, i) => (code > 0 ? `${X(times[i]).toFixed(1)},${height - 4}` : null))
    .filter(Boolean)
    .join(" ");
  holder.innerHTML = `
    <svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true" class="block" style="width:100%;height:auto">
      <path d="${path} L${X(t[t.length - 1])},${height} L${X(t[0])},${height} Z" fill="${color}" opacity="0.18"/>
      <path d="${path}" fill="none" stroke="${color}" stroke-width="1.3" vector-effect="non-scaling-stroke"/>
      ${flags ? `<polygon points="${flags}" fill="${flagColor}" opacity="0.9"/>` : ""}
    </svg>`;
}

function Gauge(holder, value) {
  if (value == null) {
    holder.innerHTML = `<span class="ts-gauge-na mono dim" style="display:flex;width:84px;height:84px;align-items:center;justify-content:center">—&nbsp;n/a</span>`;
    return;
  }
  const size = 84;
  const v = Math.max(0, Math.min(100, Math.round(value)));
  const stroke = 8;
  const r = (size - stroke) / 2;
  const cx = size / 2;
  const cy = size / 2;
  const start = Math.PI * 0.75;
  const sweep = Math.PI * 1.5;
  const arc = (frac) => {
    const a = start + sweep * Math.max(0, Math.min(1, frac));
    const x0 = cx + r * Math.cos(start);
    const y0 = cy + r * Math.sin(start);
    const x1 = cx + r * Math.cos(a);
    const y1 = cy + r * Math.sin(a);
    const large = a - start > Math.PI ? 1 : 0;
    return `M${x0.toFixed(2)},${y0.toFixed(2)} A${r},${r} 0 ${large} 1 ${x1.toFixed(2)},${y1.toFixed(2)}`;
  };
  const color = v >= 75 ? cssVar("--ts-ok") : v >= 55 ? cssVar("--ts-warn") : cssVar("--ts-danger");
  holder.innerHTML = `
    <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="health ${v}/100">
      <path d="${arc(1)}" fill="none" stroke="${cssVar("--ts-hair")}" stroke-width="${stroke}" stroke-linecap="round"/>
      <path d="${arc(v / 100)}" fill="none" stroke="${color}" stroke-width="${stroke}" stroke-linecap="round"/>
      <text x="${cx}" y="${cy - 2}" text-anchor="middle" class="ts-gauge-num" fill="${cssVar("--ts-text")}">${v}</text>
      <text x="${cx}" y="${cy + 16}" text-anchor="middle" class="ts-gauge-cap" fill="${cssVar("--ts-text-dim")}">health</text>
    </svg>`;
}

const CHART_W = 920;
const CHART_H = 300;
const CHART_PAD = { t: 14, r: 14, b: 26, l: 46 };
const CHART_INNER_W = CHART_W - CHART_PAD.l - CHART_PAD.r;
const CHART_INNER_H = CHART_H - CHART_PAD.t - CHART_PAD.b;
const SEV_CHART = {
  normal: "transparent",
  watch: "#f5b84b",
  alert: "#f96f4b",
  action: "#e5484d",
};
const SEV_OPACITY = { normal: 0, watch: 0.07, alert: 0.12, action: 0.2 };

function niceTicks(lo, hi, maxN) {
  const span = hi - lo;
  if (!(span > 0) || !isFinite(span)) return [lo];
  const rawStep = span / maxN;
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const norm = rawStep / mag;
  const step = (norm < 1.5 ? 1 : norm < 3.5 ? 2 : norm < 7.5 ? 5 : 10) * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) out.push(v);
  return out;
}

function TimeSeriesChart(holder, props) {
  const { times, values, severityCodes, bands, range, onRangeChange, onOpenBand, unit, label } = props;
  const domain = range || [times[0], times[times.length - 1]];
  const fullDomain = [times[0], times[times.length - 1]];
  const x = (ms) => CHART_PAD.l + ((ms - domain[0]) / (domain[1] - domain[0] || 1)) * CHART_INNER_W;

  // min/max sampling per ~pixel
  const sampled = sampleChart(times, values, 1200);
  let lo = Infinity;
  let hi = -Infinity;
  const pts = [];
  for (const [i, val] of sampled) {
    if (times[i] >= domain[0] && times[i] <= domain[1]) {
      pts.push([i, val]);
      if (val < lo) lo = val;
      if (val > hi) hi = val;
    }
  }
  if (!isFinite(lo)) { lo = 0; hi = 1; }
  const padY = (hi - lo || 1) * 0.08;
  const yLo = lo - padY;
  const yHi = hi + padY;
  const y = (val) => CHART_PAD.t + (1 - (val - yLo) / (yHi - yLo || 1)) * CHART_INNER_H;

  const linePath = pts.map(([i, val], k) => `${k ? "L" : "M"}${x(times[i]).toFixed(1)},${y(val).toFixed(1)}`).join("");
  const areaPath = pts.length
    ? `${linePath} L${x(times[pts[pts.length - 1][0]])},${CHART_PAD.t + CHART_INNER_H} L${x(times[pts[0][0]])},${CHART_PAD.t + CHART_INNER_H} Z`
    : "";
  const yTicks = niceTicks(yLo, yHi, 4);
  const xTicks = niceTicks(domain[0], domain[1], 6).filter((v) => v >= domain[0] && v <= domain[1]);

  const visibleBands = bands.filter((b) => times[b.startIndex] <= domain[1] && times[b.endIndex] >= domain[0]);
  const axLabel = cssVar("--ts-text-mut") || "#5d7184";
  const grid = cssVar("--ts-grid") || "rgba(159,176,193,0.09)";

  let html = `<svg viewBox="0 0 ${CHART_W} ${CHART_H}" class="block" style="width:100%;height:auto" role="img" aria-label="${esc(label)} time series with anomaly bands">`;
  for (const tv of yTicks) {
    html += `<line x1="${CHART_PAD.l}" x2="${CHART_W - CHART_PAD.r}" y1="${y(tv)}" y2="${y(tv)}" stroke="${grid}"/><text x="${CHART_PAD.l - 8}" y="${y(tv) + 4}" text-anchor="end" class="ts-axis-label">${fmtSig(tv, 1)}</text>`;
  }
  for (const tv of xTicks) {
    html += `<line x1="${x(tv)}" x2="${x(tv)}" y1="${CHART_PAD.t}" y2="${CHART_PAD.t + CHART_INNER_H}" stroke="${grid}"/><text x="${x(tv)}" y="${CHART_H - 8}" text-anchor="middle" class="ts-axis-label">${fmtAxis(tv)}</text>`;
  }
  for (const b of visibleBands) {
    const x0 = x(times[b.startIndex]);
    const x1 = x(times[b.endIndex]);
    const w = Math.max(2, x1 - x0);
    const color = SEV_CHART[b.severity];
    html += `<g data-band="${b.id}" style="cursor:pointer"><rect x="${x0}" y="${CHART_PAD.t}" width="${w}" height="${CHART_INNER_H}" fill="${color}" opacity="${SEV_OPACITY[b.severity]}"/><line x1="${x0}" x2="${x1}" y1="${CHART_PAD.t + 3}" y2="${CHART_PAD.t + 3}" stroke="${color}" stroke-width="2" opacity="0.9"/></g>`;
  }
  html += `<path d="${areaPath}" fill="${cssVar("--ts-accent")}" opacity="0.12"/>`;
  html += `<path d="${linePath}" fill="none" stroke="${cssVar("--ts-accent")}" stroke-width="1.6"/>`;
  if (visibleBands.length < 40) {
    for (const b of bands) {
      const mid = Math.round((b.startIndex + b.endIndex) / 2);
      const ms = times[mid];
      if (ms < domain[0] || ms > domain[1]) continue;
      const vv = values[mid] === null || values[mid] === undefined ? lo : values[mid];
      html += `<circle cx="${x(ms).toFixed(1)}" cy="${y(vv).toFixed(1)}" r="2.6" fill="${SEV_CHART[b.severity]}" opacity="0.95"/>`;
    }
  }
  html += `<g id="chart-crosshair" pointer-events="none"></g>`;
  html += `</svg>`;

  // brush
  const brushX0 = CHART_PAD.l + (((range ? range[0] : fullDomain[0]) - fullDomain[0]) / (fullDomain[1] - fullDomain[0] || 1)) * CHART_INNER_W;
  const brushX1 = CHART_PAD.l + (((range ? range[1] : fullDomain[1]) - fullDomain[0]) / (fullDomain[1] - fullDomain[0] || 1)) * CHART_INNER_W;
  html += `
    <svg viewBox="0 0 ${CHART_W} 34" class="ts-brush" style="width:100%;height:34px;display:block;touch-action:none">
      <rect x="${CHART_PAD.l}" y="6" width="${CHART_INNER_W}" height="22" rx="4" fill="${cssVar("--ts-panel2")}" stroke="${cssVar("--ts-hair")}"/>
      <rect x="${brushX0}" y="6" width="${Math.max(8, brushX1 - brushX0)}" height="22" rx="4" fill="${cssVar("--ts-accent-soft2")}" stroke="${cssVar("--ts-accent")}" stroke-width="1" data-brushmode="pan" style="cursor:${range ? "grab" : "default"}"/>
      <rect x="${brushX0 - 4}" y="6" width="8" height="22" rx="3" fill="${cssVar("--ts-accent")}" data-brushmode="left" style="cursor:ew-resize"/>
      <rect x="${brushX1 - 4}" y="6" width="8" height="22" rx="3" fill="${cssVar("--ts-accent")}" data-brushmode="right" style="cursor:ew-resize"/>
      ${range ? "" : `<text x="${CHART_W / 2}" y="21" text-anchor="middle" class="ts-axis-label">drag to zoom</text>`}
    </svg>`;

  holder.innerHTML = `<div class="flex items-baseline justify-between mt-2"><span class="ts-chart-sub mono">${esc(label)} · ${esc(unit)} · hover for values · click a band to investigate</span></div>` + html;

  const chartSvg = holder.querySelector("svg");
  const brushSvg = holder.querySelector(".ts-brush");
  const crosshair = holder.querySelector("#chart-crosshair");
  const drag = { mode: null, x0: 0, r0: null };

  const toX = (ev) => {
    const rect = brushSvg.getBoundingClientRect();
    return Math.max(0, Math.min(CHART_W, ((ev.clientX - rect.left) / rect.width) * CHART_W));
  };

  brushSvg.querySelectorAll("[data-brushmode]").forEach((node) => {
    node.addEventListener("pointerdown", (ev) => {
      if (node.dataset.brushmode === "pan" && !range) return;
      drag.mode = node.dataset.brushmode;
      drag.x0 = toX(ev);
      drag.r0 = range ? [range[0], range[1]] : [fullDomain[0], fullDomain[1]];
      node.setPointerCapture(ev.pointerId);
      ev.preventDefault();
    });
  });
  brushSvg.addEventListener("pointermove", (ev) => {
    if (!drag.mode) return;
    const dx = toX(ev) - drag.x0;
    const msPerPx = (fullDomain[1] - fullDomain[0]) / CHART_INNER_W;
    const deltaMs = dx * msPerPx;
    const [f0, f1] = fullDomain;
    if (drag.mode === "pan") {
      const span = drag.r0[1] - drag.r0[0];
      let n0 = drag.r0[0] - deltaMs;
      n0 = Math.max(f0, Math.min(n0, f1 - span));
      onRangeChange([n0, n0 + span]);
    } else {
      const span = drag.r0[1] - drag.r0[0];
      const minSpan = Math.min(span * 0.1, 24 * 3600000);
      if (drag.mode === "left") {
        const n0 = Math.max(f0, Math.min(drag.r0[0] + deltaMs, drag.r0[1] - minSpan));
        onRangeChange([n0, drag.r0[1]]);
      } else {
        const n1 = Math.min(f1, Math.max(drag.r0[0] + minSpan, drag.r0[1] + deltaMs));
        onRangeChange([drag.r0[0], n1]);
      }
    }
  });
  const endDrag = () => (drag.mode = null);
  brushSvg.addEventListener("pointerup", endDrag);
  brushSvg.addEventListener("pointercancel", endDrag);

  chartSvg.querySelectorAll("[data-band]").forEach((g) =>
    g.addEventListener("click", () => onOpenBand(g.dataset.band))
  );

  // crosshair
  chartSvg.addEventListener("pointermove", (ev) => {
    const rect = chartSvg.getBoundingClientRect();
    const px = ((ev.clientX - rect.left) / rect.width) * CHART_W;
    const ms = domain[0] + ((px - CHART_PAD.l) / CHART_INNER_W) * (domain[1] - domain[0]);
    let a = 0;
    let b = times.length - 1;
    while (a < b - 1) {
      const m = (a + b) >> 1;
      if (times[m] < ms) a = m;
      else b = m;
    }
    const i = Math.abs(times[a] - ms) < Math.abs(times[b] - ms) ? a : b;
    if (times[i] < domain[0] || times[i] > domain[1]) return;
    const hx = x(times[i]);
    const hv = values[i];
    const hy = hv === null || hv === undefined ? (lo + hi) / 2 : y(hv);
    const band = bands.find((bb) => i >= bb.startIndex && i <= bb.endIndex);
    const win = values.slice(Math.max(0, i - 48), i).filter((v) => v !== null && v !== undefined);
    let delta = null;
    if (hv !== null && hv !== undefined && win.length >= 8) {
      const sorted = win.slice().sort((a, b) => a - b);
      delta = hv - sorted[sorted.length >> 1];
    }
    const tipH = band ? (delta != null ? 66 : 52) : delta != null ? 52 : 38;
    const tooltipX = Math.min(Math.max(hx - 90, CHART_PAD.l), CHART_W - CHART_PAD.r - 150);
    crosshair.innerHTML = `
      <line x1="${hx}" x2="${hx}" y1="${CHART_PAD.t}" y2="${CHART_PAD.t + CHART_INNER_H}" stroke="${axLabel}" stroke-width="1" stroke-dasharray="3 3"/>
      <circle cx="${hx}" cy="${hy}" r="3.4" fill="${cssVar("--ts-accent")}" stroke="${cssVar("--ts-bg")}" stroke-width="1.5"/>
      <g transform="translate(${tooltipX}, ${CHART_PAD.t + 4})">
        <rect width="150" height="${tipH}" rx="6" fill="${cssVar("--ts-panel2")}" stroke="${cssVar("--ts-hair")}"/>
        <text x="10" y="18" class="ts-tooltip-main">${fmtTime(times[i])}</text>
        <text x="10" y="34" class="ts-tooltip-sub">${hv === null || hv === undefined ? "-" : Number(hv).toLocaleString("en-US", { maximumFractionDigits: 2 })} ${unit}</text>
        ${delta != null ? `<text x="10" y="${band ? 50 : 48}" class="ts-tooltip-sub" fill="${delta >= 0 ? cssVar("--ts-alert") : cssVar("--ts-ok")}">Δ ${delta >= 0 ? "+" : "−"}${Math.abs(Number(delta)).toLocaleString("en-US", { maximumFractionDigits: 2 })} vs 24 h baseline</text>` : ""}
        ${band ? `<text x="10" y="${delta != null ? 64 : 48}" class="ts-tooltip-sub" fill="${SEV_CHART[band.severity]}">${SEVERITY[band.severity].label} anomaly</text>` : ""}
      </g>`;
  });
  chartSvg.addEventListener("pointerleave", () => (crosshair.innerHTML = ""));
}

function sampleChart(times, values, px) {
  const n = times.length;
  const out = [];
  if (!n) return out;
  if (n <= px * 2) {
    for (let i = 0; i < n; i++) {
      if (values[i] !== null && values[i] !== undefined) out.push([i, values[i]]);
    }
    return out;
  }
  const stride = Math.ceil(n / px);
  for (let k = 0; k < n; k += stride) {
    let lo = Infinity;
    let hi = -Infinity;
    let iLo = k;
    let iHi = k;
    for (let i = k; i < Math.min(n, k + stride); i++) {
      const x = values[i];
      if (x === null || x === undefined) continue;
      if (x < lo) { lo = x; iLo = i; }
      if (x > hi) { hi = x; iHi = i; }
    }
    if (lo !== Infinity) {
      if (iLo !== iHi) {
        out.push([iLo, lo], [iHi, hi]);
      } else {
        out.push([iLo, lo]);
      }
    }
  }
  return out;
}

/* ------------------------------ constants ------------------------------ */

const NUMERIC_COLS = [
  "Chilled Water Rate",
  "Cooling Water Temperature",
  "Building Load",
  "Chiller Energy Consumption",
  "Outside Temperature",
  "Dew Point",
  "Humidity",
  "Wind Speed",
  "Pressure",
];
const COLUMN_UNITS = {
  "Chilled Water Rate": "l/s",
  "Cooling Water Temperature": "°C",
  "Building Load": "RT",
  "Chiller Energy Consumption": "kWh",
  "Outside Temperature": "°F",
  "Dew Point": "°F",
  Humidity: "%",
  "Wind Speed": "m/s",
  Pressure: "inHg",
};

const healthColor = (h) => (h >= 75 ? cssVar("--ts-ok") : h >= 55 ? cssVar("--ts-warn") : cssVar("--ts-danger"));

/* ------------------------------ boot ------------------------------ */

function init() {
  $$(".ts-tab").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
  $("#btn-brand").addEventListener("click", () => setTab("overview"));
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#drawer-close-x").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawer();
  });
  renderHeaderStatus();
  renderView();
}

document.addEventListener("DOMContentLoaded", init);