"use strict";
(() => {
  const T = window.I18N;
  const $ = (sel) => document.querySelector(sel);
  let servers = [];
  let isAdmin = false;
  let connecting = null;

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
          : `<span class="muted">○ ${escapeHtml(T.srv_not_connected)}</span>`}</td>
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
  function openConnect(srv) {
    if (!srv) return;
    connecting = srv;
    $("#connect-title").textContent = T.srv_connect_to.replace("{name}", srv.name);
    $("#c-login").value = "";
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
      await api(`/api/servers/${connecting.id}/connect`, "POST", { login: $("#c-login").value.trim(), password: $("#c-password").value });
      $("#c-password").value = "";
      $("#connect").hidden = true;
      await reload();
      message(T.srv_connected_to.replace("{name}", connecting.name));
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
      else if (b.dataset.act === "delete") {
        if (!confirm(T.srv_delete_confirm.replace("{name}", srv.name))) return;
        await api(`/api/servers/${srv.id}`, "DELETE");
        await reload();
      }
    } catch (err) { message(err.message, true); }
  });

  reload().catch((err) => message(err.message, true));
})();
