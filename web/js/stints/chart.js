// stints/chart.js — the charts of the Stint Analyzer (V1.0.3: interactive).
//
// SVG, not canvas: a session has a few dozen laps, the charts scale with the page, screen readers
// get the <title>s, and jsdom can test them. What the user can do on every chart:
//   - point at it: a cursor line and a value box show every series at that lap; all charts of the
//     same group (same x axis, e.g. "lap in session") show the same lap at once
//   - click: pin the cursor there (click again to release); click a dot that is a link to open it
//   - drag across: zoom into that range (all charts of the group follow); double-click or the
//     "Reset zoom" button: back to everything; Ctrl/⌘ + wheel zooms, a sideways wheel pans
//   - arrow keys move the cursor lap by lap, Escape hides it
//   - click a legend entry: hide / show that series
// The chart is drawn for its real width (ResizeObserver), so the labels keep their size on a
// phone and on a wide screen instead of being scaled with the picture.

import {
  clamp, createChartGroup, extent, finite, integerTicks, linearScale, nearestIndex, niceTicks, paddedRange,
  panWindow, zoomWindow,
} from "../core/chart_core.js";
import { t } from "../core/i18n.js";

const NS = "http://www.w3.org/2000/svg";
const DRAG_PX = 6;

function svg(tag, attrs = {}, children = []) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null && v !== false) node.setAttribute(k, String(v));
  for (const c of [].concat(children)) if (c !== null && c !== undefined && c !== false) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  return node;
}

function html(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else node.setAttribute(k, String(v));
  }
  for (const c of [].concat(children)) if (c !== null && c !== undefined && c !== false) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  return node;
}

// The old name, kept for the tests and callers of V0.9.4.
export function ticks(lo, hi, count = 5) {
  return niceTicks(lo, hi, count);
}

export { createChartGroup };

// x values of one series (sorted, unique) — what the cursor snaps to.
function xsOf(series) {
  const xs = new Set();
  for (const s of series) {
    if (s.kind === "band") for (const [x] of s.band || []) { if (finite(x)) xs.add(x); }
    else for (const [x, y] of s.points || []) { if (finite(x) && finite(y)) xs.add(x); }
  }
  return [...xs].sort((a, b) => a - b);
}

// The value of a series at exactly x (dots / lines: the point; band: [low, high]).
function valueAt(s, x) {
  if (s.kind === "band") {
    const row = (s.band || []).find((p) => p[0] === x);
    return row && finite(row[1]) && finite(row[2]) ? { low: row[1], high: row[2] } : null;
  }
  const p = (s.points || []).find((q) => q[0] === x && finite(q[1]));
  return p ? { y: p[1], meta: p[2] } : null;
}

/**
 * series: [{ label, color, kind: "line" | "dots" | "band" | "bars", points: [[x, y, meta?]],
 *            band: [[x, low, high]], dashed, width, radius, onClick(meta), format(v),
 *            legend: false (not in the legend), tooltip: false (not in the value box) }]
 *   meta of a point: { color, faded, radius, title, label (shown in the value box) }
 * options: { width, height, xLabel, yLabel, title, yFormat, xFormat, yMin, yMax, yZero,
 *            integerX, invertY (small values on top: positions), group (createChartGroup()), legend (true = built-in, clickable),
 *            legendExtra: [{ label, color, dot, dashed }] (not clickable), zoom (default true),
 *            markers: [{ x, label }], regions: [{ x0, x1, color, label }],
 *            hlines: [{ y, label, color, dashed }], tooltipTitle(x) }
 * Returns a <div class="schart"> holding the SVG (and the legend, value box, reset button);
 * its dispose() takes the chart out of the group.
 */
export function chart(series, options = {}) {
  const height = options.height || 240;
  const group = options.group || createChartGroup();
  const fy = options.yFormat || ((v) => String(Math.round(v * 100) / 100));
  const fx = options.xFormat || ((v) => String(Math.round(v * 100) / 100));
  const hidden = new Set(series.map((s, i) => (s.hidden ? i : -1)).filter((i) => i >= 0));
  const allXs = xsOf(series);
  const fullX = allXs.length ? [allXs[0], allXs[allXs.length - 1]] : null;
  const zoomable = options.zoom !== false && fullX && fullX[1] - fullX[0] >= 3;

  const box = html("div", { class: "schart" });
  const plot = html("div", { class: "schart__plot" });
  const root = svg("svg", {
    class: "stint-chart", role: "img", tabindex: 0, height,
    "aria-label": options.title || options.yLabel || "",
  });
  const tip = html("div", { class: "schart__tip", role: "status", "aria-live": "polite", hidden: true });
  const reset = html("button", { type: "button", class: "btn btn--ghost btn--sm schart__reset", hidden: true }, t("Reset zoom"));
  plot.append(root, tip, reset);
  box.appendChild(plot);
  if (!allXs.length) {
    root.setAttribute("viewBox", `0 0 ${options.width || 720} ${height}`);
    return box;
  }

  let width = options.width || 720;
  let geom = null;   // { sx, sy, pad, xs } of the last drawing
  let cursorLayer = null;
  let drawnWindow = null;   // the zoom window of the last drawing

  const visible = () => series.filter((_, i) => !hidden.has(i));

  function draw() {
    drawnWindow = group.state.window ? [...group.state.window] : null;
    while (root.firstChild) root.removeChild(root.firstChild);
    root.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const win = group.state.window && fullX ? group.state.window : null;
    const [x0, x1] = win || (fullX[0] === fullX[1] ? [fullX[0] - 1, fullX[1] + 1] : fullX);
    const pad = { l: 58, r: 14, t: options.yLabel ? 24 : 12, b: 34 };
    const inWin = (x) => x >= x0 - 1e-9 && x <= x1 + 1e-9;
    const shown = visible();
    const ys = [];
    for (const s of shown) {
      if (s.kind === "band") for (const [x, lo, hi] of s.band || []) { if (inWin(x)) ys.push(lo, hi); }
      else for (const [x, y] of s.points || []) { if (inWin(x)) ys.push(y); }
    }
    for (const h of options.hlines || []) if (finite(h.y) && h.fit !== false) ys.push(h.y);
    let yr = extent(ys) || [0, 1];
    if (options.yZero) yr = [Math.min(0, yr[0]), Math.max(0, yr[1])];
    yr = paddedRange(yr);
    // Fixed limits only for the full view; a zoomed window fits its own laps.
    if (!win && finite(options.yMin)) yr[0] = options.yMin;
    if (!win && finite(options.yMax)) yr[1] = options.yMax;
    if (!(yr[1] > yr[0])) yr = [yr[0] - 0.5, yr[0] + 0.5];
    const sx = linearScale([x0, x1], [pad.l, width - pad.r]);
    const sy = linearScale([yr[0], yr[1]], options.invertY ? [pad.t, height - pad.b] : [height - pad.b, pad.t]);
    const xs = allXs.filter(inWin);
    geom = { sx, sy, pad, xs, x0, x1 };

    const clip = `schart-clip-${Math.random().toString(36).slice(2, 9)}`;
    root.appendChild(svg("defs", {}, svg("clipPath", { id: clip }, svg("rect", { x: pad.l, y: pad.t - 2, width: Math.max(width - pad.l - pad.r, 1), height: height - pad.t - pad.b + 4 }))));
    // transparent ground: pointer events also in empty space
    root.appendChild(svg("rect", { class: "schart__ground", x: pad.l, y: pad.t, width: Math.max(width - pad.l - pad.r, 1), height: height - pad.t - pad.b, fill: "transparent" }));

    const back = svg("g", { "clip-path": `url(#${clip})` });
    for (const r of options.regions || []) {
      if (!finite(r.x0) || !finite(r.x1) || r.x1 < x0 || r.x0 > x1) continue;
      const a = sx(Math.max(r.x0, x0));
      const b = sx(Math.min(r.x1, x1));
      back.appendChild(svg("rect", { class: "schart__region", x: a, y: pad.t, width: Math.max(b - a, 0), height: height - pad.t - pad.b, fill: r.color, "fill-opacity": 0.07 },
        r.label ? svg("title", {}, r.label) : null));
      if (r.label && b - a > 46) back.appendChild(svg("text", { class: "schart__region-label", x: a + 4, y: height - pad.b - 5 }, r.label));
    }
    root.appendChild(back);

    const grid = svg("g", { class: "stint-chart__grid" });
    for (const v of niceTicks(yr[0], yr[1], Math.max(3, Math.round((height - pad.t - pad.b) / 45)))) {
      grid.appendChild(svg("line", { x1: pad.l, x2: width - pad.r, y1: sy(v), y2: sy(v) }));
      grid.appendChild(svg("text", { x: pad.l - 6, y: sy(v) + 4, "text-anchor": "end" }, fy(v)));
    }
    const xTarget = Math.max(3, Math.floor((width - pad.l - pad.r) / 70));
    for (const v of (options.integerX ? integerTicks(x0, x1, xTarget) : niceTicks(x0, x1, xTarget))) {
      grid.appendChild(svg("text", { x: sx(v), y: height - pad.b + 16, "text-anchor": "middle" }, fx(v)));
    }
    if (options.xLabel) grid.appendChild(svg("text", { x: width - pad.r, y: height - 4, "text-anchor": "end", class: "stint-chart__axis" }, options.xLabel));
    if (options.yLabel) grid.appendChild(svg("text", { x: 4, y: 4, class: "stint-chart__axis", "dominant-baseline": "hanging" }, options.yLabel));
    root.appendChild(grid);

    const front = svg("g", { "clip-path": `url(#${clip})` });
    for (const h of options.hlines || []) {
      if (!finite(h.y)) continue;
      front.appendChild(svg("line", { class: "schart__hline", x1: pad.l, x2: width - pad.r, y1: sy(h.y), y2: sy(h.y), stroke: h.color || "currentColor", "stroke-dasharray": h.dashed === false ? null : "4 4" },
        h.label ? svg("title", {}, h.label) : null));
      if (h.label) front.appendChild(svg("text", { class: "schart__hline-label", x: width - pad.r - 4, y: sy(h.y) - 4, "text-anchor": "end", fill: h.color || null }, h.label));
    }
    for (const m of options.markers || []) {
      if (!finite(m.x) || !inWin(m.x)) continue;
      front.appendChild(svg("line", { class: "stint-chart__marker", x1: sx(m.x), x2: sx(m.x), y1: pad.t, y2: height - pad.b }));
      if (m.label) front.appendChild(svg("text", { class: "stint-chart__marker-label", x: sx(m.x) + 3, y: pad.t + 10 }, m.label));
    }

    const barSeries = shown.filter((s) => s.kind === "bars");
    series.forEach((s, i) => {
      if (hidden.has(i)) return;
      if (s.kind === "band") {
        const pts = (s.band || []).filter(([x, lo, hi]) => finite(x) && finite(lo) && finite(hi));
        if (pts.length < 2) return;
        const d = `M${pts.map(([x, lo]) => `${sx(x).toFixed(1)},${sy(lo).toFixed(1)}`).join("L")}`
          + `L${pts.slice().reverse().map(([x, , hi]) => `${sx(x).toFixed(1)},${sy(hi).toFixed(1)}`).join("L")}Z`;
        front.appendChild(svg("path", { d, fill: s.color, opacity: s.opacity || 0.16, stroke: "none" }, s.label ? svg("title", {}, s.label) : null));
        return;
      }
      if (s.kind === "line") {
        // gaps (null values) break the line
        const segs = [];
        let cur = [];
        for (const p of s.points || []) {
          if (finite(p[0]) && finite(p[1])) cur.push(p);
          else if (cur.length) { segs.push(cur); cur = []; }
        }
        if (cur.length) segs.push(cur);
        for (const seg of segs) {
          if (seg.length < 2) continue;
          front.appendChild(svg("polyline", {
            points: seg.map(([x, y]) => `${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join(" "),
            fill: "none", stroke: s.color, "stroke-width": s.width || 2, "stroke-dasharray": s.dashed ? "5 4" : null,
            "stroke-linejoin": "round", "stroke-linecap": "round",
          }, s.label ? svg("title", {}, s.label) : null));
        }
        return;
      }
      if (s.kind === "bars") {
        const k = barSeries.indexOf(s);
        const step = xs.length > 1 ? Math.min(...xs.slice(1).map((x, j) => sx(x) - sx(xs[j]))) : 24;
        const bw = Math.max(Math.min(step * 0.8 / barSeries.length, 18), 2);
        const base = sy(clamp(0, yr[0], yr[1]));
        const g = svg("g", { class: "schart__bars" });
        for (const p of s.points || []) {
          if (!finite(p[0]) || !finite(p[1]) || !inWin(p[0])) continue;
          const cx = sx(p[0]) - (bw * barSeries.length) / 2 + k * bw;
          const y = sy(p[1]);
          g.appendChild(svg("rect", { x: cx.toFixed(1), y: Math.min(y, base).toFixed(1), width: bw.toFixed(1), height: Math.abs(base - y).toFixed(1),
            fill: (p[2] && p[2].color) || s.color, "fill-opacity": (p[2] && p[2].faded) ? 0.35 : 0.85 }, p[2] && p[2].title ? svg("title", {}, p[2].title) : null));
        }
        front.appendChild(g);
        return;
      }
      const g = svg("g", { class: "stint-chart__dots" });
      for (const p of s.points || []) {
        if (!finite(p[0]) || !finite(p[1]) || !inWin(p[0])) continue;
        const meta = p[2] || {};
        const dot = svg("circle", {
          cx: sx(p[0]).toFixed(1), cy: sy(p[1]).toFixed(1), r: meta.radius || s.radius || 3.5,
          fill: meta.color || s.color, "fill-opacity": meta.faded ? 0.35 : 1,
          class: s.onClick ? "stint-chart__dot--link" : null,
        }, meta.title ? svg("title", {}, meta.title) : null);
        if (s.onClick) {
          dot.addEventListener("click", (e) => { e.stopPropagation(); s.onClick(meta); });
        }
        g.appendChild(dot);
      }
      front.appendChild(g);
    });
    root.appendChild(front);

    cursorLayer = svg("g", { class: "schart__cursor", "pointer-events": "none" });
    root.appendChild(cursorLayer);
    reset.hidden = !group.state.window;
    drawCursor();
  }

  function drawCursor() {
    if (!cursorLayer || !geom) return;
    while (cursorLayer.firstChild) cursorLayer.removeChild(cursorLayer.firstChild);
    const x = group.state.cursor;
    if (!finite(x) || x < geom.x0 - 1e-9 || x > geom.x1 + 1e-9 || !allXs.includes(x)) {
      tip.hidden = true;
      return;
    }
    const { sx, sy, pad } = geom;
    const px = sx(x);
    cursorLayer.appendChild(svg("line", { class: "schart__crosshair", x1: px, x2: px, y1: pad.t, y2: height - pad.b }));
    const rows = [];
    series.forEach((s, i) => {
      if (hidden.has(i) || s.tooltip === false) return;
      const v = valueAt(s, x);
      if (!v) return;
      const format = s.format || fy;
      if (v.low !== undefined) {
        rows.push({ color: s.color, label: s.label || "", text: `${format(v.low)} – ${format(v.high)}` });
        return;
      }
      if (s.kind !== "bars") cursorLayer.appendChild(svg("circle", { class: "schart__focus", cx: px, cy: sy(v.y), r: 5, stroke: (v.meta && v.meta.color) || s.color }));
      rows.push({ color: (v.meta && v.meta.color) || s.color, label: (v.meta && v.meta.label) || s.label || "", text: format(v.y), note: v.meta && v.meta.note });
    });
    tip.innerHTML = "";
    tip.appendChild(html("div", { class: "schart__tip-head" }, options.tooltipTitle ? options.tooltipTitle(x) : `${options.xLabel || "x"}: ${fx(x)}`));
    for (const r of rows) {
      tip.appendChild(html("div", { class: "schart__tip-row" }, [
        html("span", { class: "schart__tip-swatch", style: `background:${r.color}` }),
        r.label ? html("span", { class: "schart__tip-label" }, r.label) : null,
        html("span", { class: "schart__tip-value" }, r.text),
        r.note ? html("span", { class: "schart__tip-note" }, r.note) : null,
      ]));
    }
    tip.hidden = false;
    // beside the cursor, on the side with more room
    const right = px < width * 0.6;
    tip.style.left = right ? `${px + 12}px` : "";
    tip.style.right = right ? "" : `${width - px + 12}px`;
    tip.style.top = `${pad.t}px`;
  }

  // ------------------------------------------------------------- pointer
  const toX = (event) => {
    const rect = root.getBoundingClientRect();
    const scale = rect.width ? width / rect.width : 1;
    return geom.sx.invert((event.clientX - rect.left) * scale);
  };
  const snap = (x) => (geom.xs.length ? geom.xs[nearestIndex(geom.xs, x)] : null);
  let drag = null;
  let brush = null;

  root.addEventListener("pointermove", (event) => {
    if (!geom) return;
    if (drag && event.pointerType === "mouse" && zoomable) {
      const px = event.clientX;
      if (Math.abs(px - drag.startPx) > DRAG_PX) {
        drag.moved = true;
        const a = geom.sx(clamp(drag.startX, geom.x0, geom.x1));
        const b = geom.sx(clamp(toX(event), geom.x0, geom.x1));
        if (!brush) { brush = svg("rect", { class: "schart__brush", y: geom.pad.t, height: height - geom.pad.t - geom.pad.b }); root.appendChild(brush); }
        brush.setAttribute("x", Math.min(a, b));
        brush.setAttribute("width", Math.abs(b - a));
      }
    }
    group.setCursor(snap(toX(event)), box);
  });
  root.addEventListener("pointerleave", () => { if (!drag) group.setCursor(null, box); });
  root.addEventListener("pointerdown", (event) => {
    if (!geom || event.button > 0) return;
    // A dot that is a link keeps its click: capturing the pointer would send the click to the SVG
    // (real browsers do), and the lap would be pinned instead of opened.
    if (event.target && event.target.classList && event.target.classList.contains("stint-chart__dot--link")) return;
    drag = { startPx: event.clientX, startX: toX(event), moved: false };
    if (root.setPointerCapture && event.pointerId !== undefined) { try { root.setPointerCapture(event.pointerId); } catch { /* jsdom */ } }
  });
  // A finger that scrolls the page (touch-action: pan-y) ends in pointercancel: no drag, no cursor left behind.
  root.addEventListener("pointercancel", () => {
    drag = null;
    if (brush) { brush.remove(); brush = null; }
    group.setCursor(null, box);
  });
  root.addEventListener("pointerup", (event) => {
    if (!drag) return;
    const d = drag;
    drag = null;
    if (brush) { brush.remove(); brush = null; }
    if (d.moved && zoomable) {
      const a = Math.min(d.startX, toX(event));
      const b = Math.max(d.startX, toX(event));
      // only a range with data in it (a drag into a gap between days would show an empty chart)
      if (b - a >= 1 && allXs.some((x) => x >= a && x <= b)) group.setWindow([clamp(a, fullX[0], fullX[1]), clamp(b, fullX[0], fullX[1])], box);
    } else if (event.pointerType !== "mouse" || !d.moved) {
      group.pin(snap(toX(event)));
    }
  });
  root.addEventListener("dblclick", () => { if (zoomable) group.setWindow(null, box); });
  root.addEventListener("wheel", (event) => {
    if (!geom || !zoomable) return;
    const win = group.state.window || fullX;
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      // proportional to the wheel: a trackpad pinch sends many small events, a mouse wheel few big ones
      group.setWindow(zoomWindow(win, clamp(Math.exp(event.deltaY * 0.01), 0.5, 2), toX(event), fullX), box);
    } else if (group.state.window && Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
      event.preventDefault();
      group.setWindow(panWindow(win, (event.deltaX / Math.max(width, 1)) * (win[1] - win[0]), fullX), box);
    }
  }, { passive: false });
  root.addEventListener("keydown", (event) => {
    if (!geom || !geom.xs.length) return;
    const i = finite(group.state.cursor) ? geom.xs.indexOf(group.state.cursor) : -1;
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      const next = i < 0 ? 0 : clamp(i + (event.key === "ArrowRight" ? 1 : -1), 0, geom.xs.length - 1);
      group.pin(null);
      group.setCursor(geom.xs[next], box);
    } else if (event.key === "Escape") {
      group.pin(null);
      group.setWindow(null, box);
    }
  });
  reset.addEventListener("click", () => group.setWindow(null, box));

  // ------------------------------------------------------------- group + size
  // box.dispose() takes a chart out of its group (the page calls it before it re-renders, because
  // the group outlives the charts); a chart that is found detached at the next change leaves too.
  let wasConnected = false;
  let ro = null;
  const sameWindow = (w) => (w === null && drawnWindow === null) || Boolean(w && drawnWindow && w[0] === drawnWindow[0] && w[1] === drawnWindow[1]);
  const unsubscribe = group.subscribe((state) => {
    if (box.isConnected) wasConnected = true;
    else if (wasConnected) { dispose(); return; }
    if (!sameWindow(state.window)) draw();
    else drawCursor();
  });
  function dispose() {
    unsubscribe();
    if (ro) { ro.disconnect(); ro = null; }
  }
  box.dispose = dispose;
  draw();

  if (typeof ResizeObserver !== "undefined") {
    ro = new ResizeObserver((entries) => {
      if (box.isConnected) wasConnected = true;
      const w = Math.round(entries[0].contentRect.width);
      if (w > 120 && Math.abs(w - width) > 2) { width = w; draw(); }
    });
    ro.observe(plot);
  }

  // ------------------------------------------------------------- legend
  if (options.legend) {
    const items = [];
    series.forEach((s, i) => {
      if (!s.label || s.legend === false) return;
      const button = html("button", { type: "button", class: "stint-legend__item stint-legend__toggle", "aria-pressed": hidden.has(i) ? "false" : "true",
        title: t("Show / hide") }, [
        html("span", { class: `stint-legend__swatch${s.dashed ? " stint-legend__swatch--dashed" : ""}${s.kind === "dots" ? " stint-legend__swatch--dot" : ""}${s.kind === "band" ? " stint-legend__swatch--band" : ""}`,
          style: `background:${s.dashed ? "transparent" : s.color};border-color:${s.color}` }),
        s.label,
      ]);
      button.addEventListener("click", () => {
        if (hidden.has(i)) hidden.delete(i); else hidden.add(i);
        button.setAttribute("aria-pressed", hidden.has(i) ? "false" : "true");
        draw();
      });
      items.push(button);
    });
    const extra = legend(options.legendExtra || []);
    for (const node of [...extra.childNodes]) items.push(node);
    if (items.length) box.appendChild(html("div", { class: "stint-legend" }, items));
  }
  return box;
}

// A legend row under a chart (not clickable).
export function legend(items) {
  const box = document.createElement("div");
  box.className = "stint-legend";
  for (const it of items) {
    const item = document.createElement("span");
    item.className = "stint-legend__item";
    const swatch = document.createElement("span");
    swatch.className = `stint-legend__swatch${it.dashed ? " stint-legend__swatch--dashed" : ""}${it.dot ? " stint-legend__swatch--dot" : ""}`;
    swatch.style.background = it.dashed ? "transparent" : it.color;
    swatch.style.borderColor = it.color;
    item.appendChild(swatch);
    item.appendChild(document.createTextNode(it.label));
    box.appendChild(item);
  }
  return box;
}
