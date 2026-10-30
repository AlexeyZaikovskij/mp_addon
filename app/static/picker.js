"use strict";
// A drop-down list with a search field, a Reset button and groups that fold and unfold: the channels of a
// node grouped by stream, the measures grouped by analyzer. Several items can be ticked (multi) or one picked.
//
//   const pk = Picker(wrapper, { multi, texts: { search, reset, resetHint, noMatch }, onChange(values) });
//   pk.setGroups([{ key, label, muted, items: [{ value, label, muted, title }] }]);
//   pk.setSelected(values); pk.setLabel(text);
//
// The wrapper holds the button; the list opens under it. Values are strings.
window.Picker = function Picker(wrapper, opts) {
  const T = opts.texts;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  wrapper.classList.add("pk");
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "pk-btn";
  const pop = document.createElement("div");
  pop.className = "pk-pop";
  pop.hidden = true;
  pop.innerHTML = `<div class="pk-head"><input type="search" class="pk-search" placeholder="${esc(T.search)}">
    <button type="button" class="btn small pk-reset" title="${esc(T.resetHint || "")}">${esc(T.reset)}</button></div>
    <div class="pk-list"></div>`;
  wrapper.append(btn, pop);
  const search = pop.querySelector(".pk-search"), list = pop.querySelector(".pk-list"), reset = pop.querySelector(".pk-reset");

  let groups = [], selected = new Set();
  const folded = new Map();  // group key -> folded; decided when a group is first seen

  function render() {
    reset.disabled = selected.size === 0;
    const q = search.value.trim().toLowerCase();
    const big = groups.reduce((n, g) => n + g.items.length, 0) > 15;
    list.innerHTML = groups.map((g) => {
      const items = q ? g.items.filter((i) => `${g.label} ${i.label}`.toLowerCase().includes(q)) : g.items;
      if (!items.length) return "";
      // A long list starts folded except the groups with a picked item; a search unfolds what it finds.
      if (!folded.has(g.key)) folded.set(g.key, big && !g.items.some((i) => selected.has(i.value)));
      const open = g.label === null || q || !folded.get(g.key);
      const picked = g.items.filter((i) => selected.has(i.value)).length;
      const head = g.label === null ? "" : `<button type="button" class="pk-group${g.muted ? " muted" : ""}" data-group="${esc(g.key)}">
          <span class="pk-arrow">${open ? "▾" : "▸"}</span><span class="pk-glabel">${esc(g.label)}</span>
          <span class="pk-count">${picked ? `${picked} / ` : ""}${g.items.length}</span></button>`;
      return head + (open ? items.map((i) => `<label class="pk-item${g.label === null ? " pk-flat" : ""}${i.muted ? " muted" : ""}${selected.has(i.value) ? " on" : ""}"${i.title ? ` title="${esc(i.title)}"` : ""}>
          <input type="${opts.multi ? "checkbox" : "radio"}" name="pk" value="${esc(i.value)}"${selected.has(i.value) ? " checked" : ""}>
          <span>${esc(i.label)}</span></label>`).join("") : "");
    }).join("") || `<p class="muted pk-empty">${esc(T.noMatch)}</p>`;
  }

  btn.addEventListener("click", () => {
    pop.hidden = !pop.hidden;
    if (!pop.hidden) { render(); search.focus(); }
  });
  // The path is taken when the click happens: folding a group redraws the list, so by now the clicked
  // header may be gone from the page and wrapper.contains(e.target) would wrongly say "outside".
  document.addEventListener("click", (e) => { if (!pop.hidden && !e.composedPath().includes(wrapper)) pop.hidden = true; });
  pop.addEventListener("keydown", (e) => { if (e.key === "Escape") { pop.hidden = true; btn.focus(); } });
  search.addEventListener("input", render);
  list.addEventListener("click", (e) => {
    const head = e.target.closest(".pk-group");
    if (!head) return;
    folded.set(head.dataset.group, !folded.get(head.dataset.group));
    render();
  });
  list.addEventListener("change", (e) => {
    if (e.target.name !== "pk") return;
    const v = e.target.value;
    let next;
    if (opts.multi) {
      next = new Set(selected);
      if (e.target.checked) next.add(v); else next.delete(v);
    } else {
      next = new Set([v]);
      pop.hidden = true;
    }
    // The owner decides (it may refuse, e.g. too many lines) and calls setSelected.
    opts.onChange([...next]);
  });
  reset.addEventListener("click", () => {
    search.value = "";
    opts.onChange([]);
  });

  return {
    setGroups(g) { groups = g; if (!pop.hidden) render(); },
    setSelected(values) { selected = new Set(values); if (!pop.hidden) render(); },
    setLabel(text, title) { btn.textContent = text; btn.title = title || text; },
    close() { pop.hidden = true; },
  };
};
