// analysis/linemap.js — the racing-line comparison (V0.9.4, "Linienvergleich"): the driven lines
// of Lap 1 and Lap 2 (and Lap 3 / 4) drawn equally on a track map that can be zoomed down to
// a single corner, with what each lap did there.
//
//   * zoom: mouse wheel around the pointer, buttons, two-finger pinch (around the pinch centre,
//     in both directions); pan: drag with the mouse or one finger; double-click / "Whole lap"
//   * both lines in the same width — the comparison is the point, neither lap is "the track"
//   * a band of the car's width around Lap 1 (2 m; drawn when zoomed in far enough to mean
//     something) and a scale bar
//   * per lap and corner: braking point ▲, turn-in ◆ (estimated from the steering), apex ●,
//     full throttle ▼ — in the lap's colour, when zoomed in
//   * the corner chips zoom to a corner (and select it for the corner window)
//   * "Follow": the map keeps the cursor in the middle, or shows what the charts show
//   * zoomed in to less than ~600 m of track, the lines are loaded again in high resolution
//     (GET /telemetry/analysis/line, 0.5 m) — mirror decision and corner directions still come
//     from the whole laps, so the detail lies exactly on the overview
//   * at the cursor: "Lap 2 1.4 m further inside" — Lap 2's lateral distance from Lap 1's line,
//     inside = towards the inside of the nearest corner (server/telemetry_analysis.py)
//
// The lines' positions come from LMU's world coordinates (accurate to centimetres in the game).
// V0.9.5: behind them the TRACK — asphalt between both edges, the kerbs red/white — rebuilt on
// the server from LMU's path_lateral / track_edge / surface type of several laps
// (server/track_surface.py; approximate, an edge no lap ever drove along is dashed = estimated),
// and a faint band of the car's width around EVERY lap.

import { el } from "../format.js";
import { cssVar, onResize } from "./charts.js";
import { clamp, distanceAt, formatMetres, indexForDistance, lapColor, lapName, LAP_DASH } from "./model.js";

import { t } from "../i18n.js";

export const CAR_WIDTH_M = 2.0;
export const DETAIL_SPAN_M = 600;
const HEIGHT = 440;
const MIN_SCALE_FACTOR = 0.6;   // relative to "whole lap fits"
const MAX_PX_PER_M = 60;
const MARKER_SPAN_M = 2500;     // markers only when less than this much track is in view
const SIDES = ["a", "b", "c", "d"];

// ------------------------------------------------------------------ pure helpers (tested)

const v = (a, i) => (a && a[i] !== null && a[i] !== undefined ? a[i] : null);

// The server's surface (centre path + normals + widths per 4 m) -> what is drawn, in world
// coordinates: { left: [[x,y]|null…], right: […], leftEst: [bool], rightEst: [bool],
// kerbs: [{ side, i, quad: [[x,y]×4] }] } — a kerb quad spans bin i to i+1.
export function surfaceGeometry(surface) {
  if (!surface || !Array.isArray(surface.cx)) return null;
  const n = surface.count;
  const at = (i, offset) => {
    const cx = v(surface.cx, i); const cy = v(surface.cy, i); const nx = v(surface.nx, i); const ny = v(surface.ny, i);
    if (cx === null || cy === null || nx === null || ny === null || offset === null) return null;
    return [cx + nx * offset, cy + ny * offset];
  };
  const left = []; const right = [];
  for (let i = 0; i < n; i++) {
    const wl = v(surface.wl, i); const wr = v(surface.wr, i);
    left.push(wl === null ? null : at(i, wl));
    right.push(wr === null ? null : at(i, -wr));
  }
  const kerbs = [];
  for (const [side, inK, outK, sign] of [["l", "kl_in", "kl_out", 1], ["r", "kr_in", "kr_out", -1]]) {
    for (let i = 0; i + 1 < n; i++) {
      const a0 = v(surface[inK], i); const a1 = v(surface[outK], i);
      const b0 = v(surface[inK], i + 1); const b1 = v(surface[outK], i + 1);
      if (a0 === null || a1 === null) continue;
      // a single bin of kerb still gets drawn: the next bin takes the same offsets
      const p = [at(i, sign * a0), at(i, sign * a1), at(i + 1, sign * (b1 ?? a1)), at(i + 1, sign * (b0 ?? a0))];
      if (p.every(Boolean)) kerbs.push({ side, i, quad: p });
    }
  }
  return { left, right, leftEst: (surface.wl_est || []).map(Boolean), rightEst: (surface.wr_est || []).map(Boolean), kerbs };
}

// A "nice" scale-bar length for `pxPerM` so the bar is 60..160 px long.
export function scaleBar(pxPerM, minPx = 60, maxPx = 160) {
  const steps = [0.5, 1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000];
  for (const m of steps) if (m * pxPerM >= minPx && m * pxPerM <= maxPx) return { metres: m, px: m * pxPerM };
  const m = steps.reduce((best, s) => (Math.abs(s * pxPerM - (minPx + maxPx) / 2) < Math.abs(best * pxPerM - (minPx + maxPx) / 2) ? s : best), steps[0]);
  return { metres: m, px: m * pxPerM };
}

// The camera: world (x, y up) <-> screen (px, y down). Zoom keeps the world point under `at`.
export function createCamera(width, height) {
  const cam = { cx: 0, cy: 0, scale: 1, width, height };
  cam.toX = (x) => cam.width / 2 + (x - cam.cx) * cam.scale;
  cam.toY = (y) => cam.height / 2 - (y - cam.cy) * cam.scale;
  cam.fromX = (px) => cam.cx + (px - cam.width / 2) / cam.scale;
  cam.fromY = (py) => cam.cy - (py - cam.height / 2) / cam.scale;
  cam.fit = (b, pad = 24) => {
    const sx = Math.max(b.maxX - b.minX, 1e-3);
    const sy = Math.max(b.maxY - b.minY, 1e-3);
    cam.scale = Math.min((cam.width - 2 * pad) / sx, (cam.height - 2 * pad) / sy);
    cam.cx = (b.minX + b.maxX) / 2;
    cam.cy = (b.minY + b.maxY) / 2;
  };
  cam.zoomAt = (factor, px, py, minScale, maxScale) => {
    const wx = cam.fromX(px);
    const wy = cam.fromY(py);
    cam.scale = clamp(cam.scale * factor, minScale, maxScale);
    cam.cx = wx - (px - cam.width / 2) / cam.scale;
    cam.cy = wy + (py - cam.height / 2) / cam.scale;
  };
  cam.panBy = (dxPx, dyPx) => {
    cam.cx -= dxPx / cam.scale;
    cam.cy += dyPx / cam.scale;
  };
  return cam;
}

export function boundsOf(xs, ys, lo = 0, hi = xs ? xs.length - 1 : -1) {
  let minX = Infinity; let maxX = -Infinity; let minY = Infinity; let maxY = -Infinity;
  for (let i = Math.max(lo, 0); i <= hi && xs && i < xs.length; i++) {
    const x = xs[i]; const y = ys[i];
    if (x === null || y === null || x === undefined || !Number.isFinite(x)) continue;
    if (x < minX) minX = x; if (x > maxX) maxX = x;
    if (y < minY) minY = y; if (y > maxY) maxY = y;
  }
  return Number.isFinite(minX) ? { minX, maxX, minY, maxY } : null;
}

// The sentence at the cursor: Lap 2's line relative to Lap 1's (offset > 0 = further inside).
export function offsetText(offset, side) {
  if (offset === null || offset === undefined || !Number.isFinite(offset)) return null;
  const metres = Math.abs(offset);
  if (metres < 0.1) return t("Lap 2 on the same line as Lap 1");
  const corner = side > 0 ? t("(left-hand corner)") : side < 0 ? t("(right-hand corner)") : "";
  return offset > 0
    ? t("Lap 2 {0} m further inside {1}", [metres.toFixed(1), corner]).trim()
    : t("Lap 2 {0} m further outside {1}", [metres.toFixed(1), corner]).trim();
}

// The stretch of track (metres) a corner covers for zooming: braking of either lap .. full
// throttle of either lap, with some run-up.
export function cornerStretch(c) {
  const num = (v) => v !== null && v !== undefined && Number.isFinite(v);
  const first = Math.min(...[c.a && c.a.brake, c.b && c.b.brake, c.entry ?? c.start].filter(num));
  const last = Math.max(...[c.a && c.a.throttle, c.b && c.b.throttle, c.apex + 120].filter(num));
  return [first - 50, Math.min(last + 50, c.end + 50)];
}

// ------------------------------------------------------------------ the window

/**
 * state      the map state (data = the whole-lap overview, cursor in its indices)
 * opts       { fetchLine({start, end, step}) => Promise, selection, viewState }
 *   viewState  the charts' state (for "follow the charts")
 *   selection  shared corner selection { get(), set(n), subscribe(fn) }
 */
export function createLineMap(state, opts = {}) {
  const boxHeight = Number.isFinite(opts.height) ? opts.height : HEIGHT; // V0.9.5: fills its tile
  const data = state.data;
  const map = data.map || {};
  const sides = SIDES.filter((s) => map[`x_${s}`]);
  if (!sides.length) {
    return { element: el("div", { class: "empty-state" }, t("No position data in these laps — the track map needs laps recorded with the V0.9 client.")), destroy() {} };
  }
  const hasB = sides.includes("b");
  const grid = data.grid;
  const xs = (s) => map[`x_${s}`];
  const ys = (s) => map[`y_${s}`];
  const corners = data.corners || [];

  const canvas = el("canvas", { class: "linemap__canvas", "aria-label": t("Racing lines"), tabindex: "0" });
  const info = el("div", { class: "linemap__info", role: "status" });
  const follow = el("select", { class: "linemap__follow", "aria-label": t("Follow") }, [
    el("option", { value: "off" }, t("Free")),
    el("option", { value: "cursor" }, t("Follow the cursor")),
    el("option", { value: "charts" }, t("Show what the charts show")),
  ]);
  const markersBox = el("input", { type: "checkbox", checked: "checked" });
  const bandBox = el("input", { type: "checkbox", checked: "checked" });
  const trackBox = el("input", { type: "checkbox", checked: "checked" });
  let surface = null;   // V0.9.5: surfaceGeometry() of the server's track surface, once loaded
  const btn = (label, title, fn) => {
    const b = el("button", { type: "button", class: "btn btn--ghost linemap__btn", title }, label);
    b.addEventListener("click", fn);
    return b;
  };
  const fromInput = el("input", { type: "number", class: "linemap__range", min: "0", step: "10", placeholder: t("from m"), "aria-label": t("From (m)") });
  const toInput = el("input", { type: "number", class: "linemap__range", min: "0", step: "10", placeholder: t("to m"), "aria-label": t("To (m)") });
  const chips = el("div", { class: "linemap__corners" }, corners.map((c) => {
    const chip = el("button", { type: "button", class: "linemap__chip", "data-corner": String(c.n), title: t("Zoom into corner {0}", [c.n]) }, `${t("C{0}", [c.n])}${c.delta !== undefined && c.delta !== null && hasB ? ` ${c.delta > 0 ? "+" : ""}${c.delta.toFixed(2)}` : ""}`);
    chip.addEventListener("click", () => zoomToCorner(c));
    return chip;
  }));
  const legend = el("div", { class: "linemap__legend" }, sides.map((s) => el("span", { class: "linemap__legend-item" }, [
    el("span", { class: `linemap__swatch linemap__swatch--${s}`, style: `border-color:${lapColor(s)};` }), lapName(s),
  ])));
  const element = el("div", { class: "linemap" }, [
    el("div", { class: "linemap__controls" }, [
      btn("+", t("Zoom in"), () => zoomCenter(1.6)),
      btn("−", t("Zoom out"), () => zoomCenter(1 / 1.6)),
      btn(t("Whole lap"), t("Show the whole lap"), () => { camFit(); followSet("off"); }),
      el("label", { class: "linemap__opt" }, [follow]),
      el("label", { class: "linemap__opt", title: t("Braking point ▲, turn-in ◆ (estimated), apex ●, full throttle ▼") }, [markersBox, ` ${t("Markers")}`]),
      el("label", { class: "linemap__opt", title: t("A faint band of {0} m (about a car's width) around every lap", [CAR_WIDTH_M]) }, [bandBox, ` ${t("Car width")}`]),
      el("label", { class: "linemap__opt", title: t("Asphalt, edges and kerbs, rebuilt from LMU's track data of several laps (approximate)") }, [trackBox, ` ${t("Track")}`]),
      el("span", { class: "linemap__opt" }, [t("Stretch"), " ", fromInput, " – ", toInput, " m"]),
    ]),
    corners.length ? chips : null,
    canvas,
    el("div", { class: "linemap__foot" }, [legend, info]),
    el("div", { class: "subtitle linemap__caption" }, t("Lines: where the cars drove, from LMU's positions. Track: edges and kerbs rebuilt from LMU's track data of several laps — approximate; a dashed edge is estimated. ▲ braking point · ◆ turn-in (estimated from the steering) · ● apex · ▼ full throttle. Inside = towards the inside of the nearest corner.")),
  ]);

  let width = 0;
  let cam = null;
  let homeScale = 1;
  let detail = null;          // { start, end, step, response }
  let detailTimer = null;
  let detailSeq = 0;
  let selected = opts.selection ? opts.selection.get() : null;
  const overall = (() => {
    let b = null;
    for (const s of sides) {
      const bb = boundsOf(xs(s), ys(s));
      if (!bb) continue;
      b = b ? { minX: Math.min(b.minX, bb.minX), maxX: Math.max(b.maxX, bb.maxX), minY: Math.min(b.minY, bb.minY), maxY: Math.max(b.maxY, bb.maxY) } : bb;
    }
    return b;
  })();

  function ensureCamera() {
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(Math.round(rect.width || canvas.clientWidth || 600), 200);
    if (cam && w === width) return true;
    const dpr = (typeof window !== "undefined" && window.devicePixelRatio) || 1;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(boxHeight * dpr);
    canvas.style.height = `${boxHeight}px`;
    if (!cam) {
      cam = createCamera(w, boxHeight);
      if (overall) cam.fit(overall);
      homeScale = cam.scale;
    } else {
      cam.width = w;
    }
    width = w;
    return true;
  }
  const minScale = () => homeScale * MIN_SCALE_FACTOR;
  function camFit() {
    if (!overall || !ensureCamera()) return;
    cam.fit(overall);
    schedule();
  }
  function zoomCenter(f) {
    if (!ensureCamera()) return;
    cam.zoomAt(f, cam.width / 2, cam.height / 2, minScale(), MAX_PX_PER_M);
    afterMove();
  }
  function fitStretch(lo, hi) {
    if (!ensureCamera()) return;
    let b = null;
    for (const s of sides) {
      const bb = boundsOf(xs(s), ys(s), indexForDistance(grid, lo), indexForDistance(grid, hi));
      if (!bb) continue;
      b = b ? { minX: Math.min(b.minX, bb.minX), maxX: Math.max(b.maxX, bb.maxX), minY: Math.min(b.minY, bb.minY), maxY: Math.max(b.maxY, bb.maxY) } : bb;
    }
    if (!b) return;
    cam.fit(b, 30);
    cam.scale = clamp(cam.scale, minScale(), MAX_PX_PER_M);
    afterMove();
  }
  function zoomToCorner(c) {
    const [lo, hi] = cornerStretch(c);
    followSet("off");
    fitStretch(lo, hi);
    if (opts.selection) opts.selection.set(c.n);
    if (opts.viewState) opts.viewState.setView(lo, hi);
    state.pinCursor(indexForDistance(grid, c.apex));
  }
  function followSet(value) { follow.value = value; }

  // ---------------------------------------------------------------- detail lines
  // What stretch of Lap 1 is on screen (metres), or null when most of the lap is.
  function visibleStretch() {
    const x = xs(sides[0]);
    const y = ys(sides[0]);
    let lo = Infinity; let hi = -Infinity;
    for (let i = 0; i < x.length; i++) {
      if (x[i] === null) continue;
      const px = cam.toX(x[i]); const py = cam.toY(y[i]);
      if (px < -20 || py < -20 || px > cam.width + 20 || py > cam.height + 20) continue;
      const d = distanceAt(grid, i);
      if (d < lo) lo = d; if (d > hi) hi = d;
    }
    return Number.isFinite(lo) ? [lo, hi] : null;
  }
  function planDetail() {
    if (!opts.fetchLine || !cam) return;
    const stretch = visibleStretch();
    if (!stretch || stretch[1] - stretch[0] > DETAIL_SPAN_M || cam.scale < 1.5) {
      if (detail) { detail = null; detailVersion += 1; schedule(); }
      return;
    }
    if (detail && detail.start <= stretch[0] && detail.end >= stretch[1]) return;
    if (detailTimer) clearTimeout(detailTimer);
    detailTimer = setTimeout(async () => {
      detailTimer = null;
      const seq = ++detailSeq;
      const margin = 80;
      const start = Math.max(Math.floor(stretch[0] - margin), Math.floor(grid.start));
      const end = Math.ceil(stretch[1] + margin);
      try {
        const response = await opts.fetchLine({ start, end, step: 0.5 });
        if (seq !== detailSeq || !response || !response.grid) return;
        detail = { start: response.grid.start, end: response.grid.start + response.grid.step * (response.grid.count - 1), response };
        detailVersion += 1;
        schedule();
      } catch {
        // the overview stays — less detailed, not broken
      }
    }, 250);
  }

  // ---------------------------------------------------------------- drawing
  let frame = null;
  function schedule() {
    if (frame !== null) return;
    const raf = typeof requestAnimationFrame === "function" ? requestAnimationFrame : (fn) => setTimeout(fn, 16);
    frame = raf(() => { frame = null; draw(); });
  }
  function afterMove() { schedule(); planDetail(); }

  function strokeLine(ctx, x, y, from = 0, to = x.length - 1) {
    ctx.beginPath();
    let pen = false;
    for (let i = from; i <= to; i++) {
      if (x[i] === null || y[i] === null || x[i] === undefined) { pen = false; continue; }
      const px = cam.toX(x[i]); const py = cam.toY(y[i]);
      if (!pen) { ctx.moveTo(px, py); pen = true; } else ctx.lineTo(px, py);
    }
    ctx.stroke();
  }

  function markerShape(ctx, kind, px, py, color) {
    const r = 5;
    ctx.fillStyle = color;
    ctx.strokeStyle = cssVar("--asphalt", "#14171A");
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    if (kind === "brake") { ctx.moveTo(px, py - r); ctx.lineTo(px + r, py + r * 0.8); ctx.lineTo(px - r, py + r * 0.8); }
    else if (kind === "turn_in") { ctx.moveTo(px, py - r); ctx.lineTo(px + r, py); ctx.lineTo(px, py + r); ctx.lineTo(px - r, py); }
    else if (kind === "throttle") { ctx.moveTo(px, py + r); ctx.lineTo(px + r, py - r * 0.8); ctx.lineTo(px - r, py - r * 0.8); }
    else ctx.arc(px, py, r * 0.8, 0, Math.PI * 2);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  }

  // Everything but the cursor dots is drawn into a cached layer; a cursor move only copies it
  // (the map must stay smooth while the mouse sweeps a chart — like the charts themselves).
  let layer = null;
  let layerKey = "";
  let detailVersion = 0;
  function draw() {
    if (!ensureCamera()) return;
    const ctx = canvas.getContext ? canvas.getContext("2d") : null;
    if (!ctx) return;
    const dpr = (typeof window !== "undefined" && window.devicePixelRatio) || 1;
    const key = `${cam.cx}|${cam.cy}|${cam.scale}|${cam.width}|${detailVersion}|${markersBox.checked}|${bandBox.checked}|${trackBox.checked}|${surface ? 1 : 0}|${selected}`;
    if (key !== layerKey || !layer) {
      if (!layer) layer = document.createElement("canvas");
      layer.width = canvas.width;
      layer.height = canvas.height;
      const lc = layer.getContext ? layer.getContext("2d") : null;
      if (!lc) return;
      lc.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawStatic(lc);
      layerKey = key;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cam.width, cam.height);
    ctx.drawImage(layer, 0, 0, layer.width / dpr, layer.height / dpr);
    drawCursor(ctx);
  }

  function polyPath(ctx, points, from, to) {
    let pen = false;
    for (let i = from; i <= to; i++) {
      const p = points[i];
      if (!p) { pen = false; continue; }
      const px = cam.toX(p[0]); const py = cam.toY(p[1]);
      if (!pen) { ctx.moveTo(px, py); pen = true; } else ctx.lineTo(px, py);
    }
  }
  function drawSurface(ctx) {
    const { left, right, leftEst, rightEst, kerbs } = surface;
    // grass / run-off everywhere, the asphalt on top
    ctx.fillStyle = "#1b261d";
    ctx.fillRect(0, 0, cam.width, cam.height);
    ctx.fillStyle = "#33383d";
    ctx.beginPath();
    for (let i = 0; i + 1 < left.length; i++) {
      const a = left[i]; const b = left[i + 1]; const c = right[i + 1]; const d = right[i];
      if (!a || !b || !c || !d) continue;
      ctx.moveTo(cam.toX(a[0]), cam.toY(a[1]));
      ctx.lineTo(cam.toX(b[0]), cam.toY(b[1]));
      ctx.lineTo(cam.toX(c[0]), cam.toY(c[1]));
      ctx.lineTo(cam.toX(d[0]), cam.toY(d[1]));
      ctx.closePath();
    }
    ctx.fill();
    // kerbs: red / white every 4 m
    if (cam.scale > 0.25) {
      for (const [colour, parity] of [["#c8372d", 0], ["#e6e6e6", 1]]) {
        ctx.fillStyle = colour;
        ctx.beginPath();
        for (const k of kerbs) {
          if (k.i % 2 !== parity) continue;
          const q = k.quad;
          ctx.moveTo(cam.toX(q[0][0]), cam.toY(q[0][1]));
          for (let j = 1; j < 4; j++) ctx.lineTo(cam.toX(q[j][0]), cam.toY(q[j][1]));
          ctx.closePath();
        }
        ctx.fill();
      }
    }
    // the edges: white lines; dashed where the edge is estimated (no lap drove along it)
    ctx.strokeStyle = "#d9dcdf";
    ctx.lineWidth = cam.scale > 2 ? 2 : 1;
    ctx.globalAlpha = 0.85;
    for (const [points, est] of [[left, leftEst], [right, rightEst]]) {
      let i = 0;
      while (i < points.length) {
        const dashed = !!est[i];
        let j = i;
        while (j + 1 < points.length && !!est[j + 1] === dashed) j++;
        if (ctx.setLineDash) ctx.setLineDash(dashed ? [6, 6] : []);
        ctx.beginPath();
        polyPath(ctx, points, Math.max(i - 1, 0), j);
        ctx.stroke();
        i = j + 1;
      }
    }
    if (ctx.setLineDash) ctx.setLineDash([]);
    ctx.globalAlpha = 1;
  }

  function drawStatic(ctx) {
    ctx.clearRect(0, 0, cam.width, cam.height);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    const d = detail && detail.response;
    const lineFor = (s) => (d && d[`x_${s}`] ? [d[`x_${s}`], d[`y_${s}`]] : null);

    // V0.9.5: the track from above — grass, asphalt, edges, kerbs
    if (surface && trackBox.checked) drawSurface(ctx);
    // a faint band of the car's width around EVERY lap (only where it is wider than ~3 px)
    if (bandBox.checked && cam.scale * CAR_WIDTH_M >= 3) {
      ctx.lineWidth = cam.scale * CAR_WIDTH_M;
      ctx.globalAlpha = surface && trackBox.checked ? 0.2 : (sides.length > 1 ? 0.11 : 0.14);
      for (const s of [...sides].reverse()) {
        ctx.strokeStyle = lapColor(s);
        const fine = lineFor(s);
        if (fine) {
          const i0 = indexForDistance(grid, detail.start);
          const i1 = indexForDistance(grid, detail.end);
          strokeLine(ctx, xs(s), ys(s), 0, Math.max(i0, 0));
          strokeLine(ctx, xs(s), ys(s), Math.min(i1, grid.count - 1), grid.count - 1);
          strokeLine(ctx, fine[0], fine[1]);
        } else {
          strokeLine(ctx, xs(s), ys(s));
        }
      }
      ctx.globalAlpha = 1;
    }
    // the lines, all the same width (Lap 2 drawn first, Lap 1 on top; extra laps with their style);
    // slightly transparent, so where two laps drove the same line both colours stay visible
    ctx.globalAlpha = 0.85;
    for (const s of [...sides].reverse()) {
      ctx.strokeStyle = lapColor(s);
      ctx.lineWidth = 2;
      if (ctx.setLineDash) ctx.setLineDash(LAP_DASH[s] || []);
      const fine = lineFor(s);
      if (fine) {
        // the overview outside the detail stretch, the detail inside it
        const i0 = indexForDistance(grid, detail.start);
        const i1 = indexForDistance(grid, detail.end);
        strokeLine(ctx, xs(s), ys(s), 0, Math.max(i0, 0));
        strokeLine(ctx, xs(s), ys(s), Math.min(i1, grid.count - 1), grid.count - 1);
        strokeLine(ctx, fine[0], fine[1]);
      } else {
        strokeLine(ctx, xs(s), ys(s));
      }
    }
    if (ctx.setLineDash) ctx.setLineDash([]);
    ctx.globalAlpha = 1;

    // start/finish
    const sx = xs(sides[0]); const sy = ys(sides[0]);
    if (sx[0] !== null && sx[0] !== undefined) {
      ctx.fillStyle = cssVar("--text", "#ECEDEE");
      ctx.beginPath();
      ctx.arc(cam.toX(sx[0]), cam.toY(sy[0]), 4, 0, Math.PI * 2);
      ctx.fill();
    }

    // markers per lap and corner, when zoomed in
    const span = cam.width / cam.scale;
    if (markersBox.checked && span < MARKER_SPAN_M) {
      for (const c of corners) {
        for (const s of ["b", "a"]) {
          const m = c[s];
          if (!m || !xs(s)) continue;
          for (const [kind, at] of [["brake", m.brake], ["turn_in", m.turn_in], ["apex", m.apex], ["throttle", m.throttle]]) {
            if (at === null || at === undefined) continue;
            const i = indexForDistance(grid, at);
            const x = xs(s)[i]; const y = ys(s)[i];
            if (x === null || x === undefined) continue;
            markerShape(ctx, kind, cam.toX(x), cam.toY(y), lapColor(s));
          }
        }
        // corner number near Lap 1's apex
        const ia = indexForDistance(grid, c.apex);
        const x = sx[ia]; const y = sy[ia];
        if (x !== null && x !== undefined) {
          ctx.font = "600 11px 'IBM Plex Mono', monospace";
          ctx.fillStyle = c.n === selected ? cssVar("--gold", "#D8A83E") : cssVar("--text-muted", "#8B9199");
          ctx.fillText(t("C{0}", [c.n]), cam.toX(x) + 9, cam.toY(y) - 9);
        }
      }
    }

    // scale bar
    const bar = scaleBar(cam.scale);
    const bx = 14; const by = cam.height - 16;
    ctx.strokeStyle = cssVar("--text", "#ECEDEE");
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(bx, by - 4); ctx.lineTo(bx, by); ctx.lineTo(bx + bar.px, by); ctx.lineTo(bx + bar.px, by - 4);
    ctx.stroke();
    ctx.fillStyle = cssVar("--text", "#ECEDEE");
    ctx.font = "11px 'IBM Plex Mono', monospace";
    ctx.fillText(`${bar.metres} m`, bx + bar.px + 6, by);
  }

  function drawCursor(ctx) {
    const d = detail && detail.response;
    // cursor: one dot per lap
    const cursor = state.getCursor();
    const cursorAt = distanceAt(grid, cursor);
    for (const s of [...sides].reverse()) {
      let x = xs(s)[cursor]; let y = ys(s)[cursor];
      if (d && d[`x_${s}`] && cursorAt >= detail.start && cursorAt <= detail.end) {
        const k = indexForDistance(d.grid, cursorAt);
        if (d[`x_${s}`][k] !== null) { x = d[`x_${s}`][k]; y = d[`y_${s}`][k]; }
      }
      if (x === null || x === undefined) continue;
      ctx.fillStyle = cssVar("--asphalt", "#14171A");
      ctx.beginPath();
      ctx.arc(cam.toX(x), cam.toY(y), 7.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = lapColor(s);
      ctx.strokeStyle = cssVar("--text", "#ECEDEE");
      ctx.lineWidth = state.isPinned() ? 2.5 : 1.5;
      ctx.beginPath();
      ctx.arc(cam.toX(x), cam.toY(y), 5.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
    updateInfo(cursor, cursorAt);
  }

  function offsetAt(cursor, cursorAt) {
    const d = detail && detail.response;
    if (d && d.offset_b && cursorAt >= detail.start && cursorAt <= detail.end) {
      const k = indexForDistance(d.grid, cursorAt);
      return [d.offset_b[k], d.side ? d.side[k] : 0];
    }
    return [map.offset_b ? map.offset_b[cursor] : null, map.side ? map.side[cursor] : 0];
  }
  function updateInfo(cursor, cursorAt) {
    const parts = [`${formatMetres(cursorAt)}`];
    if (hasB) {
      const [off, side] = offsetAt(cursor, cursorAt);
      const text = offsetText(off, side);
      if (text) parts.push(text);
    }
    if (detail) parts.push(t("high resolution"));
    info.textContent = parts.join(" · ");
  }

  // ---------------------------------------------------------------- pointer
  function nearest(px, py) {
    const x = xs(sides[0]); const y = ys(sides[0]);
    let best = -1; let bestD = 40 * 40;
    for (let i = 0; i < x.length; i++) {
      if (x[i] === null) continue;
      const dx = cam.toX(x[i]) - px; const dy = cam.toY(y[i]) - py;
      const dd = dx * dx + dy * dy;
      if (dd < bestD) { bestD = dd; best = i; }
    }
    return best;
  }
  const local = (event) => {
    const rect = canvas.getBoundingClientRect();
    return [event.clientX - rect.left, event.clientY - rect.top];
  };
  let drag = null;
  canvas.addEventListener("mousedown", (event) => {
    if (event.button !== 0) return;
    drag = { x: event.clientX, y: event.clientY, moved: false };
  });
  const onUp = () => {
    if (!element.isConnected) { window.removeEventListener("mouseup", onUp); return; }
    if (drag && drag.moved) canvas.dataset.justDragged = "1";
    drag = null;
  };
  window.addEventListener("mouseup", onUp);
  canvas.addEventListener("mousemove", (event) => {
    if (!cam) return;
    if (drag) {
      const dx = event.clientX - drag.x; const dy = event.clientY - drag.y;
      if (drag.moved || Math.hypot(dx, dy) > 4) {
        drag.moved = true;
        followSet("off");
        cam.panBy(dx, dy);
        drag.x = event.clientX; drag.y = event.clientY;
        afterMove();
      }
      return;
    }
    const [px, py] = local(event);
    const i = nearest(px, py);
    if (i >= 0) state.hoverCursor(i);
  });
  canvas.addEventListener("click", (event) => {
    if (canvas.dataset.justDragged) { delete canvas.dataset.justDragged; return; }
    if (!cam) return;
    const [px, py] = local(event);
    const i = nearest(px, py);
    if (i >= 0) state.pinCursor(i);
  });
  canvas.addEventListener("dblclick", () => { followSet("off"); camFit(); planDetail(); });
  canvas.addEventListener("wheel", (event) => {
    if (!cam) return;
    event.preventDefault();
    const [px, py] = local(event);
    followSet("off");
    cam.zoomAt(event.deltaY < 0 ? 1.25 : 0.8, px, py, minScale(), MAX_PX_PER_M);
    afterMove();
  }, { passive: false });
  canvas.addEventListener("keydown", (event) => {
    if (event.key === "+" || event.key === "=") { zoomCenter(1.4); event.preventDefault(); }
    if (event.key === "-") { zoomCenter(1 / 1.4); event.preventDefault(); }
  });

  // touch: one finger pans the map (the page does not scroll over the map — touch-action: none),
  // a tap places the cursor, two fingers pinch-zoom around their centre and pan in 2D
  let touch = null;
  const points = (event) => Array.from(event.touches || []).map((p) => { const r = canvas.getBoundingClientRect(); return [p.clientX - r.left, p.clientY - r.top]; });
  canvas.addEventListener("touchstart", (event) => {
    const p = points(event);
    if (p.length >= 2) touch = { mode: "pinch", d: Math.hypot(p[0][0] - p[1][0], p[0][1] - p[1][1]), c: [(p[0][0] + p[1][0]) / 2, (p[0][1] + p[1][1]) / 2] };
    else if (p.length === 1) touch = { mode: "one", start: p[0], last: p[0], moved: false };
    if (event.cancelable) event.preventDefault();
  }, { passive: false });
  canvas.addEventListener("touchmove", (event) => {
    if (!cam || !touch) return;
    const p = points(event);
    if (p.length >= 2) {
      const d = Math.hypot(p[0][0] - p[1][0], p[0][1] - p[1][1]);
      const c = [(p[0][0] + p[1][0]) / 2, (p[0][1] + p[1][1]) / 2];
      if (touch.mode !== "pinch") touch = { mode: "pinch", d, c };
      else {
        if (touch.d > 0 && d > 0) cam.zoomAt(d / touch.d, c[0], c[1], minScale(), MAX_PX_PER_M);
        cam.panBy(c[0] - touch.c[0], c[1] - touch.c[1]);
        touch.d = d; touch.c = c;
      }
      followSet("off");
      afterMove();
    } else if (p.length === 1 && touch.mode === "one") {
      const [x, y] = p[0];
      if (touch.moved || Math.hypot(x - touch.start[0], y - touch.start[1]) > 8) {
        touch.moved = true;
        followSet("off");
        cam.panBy(x - touch.last[0], y - touch.last[1]);
        afterMove();
      }
      touch.last = p[0];
    }
    if (event.cancelable) event.preventDefault();
  }, { passive: false });
  canvas.addEventListener("touchend", (event) => {
    if (touch && touch.mode === "one" && !touch.moved && !(event.touches || []).length) {
      const i = nearest(touch.start[0], touch.start[1]);
      if (i >= 0) state.setCursor(i);
    }
    if (!(event.touches || []).length) touch = null;
  });

  // ---------------------------------------------------------------- inputs
  const applyRange = () => {
    const lo = Number(fromInput.value);
    const hi = Number(toInput.value);
    if (fromInput.value === "" || toInput.value === "" || !(hi > lo)) return;
    followSet("off");
    fitStretch(lo, hi);
    if (opts.viewState) opts.viewState.setView(lo, hi);
  };
  fromInput.addEventListener("change", applyRange);
  toInput.addEventListener("change", applyRange);
  markersBox.addEventListener("change", schedule);
  bandBox.addEventListener("change", schedule);
  trackBox.addEventListener("change", schedule);
  let alive = true;
  if (opts.trackSurface) {
    opts.trackSurface.get().then((res) => {
      if (!alive || !res || !res.surface) return;
      surface = surfaceGeometry(res.surface);
      schedule();
    }).catch(() => { /* no surface: the lines alone, as before */ });
  }
  follow.addEventListener("change", () => { followNow(); });

  function followNow() {
    if (!cam) return;
    if (follow.value === "cursor") {
      const cursor = state.getCursor();
      const x = xs(sides[0])[cursor]; const y = ys(sides[0])[cursor];
      if (x !== null && x !== undefined) {
        cam.cx = x; cam.cy = y;
        if (cam.scale < homeScale * 4) cam.scale = homeScale * 4;
        afterMove();
      }
    } else if (follow.value === "charts" && opts.viewState) {
      const [lo, hi] = opts.viewState.getView();
      fitStretch(lo, hi);
    }
  }

  const unsubscribe = state.subscribe((what) => {
    if (what === "cursor") {
      if (follow.value === "cursor") followNow();
      schedule();
    }
  });
  const unsubscribeView = opts.viewState ? opts.viewState.subscribe((what) => {
    if (what === "view" && follow.value === "charts") followNow();
  }) : () => {};
  const unsubscribeSel = opts.selection ? opts.selection.subscribe((n) => {
    selected = n;
    chips.querySelectorAll(".linemap__chip").forEach((chip) => chip.classList.toggle("linemap__chip--on", chip.dataset.corner === String(n)));
    schedule();
  }) : () => {};
  const stopResize = onResize(element, () => { width = 0; schedule(); });
  schedule();

  return {
    element,
    redraw: draw,
    destroy() { alive = false; unsubscribe(); unsubscribeView(); unsubscribeSel(); stopResize(); if (detailTimer) clearTimeout(detailTimer); },
    // for tests
    _camera: () => cam,
    _zoomToCorner: zoomToCorner,
    _detail: () => detail,
    _info: info,
  };
}
