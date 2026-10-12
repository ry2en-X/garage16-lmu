// analysis/tyres.js — the tyre window (V0.9.3): tread temperatures inner / middle / outer,
// pressure, wear and brake temperature per wheel.
//
//   * Lap averages come with the lap header (per-lap figures stored at upload,
//     server/lap_insights.py) — shown at once, no extra request.
//   * "At the cursor" loads the zone/pressure/brake channels on demand (like the physics
//     window) and then follows the shared cursor without asking the server again.
//
// Tread zones: LMU reports the three tread temperatures left / centre / right in the car's
// frame (rFactor 2 header: "not inside/outside"). Inner/outer follows from the wheel's side:
// on the left wheels the left zone is the outer one, on the right wheels the inner one.

import { el } from "../format.js";
import { formatValue, LAP_COLORS, rampColor, WHEEL_LABEL, WHEELS } from "./model.js";

import { t } from "../i18n.js";

export const ZONES = ["inner", "middle", "outer"];
const LEFT_WHEELS = new Set(["fl", "rl"]);

// [left, centre, right] -> {inner, middle, outer} for one wheel (pure)
export function zonesFromLcr(wheel, lcr) {
  if (!Array.isArray(lcr) || lcr.length !== 3) return null;
  const [l, c, r] = lcr;
  return LEFT_WHEELS.has(wheel) ? { inner: r, middle: c, outer: l } : { inner: l, middle: c, outer: r };
}

export const TYRE_CHANNELS = [
  ...WHEELS.flatMap((w) => ZONES.map((z) => `tire_temp_${z}_${w}`)),
  ...WHEELS.map((w) => `tire_pressure_${w}`),
  ...WHEELS.map((w) => `brake_temp_${w}`),
];

// Colour of a tread temperature: cold blue .. hot red over 40..120 °C (a fixed scale, so two
// laps and two moments look the same for the same temperature).
const TEMP_LO = 40;
const TEMP_HI = 120;
const tempColor = (v) => (v === null || v === undefined || !Number.isFinite(v) ? "transparent" : rampColor(Math.min(Math.max((v - TEMP_LO) / (TEMP_HI - TEMP_LO), 0), 1)));

function zoneStrip(wheel) {
  // outer zone towards the car's outside: left wheels show outer|middle|inner, right inner|middle|outer
  const order = LEFT_WHEELS.has(wheel) ? ["outer", "middle", "inner"] : ["inner", "middle", "outer"];
  const cells = {};
  const strip = el("div", { class: "tyre__zones" }, order.map((z) => {
    cells[z] = el("span", { class: `tyre__zone tyre__zone--${z}`, title: { inner: t("inner"), middle: t("middle"), outer: t("outer") }[z] }, "—");
    return cells[z];
  }));
  return { strip, cells };
}

function wheelTile(wheel) {
  const zones = zoneStrip(wheel);
  const lines = { pressure: el("span", {}, "—"), wear: el("span", {}, "—"), brake: el("span", {}, "—") };
  const tile = el("div", { class: "tyre", "data-wheel": wheel }, [
    el("div", { class: "tyre__title" }, WHEEL_LABEL[wheel]),
    zones.strip,
    el("div", { class: "tyre__line" }, [el("span", { class: "tyre__name" }, t("Pressure")), lines.pressure]),
    el("div", { class: "tyre__line" }, [el("span", { class: "tyre__name" }, t("Wear / lap")), lines.wear]),
    el("div", { class: "tyre__line" }, [el("span", { class: "tyre__name" }, t("Brake")), lines.brake]),
  ]);
  const set = ({ zones: z, pressure, wear, brake }) => {
    for (const name of ZONES) {
      const v = z ? z[name] : null;
      zones.cells[name].textContent = formatValue(v, "", false);
      zones.cells[name].style.background = tempColor(v);
    }
    lines.pressure.textContent = formatValue(pressure, "kPa");
    lines.wear.textContent = wear === null || wear === undefined ? "—" : `${wear.toFixed(2)} %`;
    lines.brake.textContent = brake === null || brake === undefined || !Number.isFinite(brake) ? "—" : `${Math.round(brake)} °C`;
  };
  return { tile, set };
}

function carCard(side, label) {
  const tiles = {};
  for (const w of WHEELS) tiles[w] = wheelTile(w);
  const card = el("div", { class: "tyres__car", style: `border-top:3px solid ${LAP_COLORS[side]};` }, [
    el("div", { class: "tyres__label", style: `color:${LAP_COLORS[side]};` }, label),
    el("div", { class: "tyres__grid" }, [tiles.fl.tile, tiles.fr.tile, tiles.rl.tile, tiles.rr.tile]),
  ]);
  return { card, tiles };
}

// Pure: lap averages of one lap from its stored figures
export function averagesFor(stats, wheel) {
  const w = stats && stats.wheels && stats.wheels[wheel];
  if (!w) return { zones: null, pressure: null, wear: null, brake: null };
  return {
    zones: zonesFromLcr(wheel, w.temp_lcr_c) || (w.temp_avg_c !== undefined ? { inner: null, middle: w.temp_avg_c, outer: null } : null),
    pressure: w.pressure_avg_kpa ?? null,
    wear: w.wear_used_pct ?? null,
    brake: w.brake_max_c ?? null,
  };
}

export function renderTyres(widget, ctx) {
  const base = ctx.baseData || ctx.data;
  const sides = base.laps.b ? ["a", "b"] : ["a"];
  const lapLabel = (side) => (side === "a" ? t("Lap 1") : t("Lap 2"));

  // --- lap averages (from the lap headers)
  const averages = el("div", { class: "tyres__cars" }, sides.map((side) => {
    const car = carCard(side, lapLabel(side));
    const stats = base.laps[side].stats;
    for (const w of WHEELS) car.tiles[w].set(averagesFor(stats, w));
    return car.card;
  }));
  const hasAverages = sides.some((side) => base.laps[side].stats && base.laps[side].stats.wheels);

  // --- at the cursor (loaded when opened)
  const cursorBody = el("div", { class: "tyres__cursor" });
  const details = el("details", { class: "tyres__details" }, [el("summary", {}, t("At the cursor")), cursorBody]);
  let unsubscribe = () => {};
  let loaded = false;
  details.addEventListener("toggle", async () => {
    if (!details.open || loaded) return;
    loaded = true;
    cursorBody.appendChild(el("div", { class: "notice" }, t("Loading tyre data…")));
    try {
      await ctx.ensureChannels(TYRE_CHANNELS);
    } catch (err) {
      loaded = false;
      cursorBody.innerHTML = "";
      cursorBody.appendChild(el("div", { class: "notice notice--error" }, err.message));
      return;
    }
    cursorBody.innerHTML = "";
    const cars = {};
    for (const side of sides) {
      cars[side] = carCard(side, lapLabel(side));
      cursorBody.appendChild(cars[side].card);
    }
    const state = ctx.mapState || ctx.state;
    const value = (key, side, i) => {
      const entry = state.data.channels[key];
      const series = entry && entry[side];
      const v = series ? series[i] : null;
      return v === undefined ? null : v;
    };
    const update = () => {
      const i = state.getCursor();
      for (const side of sides) {
        for (const w of WHEELS) {
          const zones = {};
          for (const z of ZONES) zones[z] = value(`tire_temp_${z}_${w}`, side, i);
          const stats = averagesFor(base.laps[side].stats, w);
          cars[side].tiles[w].set({ zones, pressure: value(`tire_pressure_${w}`, side, i), wear: stats.wear, brake: value(`brake_temp_${w}`, side, i) });
        }
      }
    };
    unsubscribe = state.subscribe((what) => {
      if (what === "cursor" || what === "data") update();
    });
    update();
  });

  const element = el("div", { class: "card tyres" }, [
    el("h2", {}, t("Tyres & brakes")),
    hasAverages
      ? el("div", { class: "subtitle" }, t("Lap averages: tread temperature inner · middle · outer (°C), pressure, tyre wear over the lap, hottest brake disc."))
      : el("div", { class: "subtitle" }, t("Lap averages are calculated when a lap is uploaded (server 0.9.3 or newer) — open “At the cursor” for this lap.")),
    hasAverages ? averages : null,
    details,
  ]);
  return { element, destroy: () => unsubscribe() };
}
