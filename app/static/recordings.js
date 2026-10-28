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
  const dayStart = () => toMs(`${$("#day").value}T00:00:00`);
  const hms = (sec) => [Math.floor(sec / 3600), Math.floor(sec % 3600 / 60), Math.floor(sec % 60)]
    .map((v) => String(v).padStart(2, "0")).join(":");

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
    tasks = data.tasks;
    $("#no-tasks").hidden = !data.connected || tasks.length > 0;
    for (const id of ["#pick", "#tl-wrap", "#period"]) $(id).hidden = !tasks.length;
    if (!tasks.length) return;
    $("#task").innerHTML = tasks.map((t, i) => {
      const where = data.multi_server ? `${t.server} · ${t.node}` : t.node;
      return `<option value="${i}">${escapeHtml(t.name)} — ${escapeHtml(where)}${t.disabled ? ` (${escapeHtml(T.rec_disabled)})` : ""}</option>`;
    }).join("");
    task = tasks[0];
    // Default piece: 5 minutes ending 2 minutes ago (the most recent minutes are not written yet).
    const end = toMs(nowStr) - 120_000;
    $("#day").value = fromMs(end).slice(0, 10);
    setPeriod(end - 300_000, end);
    loadTimeline();
  }

  $("#task").addEventListener("change", () => { task = tasks[Number($("#task").value)]; loadTimeline(); });

  // --- Timeline -------------------------------------------------------------------------------------------
  let intervals = [];
  let nowSec = null;
  let tlSeq = 0;

  async function loadTimeline() {
    if (!task || !$("#day").value) return;
    const seq = ++tlSeq;
    const q = new URLSearchParams({ server_id: task.server_id, task_id: task.task_id, day: $("#day").value });
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

  function renderTimeline() {
    const pct = (s) => `${(100 * Math.min(Math.max(s, 0), DAY) / DAY).toFixed(3)}%`;
    const bars = intervals.map((iv) => {
      const cls = iv.media ? "rec-media" : LOSS.has(iv.status) ? "rec-loss" : "rec-none";
      const tip = `${hms(iv.from)} – ${hms(iv.to)}: ${iv.status}${iv.storage ? ` (${iv.storage})` : ""}`;
      return `<div class="rec-iv ${cls}" style="left:${pct(iv.from)};width:calc(${pct(iv.to)} - ${pct(iv.from)})" title="${escapeHtml(tip)}"></div>`;
    }).join("");
    const from = (toMs($("#from").value) - dayStart()) / 1000, to = (toMs($("#to").value) - dayStart()) / 1000;
    const sel = Number.isFinite(from) && Number.isFinite(to) && to > 0 && from < DAY
      ? `<div class="rec-sel" style="left:${pct(from)};width:calc(${pct(to)} - ${pct(from)})"></div>` : "";
    const now = nowSec !== null ? `<div class="rec-now" style="left:${pct(nowSec)}"></div>` : "";
    $("#tl").innerHTML = bars + sel + now;
    $("#ticks").innerHTML = [0, 3, 6, 9, 12, 15, 18, 21, 24]
      .map((h) => `<span style="left:${pct(h * 3600)}">${String(h).padStart(2, "0")}:00</span>`).join("");
  }

  $("#tl").addEventListener("click", (e) => {
    const r = $("#tl").getBoundingClientRect();
    const sec = Math.round(DAY * (e.clientX - r.left) / r.width);
    const length = Math.max(60_000, toMs($("#to").value) - toMs($("#from").value) || 300_000);
    const begin = dayStart() + sec * 1000;
    setPeriod(begin, begin + length);
  });

  function shiftDay(days) {
    $("#day").value = fromMs(dayStart() + days * DAY * 1000).slice(0, 10);
    loadTimeline();
  }
  $("#day").addEventListener("change", loadTimeline);
  $("#day-prev").addEventListener("click", () => shiftDay(-1));
  $("#day-next").addEventListener("click", () => shiftDay(1));

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

  for (const b of document.querySelectorAll("#lengths button")) {
    b.addEventListener("click", () => {
      const begin = toMs($("#from").value);
      if (Number.isFinite(begin)) setPeriod(begin, begin + Number(b.dataset.min) * 60_000);
    });
  }
  $("#from").addEventListener("change", () => {
    const begin = toMs($("#from").value);
    if (!Number.isFinite(begin)) return;
    const day = fromMs(begin).slice(0, 10);
    if (day !== $("#day").value) { $("#day").value = day; loadTimeline(); }
    markLength();
    renderTimeline();
  });
  $("#to").addEventListener("change", () => { markLength(); renderTimeline(); });

  function period() {
    const begin = $("#from").value, end = $("#to").value;
    const problem = !begin || !end || toMs(end) <= toMs(begin) ? T.err_period
      : toMs(end) - toMs(begin) > MAX_MIN * 60_000 ? fmt(T.rec_err_too_long, { n: MAX_MIN }) : "";
    $("#period-error").innerHTML = problem ? `<div class="error">${escapeHtml(problem)}</div>` : "";
    if (problem) return null;
    return { server_id: task.server_id, task_id: task.task_id, name: task.name, begin, end };
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
    setTracks(st.tracks);
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

  function setTracks(tracks) {
    const many = tracks && tracks.length > 1;
    $("#track-wrap").hidden = !many;
    if (many && $("#track").options.length !== tracks.length) {
      $("#track").innerHTML = tracks.map((t, i) =>
        `<option value="${i}">${escapeHtml(t.language ? `${t.name} (${t.language})` : t.name)}</option>`).join("");
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
      const { id } = await api("/api/rec/export", { ...p, format, audio: Number($("#track").value || 0) });
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
