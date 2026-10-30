"use strict";
(() => {
  const T = window.I18N;
  const $ = (sel) => document.querySelector(sel);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  // The same palette as the report charts (measures.js); one colour per channel of a chart.
  const PALETTE = ["#50b3d0", "#f08a5d", "#3fc28a", "#f2b632", "#e889b4", "#7bd36b", "#9b8cf2", "#ff6b6b"];
  const MAX_OBJECTS = PALETTE.length;  // as charts.MAX_OBJECTS
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
  const objectsOf = (src) => cached(objectCache, `${src.server_id}:${src.node_id}`,
    () => api(`/api/charts/objects?${new URLSearchParams({ server_id: src.server_id, node_id: src.node_id })}`));
  const measuresOf = (src, analyzers) => {
    const ids = [...analyzers].sort((a, b) => a - b).join(",");
    return cached(measureCache, `${src.server_id}:${ids}`,
      () => api(`/api/charts/measures?${new URLSearchParams({ server_id: src.server_id, analyzers: ids })}`));
  };

  // --- Board ----------------------------------------------------------------------------------------------
  // A chart's cfg: the source (server, node) whose channels its list shows, the picked channels
  // (objects: [{server_id, node_id, id}], they may come from several sources), the picked measures
  // (measures: [{analyzer_id, column}]), period and size. Lines = channels x measures, at most MAX_LINES.
  const MAX_LINES = PALETTE.length;
  const MAX_UNITS = 2;  // one axis on the left, one on the right
  let panels = [];   // [{cfg, el, chart, seq, objects (of the source), known (every loaded channel), measures, filled, pkObj, pkMes}]
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
  const pickerTexts = { search: T.ch_search, reset: T.ch_reset, resetHint: T.ch_reset_hint, noMatch: T.ch_no_match };

  function addPanel(cfg, scroll) {
    cfg.objects = cfg.objects || [];
    cfg.measures = cfg.measures || [];
    const p = { cfg, el: document.createElement("section"), chart: null, seq: 0, objects: [], known: new Map(), measures: [], filled: null };
    p.el.className = "card ch-panel";
    p.el.innerHTML = `
      <div class="ch-bar">
        <select class="ch-src" title="${escapeHtml(T.ch_source)}"></select>
        <span class="ch-step">
          <button type="button" class="btn small" data-step="obj" data-dir="-1" title="${escapeHtml(T.ch_prev)}">‹</button>
          <span class="ch-objs"></span>
          <button type="button" class="btn small" data-step="obj" data-dir="1" title="${escapeHtml(T.ch_next)}">›</button>
        </span>
        <span class="ch-step">
          <button type="button" class="btn small" data-step="mes" data-dir="-1" title="${escapeHtml(T.ch_prev)}">‹</button>
          <span class="ch-mes"></span>
          <button type="button" class="btn small" data-step="mes" data-dir="1" title="${escapeHtml(T.ch_next)}">›</button>
        </span>
      </div>
      <div class="ch-chips"></div>
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
    p.pkObj = Picker(p.el.querySelector(".ch-objs"), { multi: true, texts: pickerTexts, onChange: (v) => pickObjects(p, v) });
    p.pkMes = Picker(p.el.querySelector(".ch-mes"), { multi: true, texts: { ...pickerTexts, search: T.ch_search_measures,
      resetHint: T.ch_reset_measures }, onChange: (v) => pickMeasures(p, v) });
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

  const multiServer = () => new Set(nodes.map((n) => n.server_id)).size > 1;

  function renderSources(p) {
    const known = nodes.some((n) => n.server_id === p.cfg.server_id && n.id === p.cfg.node_id);
    p.el.querySelector(".ch-src").innerHTML = (known ? "" : `<option value="">${escapeHtml(T.ch_pick)}</option>`) +
      nodes.map((n, i) => `<option value="${i}"${n.server_id === p.cfg.server_id && n.id === p.cfg.node_id ? " selected" : ""}>${escapeHtml(multiServer() ? `${n.server} · ${n.name}` : n.name)}</option>`).join("");
  }

  const objLabel = (o) => o.stream ? `${o.stream} › ${o.name}` : o.name;
  // A channel "has" the chart's measures when it has the analyzer of at least one of them.
  const hasMeasure = (o, cfg) => !cfg.measures.length || cfg.measures.some((m) => o.analyzers.includes(m.analyzer_id));
  const keyOf = (sid, oid) => `${sid}:${oid}`;
  const mesKey = (m) => `${m.analyzer_id}|${m.column}`;

  // A picked channel's label; the server is named when the chart mixes servers.
  function pickedLabel(p, x) {
    const o = p.known.get(keyOf(x.server_id, x.id));
    const name = o ? objLabel(o) : x.id || "";
    const mixed = new Set(p.cfg.objects.map((y) => y.server_id)).size > 1;
    const srv = nodes.find((n) => n.server_id === x.server_id);
    return mixed && srv ? `${srv.server} · ${name}` : name;
  }

  // Channel lists of the chart's source and of every source its picked channels come from.
  async function loadObjects(p) {
    const sources = new Map();
    if (p.cfg.server_id != null && p.cfg.node_id != null) sources.set(`${p.cfg.server_id}:${p.cfg.node_id}`, p.cfg);
    for (const x of p.cfg.objects) sources.set(`${x.server_id}:${x.node_id}`, x);
    p.objects = [];
    p.pkObj.setLabel(T.loading);
    let failed = "";
    await Promise.all([...sources.values()].map(async (src) => {
      try {
        const list = await objectsOf(src);
        for (const o of list) p.known.set(keyOf(src.server_id, o.id), o);
        if (src.server_id === p.cfg.server_id && src.node_id === p.cfg.node_id) p.objects = list;
        // Channels gone from MultiProbe leave the chart.
        p.cfg.objects = p.cfg.objects.filter((x) => x.server_id !== src.server_id || x.node_id !== src.node_id ||
          list.some((o) => o.id === x.id));
      } catch (err) {
        failed = err.message;
      }
    }));
    renderObjects(p);
    renderChips(p);
    if (failed) showText(p, failed, true);
    await loadMeasures(p);
  }

  // The source's channels grouped by stream: the stream itself first, then what is inside it.
  function renderObjects(p) {
    const n = p.cfg.objects.length;
    p.pkObj.setLabel(n === 0 ? T.ch_pick : n === 1 ? pickedLabel(p, p.cfg.objects[0]) : T.ch_n_channels.replace("{n}", n));
    for (const b of p.el.querySelectorAll('[data-step="obj"]')) b.disabled = n > 1;
    const groups = new Map();
    for (const o of p.objects) {
      const g = o.stream || o.name;
      if (!groups.has(g)) groups.set(g, { key: g, label: g, items: [] });
      groups.get(g).items.push({ value: o.id, label: o.stream ? o.name : `${o.name} (${o.kind || T.ch_stream})`,
        muted: !hasMeasure(o, p.cfg), title: objLabel(o) });
    }
    // A stream with nothing inside it (an HLS channel) is not a group of one: such streams go first, unfolded.
    const lone = [...groups.values()].filter((g) => g.items.length === 1);
    const list = [...(lone.length ? [{ key: "", label: null, items: lone.map((g) => ({ ...g.items[0], label: g.label })) }] : []),
      ...[...groups.values()].filter((g) => g.items.length > 1)];
    p.pkObj.setGroups(list);
    p.pkObj.setSelected(p.cfg.objects.filter((x) => x.server_id === p.cfg.server_id).map((x) => x.id));
  }

  // With several channels and one measure the channel chips are the legend (with the line colours); with one
  // channel and several measures the measure chips are. ✕ takes a channel or a measure off the chart.
  function renderChips(p) {
    const objs = p.cfg.objects, mes = p.cfg.measures;
    const objColour = mes.length <= 1, mesColour = objs.length <= 1;
    const chip = (kind, i, label, colour) => `<span class="ch-chip" data-kind="${kind}" data-i="${i}">${colour ? `<i style="background:${PALETTE[i % PALETTE.length]}"></i>` : ""}${escapeHtml(label)}
      <button type="button" class="link" data-unpick="${kind}:${i}" title="${escapeHtml(T.ch_remove_channel)}">✕</button></span>`;
    p.el.querySelector(".ch-chips").innerHTML =
      (objs.length > 1 ? objs.map((x, i) => chip("obj", i, pickedLabel(p, x), objColour)).join("") : "") +
      (mes.length > 1 ? mes.map((m, i) => chip("mes", i, measureName(p, m), mesColour)).join("") : "");
  }

  // Measures of every analyzer the picked channels have (a channel without a measure has no line for it).
  async function loadMeasures(p) {
    const analyzers = new Set();
    for (const x of p.cfg.objects) for (const a of p.known.get(keyOf(x.server_id, x.id))?.analyzers || []) analyzers.add(a);
    p.measures = [];
    p.filled = null;
    if (analyzers.size) {
      try {
        p.measures = await measuresOf(p.cfg.objects[0], analyzers);
      } catch (err) {
        showText(p, err.message, true);
      }
    }
    renderMeasures(p);
    loadFilled(p);
  }

  // Which measures have values in the chart's period: the empty ones are greyed, empty groups go last.
  // Asked again when the channels or the period change, not on every refresh (kept FILLED_TTL_MS).
  const FILLED_TTL_MS = 5 * 60_000;
  const filledCache = new Map();
  async function loadFilled(p) {
    if (!p.measures.length) return;
    const body = { objects: p.cfg.objects, analyzer_ids: p.measures.map((a) => a.id), hours: p.cfg.hours };
    const key = JSON.stringify(body);
    const hit = filledCache.get(key);
    if (!hit || Date.now() - hit.at > FILLED_TTL_MS) {
      filledCache.set(key, { at: Date.now(), data: api("/api/charts/filled", body).catch((err) => { filledCache.delete(key); throw err; }) });
    }
    try {
      const data = await filledCache.get(key).data;
      if (JSON.stringify({ objects: p.cfg.objects, analyzer_ids: p.measures.map((a) => a.id), hours: p.cfg.hours }) !== key) return;
      p.filled = data;
      renderMeasures(p);
    } catch { /* without it the list is simply not greyed */ }
  }

  // An analyzer missing from `filled` could not be checked: it is shown as having values.
  const groupFilled = (p, a) => !p.filled || !(a.id in p.filled) || p.filled[a.id].length > 0;
  const measureFilled = (p, a, m) => !p.filled || !(a.id in p.filled) || p.filled[a.id].includes(m.column);
  const orderedGroups = (p) => [...p.measures.filter((a) => groupFilled(p, a)), ...p.measures.filter((a) => !groupFilled(p, a))];
  const flatMeasures = (p) => orderedGroups(p).flatMap((a) => a.measures.map((m) => ({ ...m, aid: a.id, analyzer: a.name })));
  const measureInfo = (p, m) => flatMeasures(p).find((x) => x.aid === m.analyzer_id && x.column === m.column);
  const measureName = (p, m) => measureInfo(p, m)?.name || m.column;

  function renderMeasures(p) {
    const known = new Set(flatMeasures(p).map((m) => `${m.aid}|${m.column}`));
    if (p.measures.length) p.cfg.measures = p.cfg.measures.filter((m) => known.has(mesKey(m)));
    else if (!p.cfg.objects.length) p.cfg.measures = [];
    const n = p.cfg.measures.length;
    p.pkMes.setLabel(n === 0 ? T.ch_pick : n === 1 ? measureName(p, p.cfg.measures[0]) : T.ch_n_measures.replace("{n}", n));
    for (const b of p.el.querySelectorAll('[data-step="mes"]')) b.disabled = n > 1;
    p.pkMes.setGroups(orderedGroups(p).map((a) => {
      const empty = !groupFilled(p, a);
      return { key: String(a.id), label: empty ? `${a.name} — ${T.ch_empty_group}` : a.name, muted: empty,
        items: a.measures.map((m) => ({ value: `${a.id}|${m.column}`, label: `${m.name}${m.unit ? `, ${m.unit}` : ""}`,
          muted: !measureFilled(p, a, m) })) };
    }));
    p.pkMes.setSelected(p.cfg.measures.map(mesKey));
    renderChips(p);
  }

  function wire(p) {
    p.el.querySelector(".ch-src").addEventListener("change", async (e) => {
      const n = nodes[Number(e.target.value)];
      if (!n) return;
      // Picked channels stay: one chart can compare channels of several nodes and servers.
      Object.assign(p.cfg, { server_id: n.server_id, node_id: n.id });
      renderSources(p);
      save();
      await loadObjects(p);
    });
    const chips = p.el.querySelector(".ch-chips");
    chips.addEventListener("click", (e) => {
      const b = e.target.closest("[data-unpick]");
      if (!b) return;
      const [kind, i] = b.dataset.unpick.split(":");
      if (kind === "obj") setObjects(p, p.cfg.objects.filter((_, j) => j !== Number(i)));
      else setMeasures(p, p.cfg.measures.filter((_, j) => j !== Number(i)));
    });
    // A chip points at its lines on the chart.
    for (const [ev, type] of [["mouseover", "highlight"], ["mouseout", "downplay"]]) {
      chips.addEventListener(ev, (e) => {
        const chip = e.target.closest(".ch-chip");
        if (!chip || !p.chart) return;
        const ids = (p.lines || []).filter((l) => (chip.dataset.kind === "obj" ? l.oi : l.mi) === Number(chip.dataset.i)).map((l) => l.id);
        if (ids.length) p.chart.dispatchAction({ type, seriesId: ids });
      });
    }
    for (const b of p.el.querySelectorAll("[data-step]")) {
      b.addEventListener("click", () => (b.dataset.step === "obj" ? stepObject : stepMeasure)(p, Number(b.dataset.dir)));
    }
    for (const b of p.el.querySelectorAll(".ch-periods button")) {
      b.addEventListener("click", () => { p.cfg.hours = Number(b.dataset.h); applyLook(p); save(); loadData(p); loadFilled(p); });
    }
    for (const b of p.el.querySelectorAll(".ch-sizes button")) {
      b.addEventListener("click", () => { p.cfg.size = b.dataset.size; applyLook(p); save(); });
    }
    for (const b of p.el.querySelectorAll("[data-move]")) b.addEventListener("click", () => move(p, Number(b.dataset.move)));
    p.el.querySelector("[data-remove]").addEventListener("click", () => remove(p));
    // The chart follows its box: size buttons, window width, the grid reflowing.
    new ResizeObserver(() => p.chart && p.chart.resize()).observe(p.el.querySelector(".ch-chart"));
  }

  // The channel list shows the current source: its ticks replace the picked channels of that source,
  // those of other sources stay.
  function pickObjects(p, ids) {
    const others = p.cfg.objects.filter((x) => x.server_id !== p.cfg.server_id || x.node_id !== p.cfg.node_id);
    const mine = ids.map((id) => p.cfg.objects.find((x) => x.server_id === p.cfg.server_id && x.id === id)
      || { server_id: p.cfg.server_id, node_id: p.cfg.node_id, id });
    const next = ids.length ? [...others, ...mine] : [];  // Reset takes every channel off
    if (next.length > MAX_OBJECTS || next.length * Math.max(1, p.cfg.measures.length) > MAX_LINES) {
      renderObjects(p);
      return flash(p, T.ch_too_many_lines.replace("{n}", MAX_LINES));
    }
    setObjects(p, next);
  }

  function pickMeasures(p, keys) {
    const next = keys.map((k) => ({ analyzer_id: Number(k.split("|")[0]), column: k.slice(k.indexOf("|") + 1) }));
    if (next.length * Math.max(1, p.cfg.objects.length) > MAX_LINES) {
      renderMeasures(p);
      return flash(p, T.ch_too_many_lines.replace("{n}", MAX_LINES));
    }
    const rawUnits = new Set(next.map((m) => (measureInfo(p, m)?.unit || "").toLowerCase()));
    if (rawUnits.size > MAX_UNITS) {
      renderMeasures(p);
      return flash(p, T.ch_too_many_units.replace("{n}", MAX_UNITS));
    }
    setMeasures(p, next);
  }

  async function setObjects(p, list) {
    p.cfg.objects = list;
    renderObjects(p);
    renderChips(p);
    save();
    await loadMeasures(p);
    save();
    loadData(p);
  }

  function setMeasures(p, list) {
    p.cfg.measures = list;
    renderMeasures(p);
    renderObjects(p);
    save();
    loadData(p);
  }

  function flash(p, text) {
    const info = p.el.querySelector(".ch-info");
    info.textContent = text;
    info.classList.add("error");
    setTimeout(() => info.classList.remove("error"), 3000);
  }

  // With one channel picked, the arrows step to the next channel of the source that has the chart's measures.
  function stepObject(p, dir) {
    const list = p.objects.filter((o) => hasMeasure(o, p.cfg));
    if (!list.length || p.cfg.objects.length > 1) return;
    let i = list.findIndex((o) => p.cfg.objects.some((x) => x.server_id === p.cfg.server_id && x.id === o.id));
    if (i < 0) i = dir > 0 ? -1 : list.length;
    setObjects(p, [{ server_id: p.cfg.server_id, node_id: p.cfg.node_id, id: list[(i + dir + list.length) % list.length].id }]);
  }

  function stepMeasure(p, dir) {
    const list = flatMeasures(p);
    if (!list.length || p.cfg.measures.length > 1) return;
    const cur = p.cfg.measures[0];
    const i = cur ? list.findIndex((m) => m.aid === cur.analyzer_id && m.column === cur.column) : -1;
    const next = list[(i + dir + list.length) % list.length] || list[0];
    setMeasures(p, [{ analyzer_id: next.aid, column: next.column }]);
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
    if (!c.objects.length || !c.measures.length) {
      info.textContent = "";
      return showText(p, T.ch_pick_hint);
    }
    const seq = ++p.seq;
    let data;
    try {
      data = await api("/api/charts/data", { objects: c.objects, measures: c.measures, hours: c.hours });
    } catch (err) {
      if (seq === p.seq) showText(p, err.message, true);
      return;
    }
    if (seq !== p.seq) return;
    const vals = (s, key) => (data.raw ? s.avg : s[key]).map((x) => x[1]).filter((v) => v !== null);
    const any = data.series.some((s) => s.avg.some((x) => x[1] !== null));
    const error = data.series.find((s) => s.error)?.error;
    // Min / max over the whole chart only when it has one unit; with two they mean nothing together.
    if (data.units.length === 1 && any) {
      const mins = data.series.flatMap((s) => vals(s, "min")), maxs = data.series.flatMap((s) => vals(s, "max"));
      info.textContent = error || T.ch_minmax.replace("{a}", `${fmtValue(Math.min(...mins))} ${data.units[0]}`)
        .replace("{b}", `${fmtValue(Math.max(...maxs))} ${data.units[0]}`);
    } else {
      info.textContent = error || "";
    }
    // A channel or measure without values (no such measure, or nothing recorded) keeps its chip, greyed.
    for (const chip of p.el.querySelectorAll(".ch-chip")) {
      const i = Number(chip.dataset.i);
      const own = data.series.filter((s) => (chip.dataset.kind === "obj"
        ? s.server_id === c.objects[i]?.server_id && s.object_id === c.objects[i]?.id : s.measure === i));
      const empty = !own.some((s) => s.avg.some((x) => x[1] !== null));
      chip.classList.toggle("ch-chip-empty", empty);
      chip.title = empty ? own.find((s) => s.error)?.error || T.ch_no_data : "";
    }
    if (!any) return showText(p, T.ch_no_data);
    const box = p.el.querySelector(".ch-chart");
    if (!p.chart) { box.innerHTML = ""; p.chart = echarts.init(box, null, { renderer: "canvas" }); }
    p.chart.setOption(option(p, data), true);
  }

  function option(p, data) {
    const c = p.cfg;
    const nObj = c.objects.length, nMes = c.measures.length;
    // One line per channel and measure. Its colour follows what varies: the channel, the measure, or both.
    p.lines = data.series.map((s, i) => {
      const oi = c.objects.findIndex((x) => x.server_id === s.server_id && x.id === s.object_id);
      const mi = s.measure;
      const name = nMes === 1 ? pickedLabel(p, c.objects[oi] || { server_id: s.server_id, id: s.object_id })
        : nObj === 1 ? measureName(p, c.measures[mi])
          : `${pickedLabel(p, c.objects[oi] || { server_id: s.server_id, id: s.object_id })} · ${measureName(p, c.measures[mi])}`;
      const colour = PALETTE[(nMes === 1 ? oi : nObj === 1 ? mi : i) % PALETTE.length];
      return { id: `s${i}`, oi, mi, s, name, colour, axis: data.units.indexOf(s.unit) };
    });
    const single = p.lines.length === 1;
    const band = single && !data.raw;  // the min–max band only behind a single line: it would hide the others
    const s0 = data.series[0];
    const series = band ? [
      { id: "band-min", type: "line", data: s0.min, stack: "band", symbol: "none", lineStyle: { opacity: 0 },
        silent: true, tooltip: { show: false } },
      { id: "band-range", type: "line", stack: "band", symbol: "none", silent: true, lineStyle: { opacity: 0 },
        data: s0.max.map(([t, v], i) => [t, v === null || s0.min[i][1] === null ? null : v - s0.min[i][1]]),
        areaStyle: { color: PALETTE[0], opacity: 0.14 } },
    ] : [];
    for (const l of p.lines) {
      series.push({ id: l.id, name: l.name, type: "line", data: l.s.avg, yAxisIndex: l.axis, showSymbol: false,
        lineStyle: { width: 1.6, type: l.axis === 1 ? "dashed" : "solid" }, color: l.colour, connectNulls: false,
        emphasis: { focus: "series" } });
    }
    const both = nObj > 1 && nMes > 1;  // the chips cannot carry the colours then: the chart shows a legend
    const yAxis = data.units.map((u, i) => ({
      type: "value", scale: true, name: u, position: i ? "right" : "left",
      nameTextStyle: { color: MUTED, align: i ? "left" : "right" },
      axisLabel: { color: MUTED, formatter: fmtValue },
      splitLine: { show: i === 0, lineStyle: { color: GRID, type: "dashed" } },
    }));
    return {
      useUTC: true, animation: false,
      textStyle: { fontFamily: "Roboto, Segoe UI, system-ui, sans-serif", color: INK },
      legend: both ? { type: "scroll", top: 0, left: 0, right: 0, icon: "roundRect", itemWidth: 12, itemHeight: 4,
        textStyle: { color: INK, fontSize: 11 }, data: p.lines.map((l) => l.name) } : { show: false },
      grid: { left: 52, right: data.units.length > 1 ? 52 : 14, top: both ? 46 : 26, bottom: 26 },
      tooltip: {
        trigger: "axis", axisPointer: { type: "line", lineStyle: { color: MUTED } }, confine: true,
        backgroundColor: css("--raised"), borderColor: css("--border"), borderWidth: 1, textStyle: { color: INK, fontSize: 12 },
        formatter: (params) => {
          const main = params.filter((x) => /^s\d+$/.test(String(x.seriesId)));
          if (!main.length) return "";
          const lines = [`<b>${fmtTime(main[0].value[0])}</b>`];
          if (nObj === 1) lines.push(escapeHtml(pickedLabel(p, c.objects[0])));
          else if (nMes === 1) lines.push(escapeHtml(measureName(p, c.measures[0])));
          for (const x of main) {
            const l = p.lines.find((y) => y.id === x.seriesId);
            lines.push(`${x.marker}${escapeHtml(x.seriesName)}: <b>${fmtValue(x.value[1])}</b> ${escapeHtml(l?.s.unit || "")}`);
          }
          if (band) {
            lines.push(`<span style="opacity:.75">${escapeHtml(T.ch_minmax.replace("{a}", fmtValue(s0.min[main[0].dataIndex][1]))
              .replace("{b}", fmtValue(s0.max[main[0].dataIndex][1])))}</span>`);
          }
          return lines.join("<br>");
        },
      },
      xAxis: {
        type: "time", min: data.from, max: data.to,
        axisLine: { lineStyle: { color: GRID } }, axisTick: { show: false },
        axisLabel: { color: MUTED, hideOverlap: true, formatter: (v) => {
          const d = new Date(v);
          return c.hours > 24 ? `${pad(d.getUTCDate())}.${pad(d.getUTCMonth() + 1)}` : `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
        } },
        splitLine: { show: true, lineStyle: { color: GRID } },
      },
      yAxis,
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
    addPanel({ id: newId(), ...src, objects: [], measures: [], hours: 24,
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
