// analysis/refine.js — zoom-dependent reloading of finer data (V0.9.1).
//
// The first load is a coarse overview of the whole lap (at most ~1,000–3,000 points per
// series). When the user zooms far in, the points that fall inside the view become too
// few to read exact values from; then — and only then — the browser asks the server for
// just that stretch at a finer step (compare?start&end&step), swaps it in under the same
// view, and swaps the overview back when the user zooms out or pans out of the stretch.
//
// This file is the decision logic and a small adapter, with no DOM and no network, so
// every rule is tested in plain Node. The page (pages/analysis.js) does the fetching.
//
// Hard rules kept here:
//   * nothing is requested for cursor moves — only the VIEW (zoom/pan) can trigger it
//   * nothing is requested unless a clearly finer grid can be had (never refine for show)
//   * requests are sized: the stretch is the view plus a margin, at most ~2,000 points

import { distanceAt, indexForDistance } from "./model.js";

// Mutable on purpose: tests shorten the debounce.
export const REFINE = {
  debounceMs: 300,          // wait for the zoom/pan to settle before asking
  maxZoomedFraction: 0.5,   // the view must be at most this share of the lap (zoomed in >= 2x)
  minVisiblePoints: 600,    // ...and the overview must show fewer points than this inside the view
  targetVisiblePoints: 1200,// aim for about this many points inside the view
  minStep: 0.5,             // metres — the server's own floor
  worthwhile: 0.7,          // the new step must be at most this share of the current one
  margin: 0.25,             // fetch this much extra on each side, so small pans need no new request
  maxPoints: 3000,
  cacheEntries: 6,          // refined stretches kept in memory: going back to a place costs no request
  backoffMs: 60000,         // after a "too many requests" answer, stop asking for this long
};

/**
 * -> { action: "none" }                          nothing to do
 *    { action: "keep" }                          the refined data already fits
 *    { action: "base" }                          go back to the overview
 *    { action: "refine", start, end, step }      ask the server for this stretch
 *
 * view, full: [lo, hi] metres. baseStep: the overview's grid step. current: the refined
 * stretch in use ({ start, end, step }) or null.
 */
export function planRefinement({ view, full, baseStep, current }, opts = REFINE) {
  const idle = current ? { action: "base" } : { action: "none" };
  const span = view[1] - view[0];
  const fullSpan = full[1] - full[0];
  if (!(span > 0) || !(fullSpan > 0) || !(baseStep > 0)) return idle;
  if (span > fullSpan * opts.maxZoomedFraction) return idle;
  if (span / baseStep >= opts.minVisiblePoints) return idle;

  const wanted = Math.max(opts.minStep, Math.round((span / opts.targetVisiblePoints) * 100) / 100);
  if (wanted > baseStep * opts.worthwhile) return idle;

  if (current && current.start <= view[0] && current.end >= view[1] && current.step <= wanted * 1.5) {
    return { action: "keep" };
  }
  const margin = span * opts.margin;
  return {
    action: "refine",
    start: Math.floor(Math.max(full[0], view[0] - margin)),
    end: Math.ceil(Math.min(full[1], view[1] + margin)),
    step: wanted,
  };
}

// Does the refined stretch still cover the whole view? (A pan that leaves it must not
// show a half-empty chart for even one frame, so the page checks this on every pan.)
export function covers(current, view) {
  return !!current && current.start <= view[0] && current.end >= view[1];
}

// A server answer is only swapped in if it is internally consistent.
export function isUsable(response, requested) {
  try {
    const g = response.grid;
    if (!g || !Number.isFinite(g.start) || !Number.isFinite(g.step) || !(g.step > 0) || !(g.count >= 2)) return false;
    const n = g.count;
    if (g.start > requested.start + g.step || g.start + g.step * (n - 1) < requested.end - g.step * 2) return false;
    if (!Array.isArray(response.time && response.time.a) || response.time.a.length !== n) return false;
    if (response.delta && response.delta.length !== n) return false;
    // V0.9.4: Lap 3 / Lap 4 drawn over the pair — same grid, or the answer is not used
    for (const side of requested.extraSides || []) {
      if (!Array.isArray(response.time[side]) || response.time[side].length !== n) return false;
    }
    for (const series of Object.values(response.extra_delta || {})) if (series && series.length !== n) return false;
    for (const entry of Object.values(response.channels || {})) {
      for (const side of ["a", "b", "c", "d"]) if (entry[side] && entry[side].length !== n) return false;
    }
    return !!response.laps && !!response.laps.a;
  } catch {
    return false;
  }
}

/**
 * The track map (and anything else that is about the WHOLE lap) keeps working on the
 * overview data while the charts show the refined stretch. This view of the shared state
 * presents `base` as its data and translates cursor indices by DISTANCE between the two
 * grids, so one cursor still drives everything.
 */
export function baseAdapter(state, base) {
  const toBase = (index) => indexForDistance(base.grid, distanceAt(state.data.grid, index));
  const fromBase = (index) => indexForDistance(state.data.grid, distanceAt(base.grid, index));
  return {
    ...state,
    data: base,
    getCursor: () => toBase(state.getCursor()),
    setCursor: (index) => state.setCursor(fromBase(index)),
    hoverCursor: (index) => state.hoverCursor(fromBase(index)),
    pinCursor: (index) => state.pinCursor(fromBase(index)),
  };
}
