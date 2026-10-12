// analysis/grid.js — lays the analysis windows out in a responsive grid and, in edit
// mode, lets the user move / resize / reconfigure them (V0.9.1).
//
// Layout is pure CSS grid (see styles.css: .awin--small/medium/large and the media
// queries), so windows cannot overlap, be cut off or overflow sideways whatever the
// screen width: every window spans a share of the 12 columns that depends on the
// breakpoint, and charts are always at least readable-wide (min-width:0 + canvas
// sized to its box).
//
// Moving windows: only in edit mode, only by the drag HANDLE (pointer events, so mouse,
// pen and touch all work) or the up/down buttons. A normal drag on a chart still pans
// the chart; a window can never be moved by accident.

import { el } from "../format.js";
import { moduleForType } from "./modules.js";
import { moveWidget, nudgeWidget, removeWidget, setChannel, setSize, setWidgetColors, SIZES } from "./layout.js";
import { LAP_COLORS } from "./model.js";
import { T } from "./strings.js";

function windowTitle(widget, catalog) {
  const module = moduleForType(widget.type);
  if (widget.type === "chart" || widget.type === "histogram") {
    const entry = catalog.channels.find((c) => c.key === widget.channel);
    const label = entry ? entry.label : widget.channel;
    return widget.type === "histogram" ? `${module ? module.title : ""} — ${label}` : label;
  }
  return module ? module.title : widget.type;
}

function channelSelect(widget, layout, catalog, onChange) {
  const taken = new Set(layout.widgets.filter((w) => w.type === widget.type && w.id !== widget.id).map((w) => w.channel));
  const groups = new Map();
  for (const channel of catalog.channels) {
    // V0.9.4: a channel of the lap PAIR (line offset) has no histogram
    if (widget.type === "histogram" && channel.pair) continue;
    if (!groups.has(channel.group)) groups.set(channel.group, []);
    groups.get(channel.group).push(channel);
  }
  const select = el("select", { class: "awin__channel", "aria-label": T.channel }, [...groups].map(([group, channels]) => el("optgroup", { label: group }, channels.map((c) => {
    const option = el("option", { value: c.key }, c.unit ? `${c.label} (${c.unit})` : c.label);
    if (taken.has(c.key)) option.setAttribute("disabled", "disabled");
    return option;
  }))));
  select.value = widget.channel;
  select.addEventListener("change", () => onChange(select.value));
  return select;
}

function colorInput(label, value, onChange) {
  const input = el("input", { type: "color", value, "aria-label": label, title: label });
  input.addEventListener("change", () => onChange(input.value));
  return input;
}

/**
 * Renders every window of `layout` into `host`.
 *
 *   ctx            passed to each renderer (see widgets.js)
 *   catalog        channel catalog (labels, groups)
 *   editing        show the edit controls
 *   onLayoutChange (nextLayout) => void
 *
 * -> { destroy(), element }
 */
export function renderGrid(host, layout, ctx, { catalog, editing, onLayoutChange }) {
  const rendered = [];
  const grid = el("div", { class: `awin-grid${editing ? " awin-grid--editing" : ""}`, "data-editing": editing ? "1" : "0" });
  const wrappers = [];

  layout.widgets.forEach((widget, index) => {
    const module = moduleForType(widget.type);
    const body = el("div", { class: "awin__body" });
    const wrapper = el("section", {
      class: `awin awin--${widget.size} awin--${widget.type}`,
      "data-widget": widget.id, "data-type": widget.type, "data-size": widget.size, "data-index": String(index),
      ...(widget.channel ? { "data-channel": widget.channel } : {}),
    }, [body]);
    wrappers.push(wrapper);

    let result;
    try {
      result = module.renderer(widget, ctx);
    } catch (err) {
      // One broken window must not take the whole analysis down.
      result = { element: el("div", { class: "notice notice--error" }, `${windowTitle(widget, catalog)}: ${T.genericError}`), destroy() {} };
    }
    rendered.push(result);
    body.appendChild(result.element);

    if (editing) wrapper.insertBefore(editBar(widget, index), body);
    grid.appendChild(wrapper);
  });

  function editBar(widget, index) {
    const handle = el("button", { type: "button", class: "awin__handle", "aria-label": T.dragHandle, title: T.dragHandle }, "⠿");
    attachDrag(handle, widget, index);
    const controls = [
      handle,
      el("span", { class: "awin__name" }, windowTitle(widget, catalog)),
      el("button", { type: "button", class: "btn btn--ghost awin__up", "aria-label": T.moveUp, title: T.moveUp, disabled: index === 0 ? "disabled" : null, onclick: () => onLayoutChange(nudgeWidget(layout, widget.id, -1)) }, "↑"),
      el("button", { type: "button", class: "btn btn--ghost awin__down", "aria-label": T.moveDown, title: T.moveDown, disabled: index === layout.widgets.length - 1 ? "disabled" : null, onclick: () => onLayoutChange(nudgeWidget(layout, widget.id, 1)) }, "↓"),
    ];
    const sizeSelect = el("select", { class: "awin__size", "aria-label": T.size }, SIZES.map((s) => el("option", { value: s }, T.sizes[s])));
    sizeSelect.value = widget.size;
    sizeSelect.addEventListener("change", () => onLayoutChange(setSize(layout, widget.id, sizeSelect.value)));
    controls.push(sizeSelect);
    if (widget.type === "histogram") {
      const valid = new Set(catalog.channels.map((c) => c.key));
      controls.push(channelSelect(widget, layout, catalog, (channel) => onLayoutChange(setChannel(layout, widget.id, channel, valid))));
    }
    if (widget.type === "chart") {
      const valid = new Set(catalog.channels.map((c) => c.key));
      controls.push(channelSelect(widget, layout, catalog, (channel) => onLayoutChange(setChannel(layout, widget.id, channel, valid))));
      const colors = widget.colors || {};
      controls.push(el("span", { class: "awin__colors", title: T.windowColors }, [
        colorInput(`${T.windowColors}: ${T.colorLap1}`, colors.a || LAP_COLORS.a, (v) => onLayoutChange(setWidgetColors(layout, widget.id, { ...colors, a: v }))),
        colorInput(`${T.windowColors}: ${T.colorLap2}`, colors.b || LAP_COLORS.b, (v) => onLayoutChange(setWidgetColors(layout, widget.id, { ...colors, b: v }))),
        widget.colors ? el("button", { type: "button", class: "btn btn--ghost awin__color-reset", title: T.colorReset, onclick: () => onLayoutChange(setWidgetColors(layout, widget.id, null)) }, "↺") : null,
      ]));
    }
    controls.push(el("button", { type: "button", class: "btn btn--ghost awin__remove", disabled: layout.widgets.length <= 1 ? "disabled" : null, onclick: () => onLayoutChange(removeWidget(layout, widget.id)) }, T.remove));
    return el("div", { class: "awin__bar" }, controls);
  }

  // Which window is under (x, y)? Falls back to the nearest one by vertical distance,
  // so letting go in a gap still drops somewhere sensible.
  function windowIndexAt(x, y) {
    let nearest = -1;
    let nearestDistance = Infinity;
    for (let i = 0; i < wrappers.length; i++) {
      const r = wrappers[i].getBoundingClientRect();
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return i;
      const dy = y < r.top ? r.top - y : y > r.bottom ? y - r.bottom : 0;
      const dx = x < r.left ? r.left - x : x > r.right ? x - r.right : 0;
      const d = dy * 1000 + dx; // vertical distance dominates: reading order
      if (d < nearestDistance) {
        nearestDistance = d;
        nearest = i;
      }
    }
    return nearest;
  }

  function attachDrag(handle, widget, from) {
    let drag = null;
    const clearMarks = () => wrappers.forEach((w) => w.classList.remove("awin--drop-target", "awin--dragging"));
    handle.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      event.preventDefault();
      if (handle.setPointerCapture && event.pointerId !== undefined) {
        try { handle.setPointerCapture(event.pointerId); } catch { /* synthetic events: no capture */ }
      }
      drag = { from, over: from };
      wrappers[from].classList.add("awin--dragging");
    });
    handle.addEventListener("pointermove", (event) => {
      if (!drag) return;
      const over = windowIndexAt(event.clientX, event.clientY);
      if (over < 0 || over === drag.over) return;
      drag.over = over;
      wrappers.forEach((w, i) => w.classList.toggle("awin--drop-target", i === over && i !== drag.from));
    });
    const finish = (commit) => {
      if (!drag) return;
      const { from: start, over } = drag;
      drag = null;
      clearMarks();
      if (commit && over !== start) onLayoutChange(moveWidget(layout, start, over));
    };
    handle.addEventListener("pointerup", () => finish(true));
    handle.addEventListener("pointercancel", () => finish(false));
    handle.addEventListener("keydown", (event) => {
      if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        event.preventDefault();
        event.stopPropagation(); // the page's ←/→ cursor keys are not for this
        onLayoutChange(nudgeWidget(layout, widget.id, event.key === "ArrowUp" ? -1 : 1));
      }
    });
  }

  host.appendChild(grid);
  return {
    element: grid,
    destroy() {
      for (const r of rendered) {
        try { r.destroy(); } catch { /* a window that fails to clean up must not block the rest */ }
      }
      grid.remove();
    },
  };
}
