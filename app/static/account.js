"use strict";
(() => {
  const T = window.I18N;
  const $ = (sel) => document.querySelector(sel);

  async function send(path, method, body) {
    const resp = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
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

  $("#profile-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await send("/api/account", "PUT", { name: $("#a-name").value.trim(), time_zone: $("#a-tz").value });
      location.reload();
    } catch (err) { message(err.message, true); }
  });

  $("#password-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await send("/api/account/password", "POST", { current: $("#p-current").value, new: $("#p-new").value, new2: $("#p-new2").value });
      e.target.reset();
      message(T.account_password_changed);
    } catch (err) { message(err.message, true); }
  });
})();
