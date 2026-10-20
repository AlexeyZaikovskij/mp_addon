"use strict";
(() => {
  const T = window.I18N;
  const $ = (sel) => document.querySelector(sel);
  let templates = [];

  async function api(path, method = "GET", body) {
    const resp = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (resp.status === 401) { location.href = "/login"; throw new Error("session"); }
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || data.message || resp.statusText);
    return data;
  }

  const escapeHtml = (s) =>
    String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function periodText(t) {
    const h = t.period_hours;
    if (!h) return `${t.params.period_from.replace("T", " ")} — ${t.params.period_to.replace("T", " ")}`;
    const named = { 1: T.last_hour, 24: T.last_24h, 168: T.last_7d, 720: T.last_30d }[h];
    return named || T.last_n_hours.replace("{n}", h);
  }

  function updatedText(iso) {
    return new Intl.DateTimeFormat("sv-SE", {
      timeZone: window.USER_TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    }).format(new Date(iso));
  }

  function render() {
    const q = $("#tpl-search").value.trim().toLowerCase();
    const rows = templates.filter((t) => !q || t.name.toLowerCase().includes(q));
    $("#tpl-rows").innerHTML = rows.length ? rows.map((t) => `
      <tr data-id="${t.id}">
        <td>
          <span class="tpl-name">${escapeHtml(t.name)}</span>
          ${t.shared ? `<span class="tag">${escapeHtml(T.tpl_shared_tag)}</span>` : ""}
        </td>
        <td>${escapeHtml(t.type === "alarms" ? T.type_alarms : T.type_measures)}</td>
        <td class="num-cell">${t.objects}</td>
        <td>${escapeHtml(periodText(t))}</td>
        <td>${t.mine ? escapeHtml(T.tpl_me) : escapeHtml(t.owner)}</td>
        <td class="nowrap">${escapeHtml(updatedText(t.updated))}</td>
        <td class="actions-cell">
          <button type="button" class="btn small primary-btn" data-act="run">${escapeHtml(T.tpl_run)}</button>
          <a class="link" href="/?template=${t.id}">${escapeHtml(t.mine ? T.tpl_edit : T.tpl_open)}</a>
          <button type="button" class="link" data-act="copy">${escapeHtml(T.tpl_copy)}</button>
          ${t.mine ? `<button type="button" class="link" data-act="rename">${escapeHtml(T.tpl_rename)}</button>
            <button type="button" class="link" data-act="share">${escapeHtml(t.shared ? T.tpl_unshare : T.tpl_share)}</button>
            <button type="button" class="link danger" data-act="delete">${escapeHtml(T.tpl_delete)}</button>` : ""}
        </td>
      </tr>`).join("") : `<tr><td colspan="7" class="muted">${escapeHtml(templates.length ? T.no_rows : T.tpl_empty)}</td></tr>`;
  }

  function message(text, isError = false) {
    const box = $("#tpl-message");
    box.hidden = false;
    box.className = isError ? "error" : "notice";
    box.textContent = text;
  }

  async function reload() {
    templates = await api("/api/templates");
    render();
  }

  $("#tpl-rows").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const tpl = templates.find((t) => String(t.id) === btn.closest("tr").dataset.id);
    try {
      switch (btn.dataset.act) {
        case "run": {
          btn.disabled = true;
          const { id } = await api(`/api/templates/${tpl.id}/run`, "POST");
          location.href = `/report/${id}`;
          return;
        }
        case "copy": {
          // The copy opens straight in the wizard: the usual next step is picking another channel.
          const name = prompt(T.tpl_copy_name, T.copy_of.replace("{name}", tpl.name));
          if (name === null) return;
          const { id } = await api(`/api/templates/${tpl.id}/copy`, "POST", { name: name.trim() });
          location.href = `/?template=${id}`;
          return;
        }
        case "rename": {
          const name = prompt(T.tpl_name, tpl.name);
          if (!name || !name.trim()) return;
          await api(`/api/templates/${tpl.id}`, "PUT", { name: name.trim(), shared: tpl.shared });
          break;
        }
        case "share":
          await api(`/api/templates/${tpl.id}`, "PUT", { name: tpl.name, shared: !tpl.shared });
          break;
        case "delete":
          if (!confirm(T.tpl_delete_confirm.replace("{name}", tpl.name))) return;
          await api(`/api/templates/${tpl.id}`, "DELETE");
          break;
      }
      await reload();
    } catch (err) {
      btn.disabled = false;
      message(err.message, true);
    }
  });

  $("#tpl-search").addEventListener("input", render);
  reload().catch((err) => message(err.message, true));
})();
