"use strict";
(() => {
  const T = window.I18N;
  const $ = (sel) => document.querySelector(sel);
  let servers = [];
  let isAdmin = false;
  let connecting = null;
  let connectMode = "user"; // "user": connect myself; "jobs": the account for scheduled jobs (administrators)

  const escapeHtml = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(path, method = "GET", body) {
    const resp = await fetch(path, {
      method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined,
    });
    if (resp.status === 401) { location.href = "/login"; throw new Error("session"); }
    const data = await resp.json();
    if (!resp.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : data.detail;
      throw new Error(detail || data.message || resp.statusText);
    }
    return data;
  }

  function message(text, isError = false) {
    const box = $("#srv-message");
    box.hidden = false;
    box.className = isError ? "error" : "notice";
    box.textContent = text;
  }

  function render() {
    $("#srv-rows").innerHTML = servers.map((s) => `
      <tr data-id="${s.id}">
        <td><b>${escapeHtml(s.name)}</b>
          ${s.added_by ? `<div class="muted small">${escapeHtml(T.srv_added_by)}: ${escapeHtml(s.added_by)}</div>` : ""}</td>
        <td class="mono">${escapeHtml(s.url)}${s.verify_tls ? "" : ` <span class="tag warn">${escapeHtml(T.srv_insecure_tag)}</span>`}</td>
        <td>${s.connected
          ? `<span class="srv-on">● ${escapeHtml(T.srv_connected)}</span> <span class="muted">${escapeHtml(s.login || "")}</span>`
          : `<span class="muted">○ ${escapeHtml(T.srv_not_connected)}</span>`}
          ${s.remembered ? `<div class="muted small">${escapeHtml(T.srv_remembered.replace("{login}", s.remembered))}
            <button type="button" class="link" data-act="forget">${escapeHtml(T.srv_forget)}</button></div>` : ""}
          ${s.auto_error && !s.connected ? `<div class="error small">${escapeHtml(T.srv_auto_failed)}: ${escapeHtml(s.auto_error)}</div>` : ""}</td>
        ${isAdmin ? `<td>${s.job_login ? `${escapeHtml(s.job_login)}
            <button type="button" class="link" data-act="jobs">${escapeHtml(T.tpl_edit)}</button>
            <button type="button" class="link danger" data-act="jobs-delete">${escapeHtml(T.tpl_delete)}</button>`
          : `<span class="muted">${escapeHtml(T.srv_jobs_none)}</span>
            <button type="button" class="link" data-act="jobs">${escapeHtml(T.srv_jobs_set)}</button>`}</td>` : ""}
        <td class="actions-cell">
          ${s.connected
            ? `<button type="button" class="link" data-act="disconnect">${escapeHtml(T.srv_disconnect)}</button>`
            : `<button type="button" class="btn small primary-btn" data-act="connect">${escapeHtml(T.srv_connect)}</button>`}
          ${isAdmin ? `<button type="button" class="link" data-act="edit">${escapeHtml(T.tpl_edit)}</button>
            <button type="button" class="link danger" data-act="delete">${escapeHtml(T.tpl_delete)}</button>` : ""}
        </td>
      </tr>`).join("");
  }

  async function reload() {
    const data = await api("/api/servers");
    servers = data.servers;
    isAdmin = data.is_admin;
    $("#add").hidden = !isAdmin;
    $("#srv-empty").hidden = servers.length > 0;
    render();
  }

  // --- Add / edit -------------------------------------------------------------------
  function openForm(srv) {
    $("#srv-form").hidden = false;
    $("#srv-id").value = srv ? srv.id : "";
    $("#srv-name").value = srv ? srv.name : "";
    $("#srv-url").value = srv ? srv.url : "";
    $("#srv-insecure").checked = srv ? !srv.verify_tls : false;
    $("#srv-name").focus();
  }
  $("#add").addEventListener("click", () => openForm(null));
  $("#srv-cancel").addEventListener("click", () => { $("#srv-form").hidden = true; });
  $("#srv-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = $("#srv-id").value;
    const body = { name: $("#srv-name").value.trim(), url: $("#srv-url").value.trim(), verify_tls: !$("#srv-insecure").checked };
    try {
      const res = await api(id ? `/api/servers/${id}` : "/api/servers", id ? "PUT" : "POST", body);
      $("#srv-form").hidden = true;
      await reload();
      message(T.srv_saved.replace("{name}", body.name));
      if (!id) openConnect(servers.find((s) => s.id === res.id)); // a new server: sign in right away
    } catch (err) { message(err.message, true); }
  });

  // --- Connect ---------------------------------------------------------------------
  function openConnect(srv, mode = "user") {
    if (!srv) return;
    connecting = srv;
    connectMode = mode;
    const jobs = mode === "jobs";
    $("#connect-title").textContent = (jobs ? T.srv_jobs_title : T.srv_connect_to).replace("{name}", srv.name);
    $("#connect-submit").textContent = jobs ? T.tpl_save : T.srv_connect;
    $("#c-remember-wrap").hidden = jobs;
    $("#c-remember-hint").textContent = jobs ? T.srv_jobs_hint : T.srv_remember_hint;
    $("#c-remember").checked = !!srv.remembered;
    $("#c-login").value = jobs ? (srv.job_login || "") : (srv.remembered || "");
    $("#c-password").value = "";
    $("#connect-error").hidden = true;
    $("#connect").hidden = false;
    $("#c-login").focus();
  }
  $("#connect-cancel").addEventListener("click", () => { $("#connect").hidden = true; });
  $("#connect-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.submitter;
    btn.disabled = true;
    try {
      const body = { login: $("#c-login").value.trim(), password: $("#c-password").value };
      if (connectMode === "jobs") {
        await api(`/api/servers/${connecting.id}/job-account`, "PUT", body);
      } else {
        await api(`/api/servers/${connecting.id}/connect`, "POST", { ...body, remember: $("#c-remember").checked });
      }
      $("#c-password").value = "";
      $("#connect").hidden = true;
      await reload();
      message((connectMode === "jobs" ? T.srv_jobs_saved : T.srv_connected_to).replace("{name}", connecting.name));
    } catch (err) {
      $("#connect-error").hidden = false;
      $("#connect-error").textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  });

  // --- Row actions --------------------------------------------------------------------
  $("#srv-rows").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    const srv = servers.find((s) => String(s.id) === b.closest("tr").dataset.id);
    try {
      if (b.dataset.act === "connect") openConnect(srv);
      else if (b.dataset.act === "edit") openForm(srv);
      else if (b.dataset.act === "disconnect") { await api(`/api/servers/${srv.id}/disconnect`, "POST"); await reload(); }
      else if (b.dataset.act === "forget") { await api(`/api/servers/${srv.id}/forget`, "POST"); await reload(); }
      else if (b.dataset.act === "jobs") openConnect(srv, "jobs");
      else if (b.dataset.act === "jobs-delete") {
        if (!confirm(T.srv_jobs_delete_confirm.replace("{name}", srv.name))) return;
        await api(`/api/servers/${srv.id}/job-account`, "DELETE");
        await reload();
      }
      else if (b.dataset.act === "delete") {
        if (!confirm(T.srv_delete_confirm.replace("{name}", srv.name))) return;
        await api(`/api/servers/${srv.id}`, "DELETE");
        await reload();
      }
    } catch (err) { message(err.message, true); }
  });

  reload().catch((err) => message(err.message, true));
})();
