"use strict";

/* ------------------------------------------------------------------ *
 *  envy — front-end. Recibe muestras por SSE y dibuja el panel.
 * ------------------------------------------------------------------ */

const CHART_H = 130;

// Rango -> puntos objetivo, refresco (ms) y etiqueta. `live` usa el flujo SSE.
const RANGE_CFG = {
  60:     { points: 30,  refresh: 0,      label: "60 s",  live: true },
  300:    { points: 150, refresh: 15000,  label: "5 min" },
  3600:   { points: 240, refresh: 30000,  label: "1 h" },
  86400:  { points: 288, refresh: 60000,  label: "24 h" },
  604800: { points: 336, refresh: 300000, label: "7 d" },
};

const state = {
  xs: [],                 // timestamps (segundos Unix) compartidos
  series: {},             // index -> {gpu:[], used:[], io:[]}
  gpus: [],
  sig: "",
  plots: {},              // index -> {gpu, mem, gpuEl, memEl, refs}
  maxPoints: 30,
  range: 60,
  live: true,
  rangeTimer: null,
  window: 60,
  interval: 2,
  backend: "nvidia-smi",
  persistence: false,
};

/* -------------------------- utilidades --------------------------- */

const $ = (sel, root = document) => root.querySelector(sel);

function fmt(v, unit = "", digits = 0) {
  if (v === null || v === undefined || Number.isNaN(v)) return "N/A";
  const n = digits > 0 ? Number(v).toFixed(digits) : Math.round(v);
  return `${n}${unit ? " " + unit : ""}`;
}

function pct(v) { return v === null || v === undefined ? "N/A" : `${Math.round(v)}%`; }

function mm(arr) {
  if (!arr || !arr.length) return { min: null, avg: null, max: null };
  let min = Infinity, max = -Infinity, sum = 0, count = 0;
  for (const v of arr) {
    if (v == null) continue;
    min = Math.min(min, v); max = Math.max(max, v); sum += v; count += 1;
  }
  if (count === 0) return { min: null, avg: null, max: null };
  return { min, avg: sum / count, max };
}

function agg(arr) {
  const { min, avg, max } = mm(arr);
  if (min === null) return "";
  return ` (${pct(min)} / ${pct(avg)} / ${pct(max)})`;
}

function setStatus(kind, text) {
  const el = $("#status");
  el.className = `status status--${kind}`;
  el.title = text || "";
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// nvidia-smi trunca el nombre del proceso; NVML entrega el cmdline completo.
function shortProc(name) {
  const first = String(name).trim().split(/\s+/)[0] || String(name);
  return first.split("/").pop() || String(name);
}

/* --------------------- construcción de la UI --------------------- */

function chartHeight() {
  // Se adapta al alto de la ventana (dos paneles: GPU y memoria).
  const byHeight = Math.round(window.innerHeight * 0.28);
  const cap = window.innerWidth <= 640 ? 150 : 380;
  return Math.max(CHART_H, Math.min(byHeight, cap));
}

function chartColors() {
  return document.documentElement.dataset.theme === "dark"
    ? { tick: "#3a4150", grid: "#2a2e37", axis: "#9aa4b2" }
    : { tick: "#c9c9c9", grid: "#ececec", axis: "#6b7280" };
}

function hexToRgb(hex) {
  const h = String(hex).replace("#", "");
  const v = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(v, 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

// El verde sale de la variable CSS --accent (coincide con el punto de estado).
function accentColor() {
  const v = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
  return v || "#2ec27e";
}

// Relleno en degradado bajo la curva, como el original.
function areaFill(u) {
  if (!u.bbox) return "transparent";
  const { r, g, b } = hexToRgb(accentColor());
  const grad = u.ctx.createLinearGradient(0, u.bbox.top, 0, u.bbox.top + u.bbox.height);
  grad.addColorStop(0, `rgba(${r},${g},${b},0.30)`);
  grad.addColorStop(1, `rgba(${r},${g},${b},0.02)`);
  return grad;
}

function chartOptions(width) {
  const c = chartColors();
  return {
    width,
    height: chartHeight(),
    padding: [0, 0, 0, 0],
    legend: { show: false },
    cursor: { show: false },
    scales: {
      x: { time: true },
      y: { range: [0, 100], auto: false },
    },
    axes: [
      {
        stroke: c.tick,
        size: 0,
        ticks: { show: false },
        grid: { stroke: c.grid, width: 1 },
        values: () => [],
      },
      {
        side: 1,
        size: 0,
        stroke: c.tick,
        ticks: { show: false },
        grid: { stroke: c.grid, width: 1 },
        splits: () => [0, 25, 50, 75, 100],
        values: () => [],
      },
    ],
    series: [
      {},
      {
        stroke: accentColor(),
        width: 2,
        fill: areaFill,
        paths: uPlot.paths.spline ? uPlot.paths.spline() : undefined,
        points: { show: false },
      },
    ],
  };
}

// Aplica los colores del tema a las gráficas ya creadas.
function applyChartTheme() {
  const c = chartColors();
  for (const p of Object.values(state.plots)) {
    for (const u of [p.gpu, p.mem]) {
      u.axes[0].grid.stroke = c.grid;
      u.axes[1].stroke = c.axis;
      u.axes[1].grid.stroke = c.grid;
      u.series[1].stroke = accentColor();
      u.redraw();
    }
  }
}

function makePlot(el) {
  const width = Math.max(200, el.clientWidth || 600);
  return new uPlot(chartOptions(width), [[], []], el);
}

function buildGpuBlock(gpu) {
  const idx = gpu.index;
  const block = document.createElement("div");
  block.className = "gpu-block";
  block.innerHTML = `
    <h2>GPU Utilization</h2>
    <div class="chart-head"><span class="step-label">${state.intervalLabel || "2 sec"} step</span><span class="scale-label">100%</span></div>
    <div class="chart" id="chart-gpu-${idx}"></div>
    <div class="chart-foot"><span class="window-label">${state.windowLabel || "60 sec"}</span><span class="scale-label">0%</span></div>

    <h3><span class="dot"></span>${gpu.name}</h3>
    <div class="stats">
      <div><span class="label">Utilization:</span> <span class="val" data-f="gpu-util"></span></div>
      <div><span class="label">Temperature:</span> <span class="val" data-f="temp"></span></div>
      <div><span class="label">Power:</span> <span class="val" data-f="power"></span></div>
      <div><span class="label">Frequency:</span> <span class="val" data-f="clk-gpu"></span></div>
    </div>

    <div class="dashed"></div>

    <h2 style="margin-top:18px">Memory Utilization</h2>
    <div class="chart-head"><span class="step-label">${state.intervalLabel || "2 sec"} step</span><span class="scale-label">100%</span></div>
    <div class="chart" id="chart-mem-${idx}"></div>
    <div class="chart-foot"><span class="window-label">${state.windowLabel || "60 sec"}</span><span class="scale-label">0%</span></div>

    <h3><span class="dot"></span>${gpu.name}</h3>
    <div class="stats">
      <div><span class="label">Utilization:</span> <span class="val" data-f="mem-util"></span></div>
      <div><span class="label">Total:</span> <span class="val" data-f="mem-total"></span></div>
      <div><span class="label">Used:</span> <span class="val" data-f="mem-used"></span></div>
      <div><span class="label">Free:</span> <span class="val" data-f="mem-free"></span></div>
      <div><span class="label">IO Utilization:</span> <span class="val" data-f="mem-io"></span></div>
      <div><span class="label">Frequency:</span> <span class="val" data-f="clk-mem"></span></div>
    </div>
  `;
  return block;
}

function ensureGpuUI(gpus) {
  const sig = gpus.map((g) => g.index).join(",");
  if (sig === state.sig) return;
  state.sig = sig;
  state.gpus = gpus;

  const root = $("#gpus");
  root.innerHTML = "";
  state.plots = {};

  for (const gpu of gpus) {
    root.appendChild(buildGpuBlock(gpu));
    const gpuEl = $(`#chart-gpu-${gpu.index}`);
    const memEl = $(`#chart-mem-${gpu.index}`);
    const block = gpuEl.closest(".gpu-block");
    const refs = {};
    block.querySelectorAll("[data-f]").forEach((el) => { refs[el.dataset.f] = el; });
    state.plots[gpu.index] = {
      gpuEl,
      memEl,
      gpu: makePlot(gpuEl),
      mem: makePlot(memEl),
      refs,
    };
  }
  renderCharts();
}

/* ------------------------- datos / series ------------------------ */

function ensureSeries(index) {
  if (!state.series[index]) state.series[index] = { gpu: [], used: [], io: [] };
  return state.series[index];
}

function pushPoint(point) {
  state.xs.push(point.ts);
  for (const g of point.gpus || []) {
    const s = ensureSeries(g.index);
    s.gpu.push(g.gpu == null ? 0 : g.gpu);
    s.used.push(g.total ? (g.used / g.total) * 100 : 0);
    s.io.push(g.io == null ? 0 : g.io);
  }
  while (state.xs.length > state.maxPoints) {
    state.xs.shift();
    for (const idx in state.series) {
      state.series[idx].gpu.shift();
      state.series[idx].used.shift();
      state.series[idx].io.shift();
    }
  }
}

function renderCharts() {
  for (const idx in state.plots) {
    const s = ensureSeries(Number(idx));
    const p = state.plots[idx];
    p.gpu.setData([state.xs, s.gpu]);
    p.mem.setData([state.xs, s.used]);
  }
}

function updateStats(sample) {
  if (!sample) return;
  for (const gpu of sample.gpus || []) {
    const p = state.plots[gpu.index];
    if (!p) continue;
    const s = ensureSeries(gpu.index);
    const r = p.refs;
    r["gpu-util"].textContent = `${pct(gpu.utilization.gpu)}${agg(s.gpu)}`;
    r["temp"].textContent = fmt(gpu.temperature.gpu, "°C");
    r["power"].textContent = fmt(gpu.power.draw, "W", 2);
    r["clk-gpu"].textContent = fmt(gpu.clocks.graphics, "MHz");

    const usedPct = gpu.memory.total ? (gpu.memory.used / gpu.memory.total) * 100 : null;
    r["mem-util"].textContent = `${pct(usedPct)}${agg(s.used)}`;
    r["mem-total"].textContent = fmt(gpu.memory.total, "MiB");
    r["mem-used"].textContent = fmt(gpu.memory.used, "MiB");
    r["mem-free"].textContent = fmt(gpu.memory.free, "MiB");
    r["mem-io"].textContent = pct(gpu.utilization.memory);
    r["clk-mem"].textContent = fmt(gpu.clocks.memory, "MHz");
  }
}

/* --------------------------- procesos ---------------------------- */

function renderProcesses(processes) {
  const body = $("#proc-body");
  if (!processes || !processes.length) {
    body.innerHTML = `<tr><td colspan="5" class="empty">Sin procesos usando la GPU</td></tr>`;
    return;
  }
  body.innerHTML = processes
    .map(
      (p) => `<tr>
        <td data-label="GPU">${p.gpu}</td>
        <td data-label="PID">${p.pid}</td>
        <td data-label="Tipo">${esc(p.type)}</td>
        <td data-label="Proceso" title="${esc(p.name)}">${esc(shortProc(p.name))}</td>
        <td data-label="Memoria" class="num">${p.memory} MiB</td>
      </tr>`
    )
    .join("");
}

/* ---------------------------- estado ----------------------------- */

function showError(message) {
  const banner = $("#banner");
  if (message) {
    banner.textContent = message;
    banner.classList.remove("hidden");
    setStatus("error", message);
  } else {
    banner.classList.add("hidden");
    setStatus("ok", "Conectado");
  }
}

function applySnapshot(data) {
  state.window = data.window || 60;
  state.interval = data.interval || 2;
  state.intervalLabel = `${state.interval} sec`;
  state.windowLabel = `${Math.round(state.window)} sec`;
  state.maxPoints = Math.max(2, Math.round(state.window / state.interval));
  state.persistence = !!data.persistence;
  state.backend = data.backend || (data.sample && data.sample.backend) || "nvidia-smi";

  if (state.live) {
    state.xs = [];
    state.series = {};
    for (const point of data.history || []) pushPoint(point);
  }

  if (data.sample) {
    ensureGpuUI(data.sample.gpus || []);
    updateStats(data.sample);
    renderProcesses(data.sample.processes);
    showError(data.sample.error);
  }
  if (state.live) renderCharts();
  $("#backend").textContent = state.backend;
  updateMeta();
}

function applySample(sample) {
  ensureGpuUI(sample.gpus || []);
  if (state.live && sample.gpus && sample.gpus.length) {
    pushPoint({
      ts: sample.ts,
      gpus: sample.gpus.map((g) => ({
        index: g.index,
        gpu: g.utilization.gpu,
        io: g.utilization.memory,
        used: g.memory.used,
        total: g.memory.total,
      })),
    });
    renderCharts();
  }
  updateStats(sample);
  renderProcesses(sample.processes);
  showError(sample.error);
}

/* ------------------------ rango temporal ------------------------- */

function updateRangeLabels(stepText, windowText) {
  document.querySelectorAll(".step-label").forEach((el) => {
    el.textContent = stepText ? `${stepText} step` : "";
  });
  document.querySelectorAll(".window-label").forEach((el) => {
    el.textContent = windowText || "";
  });
}

function updateMeta() {
  const cfg = RANGE_CFG[state.range] || {};
  const win = state.live ? (state.windowLabel || "60 sec") : (cfg.label || "");
  const persist = state.persistence ? "" : " · sin persistencia";
  $("#meta").textContent =
    `rango ${win} · intervalo ${state.intervalLabel || ""} · backend ${state.backend}${persist}`;
}

function setChartsFromHistory(data, seconds) {
  const series = data.series || {};
  const perGpu = {};
  const allTs = new Set();
  for (const idx in series) {
    const map = new Map();
    for (const p of series[idx] || []) {
      map.set(p.ts, p);
      allTs.add(p.ts);
    }
    perGpu[idx] = map;
  }
  state.xs = Array.from(allTs).sort((a, b) => a - b);
  state.series = {};
  for (const idx in perGpu) {
    const map = perGpu[idx];
    const s = { gpu: [], used: [], io: [] };
    for (const ts of state.xs) {
      const p = map.get(ts);
      s.gpu.push(p && p.gpu != null ? p.gpu : null);
      s.used.push(p && p.used != null ? p.used : null);
      s.io.push(p && p.io != null ? p.io : null);
    }
    state.series[idx] = s;
  }
  state.maxPoints = state.live
    ? Math.max(2, Math.round(state.window / state.interval))
    : Math.max(2, state.xs.length);
  renderCharts();
  const cfg = RANGE_CFG[seconds] || {};
  updateRangeLabels(data.step ? `${Math.round(data.step)} sec` : "", cfg.label || "");
}

async function fetchHistory(seconds, points) {
  try {
    const res = await fetch(`/api/history?seconds=${seconds}&points=${points}`);
    if (!res.ok) return;
    setChartsFromHistory(await res.json(), seconds);
  } catch (_) {
    /* sin persistencia o red caída: se conserva lo que haya */
  }
}

function scheduleRangeRefresh(seconds, cfg) {
  if (!cfg.refresh) return;
  state.rangeTimer = setTimeout(async () => {
    await fetchHistory(seconds, cfg.points);
    scheduleRangeRefresh(seconds, cfg);
  }, cfg.refresh);
}

function loadRange(seconds) {
  const cfg = RANGE_CFG[seconds] || RANGE_CFG[300];
  state.range = seconds;
  state.live = !!cfg.live;
  if (state.rangeTimer) {
    clearTimeout(state.rangeTimer);
    state.rangeTimer = null;
  }
  if (state.live) {
    state.xs = [];
    state.series = {};
    state.maxPoints = Math.max(2, Math.round(state.window / state.interval));
    fetchHistory(60, cfg.points); // rellena los últimos 60 s (si hay persistencia)
  } else {
    fetchHistory(seconds, cfg.points);
    scheduleRangeRefresh(seconds, cfg);
  }
  updateMeta();
}

/* ----------------------------- SSE ------------------------------- */

function connect() {
  setStatus("connecting", "Conectando…");
  const es = new EventSource("/api/stream");
  es.addEventListener("snapshot", (e) => applySnapshot(JSON.parse(e.data)));
  es.addEventListener("sample", (e) => applySample(JSON.parse(e.data)));
  es.onerror = () => setStatus("connecting", "Reconectando…");
}

/* ----------------------------- tema ------------------------------ */

const THEME_ORDER = ["auto", "light", "dark"];
const THEME_LABEL = { auto: "Auto", light: "Claro", dark: "Oscuro" };

function applyTheme(pref) {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const dark = pref === "dark" || (pref === "auto" && media.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.dataset.themePref = pref;
  try { localStorage.setItem("envy-theme", pref); } catch (e) { /* modo privado */ }
  const btn = $("#theme-toggle");
  if (btn) btn.textContent = THEME_LABEL[pref] || "Auto";
  applyChartTheme();
}

function initTheme() {
  applyTheme(document.documentElement.dataset.themePref || "auto");
  const btn = $("#theme-toggle");
  if (btn) {
    btn.addEventListener("click", () => {
      const cur = document.documentElement.dataset.themePref || "auto";
      const next = THEME_ORDER[(THEME_ORDER.indexOf(cur) + 1) % THEME_ORDER.length];
      applyTheme(next);
    });
  }
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if ((document.documentElement.dataset.themePref || "auto") === "auto") applyTheme("auto");
  });
}

/* ---------------------------- pestañas --------------------------- */

const rangeSelect = $("#range");
if (rangeSelect) {
  rangeSelect.addEventListener("change", (e) => loadRange(Number(e.target.value)));
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-active", t === tab));
    const target = tab.dataset.tab;
    $("#tab-utilization").classList.toggle("hidden", target !== "utilization");
    $("#tab-processes").classList.toggle("hidden", target !== "processes");
    if (target === "utilization") {
      // uPlot necesita saber el ancho cuando el contenedor vuelve a ser visible.
      for (const p of Object.values(state.plots)) {
        p.gpu.setSize({ width: p.gpuEl.clientWidth, height: chartHeight() });
        p.mem.setSize({ width: p.memEl.clientWidth, height: chartHeight() });
      }
    }
  });
});

window.addEventListener("resize", () => {
  for (const p of Object.values(state.plots)) {
    p.gpu.setSize({ width: p.gpuEl.clientWidth, height: chartHeight() });
    p.mem.setSize({ width: p.memEl.clientWidth, height: chartHeight() });
  }
});

initTheme();
connect();
