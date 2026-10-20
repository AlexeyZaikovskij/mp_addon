"use strict";
(() => {
  const T = window.I18N;
  const $ = (sel) => document.querySelector(sel);

  // Categorical palette, fixed order (validated for colour-vision deficiencies on adjacent pairs).
  const PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
  const INK = "#1d2433";
  const MUTED = "#6b7486";
  const GRID = "#e7ebf1";

  async function getJson(url) {
    const resp = await fetch(url);
    if (resp.status === 401) { location.href = "/login"; throw new Error("session"); }
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    return resp.json();
  }
  const escapeHtml = (s) =>
    String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Times arrive as the MultiProbe-zone wall clock encoded as UTC, so read them with UTC getters.
  const pad = (n) => String(n).padStart(2, "0");
  function fmtTime(ms, withSeconds = true) {
    const d = new Date(ms);
    const date = `${pad(d.getUTCDate())}.${pad(d.getUTCMonth() + 1)}.${d.getUTCFullYear()}`;
    const time = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}${withSeconds ? `:${pad(d.getUTCSeconds())}` : ""}`;
    return `${date} ${time}`;
  }
  function fmtValue(v) {
    if (v === null || v === undefined) return "—";
    const abs = Math.abs(v);
    if (abs >= 1e9) return `${(v / 1e9).toFixed(2)} G`;
    if (abs >= 1e6) return `${(v / 1e6).toFixed(2)} M`;
    if (abs >= 1e4) return `${(v / 1e3).toFixed(1)} k`;
    return String(Math.round(v * 1000) / 1000);
  }

  // --- Charts -------------------------------------------------------------------
  const chartsBox = $("#charts");
  const charts = [];

  function chartOption(c, data) {
    const single = c.series.length === 1 && !data.raw;
    const series = [];
    if (single) {
      // Min–max band behind the average line: an invisible "min" line plus a stacked range area.
      const s = c.series[0];
      series.push({
        id: "band-min", type: "line", data: s.min, stack: "band", symbol: "none",
        lineStyle: { opacity: 0 }, silent: true, tooltip: { show: false },
      });
      series.push({
        id: "band-range", type: "line", stack: "band", symbol: "none", silent: true,
        data: s.max.map(([t, v], i) => [t, v === null || s.min[i][1] === null ? null : v - s.min[i][1]]),
        lineStyle: { opacity: 0 }, areaStyle: { color: PALETTE[0], opacity: 0.14 },
      });
    }
    c.series.forEach((s, i) => series.push({
      id: `s${i}`, name: s.name, type: "line", data: s.avg, showSymbol: false, symbolSize: 8,
      lineStyle: { width: 2 }, color: PALETTE[i], connectNulls: false, emphasis: { focus: "series" },
    }));

    return {
      useUTC: true,
      animation: false,
      textStyle: { fontFamily: "Segoe UI, system-ui, sans-serif", color: INK },
      grid: { left: 64, right: 24, top: c.series.length > 1 ? 44 : 20, bottom: 64 },
      legend: c.series.length > 1
        ? { type: "scroll", top: 0, left: 0, icon: "roundRect", itemWidth: 14, itemHeight: 4, textStyle: { color: INK } }
        : { show: false },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "line", lineStyle: { color: MUTED } },
        backgroundColor: "#16202f", borderWidth: 0, textStyle: { color: "#fff", fontSize: 12 },
        formatter: (params) => {
          const main = params.filter((p) => !String(p.seriesId).startsWith("band"));
          if (!main.length) return "";
          const lines = [`<b>${fmtTime(main[0].axisValue)}</b>`];
          for (const p of main) {
            let text = `${p.marker}${escapeHtml(p.seriesName)}: <b>${fmtValue(p.value[1])}</b> ${escapeHtml(c.unit)}`;
            if (single) {
              const s = c.series[0];
              text += ` <span style="opacity:.75">(${T.col_min.toLowerCase()} ${fmtValue(s.min[p.dataIndex][1])},
                ${T.col_max.toLowerCase()} ${fmtValue(s.max[p.dataIndex][1])})</span>`;
            }
            lines.push(text);
          }
          return lines.join("<br>");
        },
      },
      xAxis: {
        type: "time", min: data.from, max: data.to,
        axisLine: { lineStyle: { color: GRID } }, axisTick: { show: false },
        axisLabel: { color: MUTED, hideOverlap: true,
          formatter: (v) => { const d = new Date(v); return `${pad(d.getUTCDate())}.${pad(d.getUTCMonth() + 1)}\n${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; } },
        splitLine: { show: true, lineStyle: { color: GRID } },
      },
      yAxis: {
        type: "value", scale: true, // the unit is in the chart title
        axisLabel: { color: MUTED, formatter: fmtValue },
        splitLine: { lineStyle: { color: GRID, type: "dashed" } },
      },
      dataZoom: [
        { type: "inside", filterMode: "none" },
        { type: "slider", height: 18, bottom: 8, filterMode: "none", borderColor: GRID,
          fillerColor: "rgba(42,120,214,.12)", handleStyle: { color: "#fff", borderColor: MUTED },
          labelFormatter: (v) => fmtTime(v, false), textStyle: { color: MUTED } },
      ],
      series,
    };
  }

  async function renderCharts() {
    const data = await getJson(`/api/report/${chartsBox.dataset.job}/charts`);
    chartsBox.innerHTML = data.charts.map((c, i) => `
      <div class="card chart-card">
        <div class="section-head">
          <h2>${escapeHtml(c.title)}${c.unit ? ` <em>${escapeHtml(c.unit)}</em>` : ""}
            <span class="muted chart-sub">· ${escapeHtml(c.analyzer)}</span></h2>
          ${c.total > c.series.length ? `<span class="muted">${escapeHtml(T.chart_limited.replace("{n}", c.series.length).replace("{total}", c.total))}</span>` : ""}
        </div>
        ${c.series.length === 1 && !data.raw ? `<p class="muted chart-hint">${escapeHtml(T.chart_band_hint)}</p>` : ""}
        <div class="chart" id="chart-${i}"></div>
      </div>`).join("");
    data.charts.forEach((c, i) => {
      const chart = echarts.init(document.getElementById(`chart-${i}`), null, { renderer: "canvas" });
      chart.setOption(chartOption(c, data));
      charts.push(chart);
    });
    // Zooming one chart zooms them all: the measures share the same time axis.
    if (charts.length > 1) echarts.connect(charts);
  }

  if (chartsBox) {
    renderCharts().catch((err) => { chartsBox.innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`; });
    window.addEventListener("resize", () => charts.forEach((c) => c.resize()));
  }

  // --- Values table ---------------------------------------------------------------
  const card = $("#points-card");
  if (!card) return;
  const state = { page: 1, size: 100 };

  async function load() {
    const params = new URLSearchParams({
      page: state.page, size: state.size, q: $("#q").value.trim(), measure: $("#measure").value,
    });
    const data = await getJson(`/api/report/${card.dataset.job}/points?${params}`);
    const cols = window.RAW ? 5 : 7;
    $("#rows").innerHTML = data.rows.length ? data.rows.map((r) => `
      <tr>
        <td class="nowrap">${escapeHtml(r.time)}</td>
        <td>${escapeHtml(r.stream)}</td>
        <td>${escapeHtml(r.object)}</td>
        <td>${escapeHtml(r.measure)}${r.unit ? ` <em>${escapeHtml(r.unit)}</em>` : ""}</td>
        ${window.RAW
          ? `<td class="num-cell">${escapeHtml(r.avg)}</td>`
          : `<td class="num-cell">${escapeHtml(r.min)}</td><td class="num-cell">${escapeHtml(r.avg)}</td><td class="num-cell">${escapeHtml(r.max)}</td>`}
      </tr>`).join("") : `<tr><td colspan="${cols}" class="muted">${escapeHtml(T.no_rows)}</td></tr>`;
    const from = data.total ? (data.page - 1) * data.size + 1 : 0;
    const to = Math.min(data.page * data.size, data.total);
    $("#page-info").textContent = T.page_of.replace("{a}", from).replace("{b}", to).replace("{n}", data.total);
    $("#prev").disabled = data.page <= 1;
    $("#next").disabled = to >= data.total;
  }

  let debounce;
  $("#q").addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(() => { state.page = 1; load(); }, 250); });
  $("#measure").addEventListener("change", () => { state.page = 1; load(); });
  $("#prev").addEventListener("click", () => { state.page -= 1; load(); });
  $("#next").addEventListener("click", () => { state.page += 1; load(); });
  load();
})();
