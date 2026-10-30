"use strict";
(() => {
  const T = window.I18N;
  const $ = (sel) => document.querySelector(sel);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const ACCENT = "#50b3d0";
  const INK = css("--text"), MUTED = css("--muted"), GRID = css("--grid-line");
  const PERIODS = [1, 6, 24, 168];
  const SIZES = ["s", "m", "l"];
  const REFRESH_MS = 60_000;

  const escapeHtml = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(path, body, method) {
    const opts = body === undefined ? { method: method || "GET" } : {
      method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    };
    const resp = await fetch(path, opts);
    if (resp.status === 401) { location.href = "/login"; throw new Error("session"); }
    const data = await resp.json();
    if (!resp.ok) {
      const d = data.detail;
      throw new Error(Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d || data.message || resp.statusText);
    }
    return data;
  }

  // Times arrive as the user's wall clock encoded as UTC, so they are read with UTC getters.
  const pad = (n) => String(n).padStart(2, "0");
  const fmtTime = (ms) => {
    const d = new Date(ms);
    return `${pad(d.getUTCDate())}.${pad(d.getUTCMonth() + 1)} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
  };
  function fmtValue(v) {
    if (v === null || v === undefined) return "—";
    const abs = Math.abs(v);
    if (abs >= 1e9) return `${(v / 1e9).toFixed(2)} G`;
    if (abs >= 1e6) return `${(v / 1e6).toFixed(2)} M`;
    return String(Math.round(v * 100) / 100);
  }

  // --- Catalogues (cached per page) -----------------------------------------------------------------------
  let nodes = [];
  const objectCache = new Map(), measureCache = new Map();
  const cached = (map, key, load) => {
    if (!map.has(key)) map.set(key, load().catch((err) => { map.delete(key); throw err; }));
    return map.get(key);
  };
  const objectsOf = (p) => cached(objectCache, `${p.server_id}:${p.node_id}`,
    () => api(`/api/charts/objects?${new URLSearchParams({ server_id: p.server_id, node_id: p.node_id })}`));
  const measuresOf = (p, analyzers) => {
    const ids = [...analyzers].sort((a, b) => a - b).join(",");
    return cached(measureCache, `${p.server_id}:${ids}`,
      () => api(`/api/charts/measures?${new URLSearchParams({ server_id: p.server_id, analyzers: ids })}`));
  };

  // --- Board ----------------------------------------------------------------------------------------------
  let panels = [];   // [{cfg, el, chart, seq, objects, measures}]
  let saveTimer = null;

  function save() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try {
        await api("/api/charts/board", { panels: panels.map((p) => p.cfg) }, "PUT");
        $("#ch-message").innerHTML = "";
      } catch (err) {
        $("#ch-message").innerHTML = `<div class="error">${escapeHtml(T.ch_save_failed)}: ${escapeHtml(err.message)}</div>`;
      }
    }, 400);
  }

  function showEmpty() {
    $("#ch-empty").hidden = panels.length > 0 || !nodes.length;
  }

  const newId = () => Math.random().toString(36).slice(2, 10);

  function addPanel(cfg, scroll) {
    const p = { cfg, el: document.createElement("section"), chart: null, seq: 0, objects: [], measures: [] };
    p.el.className = "card ch-panel";
    p.el.innerHTML = `
      <div class="ch-bar">
        <select class="ch-src" title="${escapeHtml(T.ch_source)}"></select>
        <span class="ch-step">
          <button type="button" class="btn small" data-step="obj" data-dir="-1" title="${escapeHtml(T.ch_prev)}">‹</button>
          <select class="ch-obj" title="${escapeHtml(T.ch_channel)}"></select>
          <button type="button" class="btn small" data-step="obj" data-dir="1" title="${escapeHtml(T.ch_next)}">›</button>
        </span>
        <span class="ch-step">
          <button type="button" class="btn small" data-step="mes" data-dir="-1" title="${escapeHtml(T.ch_prev)}">‹</button>
          <select class="ch-mes" title="${escapeHtml(T.ch_measure)}"></select>
          <button type="button" class="btn small" data-step="mes" data-dir="1" title="${escapeHtml(T.ch_next)}">›</button>
        </span>
      </div>
      <div class="ch-bar ch-bar2">
        <span class="ch-info muted"></span>
        <span class="ch-tools">
          <span class="presets ch-periods">${PERIODS.map((h) => `<button type="button" data-h="${h}">${escapeHtml(T[`ch_period_${h}`])}</button>`).join("")}</span>
          <span class="presets ch-sizes" title="${escapeHtml(T.ch_size)}">${SIZES.map((s) => `<button type="button" data-size="${s}">${s.toUpperCase()}</button>`).join("")}</span>
          <button type="button" class="btn small" data-move="-1" title="${escapeHtml(T.ch_move_back)}">◀</button>
          <button type="button" class="btn small" data-move="1" title="${escapeHtml(T.ch_move_fwd)}">▶</button>
          <button type="button" class="btn small danger-btn" data-remove title="${escapeHtml(T.ch_remove)}">✕</button>
        </span>
      </div>
      <div class="ch-chart"></div>`;
    $("#board").append(p.el);
    panels.push(p);
    wire(p);
    applyLook(p);
    renderSources(p);
    loadObjects(p).then(() => loadData(p));
    if (scroll) p.el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    showEmpty();
    return p;
  }

  function applyLook(p) {
    p.el.dataset.size = p.cfg.size;
    for (const b of p.el.querySelectorAll(".ch-periods button")) b.classList.toggle("active", Number(b.dataset.h) === p.cfg.hours);
    for (const b of p.el.querySelectorAll(".ch-sizes button")) b.classList.toggle("active", b.dataset.size === p.cfg.size);
  }

  function renderSources(p) {
    const multi = new Set(nodes.map((n) => n.server_id)).size > 1;
    const known = nodes.some((n) => n.server_id === p.cfg.server_id && n.id === p.cfg.node_id);
    p.el.querySelector(".ch-src").innerHTML = (known ? "" : `<option value="">${escapeHtml(T.ch_pick)}</option>`) +
      nodes.map((n, i) => `<option value="${i}"${n.server_id === p.cfg.server_id && n.id === p.cfg.node_id ? " selected" : ""}>${escapeHtml(multi ? `${n.server} · ${n.name}` : n.name)}</option>`).join("");
  }

  const objLabel = (o) => o.stream ? `${o.stream} › ${o.name}` : o.name;
  const hasMeasure = (o, cfg) => !cfg.analyzer_id || o.analyzers.includes(cfg.analyzer_id);

  async function loadObjects(p) {
    const sel = p.el.querySelector(".ch-obj");
    p.objects = [];
    if (p.cfg.server_id == null || p.cfg.node_id == null) {
      sel.innerHTML = `<option value="">${escapeHtml(T.ch_pick)}</option>`;
      return renderMeasures(p);
    }
    sel.innerHTML = `<option>${escapeHtml(T.loading)}</option>`;
    try {
      p.objects = await objectsOf(p.cfg);
    } catch (err) {
      sel.innerHTML = "";
      return showText(p, err.message, true);
    }
    sel.innerHTML = `<option value="">${escapeHtml(T.ch_pick)}</option>` + p.objects.map((o) =>
      `<option value="${escapeHtml(o.id)}"${o.id === p.cfg.object_id ? " selected" : ""}>${escapeHtml(objLabel(o))}</option>`).join("");
    if (!p.objects.some((o) => o.id === p.cfg.object_id)) p.cfg.object_id = null;
    await loadMeasures(p);
  }

  async function loadMeasures(p) {
    const obj = p.objects.find((o) => o.id === p.cfg.object_id);
    p.measures = [];
    if (obj) {
      try {
        p.measures = await measuresOf(p.cfg, obj.analyzers);
      } catch (err) {
        showText(p, err.message, true);
      }
    }
    renderMeasures(p);
  }

  const flatMeasures = (p) => p.measures.flatMap((a) => a.measures.map((m) => ({ ...m, aid: a.id, analyzer: a.name })));

  function renderMeasures(p) {
    const sel = p.el.querySelector(".ch-mes");
    const cur = `${p.cfg.analyzer_id}|${p.cfg.column}`;
    if (!flatMeasures(p).some((m) => `${m.aid}|${m.column}` === cur)) { p.cfg.analyzer_id = null; p.cfg.column = null; }
    sel.innerHTML = `<option value="">${escapeHtml(T.ch_pick)}</option>` + p.measures.map((a) =>
      `<optgroup label="${escapeHtml(a.name)}">${a.measures.map((m) => {
        const v = `${a.id}|${m.column}`;
        return `<option value="${escapeHtml(v)}"${v === cur ? " selected" : ""}>${escapeHtml(m.name)}${m.unit ? `, ${escapeHtml(m.unit)}` : ""}</option>`;
      }).join("")}</optgroup>`).join("");
  }

  function wire(p) {
    p.el.querySelector(".ch-src").addEventListener("change", async (e) => {
      const n = nodes[Number(e.target.value)];
      if (!n) return;
      Object.assign(p.cfg, { server_id: n.server_id, node_id: n.id, object_id: null });
      renderSources(p);
      save();
      await loadObjects(p);
      loadData(p);
    });
    p.el.querySelector(".ch-obj").addEventListener("change", (e) => pickObject(p, e.target.value || null));
    p.el.querySelector(".ch-mes").addEventListener("change", (e) => pickMeasure(p, e.target.value));
    for (const b of p.el.querySelectorAll("[data-step]")) {
      b.addEventListener("click", () => (b.dataset.step === "obj" ? stepObject : stepMeasure)(p, Number(b.dataset.dir)));
    }
    for (const b of p.el.querySelectorAll(".ch-periods button")) {
      b.addEventListener("click", () => { p.cfg.hours = Number(b.dataset.h); applyLook(p); save(); loadData(p); });
    }
    for (const b of p.el.querySelectorAll(".ch-sizes button")) {
      b.addEventListener("click", () => { p.cfg.size = b.dataset.size; applyLook(p); save(); });
    }
    for (const b of p.el.querySelectorAll("[data-move]")) b.addEventListener("click", () => move(p, Number(b.dataset.move)));
    p.el.querySelector("[data-remove]").addEventListener("click", () => remove(p));
    // The chart follows its box: size buttons, window width, the grid reflowing.
    new ResizeObserver(() => p.chart && p.chart.resize()).observe(p.el.querySelector(".ch-chart"));
  }

  async function pickObject(p, id) {
    p.cfg.object_id = id;
    p.el.querySelector(".ch-obj").value = id || "";
    save();
    await loadMeasures(p);
    save();
    loadData(p);
  }

  function pickMeasure(p, value) {
    const [aid, column] = value ? [Number(value.split("|")[0]), value.slice(value.indexOf("|") + 1)] : [null, null];
    Object.assign(p.cfg, { analyzer_id: aid, column });
    p.el.querySelector(".ch-mes").value = value || "";
    save();
    loadData(p);
  }

  // The next channel that has the chart's measure, so stepping keeps showing the same measure.
  function stepObject(p, dir) {
    const list = p.objects.filter((o) => hasMeasure(o, p.cfg));
    if (!list.length) return;
    let i = list.findIndex((o) => o.id === p.cfg.object_id);
    if (i < 0) i = dir > 0 ? -1 : list.length;
    pickObject(p, list[(i + dir + list.length) % list.length].id);
  }

  function stepMeasure(p, dir) {
    const list = flatMeasures(p);
    if (!list.length) return;
    const i = list.findIndex((m) => m.aid === p.cfg.analyzer_id && m.column === p.cfg.column);
    const next = list[(i + dir + list.length) % list.length] || list[0];
    pickMeasure(p, `${next.aid}|${next.column}`);
  }

  function move(p, dir) {
    const i = panels.indexOf(p), j = i + dir;
    if (j < 0 || j >= panels.length) return;
    [panels[i], panels[j]] = [panels[j], panels[i]];
    if (dir < 0) panels[i].el.before(p.el); else panels[i].el.after(p.el);
    save();
  }

  function remove(p) {
    if (p.chart) p.chart.dispose();
    p.el.remove();
    panels = panels.filter((x) => x !== p);
    save();
    showEmpty();
  }

  // --- Data -----------------------------------------------------------------------------------------------
  function showText(p, text, error) {
    if (p.chart) { p.chart.dispose(); p.chart = null; }
    p.el.querySelector(".ch-chart").innerHTML = `<div class="ch-placeholder ${error ? "error" : "muted"}">${escapeHtml(text)}</div>`;
  }

  async function loadData(p) {
    const c = p.cfg;
    const info = p.el.querySelector(".ch-info");
    if (!c.object_id || !c.analyzer_id || !c.column) {
      info.textContent = "";
      return showText(p, T.ch_pick_hint);
    }
    const seq = ++p.seq;
    let data;
    try {
      data = await api(`/api/charts/data?${new URLSearchParams({ server_id: c.server_id, object_id: c.object_id,
        analyzer_id: c.analyzer_id, column: c.column, hours: c.hours })}`);
    } catch (err) {
      if (seq === p.seq) showText(p, err.message, true);
      return;
    }
    if (seq !== p.seq) return;
    const values = data.avg.map((x) => x[1]).filter((v) => v !== null);
    const mins = (data.raw ? data.avg : data.min).map((x) => x[1]).filter((v) => v !== null);
    const maxs = (data.raw ? data.avg : data.max).map((x) => x[1]).filter((v) => v !== null);
    info.textContent = values.length ? T.ch_minmax.replace("{a}", `${fmtValue(Math.min(...mins))} ${data.unit}`)
      .replace("{b}", `${fmtValue(Math.max(...maxs))} ${data.unit}`) : "";
    if (!values.length) return showText(p, T.ch_no_data);
    const box = p.el.querySelector(".ch-chart");
    if (!p.chart) { box.innerHTML = ""; p.chart = echarts.init(box, null, { renderer: "canvas" }); }
    p.chart.setOption(option(p, data), true);
  }

  function option(p, data) {
    const obj = p.objects.find((o) => o.id === p.cfg.object_id);
    const mes = flatMeasures(p).find((m) => m.aid === p.cfg.analyzer_id && m.column === p.cfg.column);
    const name = mes ? mes.name : p.cfg.column;
    const band = !data.raw;
    const series = band ? [
      { id: "band-min", type: "line", data: data.min, stack: "band", symbol: "none", lineStyle: { opacity: 0 },
        silent: true, tooltip: { show: false } },
      { id: "band-range", type: "line", stack: "band", symbol: "none", silent: true, lineStyle: { opacity: 0 },
        data: data.max.map(([t, v], i) => [t, v === null || data.min[i][1] === null ? null : v - data.min[i][1]]),
        areaStyle: { color: ACCENT, opacity: 0.14 } },
    ] : [];
    series.push({ id: "avg", name, type: "line", data: data.avg, showSymbol: false, lineStyle: { width: 1.6 },
      color: ACCENT, connectNulls: false });
    return {
      useUTC: true, animation: false,
      textStyle: { fontFamily: "Roboto, Segoe UI, system-ui, sans-serif", color: INK },
      grid: { left: 52, right: 14, top: 26, bottom: 26 },
      tooltip: {
        trigger: "axis", axisPointer: { type: "line", lineStyle: { color: MUTED } },
        backgroundColor: css("--raised"), borderColor: css("--border"), borderWidth: 1, textStyle: { color: INK, fontSize: 12 },
        formatter: (params) => {
          const main = params.find((x) => x.seriesId === "avg");
          if (!main) return "";
          let text = `<b>${fmtTime(main.value[0])}</b>${obj ? `<br>${escapeHtml(objLabel(obj))}` : ""}<br>${main.marker}${escapeHtml(name)}: <b>${fmtValue(main.value[1])}</b> ${escapeHtml(data.unit)}`;
          if (band) {
            text += `<br><span style="opacity:.75">${escapeHtml(T.ch_minmax.replace("{a}", fmtValue(data.min[main.dataIndex][1]))
              .replace("{b}", fmtValue(data.max[main.dataIndex][1])))}</span>`;
          }
          return text;
        },
      },
      xAxis: {
        type: "time", min: data.from, max: data.to,
        axisLine: { lineStyle: { color: GRID } }, axisTick: { show: false },
        axisLabel: { color: MUTED, hideOverlap: true, formatter: (v) => {
          const d = new Date(v);
          return p.cfg.hours > 24 ? `${pad(d.getUTCDate())}.${pad(d.getUTCMonth() + 1)}` : `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
        } },
        splitLine: { show: true, lineStyle: { color: GRID } },
      },
      yAxis: {
        type: "value", scale: true, name: data.unit, nameTextStyle: { color: MUTED, align: "right" },
        axisLabel: { color: MUTED, formatter: fmtValue }, splitLine: { lineStyle: { color: GRID, type: "dashed" } },
      },
      dataZoom: [{ type: "inside", filterMode: "none" }],
      series,
    };
  }

  function refreshAll() {
    for (const p of panels) loadData(p);
    stamp();
  }

  function stamp() {
    const d = new Date();
    $("#updated").textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  // --- Start ----------------------------------------------------------------------------------------------
  function addNew() {
    const last = panels[panels.length - 1]?.cfg;
    const scoped = nodes.find((n) => n.server_id === scope) || nodes[0];
    const src = last && last.server_id != null ? { server_id: last.server_id, node_id: last.node_id }
      : scoped ? { server_id: scoped.server_id, node_id: scoped.id } : { server_id: null, node_id: null };
    addPanel({ id: newId(), ...src, object_id: null, analyzer_id: null, column: null, hours: 24,
      size: panels.length >= 2 ? "s" : "m" }, true);
    save();
  }

  let scope = null;
  async function start() {
    const [board, nodeList] = await Promise.all([api("/api/charts/board"), api("/api/nodes")]);
    nodes = nodeList.nodes;
    scope = board.scope;
    $("#no-servers").hidden = nodeList.connected;
    if (nodeList.errors.length) {
      $("#ch-message").innerHTML = nodeList.errors.map((e) =>
        `<div class="error">${escapeHtml(T.err_server_unavailable.replace("{name}", e.server))}: ${escapeHtml(e.message)}</div>`).join("");
    }
    for (const cfg of board.panels) addPanel(cfg, false);
    showEmpty();
    stamp();  // each panel loads its values once its channel list is there
    setInterval(() => { if (!document.hidden) refreshAll(); }, REFRESH_MS);
  }

  $("#add").addEventListener("click", addNew);
  $("#add-empty").addEventListener("click", addNew);
  $("#refresh").addEventListener("click", refreshAll);
  start().catch((err) => { $("#ch-message").innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`; });
})();
