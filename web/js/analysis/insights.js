// analysis/insights.js — what the delta curve and the sector times say, in numbers
// a driver can act on (V0.9.1). Pure functions over the /compare response.
//
// Convention (same as the server): delta(d) = time(A, d) − time(B, d); POSITIVE means
// lap A is BEHIND lap B at that point. So A GAINS time where the curve falls and
// LOSES time where it rises.

import { distanceAt, indexForDistance, sectorDeltas } from "./model.js";

const finite = (v) => v !== null && v !== undefined && Number.isFinite(v);

// Length of the stretch "biggest gain / loss" looks at: about a corner, scaled to the lap
// (1/40 of it), never shorter than 100 m or longer than 400 m. Le Mans (~13.6 km) -> 340 m,
// a 4 km track -> 100 m.
export const FACT_WINDOW = Object.freeze({ fraction: 1 / 40, min: 100, max: 400 });

export function factWindowMetres(lapLength) {
  return Math.min(FACT_WINDOW.max, Math.max(FACT_WINDOW.min, lapLength * FACT_WINDOW.fraction));
}

/**
 * Where on the lap A gained the most time, where it lost the most, and the net result.
 *
 *   gain / loss: { seconds (>0), from, to, at, index } — the stretch of FACT_WINDOW length (see
 *   factWindowMetres) over which the delta fell (gain) or rose (loss) the most; metres; `at`
 *   is its middle and `index` the grid index there (so a click can move the shared cursor);
 *   null when A gained / lost nothing anywhere.
 *   net: the lap's real difference, A minus B at the end (negative = A finished ahead) — the same
 *   sign convention as the delta chart and the sector table; `gain.seconds` / `loss.seconds` are
 *   magnitudes (>0), the UI shows a gain as negative and a loss as positive.
 *
 * Why a fixed-length stretch: the largest rise between ANY two points of a lap that loses
 * time steadily is simply "most of the lap", which tells a driver nothing. A stretch about
 * the size of a corner points at the place to work on.
 */
export function deltaFacts(data) {
  if (!data || !data.delta || !data.grid || data.grid.count < 2) return null;
  // The server measures them on a fixed 0.5 m grid (V0.9.1 review): the same numbers on every
  // screen. Computed here from the chart's grid only for older / zoomed responses.
  const exact = data.delta_facts;
  if (exact && finite(exact.net)) {
    const side = (s) => (s ? {
      seconds: s.seconds, from: s.from, to: s.to, at: (s.from + s.to) / 2,
      index: indexForDistance(data.grid, (s.from + s.to) / 2),
    } : null);
    return { gain: side(exact.gain), loss: side(exact.loss), net: exact.net };
  }
  const delta = data.delta;
  const grid = data.grid;
  const n = grid.count;

  let last = null;
  for (let i = n - 1; i >= 0; i--) {
    if (finite(delta[i])) {
      last = delta[i];
      break;
    }
  }
  if (last === null) return null;

  const lapLength = grid.step * (n - 1);
  const span = Math.max(1, Math.min(n - 1, Math.round(factWindowMetres(lapLength) / grid.step)));
  let gain = null;
  let loss = null;
  for (let i = 0; i + span < n; i++) {
    const a = delta[i];
    const b = delta[i + span];
    if (!finite(a) || !finite(b)) continue;
    const change = b - a;
    if (change < 0 && (!gain || -change > gain.seconds)) gain = { seconds: -change, i0: i, i1: i + span };
    if (change > 0 && (!loss || change > loss.seconds)) loss = { seconds: change, i0: i, i1: i + span };
  }

  const describe = (s) => {
    if (!s || s.seconds < 0.0005) return null;
    const from = distanceAt(grid, s.i0);
    const to = distanceAt(grid, s.i1);
    return { seconds: s.seconds, from, to, at: (from + to) / 2, index: Math.round((s.i0 + s.i1) / 2) };
  };
  return { gain: describe(gain), loss: describe(loss), net: last };
}

/**
 * Sector rows for the sector table. Uses the game's own sector times of both laps
 * when both are complete (exact); otherwise falls back to the delta curve at the
 * sector boundaries; null when neither is available.
 * -> { rows: [{ label, diff, best, worst }], total, source, bestIndex, worstIndex }
 * diff = A − B, negative = A faster. `best` marks the biggest gain, `worst` the
 * biggest loss; neither is marked when all sectors are equal or only one exists.
 */
export function sectorTable(data) {
  if (!data || !data.laps || !data.laps.a || !data.laps.b) return null;
  const a = data.laps.a.sector_times;
  const b = data.laps.b.sector_times;
  let diffs = null;
  let source = null;
  if (Array.isArray(a) && Array.isArray(b) && a.length === 3 && b.length === 3 && a.every((s) => s > 0) && b.every((s) => s > 0)) {
    diffs = a.map((s, i) => s - b[i]);
    source = "times";
  } else {
    const fromCurve = sectorDeltas(data);
    if (fromCurve) {
      diffs = fromCurve;
      source = "curve";
    }
  }
  if (!diffs) return null;
  const total = diffs.reduce((sum, d) => sum + d, 0);
  let bestIndex = -1;
  let worstIndex = -1;
  diffs.forEach((d, i) => {
    if (bestIndex < 0 || d < diffs[bestIndex]) bestIndex = i;
    if (worstIndex < 0 || d > diffs[worstIndex]) worstIndex = i;
  });
  if (diffs[bestIndex] === diffs[worstIndex]) bestIndex = worstIndex = -1;
  return {
    rows: diffs.map((diff, i) => ({ label: `S${i + 1}`, diff, best: i === bestIndex, worst: i === worstIndex })),
    total,
    source,
    bestIndex,
    worstIndex,
  };
}

// The grid index a distance (metres) belongs to — for "click the fact, move the cursor".
export const indexAt = (data, metres) => indexForDistance(data.grid, metres);

// Roughly as many points as the chart can draw (2 per pixel), within what the server
// and the browser are comfortable with: the spec's 1,000–3,000 per series.
export function chooseMaxPoints(widthPx) {
  const w = Number.isFinite(widthPx) && widthPx > 0 ? widthPx : 0;
  return Math.min(3000, Math.max(1000, Math.round(w * 2)));
}
