"use strict";
(() => {
  const T = window.I18N;
  const $ = (sel) => document.querySelector(sel);
  const form = $("#wizard");

  // Objects of every ticked node. Ids are "server:node|objectId" so objects of different servers
  // never clash; with several nodes each gets a root row "Server · Node".
  let objects = [];
  let nodesAll = [];
  const checkedNodes = new Set(); // "server:node"
  const nodeKey = (sid, nid) => `${sid}:${nid}`;
  const rawId = (id) => id.slice(id.indexOf("|") + 1);
  const nodeOf = (id) => id.slice(0, id.indexOf("|"));
  const selected = new Set();  // selected object ids
  const expanded = new Set();  // ids of open tree branches; everything starts collapsed
  const checkedMeasures = new Set(); // "analyzerId|column"

  async function api(path, body, method = "POST") {
    const resp = await fetch(path, body === undefined && method === "POST" ? {} : {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (resp.status === 401) {
      alert(T.err_session);
      location.href = "/login";
      throw new Error("session");
    }
    const data = await resp.json();
    if (!resp.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : data.detail;
      throw new Error(data.message || detail || data.error || resp.statusText);
    }
    return data;
  }

  const reportType = () => form.querySelector("input[name=type]:checked").value;

  // --- Step 2: servers ------------------------------------------------------
  async function loadNodes() {
    const data = await api("/api/nodes", undefined, "GET");
    nodesAll = data.nodes;
    const groups = new Map();
    for (const n of nodesAll) (groups.get(n.server) || groups.set(n.server, []).get(n.server)).push(n);
    $("#nodes").innerHTML = nodesAll.length ? [...groups.entries()].map(([server, list]) => `
      <div class="node-group">
        ${data.multi_server ? `<div class="node-server">${escapeHtml(server)}</div>` : ""}
        ${list.map((n) => `<label class="inline"><input type="checkbox" data-node="${nodeKey(n.server_id, n.id)}"
            ${checkedNodes.has(nodeKey(n.server_id, n.id)) ? "checked" : ""}> ${escapeHtml(n.name)}</label>`).join("")}
      </div>`).join("") : `<p class="muted">—</p>`;
    $("#node-errors").innerHTML = data.errors.map((e) =>
      `<div class="error">${escapeHtml(T.err_server_unavailable.replace("{name}", e.server))}: ${escapeHtml(e.message)}</div>`).join("");
    if (nodesAll.length === 1 && !checkedNodes.size) {
      checkedNodes.add(nodeKey(nodesAll[0].server_id, nodesAll[0].id));
      $("#nodes input").checked = true;
      await loadObjects();
    }
  }

  $("#nodes").addEventListener("change", (e) => {
    const key = e.target.dataset.node;
    if (!key) return;
    if (e.target.checked) checkedNodes.add(key); else checkedNodes.delete(key);
    loadObjects(true);
  });

  // keep = keep the ticks of objects that are still listed (a node was added or removed)
  async function loadObjects(keep = false) {
    const before = new Set(selected);
    selected.clear();
    if (!keep) expanded.clear();
    objects = [];
    $("#obj-tree").innerHTML = `<p class="muted">${checkedNodes.size ? T.loading : T.select_server}</p>`;
    const grouped = checkedNodes.size > 1 || nodesAll.some((n) => n.server_id !== 0);
    for (const key of checkedNodes) {
      const [sid, nid] = key.split(":").map(Number);
      const node = nodesAll.find((n) => n.server_id === sid && n.id === nid);
      const list = await api(`/api/objects?server_id=${sid}&node_id=${nid}`, undefined, "GET");
      if (grouped) {
        objects.push({ id: key, parentId: null, name: node ? `${node.server} · ${node.name}` : key,
                       kindName: "", analyzers: [], synthetic: true });
        expanded.add(key);
      }
      for (const o of list) {
        objects.push({ ...o, id: `${key}|${o.id}`, parentId: o.parentId ? `${key}|${o.parentId}` : (grouped ? key : null) });
      }
    }
    for (const o of objects) if (before.has(o.id)) selected.add(o.id);
    renderTree();
    selectionChanged();
  }

  // --- Step 3: object tree --------------------------------------------------
  function buildTree(list) {
    const byId = new Map(list.map((o) => [o.id, { ...o, children: [] }]));
    const roots = [];
    for (const o of byId.values()) {
      const parent = o.parentId && byId.get(o.parentId);
      (parent ? parent.children : roots).push(o);
    }
    const sort = (arr) => {
      arr.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
      arr.forEach((o) => sort(o.children));
    };
    sort(roots);
    return roots;
  }

  function renderTree() {
    const query = $("#obj-search").value.trim().toLowerCase();
    const roots = buildTree(objects);

    // An object is shown if it or any descendant matches the search.
    const matches = (o) => !query || o.name.toLowerCase().includes(query) || o.children.some(matches);

    // Collapsed branches are still rendered (just hidden), so ticking a parent also ticks them.
    // While searching, every branch with a match is opened.
    // `hit`: the object itself (or an ancestor) matches the search. Ancestors shown only as
    // context for a match are not hits, so "Select all" leaves them alone.
    const renderNode = (o, parentHit = false) => {
      if (!matches(o)) return "";
      const hit = !query || parentHit || o.name.toLowerCase().includes(query);
      const kids = o.children.map((c) => renderNode(c, hit)).join("");
      const open = kids && (query || expanded.has(o.id));
      const toggle = kids
        ? `<button type="button" class="twisty" data-toggle="${o.id}" aria-expanded="${open ? "true" : "false"}"></button>`
        : `<span class="twisty"></span>`;
      return `<li>
        <div class="row">
          ${toggle}
          <label>
            <input type="checkbox" data-id="${o.id}" ${hit ? "data-hit" : ""} ${selected.has(o.id) ? "checked" : ""}>
            <span>${escapeHtml(o.name)}</span><em>${escapeHtml(o.kindName || "")}</em>
          </label>
        </div>
        ${kids ? `<ul ${open ? "" : "hidden"}>${kids}</ul>` : ""}
      </li>`;
    };
    const html = roots.map((r) => renderNode(r)).join("");
    $("#obj-tree").innerHTML = html ? `<ul>${html}</ul>` : `<p class="muted">—</p>`;
    updateIndeterminate();
  }

  function updateIndeterminate() {
    for (const box of $("#obj-tree").querySelectorAll("li")) {
      const own = box.querySelector(":scope > .row input");
      const below = [...box.querySelectorAll(":scope > ul input")];
      if (below.length) {
        const n = below.filter((b) => b.checked).length;
        own.indeterminate = !own.checked && n > 0;
      }
    }
  }

  $("#obj-tree").addEventListener("change", (e) => {
    const box = e.target;
    if (!box.matches("input[data-id]")) return;
    // Ticking a parent ticks every visible descendant too.
    const li = box.closest("li");
    for (const b of li.querySelectorAll("input[data-id]")) {
      b.checked = box.checked;
      if (box.checked) selected.add(b.dataset.id); else selected.delete(b.dataset.id);
    }
    updateIndeterminate();
    selectionChanged();
  });

  $("#obj-tree").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-toggle]");
    if (!btn) return;
    const id = btn.dataset.toggle;
    const open = btn.getAttribute("aria-expanded") !== "true";
    if (open) expanded.add(id); else expanded.delete(id);
    btn.setAttribute("aria-expanded", String(open));
    btn.closest("li").querySelector(":scope > ul").hidden = !open;
  });

  function setAllExpanded(open) {
    expanded.clear();
    if (open) objects.forEach((o) => expanded.add(o.id));
    renderTree();
  }
  $("#obj-expand").addEventListener("click", () => setAllExpanded(true));
  $("#obj-collapse").addEventListener("click", () => setAllExpanded(false));

  $("#obj-search").addEventListener("input", renderTree);
  // Select what the search found (everything when there is no search).
  $("#obj-all").addEventListener("click", () => {
    for (const b of $("#obj-tree").querySelectorAll("input[data-hit]")) {
      b.checked = true;
      selected.add(b.dataset.id);
    }
    updateIndeterminate();
    selectionChanged();
  });
  $("#obj-clear").addEventListener("click", () => { selected.clear(); renderTree(); selectionChanged(); });

  function selectionChanged() {
    $("#obj-count").textContent = selected.size ? `${T.selected}: ${selected.size}` : "";
    refreshTypeStep();
  }

  function refreshTypeStep() {
    const type = reportType();
    if (type === "measures") loadMeasures();
    else if (type === "scte") updateScteFound();
    else loadAlarmTypes();
  }

  // SCTE-35: the markers live on decoders with the SCTE-35 analyzer (for HLS a "PID: 8191; Scte35"
  // per profile). Ticking the stream ticks them too.
  const SCTE_ANALYZER = 51;
  function updateScteFound() {
    const found = objects.filter((o) => selected.has(o.id) && o.analyzers.includes(SCTE_ANALYZER));
    $("#scte-found").textContent = !selected.size ? T.pick_objects_first
      : found.length ? T.scte_found.replace("{n}", found.length) : T.scte_none_selected;
    $("#scte-found").classList.toggle("error", !!selected.size && !found.length);
  }

  const selectedAnalyzers = () =>
    [...new Set(objects.filter((o) => selected.has(o.id)).flatMap((o) => o.analyzers))];

  // --- Step 4 (alarms): alarm types -------------------------------------------
  // Everything is ticked by default, so we remember what the user un-ticked.
  const uncheckedAlarms = new Set();
  let alarmIdsShown = [];
  let alarmsSeq = 0;

  async function loadAlarmTypes() {
    const box = $("#alarm-types");
    const analyzerIds = selectedAnalyzers();
    alarmIdsShown = [];
    if (!selected.size) { box.innerHTML = `<p class="muted">${T.pick_objects_first}</p>`; updateAlarmCount(); return; }

    const seq = ++alarmsSeq;
    box.innerHTML = `<p class="muted">${T.loading}</p>`;
    const groups = analyzerIds.length ? await api("/api/alarm-types", { analyzer_ids: analyzerIds }) : [];
    if (seq !== alarmsSeq) return;

    alarmIdsShown = groups.flatMap((g) => g.alarms.map((a) => a.id));
    box.innerHTML = alarmIdsShown.length ? groups.map((g) => g.alarms.length ? `
      <fieldset class="measure-group">
        <legend><label><input type="checkbox" data-group="1"> ${escapeHtml(g.name)}</label></legend>
        ${g.alarms.map((a) => `<label class="inline"><input type="checkbox" data-alarm="${a.id}" ${uncheckedAlarms.has(a.id) ? "" : "checked"}>
            ${escapeHtml(a.name)} <em>${a.priority}</em></label>`).join("")}
      </fieldset>` : `
      <fieldset class="measure-group empty">
        <legend>${escapeHtml(g.name)}</legend>
        <span class="muted">${T.group_not_applicable}</span>
      </fieldset>`).join("") : `<p class="muted">${T.no_alarm_types}</p>`;
    syncGroupBoxes($("#alarm-types"), "alarm");
    updateAlarmCount();
  }

  $("#alarm-types").addEventListener("change", (e) => {
    const t = e.target;
    const boxes = t.dataset.group ? t.closest("fieldset").querySelectorAll("input[data-alarm]") : [t];
    for (const b of boxes) {
      if (!b.dataset.alarm) continue;
      b.checked = t.checked;
      if (t.checked) uncheckedAlarms.delete(Number(b.dataset.alarm)); else uncheckedAlarms.add(Number(b.dataset.alarm));
    }
    syncGroupBoxes($("#alarm-types"), "alarm");
    updateAlarmCount();
  });

  const checkedAlarmIds = () => alarmIdsShown.filter((id) => !uncheckedAlarms.has(id));

  function updateAlarmCount() {
    const n = checkedAlarmIds().length;
    $("#alarm-count").textContent = alarmIdsShown.length ? `${T.selected}: ${n} / ${alarmIdsShown.length}` : "";
  }

  // --- Step 4: measures -----------------------------------------------------
  let measuresSeq = 0;
  async function loadMeasures() {
    const box = $("#measures");
    const analyzerIds = selectedAnalyzers();
    if (!selected.size) { box.innerHTML = `<p class="muted">${T.pick_objects_first}</p>`; updateMeasureCount(); return; }
    if (!analyzerIds.length) { box.innerHTML = `<p class="muted">${T.no_measures}</p>`; updateMeasureCount(); return; }

    const seq = ++measuresSeq;
    box.innerHTML = `<p class="muted">${T.loading}</p>`;
    const groups = await api("/api/measures", { analyzer_ids: analyzerIds });
    if (seq !== measuresSeq) return; // a newer request is on its way

    const available = new Set(groups.flatMap((g) => g.measures.map((m) => `${g.id}|${m.column}`)));
    for (const key of [...checkedMeasures]) if (!available.has(key)) checkedMeasures.delete(key);

    box.innerHTML = groups.length ? groups.map((g) => `
      <fieldset class="measure-group">
        <legend><label><input type="checkbox" data-group="${g.id}"> ${escapeHtml(g.name)}</label></legend>
        ${g.measures.map((m) => {
          const key = `${g.id}|${m.column}`;
          return `<label class="inline"><input type="checkbox" data-measure="${key}" ${checkedMeasures.has(key) ? "checked" : ""}>
            ${escapeHtml(m.name)}${m.unit ? ` <em>${escapeHtml(m.unit)}</em>` : ""}</label>`;
        }).join("")}
      </fieldset>`).join("") : `<p class="muted">${T.no_measures}</p>`;
    syncGroupBoxes($("#measures"), "measure");
    updateMeasureCount();
  }

  $("#measures").addEventListener("change", (e) => {
    const t = e.target;
    if (t.dataset.group) {
      for (const b of t.closest("fieldset").querySelectorAll("input[data-measure]")) {
        b.checked = t.checked;
        if (t.checked) checkedMeasures.add(b.dataset.measure); else checkedMeasures.delete(b.dataset.measure);
      }
    } else if (t.dataset.measure) {
      if (t.checked) checkedMeasures.add(t.dataset.measure); else checkedMeasures.delete(t.dataset.measure);
    }
    syncGroupBoxes($("#measures"), "measure");
    updateMeasureCount();
  });

  // Group checkbox reflects its items: ticked, empty or indeterminate.
  function syncGroupBoxes(container, item) {
    for (const fs of container.querySelectorAll("fieldset")) {
      const all = [...fs.querySelectorAll(`input[data-${item}]`)];
      const n = all.filter((b) => b.checked).length;
      const g = fs.querySelector("input[data-group]");
      if (!g) continue; // empty priority group: nothing to select
      g.checked = n === all.length;
      g.indeterminate = n > 0 && n < all.length;
    }
  }

  function updateMeasureCount() {
    $("#meas-count").textContent = checkedMeasures.size ? `${T.selected}: ${checkedMeasures.size}` : "";
  }

  // --- Report type toggles the measures step --------------------------------
  function applyType() {
    const type = reportType();
    $("#alarms-card").hidden = type !== "alarms";
    $("#measures-card").hidden = type !== "measures";
    $("#scte-card").hidden = type !== "scte";
    $("#sections-alarms").hidden = type !== "alarms";
    $("#sections-measures").hidden = type !== "measures";
    $("#sections-scte").hidden = type !== "scte";
    $("#measure-options").hidden = type !== "measures";
    refreshTypeStep();
  }
  for (const r of form.querySelectorAll("input[name=type]")) r.addEventListener("change", applyType);

  // --- Period ---------------------------------------------------------------
  // Values are wall-clock time in the user's MultiProbe time zone, not the browser's.
  // A preset is remembered as "last N hours", so a template re-runs over a fresh period;
  // editing the dates by hand makes the period fixed.
  let periodHours = null;

  function inUserTz(date) {
    const s = new Intl.DateTimeFormat("sv-SE", {
      timeZone: window.USER_TZ, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
    }).format(date);
    return s.replace(" ", "T");
  }
  function markPreset(hours) {
    for (const b of document.querySelectorAll(".presets button")) {
      b.classList.toggle("active", Number(b.dataset.hours) === hours);
    }
  }
  function setPreset(hours) {
    const now = new Date();
    $("#from").value = inUserTz(new Date(now - hours * 3600e3));
    $("#to").value = inUserTz(now);
    periodHours = hours;
    markPreset(hours);
  }
  for (const b of document.querySelectorAll(".presets button")) {
    b.addEventListener("click", () => setPreset(Number(b.dataset.hours)));
  }
  for (const input of [$("#from"), $("#to")]) {
    input.addEventListener("input", () => { periodHours = null; markPreset(null); });
  }

  // --- Wizard state <-> request / template ----------------------------------
  function collect() {
    const type = reportType();
    const alarms = type === "alarms";
    const isMeasures = type === "measures";
    const measures = {};
    for (const key of checkedMeasures) {
      const [id, column] = key.split("|");
      (measures[id] ||= []).push(column);
    }
    const byNode = new Map();
    for (const id of selected) {
      if (!id.includes("|")) continue; // a "Server · Node" row, not an object
      const key = nodeOf(id);
      (byNode.get(key) || byNode.set(key, []).get(key)).push(rawId(id));
    }
    const payload = {
      type: reportType(),
      targets: [...byNode.entries()].map(([key, ids]) => {
        const [server_id, node_id] = key.split(":").map(Number);
        return { server_id, node_id, object_ids: ids };
      }),
      alarm_exclude: alarms ? alarmIdsShown.filter((id) => uncheckedAlarms.has(id)) : [],
      measures: isMeasures ? measures : {},
      resolution: isMeasures ? $("#resolution").value : "auto",
      scte_include_null: type === "scte" && $("#scte-null").checked,
    };
    const box = `#sections-${type}`;
    payload.sections = [...document.querySelectorAll(`${box} input[data-section]:checked`)].map((b) => b.dataset.section);
    payload.with_chart = isMeasures && payload.sections.includes("charts");
    if (!payload.sections.length) throw new Error(T.pick_sections);
    if (periodHours) payload.period_hours = periodHours;
    else Object.assign(payload, { period_from: $("#from").value, period_to: $("#to").value });

    if (!payload.targets.length) throw new Error(T.pick_objects_first);
    if (alarms && alarmIdsShown.length && !checkedAlarmIds().length) throw new Error(T.pick_alarms);
    if (isMeasures && !checkedMeasures.size) throw new Error(T.pick_measures);
    if (type === "scte" && !objects.some((o) => selected.has(o.id) && o.analyzers.includes(SCTE_ANALYZER))) {
      throw new Error(T.scte_none_selected);
    }
    if (isMeasures && payload.resolution === "raw") {
      const hours = periodHours || (new Date(payload.period_to) - new Date(payload.period_from)) / 3600e3;
      if (hours > 24) throw new Error(T.err_raw_too_long);
    }
    if (!periodHours && payload.period_to <= payload.period_from) throw new Error(T.err_period);
    return payload;
  }

  async function applyTemplate(p) {
    form.querySelector(`input[name=type][value="${p.type}"]`).checked = true;
    // Templates saved before several servers were supported: one node of the main server.
    const targets = p.targets || [{ server_id: 0, node_id: p.node_id, object_ids: p.object_ids || [] }];
    checkedNodes.clear();
    for (const t of targets) {
      if (nodesAll.some((n) => n.server_id === t.server_id && n.id === t.node_id)) checkedNodes.add(nodeKey(t.server_id, t.node_id));
    }
    for (const b of document.querySelectorAll("#nodes input[data-node]")) b.checked = checkedNodes.has(b.dataset.node);
    await loadObjects();
    const known = new Set(objects.map((o) => o.id));
    const wantedIds = targets.flatMap((t) => t.object_ids.map((id) => `${nodeKey(t.server_id, t.node_id)}|${id}`));
    const missing = wantedIds.filter((id) => !known.has(id)).length;
    wantedIds.filter((id) => known.has(id)).forEach((id) => selected.add(id));
    uncheckedAlarms.clear();
    (p.alarm_exclude || []).forEach((id) => uncheckedAlarms.add(id));
    checkedMeasures.clear();
    for (const [aid, cols] of Object.entries(p.measures || {})) cols.forEach((c) => checkedMeasures.add(`${aid}|${c}`));
    // Templates saved before sections existed: everything on (charts as they were saved).
    const box = `#sections-${p.type}`;
    const wanted = p.sections || {
      measures: [...(p.with_chart ? ["charts"] : []), "summary", "values"],
      scte: ["summary", "timeline", "missing", "list"],
    }[p.type] || ["summary", "timeline", "availability", "top", "list"];
    $("#scte-null").checked = !!p.scte_include_null;
    for (const b of document.querySelectorAll(`${box} input[data-section]`)) b.checked = wanted.includes(b.dataset.section);
    $("#resolution").value = p.resolution || "auto";
    if (p.period_hours) setPreset(p.period_hours);
    else {
      periodHours = null;
      markPreset(null);
      $("#from").value = p.period_from;
      $("#to").value = p.period_to;
    }
    renderTree();
    applyType();
    selectionChanged();
    return missing;
  }

  // Opened from the overview: tick a channel and everything inside it, open the branches leading to it.
  async function preselect(serverId, nodeId, rawObjectId) {
    const key = nodeKey(serverId, nodeId);
    if (!checkedNodes.has(key)) {
      checkedNodes.add(key);
      for (const b of document.querySelectorAll("#nodes input[data-node]")) b.checked = checkedNodes.has(b.dataset.node);
      await loadObjects(true);
    }
    const objectId = `${key}|${rawObjectId}`;
    const children = new Map();
    for (const o of objects) {
      if (o.parentId) (children.get(o.parentId) || children.set(o.parentId, []).get(o.parentId)).push(o.id);
    }
    const stack = [objectId];
    while (stack.length) {
      const id = stack.pop();
      if (objects.some((o) => o.id === id)) selected.add(id);
      stack.push(...(children.get(id) || []));
    }
    const byId = new Map(objects.map((o) => [o.id, o]));
    for (let cur = byId.get(objectId); cur && cur.parentId; cur = byId.get(cur.parentId)) expanded.add(cur.parentId);
    renderTree();
    selectionChanged();
  }

  function showMessage(text, isError = false) {
    const box = $("#result");
    box.hidden = false;
    box.innerHTML = `<div class="${isError ? "error" : "notice"}">${escapeHtml(text)}</div>`;
  }

  // --- Templates ------------------------------------------------------------
  let templates = [];
  let current = null; // template loaded into the wizard

  async function loadTemplates(selectId) {
    templates = await api("/api/templates", undefined, "GET");
    const mine = templates.filter((t) => t.mine);
    const shared = templates.filter((t) => !t.mine);
    const opt = (t) => `<option value="${t.id}">${escapeHtml(t.name)}${t.mine ? "" : ` — ${escapeHtml(t.owner)}`}</option>`;
    $("#tpl-select").innerHTML = `<option value="">${escapeHtml(T.tpl_none)}</option>` +
      (mine.length ? `<optgroup label="${escapeHtml(T.tpl_mine)}">${mine.map(opt).join("")}</optgroup>` : "") +
      (shared.length ? `<optgroup label="${escapeHtml(T.tpl_shared)}">${shared.map(opt).join("")}</optgroup>` : "");
    $("#tpl-select").value = selectId ? String(selectId) : "";
    current = templates.find((t) => String(t.id) === String(selectId)) || null;
    updateTemplateButtons();
  }

  function updateTemplateButtons() {
    $("#tpl-save").hidden = !(current && current.mine);
  }

  async function openTemplate(id) {
    current = templates.find((t) => String(t.id) === String(id)) || null;
    updateTemplateButtons();
    if (!current) return;
    history.replaceState(null, "", `/reports?template=${current.id}`);
    const missing = await applyTemplate(current.params);
    if (missing) showMessage(T.tpl_missing_objects.replace("{n}", missing), true);
    else $("#result").hidden = true;
  }

  $("#tpl-select").addEventListener("change", (e) => {
    if (e.target.value) openTemplate(e.target.value).catch((err) => showMessage(err.message, true));
    else { current = null; updateTemplateButtons(); history.replaceState(null, "", "/reports"); }
  });

  // Overwrite the loaded template with what the wizard shows now.
  $("#tpl-save").addEventListener("click", async () => {
    try {
      const id = current.id;
      await api(`/api/templates/${id}`, { name: current.name, shared: current.shared, params: collect() }, "PUT");
      await loadTemplates(id);
      showMessage(T.tpl_saved.replace("{name}", current.name));
    } catch (err) { showMessage(err.message, true); }
  });

  // "Save as new template": a small inline form instead of a browser prompt.
  const dialog = $("#tpl-dialog");
  $("#tpl-save-as").addEventListener("click", () => {
    dialog.hidden = false;
    $("#tpl-name").value = current ? T.copy_of.replace("{name}", current.name) : "";
    $("#tpl-shared").checked = false;
    $("#tpl-name").focus();
  });
  $("#tpl-cancel").addEventListener("click", () => { dialog.hidden = true; });
  $("#tpl-confirm").addEventListener("click", async () => {
    const name = $("#tpl-name").value.trim();
    if (!name) { $("#tpl-name").focus(); return; }
    try {
      const { id } = await api("/api/templates", { name, shared: $("#tpl-shared").checked, params: collect() });
      dialog.hidden = true;
      await loadTemplates(id);
      history.replaceState(null, "", `/reports?template=${id}`);
      showMessage(T.tpl_saved.replace("{name}", name));
    } catch (err) { showMessage(err.message, true); }
  });
  $("#tpl-name").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); $("#tpl-confirm").click(); }
  });

  // --- Generate -------------------------------------------------------------
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = $("#generate");
    try {
      const payload = collect();
      button.disabled = true;
      const { id } = await api("/api/report", payload);
      location.href = `/report/${id}`;
    } catch (err) {
      button.disabled = false;
      showMessage(err.message, true);
    }
  });

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  (async () => {
    setPreset(24);
    applyType();
    try {
      await loadNodes();
      const query = new URLSearchParams(location.search);
      const wanted = query.get("template");
      await loadTemplates(wanted);
      if (wanted) await openTemplate(wanted);
      else if (query.get("select")) await preselect(Number(query.get("server") || 0), Number(query.get("node")), query.get("select"));
    } catch (err) {
      showMessage(err.message, true);
    }
  })();
})();
