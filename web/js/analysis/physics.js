// analysis/physics.js — "Car physics": at the shared cursor, one card per lap shows
// the four wheels (suspension travel, tyre load, slip angle, temperature, sliding
// share) and a G-G diagram shows lateral vs longitudinal acceleration with the whole
// lap as a faint trace.
//
// It is a replay at the cursor, not a live view: move over a chart, hover the map or
// pin a spot, and these numbers follow. A lap recorded before V0.9.0 has no such
// channels; its card says so instead of showing zeros.

import { el } from "../format.js";
import { clamp, formatValue, LAP_COLORS, niceTicks, physicsSnapshot, WHEEL_LABEL, WHEELS } from "./model.js";
import { coalesce, cssVar, fitCanvas, onResize } from "./charts.js";

import { t } from "../i18n.js";
const GG_SIZE = 300;
const SLIP_NEEDLE_DEGREES_PER_DEGREE = 4; // a 5° slip angle is easier to see as a 20° needle
const MAX_NEEDLE = 50;

function maxOf(data, prefix) {
  let m = 0;
  for (const w of WHEELS) {
    const entry = data.channels[`${prefix}_${w}`];
    if (!entry) continue;
    for (const side of ["a", "b"]) {
      const series = entry[side];
      if (!series) continue;
      for (const v of series) if (v !== null && Number.isFinite(v) && v > m) m = v;
    }
  }
  return m || 1;
}

function bar(color) {
  const fill = el("span", { class: "phys__fill", style: `background:${color};` });
  return { track: el("span", { class: "phys__bar" }, fill), fill };
}

function wheelCell(label, color) {
  const travel = bar(color);
  const load = bar(color);
  const sliding = bar(color);
  const needle = el("span", { class: "phys__needle" });
  const values = {
    travel: el("span", { class: "phys__num" }, "—"),
    load: el("span", { class: "phys__num" }, "—"),
    slip: el("span", { class: "phys__num" }, "—"),
    temp: el("span", { class: "phys__num" }, "—"),
    sliding: el("span", { class: "phys__num" }, "—"),
  };
  const row = (name, bartrack, value) => el("div", { class: "phys__row" }, [el("span", { class: "phys__name" }, name), bartrack || el("span", {}), value]);
  const cell = el("div", { class: "phys__wheel" }, [
    el("div", { class: "phys__wheel-title" }, label),
    row(t("Travel"), travel.track, values.travel),
    row(t("Load"), load.track, values.load),
    row(t("Sliding"), sliding.track, values.sliding),
    el("div", { class: "phys__row" }, [el("span", { class: "phys__name" }, t("Slip")), el("span", { class: "phys__needle-box" }, needle), values.slip]),
    row(t("Temp"), null, values.temp),
  ]);
  return { cell, travel, load, sliding, needle, values };
}

export function createPhysicsView(state) {
  const data = state.data;
  const travelMax = maxOf(data, "susp_travel");
  const loadMax = maxOf(data, "tire_load");

  const hasPhysics = (side) => WHEELS.some((w) => data.channels[`susp_travel_${w}`] && data.channels[`susp_travel_${w}`][side]);

  const cards = {};
  const cardEls = [];
  for (const side of ["a", "b"]) {
    if (!data.laps[side]) continue;
    const color = LAP_COLORS[side];
    const cells = {};
    for (const w of WHEELS) cells[w] = wheelCell(WHEEL_LABEL[w], color);
    const grid = el("div", { class: "phys__car" }, [
      el("div", { class: "phys__row2" }, [cells.fl.cell, cells.fr.cell]),
      el("div", { class: "phys__chassis" }),
      el("div", { class: "phys__row2" }, [cells.rl.cell, cells.rr.cell]),
    ]);
    const note = el("div", { class: "notice", style: hasPhysics(side) ? "display:none;" : "" },
      t("This lap has no suspension/tyre data (recorded before V0.9 or by an older client)."));
    const title = el("div", { class: "phys__title", style: `border-color:${color};` }, [
      el("span", { style: `color:${color};` }, side === "a" ? t("Lap 1") : t("Lap 2")),
      ` — ${data.laps[side].driver_name}`,
    ]);
    const card = el("div", { class: "card phys__card" }, [title, note, grid]);
    cards[side] = cells;
    cardEls.push(card);
  }

  const gg = el("canvas", { class: "phys__gg", "aria-label": t("G-G diagram") });
  const ggReadout = el("div", { class: "phys__gg-readout" }, "");
  const ggCard = el("div", { class: "card phys__card phys__card--gg" }, [
    el("div", { class: "phys__title" }, t("G-G diagram")),
    gg,
    ggReadout,
    el("div", { class: "subtitle", style: "margin:6px 0 0;" }, t("Right turn = +, accelerating = +. The faint line is the whole lap.")),
  ]);

  const element = el("div", { class: "phys" }, cardEls.length === 2 ? [cardEls[0], ggCard, cardEls[1]] : [...cardEls, ggCard]);

  function updateCards() {
    const index = state.getCursor();
    for (const side of Object.keys(cards)) {
      const snap = physicsSnapshot(data, index, side);
      for (const w of WHEELS) {
        const cell = cards[side][w];
        const v = snap.wheels[w];
        cell.values.travel.textContent = formatValue(v.travel, "mm");
        cell.values.load.textContent = formatValue(v.load, "N");
        cell.values.slip.textContent = formatValue(v.slip, "°");
        cell.values.temp.textContent = formatValue(v.temp, "°C");
        cell.values.sliding.textContent = v.sliding === null ? "—" : `${Math.round(v.sliding)} %`;
        cell.travel.fill.style.width = v.travel === null ? "0%" : `${clamp((v.travel / travelMax) * 100, 0, 100)}%`;
        cell.load.fill.style.width = v.load === null ? "0%" : `${clamp((v.load / loadMax) * 100, 0, 100)}%`;
        cell.sliding.fill.style.width = v.sliding === null ? "0%" : `${clamp(v.sliding, 0, 100)}%`;
        const angle = v.slip === null ? 0 : clamp(v.slip * SLIP_NEEDLE_DEGREES_PER_DEGREE, -MAX_NEEDLE, MAX_NEEDLE);
        cell.needle.style.transform = `rotate(${angle}deg)`;
        cell.needle.style.opacity = v.slip === null ? "0.25" : "1";
      }
    }
    const parts = [];
    for (const side of ["a", "b"]) {
      if (!data.laps[side]) continue;
      const s = physicsSnapshot(data, index, side);
      parts.push(t("{0}: {1} lat · {2} long", [side === "a" ? t("Lap 1") : t("Lap 2"), formatValue(s.latG, "G"), formatValue(s.longG, "G")]));
    }
    ggReadout.textContent = parts.join("   |   ");
  }

  // ---- G-G canvas
  function ggSeries(side) {
    const lat = data.channels.lat_g && data.channels.lat_g[side];
    const lon = data.channels.long_g && data.channels.long_g[side];
    return lat && lon ? { lat, lon } : null;
  }
  let ggMax = 1.5;
  for (const side of ["a", "b"]) {
    const s = ggSeries(side);
    if (!s) continue;
    for (let i = 0; i < s.lat.length; i++) {
      for (const v of [s.lat[i], s.lon[i]]) if (v !== null && Number.isFinite(v)) ggMax = Math.max(ggMax, Math.abs(v));
    }
  }
  ggMax = Math.ceil(ggMax * 2) / 2;

  function drawGG() {
    const fit = fitCanvas(gg, GG_SIZE);
    if (!fit) return;
    const { ctx, w, h } = fit;
    const cx = w / 2;
    const cy = h / 2;
    const radius = Math.min(w, h) / 2 - 16;
    const toX = (g) => cx + (g / ggMax) * radius;
    const toY = (g) => cy - (g / ggMax) * radius;
    const hairline = cssVar("--hairline", "#2C3136");
    const muted = cssVar("--text-muted", "#8B9199");

    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = hairline;
    ctx.fillStyle = muted;
    ctx.lineWidth = 1;
    ctx.font = "10px 'IBM Plex Mono', monospace";
    ctx.textAlign = "left";
    for (const ring of niceTicks(0, ggMax, 4)) {
      if (ring <= 0) continue;
      ctx.beginPath();
      ctx.arc(cx, cy, (ring / ggMax) * radius, 0, Math.PI * 2);
      ctx.stroke();
      ctx.fillText(`${ring} G`, cx + (ring / ggMax) * radius * 0.71 + 3, cy - (ring / ggMax) * radius * 0.71);
    }
    ctx.beginPath();
    ctx.moveTo(cx - radius, cy);
    ctx.lineTo(cx + radius, cy);
    ctx.moveTo(cx, cy - radius);
    ctx.lineTo(cx, cy + radius);
    ctx.stroke();

    for (const side of ["a", "b"]) {
      const s = ggSeries(side);
      if (!s) continue;
      ctx.fillStyle = LAP_COLORS[side];
      ctx.globalAlpha = 0.28;
      for (let i = 0; i < s.lat.length; i += 2) {
        const lat = s.lat[i];
        const lon = s.lon[i];
        if (lat === null || lon === null || !Number.isFinite(lat) || !Number.isFinite(lon)) continue;
        ctx.fillRect(toX(lat) - 1, toY(lon) - 1, 2, 2);
      }
      ctx.globalAlpha = 1;
    }
    const index = state.getCursor();
    for (const side of ["b", "a"]) {
      const s = ggSeries(side);
      if (!s) continue;
      const lat = s.lat[index];
      const lon = s.lon[index];
      if (lat === null || lon === null || !Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      ctx.fillStyle = LAP_COLORS[side];
      ctx.strokeStyle = cssVar("--text", "#ECEDEE");
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(toX(lat), toY(lon), 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
    ctx.fillStyle = muted;
    ctx.textAlign = "center";
    ctx.fillText(t("right →"), cx + radius - 22, cy + 12);
    ctx.fillText(t("accel ↑"), cx + 26, cy - radius + 8);
  }

  const schedule = coalesce(drawGG);
  const unsubscribe = state.subscribe((what) => {
    if (what !== "cursor") return;
    updateCards();
    schedule();
  });
  const stopResize = onResize(element, schedule);
  updateCards();
  schedule();

  return {
    element,
    redraw: drawGG,
    destroy() {
      unsubscribe();
      stopResize();
    },
    _updateCards: updateCards,
  };
}
