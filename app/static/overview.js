"use strict";
(() => {
  const T = window.I18N;
  const $ = (sel) => document.querySelector(sel);
  const REFRESH_MS = 30_000;

  // Status palette (fixed meaning, always shown with a label).
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const STATES = [
    { key: "error", color: "#e54848", label: T.ov_state_error },
    { key: "warning", color: "#ec835a", label: T.ov_state_warning },
    { key: "caution", color: "#fab219", label: T.ov_state_caution },
    { key: "ok", color: "#3fae5a", label: T.ov_state_ok },
  ];
  const stateInfo = Object.fromEntries(STATES.map((s) => [s.key, s]));

  const escapeHtml = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(path, body) {
    const resp = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (resp.status === 401) { location.href = "/login"; throw new Error("session"); }
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || data.message || resp.statusText);
    return data;
  }

  function fmtDuration(sec) {
    if (sec === null || sec === undefined) return "—";
    const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const hms = [h, m, s].map((v) => String(v).padStart(2, "0")).join(":");
    return d ? `${d}d ${hms}` : hms;
  }

  // --- Current state ----------------------------------------------------------------
  const pie = echarts.init($("#pie"));
  window.addEventListener("resize", () => pie.resize());
  let state = null;
  let stateFilter = null; // clicking a sector or a legend entry filters the list

  function renderPie() {
    const data = STATES.map((s) => ({ name: s.label, value: state.counts[s.key], key: s.key, itemStyle: { color: s.color } }));
    const total = data.reduce((a, d) => a + d.value, 0);
    pie.setOption({
      animation: false,
      tooltip: { trigger: "item", formatter: (p) => `${escapeHtml(p.name)}: <b>${p.value}</b> (${p.percent}%)` },
      title: { text: String(total), subtext: T.ov_channels, left: "center", top: "39%",
               textStyle: { fontSize: 26, fontWeight: 700, color: css("--text") }, subtextStyle: { color: css("--muted") } },
      series: [{
        type: "pie", radius: ["58%", "84%"], avoidLabelOverlap: true, label: { show: false },
        itemStyle: { borderColor: css("--card"), borderWidth: 2 }, data: data.filter((d) => d.value),
      }],
    }, true);
    $("#legend").innerHTML = STATES.map((s) => `
      <li data-state="${s.key}" class="${stateFilter === s.key ? "active" : ""}">
        <i style="background:${s.color}"></i><span>${escapeHtml(s.label)}</span><b>${state.counts[s.key]}</b>
      </li>`).join("");
  }

  pie.on("click", (p) => setFilter(p.data.key));
  $("#legend").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-state]");
    if (li) setFilter(li.dataset.state);
  });
  $("#filter-clear").addEventListener("click", () => setFilter(null));

  function setFilter(key) {
    stateFilter = stateFilter === key ? null : key;
    renderPie();
    renderNow();
  }

  function channelRow(c) {
    const st = stateInfo[c.state];
    return `
      <div class="ov-item">
        <span class="ov-dot" style="background:${st.color}" title="${escapeHtml(st.label)}"></span>
        <div class="ov-main">
          <button type="button" class="link ov-name" data-server="${c.server_id}" data-node="${c.node_id}" data-id="${c.id}" data-name="${escapeHtml(c.name)}">${escapeHtml(c.name)}</button>
          <span class="muted">${escapeHtml(state.servers.length > 1 ? `${c.server} · ${c.stream}` : c.stream)}</span>
          ${c.alarms.length ? `<div class="ov-alarms">${c.alarms.map(escapeHtml).join(", ")}</div>` : ""}
        </div>
        <div class="ov-side">
          <span class="ov-state" style="color:${st.color}">${escapeHtml(st.label)}</span>
          ${c.since_sec !== null ? `<span class="muted" title="${escapeHtml(c.since)}">${fmtDuration(c.since_sec)}</span>` : ""}
        </div>
      </div>`;
  }

  function renderNow() {
    const list = state.channels.filter((c) => (stateFilter ? c.state === stateFilter : c.state !== "ok"));
    $("#now-count").textContent = list.length ? String(list.length) : "";
    $("#filter-clear").hidden = !stateFilter;
    $("#now-list").innerHTML = list.length ? list.map(channelRow).join("")
      : `<p class="muted">${escapeHtml(stateFilter ? T.no_rows : T.ov_all_ok)}</p>`;
    $("#streams-card").hidden = !state.streams.length;
    $("#stream-list").innerHTML = state.streams.map((s) => channelRow({ ...s, name: `${s.name}`, stream: s.kind })).join("");
  }

  async function loadState() {
    try {
      state = await api("/api/overview/state");
      $("#updated").textContent = state.updated;
      $("#no-servers").hidden = state.servers.length > 0;
      $("#server-errors").innerHTML = state.errors.map((e) =>
        `<div class="error">${escapeHtml(T.err_server_unavailable.replace("{name}", e.server))}: ${escapeHtml(e.message)}</div>`).join("");
      renderPie();
      renderNow();
    } catch (err) {
      $("#now-list").innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
    }
  }

  // --- Most problematic channels ------------------------------------------------------
  let topHours = 24;
  let topSeq = 0;
  async function loadTop() {
    const seq = ++topSeq;
    $("#top-rows").innerHTML = `<tr><td colspan="8" class="muted">${escapeHtml(T.ov_calculating)}</td></tr>`;
    try {
      const data = await api(`/api/overview/top?hours=${topHours}`);
      if (seq !== topSeq) return;
      const max = Math.max(1, ...data.rows.map((r) => r.outage_sec));
      $("#top-rows").innerHTML = data.rows.length ? data.rows.map((r, i) => `
        <tr>
          <td class="muted">${i + 1}</td>
          <td><button type="button" class="link ov-name" data-server="${r.server_id}" data-node="${r.node_id}" data-id="${r.id}" data-name="${escapeHtml(r.name)}">${escapeHtml(r.name)}</button></td>
          <td>${escapeHtml(state && state.servers.length > 1 ? `${r.server} · ${r.stream}` : r.stream)}</td>
          <td class="num-cell bad">${fmtDuration(r.outage_sec)}</td>
          <td class="ov-share"><span style="width:${(100 * r.outage_sec / max).toFixed(1)}%"></span></td>
          <td class="num-cell">${r.availability.toFixed(2)} %</td>
          <td class="num-cell">${r.incidents}</td>
          <td class="num-cell">${fmtDuration(r.degraded_sec)}</td>
        </tr>`).join("") : `<tr><td colspan="8" class="muted">${escapeHtml(T.no_problems)}</td></tr>`;
      $("#top-note").textContent = T.ov_top_note.replace("{n}", data.total_with_problems);
    } catch (err) {
      if (seq === topSeq) $("#top-rows").innerHTML = `<tr><td colspan="8"><div class="error">${escapeHtml(err.message)}</div></td></tr>`;
    }
  }
  for (const b of document.querySelectorAll("#top-period button")) {
    b.addEventListener("click", () => {
      topHours = Number(b.dataset.hours);
      for (const x of document.querySelectorAll("#top-period button")) x.classList.toggle("active", x === b);
      loadTop();
    });
  }

  // --- Channel actions menu ----------------------------------------------------------------
  const menu = $("#menu");
  let templates = null;
  let target = null;

  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".ov-name");
    if (btn) {
      e.stopPropagation();
      target = { server_id: Number(btn.dataset.server || 0), node_id: Number(btn.dataset.node), id: btn.dataset.id, name: btn.dataset.name };
      if (templates === null) templates = await api("/api/templates").catch(() => []);
      openMenu(btn);
      return;
    }
    if (!e.target.closest("#menu")) menu.hidden = true;
  });

  function openMenu(anchor) {
    const usable = templates.filter((t) => t.type === "alarms" || t.type === "measures" || t.type === "scte");
    menu.innerHTML = `
      <div class="menu-title">${escapeHtml(target.name)}</div>
      <button type="button" data-act="quick">${escapeHtml(T.ov_quick_report)}</button>
      <button type="button" data-act="wizard">${escapeHtml(T.ov_open_wizard)}</button>
      <div class="menu-sep">${escapeHtml(T.ov_by_template)}</div>
      ${usable.length ? usable.map((t) => `<button type="button" data-act="tpl" data-tpl="${t.id}">${escapeHtml(t.name)}
          <em>${escapeHtml({ alarms: T.type_alarms, measures: T.type_measures, scte: T.type_scte }[t.type])}</em></button>`).join("")
        : `<div class="menu-empty">${escapeHtml(T.ov_no_templates)}</div>`}`;
    menu.hidden = false;
    const r = anchor.getBoundingClientRect();
    menu.style.left = `${Math.min(r.left + window.scrollX, window.scrollX + document.documentElement.clientWidth - menu.offsetWidth - 12)}px`;
    menu.style.top = `${r.bottom + window.scrollY + 4}px`;
  }

  menu.addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    const objects = { server_id: target.server_id, node_id: target.node_id, object_ids: [target.id] };
    try {
      b.disabled = true;
      if (b.dataset.act === "quick") {
        const { id } = await api("/api/report/quick?hours=24", objects);
        location.href = `/report/${id}`;
      } else if (b.dataset.act === "wizard") {
        location.href = `/reports?server=${target.server_id}&node=${target.node_id}&select=${encodeURIComponent(target.id)}`;
      } else {
        const { id } = await api(`/api/templates/${b.dataset.tpl}/run`, objects);
        location.href = `/report/${id}`;
      }
    } catch (err) {
      b.disabled = false;
      alert(err.message);
    }
  });

  // --- Refresh -----------------------------------------------------------------------------
  let timer = null;
  function schedule() {
    clearInterval(timer);
    if ($("#auto").checked) timer = setInterval(loadState, REFRESH_MS);
  }
  $("#auto").addEventListener("change", schedule);
  $("#refresh").addEventListener("click", () => { loadState(); loadTop(); });

  // Which connected server the overview shows (also the Recordings page's pick).
  $("#scope")?.addEventListener("change", async (e) => {
    const v = e.target.value;
    try {
      await api("/api/scope", { server_id: v === "" ? null : Number(v) });
    } catch (err) {
      $("#server-errors").innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
      return;
    }
    loadState();
    loadTop();
  });

  loadState();
  loadTop();
  schedule();
})();
