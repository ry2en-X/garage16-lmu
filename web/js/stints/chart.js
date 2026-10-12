// stints/chart.js — small SVG charts for the Stint Analyzer (V0.9.4): lap times over the
// session, lap time over tyre age, consumption per lap. SVG (not canvas) so the charts scale
// with the page, are readable by screen readers through <title>, and are testable in jsdom.

const NS = "http://www.w3.org/2000/svg";

function svg(tag, attrs = {}, children = []) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) node.setAttribute(k, String(v));
  for (const c of [].concat(children)) if (c) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  return node;
}

const finite = (v) => typeof v === "number" && Number.isFinite(v);

// "Nice" tick values between lo and hi (about `count` of them).
export function ticks(lo, hi, count = 5) {
  if (!finite(lo) || !finite(hi)) return [];
  if (hi === lo) return [lo];
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.round(v / step) * step);
  return out;
}

/**
 * series: [{ label, color, points: [[x, y, meta?]], kind: "line" | "dots" | "band",
 *            dashed?, radius?, band?: [[x, low, high]] , onClick?(meta) }]
 * options: { width, height, xLabel, yLabel, yFormat(v), xFormat(v), yMin, yMax, invertY,
 *            markers: [{ x, label }] }
 */
export function chart(series, options = {}) {
  const width = options.width || 720;
  const height = options.height || 240;
  const pad = { l: 58, r: 12, t: options.yLabel ? 24 : 12, b: 34 };
  const all = series.flatMap((s) => (s.kind === "band" ? (s.band || []).flatMap(([x, lo, hi]) => [[x, lo], [x, hi]]) : s.points || []))
    .filter(([x, y]) => finite(x) && finite(y));
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, class: "stint-chart", role: "img",
    "aria-label": options.title || options.yLabel || "" });
  if (!all.length) return root;
  let [x0, x1] = [Math.min(...all.map((p) => p[0])), Math.max(...all.map((p) => p[0]))];
  let [y0, y1] = [finite(options.yMin) ? options.yMin : Math.min(...all.map((p) => p[1])),
    finite(options.yMax) ? options.yMax : Math.max(...all.map((p) => p[1]))];
  if (x0 === x1) { x0 -= 1; x1 += 1; }
  if (y0 === y1) { y0 -= 0.5; y1 += 0.5; }
  const yPad = (y1 - y0) * 0.06;
  y0 -= yPad; y1 += yPad;
  const sx = (x) => pad.l + ((x - x0) / (x1 - x0)) * (width - pad.l - pad.r);
  const sy = (y) => pad.t + ((y1 - y) / (y1 - y0)) * (height - pad.t - pad.b);
  const fy = options.yFormat || ((v) => String(Math.round(v * 100) / 100));
  const fx = options.xFormat || ((v) => String(v));

  const grid = svg("g", { class: "stint-chart__grid" });
  for (const v of ticks(y0, y1, 4)) {
    grid.appendChild(svg("line", { x1: pad.l, x2: width - pad.r, y1: sy(v), y2: sy(v) }));
    grid.appendChild(svg("text", { x: pad.l - 6, y: sy(v) + 4, "text-anchor": "end" }, fy(v)));
  }
  for (const v of ticks(x0, x1, 8)) {
    grid.appendChild(svg("text", { x: sx(v), y: height - pad.b + 16, "text-anchor": "middle" }, fx(v)));
  }
  if (options.xLabel) grid.appendChild(svg("text", { x: width - pad.r, y: height - 4, "text-anchor": "end", class: "stint-chart__axis" }, options.xLabel));
  if (options.yLabel) grid.appendChild(svg("text", { x: 4, y: pad.t + 2, class: "stint-chart__axis", "dominant-baseline": "hanging" }, options.yLabel));
  root.appendChild(grid);

  for (const m of options.markers || []) {
    if (!finite(m.x)) continue;
    root.appendChild(svg("line", { class: "stint-chart__marker", x1: sx(m.x), x2: sx(m.x), y1: pad.t, y2: height - pad.b }));
    if (m.label) root.appendChild(svg("text", { class: "stint-chart__marker-label", x: sx(m.x) + 3, y: pad.t + 10 }, m.label));
  }

  for (const s of series) {
    if (s.kind === "band") {
      const pts = (s.band || []).filter(([x, lo, hi]) => finite(x) && finite(lo) && finite(hi));
      if (pts.length < 2) continue;
      const d = `M${pts.map(([x, lo]) => `${sx(x).toFixed(1)},${sy(lo).toFixed(1)}`).join("L")}`
        + `L${pts.slice().reverse().map(([x, , hi]) => `${sx(x).toFixed(1)},${sy(hi).toFixed(1)}`).join("L")}Z`;
      root.appendChild(svg("path", { d, fill: s.color, opacity: 0.18, stroke: "none" }, s.label ? svg("title", {}, s.label) : null));
      continue;
    }
    const pts = (s.points || []).filter(([x, y]) => finite(x) && finite(y));
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
        root.appendChild(svg("polyline", {
          points: seg.map(([x, y]) => `${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join(" "),
          fill: "none", stroke: s.color, "stroke-width": s.width || 2, "stroke-dasharray": s.dashed ? "5 4" : null,
        }, s.label ? svg("title", {}, s.label) : null));
      }
    } else {
      const g = svg("g", { class: "stint-chart__dots" });
      for (const p of pts) {
        const dot = svg("circle", { cx: sx(p[0]).toFixed(1), cy: sy(p[1]).toFixed(1), r: (p[2] && p[2].radius) || s.radius || 3.5,
          fill: (p[2] && p[2].color) || s.color, "fill-opacity": (p[2] && p[2].faded) ? 0.35 : 1,
          tabindex: s.onClick ? 0 : null, class: s.onClick ? "stint-chart__dot--link" : null },
        p[2] && p[2].title ? svg("title", {}, p[2].title) : null);
        if (s.onClick) {
          dot.addEventListener("click", () => s.onClick(p[2]));
          dot.addEventListener("keydown", (e) => { if (e.key === "Enter") s.onClick(p[2]); });
        }
        g.appendChild(dot);
      }
      root.appendChild(g);
    }
  }
  return root;
}

// A legend row under a chart.
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
