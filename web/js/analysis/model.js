// analysis/model.js — the logic of the lap-analysis view, with no DOM, no canvas
// and no network, so it can be tested in plain Node (tests/js/analysis_model.test.mjs).
//
// The data shape is the response of GET /telemetry/analysis/compare
// (server/routers/analysis.py): every series has one value per grid point, point i
// lies at distance  grid.start + i * grid.step  metres, and lap A / lap B are
// "a" / "b" in every series (null when that lap lacks the channel).

import { t } from "../i18n.js";

// The built-in colours. They are never modified: LAP_COLORS below is the live set
// the views read, and applyColors() can only ever overwrite its entries with a
// valid #rrggbb value — anything else falls back to the entry here (V0.9.1: a
// stored user configuration can therefore not break the defaults).
export const DEFAULT_COLORS = Object.freeze({ a: "#e0472b", b: "#4fb3e8", delta: "#ecedee" }); // lower case, like every sanitised colour
export const LAP_COLORS = { a: DEFAULT_COLORS.a, b: DEFAULT_COLORS.b, delta: DEFAULT_COLORS.delta };

// V0.9.4: up to two more laps drawn over Lap 1 / Lap 2 ("c", "d" — Lap 3 / Lap 4). Fixed colours and
// line styles, so four laps stay apart on a chart even in black and white: Lap 1 and 2 solid,
// Lap 3 dashed, Lap 4 dotted.
export const EXTRA_COLORS = Object.freeze({ c: "#4ca771", d: "#d8a83e" });
export const LAP_DASH = Object.freeze({ a: [], b: [], c: [7, 4], d: [2, 3] });
export const EXTRA_SIDES = ["c", "d"];
export const lapColor = (side) => LAP_COLORS[side] || EXTRA_COLORS[side] || "#8b9199";
export const lapName = (side) => ({ a: t("Lap 1"), b: t("Lap 2"), c: t("Lap 3"), d: t("Lap 4") })[side] || side;

const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;
export const isHexColor = (value) => typeof value === "string" && HEX_COLOR.test(value);

// `colors`: { a?, b?, delta? } — each entry a valid #rrggbb or it is ignored.
export function applyColors(colors) {
  for (const key of Object.keys(DEFAULT_COLORS)) {
    const wanted = colors && colors[key];
    LAP_COLORS[key] = isHexColor(wanted) ? wanted.toLowerCase() : DEFAULT_COLORS[key];
  }
  return LAP_COLORS;
}

export function clamp(value, lo, hi) {
  return Math.min(Math.max(value, lo), hi);
}

// ---------------------------------------------------------------- recordings from older clients

export const FULL_DATA_CLIENT = "0.9.0";

// What a "basic" lap lacks, in words a driver understands.
export const BASIC_DATA_MISSING = t("suspension, tyre forces and loads, G-forces, driver aids (TC/ABS) and the other extended channels");

// Short tag text for a lap's recording ("full" needs none). `recording` is the
// server's per-lap object from /telemetry/analysis/compare.
export function basicTagText(recording) {
  if (!recording || recording.level !== "basic") return null;
  return recording.client_version ? t("Basic data · client {0}", [recording.client_version]) : t("Basic data · older client");
}

// The explanatory notice shown above the charts when a lap was recorded by a client
// older than 0.9.0; null when there is nothing to say.
export function basicDataNotice(data) {
  const rec = data.recording;
  if (!rec) return null;
  const parts = [];
  for (const side of ["a", "b"]) {
    const r = rec[side];
    const lap = data.laps[side];
    if (!r || !lap || r.level !== "basic") continue;
    const who = `${side === "a" ? t("Lap 1") : t("Lap 2")} (${lap.driver_name})`;
    const version = r.client_version ? t("client {0}", [r.client_version]) : t("an older client");
    parts.push(t("{0} was recorded with {1} and has only the basic channels — {2} of {3}.", [who, version, r.channels_available, r.channels_total]));
  }
  if (!parts.length) return null;
  return t("{0} Missing for those laps: {1}. They only exist for laps recorded with client {2} or newer — update the client and drive the lap again. Speed, throttle, brake, steering, gear, RPM, fuel, tyre pressure/temperature, brake temperature and the track map work as usual.", [parts.join(" "), BASIC_DATA_MISSING, FULL_DATA_CLIENT]);
}

// For lists (leaderboard rows, lap tables): the server's version-based label.
export function isBasicLap(item) {
  return !!item && item.data_level === "basic";
}

// ---------------------------------------------------------------- grid helpers

export function distanceAt(grid, index) {
  return grid.start + grid.step * index;
}

export function endDistance(grid) {
  return distanceAt(grid, grid.count - 1);
}

export function indexForDistance(grid, distance) {
  return clamp(Math.round((distance - grid.start) / grid.step), 0, grid.count - 1);
}

// ---------------------------------------------------------------- scales

// "Nice" tick values (1, 2, 5 x 10^n) between lo and hi.
export function niceTicks(lo, hi, target = 5) {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / Math.max(target, 1);
  const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / magnitude;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * magnitude;
  // Integer multiples of the step, rounded to the step's decimals: adding the step
  // repeatedly accumulates float noise (0.6000000000000001) that would end up in
  // the axis labels.
  const decimals = Math.max(0, -Math.round(Math.log10(magnitude)));
  const ticks = [];
  const last = Math.floor(hi / step + 1e-9);
  for (let k = Math.ceil(lo / step - 1e-9); k <= last; k++) {
    ticks.push(Number((k * step).toFixed(decimals)));
  }
  return ticks;
}

// Y range of one or several series (nulls ignored); a fixed range wins.
export function valueRange(seriesList, fixed) {
  if (fixed) return [fixed[0], fixed[1]];
  let lo = Infinity;
  let hi = -Infinity;
  for (const series of seriesList) {
    if (!series) continue;
    for (const v of series) {
      if (v === null || v === undefined || !Number.isFinite(v)) continue;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  if (!Number.isFinite(lo)) return [0, 1];
  if (hi === lo) {
    const pad = Math.abs(lo) * 0.05 || 1;
    return [lo - pad, hi + pad];
  }
  const pad = (hi - lo) * 0.06;
  return [lo - pad, hi + pad];
}

// ---------------------------------------------------------------- formatting

export function formatValue(value, unit = "", discrete = false) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  let text;
  if (discrete) {
    text = String(Math.round(value));
  } else {
    const a = Math.abs(value);
    text = a >= 1000 ? value.toFixed(0) : a >= 10 ? value.toFixed(1) : value.toFixed(2);
  }
  return unit ? `${text} ${unit}` : text;
}

// Gear numbers: -1 reverse, 0 neutral.
export function formatChannelValue(key, value, entry) {
  if (key === "gear" && value !== null && value !== undefined && Number.isFinite(value)) {
    const g = Math.round(value);
    return g < 0 ? "R" : g === 0 ? "N" : String(g);
  }
  return formatValue(value, entry ? entry.unit : "", entry ? entry.discrete : false);
}

// "+0.123" / "-0.045" seconds; positive = lap A behind.
export function formatDelta(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
  const sign = seconds > 0 ? "+" : seconds < 0 ? "−" : "±";
  return `${sign}${Math.abs(seconds).toFixed(3)}`;
}

export function formatMetres(m) {
  if (m === null || m === undefined || !Number.isFinite(m)) return "—";
  return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`;
}

// ---------------------------------------------------------------- values at the cursor

// One row per channel for the table beside the map.
export function buildCursorRows(data, index, keys) {
  const rows = [];
  for (const key of keys) {
    const entry = data.channels[key];
    if (!entry) continue;
    const a = entry.a ? entry.a[index] : null;
    const b = entry.b ? entry.b[index] : null;
    const valid = (v) => v !== null && v !== undefined && Number.isFinite(v);
    rows.push({
      key,
      label: entry.label,
      unit: entry.unit,
      group: entry.group,
      a: valid(a) ? a : null,
      b: valid(b) ? b : null,
      diff: valid(a) && valid(b) ? a - b : null,
      aText: formatChannelValue(key, valid(a) ? a : null, entry),
      bText: formatChannelValue(key, valid(b) ? b : null, entry),
      discrete: entry.discrete,
    });
  }
  return rows;
}

// Time gained/lost per sector (A minus B; positive = A lost time). null when the
// sector boundaries are unknown.
export function sectorDeltas(data) {
  if (!data.delta || !data.sectors || !data.sectors.a || data.sectors.a.length !== 2) return null;
  const [d1, d2] = data.sectors.a;
  const at = (distance) => data.delta[indexForDistance(data.grid, distance)];
  const last = data.delta[data.grid.count - 1];
  const v1 = at(d1);
  const v2 = at(d2);
  if (![v1, v2, last].every((v) => v !== null && Number.isFinite(v))) return null;
  return [v1, v2 - v1, last - v2];
}

// ---------------------------------------------------------------- colour scales (track map)

const RAMP = [
  [0.0, [44, 92, 210]],
  [0.25, [43, 172, 205]],
  [0.5, [76, 167, 113]],
  [0.75, [216, 168, 62]],
  [1.0, [224, 71, 43]],
];

function mix(c1, c2, ratio) {
  return [0, 1, 2].map((k) => Math.round(c1[k] + (c2[k] - c1[k]) * ratio));
}

// 0..1 -> blue ... green ... yellow ... red
export function rampColor(position) {
  const x = clamp(position, 0, 1);
  for (let i = 1; i < RAMP.length; i++) {
    if (x <= RAMP[i][0]) {
      const [p0, c0] = RAMP[i - 1];
      const [p1, c1] = RAMP[i];
      const [r, g, b] = mix(c0, c1, (x - p0) / (p1 - p0));
      return `rgb(${r},${g},${b})`;
    }
  }
  return "rgb(224,71,43)";
}

// -1..1 -> green (lap A faster) ... grey ... red (lap A slower)
export function divergingColor(level) {
  const x = clamp(level, -1, 1);
  const grey = [140, 145, 153];
  const target = x < 0 ? [76, 190, 120] : [224, 71, 43];
  const [r, g, b] = mix(grey, target, Math.abs(x));
  return `rgb(${r},${g},${b})`;
}

// Values -> 0..1 positions for rampColor, with the range taken from the data
// (2nd..98th percentile, so one spike does not flatten the whole colour scale).
export function normalizeForColor(values, fixed) {
  const finite = values.filter((v) => v !== null && v !== undefined && Number.isFinite(v));
  if (!finite.length) return values.map(() => null);
  let lo;
  let hi;
  if (fixed) {
    [lo, hi] = fixed;
  } else {
    const sorted = [...finite].sort((p, q) => p - q);
    lo = sorted[Math.floor((sorted.length - 1) * 0.02)];
    hi = sorted[Math.ceil((sorted.length - 1) * 0.98)];
  }
  if (!(hi > lo)) return values.map((v) => (v === null || v === undefined || !Number.isFinite(v) ? null : 0.5));
  return values.map((v) => (v === null || v === undefined || !Number.isFinite(v) ? null : clamp((v - lo) / (hi - lo), 0, 1)));
}

// ---------------------------------------------------------------- map geometry

function bounds(xs, ys) {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (let i = 0; i < xs.length; i++) {
    const x = xs[i];
    const y = ys[i];
    if (x === null || y === null || x === undefined || y === undefined) continue;
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
  }
  return Number.isFinite(minX) ? { minX, maxX, minY, maxY } : null;
}

// Fits the track into a width x height box (equal scale on both axes, Y pointing up).
export function fitView(xs, ys, width, height, pad = 16) {
  const b = bounds(xs, ys);
  if (!b) return null;
  const spanX = Math.max(b.maxX - b.minX, 1e-6);
  const spanY = Math.max(b.maxY - b.minY, 1e-6);
  const scale = Math.min((width - 2 * pad) / spanX, (height - 2 * pad) / spanY);
  const offsetX = (width - spanX * scale) / 2;
  const offsetY = (height - spanY * scale) / 2;
  return {
    scale,
    toX: (x) => offsetX + (x - b.minX) * scale,
    toY: (y) => height - (offsetY + (y - b.minY) * scale),
  };
}

// Index of the point nearest to (px, py) in the same coordinates as xs/ys; -1 if none.
export function nearestIndex(xs, ys, px, py, maxDistance = Infinity) {
  let best = -1;
  let bestD = maxDistance * maxDistance;
  for (let i = 0; i < xs.length; i++) {
    const x = xs[i];
    const y = ys[i];
    if (x === null || y === null || x === undefined || y === undefined) continue;
    const d = (x - px) * (x - px) + (y - py) * (y - py);
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  }
  return best;
}

// ---------------------------------------------------------------- physics view

export const WHEELS = ["fl", "fr", "rl", "rr"];
export const WHEEL_LABEL = { fl: "FL", fr: "FR", rl: "RL", rr: "RR" };

// Channels the physics view needs (requested from the server when it is opened).
export const PHYSICS_CHANNELS = [
  "lat_g", "long_g", "yaw_rate",
  ...WHEELS.map((w) => `susp_travel_${w}`),
  ...WHEELS.map((w) => `tire_load_${w}`),
  ...WHEELS.map((w) => `slip_angle_${w}`),
  ...WHEELS.map((w) => `tire_temp_${w}`),
  ...WHEELS.map((w) => `grip_fract_${w}`),
];

function pick(data, key, side, index) {
  const entry = data.channels[key];
  const series = entry ? entry[side] : null;
  if (!series) return null;
  const v = series[index];
  return v === null || v === undefined || !Number.isFinite(v) ? null : v;
}

// Everything the physics view draws for one lap at one grid point. Missing
// channels (a lap recorded before V0.9.0) are null, never 0.
export function physicsSnapshot(data, index, side) {
  const wheels = {};
  for (const w of WHEELS) {
    wheels[w] = {
      travel: pick(data, `susp_travel_${w}`, side, index),
      load: pick(data, `tire_load_${w}`, side, index),
      slip: pick(data, `slip_angle_${w}`, side, index),
      temp: pick(data, `tire_temp_${w}`, side, index),
      sliding: pick(data, `grip_fract_${w}`, side, index),
    };
  }
  return {
    wheels,
    latG: pick(data, "lat_g", side, index),
    longG: pick(data, "long_g", side, index),
    yaw: pick(data, "yaw_rate", side, index),
  };
}

// ---------------------------------------------------------------- shared view state

// One object every part of the page (charts, map, table, physics) listens to, so
// moving the cursor or zooming in one place moves it everywhere.
export function createViewState(data) {
  const full = [data.grid.start, endDistance(data.grid)];
  const MIN_SPAN = 25; // metres
  let cursor = 0;
  let pinned = false;
  let view = full.slice();
  const listeners = new Set();
  const emit = (what) => listeners.forEach((fn) => fn(what));

  const state = {
    data,
    full,
    getCursor: () => cursor,
    getView: () => view.slice(),
    isPinned: () => pinned,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    // Hover: follows the mouse unless a position is pinned.
    hoverCursor(index) {
      if (pinned) return;
      state.setCursor(index);
    },
    setCursor(index) {
      const next = clamp(Math.round(index), 0, data.grid.count - 1);
      if (next === cursor) return;
      cursor = next;
      emit("cursor");
    },
    // Click: pin there (click the same spot again to release).
    pinCursor(index) {
      const next = clamp(Math.round(index), 0, data.grid.count - 1);
      if (pinned && next === cursor) {
        pinned = false;
      } else {
        pinned = true;
        cursor = next;
      }
      emit("cursor");
    },
    unpin() {
      if (!pinned) return;
      pinned = false;
      emit("cursor");
    },
    // V0.9.1 zoom refinement: swap in data of the SAME laps on a different grid (the
    // zoomed stretch at a finer step, or the whole-lap overview again). What the user
    // is looking at does not move: the view stays in metres, the cursor keeps its
    // distance (re-expressed as an index of the new grid), a pinned cursor stays pinned.
    // `full` (the extent of the whole lap) is deliberately not recomputed.
    replaceData(next) {
      const distance = distanceAt(data.grid, cursor);
      data = next;
      state.data = next;
      cursor = clamp(indexForDistance(next.grid, distance), 0, next.grid.count - 1);
      emit("data");
    },
    setView(lo, hi) {
      let a = lo;
      let b = hi;
      if (b - a < MIN_SPAN) {
        const mid = (a + b) / 2;
        a = mid - MIN_SPAN / 2;
        b = mid + MIN_SPAN / 2;
      }
      const span = Math.min(b - a, full[1] - full[0]);
      a = clamp(a, full[0], full[1] - span);
      b = a + span;
      if (a === view[0] && b === view[1]) return;
      view = [a, b];
      emit("view");
    },
    zoom(factor, anchorDistance) {
      const [lo, hi] = view;
      const anchor = clamp(anchorDistance, lo, hi);
      state.setView(anchor - (anchor - lo) * factor, anchor + (hi - anchor) * factor);
    },
    pan(deltaDistance) {
      state.setView(view[0] + deltaDistance, view[1] + deltaDistance);
    },
    resetView() {
      state.setView(full[0], full[1]);
    },
  };
  return state;
}
