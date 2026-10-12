// analysis/charts.js — one canvas chart per channel, all sharing one view state
// (cursor + zoom, see model.js createViewState).
//
// What the user can do on any chart:
//   - move the mouse: the shared cursor follows, every chart / the map / the value
//     table show the exact values at that point
//   - click: pin the cursor there (click the same spot again to release it)
//   - mouse wheel: zoom the distance axis of ALL charts around the pointer
//   - drag: pan; double-click: reset the zoom
//   - touch: drag a finger across a chart to scrub
//
// Drawing is plain Canvas 2D. Every function tolerates a missing 2D context (the
// jsdom test environment has none) by simply not drawing.

import { el } from "../format.js";
import { bindTouchGestures } from "./touch.js";
import { clamp, distanceAt, formatDelta, indexForDistance, LAP_COLORS, niceTicks, valueRange } from "./model.js";

import { t } from "../i18n.js";
const PAD = { left: 58, right: 12, top: 8, bottom: 6 };
const X_AXIS_HEIGHT = 18;
const DRAG_THRESHOLD_PX = 4;

export function cssVar(name, fallback) {
  try {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  } catch {
    return fallback;
  }
}

// Sizes the canvas backing store for the device pixel ratio; returns { ctx, w, h } or null.
export function fitCanvas(canvas, cssHeight) {
  const host = canvas.parentElement;
  const rect = canvas.getBoundingClientRect ? canvas.getBoundingClientRect() : { width: 0 };
  const w = Math.max(Math.floor(rect.width || (host && host.clientWidth) || 300), 50);
  const dpr = (typeof window !== "undefined" && window.devicePixelRatio) || 1;
  const pxW = Math.round(w * dpr);
  const pxH = Math.round(cssHeight * dpr);
  if (canvas.width !== pxW) canvas.width = pxW;
  if (canvas.height !== pxH) canvas.height = pxH;
  canvas.style.height = `${cssHeight}px`;
  const ctx = canvas.getContext ? canvas.getContext("2d") : null;
  if (!ctx) return null;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h: cssHeight };
}

// Calls fn on the next animation frame, at most once however often it is requested.
export function coalesce(fn) {
  let queued = false;
  const raf = typeof requestAnimationFrame === "function" ? requestAnimationFrame : (cb) => setTimeout(cb, 16);
  return () => {
    if (queued) return;
    queued = true;
    raf(() => {
      queued = false;
      fn();
    });
  };
}

// Calls fn whenever the element changes size.
export function onResize(element, fn) {
  if (typeof ResizeObserver !== "undefined") {
    const observer = new ResizeObserver(() => fn());
    observer.observe(element);
    return () => observer.disconnect();
  }
  window.addEventListener("resize", fn);
  return () => window.removeEventListener("resize", fn);
}

// How long the view must stay still before a chart caches its static part (see draw()).
const LAYER_SETTLE_MS = 120;

function formatAxisDistance(metres, span) {
  if (span >= 3000) return `${(metres / 1000).toFixed(1)} km`;
  return `${Math.round(metres)} m`;
}

/**
 * spec: {
 *   key, title, unit,
 *   kind: "line" | "delta",
 *   series: [{ values: number[]|null, color, name, side }],   // side "a" | "b" | undefined
 *   range?: [lo, hi]        // fixed y range, else taken from the data
 *   discrete?: boolean      // draw as steps
 *   height?: number         // css px
 *   showXAxis?: boolean
 *   sectors?: number[]      // distances to mark
 *   format?: (v:number|null) => string
 * }
 */
export function createChart(state, spec) {
  const height = spec.height || 120;
  let data = state.data; // replaced by setData() when finer / coarser data of the same laps arrives
  const format = spec.format || ((v) => (v === null || v === undefined || !Number.isFinite(v) ? "—" : String(v)));

  const legendValues = (spec.series || []).map((s) => {
    const cls = s.side === "a" ? "chart__val chart__val--a" : s.side === "b" ? "chart__val chart__val--b" : "chart__val";
    return el("span", { class: cls, style: `color:${s.color};` }, "—");
  });
  const header = el("div", { class: "chart__head" }, [
    el("span", { class: "chart__title" }, [spec.title, spec.unit ? el("small", {}, ` ${spec.unit}`) : null]),
    el("span", { class: "chart__legend" }, legendValues),
  ]);
  const canvas = el("canvas", { class: "chart__canvas", "aria-label": spec.title });
  const element = el("div", { class: "chart", "data-chart": spec.key }, [header, canvas]);
  const totalHeight = height + (spec.showXAxis ? X_AXIS_HEIGHT : 0);

  // spec.rangeValues (optional): the arrays the y-axis is scaled to — the whole lap's, so a chart
  // made or refreshed while zoomed in has the same axis as the overview.
  const rangeSource = spec.rangeValues || spec.series.map((s) => s.values);
  const yRange = spec.kind === "delta"
    ? deltaRange(rangeSource[0])
    : valueRange(rangeSource, spec.range);

  let geometry = null; // set on every draw: what pointer handlers need
  let layer = null;     // offscreen canvas with grid, axes, sectors and series (see draw)
  let layerKey = "";     // what the layer shows
  let lastKey = "";      // what the canvas showed after the previous draw
  let layerTimer = null;
  let dataVersion = 0;  // bumped by setData so the cached layer is redrawn

  function toX(distance, view, x0, x1) {
    return x0 + ((distance - view[0]) / (view[1] - view[0])) * (x1 - x0);
  }

  function draw() {
    const fit = fitCanvas(canvas, totalHeight);
    if (!fit) return;
    const { ctx, w } = fit;
    const view = state.getView();
    const x0 = PAD.left;
    const x1 = w - PAD.right;
    const y0 = PAD.top;
    const y1 = height - PAD.bottom;
    const [lo, hi] = yRange;
    const toY = (v) => y1 - ((v - lo) / (hi - lo)) * (y1 - y0);
    geometry = { x0, x1, view };

    const hairline = cssVar("--hairline", "#2C3136");
    const muted = cssVar("--text-muted", "#8B9199");
    const text = cssVar("--text", "#ECEDEE");

    // Everything but the cursor changes only with the view, the data or the size: it is drawn
    // into a cached layer, and a cursor move just copies that layer and draws the cursor on top
    // (before V0.9.1 every cursor move redrew every line of every chart).
    const drawStatic = (ctx) => {
      ctx.clearRect(0, 0, w, totalHeight);
      ctx.font = "11px 'IBM Plex Mono', monospace";
      ctx.textBaseline = "middle";

      // horizontal grid + y labels
      ctx.lineWidth = 1;
      ctx.strokeStyle = hairline;
      ctx.fillStyle = muted;
      ctx.textAlign = "right";
      const yTicks = spec.discrete && hi - lo <= 12
        ? niceTicks(lo, hi, Math.max(2, Math.floor(hi - lo))).filter(Number.isInteger)
        : niceTicks(lo, hi, Math.max(2, Math.floor((y1 - y0) / 34)));
      for (const tick of yTicks) {
        const y = Math.round(toY(tick)) + 0.5;
        ctx.beginPath();
        ctx.moveTo(x0, y);
        ctx.lineTo(x1, y);
        ctx.stroke();
        ctx.fillText(tickLabel(tick, hi - lo), x0 - 6, y);
      }

      // vertical grid (+ labels on the bottom chart)
      const xTicks = niceTicks(view[0], view[1], Math.max(2, Math.floor((x1 - x0) / 90)));
      ctx.textAlign = "center";
      for (const tick of xTicks) {
        const x = Math.round(toX(tick, view, x0, x1)) + 0.5;
        ctx.strokeStyle = hairline;
        ctx.beginPath();
        ctx.moveTo(x, y0);
        ctx.lineTo(x, y1);
        ctx.stroke();
        if (spec.showXAxis) {
          ctx.fillStyle = muted;
          ctx.fillText(formatAxisDistance(tick, view[1] - view[0]), x, height + X_AXIS_HEIGHT / 2);
        }
      }

      // zero line for the delta chart + coloured area
      if (spec.kind === "delta") {
        const zeroY = toY(0);
        drawDeltaArea(ctx, spec.series[0].values, data.grid, view, toX, toY, x0, x1, zeroY);
        ctx.strokeStyle = muted;
        ctx.beginPath();
        ctx.moveTo(x0, Math.round(zeroY) + 0.5);
        ctx.lineTo(x1, Math.round(zeroY) + 0.5);
        ctx.stroke();
      }

      // sector boundaries
      if (spec.sectors && spec.sectors.length) {
        ctx.save();
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = muted;
        for (const d of spec.sectors) {
          if (d < view[0] || d > view[1]) continue;
          const x = Math.round(toX(d, view, x0, x1)) + 0.5;
          ctx.beginPath();
          ctx.moveTo(x, y0);
          ctx.lineTo(x, y1);
          ctx.stroke();
        }
        ctx.restore();
      }

      // series
      const i0 = Math.max(indexForDistance(data.grid, view[0]) - 1, 0);
      const i1 = Math.min(indexForDistance(data.grid, view[1]) + 1, data.grid.count - 1);
      ctx.save();
      ctx.beginPath();
      ctx.rect(x0, y0 - 1, x1 - x0, y1 - y0 + 2);
      ctx.clip();
      for (const series of spec.series) {
        if (!series.values) continue;
        ctx.strokeStyle = series.color;
        ctx.lineWidth = spec.kind === "delta" ? 1.6 : 1.5;
        ctx.lineJoin = "round";
        // V0.9.4: a line style per lap (Lap 3 dashed, Lap 4 dotted)
        if (ctx.setLineDash) ctx.setLineDash(series.dash || []);
        let penDown = false;
        let lastY = 0;
        ctx.beginPath();
        for (let i = i0; i <= i1; i++) {
          const v = series.values[i];
          if (v === null || v === undefined || !Number.isFinite(v)) {
            penDown = false;
            continue;
          }
          const x = toX(distanceAt(data.grid, i), view, x0, x1);
          const y = toY(v);
          if (!penDown) {
            ctx.moveTo(x, y);
            penDown = true;
          } else if (spec.discrete) {
            ctx.lineTo(x, lastY);
            ctx.lineTo(x, y);
          } else {
            ctx.lineTo(x, y);
          }
          lastY = y;
        }
        ctx.stroke();
        // V0.9.1: raw extremes between grid points (brake taps, kerbs) as thin vertical strokes
        const sp = series.spikes;
        if (sp && sp.i && sp.i.length) {
          ctx.lineWidth = 1.2;
          ctx.beginPath();
          for (let k = 0; k < sp.i.length; k++) {
            const i = sp.i[k];
            if (i < i0 || i > i1 || sp.lo[k] === null || sp.hi[k] === null) continue;
            const x = toX(distanceAt(data.grid, i), view, x0, x1);
            ctx.moveTo(x, toY(sp.lo[k]));
            ctx.lineTo(x, toY(sp.hi[k]));
          }
          ctx.stroke();
        }
      }
      if (ctx.setLineDash) ctx.setLineDash([]);
      ctx.restore();
    };
    const dpr = (typeof window !== "undefined" && window.devicePixelRatio) || 1;
    const key = `${canvas.width}x${canvas.height}|${view[0]}|${view[1]}|${dataVersion}|${lo}|${hi}`;
    const buildLayer = () => {
      if (!layer) layer = document.createElement("canvas");
      // resizing a canvas reallocates and clears it: only when the size really changed
      if (layer.width !== canvas.width) layer.width = canvas.width;
      if (layer.height !== canvas.height) layer.height = canvas.height;
      const lc = layer.getContext("2d");
      if (!lc) return false;
      lc.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawStatic(lc);
      layerKey = key;
      return true;
    };
    if (layerTimer) {
      clearTimeout(layerTimer);
      layerTimer = null;
    }
    if (layerKey === key || (key === lastKey && buildLayer())) {
      // only the cursor moved: copy the cached layer
      ctx.clearRect(0, 0, w, totalHeight);
      ctx.drawImage(layer, 0, 0, layer.width / dpr, layer.height / dpr);
    } else {
      // The view, the data or the size changed (zoom, pan, resize): draw straight onto the
      // canvas — copying a layer costs about as much as drawing, so building one for every
      // frame of a gesture would double the work. Once things settle, the layer is built in
      // the background, so the next cursor move is already cheap.
      drawStatic(ctx);
      layerTimer = setTimeout(() => {
        layerTimer = null;
        if (lastKey === key && layerKey !== key) buildLayer();
      }, LAYER_SETTLE_MS);
    }
    lastKey = key;

    // cursor line + a dot on every series
    const cursor = state.getCursor();
    const cursorDistance = distanceAt(data.grid, cursor);
    if (cursorDistance >= view[0] && cursorDistance <= view[1]) {
      const cx = Math.round(toX(cursorDistance, view, x0, x1)) + 0.5;
      ctx.save();
      ctx.strokeStyle = text;
      ctx.lineWidth = 1;
      if (!state.isPinned()) ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(cx, y0);
      ctx.lineTo(cx, y1);
      ctx.stroke();
      ctx.restore();
      for (const series of spec.series) {
        const v = series.values ? series.values[cursor] : null;
        if (v === null || v === undefined || !Number.isFinite(v)) continue;
        ctx.fillStyle = series.color;
        ctx.beginPath();
        ctx.arc(cx, toY(v), 3.5, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  function updateLegend() {
    const cursor = state.getCursor();
    spec.series.forEach((series, k) => {
      const v = series.values ? series.values[cursor] : null;
      legendValues[k].textContent = series.values ? format(v) : t("no data");
    });
  }

  const schedule = coalesce(() => {
    draw();
  });

  const unsubscribe = state.subscribe((what) => {
    if (what === "cursor") updateLegend();
    schedule();
  });
  updateLegend();

  // ------------------------------------------------ pointer interaction
  function indexAtClientX(clientX) {
    const rect = canvas.getBoundingClientRect();
    const g = geometry || { x0: PAD.left, x1: rect.width - PAD.right, view: state.getView() };
    const fraction = clamp((clientX - rect.left - g.x0) / Math.max(g.x1 - g.x0, 1), 0, 1);
    return indexForDistance(data.grid, g.view[0] + fraction * (g.view[1] - g.view[0]));
  }

  let drag = null;
  canvas.addEventListener("mousedown", (event) => {
    if (event.button !== 0) return;
    drag = { startX: event.clientX, lastX: event.clientX, moved: false };
  });
  const onMouseUp = () => {
    if (!element.isConnected) {
      // The page was replaced (the router re-renders by clearing #app): a window
      // listener would otherwise live on, one more per chart per visit.
      window.removeEventListener("mouseup", onMouseUp);
      return;
    }
    if (!drag) return;
    const wasDrag = drag.moved;
    drag = null;
    if (wasDrag) canvas.dataset.justDragged = "1";
  };
  window.addEventListener("mouseup", onMouseUp);
  canvas.addEventListener("mousemove", (event) => {
    if (drag) {
      if (Math.abs(event.clientX - drag.startX) > DRAG_THRESHOLD_PX) drag.moved = true;
      if (drag.moved && geometry) {
        const view = state.getView();
        const perPixel = (view[1] - view[0]) / Math.max(geometry.x1 - geometry.x0, 1);
        state.pan(-(event.clientX - drag.lastX) * perPixel);
      }
      drag.lastX = event.clientX;
      return;
    }
    state.hoverCursor(indexAtClientX(event.clientX));
  });
  canvas.addEventListener("click", (event) => {
    if (canvas.dataset.justDragged) {
      delete canvas.dataset.justDragged;
      return;
    }
    state.pinCursor(indexAtClientX(event.clientX));
  });
  canvas.addEventListener("dblclick", () => state.resetView());
  canvas.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      const index = indexAtClientX(event.clientX);
      state.zoom(event.deltaY < 0 ? 0.8 : 1.25, distanceAt(data.grid, index));
    },
    { passive: false }
  );
  // Touch (V0.9.1): tap = cursor, sideways drag = scrub, up/down = the page scrolls,
  // hold = pin, two fingers = pinch zoom + pan. See analysis/touch.js.
  bindTouchGestures(canvas, {
    tap: (x) => state.setCursor(indexAtClientX(x)),
    doubleTap: () => state.resetView(),
    scrub: (x) => state.setCursor(indexAtClientX(x)),
    longPress: (x) => state.pinCursor(indexAtClientX(x)),
    pinch: ({ factor, centerX }) => state.zoom(factor, distanceAt(data.grid, indexAtClientX(centerX))),
    pan: (dx) => {
      const view = state.getView();
      const perPixel = (view[1] - view[0]) / Math.max((geometry ? geometry.x1 - geometry.x0 : 300), 1);
      state.pan(-dx * perPixel);
    },
  });

  const stopResize = onResize(element, schedule);
  schedule();

  return {
    element,
    redraw: draw,
    // New values for the same series on a different grid (zoom refinement), WITHOUT rebuilding
    // the chart: the canvas, its listeners and a drag in progress stay, and the y-axis keeps the
    // range it got from the whole lap, so it does not jump when the stretch is swapped in.
    setData(nextData, seriesValues, seriesSpikes = []) {
      data = nextData;
      dataVersion += 1;
      spec.series.forEach((series, k) => {
        series.values = seriesValues[k];
        series.spikes = seriesSpikes[k] || null;
      });
      schedule();
    },
    destroy() {
      unsubscribe();
      stopResize();
      window.removeEventListener("mouseup", onMouseUp);
      if (layerTimer) clearTimeout(layerTimer);
      layer = null;
    },
    // exposed for tests
    _indexAtClientX: indexAtClientX,
    _canvas: canvas,
  };
}

function tickLabel(value, span) {
  if (Math.abs(value) >= 1000) return value.toFixed(0);
  if (span >= 8) return String(Math.round(value * 10) / 10);
  if (span >= 0.8) return value.toFixed(1);
  return value.toFixed(2);
}

// Symmetric around zero so "A ahead" and "A behind" are visually comparable.
function deltaRange(values) {
  let m = 0.05;
  if (values) {
    for (const v of values) if (v !== null && Number.isFinite(v) && Math.abs(v) > m) m = Math.abs(v);
  }
  m *= 1.15;
  return [-m, m];
}

function drawDeltaArea(ctx, values, grid, view, toX, toY, x0, x1, zeroY) {
  if (!values) return;
  const i0 = Math.max(indexForDistance(grid, view[0]) - 1, 0);
  const i1 = Math.min(indexForDistance(grid, view[1]) + 1, grid.count - 1);
  ctx.save();
  ctx.beginPath();
  ctx.rect(x0, 0, x1 - x0, zeroY * 2 + 1000);
  ctx.clip();
  for (const [positive, color] of [[true, "rgba(224,71,43,0.28)"], [false, "rgba(76,190,120,0.28)"]]) {
    ctx.fillStyle = color;
    ctx.beginPath();
    let open = false;
    let lastX = 0;
    for (let i = i0; i <= i1; i++) {
      const v = values[i];
      if (v === null || !Number.isFinite(v)) continue;
      const x = toX(distanceAt(grid, i), view, x0, x1);
      const clamped = positive ? Math.max(v, 0) : Math.min(v, 0);
      if (!open) {
        ctx.moveTo(x, zeroY);
        open = true;
      }
      ctx.lineTo(x, toY(clamped));
      lastX = x;
    }
    if (open) {
      ctx.lineTo(lastX, zeroY);
      ctx.closePath();
      ctx.fill();
    }
  }
  ctx.restore();
}

export { formatDelta, LAP_COLORS };
