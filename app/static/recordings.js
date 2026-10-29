"use strict";
(() => {
  const T = window.I18N;
  const MAX_MIN = window.REC_MAX_MINUTES;
  const $ = (sel) => document.querySelector(sel);
  const DAY = 86400;
  const LOSS = new Set(["signalloss", "diskwriteerror", "sqLerror", "notenoughperformance", "diskwaittimeout",
    "sqLwaittimeout", "corrupteddata", "nomediadata", "nolicense"]);

  const escapeHtml = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (s, vars) => s.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");

  async function api(path, body, method) {
    const opts = body === undefined ? { method: method || "GET" } : {
      method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    };
    const resp = await fetch(path, opts);
    if (resp.status === 401) { location.href = "/login"; throw new Error("session"); }
    const data = await resp.json();
    if (!resp.ok) {
      const d = data.detail;
      throw new Error(Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d || data.message || resp.statusText);
    }
    return data;
  }

  // Times are naive "YYYY-MM-DDTHH:MM:SS" strings in the user's Toolkit time zone (as the server expects);
  // arithmetic on them goes through UTC so the browser's own zone does not interfere.
  const toMs = (s) => Date.parse(`${s.length === 16 ? s + ":00" : s}Z`);
  const fromMs = (ms) => new Date(ms).toISOString().slice(0, 19);
  const midnight = (ms) => ms - (ms % (DAY * 1000));

  let tasks = [];
  let task = null;
  let nowStr = null;

  // --- Channels -------------------------------------------------------------------------------------------
  async function loadTasks() {
    const data = await api("/api/rec/tasks");
    nowStr = data.now;
    $("#no-servers").hidden = data.connected;
    $("#server-errors").innerHTML = data.errors.map((e) =>
      `<div class="error">${escapeHtml(T.err_server_unavailable.replace("{name}", e.server))}: ${escapeHtml(e.message)}</div>`).join("");
    renderServers(data.servers, data.scope);
    tasks = data.tasks;
    task = null;
    $("#no-tasks").hidden = !data.connected || tasks.length > 0;
    for (const id of ["#pick", "#tl-wrap", "#period"]) $(id).hidden = !tasks.length;
    if (!tasks.length) return;
    $("#task").innerHTML = tasks.map((t, i) => {
      const where = data.multi_server ? `${t.server} · ${t.node}` : t.node;
      return `<option value="${i}"${t.disabled ? ' class="rec-off"' : ""}>${escapeHtml(t.name)} — ${escapeHtml(where)}${t.disabled ? ` (${escapeHtml(T.rec_disabled)})` : ""}</option>`;
    }).join("");
    pickTask(0);
    // Default piece: 5 minutes ending 2 minutes ago (the most recent minutes are not written yet).
    const end = toMs(nowStr) - 120_000;
    setPeriod(end - 300_000, end);
    centerOn(end - 150_000);
  }

  function pickTask(i) {
    task = tasks[i];
    $("#task").classList.toggle("rec-off", !!task.disabled); // the closed list shows the pick greyed too
    loadServices();
  }
  $("#task").addEventListener("change", () => { pickTask(Number($("#task").value)); loadTimeline(); });

  // The same pick as the header's server switcher (shown only with several connected servers).
  function renderServers(servers, scope) {
    $("#server-pick").hidden = servers.length < 2;
    $("#server").innerHTML = `<option value="">${escapeHtml(T.scope_all)}</option>` + servers.map((s) =>
      `<option value="${s.id}"${s.id === scope ? " selected" : ""}>${escapeHtml(s.name)}</option>`).join("");
  }
  $("#server").addEventListener("change", async () => {
    const v = $("#server").value;
    try {
      await api("/api/scope", { server_id: v === "" ? null : Number(v) });
      const header = document.getElementById("scope");
      if (header) header.value = v;
      await loadTasks();
    } catch (err) {
      $("#server-errors").innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
    }
  });

  // A recording of a whole stream holds every channel of it: the file is made for the channel picked here.
  let services = [];
  let svcSeq = 0;
  async function loadServices() {
    const seq = ++svcSeq;
    services = [];
    renderServices();
    if (!task.object_id) return;
    const q = new URLSearchParams({ server_id: task.server_id, node_id: task.node_id, object_id: task.object_id });
    try {
      const list = await api(`/api/rec/services?${q}`);
      if (seq !== svcSeq) return;
      services = list;
    } catch { /* without the list the whole recording is saved, as before */ }
    renderServices();
  }

  function renderServices() {
    const multi = services.length > 1;
    $("#service-wrap").hidden = !multi;
    $("#service").innerHTML = services.map((s, i) =>
      `<option value="${i}">${escapeHtml(s.name)}${s.service_id ? ` (${escapeHtml(T.rec_service_id)} ${escapeHtml(s.service_id)})` : ""}</option>`).join("");
    $("#stream-note").hidden = !multi;
    renderTracks();
  }

  const service = () => services[Number($("#service").value || 0)] || null;

  function renderTracks() {
    const audio = service()?.audio || [];
    $("#track-wrap").hidden = audio.length < 2;
    $("#track").innerHTML = audio.map((a) => `<option value="${a.pid}">${escapeHtml(a.name)}</option>`).join("");
  }
  $("#service").addEventListener("change", renderTracks);

  // --- Timeline -------------------------------------------------------------------------------------------
  // The timeline shows a window of `span` seconds from `winStart` (ms, see toMs): a whole day, or a zoom of
  // a few hours or minutes, which makes picking a 5-minute piece practical.
  const TICK = { 86400: 3 * 3600, 21600: 3600, 3600: 600, 900: 180 };
  let span = 3600;
  let winStart = 0;
  let intervals = [];
  let nowSec = null;
  let tlSeq = 0;

  function markSpan() {
    for (const b of document.querySelectorAll("#spans button")) b.classList.toggle("active", Number(b.dataset.sec) === span);
  }

  // Put the window around a moment: the day for 24 h, otherwise aligned to a tick so the labels stay round.
  function centerOn(ms) {
    if (span === DAY) winStart = midnight(ms);
    else {
      const step = TICK[span] * 1000;
      winStart = Math.floor((ms - span * 500) / step) * step;
    }
    loadTimeline();
  }

  async function loadTimeline() {
    markSpan();
    $("#day").value = fromMs(winStart).slice(0, 10);
    renderTimeline();
    if (!task) return;
    const seq = ++tlSeq;
    const q = new URLSearchParams({ server_id: task.server_id, task_id: task.task_id, start: fromMs(winStart), seconds: span });
    try {
      const data = await api(`/api/rec/timeline?${q}`);
      if (seq !== tlSeq) return;
      intervals = data.intervals;
      nowSec = data.now;
      renderTimeline();
    } catch (err) {
      if (seq === tlSeq) $("#tl").innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
    }
  }

  const kind = (iv) => !iv ? "none" : iv.media ? "media" : LOSS.has(iv.status) ? "loss" : "none";

  function renderTimeline() {
    const pct = (sec) => `${(100 * Math.min(Math.max(sec, 0), span) / span).toFixed(3)}%`;
    const bars = intervals.map((iv) =>
      `<div class="rec-iv rec-${kind(iv)}" style="left:${pct(iv.from)};width:calc(${pct(iv.to)} - ${pct(iv.from)})"></div>`).join("");
    const from = (toMs($("#from").value) - winStart) / 1000, to = (toMs($("#to").value) - winStart) / 1000;
    const sel = Number.isFinite(from) && Number.isFinite(to) && to > 0 && from < span
      ? `<div class="rec-sel" style="left:${pct(from)};width:calc(${pct(to)} - ${pct(from)})"></div>` : "";
    const now = nowSec !== null ? `<div class="rec-now" style="left:${pct(nowSec)}"></div>` : "";
    $("#tl").innerHTML = bars + sel + now;
    const ticks = [];
    for (let sec = 0; sec <= span; sec += TICK[span]) ticks.push(`<span style="left:${pct(sec)}">${fromMs(winStart + sec * 1000).slice(11, 16)}</span>`);
    $("#ticks").innerHTML = ticks.join("");
  }

  const secAt = (e) => {
    const r = $("#tl").getBoundingClientRect();
    return Math.min(Math.max(span * (e.clientX - r.left) / r.width, 0), span);
  };

  $("#tl").addEventListener("click", (e) => {
    const length = Math.max(60_000, toMs($("#to").value) - toMs($("#from").value) || 300_000);
    const begin = winStart + Math.round(secAt(e)) * 1000;
    setPeriod(begin, begin + length);
  });

  // Tooltip at the cursor: the time there and what the recording has at that moment (as in the legend).
  const tip = $("#tl-tip");
  const LABEL = { media: T.rec_legend_media, loss: T.rec_legend_loss, none: T.rec_legend_none };
  $("#tl").addEventListener("mousemove", (e) => {
    const sec = secAt(e);
    const iv = intervals.find((x) => x.from <= sec && sec < x.to);
    const k = kind(iv);
    const lines = [fromMs(winStart + Math.floor(sec) * 1000).replace("T", " "),
      `${LABEL[k]}${iv && !iv.media && iv.status ? ` (${iv.status})` : ""}`];
    const from = (toMs($("#from").value) - winStart) / 1000, to = (toMs($("#to").value) - winStart) / 1000;
    if (from <= sec && sec < to) lines.push(T.rec_legend_selected);
    tip.textContent = lines.join("\n");
    tip.hidden = false;
    const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    const y = e.clientY + 16 + tip.offsetHeight > window.innerHeight ? e.clientY - tip.offsetHeight - 10 : e.clientY + 16;
    tip.style.left = `${x}px`;
    tip.style.top = `${y}px`;
  });
  $("#tl").addEventListener("mouseleave", () => { tip.hidden = true; });

  for (const b of document.querySelectorAll("#spans button")) {
    b.addEventListener("click", () => {
      span = Number(b.dataset.sec);
      const from = toMs($("#from").value), to = toMs($("#to").value);
      centerOn(Number.isFinite(from) && Number.isFinite(to) ? (from + to) / 2 : winStart + span * 500);
    });
  }
  $("#day").addEventListener("change", () => {
    const day = toMs(`${$("#day").value}T00:00:00`);
    if (!Number.isFinite(day)) return;
    winStart = day + (span === DAY ? 0 : winStart - midnight(winStart));  // same time of day, other date
    loadTimeline();
  });
  $("#day-prev").addEventListener("click", () => { winStart -= span * 1000; loadTimeline(); });
  $("#day-next").addEventListener("click", () => { winStart += span * 1000; loadTimeline(); });

  // --- Period ---------------------------------------------------------------------------------------------
  function setPeriod(beginMs, endMs) {
    $("#from").value = fromMs(beginMs);
    $("#to").value = fromMs(endMs);
    markLength();
    renderTimeline();
  }

  function markLength() {
    const min = (toMs($("#to").value) - toMs($("#from").value)) / 60_000;
    for (const b of document.querySelectorAll("#lengths button")) b.classList.toggle("active", Number(b.dataset.min) === min);
  }

  // A piece typed in or lengthened beyond the visible window brings the window to it.
  function followPeriod() {
    const from = toMs($("#from").value), to = toMs($("#to").value);
    markLength();
    if (Number.isFinite(from) && (from < winStart || from >= winStart + span * 1000)) centerOn(Number.isFinite(to) ? (from + to) / 2 : from);
    else renderTimeline();
  }

  for (const b of document.querySelectorAll("#lengths button")) {
    b.addEventListener("click", () => {
      const begin = toMs($("#from").value);
      if (Number.isFinite(begin)) setPeriod(begin, begin + Number(b.dataset.min) * 60_000);
    });
  }
  $("#from").addEventListener("change", followPeriod);
  $("#to").addEventListener("change", followPeriod);

  function period() {
    const begin = $("#from").value, end = $("#to").value;
    const problem = !begin || !end || toMs(end) <= toMs(begin) ? T.err_period
      : toMs(end) - toMs(begin) > MAX_MIN * 60_000 ? fmt(T.rec_err_too_long, { n: MAX_MIN }) : "";
    $("#period-error").innerHTML = problem ? `<div class="error">${escapeHtml(problem)}</div>` : "";
    if (problem) return null;
    const svc = services.length > 1 ? service() : null;
    return { server_id: task.server_id, task_id: task.task_id, name: svc ? svc.name : task.name, begin, end };
  }

  // --- Player ---------------------------------------------------------------------------------------------
  const video = $("#video");
  let piece = null;   // id of the piece being played
  let hls = null;
  let pollTimer = null;

  async function stopPiece() {
    clearTimeout(pollTimer);
    if (hls) { hls.destroy(); hls = null; }
    video.removeAttribute("src");
    video.load();
    if (piece) {
      const id = piece;
      piece = null;
      fetch(`/api/rec/play/${id}`, { method: "DELETE", keepalive: true }).catch(() => {});
    }
  }

  $("#play").addEventListener("click", async () => {
    const p = period();
    if (!p) return;
    const btn = $("#play");
    btn.disabled = true;
    try {
      await stopPiece();
      video.muted = true;  // every new piece starts silent; the user turns the sound on in the player
      $("#video-empty").hidden = true;
      $("#player-title").textContent = `${p.name} · ${p.begin.replace("T", " ")} – ${p.end.slice(11)}`;
      $("#player-status").textContent = fmt(T.rec_preparing, { done: 0, total: Math.round((toMs(p.end) - toMs(p.begin)) / 1000) });
      piece = (await api("/api/rec/play", p)).id;
      poll(piece, false);
    } catch (err) {
      $("#player-status").innerHTML = `<span class="error">${escapeHtml(err.message)}</span>`;
    } finally {
      btn.disabled = false;
    }
  });

  async function poll(id, attached) {
    if (id !== piece) return;
    let st;
    try {
      st = await api(`/api/rec/play/${id}`);
    } catch (err) {
      $("#player-status").innerHTML = `<span class="error">${escapeHtml(err.message)}</span>`;
      return;
    }
    if (id !== piece) return;
    if (st.failed) {
      $("#player-status").innerHTML = `<span class="error">${escapeHtml(T.rec_failed)}: ${escapeHtml(st.state)} ${escapeHtml(st.message)}</span>`;
      return;
    }
    $("#player-status").textContent = st.ended ? fmt(T.rec_ready, { total: st.seconds })
      : fmt(T.rec_preparing, { done: st.seconds, total: st.total_seconds });
    if (st.playable && !attached) {
      attach(id);
      attached = true;
    }
    if (!st.ended) pollTimer = setTimeout(() => poll(id, attached), 1500);
  }

  function attach(id) {
    const src = `/api/rec/hls/${id}/playlist.m3u8`;
    if (window.Hls && Hls.isSupported()) {
      // While MultiProbe is still writing, the playlist looks live; start from the beginning anyway.
      hls = new Hls({ startPosition: 0, liveDurationInfinity: true, maxBufferLength: 30 });
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
      hls.on(Hls.Events.ERROR, (_, data) => {
        if (data.fatal && data.type === Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad();
        else if (data.fatal && data.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
      });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = src;  // Safari plays HLS itself
      video.play().catch(() => {});
    }
  }

  window.addEventListener("pagehide", () => {
    if (piece) fetch(`/api/rec/play/${piece}`, { method: "DELETE", keepalive: true }).catch(() => {});
  });

  // --- Saving ---------------------------------------------------------------------------------------------
  const PHASES = { cutting: T.rec_phase_cutting, downloading: T.rec_phase_downloading, converting: T.rec_phase_converting };
  const fmtSize = (n) => n >= 1 << 30 ? `${(n / (1 << 30)).toFixed(2)} GB` : n >= 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MB` : `${Math.ceil(n / 1024)} KB`;

  async function save(format) {
    const p = period();
    if (!p) return;
    const row = document.createElement("div");
    row.className = "rec-export";
    row.innerHTML = `<div class="rec-export-name">${escapeHtml(`${p.name} · ${p.begin.replace("T", " ")} – ${p.end.slice(11)} · ${format.toUpperCase()}`)}</div>
      <div class="rec-bar"><div class="bar-fill" style="width:0"></div></div><div class="muted rec-export-state"></div>`;
    $("#exports").prepend(row);
    const state = row.querySelector(".rec-export-state"), fill = row.querySelector(".bar-fill");
    try {
      const svc = service();
      const audioPid = $("#track").value ? Number($("#track").value) : svc?.audio[0]?.pid ?? null;
      const pids = svc ? [...svc.video, ...(format === "mp3" ? [] : svc.audio.map((a) => a.pid))] : null;
      const { id } = await api("/api/rec/export", { ...p, format, pids: pids && pids.length ? pids : null, audio_pid: audioPid });
      for (;;) {
        const st = await api(`/api/rec/export/${id}`);
        fill.style.width = `${st.percent}%`;
        if (st.state === "error") throw new Error(st.error);
        if (st.state === "done") {
          row.querySelector(".rec-bar").remove();
          state.innerHTML = `<a class="btn small" href="/rec/export/${id}/file">${escapeHtml(T.rec_download)}</a>
            <span>${escapeHtml(st.file_name)} · ${fmtSize(st.size)}</span>`;
          return;
        }
        state.textContent = `${PHASES[st.phase] || ""} · ${st.percent}%`;
        await new Promise((r) => setTimeout(r, 1500));
      }
    } catch (err) {
      state.innerHTML = `<span class="error">${escapeHtml(err.message)}</span>`;
    }
  }
  $("#save-mp4").addEventListener("click", () => save("mp4"));
  $("#save-mp3").addEventListener("click", () => save("mp3"));

  loadTasks().catch((err) => { $("#server-errors").innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`; });
})();
