"use strict";
(() => {
  const T = window.I18N;
  const $ = (sel) => document.querySelector(sel);
  const fmt = (s, vars) => s.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");
  const escapeHtml = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(path, method = "GET", body) {
    const resp = await fetch(path, {
      method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined,
    });
    if (resp.status === 401) { location.href = "/login"; throw new Error("session"); }
    const data = await resp.json();
    if (!resp.ok) {
      const d = data.detail;
      throw new Error(Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d || data.message || resp.statusText);
    }
    return data;
  }

  function message(text, isError = false) {
    const box = $("#svc-message");
    box.hidden = !text;
    box.className = isError ? "error" : "notice";
    box.textContent = text;
  }

  const START_TYPES = ["automatic", "delayed", "manual", "disabled"];
  const ACTION = { start: T.svc_start, stop: T.svc_stop, restart: T.svc_restart };
  const PENDING = new Set(["startPending", "stopPending", "continuePending", "pausePending"]);
  const statusText = (s) => T[`svc_st_${s}`] || s;
  const typeText = (s) => T[`svc_type_${s}`] || s || "—";

  let data = null;
  let busy = false;

  function serviceRow(srv, node, s) {
    const off = !s.can_control;
    const running = s.status === "running";
    const type = s.can_set_start
      ? `<select data-act="start-type">${START_TYPES.map((x) =>
          `<option value="${x}"${x === s.start_type ? " selected" : ""}>${escapeHtml(typeText(x))}</option>`).join("")}</select>`
      : `<span class="${s.start_type === "disabled" ? "bad-text" : ""}">${escapeHtml(typeText(s.start_type))}</span>`;
    const buttons = off ? `<span class="muted small">${escapeHtml(T.svc_view_only)}</span>` : `
      ${running ? "" : `<button type="button" class="btn small" data-act="start"${s.start_type === "disabled" ? " disabled" : ""}>${escapeHtml(T.svc_start)}</button>`}
      ${running && !s.protected ? `<button type="button" class="link danger" data-act="stop">${escapeHtml(T.svc_stop)}</button>` : ""}
      ${running ? `<button type="button" class="link" data-act="restart">${escapeHtml(T.svc_restart)}</button>` : ""}`;
    return `<tr data-name="${escapeHtml(s.name)}">
      <td><b>${escapeHtml(s.display)}</b>${s.protected ? ` <span class="tag" title="${escapeHtml(T.svc_protected)}">🔒</span>` : ""}
        <div class="muted small">${escapeHtml(s.name)}</div></td>
      <td class="nowrap"><span class="svc-st svc-st-${escapeHtml(s.status)}">● ${escapeHtml(statusText(s.status))}</span></td>
      <td>${type}</td>
      <td class="nowrap muted">${escapeHtml(s.since || "—")}</td>
      <td class="muted">${escapeHtml(s.version || "")}</td>
      <td><div class="actions-cell">${buttons}</div></td>
    </tr>`;
  }

  function render() {
    $("#no-servers").hidden = data.connected;
    const errors = data.errors.map((e) =>
      `<section class="card"><div class="error">${escapeHtml(T.err_server_unavailable.replace("{name}", e.server))}: ${escapeHtml(e.message)}</div></section>`);
    $("#svc-servers").innerHTML = data.servers.map((srv) => `
      <section class="card" data-server="${srv.server_id}">
        <h2>${escapeHtml(srv.server)} ${srv.local ? `<span class="tag">${escapeHtml(T.svc_this_pc)}</span>` : ""}</h2>
        <p class="muted small">${escapeHtml(srv.local ? (srv.admin ? "" : T.svc_no_admin) : T.svc_remote)}</p>
        ${srv.nodes.map((node) => `
          <div class="svc-node" data-node="${node.id}">
            <div class="section-head">
              <h3>${escapeHtml(node.name)} ${node.this_pc && srv.nodes.length > 1 ? `<span class="tag">${escapeHtml(T.svc_this_pc)}</span>` : ""}</h3>
              <div class="actions-cell">
                <button type="button" class="btn small" data-act="start-all">${escapeHtml(T.svc_start_all)}</button>
                <button type="button" class="btn small danger-btn" data-act="stop-all">${escapeHtml(T.svc_stop_all)}</button>
              </div>
            </div>
            ${node.error ? `<div class="error small">${escapeHtml(node.error)}</div>` : ""}
            <div class="table-wrap"><table class="grid svc-table">
              <thead><tr><th>${escapeHtml(T.svc_service)}</th><th>${escapeHtml(T.svc_status)}</th><th>${escapeHtml(T.svc_start_type)}</th>
                <th>${escapeHtml(T.svc_since)}</th><th>${escapeHtml(T.svc_version)}</th><th></th></tr></thead>
              <tbody>${node.services.map((s) => serviceRow(srv, node, s)).join("")}</tbody>
            </table></div>
          </div>`).join("")}
      </section>`).join("") + errors.join("");
  }

  let timer = null;
  async function load() {
    clearTimeout(timer);
    try {
      data = await api("/api/services");
      $("#updated").textContent = new Date().toLocaleTimeString();
      if (!busy) render();
    } catch (err) { message(err.message, true); }
    const pending = data && data.servers.some((s) => s.nodes.some((n) => n.services.some((x) => PENDING.has(x.status))));
    timer = setTimeout(load, pending ? 3000 : 15000);
  }

  const find = (serverId, nodeId) => {
    const srv = data.servers.find((s) => s.server_id === serverId);
    return { srv, node: srv && srv.nodes.find((n) => n.id === nodeId) };
  };

  async function act(serverId, nodeId, names, action, btn) {
    busy = true;
    for (const b of document.querySelectorAll("#svc-servers button, #svc-servers select")) b.disabled = true;
    if (btn) btn.textContent = "…";
    try {
      const res = await api("/api/services/action", "POST", { server_id: serverId, node_id: nodeId, names, action });
      const display = names.map((n) => n.replace(/^Stream Labs MP /, "").replace(/ Service$/, "")).join(", ");
      if (res.failed.length) message(`${T.svc_failed}: ${res.failed.join("; ")}`, true);
      else message(fmt(T.svc_done, { action: ACTION[action], names: display }));
    } catch (err) { message(err.message, true); }
    busy = false;
    await load();
  }

  $("#svc-servers").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b || busy) return;
    const serverId = Number(b.closest("[data-server]").dataset.server);
    const nodeId = Number(b.closest("[data-node]").dataset.node);
    const { node } = find(serverId, nodeId);
    const act_ = b.dataset.act;
    if (act_ === "start-all" || act_ === "stop-all") {
      const stop = act_ === "stop-all";
      const names = node.services.filter((s) => s.can_control && (stop
        ? s.status === "running" && !s.protected
        : s.status === "stopped" && s.start_type !== "disabled")).map((s) => s.name);
      if (!names.length) { message(T.svc_nothing); return; }
      if (!confirm(fmt(stop ? T.svc_confirm_all_stop : T.svc_confirm_all_start, { n: names.length, node: node.name }))) return;
      act(serverId, nodeId, names, stop ? "stop" : "start", b);
      return;
    }
    const name = b.closest("tr").dataset.name;
    const s = node.services.find((x) => x.name === name);
    if (!confirm(fmt(T.svc_confirm, { action: ACTION[act_], names: s.display }))) return;
    act(serverId, nodeId, [name], act_, b);
  });

  $("#svc-servers").addEventListener("change", async (e) => {
    const sel = e.target.closest("select[data-act=start-type]");
    if (!sel) return;
    const serverId = Number(sel.closest("[data-server]").dataset.server);
    const name = sel.closest("tr").dataset.name;
    sel.disabled = true;
    try {
      await api("/api/services/start-type", "POST", { server_id: serverId, name, start_type: sel.value });
      message(fmt(T.svc_start_type_set, { name: name.replace(/^Stream Labs MP /, "").replace(/ Service$/, ""), type: typeText(sel.value) }));
    } catch (err) { message(err.message, true); }
    await load();
  });

  $("#refresh").addEventListener("click", load);
  $("#scope")?.addEventListener("change", async (e) => {
    const v = e.target.value;
    try { await api("/api/scope", "POST", { server_id: v === "" ? null : Number(v) }); } catch (err) { message(err.message, true); return; }
    load();
  });

  load();
})();
