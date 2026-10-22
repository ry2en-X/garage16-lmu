// analysis/distribution.js — histograms (V0.9.3): the damper window and the
// "histogram of any channel" window.
//
// The numbers come from GET /telemetry/analysis/distribution (server/routers/analysis.py),
// fetched through ctx.fetchDistribution (the page binds it to the two laps):
// per channel the bin edges (the same for both laps) and, per lap, the share of the lap's
// TIME in each bin. Nothing here depends on the cursor, so these windows never redraw
// while the mouse moves; they load once and redraw only when resized.
//
// Damper histogram convention (as in common damper-analysis practice): x = damper
// velocity in mm/s, bump (compression) positive to the right, rebound negative to the
// left; the vertical lines mark the low/high-speed split (`knee`, default 25 mm/s).

import { el } from "../format.js";
import { coalesce, cssVar, fitCanvas, onResize } from "./charts.js";
import { formatValue, LAP_COLORS, niceTicks, WHEEL_LABEL, WHEELS } from "./model.js";

import { t } from "../i18n.js";

const PAD = { left: 40, right: 8, top: 8, bottom: 22 };
export const DAMPER_CHANNELS = WHEELS.map((w) => `damper_vel_${w}`);

// Pure: the drawing plan of one histogram (bars per lap, axes) — testable without a canvas.
//   entry: one channel of the /distribution response; sides: ["a"] or ["a", "b"]
export function histogramPlan(entry, sides) {
  const edges = entry && entry.edges;
  if (!edges || edges.length < 2) return null;
  const bins = edges.length - 1;
  let top = 0;
  const series = [];
  for (const side of sides) {
    const d = entry[side];
    if (!d || !d.pct) continue;
    for (const v of d.pct) if (v !== null && v > top) top = v;
    series.push({ side, pct: d.pct, below: d.below || 0, above: d.above || 0 });
  }
  if (!series.length) return null;
  return { edges, bins, lo: edges[0], hi: edges[edges.length - 1], top: top > 0 ? top : 1, series };
}

export function drawHistogram(canvas, plan, { height = 150, knee = null, unit = "", discrete = false } = {}) {
  const fit = fitCanvas(canvas, height);
  if (!fit || !plan) return;
  const { ctx, w, h } = fit;
  ctx.clearRect(0, 0, w, h);
  const plotW = Math.max(w - PAD.left - PAD.right, 10);
  const plotH = Math.max(h - PAD.top - PAD.bottom, 10);
  const x = (v) => PAD.left + ((v - plan.lo) / (plan.hi - plan.lo)) * plotW;
  const y = (pct) => PAD.top + plotH - (pct / plan.top) * plotH;
  const muted = cssVar("--text-muted", "#8B9199");
  const grid = cssVar("--hairline", "#2C3136");

  // horizontal grid + y labels (percent of the lap's time)
  ctx.font = "10px 'IBM Plex Mono', monospace";
  ctx.fillStyle = muted;
  ctx.strokeStyle = grid;
  ctx.lineWidth = 1;
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (const tick of niceTicks(0, plan.top, 3)) {
    const py = y(tick);
    ctx.beginPath();
    ctx.moveTo(PAD.left, py);
    ctx.lineTo(PAD.left + plotW, py);
    ctx.stroke();
    ctx.fillText(`${tick}%`, PAD.left - 4, py);
  }
  // x labels
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const xTicks = discrete
    ? plan.edges.slice(0, -1).map((e) => e + 0.5)
    : niceTicks(plan.lo, plan.hi, Math.max(3, Math.floor(plotW / 70)));
  // whole numbers without decimals ("-40", not "-40.0"); the outermost labels are aligned
  // inwards so they are never cut off at the edge of the canvas
  const label = (tick) => (Number.isInteger(tick) ? String(tick) : formatValue(tick, "", discrete));
  for (const tick of xTicks) {
    const px = x(tick);
    ctx.textAlign = px < PAD.left + 14 ? "left" : px > PAD.left + plotW - 14 ? "right" : "center";
    ctx.fillText(label(tick), px, PAD.top + plotH + 4);
  }

  // bars: two laps side by side within each bin
  const n = plan.series.length;
  for (let b = 0; b < plan.bins; b++) {
    const x0 = x(plan.edges[b]);
    const x1 = x(plan.edges[b + 1]);
    const width = Math.max((x1 - x0) / n - 1, 1);
    plan.series.forEach((s, k) => {
      const v = s.pct[b];
      if (v === null || v === undefined || v <= 0) return;
      ctx.fillStyle = LAP_COLORS[s.side];
      ctx.globalAlpha = 0.85;
      ctx.fillRect(x0 + k * (width + 1) + 0.5, y(v), width, PAD.top + plotH - y(v));
    });
  }
  ctx.globalAlpha = 1;
  // zero line and knees (damper)
  if (knee !== null && plan.lo < 0 && plan.hi > 0) {
    ctx.strokeStyle = cssVar("--text", "#ECEDEE");
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x(0), PAD.top);
    ctx.lineTo(x(0), PAD.top + plotH);
    ctx.stroke();
    ctx.setLineDash([3, 3]);
    ctx.strokeStyle = muted;
    for (const k of [-knee, knee]) {
      if (k <= plan.lo || k >= plan.hi) continue;
      ctx.beginPath();
      ctx.moveTo(x(k), PAD.top);
      ctx.lineTo(x(k), PAD.top + plotH);
      ctx.stroke();
    }
    ctx.setLineDash([]);
  }
  if (unit) {
    ctx.textAlign = "right";
    ctx.textBaseline = "top";
    ctx.fillStyle = muted;
    ctx.fillText(unit, PAD.left + plotW, PAD.top);
  }
}

const sidesOf = (ctx) => (ctx.baseData && ctx.baseData.laps.b ? ["a", "b"] : ["a"]);
const pct = (v) => (v === null || v === undefined ? "—" : `${v.toFixed(1)} %`);

function shell(title, caption) {
  const body = el("div", { class: "dist__body" }, el("div", { class: "notice" }, t("Loading …")));
  const element = el("div", { class: "card dist" }, [el("h2", {}, title), body, caption ? el("div", { class: "subtitle dist__caption" }, caption) : null]);
  return { element, body };
}

// ---------------------------------------------------------------- damper window

function damperTable(response, sides) {
  const head = el("tr", {}, [el("th", {}, ""), ...WHEELS.map((w) => el("th", { colspan: String(sides.length) }, WHEEL_LABEL[w]))]);
  const lapRow = el("tr", { class: "dist__laps" }, [el("th", {}, ""), ...WHEELS.flatMap(() => sides.map((s) => el("th", { style: `color:${LAP_COLORS[s]};` }, s === "a" ? t("Lap 1") : t("Lap 2"))))]);
  const rows = [
    [t("Bump"), "bump"], [t("Rebound"), "rebound"],
    [t("Low-speed bump"), "low_bump"], [t("High-speed bump"), "high_bump"],
    [t("Low-speed rebound"), "low_rebound"], [t("High-speed rebound"), "high_rebound"],
  ].map(([label, key]) => el("tr", { "data-row": key }, [
    el("td", {}, label),
    ...WHEELS.flatMap((w) => sides.map((s) => {
      const d = response.channels[`damper_vel_${w}`] && response.channels[`damper_vel_${w}`][s];
      return el("td", {}, d && d.damper ? pct(d.damper[key]) : "—");
    })),
  ]));
  return el("div", { class: "dist__table-wrap" }, el("table", { class: "dist__table" }, [el("thead", {}, [head, lapRow]), el("tbody", {}, rows)]));
}

export function renderDamper(widget, ctx) {
  const { element, body } = shell(
    t("Damper histograms"),
    t("Share of the lap's time at each damper velocity (bump = compression, right; rebound = left). Dashed lines: low/high-speed split at {0} mm/s.", [25]),
  );
  const sides = sidesOf(ctx);
  const canvases = [];
  let stop = () => {};
  let alive = true;
  ctx.fetchDistribution({ channels: DAMPER_CHANNELS, bins: 30 }).then((response) => {
    if (!alive) return;
    body.innerHTML = "";
    const tiles = [];
    for (const w of WHEELS) {
      const entry = response.channels[`damper_vel_${w}`];
      const plan = histogramPlan(entry, sides);
      if (!plan) {
        tiles.push(el("div", { class: "dist__tile" }, [el("div", { class: "dist__tile-title" }, WHEEL_LABEL[w]), el("div", { class: "subtitle" }, t("No suspension data in this lap (recorded before V0.9)."))]));
        continue;
      }
      const canvas = el("canvas", { class: "dist__canvas", "data-wheel": w });
      canvases.push({ canvas, plan, knee: response.knee });
      tiles.push(el("div", { class: "dist__tile" }, [el("div", { class: "dist__tile-title" }, WHEEL_LABEL[w]), canvas]));
    }
    body.appendChild(el("div", { class: "dist__wheels" }, tiles));
    if (canvases.length) body.appendChild(damperTable(response, sides));
    const draw = coalesce(() => canvases.forEach((c) => drawHistogram(c.canvas, c.plan, { height: 130, knee: c.knee, unit: "mm/s" })));
    stop = onResize(body, draw);
    draw();
  }).catch((err) => {
    if (!alive) return;
    body.innerHTML = "";
    body.appendChild(el("div", { class: "notice notice--error" }, err.message || t("Could not load the histograms.")));
  });
  return { element, destroy: () => { alive = false; stop(); } };
}

// ---------------------------------------------------------------- one channel

export function renderHistogram(widget, ctx) {
  const label = (ctx.catalog.channels.find((c) => c.key === widget.channel) || {}).label || widget.channel;
  const { element, body } = shell(t("Histogram — {0}", [label]), t("Share of the lap's time in each range."));
  const sides = sidesOf(ctx);
  let stop = () => {};
  let alive = true;
  ctx.fetchDistribution({ channels: [widget.channel], bins: 30 }).then((response) => {
    if (!alive) return;
    body.innerHTML = "";
    const entry = response.channels[widget.channel];
    const plan = histogramPlan(entry, sides);
    if (!plan) {
      body.appendChild(el("div", { class: "notice" }, t("No data for this channel in these laps.")));
      return;
    }
    const canvas = el("canvas", { class: "dist__canvas" });
    const facts = el("div", { class: "dist__facts" }, sides.map((s) => {
      const d = entry[s];
      if (!d) return null;
      const st = d.stats;
      const f = (v) => formatValue(v, entry.unit, entry.discrete);
      return el("div", { class: "dist__fact", style: `border-left:3px solid ${LAP_COLORS[s]};` },
        t("{0}: median {1} · 90 % between {2} and {3} · max {4}", [s === "a" ? t("Lap 1") : t("Lap 2"), f(st.p50), f(st.p5), f(st.p95), f(st.max)]));
    }));
    body.appendChild(canvas);
    body.appendChild(facts);
    const draw = coalesce(() => drawHistogram(canvas, plan, { height: Number.isFinite(widget.boxHeight) ? Math.max(widget.boxHeight - 110, 80) : widget.size === "small" ? 110 : 150, unit: entry.unit, discrete: entry.discrete }));
    stop = onResize(body, draw);
    draw();
  }).catch((err) => {
    if (!alive) return;
    body.innerHTML = "";
    body.appendChild(el("div", { class: "notice notice--error" }, err.message || t("Could not load the histogram.")));
  });
  return { element, destroy: () => { alive = false; stop(); } };
}
