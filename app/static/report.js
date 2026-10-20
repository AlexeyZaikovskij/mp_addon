"use strict";
(() => {
  const T = window.I18N;
  const $ = (sel) => document.querySelector(sel);

  async function getJson(url) {
    const resp = await fetch(url);
    if (resp.status === 401) { location.href = "/login"; throw new Error("session"); }
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    return resp.json();
  }

  const escapeHtml = (s) =>
    String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // --- Progress while the report is being generated ---------------------------
  const progress = $("#progress");
  if (progress) {
    const id = progress.dataset.job;
    const tick = async () => {
      const p = await getJson(`/api/report/${id}`);
      progress.querySelector(".bar-fill").style.width = `${p.percent}%`;
      $("#progress-rows").textContent = p.rows;
      if (p.state === "running") setTimeout(tick, 1000);
      else location.reload();
    };
    setTimeout(tick, 1000);
    return;
  }

  // --- Result table -------------------------------------------------------------
  const card = $("#table-card");
  if (!card) return;
  const id = card.dataset.job;
  const state = { page: 1, size: 100, sort: "detected", desc: true };

  async function load() {
    const params = new URLSearchParams({
      page: state.page, size: state.size, sort: state.sort, desc: state.desc,
      q: $("#q").value.trim(), group: $("#group").value, status: $("#status").value,
    });
    const data = await getJson(`/api/report/${id}/rows?${params}`);
    $("#rows").innerHTML = data.rows.length ? data.rows.map((r) => `
      <tr class="${r.active ? "active" : ""}">
        <td><span class="prio prio-${escapeHtml(r.group.toLowerCase())}">${escapeHtml(r.group)}</span> <em>${r.priority}</em></td>
        <td>${escapeHtml(r.alarm)}</td>
        <td class="mono">${escapeHtml(r.condition)}</td>
        <td>${escapeHtml(r.stream)}</td>
        <td>${escapeHtml(r.object)}</td>
        <td class="nowrap">${escapeHtml(r.detected)}</td>
        <td class="nowrap">${r.active ? `<span class="badge">${escapeHtml(r.status)}</span>` : escapeHtml(r.finished)}</td>
        <td class="nowrap num-cell">${escapeHtml(r.duration)}</td>
      </tr>`).join("") : `<tr><td colspan="8" class="muted">${T.no_rows}</td></tr>`;

    const from = data.total ? (data.page - 1) * data.size + 1 : 0;
    const to = Math.min(data.page * data.size, data.total);
    $("#page-info").textContent = T.page_of.replace("{a}", from).replace("{b}", to).replace("{n}", data.total);
    $("#prev").disabled = data.page <= 1;
    $("#next").disabled = to >= data.total;
  }

  for (const th of card.querySelectorAll("th[data-sort]")) {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      state.desc = state.sort === key ? !state.desc : ["detected", "finished", "duration", "priority"].includes(key);
      state.sort = key;
      state.page = 1;
      for (const h of card.querySelectorAll("th")) h.classList.remove("sorted", "asc", "desc");
      th.classList.add("sorted", state.desc ? "desc" : "asc");
      load();
    });
  }

  let debounce;
  $("#q").addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(() => { state.page = 1; load(); }, 250); });
  for (const sel of ["#group", "#status"]) $(sel).addEventListener("change", () => { state.page = 1; load(); });
  $("#prev").addEventListener("click", () => { state.page -= 1; load(); });
  $("#next").addEventListener("click", () => { state.page += 1; load(); });

  load();
})();
