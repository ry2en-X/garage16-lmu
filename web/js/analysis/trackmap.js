// analysis/trackmap.js — the track map: lap A's line coloured by a chosen channel
// (or by where time is gained/lost against lap B), lap B as a thin line, one dot per
// lap at the shared cursor. Hover moves the cursor, a click pins it.
//
// The plan-view coordinates come from the server already corrected for LMU's
// world handedness (server/telemetry_analysis.py map_mirror), so what is drawn is
// the real track, not its mirror image.
//
// The coloured track is rendered once into an offscreen canvas and only re-rendered
// when the size, the data or the colour mode change; moving the cursor just blits it
// and draws two dots — the map must stay smooth while the mouse sweeps a chart.

import { el } from "../format.js";
import {
  distanceAt, divergingColor, fitView, formatChannelValue, formatDelta, indexForDistance, LAP_COLORS,
  nearestIndex, normalizeForColor, rampColor,
} from "./model.js";
import { coalesce, cssVar, fitCanvas, onResize } from "./charts.js";
import { bindTouchGestures } from "./touch.js";

import { t } from "../i18n.js";
const HEIGHT = 380;
const COLOR_MODE_CHANNELS = ["speed", "throttle", "brake", "gear", "rpm", "lat_g", "long_g"];
const LEVELS = 28; // colour levels: neighbouring segments of one level are drawn in one stroke

// Where lap A gains/loses time per metre: the local slope of the delta curve over a
// small window, so the colour says "lost time HERE", not "is behind by now".
export function deltaGain(delta, window = 8) {
  const out = new Array(delta.length).fill(null);
  for (let i = 0; i < delta.length; i++) {
    const a = delta[Math.max(i - window, 0)];
    const b = delta[Math.min(i + window, delta.length - 1)];
    if (a === null || b === null || !Number.isFinite(a) || !Number.isFinite(b)) continue;
    out[i] = b - a;
  }
  return out;
}

// Per-point colour positions for a mode; { colors: (string|null)[], legend: {...} }
export function colorsForMode(data, mode) {
  const n = data.grid.count;
  if (mode === "none" || !mode) return { colors: new Array(n).fill(null), legend: null };
  if (mode === "delta") {
    if (!data.delta) return { colors: new Array(n).fill(null), legend: null };
    const gain = deltaGain(data.delta);
    let maxAbs = 0;
    for (const v of gain) if (v !== null && Math.abs(v) > maxAbs) maxAbs = Math.abs(v);
    maxAbs = Math.max(maxAbs, 1e-6);
    return {
      colors: gain.map((v) => (v === null ? null : divergingColor(v / maxAbs))),
      legend: { kind: "diverging", left: t("Lap 1 gains"), right: t("Lap 1 loses") },
    };
  }
  const entry = data.channels[mode];
  const values = entry && (entry.a || entry.b);
  if (!values) return { colors: new Array(n).fill(null), legend: null };
  const positions = normalizeForColor(values, entry.fixed_range);
  const finite = values.filter((v) => v !== null && Number.isFinite(v));
  const sorted = [...finite].sort((p, q) => p - q);
  const lo = entry.fixed_range ? entry.fixed_range[0] : sorted[Math.floor((sorted.length - 1) * 0.02)];
  const hi = entry.fixed_range ? entry.fixed_range[1] : sorted[Math.ceil((sorted.length - 1) * 0.98)];
  return {
    colors: positions.map((pos) => (pos === null ? null : rampColor(Math.round(pos * LEVELS) / LEVELS))),
    legend: { kind: "ramp", left: formatChannelValue(mode, lo, entry), right: formatChannelValue(mode, hi, entry) },
  };
}

// Short names for the colour-by list when a channel is not loaded yet.
const MODE_FALLBACK_LABELS = {
  speed: t("Speed"), throttle: t("Throttle"), brake: t("Brake"), gear: t("Gear"), rpm: t("Engine speed"), lat_g: t("Lateral G"), long_g: t("Longitudinal G"),
};

/**
 * opts (all optional, V0.9.1):
 *   mode            initial colour mode ("delta", "none" or a channel key)
 *   ensureChannels  (keys) => Promise  loads a channel that is not in the data yet
 *   onModeChange    (mode) => void     called after the user picked a mode
 */
export function createTrackMap(state, opts = {}) {
  const data = state.data;
  const map = data.map || {};
  const xsA = map.x_a;
  const ysA = map.y_a;
  const xsB = map.x_b;
  const ysB = map.y_b;
  const mainXs = xsA || xsB;
  const mainYs = ysA || ysB;

  if (!mainXs) {
    return { element: el("div", { class: "empty-state" }, t("No position data in these laps — the track map needs laps recorded with the V0.9 client.")), destroy() {} };
  }

  const canvas = el("canvas", { class: "trackmap__canvas", "aria-label": t("Track map") });
  const legendBar = el("div", { class: "trackmap__legend" });
  const modes = [["none", t("Plain")]];
  if (data.delta) modes.push(["delta", t("Time gained / lost")]);
  for (const key of COLOR_MODE_CHANNELS) {
    const entry = data.channels[key];
    if (entry && (entry.a || entry.b)) modes.push([key, entry.label]);
    // A channel that is not loaded yet is still offered when we can load it on demand.
    else if (!entry && opts.ensureChannels) modes.push([key, MODE_FALLBACK_LABELS[key] || key]);
  }
  const offered = new Set(modes.map(([value]) => value));
  const fallbackMode = data.delta ? "delta" : data.channels.speed ? "speed" : "none";
  const defaultMode = opts.mode && offered.has(opts.mode) ? opts.mode : fallbackMode;
  const select = el("select", { id: "mapColorMode" }, modes.map(([value, label]) => el("option", { value }, label)));
  select.value = defaultMode;
  const controls = el("div", { class: "trackmap__controls" }, [el("label", { for: "mapColorMode" }, t("Colour by")), select]);
  const element = el("div", { class: "trackmap" }, [controls, canvas, legendBar]);

  let mode = defaultMode;
  let colored = colorsForMode(data, mode);
  let layer = null; // offscreen canvas with the static track
  let layerKey = "";
  let view = null;

  function renderLegend() {
    legendBar.innerHTML = "";
    if (!colored.legend) {
      if (mode !== "none" && mode !== "delta") legendBar.appendChild(el("span", {}, t("No data in this lap")));
      return;
    }
    const stops = colored.legend.kind === "diverging"
      ? [-1, -0.5, 0, 0.5, 1].map(divergingColor)
      : [0, 0.25, 0.5, 0.75, 1].map(rampColor);
    legendBar.appendChild(el("span", {}, colored.legend.left));
    legendBar.appendChild(el("span", { class: "trackmap__bar", style: `background:linear-gradient(90deg,${stops.join(",")});` }));
    legendBar.appendChild(el("span", {}, colored.legend.right));
  }

  function drawStaticLayer(w, h) {
    const dpr = (typeof window !== "undefined" && window.devicePixelRatio) || 1;
    layer = document.createElement("canvas");
    layer.width = Math.round(w * dpr);
    layer.height = Math.round(h * dpr);
    const ctx = layer.getContext ? layer.getContext("2d") : null;
    if (!ctx) return false;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    // lap B first, thin and quiet, underneath
    if (xsB && xsA) {
      ctx.strokeStyle = LAP_COLORS.b;
      ctx.globalAlpha = 0.55;
      ctx.lineWidth = 1.5;
      strokePath(ctx, xsB, ysB);
      ctx.globalAlpha = 1;
    }

    // dark casing, so the coloured line reads on any background
    ctx.strokeStyle = cssVar("--asphalt", "#14171A");
    ctx.lineWidth = 9;
    strokePath(ctx, mainXs, mainYs);

    // coloured line: consecutive segments of one colour are one stroke
    ctx.lineWidth = 5;
    const plain = LAP_COLORS.a;
    let currentColor = null;
    let open = false;
    const flush = () => {
      if (open) ctx.stroke();
      open = false;
    };
    for (let i = 0; i < mainXs.length - 1; i++) {
      const x1 = mainXs[i], y1 = mainYs[i], x2 = mainXs[i + 1], y2 = mainYs[i + 1];
      if (x1 === null || x2 === null || y1 === null || y2 === null) {
        flush();
        continue;
      }
      const color = colored.colors[i] || plain;
      if (color !== currentColor || !open) {
        flush();
        ctx.strokeStyle = color;
        ctx.beginPath();
        ctx.moveTo(view.toX(x1), view.toY(y1));
        currentColor = color;
        open = true;
      }
      ctx.lineTo(view.toX(x2), view.toY(y2));
    }
    flush();

    // start/finish + sector markers
    marker(ctx, mainXs, mainYs, 0, "S/F");
    const sectors = (data.sectors && data.sectors.a) || [];
    sectors.forEach((distance, k) => marker(ctx, mainXs, mainYs, indexForDistance(data.grid, distance), `S${k + 2}`));
    return true;

    function strokePath(c, xs, ys) {
      c.beginPath();
      let pen = false;
      for (let i = 0; i < xs.length; i++) {
        if (xs[i] === null || ys[i] === null) {
          pen = false;
          continue;
        }
        const x = view.toX(xs[i]);
        const y = view.toY(ys[i]);
        if (!pen) {
          c.moveTo(x, y);
          pen = true;
        } else {
          c.lineTo(x, y);
        }
      }
      c.stroke();
    }

    function marker(c, xs, ys, index, label) {
      const x = xs[index];
      const y = ys[index];
      if (x === null || y === null || x === undefined) return;
      const px = view.toX(x);
      const py = view.toY(y);
      c.fillStyle = cssVar("--text", "#ECEDEE");
      c.beginPath();
      c.arc(px, py, 5.5, 0, Math.PI * 2);
      c.fill();
      c.font = "600 10px 'IBM Plex Mono', monospace";
      c.textAlign = "left";
      c.textBaseline = "middle";
      c.fillStyle = cssVar("--text-muted", "#8B9199");
      c.fillText(label, px + 9, py - 9);
    }
  }

  function draw() {
    const fit = fitCanvas(canvas, HEIGHT);
    if (!fit) return;
    const { ctx, w, h } = fit;
    const key = `${w}x${h}|${mode}`;
    if (!view || layerKey !== key) {
      view = fitView(mainXs, mainYs, w, h, 22);
      if (!view) return;
      if (!drawStaticLayer(w, h)) return;
      layerKey = key;
    }
    ctx.clearRect(0, 0, w, h);
    const dpr = (typeof window !== "undefined" && window.devicePixelRatio) || 1;
    ctx.drawImage(layer, 0, 0, layer.width / dpr, layer.height / dpr);

    const cursor = state.getCursor();
    for (const [xs, ys, color] of [[xsB, ysB, LAP_COLORS.b], [xsA, ysA, LAP_COLORS.a]]) {
      if (!xs || xs[cursor] === null || xs[cursor] === undefined) continue;
      const px = view.toX(xs[cursor]);
      const py = view.toY(ys[cursor]);
      ctx.fillStyle = cssVar("--asphalt", "#14171A");
      ctx.beginPath();
      ctx.arc(px, py, 8, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = color;
      ctx.strokeStyle = cssVar("--text", "#ECEDEE");
      ctx.lineWidth = state.isPinned() ? 2.5 : 1.5;
      ctx.beginPath();
      ctx.arc(px, py, 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
  }

  const schedule = coalesce(draw);

  function indexAtPointer(event) {
    if (!view) return -1;
    const rect = canvas.getBoundingClientRect();
    const px = event.clientX - rect.left;
    const py = event.clientY - rect.top;
    // Compare in screen space: project every point, find the nearest to the pointer.
    const sx = mainXs.map((x) => (x === null ? null : view.toX(x)));
    const sy = mainYs.map((y) => (y === null ? null : view.toY(y)));
    return nearestIndex(sx, sy, px, py, 60);
  }

  canvas.addEventListener("mousemove", (event) => {
    const index = indexAtPointer(event);
    if (index >= 0) state.hoverCursor(index);
  });
  canvas.addEventListener("click", (event) => {
    const index = indexAtPointer(event);
    if (index >= 0) state.pinCursor(index);
  });
  // Touch (V0.9.1): tap = cursor; a plain swipe scrolls the page; hold pins the cursor
  // and dragging on after the hold moves it along the track (analysis/touch.js).
  const atXY = (x, y) => indexAtPointer({ clientX: x, clientY: y });
  bindTouchGestures(canvas, {
    tap: (x, y) => { const i = atXY(x, y); if (i >= 0) state.setCursor(i); },
    scrub: (x, y) => { const i = atXY(x, y); if (i >= 0) state.setCursor(i); },
    longPress: (x, y) => { const i = atXY(x, y); if (i >= 0) state.pinCursor(i); },
    pinch: () => {},
    pan: () => {},
  }, { holdToScrub: true });

  select.addEventListener("change", async () => {
    mode = select.value;
    if (mode !== "none" && mode !== "delta" && !data.channels[mode] && opts.ensureChannels) {
      legendBar.textContent = t("Loading channel …");
      try {
        await opts.ensureChannels([mode]);
      } catch {
        legendBar.textContent = t("The channel could not be loaded.");
        return;
      }
      if (select.value !== mode) return; // the user picked something else meanwhile
    }
    colored = colorsForMode(data, mode);
    layerKey = ""; // the static layer holds the colours: draw it again
    renderLegend();
    schedule();
    if (opts.onModeChange) opts.onModeChange(mode);
  });

  const unsubscribe = state.subscribe((what) => {
    if (what === "cursor") schedule();
  });
  const stopResize = onResize(element, () => {
    layerKey = "";
    schedule();
  });
  renderLegend();
  schedule();

  return {
    element,
    redraw: draw,
    destroy() {
      unsubscribe();
      stopResize();
    },
    _canvas: canvas,
    _indexAtPointer: indexAtPointer,
  };
}

export { distanceAt, formatDelta };
