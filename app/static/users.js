"use strict";
(() => {
  const T = window.I18N;
  const $ = (sel) => document.querySelector(sel);
  let users = [];
  let me = null;

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
    const box = $("#message");
    box.hidden = false;
    box.className = isError ? "error" : "notice";
    box.textContent = text;
  }

  function render() {
    $("#rows").innerHTML = users.map((u) => `
      <tr data-id="${u.id}" class="${u.disabled ? "muted" : ""}">
        <td><b>${escapeHtml(u.login)}</b>${u.id === me ? ` <span class="tag">${escapeHtml(T.users_you)}</span>` : ""}
          ${u.disabled ? ` <span class="tag warn">${escapeHtml(T.users_disabled_tag)}</span>` : ""}</td>
        <td>${escapeHtml(u.name)}</td>
        <td>${escapeHtml(u.role === "admin" ? T.role_admin : T.role_user)}</td>
        <td class="nowrap">${escapeHtml(u.last_login ? u.last_login.replace("T", " ").slice(0, 16) : "—")}</td>
        <td class="actions-cell">
          <button type="button" class="link" data-act="edit">${escapeHtml(T.tpl_edit)}</button>
          ${u.id === me ? "" : `<button type="button" class="link danger" data-act="delete">${escapeHtml(T.tpl_delete)}</button>`}
        </td>
      </tr>`).join("");
  }

  async function reload() {
    const data = await api("/api/users");
    users = data.users;
    me = data.me;
    render();
  }

  function openForm(u) {
    $("#user-form").hidden = false;
    $("#u-id").value = u ? u.id : "";
    $("#u-login").value = u ? u.login : "";
    $("#u-login").disabled = !!u;
    $("#u-name").value = u ? u.name : "";
    $("#u-role").value = u ? u.role : "user";
    $("#u-password").value = "";
    $("#u-password-label").textContent = u ? T.users_new_password : T.password;
    $("#u-disabled").checked = u ? u.disabled : false;
    $("#u-disabled-row").hidden = !u || u.id === me;
    (u ? $("#u-name") : $("#u-login")).focus();
  }

  $("#add").addEventListener("click", () => openForm(null));
  $("#u-cancel").addEventListener("click", () => { $("#user-form").hidden = true; });
  $("#user-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = $("#u-id").value;
    try {
      if (id) {
        await api(`/api/users/${id}`, "PUT", {
          name: $("#u-name").value.trim(), role: $("#u-role").value,
          disabled: $("#u-disabled").checked, password: $("#u-password").value,
        });
      } else {
        await api("/api/users", "POST", {
          login: $("#u-login").value.trim(), name: $("#u-name").value.trim(),
          role: $("#u-role").value, password: $("#u-password").value,
        });
      }
      $("#user-form").hidden = true;
      await reload();
      message(T.users_saved);
    } catch (err) { message(err.message, true); }
  });

  $("#rows").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    const u = users.find((x) => String(x.id) === b.closest("tr").dataset.id);
    try {
      if (b.dataset.act === "edit") openForm(u);
      else if (confirm(T.users_delete_confirm.replace("{name}", u.login))) {
        await api(`/api/users/${u.id}`, "DELETE");
        await reload();
      }
    } catch (err) { message(err.message, true); }
  });

  reload().catch((err) => message(err.message, true));
})();
