"use strict";
(() => {
  const T = window.I18N;
  const $ = (sel) => document.querySelector(sel);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const FLAP_PER_DAY = 20;  // as in patterns.py
  const WD = T.pt_wd.split(","), WD_LONG = T.pt_wd_long.split(","), WD_LONG_NOM = T.pt_wd_nom.split(",");
  // Priority bands of MultiProbe, the same colours as the overview.
  const prioColor = (p) => p >= 400 ? "#e54848" : p >= 300 ? "#ec835a" : p >= 200 ? "#fab219" : "#50b3d0";

  const escapeHtml = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const LANG = document.documentElement.lang || "en";
  const fmt = (s, vars) => s.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");

  async function api(path, body) {
    const resp = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (resp.status === 401) { location.href = "/login"; throw new Error("session"); }
    const data = await resp.json();
    if (!resp.ok) {
      const d = data.detail;
      throw new Error(Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d || data.message || resp.statusText);
    }
    return data;
  }

  function fmtDuration(sec) {
    if (sec < 60) return fmt(T.pt_dur_s, { n: sec });
    if (sec < 3600) return fmt(T.pt_dur_min, { n: Math.round(sec / 60) });
    return fmt(T.pt_dur_h, { n: (sec / 3600).toFixed(1) });
  }

  let days = 14;
  let result = null;
  let heatView = "weekdays";
  for (const b of document.querySelectorAll("#days button")) {
    b.addEventListener("click", () => {
      days = Number(b.dataset.days);
      for (const x of document.querySelectorAll("#days button")) x.classList.toggle("active", x === b);
    });
  }

  // --- Run --------------------------------------------------------------------------------------------
  let runSeq = 0;
  async function run() {
    const seq = ++runSeq;
    const btn = $("#run");
    btn.disabled = true;
    $("#pt-bar").hidden = false;
    $("#pt-status").textContent = fmt(T.pt_running, { p: 0, n: 0 });
    try {
      const { id } = await api("/api/patterns", { days, level: Number($("#level").value) });
      for (;;) {
        await new Promise((r) => setTimeout(r, 1500));
        if (seq !== runSeq) return;
        const st = await api(`/api/patterns/${id}`);
        $("#pt-bar .bar-fill").style.width = `${st.percent}%`;
        if (st.state === "error") throw new Error(st.error);
        if (st.state === "done") { render(st.result); break; }
        $("#pt-status").textContent = fmt(T.pt_running, { p: st.percent, n: st.rows });
      }
    } catch (err) {
      $("#pt-status").innerHTML = `<span class="error">${escapeHtml(err.message)}</span>`;
    } finally {
      if (seq === runSeq) { btn.disabled = false; $("#pt-bar").hidden = true; }
    }
  }

  // --- Result -----------------------------------------------------------------------------------------
  function render(r) {
    $("#pt-status").textContent = fmt(T.pt_summary, { events: r.events.toLocaleString(LANG), channels: r.channels, from: r.period[0], to: r.period[1] })
      + (r.busy_hours.length ? ` ${fmt(T.pt_busy, { hours: r.busy_hours.map((h) => `${String(h).padStart(2, "0")}:00`).join(", ") })}` : "");
    $("#pt-result").hidden = false;
    renderFindings(r);
    result = r;
    renderHeat();
    renderFlapping(r);
  }

  function when(f) {
    const time = `${f.time} <span class="muted">${escapeHtml(fmt(T.pt_spread, { n: f.spread }))}</span>`;
    return f.kind === "daily" ? fmt(T.pt_daily, { time }) : fmt(T.pt_weekly, { time, weekday: escapeHtml(WD_LONG[f.weekday]) });
  }

  function renderFindings(r) {
    $("#findings-count").textContent = r.findings.length || "";
    if (!r.findings.length) {
      $("#findings").innerHTML = `<p class="muted">${escapeHtml(T.pt_no_findings)}</p>`;
      return;
    }
    $("#findings").innerHTML = r.findings.map((f) => {
      const advice = [f.kind === "daily" ? T.pt_adv_daily : T.pt_adv_weekly];
      if (f.scope === "stream") advice.unshift(T.pt_adv_stream);
      if (f.scope === "node") advice.unshift(T.pt_adv_node);
      const share = fmt(f.kind === "daily" ? T.pt_days_of : T.pt_weeks_of, { n: f.days, of: f.of_days });
      const where = [f.server, f.node].filter(Boolean).join(" · ");
      const shown = f.items.slice(0, 6);
      return `<div class="pt-finding" style="--pr:${prioColor(f.priority)}">
        <div class="pt-when"><b>${when(f)}</b> · ${escapeHtml(share)}
          <span class="pt-scope">${escapeHtml(T[`pt_scope_${f.scope}`])}</span>
          ${where ? `<span class="muted">${escapeHtml(where)}</span>` : ""}</div>
        <ul class="pt-items">${shown.map((i) => `<li><span class="ov-dot" style="background:${prioColor(i.priority)}"></span>
          <b>${escapeHtml(i.object)}</b>${i.stream ? ` <span class="muted">${escapeHtml(i.stream)}</span>` : ""} — ${escapeHtml(i.alarm)}
          <span class="muted">· ${escapeHtml(fmt(T.pt_typical, { d: fmtDuration(i.median_sec) }))} · ${escapeHtml(fmt(T.pt_starts, { n: i.count }))}</span></li>`).join("")}
          ${f.items.length > shown.length ? `<li class="muted">${escapeHtml(fmt(T.pt_more, { n: f.items.length - shown.length }))}</li>` : ""}</ul>
        ${advice.map((a) => `<p class="pt-advice">${escapeHtml(a)}</p>`).join("")}
      </div>`;
    }).join("");
  }

  const heat = echarts.init($("#heat"));
  window.addEventListener("resize", () => heat.resize());
  // Columns are the hours of the day. Rows: the weekdays, each cell the sum over every such weekday of the
  // period (28 days = 4 Mondays), or every date of the period one by one.
  const hh = (h) => String(h).padStart(2, "0");
  function renderHeat() {
    const r = result;
    const byDate = heatView === "dates";
    const rows = byDate ? r.heat_dates.map((d) => d.hours) : r.heat;
    const labels = byDate ? r.heat_dates.map((d) => `${WD[d.weekday]} ${d.date}`) : WD;
    const datesOf = (wd) => r.heat_dates.filter((d) => d.weekday === wd).map((d) => d.date);
    $("#heat-hint").textContent = byDate ? T.pt_heat_hint_dates : T.pt_heat_hint_weekdays;
    $("#heat").style.height = `${byDate ? Math.max(250, rows.length * 20 + 60) : 250}px`;
    // Colour by the square root: a few very busy hours would otherwise leave every other cell alike.
    const data = [], max = Math.sqrt(Math.max(1, ...rows.flat()));
    rows.forEach((row, d) => row.forEach((n, h) => data.push([h, d, n ? Math.sqrt(n) : "-", n])));
    heat.setOption({
      animation: false,
      tooltip: { formatter: (p) => {
        const [h, d, , n] = p.value;
        const head = `<b>${byDate ? labels[d] : WD_LONG_NOM[d]}, ${hh(h)}:00–${hh(h + 1 === 24 ? 0 : h + 1)}:00</b>`;
        const sub = byDate ? "" : `<br><span style="opacity:.75">${escapeHtml(fmt(T.pt_heat_over, { dates: datesOf(d).join(", ") }))}</span>`;
        return `${head}<br>${escapeHtml(fmt(T.pt_heat_starts, { n }))}${sub}`;
      },
        backgroundColor: css("--raised"), borderColor: css("--border"), textStyle: { color: css("--text"), fontSize: 12 } },
      grid: { left: byDate ? 70 : 36, right: 8, top: 6, bottom: 40 },
      xAxis: { type: "category", data: [...Array(24).keys()].map(hh), name: T.pt_heat_hour, nameLocation: "middle", nameGap: 26,
        nameTextStyle: { color: css("--muted"), fontSize: 11 },
        axisLabel: { color: css("--muted"), fontSize: 10 }, axisLine: { show: false }, axisTick: { show: false }, splitArea: { show: false } },
      yAxis: { type: "category", data: labels, inverse: true, axisLabel: { color: css("--muted"), fontSize: byDate ? 10 : 12 },
        axisLine: { show: false }, axisTick: { show: false } },
      visualMap: { min: 0, max, show: false, dimension: 2, inRange: { color: [css("--field"), "#ec835a", "#e54848"] } },
      series: [{ type: "heatmap", data, itemStyle: { borderColor: css("--card"), borderWidth: 2, borderRadius: 3 }, emphasis: { disabled: true } }],
    }, true);
    heat.resize();  // the block was hidden when the chart was created; the "by date" view is taller
  }

  function renderFlapping(r) {
    $("#flapping-count").textContent = r.flapping_total || "";
    $("#flapping-hint").textContent = fmt(T.pt_flapping_hint, { n: FLAP_PER_DAY });
    $("#flapping").innerHTML = r.flapping.length ? `<table class="pt-flap"><tbody>${r.flapping.map((f) => `<tr>
        <td><span class="ov-dot" style="background:${prioColor(f.priority)}"></span></td>
        <td><b>${escapeHtml(f.object)}</b>${f.stream ? `<br><span class="muted">${escapeHtml([f.server, f.stream].filter(Boolean).join(" · "))}</span>` : ""}</td>
        <td>${escapeHtml(f.alarm)}</td>
        <td class="num-cell"><b>${Math.round(f.per_day).toLocaleString(LANG)}</b> <span class="muted">${escapeHtml(T.pt_per_day)}</span></td>
      </tr>`).join("")}</tbody></table>${r.flapping_total > r.flapping.length ? `<p class="muted">${escapeHtml(fmt(T.pt_more, { n: r.flapping_total - r.flapping.length }))}</p>` : ""}`
      : `<p class="muted">${escapeHtml(T.pt_no_flapping)}</p>`;
  }

  for (const b of document.querySelectorAll("#heat-view button")) {
    b.addEventListener("click", () => {
      heatView = b.dataset.view;
      for (const x of document.querySelectorAll("#heat-view button")) x.classList.toggle("active", x === b);
      if (result) renderHeat();
    });
  }
  // --- Measurements -----------------------------------------------------------------------------------
  // One measure for every channel that has it: hours regularly off the channel's usual level, and drift.
  async function loadMeasures() {
    const sel = $("#mp-measure");
    sel.innerHTML = `<option value="">${escapeHtml(T.loading)}</option>`;
    $("#mp-run").disabled = true;
    let groups;
    try {
      groups = await api("/api/patterns/measures");
    } catch (err) {
      sel.innerHTML = "";
      $("#mp-status").innerHTML = `<span class="error">${escapeHtml(err.message)}</span>`;
      return;
    }
    // Groups and measures without values in the last day are greyed (as on the Charts page).
    sel.innerHTML = `<option value="">${escapeHtml(T.ch_pick)}</option>` + groups.map((g) =>
      `<optgroup label="${escapeHtml(`${g.name} · ${fmt(T.mp_channels, { n: g.channels })}${g.filled ? "" : ` — ${T.ch_empty_group}`}`)}"${g.filled ? "" : ' class="ch-off"'}>${
        g.measures.map((m) => `<option value="${g.id}|${escapeHtml(m.column)}"${m.filled ? "" : ' class="ch-off"'}>${escapeHtml(m.name)}${m.unit ? `, ${escapeHtml(m.unit)}` : ""}</option>`).join("")
      }</optgroup>`).join("");
    $("#mp-run").disabled = false;
  }

  let mpSeq = 0;
  async function runMeasures() {
    const v = $("#mp-measure").value;
    if (!v) { $("#mp-status").textContent = T.mp_pick; return; }
    const seq = ++mpSeq;
    const btn = $("#mp-run");
    btn.disabled = true;
    $("#mp-bar").hidden = false;
    $("#mp-result").innerHTML = "";
    $("#mp-status").textContent = `${T.loading} 0%`;
    try {
      const { id } = await api("/api/patterns/measure", { days, analyzer_id: Number(v.split("|")[0]), column: v.slice(v.indexOf("|") + 1) });
      for (;;) {
        await new Promise((r) => setTimeout(r, 1200));
        if (seq !== mpSeq) return;
        const st = await api(`/api/patterns/${id}`);
        $("#mp-bar .bar-fill").style.width = `${st.percent}%`;
        if (st.state === "error") throw new Error(st.error);
        if (st.state === "done") { renderMeasures(st.result); break; }
        $("#mp-status").textContent = `${T.loading} ${st.percent}%`;
      }
    } catch (err) {
      $("#mp-status").innerHTML = `<span class="error">${escapeHtml(err.message)}</span>`;
    } finally {
      if (seq === mpSeq) { btn.disabled = false; $("#mp-bar").hidden = true; }
    }
  }

  const num = (v) => (Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 100) / 100).toLocaleString(LANG);
  function renderMeasures(r) {
    const hist = r.history_days ? fmt(T.mp_history, { n: r.history_days, from: r.history_from }) : "";
    $("#mp-status").textContent = fmt(T.mp_summary, { channels: r.channels, data: r.with_data, flat: r.flat });
    const common = r.common.map((c) => `<div class="mp-common">${escapeHtml(fmt(c.sign < 0 ? T.mp_common_down : T.mp_common_up,
      { n: c.channels, of: c.of, p: Math.round(Math.abs(c.median) * 100) }))}</div>`).join("");
    const items = r.findings.map((f) => {
      const lines = f.hours.map((h) => {
        const range = `${hh(h.from)}:00–${hh(h.to === 24 ? 0 : h.to)}:00`;
        return `<li class="${h.sign < 0 ? "mp-down" : "mp-up"}">${escapeHtml(fmt(h.sign < 0 ? T.mp_below : T.mp_above,
          { range, p: Math.round(Math.abs(h.deviation) * 100), v: `${num(h.value)} ${r.unit}`, n: h.days, of: h.of_days }))}</li>`;
      });
      if (f.trend) {
        lines.push(`<li class="${f.trend < 0 ? "mp-down" : "mp-up"}">${escapeHtml(fmt(f.trend < 0 ? T.mp_trend_down : T.mp_trend_up,
          { p: Math.round(Math.abs(f.trend) * 100), a: `${num(f.trend_from)} ${r.unit}`, b: `${num(f.trend_to)} ${r.unit}` }))}</li>`);
      }
      const where = [f.server, f.stream].filter(Boolean).join(" · ");
      return `<li><b>${escapeHtml(f.object)}</b>${where ? ` <span class="muted">${escapeHtml(where)}</span>` : ""}
        <span class="muted">· ${escapeHtml(fmt(T.mp_usual, { v: `${num(f.usual)} ${r.unit}` }))}</span><ul>${lines.join("")}</ul></li>`;
    }).join("");
    $("#mp-result").innerHTML = `<p class="muted mp-history">${escapeHtml(hist)}${r.history_days < 7 ? ` ${escapeHtml(T.mp_history_short)}` : ""}</p>
      ${common}${items ? `<ul class="mp-list">${items}</ul>` : `<p class="muted">${escapeHtml(T.mp_none)}</p>`}`;
  }

  $("#run").addEventListener("click", run);
  $("#mp-run").addEventListener("click", runMeasures);
  $("#scope")?.addEventListener("change", async (e) => {
    const v = e.target.value;
    try { await api("/api/scope", { server_id: v === "" ? null : Number(v) }); } catch { return; }
    $("#pt-result").hidden = true;
    $("#pt-status").textContent = "";
    $("#mp-result").innerHTML = "";
    $("#mp-status").textContent = "";
    loadMeasures();
  });
  $("#no-servers").hidden = window.HAS_SERVERS;
  $("#run").disabled = !window.HAS_SERVERS;
  $("#mp-card").hidden = !window.HAS_SERVERS;
  if (window.HAS_SERVERS) loadMeasures();
})();
