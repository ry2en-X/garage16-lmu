// analysis/grid.js — lays the analysis tiles out on a 12-column grid of fixed-height rows and,
// in edit mode, lets the user arrange them (V0.9.5; the V0.9.1 version was an ordered list).
//
// Every tile sits on whole cells (layout.js: x, y, w, h) — pure CSS grid placement, so tiles
// can never overlap or overflow sideways. On a narrow screen (≤ 700 px) the tiles are stacked
// full-width in reading order instead (styles.css), and the drawing / dragging on the grid is
// switched off there; adding a tile still works from the picker.
//
// Edit mode — three ways to change the arrangement, all with pointer events (mouse, pen, touch):
//   * the handle ⠿ moves a tile: a ghost shows the cells it will take, green = free, amber =
//     the tiles in the way will move down. Arrow keys on the handle move it one cell, with
//     Shift they change its size.
//   * the corner ◢ resizes it (never below the type's minimum, see layout.js TILE_SPECS)
//   * pressing on EMPTY cells and dragging draws a box; letting go asks the page which tile to
//     put there (onDraw) — the picker then offers only tiles that fit that box.
// A normal drag on a chart still pans the chart: a tile can never be moved by accident.

import { el } from "../format.js";
import { moduleForType } from "./modules.js";
import {
  COLS, GAP_PX, isFree, layoutRows, overlaps, placeWidget, removeWidget, resizeWidget, ROW_PX, setChannel, setWidgetColors, shiftWidget, tileSpec,
} from "./layout.js";
import { LAP_COLORS } from "./model.js";
import { T } from "./strings.js";

// Space a tile's own chrome takes from its height (padding + the edit bar).
const TILE_PADDING_PX = 16;
const EDIT_BAR_PX = 38;
const EMPTY_ROWS_EDITING = 8; // free rows below the last tile, to draw / drop into
export const STACK_BREAKPOINT_PX = 700;

// Height in css px of a box h rows high.
export const rowsToPx = (h) => h * ROW_PX + (h - 1) * GAP_PX;

// The height the content of a tile can use.
export function contentHeight(h, editing = false) {
  return Math.max(rowsToPx(h) - TILE_PADDING_PX - (editing ? EDIT_BAR_PX : 0), 60);
}

export function windowTitle(widget, catalog) {
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
    if (widget.type === "histogram" && channel.pair) continue; // a lap-PAIR channel has no histogram
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

const sizeLabel = (w, h) => `${w} × ${h}`;

function sizeSelect(widget, onChange) {
  const spec = tileSpec(widget.type);
  const options = spec.presets.map(([w, h]) => [w, h]);
  if (!options.some(([w, h]) => w === widget.w && h === widget.h)) options.unshift([widget.w, widget.h]);
  const select = el("select", { class: "awin__size", "aria-label": T.size, title: T.sizeHint },
    options.map(([w, h]) => el("option", { value: `${w}x${h}` }, sizeLabel(w, h))));
  select.value = `${widget.w}x${widget.h}`;
  select.addEventListener("change", () => {
    const [w, h] = select.value.split("x").map(Number);
    onChange(w, h);
  });
  return select;
}

/**
 * Renders every tile of `layout` into `host`.
 *
 *   ctx            passed to each renderer (see widgets.js)
 *   catalog        channel catalog (labels, groups)
 *   editing        show the edit controls, the cells and the free rows below
 *   onLayoutChange (nextLayout) => void
 *   onDraw         ({x, y, w, h}) => void   — a box was drawn on the grid (edit mode)
 *
 * -> { element, destroy(), cellAt(clientX, clientY), showGhost(box, state), hideGhost(), isStacked() }
 */
export function renderGrid(host, layout, ctx, { catalog, editing, onLayoutChange, onDraw }) {
  const rendered = [];
  const rows = Math.max(layoutRows(layout), 1) + (editing ? EMPTY_ROWS_EDITING : 0);
  const grid = el("div", {
    class: `awin-grid${editing ? " awin-grid--editing" : ""}`,
    "data-editing": editing ? "1" : "0",
    style: `--rows:${rows};--row:${ROW_PX}px;--gap:${GAP_PX}px;`,
  });
  const wrappers = [];
  const stacked = () => {
    try {
      return typeof window !== "undefined" && window.matchMedia ? window.matchMedia(`(max-width: ${STACK_BREAKPOINT_PX}px)`).matches : false;
    } catch {
      return false;
    }
  };

  // --- the cells (edit mode): what can be drawn on, and what shows where a tile will go
  let cellLayer = null;
  if (editing) {
    cellLayer = el("div", { class: "awin-cells", "aria-hidden": "true" });
    const frag = document.createDocumentFragment();
    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < COLS; x++) frag.appendChild(el("div", { class: "awin-cell", style: `grid-column:${x + 1};grid-row:${y + 1};`, "data-x": String(x), "data-y": String(y) }));
    }
    cellLayer.appendChild(frag);
    grid.appendChild(cellLayer);
  }
  const ghost = el("div", { class: "awin-ghost", hidden: "hidden", "aria-hidden": "true" });
  grid.appendChild(ghost);

  layout.widgets.forEach((widget, index) => {
    const module = moduleForType(widget.type);
    const body = el("div", { class: "awin__body" });
    const wrapper = el("section", {
      class: `awin awin--${widget.size} awin--${widget.type}`,
      style: `grid-column:${widget.x + 1} / span ${widget.w};grid-row:${widget.y + 1} / span ${widget.h};--h:${rowsToPx(widget.h)}px;`,
      "data-widget": widget.id, "data-type": widget.type, "data-size": `${widget.w}x${widget.h}`, "data-index": String(index),
      "data-x": String(widget.x), "data-y": String(widget.y), "data-w": String(widget.w), "data-h": String(widget.h),
      ...(widget.channel ? { "data-channel": widget.channel } : {}),
    }, [body]);
    wrappers.push(wrapper);

    let result;
    try {
      // the renderer gets the height it can fill (charts, maps and histograms size to it)
      result = module.renderer({ ...widget, boxHeight: contentHeight(widget.h, editing) }, ctx);
    } catch (err) {
      // One broken tile must not take the whole analysis down.
      result = { element: el("div", { class: "notice notice--error" }, `${windowTitle(widget, catalog)}: ${T.genericError}`), destroy() {} };
    }
    rendered.push(result);
    body.appendChild(result.element);

    if (editing) {
      wrapper.insertBefore(editBar(widget), body);
      wrapper.appendChild(resizeCorner(widget));
    }
    grid.appendChild(wrapper);
  });

  function editBar(widget) {
    const handle = el("button", { type: "button", class: "awin__handle", "aria-label": T.dragHandle, title: T.dragHandleHint }, "⠿");
    attachMove(handle, widget);
    const controls = [
      handle,
      el("span", { class: "awin__name", title: windowTitle(widget, catalog) }, windowTitle(widget, catalog)),
      sizeSelect(widget, (w, h) => onLayoutChange(resizeWidget(layout, widget.id, w, h))),
    ];
    if (widget.type === "histogram" || widget.type === "chart") {
      const valid = new Set(catalog.channels.map((c) => c.key));
      controls.push(channelSelect(widget, layout, catalog, (channel) => onLayoutChange(setChannel(layout, widget.id, channel, valid))));
    }
    if (widget.type === "chart") {
      const colors = widget.colors || {};
      controls.push(el("span", { class: "awin__colors", title: T.windowColors }, [
        colorInput(`${T.windowColors}: ${T.colorLap1}`, colors.a || LAP_COLORS.a, (v) => onLayoutChange(setWidgetColors(layout, widget.id, { ...colors, a: v }))),
        colorInput(`${T.windowColors}: ${T.colorLap2}`, colors.b || LAP_COLORS.b, (v) => onLayoutChange(setWidgetColors(layout, widget.id, { ...colors, b: v }))),
        widget.colors ? el("button", { type: "button", class: "btn btn--ghost awin__color-reset", title: T.colorReset, onclick: () => onLayoutChange(setWidgetColors(layout, widget.id, null)) }, "↺") : null,
      ]));
    }
    controls.push(el("button", {
      type: "button", class: "btn btn--ghost awin__remove", title: T.remove, "aria-label": T.remove,
      disabled: layout.widgets.length <= 1 ? "disabled" : null, onclick: () => onLayoutChange(removeWidget(layout, widget.id)),
    }, "✕"));
    return el("div", { class: "awin__bar" }, controls);
  }

  // ---------------------------------------------------------------- geometry

  // The cell under a screen point (clamped into the grid's columns; rows may go past the end).
  function cellAt(clientX, clientY) {
    const r = grid.getBoundingClientRect();
    const colWidth = (r.width - GAP_PX * (COLS - 1)) / COLS;
    const x = Math.floor((clientX - r.left) / Math.max(colWidth + GAP_PX, 1));
    const y = Math.floor((clientY - r.top) / (ROW_PX + GAP_PX));
    return { x: Math.min(Math.max(x, 0), COLS - 1), y: Math.max(y, 0), inside: clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom };
  }

  // state: "free" | "push" (tiles in the way move down) | "bad" (cannot go there)
  function showGhost(box, state = "free", label = "") {
    ghost.hidden = false;
    ghost.removeAttribute("hidden");
    ghost.className = `awin-ghost awin-ghost--${state}`;
    ghost.setAttribute("style", `grid-column:${box.x + 1} / span ${box.w};grid-row:${box.y + 1} / span ${box.h};`);
    ghost.setAttribute("data-box", `${box.x},${box.y},${box.w},${box.h}`);
    ghost.textContent = label || sizeLabel(box.w, box.h);
    // the grid grows while something is dragged below its end
    const needed = box.y + box.h + 2;
    if (needed > Number(grid.style.getPropertyValue("--rows") || rows)) grid.style.setProperty("--rows", String(needed));
  }
  function hideGhost() {
    ghost.hidden = true;
    ghost.setAttribute("hidden", "hidden");
    ghost.removeAttribute("data-box");
  }

  const ghostState = (box, ignoreId) => (isFree(layout, box, ignoreId) ? "free" : "push");
  const capture = (target, event) => {
    if (target.setPointerCapture && event.pointerId !== undefined) {
      try { target.setPointerCapture(event.pointerId); } catch { /* synthetic events: no capture */ }
    }
  };

  // ---------------------------------------------------------------- move (handle)

  function attachMove(handle, widget) {
    let drag = null;
    const wrapper = () => wrappers[layout.widgets.indexOf(widget)];
    handle.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      if (stacked()) return; // phone: the stacked view has no cells to drop on (arrow keys still work)
      event.preventDefault();
      capture(handle, event);
      const start = cellAt(event.clientX, event.clientY);
      drag = { dx: start.x - widget.x, dy: start.y - widget.y, box: { x: widget.x, y: widget.y, w: widget.w, h: widget.h } };
      wrapper().classList.add("awin--dragging");
      showGhost(drag.box, "free");
    });
    handle.addEventListener("pointermove", (event) => {
      if (!drag) return;
      const cell = cellAt(event.clientX, event.clientY);
      const x = Math.min(Math.max(cell.x - drag.dx, 0), COLS - widget.w);
      const y = Math.max(cell.y - drag.dy, 0);
      if (x === drag.box.x && y === drag.box.y) return;
      drag.box = { ...drag.box, x, y };
      showGhost(drag.box, ghostState(drag.box, widget.id));
    });
    const finish = (commit) => {
      if (!drag) return;
      const { box } = drag;
      drag = null;
      hideGhost();
      const w = wrapper();
      if (w) w.classList.remove("awin--dragging");
      if (commit && (box.x !== widget.x || box.y !== widget.y)) onLayoutChange(placeWidget(layout, widget.id, box.x, box.y));
    };
    handle.addEventListener("pointerup", () => finish(true));
    handle.addEventListener("pointercancel", () => finish(false));
    handle.addEventListener("keydown", (event) => {
      const arrows = { ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0] };
      if (event.key === "Escape" && drag) {
        event.preventDefault();
        finish(false);
        return;
      }
      const step = arrows[event.key];
      if (!step) return;
      event.preventDefault();
      event.stopPropagation(); // the page's ←/→ cursor keys are not for this
      if (event.shiftKey) onLayoutChange(resizeWidget(layout, widget.id, widget.w + step[0], widget.h + step[1]));
      else onLayoutChange(shiftWidget(layout, widget.id, step[0], step[1]));
    });
  }

  // ---------------------------------------------------------------- resize (corner)

  function resizeCorner(widget) {
    const corner = el("button", { type: "button", class: "awin__resize", "aria-label": T.resizeHandle, title: T.resizeHandle, tabindex: "-1" }, "◢");
    let drag = null;
    const { min } = tileSpec(widget.type);
    corner.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      if (stacked()) return;
      event.preventDefault();
      event.stopPropagation();
      capture(corner, event);
      drag = { box: { x: widget.x, y: widget.y, w: widget.w, h: widget.h } };
      showGhost(drag.box, "free");
    });
    corner.addEventListener("pointermove", (event) => {
      if (!drag) return;
      const cell = cellAt(event.clientX, event.clientY);
      const w = Math.min(Math.max(cell.x - widget.x + 1, min[0]), COLS - widget.x);
      const h = Math.min(Math.max(cell.y - widget.y + 1, min[1]), 20);
      if (w === drag.box.w && h === drag.box.h) return;
      drag.box = { ...drag.box, w, h };
      showGhost(drag.box, ghostState(drag.box, widget.id));
    });
    const finish = (commit) => {
      if (!drag) return;
      const { box } = drag;
      drag = null;
      hideGhost();
      if (commit && (box.w !== widget.w || box.h !== widget.h)) onLayoutChange(resizeWidget(layout, widget.id, box.w, box.h));
    };
    corner.addEventListener("pointerup", () => finish(true));
    corner.addEventListener("pointercancel", () => finish(false));
    return corner;
  }

  // ---------------------------------------------------------------- draw a box on empty cells

  if (editing && cellLayer && onDraw) {
    let draw = null;
    const boxOf = (a, b) => ({ x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(a.x - b.x) + 1, h: Math.abs(a.y - b.y) + 1 });
    cellLayer.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      if (stacked()) return;
      event.preventDefault();
      capture(event.target, event);
      const start = cellAt(event.clientX, event.clientY);
      draw = { start, box: boxOf(start, start) };
      showGhost(draw.box, ghostState(draw.box), T.drawHint);
    });
    cellLayer.addEventListener("pointermove", (event) => {
      if (!draw) return;
      const box = boxOf(draw.start, cellAt(event.clientX, event.clientY));
      box.h = Math.min(box.h, 20);
      if (box.x === draw.box.x && box.y === draw.box.y && box.w === draw.box.w && box.h === draw.box.h) return;
      draw.box = box;
      showGhost(box, ghostState(box));
    });
    const finishDraw = (commit) => {
      if (!draw) return;
      const { box } = draw;
      draw = null;
      if (!commit) {
        hideGhost();
        return;
      }
      showGhost(box, ghostState(box), sizeLabel(box.w, box.h)); // stays while the picker is open
      onDraw(box);
    };
    cellLayer.addEventListener("pointerup", () => finishDraw(true));
    cellLayer.addEventListener("pointercancel", () => finishDraw(false));
  }

  host.appendChild(grid);
  return {
    element: grid,
    cellAt,
    showGhost,
    hideGhost,
    isStacked: stacked,
    // for a drop from outside (the picker): which box would a w x h tile take with the
    // pointer at (clientX, clientY)? (the pointer holds the tile's top-left cell)
    boxAt(clientX, clientY, w, h) {
      const cell = cellAt(clientX, clientY);
      if (!cell.inside) return null;
      return { x: Math.min(cell.x, COLS - w), y: cell.y, w, h };
    },
    stateOf: (box) => (layout.widgets.some((o) => overlaps(o, box)) ? "push" : "free"),
    destroy() {
      for (const r of rendered) {
        try { r.destroy(); } catch { /* a tile that fails to clean up must not block the rest */ }
      }
      grid.remove();
    },
  };
}
