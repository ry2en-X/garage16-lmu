// core/chart_core.js — what every chart of Garage16 shares (V1.0.3).
//
// The LapTime Analyzer draws on canvas (thousands of samples per lap, web/js/analysis/charts.js),
// the Stint Analyzer in SVG (a few dozen laps, web/js/stints/chart.js). Both take their axis
// ticks, number formatting, colours and the shared cursor/zoom from here, so an axis or a
// tooltip looks and behaves the same on both pages.

export const finite = (v) => typeof v === "number" && Number.isFinite(v);
export const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

// "Nice" tick values (1, 2, 5 x 10^n) between lo and hi, about `target` of them.
// Integer multiples of the step, rounded to the step's decimals: adding the step repeatedly
// accumulates float noise (0.6000000000000001) that would end up in the labels.
export function niceTicks(lo, hi, target = 5) {
  if (!finite(lo) || !finite(hi)) return [];
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / Math.max(target, 1);
  const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / magnitude;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * magnitude;
  const decimals = Math.max(0, -Math.round(Math.log10(magnitude)));
  const out = [];
  const last = Math.floor(hi / step + 1e-9);
  for (let k = Math.ceil(lo / step - 1e-9); k <= last; k++) out.push(Number((k * step).toFixed(decimals)));
  return out;
}

// Integer ticks (laps, tyre age): never 2.5 laps.
export function integerTicks(lo, hi, target = 8) {
  const ticks = niceTicks(lo, hi, target).filter((v) => Number.isInteger(v));
  if (ticks.length >= 2 || !(hi > lo)) return ticks;
  const out = [];
  for (let v = Math.ceil(lo); v <= Math.floor(hi); v++) out.push(v);
  return out;
}

// A linear map from a domain [d0, d1] onto a range [r0, r1] (and back).
export function linearScale([d0, d1], [r0, r1]) {
  const span = d1 - d0 || 1;
  const map = (v) => r0 + ((v - d0) / span) * (r1 - r0);
  map.invert = (px) => d0 + ((px - r0) / (r1 - r0 || 1)) * span;
  map.domain = [d0, d1];
  map.range = [r0, r1];
  return map;
}

// Lowest / highest finite value of several arrays (or of [x, y] points via `pick`).
export function extent(values, pick = (v) => v) {
  let lo = Infinity;
  let hi = -Infinity;
  for (const item of values) {
    const v = pick(item);
    if (!finite(v)) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return finite(lo) ? [lo, hi] : null;
}

// A padded y range: a few percent above and below, never zero height.
export function paddedRange([lo, hi], share = 0.06) {
  if (lo === hi) return [lo - 0.5, hi + 0.5];
  const pad = (hi - lo) * share;
  return [lo - pad, hi + pad];
}

// The index in a sorted array of numbers closest to `value`.
export function nearestIndex(sorted, value) {
  if (!sorted.length) return -1;
  let lo = 0;
  let hi = sorted.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] <= value) lo = mid; else hi = mid;
  }
  return Math.abs(sorted[hi] - value) < Math.abs(sorted[lo] - value) ? hi : lo;
}

// Colours. Stints / series cycle through SERIES_COLORS; NEUTRAL for what is filtered out;
// WHEEL_COLORS for the four tyres (front warm, rear cool, left solid, right lighter).
export const SERIES_COLORS = ["#E0472B", "#4C9BE8", "#4CA771", "#D8A83E", "#B276E0", "#E07AB0", "#5FC9C2", "#C9C25F"];
export const NEUTRAL = "#5A6068";
export const TEXT_LINE = "#ECEDEE";
export const WHEEL_COLORS = Object.freeze({ fl: "#E0472B", fr: "#F2994A", rl: "#4C9BE8", rr: "#5FC9C2" });
export const seriesColor = (n) => SERIES_COLORS[((n - 1) % SERIES_COLORS.length + SERIES_COLORS.length) % SERIES_COLORS.length];

export function cssVar(name, fallback) {
  try {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  } catch {
    return fallback;
  }
}

/**
 * A group of charts that share one x axis: one cursor (hovering one chart shows the same x on
 * all of them) and one zoom window. Charts subscribe; whoever changes the cursor or the window
 * tells the group, the group tells everybody else.
 *
 *   const group = createChartGroup();
 *   group.subscribe(({ cursor, window }) => redraw());
 *   group.setCursor(12);              // x value, or null to hide
 *   group.setWindow([5, 20]);         // x range, or null for everything
 */
export function createChartGroup() {
  const listeners = new Set();
  const state = { cursor: null, window: null, pinned: false };
  const emit = (source) => { for (const fn of listeners) fn(state, source); };
  return {
    state,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    setCursor(x, source = null) {
      if (state.pinned && source !== "pin") return;
      const next = finite(x) ? x : null;
      if (next === state.cursor) return;
      state.cursor = next;
      emit(source);
    },
    pin(x) {
      state.pinned = finite(x) && !(state.pinned && state.cursor === x);
      state.cursor = finite(x) ? x : null;
      emit("pin");
    },
    setWindow(win, source = null) {
      state.window = Array.isArray(win) && finite(win[0]) && finite(win[1]) && win[1] > win[0] ? [win[0], win[1]] : null;
      emit(source);
    },
  };
}

// Zoom a window [a, b] by `factor` (< 1 = in) around `at`, kept inside [lo, hi] and at least
// `minSpan` wide. Returns null when the result covers everything (= no zoom).
export function zoomWindow([a, b], factor, at, [lo, hi], minSpan = 2) {
  const span = clamp((b - a) * factor, Math.min(minSpan, hi - lo), hi - lo);
  if (span >= hi - lo - 1e-9) return null;
  const share = (at - a) / (b - a || 1);
  let start = at - share * span;
  start = clamp(start, lo, hi - span);
  return [start, start + span];
}

// Move a window by `delta` (x units), kept inside [lo, hi].
export function panWindow([a, b], delta, [lo, hi]) {
  const span = b - a;
  const start = clamp(a + delta, lo, hi - span);
  return [start, start + span];
}
