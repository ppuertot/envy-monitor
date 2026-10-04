"use strict";

/* ------------------------------------------------------------------ *
 *  envy — front-end. Recibe muestras por SSE y dibuja el panel.
 * ------------------------------------------------------------------ */

const CHART_H = 130;

const state = {
  xs: [],                 // timestamps (segundos Unix) compartidos
  series: {},             // index -> {gpu:[], used:[], io:[]}
  gpus: [],
  sig: "",
  plots: {},              // index -> {gpu, mem, gpuEl, memEl, refs}
  maxPoints: 30,
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
  let min = Infinity, max = -Infinity, sum = 0;
  for (const v of arr) { if (v == null) continue; min = Math.min(min, v); max = Math.max(max, v); sum += v; }
  if (min === Infinity) return { min: null, avg: null, max: null };
  return { min, avg: sum / arr.length, max };
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

function chartOptions(width) {
  return {
    width,
    height: CHART_H,
    padding: [8, 46, 2, 8],
    legend: { show: false },
    cursor: { show: false },
    scales: {
      x: { time: true },
      y: { range: [0, 100], auto: false },
    },
    axes: [
      {
        stroke: "#c9c9c9",
        size: 0,
        ticks: { show: false },
        grid: { stroke: "#ececec", width: 1 },
        values: () => [],
      },
      {
        side: 1,
        size: 46,
        stroke: "#6b7280",
        font: "12px sans-serif",
        ticks: { show: false },
        grid: { stroke: "#ececec", width: 1 },
        splits: () => [0, 25, 50, 75, 100],
        values: (u, splits) => splits.map((s) => `${s}%`),
      },
    ],
    series: [
      {},
      {
        stroke: "#2ec27e",
        width: 2,
        fill: "rgba(46,194,126,0.10)",
        points: { show: false },
      },
    ],
  };
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
    <div class="chart-head"><span>${state.intervalLabel || "2 sec"} step</span></div>
    <div class="chart" id="chart-gpu-${idx}"></div>
    <div class="chart-foot"><span>${state.windowLabel || "60 sec"}</span></div>

    <h3><span class="dot"></span>${gpu.name}</h3>
    <div class="stats">
      <div><span class="label">Utilization:</span> <span class="val" data-f="gpu-util"></span></div>
      <div><span class="label">Temperature:</span> <span class="val" data-f="temp"></span></div>
      <div><span class="label">Power:</span> <span class="val" data-f="power"></span></div>
      <div><span class="label">Frequency:</span> <span class="val" data-f="clk-gpu"></span></div>
    </div>

    <div class="dashed"></div>

    <h2 style="margin-top:18px">Memory Utilization</h2>
    <div class="chart-head"><span>${state.intervalLabel || "2 sec"} step</span></div>
    <div class="chart" id="chart-mem-${idx}"></div>
    <div class="chart-foot"><span>${state.windowLabel || "60 sec"}</span></div>

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
        <td>${p.gpu}</td>
        <td>${p.pid}</td>
        <td>${esc(p.type)}</td>
        <td title="${esc(p.name)}">${esc(shortProc(p.name))}</td>
        <td class="num">${p.memory} MiB</td>
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
  state.maxPoints = Math.max(2, Math.round((data.window || 60) / (data.interval || 2)));
  state.intervalLabel = `${data.interval || 2} sec`;
  state.windowLabel = `${Math.round(data.window || 60)} sec`;

  state.xs = [];
  state.series = {};
  for (const point of data.history || []) pushPoint(point);

  if (data.sample) {
    ensureGpuUI(data.sample.gpus || []);
    updateStats(data.sample);
    renderProcesses(data.sample.processes);
    showError(data.sample.error);
  }
  renderCharts();
  const backend = data.backend || (data.sample && data.sample.backend) || "nvidia-smi";
  $("#backend").textContent = backend;
  $("#meta").textContent = `intervalo ${state.intervalLabel} · ventana ${state.windowLabel} · ${state.maxPoints} muestras`;
}

function applySample(sample) {
  ensureGpuUI(sample.gpus || []);
  if (sample.gpus && sample.gpus.length) pushPoint({
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
  updateStats(sample);
  renderProcesses(sample.processes);
  showError(sample.error);
}

/* ----------------------------- SSE ------------------------------- */

function connect() {
  setStatus("connecting", "Conectando…");
  const es = new EventSource("/api/stream");
  es.addEventListener("snapshot", (e) => applySnapshot(JSON.parse(e.data)));
  es.addEventListener("sample", (e) => applySample(JSON.parse(e.data)));
  es.onerror = () => setStatus("connecting", "Reconectando…");
}

/* ---------------------------- pestañas --------------------------- */

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-active", t === tab));
    const target = tab.dataset.tab;
    $("#tab-utilization").classList.toggle("hidden", target !== "utilization");
    $("#tab-processes").classList.toggle("hidden", target !== "processes");
    if (target === "utilization") {
      // uPlot necesita saber el ancho cuando el contenedor vuelve a ser visible.
      for (const p of Object.values(state.plots)) {
        p.gpu.setSize({ width: p.gpuEl.clientWidth, height: CHART_H });
        p.mem.setSize({ width: p.memEl.clientWidth, height: CHART_H });
      }
    }
  });
});

window.addEventListener("resize", () => {
  for (const p of Object.values(state.plots)) {
    p.gpu.setSize({ width: p.gpuEl.clientWidth, height: CHART_H });
    p.mem.setSize({ width: p.memEl.clientWidth, height: CHART_H });
  }
});

connect();
