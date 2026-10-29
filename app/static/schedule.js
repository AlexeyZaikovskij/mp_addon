"use strict";
(() => {
  const T = window.I18N;
  const $ = (sel) => document.querySelector(sel);
  const fmt = (s, vars) => s.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");
  const escapeHtml = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtSize = (n) => n >= 1 << 30 ? `${(n / (1 << 30)).toFixed(2)} GB` : n >= 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MB` : `${Math.ceil(n / 1024)} KB`;
  const chunkLabel = (m) => m % 60 ? fmt(T.rec_min, { n: m }) : m === 1440 ? T.rec_span_24h : fmt(T.sch_hours, { n: m / 60 });

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
    const box = $("#sch-message");
    box.hidden = !text;
    box.className = isError ? "error" : "notice";
    box.textContent = text;
  }

  let state = null;     // /api/schedule
  let tasks = [];       // recordings of the connected servers
  let services = [];
  const open = new Set(); // jobs whose runs are shown in full

  // --- Jobs ------------------------------------------------------------------------------------------------
  const STATE = {
    running: T.sch_st_running, done: T.sch_st_done, partial: T.sch_st_partial, failed: T.sch_st_failed,
    cancelled: T.sch_st_cancelled, pending: T.sch_st_pending, skipped: T.sch_st_skipped,
  };
  const badge = (st) => `<span class="sch-st sch-st-${st}">${escapeHtml(STATE[st] || st)}</span>`;

  function schedule(j) {
    const p = j.params;
    if (p.kind === "daily") return fmt(T.sch_daily_at, { time: p.run_at });
    return `${escapeHtml(j.period[0])} – ${escapeHtml(j.period[1])}`;
  }

  function runLine(r) {
    const c = r.counts;
    const done = (c.done || 0) + (c.skipped || 0);
    const parts = [`${done} / ${r.total}`];
    if (c.failed) parts.push(`<span class="bad">${escapeHtml(fmt(T.sch_n_failed, { n: c.failed }))}</span>`);
    if (c.skipped) parts.push(escapeHtml(fmt(T.sch_n_skipped, { n: c.skipped })));
    return `${badge(r.state)} <span class="muted">${escapeHtml(r.begin)} – ${escapeHtml(r.end)}</span> · ${parts.join(" · ")}
      ${r.progress !== null && r.progress !== undefined ? ` · ${escapeHtml(T.sch_now_piece)} ${r.progress}%` : ""}`;
  }

  function pieceRows(r) {
    return r.pieces.map((p) => `<tr>
      <td class="nowrap">${escapeHtml(p.begin.slice(11))} – ${escapeHtml(p.end.slice(11))}</td>
      <td>${p.format.toUpperCase()}</td><td>${badge(p.state)}</td>
      <td>${p.file ? `<span class="mono">${escapeHtml(p.file)}</span> · ${fmtSize(p.size || 0)}` : ""}
        ${p.error ? `<span class="${p.state === "skipped" ? "muted" : "bad"}">${escapeHtml(p.state === "skipped" ? T.sch_no_recording : p.error)}</span>` : ""}
        ${p.attempts > 1 ? `<span class="muted"> · ${escapeHtml(fmt(T.sch_attempts, { n: p.attempts }))}</span>` : ""}</td>
    </tr>`).join("");
  }

  function runBlock(r) {
    return `<div class="sch-run" data-run="${r.id}">
      <div class="sch-run-head">${runLine(r)}
        ${r.state === "running" ? `<button type="button" class="link danger" data-act="cancel">${escapeHtml(T.sch_cancel)}</button>` : ""}
        ${r.counts.failed ? `<button type="button" class="link" data-act="retry">${escapeHtml(T.sch_retry)}</button>` : ""}
      </div>
      <table class="grid sch-pieces"><tbody>${pieceRows(r)}</tbody></table>
    </div>`;
  }

  // The server picked next to the title ("" = all servers); without the switcher, every job.
  const scope = () => $("#scope")?.value || "";

  function renderJobs() {
    const jobs = state.jobs.filter((j) => !scope() || String(j.params.server_id) === scope());
    if (!jobs.length) { $("#jobs").innerHTML = `<p class="muted">${escapeHtml(T.sch_no_jobs)}</p>`; return; }
    $("#jobs").innerHTML = jobs.map((j) => {
      const p = j.params, last = j.runs[0];
      const what = [p.formats.map((f) => f.toUpperCase()).join(" + "), fmt(T.sch_by, { chunk: chunkLabel(p.chunk_minutes) }),
        j.folder ? `→ ${j.folder}` : T.sch_folder_gone].join(" · ");
      const noAccount = !state.job_accounts.includes(p.server_id);
      return `<div class="sch-job${j.enabled ? "" : " off"}" data-job="${j.id}">
        <div class="sch-job-head">
          <div class="sch-job-main">
            <b>${escapeHtml(j.name)}</b>
            <span class="muted">${escapeHtml(p.channel || p.task_name)}${state.is_admin ? ` · ${escapeHtml(j.owner)}` : ""}</span>
            <div class="small">${escapeHtml(what)}</div>
            <div class="small">${p.kind === "daily" ? escapeHtml(schedule(j)) : `${escapeHtml(T.sch_once)}: ${schedule(j)}`}
              ${j.next_run && j.enabled ? ` · ${escapeHtml(T.sch_next)} <b>${escapeHtml(j.next_run)}</b>` : ""}
              ${!j.enabled ? ` · <b>${escapeHtml(T.sch_paused)}</b>` : ""}</div>
            ${noAccount ? `<div class="error small">${escapeHtml(fmt(T.sch_no_account_for, { name: p.server || "" }))}</div>` : ""}
          </div>
          <div class="sch-job-actions">
            <button type="button" class="btn small" data-act="run">${escapeHtml(T.sch_run_now)}</button>
            ${p.kind === "daily" ? `<button type="button" class="link" data-act="toggle">${escapeHtml(j.enabled ? T.sch_pause : T.sch_resume)}</button>` : ""}
            <button type="button" class="link danger" data-act="delete">${escapeHtml(T.tpl_delete)}</button>
          </div>
        </div>
        ${last ? `<div class="sch-last">${runLine(last)}
          <button type="button" class="link" data-act="details">${escapeHtml(open.has(j.id) ? T.sch_hide : T.sch_details)}</button></div>` : `<div class="muted small">${escapeHtml(T.sch_never_ran)}</div>`}
        ${open.has(j.id) ? `<div class="sch-runs">${j.runs.map(runBlock).join("")}</div>` : ""}
      </div>`;
    }).join("");
  }

  async function load() {
    state = await api("/api/schedule");
    renderJobs();
    renderFolders();
    fillFolders();
    schedulePoll();
  }

  let poll = null;
  function schedulePoll() {
    clearTimeout(poll);
    const busy = state.jobs.some((j) => j.runs.some((r) => r.state === "running"));
    poll = setTimeout(() => load().catch(() => {}), busy ? 3000 : 30000);
  }

  $("#jobs").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    const id = Number(b.closest("[data-job]").dataset.job);
    const job = state.jobs.find((j) => j.id === id);
    const run = b.closest("[data-run]")?.dataset.run;
    try {
      if (b.dataset.act === "details") { open.has(id) ? open.delete(id) : open.add(id); renderJobs(); return; }
      if (b.dataset.act === "run") {
        if (!confirm(fmt(job.params.kind === "daily" ? T.sch_run_daily_confirm : T.sch_run_confirm, { name: job.name }))) return;
        await api(`/api/schedule/jobs/${id}/run`, "POST");
        open.add(id);
      } else if (b.dataset.act === "toggle") await api(`/api/schedule/jobs/${id}/enabled`, "POST", { enabled: !job.enabled });
      else if (b.dataset.act === "delete") {
        if (!confirm(fmt(T.sch_delete_confirm, { name: job.name }))) return;
        await api(`/api/schedule/jobs/${id}`, "DELETE");
      } else if (b.dataset.act === "cancel") await api(`/api/schedule/runs/${run}/cancel`, "POST");
      else if (b.dataset.act === "retry") await api(`/api/schedule/runs/${run}/retry`, "POST");
      await load();
    } catch (err) { message(err.message, true); }
  });

  // --- New job ---------------------------------------------------------------------------------------------
  const toMs = (s) => Date.parse(`${s.length === 16 ? s + ":00" : s}Z`);
  const fromMs = (ms) => new Date(ms).toISOString().slice(0, 19);

  $("#scope")?.addEventListener("change", async () => {
    try {
      await api("/api/scope", "POST", { server_id: scope() === "" ? null : Number(scope()) });
    } catch (err) { message(err.message, true); return; }
    renderJobs();
    if (!$("#job-form").hidden) await openForm(); // the channel list follows the pick
  });

  $("#new-job").addEventListener("click", openForm);
  async function openForm() {
    $("#job-form").hidden = false;
    $("#f-error").hidden = true;
    try {
      const data = await api("/api/rec/tasks");
      tasks = data.tasks.filter((t) => !t.disabled);
      $("#f-task").innerHTML = tasks.length ? tasks.map((t, i) =>
        `<option value="${i}">${escapeHtml(t.name)} — ${escapeHtml(data.multi_server ? `${t.server} · ${t.node}` : t.node)}</option>`).join("")
        : `<option value="">${escapeHtml(data.connected ? T.rec_no_tasks : T.no_servers_connected)}</option>`;
      const today = Date.parse(`${state.now.slice(0, 10)}T00:00:00Z`);
      $("#f-begin").value = fromMs(today - 86_400_000);
      $("#f-end").value = fromMs(today);
      $("#f-start").value = fromMs(toMs(state.now) + 3600_000).slice(0, 16);
      await onTask();
    } catch (err) { $("#f-error").hidden = false; $("#f-error").textContent = err.message; }
  }
  $("#f-cancel").addEventListener("click", () => { $("#job-form").hidden = true; });

  const task = () => tasks[Number($("#f-task").value)] || null;
  const service = () => services[Number($("#f-service").value || 0)] || null;

  async function onTask() {
    const t = task();
    services = [];
    if (t && t.object_id) {
      const q = new URLSearchParams({ server_id: t.server_id, node_id: t.node_id, object_id: t.object_id });
      services = await api(`/api/rec/services?${q}`).catch(() => []);
    }
    $("#f-service-wrap").hidden = services.length < 2;
    $("#f-service").innerHTML = services.map((s, i) => `<option value="${i}">${escapeHtml(s.name)}</option>`).join("");
    onService();
    $("#f-no-account").hidden = !t || state.job_accounts.includes(t.server_id);
    $("#f-no-account").textContent = t ? fmt(T.sch_no_account_for, { name: t.server }) : "";
  }

  function onService() {
    const audio = service()?.audio || [];
    $("#f-track-wrap").hidden = audio.length < 2;
    $("#f-track").innerHTML = audio.map((a) => `<option value="${a.pid}">${escapeHtml(a.name)}</option>`).join("");
    const t = task(), s = service();
    if (t && !$("#f-name").dataset.touched) $("#f-name").value = (services.length > 1 && s ? s.name : t.name);
    summary();
  }
  $("#f-task").addEventListener("change", () => onTask());
  $("#f-service").addEventListener("change", onService);
  $("#f-name").addEventListener("input", () => { $("#f-name").dataset.touched = "1"; });

  function fillFolders() {
    $("#f-folder").innerHTML = state.folders.map((f) => `<option value="${f.id}">${escapeHtml(f.name)}</option>`).join("");
    $("#f-no-folders").hidden = state.folders.length > 0;
    if (!$("#f-chunk").options.length) {
      $("#f-chunk").innerHTML = state.chunks.map((m) => `<option value="${m}" ${m === 60 ? "selected" : ""}>${escapeHtml(chunkLabel(m))}</option>`).join("");
    }
  }

  const kind = () => document.querySelector("input[name=kind]:checked").value;
  const when = () => document.querySelector("input[name=when]:checked").value;
  for (const r of document.querySelectorAll("input[name=kind]")) r.addEventListener("change", () => {
    $("#once-box").hidden = kind() !== "once";
    $("#daily-box").hidden = kind() !== "daily";
    summary();
  });
  for (const r of document.querySelectorAll("input[name=when]")) r.addEventListener("change", () => { $("#f-start").disabled = when() !== "at"; });
  $("#f-yesterday").addEventListener("click", () => {
    const today = Date.parse(`${state.now.slice(0, 10)}T00:00:00Z`);
    $("#f-begin").value = fromMs(today - 86_400_000);
    $("#f-end").value = fromMs(today);
    summary();
  });
  for (const id of ["#f-begin", "#f-end", "#f-chunk", "#f-mp4", "#f-mp3"]) $(id).addEventListener("change", summary);

  // "24 files of 1 h, MP4 + MP3: 48 files"
  function summary() {
    const formats = [$("#f-mp4").checked && "MP4", $("#f-mp3").checked && "MP3"].filter(Boolean);
    const chunk = Number($("#f-chunk").value || 60);
    const minutes = kind() === "daily" ? 1440 : (toMs($("#f-end").value) - toMs($("#f-begin").value)) / 60_000;
    if (!formats.length || !(minutes > 0)) { $("#f-summary").textContent = ""; return; }
    const n = Math.ceil(minutes / chunk);
    $("#f-summary").textContent = fmt(T.sch_summary, { n, chunk: chunkLabel(chunk), formats: formats.join(" + "), files: n * formats.length });
  }

  $("#job-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const t = task(), s = service();
    const formats = [$("#f-mp4").checked && "mp4", $("#f-mp3").checked && "mp3"].filter(Boolean);
    const err = (text) => { $("#f-error").hidden = false; $("#f-error").textContent = text; };
    if (!t) return err(T.rec_no_tasks);
    if (!formats.length) return err(T.sch_err_formats);
    if (!state.folders.length) return err(T.sch_no_folders);
    const audioPid = $("#f-track").value ? Number($("#f-track").value) : s?.audio[0]?.pid ?? null;
    const body = {
      name: $("#f-name").value.trim(), server_id: t.server_id, task_id: t.task_id, task_name: t.name,
      channel: services.length > 1 && s ? s.name : t.name,
      pids: s ? [...s.video, ...s.audio.map((a) => a.pid)] : null, audio_pid: audioPid,
      formats, folder_id: Number($("#f-folder").value), chunk_minutes: Number($("#f-chunk").value), kind: kind(),
    };
    if (kind() === "once") {
      Object.assign(body, { begin: $("#f-begin").value, end: $("#f-end").value, start_at: when() === "at" ? $("#f-start").value : null });
    } else body.run_at = $("#f-run-at").value;
    try {
      await api("/api/schedule/jobs", "POST", body);
      $("#job-form").hidden = true;
      delete $("#f-name").dataset.touched;
      message(fmt(T.sch_created, { name: body.name }));
      await load();
    } catch (ex) { err(ex.message); }
  });

  // --- Folders (administrators) ----------------------------------------------------------------------------
  function renderFolders() {
    if (!$("#folder-rows")) return;
    $("#folder-rows").innerHTML = state.folders.length ? state.folders.map((f) => `<tr data-folder="${f.id}">
      <td><b>${escapeHtml(f.name)}</b></td><td class="mono">${escapeHtml(f.path)}</td>
      <td class="actions-cell"><button type="button" class="link danger" data-act="del-folder">${escapeHtml(T.tpl_delete)}</button></td></tr>`).join("")
      : `<tr><td colspan="3" class="muted">${escapeHtml(T.sch_no_folders_admin)}</td></tr>`;
  }
  $("#folder-rows")?.addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act=del-folder]");
    if (!b) return;
    const f = state.folders.find((x) => String(x.id) === b.closest("tr").dataset.folder);
    if (!confirm(fmt(T.sch_folder_delete_confirm, { name: f.name }))) return;
    try { await api(`/api/schedule/folders/${f.id}`, "DELETE"); await load(); } catch (err) { message(err.message, true); }
  });
  $("#folder-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/schedule/folders", "POST", { name: $("#d-name").value.trim(), path: $("#d-path").value.trim() });
      $("#d-name").value = ""; $("#d-path").value = "";
      message("");
      await load();
    } catch (err) { message(err.message, true); }
  });

  load().catch((err) => message(err.message, true));
})();
