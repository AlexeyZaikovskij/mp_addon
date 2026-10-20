"use strict";
(() => {
  const T = window.I18N;
  const $ = (sel) => document.querySelector(sel);
  const form = $("#wizard");

  let objects = [];            // objects of the current node
  const selected = new Set();  // selected object ids
  const expanded = new Set();  // ids of open tree branches; everything starts collapsed
  const checkedMeasures = new Set(); // "analyzerId|column"

  async function api(path, body) {
    const resp = await fetch(path, body === undefined ? {} : {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
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
    const nodes = await api("/api/nodes");
    const sel = $("#node");
    sel.innerHTML = `<option value="">${T.select_server}</option>` +
      nodes.map((n) => `<option value="${n.id}">${escapeHtml(n.name)}</option>`).join("");
    if (nodes.length === 1) {
      sel.value = nodes[0].id;
      loadObjects();
    }
  }

  async function loadObjects() {
    selected.clear();
    expanded.clear();
    objects = [];
    const nodeId = $("#node").value;
    $("#obj-tree").innerHTML = `<p class="muted">${nodeId ? T.loading : T.select_server}</p>`;
    if (nodeId) {
      objects = await api(`/api/objects?node_id=${encodeURIComponent(nodeId)}`);
    }
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
    const renderNode = (o) => {
      if (!matches(o)) return "";
      const kids = o.children.map(renderNode).join("");
      const open = kids && (query || expanded.has(o.id));
      const toggle = kids
        ? `<button type="button" class="twisty" data-toggle="${o.id}" aria-expanded="${open ? "true" : "false"}"></button>`
        : `<span class="twisty"></span>`;
      return `<li>
        <div class="row">
          ${toggle}
          <label>
            <input type="checkbox" data-id="${o.id}" ${selected.has(o.id) ? "checked" : ""}>
            <span>${escapeHtml(o.name)}</span><em>${escapeHtml(o.kindName || "")}</em>
          </label>
        </div>
        ${kids ? `<ul ${open ? "" : "hidden"}>${kids}</ul>` : ""}
      </li>`;
    };
    const html = roots.map(renderNode).join("");
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
  // Select everything currently shown, so "Select all" respects the search filter.
  $("#obj-all").addEventListener("click", () => {
    for (const b of $("#obj-tree").querySelectorAll("input[data-id]")) {
      b.checked = true;
      selected.add(b.dataset.id);
    }
    updateIndeterminate();
    selectionChanged();
  });
  $("#obj-clear").addEventListener("click", () => { selected.clear(); renderTree(); selectionChanged(); });
  $("#node").addEventListener("change", loadObjects);

  function selectionChanged() {
    $("#obj-count").textContent = selected.size ? `${T.selected}: ${selected.size}` : "";
    if (reportType() === "measures") loadMeasures(); else loadAlarmTypes();
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
    box.innerHTML = groups.length ? groups.map((g) => `
      <fieldset class="measure-group">
        <legend><label><input type="checkbox" data-group="1"> ${escapeHtml(g.name)}</label></legend>
        ${g.alarms.map((a) => `<label class="inline"><input type="checkbox" data-alarm="${a.id}" ${uncheckedAlarms.has(a.id) ? "" : "checked"}>
            ${escapeHtml(a.name)} <em>${a.priority}</em></label>`).join("")}
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
      g.checked = n === all.length;
      g.indeterminate = n > 0 && n < all.length;
    }
  }

  function updateMeasureCount() {
    $("#meas-count").textContent = checkedMeasures.size ? `${T.selected}: ${checkedMeasures.size}` : "";
  }

  // --- Report type toggles the measures step --------------------------------
  function applyType() {
    const measures = reportType() === "measures";
    $("#measures-card").hidden = !measures;
    $("#options-card").hidden = !measures;
    $("#alarms-card").hidden = measures;
    if (measures) loadMeasures(); else loadAlarmTypes();
  }
  for (const r of form.querySelectorAll("input[name=type]")) r.addEventListener("change", applyType);

  // --- Period ---------------------------------------------------------------
  // Values are wall-clock time in the user's MultiProbe time zone, not the browser's.
  function inUserTz(date) {
    const s = new Intl.DateTimeFormat("sv-SE", {
      timeZone: window.USER_TZ, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
    }).format(date);
    return s.replace(" ", "T");
  }
  function setPreset(hours) {
    const now = new Date();
    $("#from").value = inUserTz(new Date(now - hours * 3600e3));
    $("#to").value = inUserTz(now);
  }
  for (const b of document.querySelectorAll(".presets button")) {
    b.addEventListener("click", () => setPreset(Number(b.dataset.hours)));
  }

  // --- Generate -------------------------------------------------------------
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const result = $("#result");
    const measures = {};
    for (const key of checkedMeasures) {
      const [id, column] = key.split("|");
      (measures[id] ||= []).push(column);
    }
    const alarms = reportType() === "alarms";
    const chosenAlarms = checkedAlarmIds();
    const payload = {
      type: reportType(),
      node_id: Number($("#node").value),
      object_ids: [...selected],
      // null = every alarm, so alarms without a definition in the catalogue are not lost
      alarm_ids: alarms && chosenAlarms.length < alarmIdsShown.length ? chosenAlarms : null,
      measures: alarms ? {} : measures,
      period_from: $("#from").value,
      period_to: $("#to").value,
      with_chart: !alarms && $("#with-chart").checked,
    };
    const button = $("#generate");
    try {
      if (!payload.object_ids.length) throw new Error(T.pick_objects_first);
      if (alarms && alarmIdsShown.length && !chosenAlarms.length) throw new Error(T.pick_alarms);
      if (!alarms) throw new Error(T.measures_next_stage);
      if (payload.period_to <= payload.period_from) throw new Error(T.err_period);
      button.disabled = true;
      const { id } = await api("/api/report", payload);
      location.href = `/report/${id}`;
    } catch (err) {
      button.disabled = false;
      result.hidden = false;
      result.innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
    }
  });

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  setPreset(24);
  applyType();
  loadNodes().catch((err) => { $("#node").innerHTML = `<option>${escapeHtml(err.message)}</option>`; });
})();
