"use strict";
(() => {
  const T = window.I18N;
  const $ = (sel) => document.querySelector(sel);
  const card = $("#markers-card");
  if (!card) return;
  const state = { page: 1, size: 100 };
  const escapeHtml = (s) =>
    String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function load() {
    const params = new URLSearchParams({
      page: state.page, size: state.size, q: $("#q").value.trim(), obj: $("#obj").value, command: $("#command").value,
    });
    const resp = await fetch(`/api/report/${card.dataset.job}/markers?${params}`);
    if (resp.status === 401) { location.href = "/login"; return; }
    const data = await resp.json();
    $("#rows").innerHTML = data.rows.length ? data.rows.map((r) => `
      <tr>
        <td class="nowrap">${escapeHtml(r.time)}</td>
        <td>${escapeHtml(r.stream)}</td>
        <td>${escapeHtml(r.object)}</td>
        <td>${escapeHtml(r.command)}</td>
        <td>${escapeHtml(r.event_id)}</td>
        <td>${escapeHtml(r.direction)}</td>
        <td class="num-cell">${escapeHtml(r.duration)}</td>
        <td>${escapeHtml(r.auto_return)}</td>
        <td>${escapeHtml(r.cancel)}</td>
        <td>${escapeHtml(r.segmentation)}</td>
      </tr>`).join("") : `<tr><td colspan="10" class="muted">${escapeHtml(T.no_rows)}</td></tr>`;
    const from = data.total ? (data.page - 1) * data.size + 1 : 0;
    const to = Math.min(data.page * data.size, data.total);
    $("#page-info").textContent = T.page_of.replace("{a}", from).replace("{b}", to).replace("{n}", data.total);
    $("#prev").disabled = data.page <= 1;
    $("#next").disabled = to >= data.total;
  }

  let debounce;
  $("#q").addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(() => { state.page = 1; load(); }, 250); });
  for (const sel of ["#obj", "#command"]) $(sel).addEventListener("change", () => { state.page = 1; load(); });
  $("#prev").addEventListener("click", () => { state.page -= 1; load(); });
  $("#next").addEventListener("click", () => { state.page += 1; load(); });
  load();
})();
