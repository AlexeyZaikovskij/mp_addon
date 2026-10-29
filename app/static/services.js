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
  const picked = new Set(); // "server|node|service name", kept across refreshes

  const key = (serverId, nodeId, name) => `${serverId}|${nodeId}|${name}`;
  const pickedIn = (srv, node) => node.services.filter((s) => picked.has(key(srv.server_id, node.id, s.name)));

  function serviceRow(srv, node, s) {
    const running = s.status === "running";
    const type = s.can_set_start
      ? `<select data-act="start-type">${START_TYPES.map((x) =>
          `<option value="${x}"${x === s.start_type ? " selected" : ""}>${escapeHtml(typeText(x))}</option>`).join("")}</select>`
      : `<span class="${s.start_type === "disabled" ? "bad-text" : ""}">${escapeHtml(typeText(s.start_type))}</span>`;
    const buttons = !s.can_control ? `<span class="muted small">${escapeHtml(T.svc_view_only)}</span>` : `
      ${running ? "" : `<button type="button" class="btn small" data-act="start"${s.start_type === "disabled" ? " disabled" : ""}>${escapeHtml(T.svc_start)}</button>`}
      ${running && s.can_stop ? `<button type="button" class="link danger" data-act="stop">${escapeHtml(T.svc_stop)}</button>` : ""}
      ${running ? `<button type="button" class="link" data-act="restart">${escapeHtml(T.svc_restart)}</button>` : ""}`;
    const selectable = s.can_control || s.can_set_start;
    return `<tr data-name="${escapeHtml(s.name)}">
      <td class="svc-pick">${selectable ? `<input type="checkbox" data-pick${picked.has(key(srv.server_id, node.id, s.name)) ? " checked" : ""}>` : ""}</td>
      <td><b>${escapeHtml(s.display)}</b>${s.protected ? ` <span class="tag" title="${escapeHtml(T.svc_protected)}">🔒</span>` : ""}
        <div class="muted small">${escapeHtml(s.name)}</div></td>
      <td class="nowrap"><span class="svc-st svc-st-${escapeHtml(s.status)}">● ${escapeHtml(statusText(s.status))}</span></td>
      <td>${type}</td>
      <td class="nowrap muted">${escapeHtml(s.since || "—")}</td>
      <td class="muted">${escapeHtml(s.version || "")}</td>
      <td><div class="actions-cell">${buttons}</div></td>
    </tr>`;
  }

  function selectionBar(srv, node) {
    const sel = pickedIn(srv, node);
    const canType = node.services.some((s) => s.can_set_start);
    return `<div class="svc-bulk"${sel.length ? "" : " hidden"}>
      <span>${escapeHtml(fmt(T.svc_selected, { n: sel.length }))}</span>
      <button type="button" class="btn small" data-bulk="start">${escapeHtml(T.svc_start)}</button>
      <button type="button" class="btn small danger-btn" data-bulk="stop">${escapeHtml(T.svc_stop)}</button>
      <button type="button" class="btn small" data-bulk="restart">${escapeHtml(T.svc_restart)}</button>
      ${canType ? `<span class="svc-bulk-sep">${escapeHtml(T.svc_start_type)}:</span>
        <button type="button" class="btn small" data-bulk-type="automatic">${escapeHtml(T.svc_type_automatic)}</button>
        <button type="button" class="btn small" data-bulk-type="delayed">${escapeHtml(T.svc_type_delayed)}</button>
        <button type="button" class="btn small" data-bulk-type="manual">${escapeHtml(T.svc_type_manual)}</button>` : ""}
      <button type="button" class="link" data-bulk="clear">${escapeHtml(T.svc_clear_selection)}</button>
    </div>`;
  }

  function nodeBlock(srv, node) {
    const selectable = node.services.filter((s) => s.can_control || s.can_set_start);
    const all = selectable.length && selectable.every((s) => picked.has(key(srv.server_id, node.id, s.name)));
    return `
      <div class="svc-node" data-node="${node.id}">
        <div class="section-head">
          <h3>${escapeHtml(node.name || T.svc_this_pc)} ${node.this_pc && srv.nodes.length > 1 ? `<span class="tag">${escapeHtml(T.svc_this_pc)}</span>` : ""}</h3>
          <div class="actions-cell">
            <button type="button" class="btn small" data-act="start-all">${escapeHtml(T.svc_start_all)}</button>
            <button type="button" class="btn small danger-btn" data-act="stop-all">${escapeHtml(T.svc_stop_all)}</button>
          </div>
        </div>
        ${node.error ? `<div class="error small">${escapeHtml(node.error)}</div>` : ""}
        ${selectionBar(srv, node)}
        <div class="table-wrap"><table class="grid svc-table">
          <thead><tr>
            <th class="svc-pick">${selectable.length ? `<input type="checkbox" data-pick-all title="${escapeHtml(T.svc_select_all)}"${all ? " checked" : ""}>` : ""}</th>
            <th>${escapeHtml(T.svc_service)}</th><th>${escapeHtml(T.svc_status)}</th><th>${escapeHtml(T.svc_start_type)}</th>
            <th>${escapeHtml(T.svc_since)}</th><th>${escapeHtml(T.svc_version)}</th><th></th></tr></thead>
          <tbody>${node.services.map((s) => serviceRow(srv, node, s)).join("")}</tbody>
        </table></div>
      </div>`;
  }

  function render() {
    $("#no-servers").hidden = data.connected;
    const errors = data.errors.map((e) =>
      `<section class="card"><div class="error">${escapeHtml(T.err_server_unavailable.replace("{name}", e.server))}: ${escapeHtml(e.message)}</div></section>`);
    $("#svc-servers").innerHTML = data.servers.map((srv) => `
      <section class="card" data-server="${srv.server_id}">
        <h2>${escapeHtml(srv.server)} ${srv.local ? `<span class="tag">${escapeHtml(T.svc_this_pc)}</span>` : ""}</h2>
        <p class="muted small">${escapeHtml(srv.local ? (srv.admin ? T.svc_local_admin : T.svc_no_admin) : T.svc_remote)}</p>
        ${srv.api_error ? `<div class="error small">${escapeHtml(fmt(T.svc_api_down, { msg: srv.api_error }))}</div>` : ""}
        ${srv.nodes.map((node) => nodeBlock(srv, node)).join("")}
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

  function where(el) {
    const serverId = Number(el.closest("[data-server]").dataset.server);
    const nodeId = Number(el.closest("[data-node]").dataset.node);
    const srv = data.servers.find((s) => s.server_id === serverId);
    return { srv, node: srv.nodes.find((n) => n.id === nodeId) };
  }

  function lock() {
    busy = true;
    for (const b of document.querySelectorAll("#svc-servers button, #svc-servers select, #svc-servers input")) b.disabled = true;
  }

  async function act(srv, node, list, action, btn) {
    lock();
    if (btn) btn.textContent = "…";
    try {
      const res = await api("/api/services/action", "POST",
        { server_id: srv.server_id, node_id: node.id, names: list.map((s) => s.name), action });
      if (res.failed.length) message(`${T.svc_failed}: ${res.failed.join("; ")}`, true);
      else message(fmt(T.svc_done, { action: ACTION[action], names: list.map((s) => s.display).join(", ") }));
    } catch (err) { message(err.message, true); }
    busy = false;
    await load();
  }

  async function setTypes(srv, list, startType, btn) {
    lock();
    if (btn) btn.textContent = "…";
    try {
      const res = await api("/api/services/start-type", "POST",
        { server_id: srv.server_id, names: list.map((s) => s.name), start_type: startType });
      if (res.failed.length) message(`${T.svc_failed}: ${res.failed.join("; ")}`, true);
      else message(fmt(T.svc_start_type_set, { name: list.map((s) => s.display).join(", "), type: typeText(startType) }));
    } catch (err) { message(err.message, true); }
    busy = false;
    await load();
  }

  // Stopping a service MultiProbe or the Toolkit works through is allowed here (through Windows), with a warning.
  function confirmAction(action, list) {
    const names = list.map((s) => s.display).join(", ");
    const risky = action === "stop" ? list.filter((s) => s.protected) : [];
    return confirm(fmt(T.svc_confirm, { action: ACTION[action], names }) +
      (risky.length ? `\n\n${fmt(T.svc_confirm_protected, { names: risky.map((s) => s.display).join(", ") })}` : ""));
  }

  // Which of `list` an action applies to: start the stopped ones, stop / restart the running ones.
  const applicable = (list, action) => list.filter((s) => s.can_control && (action === "start"
    ? s.status === "stopped" && s.start_type !== "disabled"
    : s.status === "running" && (action !== "stop" || s.can_stop)));

  $("#svc-servers").addEventListener("click", (e) => {
    if (busy) return;
    const b = e.target.closest("button[data-act], button[data-bulk], button[data-bulk-type]");
    if (!b) return;
    const { srv, node } = where(b);

    if (b.dataset.bulkType) {
      const list = pickedIn(srv, node).filter((s) => s.can_set_start);
      if (!list.length) { message(T.svc_nothing); return; }
      setTypes(srv, list, b.dataset.bulkType, b);
      return;
    }
    if (b.dataset.bulk === "clear") {
      for (const s of node.services) picked.delete(key(srv.server_id, node.id, s.name));
      render();
      return;
    }
    if (b.dataset.bulk) {
      const list = applicable(pickedIn(srv, node), b.dataset.bulk);
      if (!list.length) { message(T.svc_nothing); return; }
      if (!confirmAction(b.dataset.bulk, list)) return;
      act(srv, node, list, b.dataset.bulk, b);
      return;
    }
    const kind = b.dataset.act;
    if (kind === "start-all" || kind === "stop-all") {
      const action = kind === "stop-all" ? "stop" : "start";
      const list = applicable(node.services, action);
      if (!list.length) { message(T.svc_nothing); return; }
      const risky = action === "stop" ? list.filter((s) => s.protected) : [];
      const text = fmt(action === "stop" ? T.svc_confirm_all_stop : T.svc_confirm_all_start, { n: list.length, node: node.name || T.svc_this_pc }) +
        (risky.length ? `\n\n${fmt(T.svc_confirm_protected, { names: risky.map((s) => s.display).join(", ") })}` : "");
      if (!confirm(text)) return;
      act(srv, node, list, action, b);
      return;
    }
    const s = node.services.find((x) => x.name === b.closest("tr").dataset.name);
    if (!confirmAction(kind, [s])) return;
    act(srv, node, [s], kind, b);
  });

  $("#svc-servers").addEventListener("change", (e) => {
    const t = e.target;
    if (t.matches("input[data-pick], input[data-pick-all]")) {
      const { srv, node } = where(t);
      const rows = t.matches("[data-pick-all]")
        ? node.services.filter((s) => s.can_control || s.can_set_start)
        : [node.services.find((s) => s.name === t.closest("tr").dataset.name)];
      for (const s of rows) {
        const k = key(srv.server_id, node.id, s.name);
        t.checked ? picked.add(k) : picked.delete(k);
      }
      render();
      return;
    }
    const sel = t.closest("select[data-act=start-type]");
    if (!sel || busy) return;
    const { srv, node } = where(sel);
    setTypes(srv, [node.services.find((s) => s.name === sel.closest("tr").dataset.name)], sel.value);
  });

  $("#refresh").addEventListener("click", load);
  $("#scope")?.addEventListener("change", async (e) => {
    const v = e.target.value;
    try { await api("/api/scope", "POST", { server_id: v === "" ? null : Number(v) }); } catch (err) { message(err.message, true); return; }
    load();
  });

  load();
})();
