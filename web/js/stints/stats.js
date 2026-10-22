// stints/stats.js — the Stint Analyzer's statistics (V0.9.4). Pure functions, no DOM: tested in
// Node (tests/js/v094_stints.test.mjs).
//
// Principles (the same as everywhere in Garage16):
//   * measured and estimated are kept apart — every estimate says so (`estimated: true`)
//   * no prediction from too little data: forecasts need MIN_FORECAST_LAPS clean laps, a
//     correlation MIN_CORRELATION_LAPS, the fuel-corrected degradation laps of several fuel
//     levels that are not just "the same as tyre age" (collinearity check)
//   * robust statistics where a single bad lap would otherwise dominate: median, MAD,
//     Theil–Sen slope (the median of all pairwise slopes; Sen 1968, Theil 1950 — a textbook
//     method, implemented here from its definition, no third-party code)

export const MIN_FORECAST_LAPS = 5;
export const MIN_CORRELATION_LAPS = 8;
export const MIN_DEGRADATION_LAPS = 4;
export const MIN_SEPARATION_LAPS = 8;
export const DEFAULT_OUTLIER_PCT = 107;
export const NOT_CLEAN_DEFAULT = ["invalid", "outlap", "inlap", "pit_stop", "traffic", "blue_flag", "race_start", "outlier"];
// the flags a user can switch in the filter (outlier is a threshold, not a switch)
export const SWITCHABLE_FLAGS = ["invalid", "outlap", "inlap", "pit_stop", "traffic", "blue_flag", "race_start", "wet", "damage"];
const COLLINEAR_R = 0.9;

const finite = (v) => typeof v === "number" && Number.isFinite(v);
const nums = (values) => values.filter(finite);

export function median(values) {
  const v = nums(values).slice().sort((a, b) => a - b);
  if (!v.length) return null;
  const mid = Math.floor(v.length / 2);
  return v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2;
}

export function mean(values) {
  const v = nums(values);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
}

export function stdev(values) {
  const v = nums(values);
  if (v.length < 2) return null;
  const m = mean(v);
  return Math.sqrt(v.reduce((a, b) => a + (b - m) ** 2, 0) / (v.length - 1));
}

export function mad(values) {
  const m = median(values);
  return m === null ? null : median(nums(values).map((v) => Math.abs(v - m)));
}

export function quantile(values, q) {
  const v = nums(values).slice().sort((a, b) => a - b);
  if (!v.length) return null;
  const pos = (v.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return v[lo] + (v[hi] - v[lo]) * (pos - lo);
}

// Rolling average over the last `window` values (fewer at the start); null where the value is null.
export function rolling(values, window) {
  const out = [];
  for (let i = 0; i < values.length; i += 1) {
    if (!finite(values[i])) { out.push(null); continue; }
    const part = nums(values.slice(Math.max(0, i - window + 1), i + 1));
    out.push(part.length ? part.reduce((a, b) => a + b, 0) / part.length : null);
  }
  return out;
}

// Medians of the first, middle and last third (in the order driven) — fewer than 3 values: null.
export function thirds(values) {
  const v = nums(values);
  if (v.length < 3) return null;
  const size = v.length / 3;
  const part = (k) => v.slice(Math.round(k * size), Math.round((k + 1) * size));
  return [median(part(0)), median(part(1)), median(part(2))];
}

// The fastest run of `n` consecutive values (average), with its start index.
export function bestWindow(values, n) {
  let best = null;
  for (let i = 0; i + n <= values.length; i += 1) {
    const part = values.slice(i, i + n);
    if (!part.every(finite)) continue;
    const avg = part.reduce((a, b) => a + b, 0) / n;
    if (best === null || avg < best.average) best = { average: avg, start: i, n };
  }
  return best;
}

// Theil–Sen line through (x, y): slope = median of pairwise slopes, intercept = median of
// y - slope * x. `spread` = MAD of the residuals (for the forecast band).
export function theilSen(xs, ys) {
  const pts = xs.map((x, i) => [x, ys[i]]).filter(([x, y]) => finite(x) && finite(y));
  if (pts.length < 2) return null;
  const slopes = [];
  for (let i = 0; i < pts.length; i += 1) {
    for (let j = i + 1; j < pts.length; j += 1) {
      const dx = pts[j][0] - pts[i][0];
      if (dx !== 0) slopes.push((pts[j][1] - pts[i][1]) / dx);
    }
  }
  if (!slopes.length) return null;
  const slope = median(slopes);
  const intercept = median(pts.map(([x, y]) => y - slope * x));
  const residuals = pts.map(([x, y]) => y - (intercept + slope * x));
  // V0.9.7: Sen's 95 % confidence interval of the slope (distribution-free: the ranks of the
  // pairwise slopes around the median, C = 1.96 * sqrt(n(n-1)(2n+5)/18); Sen 1968, ties ignored)
  slopes.sort((p, q) => p - q);
  const n = pts.length;
  const c = 1.96 * Math.sqrt((n * (n - 1) * (2 * n + 5)) / 18);
  const lo = Math.floor((slopes.length - c) / 2);
  const hi = Math.ceil((slopes.length + c) / 2) - 1;
  const ci = lo >= 0 && hi < slopes.length && lo <= hi ? [slopes[lo], slopes[hi]] : null;
  return { slope, intercept, n, spread: mad(residuals), ci };
}

// Two-sided 95 % quantile of Student's t with df degrees of freedom (Cornish–Fisher expansion
// around the normal quantile; within 0.5 % of the tables for df >= 3).
export function t95(df) {
  if (!(df >= 1)) return null;
  const z = 1.959964;
  return z + (z ** 3 + z) / (4 * df) + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * df ** 2)
    + (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / (384 * df ** 3);
}

export function pearson(xs, ys) {
  const pts = xs.map((x, i) => [x, ys[i]]).filter(([x, y]) => finite(x) && finite(y));
  if (pts.length < 3) return null;
  const mx = mean(pts.map((p) => p[0]));
  const my = mean(pts.map((p) => p[1]));
  let sxy = 0; let sxx = 0; let syy = 0;
  for (const [x, y] of pts) { sxy += (x - mx) * (y - my); sxx += (x - mx) ** 2; syy += (y - my) ** 2; }
  if (sxx === 0 || syy === 0) return null;
  return { r: sxy / Math.sqrt(sxx * syy), n: pts.length };
}

// Least squares y = b0 + b1*x1 + b2*x2 (normal equations, 3x3). Used for lap time ~ tyre age +
// fuel load; null when the two inputs move together (|r| >= 0.9: their effects cannot be told
// apart — then the page says so instead of printing a number).
export function regress2(x1, x2, y) {
  const rows = y.map((v, i) => [x1[i], x2[i], v]).filter((r) => r.every(finite));
  if (rows.length < MIN_SEPARATION_LAPS) return { ok: false, reason: "too_few", n: rows.length };
  const corr = pearson(rows.map((r) => r[0]), rows.map((r) => r[1]));
  if (!corr) return { ok: false, reason: "no_variation", n: rows.length };
  if (Math.abs(corr.r) >= COLLINEAR_R) return { ok: false, reason: "collinear", n: rows.length, r: corr.r };
  const s = Array.from({ length: 3 }, () => [0, 0, 0, 0]);
  for (const [a, b, v] of rows) {
    const x = [1, a, b];
    for (let i = 0; i < 3; i += 1) {
      for (let j = 0; j < 3; j += 1) s[i][j] += x[i] * x[j];
      s[i][3] += x[i] * v;
    }
  }
  // Gauss-Jordan on [X'X | X'y | I] — V0.9.7: the inverse too, for the standard errors
  for (let i = 0; i < 3; i += 1) s[i].push(...[0, 1, 2].map((j) => (i === j ? 1 : 0)));
  for (let c = 0; c < 3; c += 1) {
    let pivot = c;
    for (let r = c + 1; r < 3; r += 1) if (Math.abs(s[r][c]) > Math.abs(s[pivot][c])) pivot = r;
    if (Math.abs(s[pivot][c]) < 1e-12) return { ok: false, reason: "singular", n: rows.length };
    [s[c], s[pivot]] = [s[pivot], s[c]];
    const d = s[c][c];
    for (let k = 0; k < 7; k += 1) s[c][k] /= d;
    for (let r = 0; r < 3; r += 1) {
      if (r === c) continue;
      const f = s[r][c];
      for (let k = 0; k < 7; k += 1) s[r][k] -= f * s[c][k];
    }
  }
  const b = [s[0][3], s[1][3], s[2][3]];
  const inv = [0, 1, 2].map((i) => [s[i][4], s[i][5], s[i][6]]);
  const rss = rows.reduce((sum, [a, c, v]) => sum + (v - (b[0] + b[1] * a + b[2] * c)) ** 2, 0);
  const df = rows.length - 3;
  const sigma2 = df > 0 ? rss / df : null;
  const se = (i) => (sigma2 !== null && inv[i][i] >= 0 ? Math.sqrt(sigma2 * inv[i][i]) : null);
  const q = t95(df);
  const ci = (i) => (se(i) !== null && q ? [b[i] - q * se(i), b[i] + q * se(i)] : null);
  return { ok: true, b0: b[0], b1: b[1], b2: b[2], se1: se(1), se2: se(2), ci1: ci(1), ci2: ci(2), n: rows.length, r: corr.r };
}

// ------------------------------------------------------------------ laps and filters

// Which laps count as clean under the user's filter: none of the excluded flags, and not slower
// than outlierPct % of the median of the remaining laps. Returns a Set of lap ids.
export function cleanIds(laps, { exclude = NOT_CLEAN_DEFAULT, outlierPct = DEFAULT_OUTLIER_PCT, stint = null } = {}) {
  const skip = new Set(exclude.filter((f) => f !== "outlier"));
  const candidates = laps.filter((l) => finite(l.lap_time) && (stint === null || l.stint === stint)
    && !(l.flags || []).some((f) => skip.has(f) && f !== "outlier"));
  const m = median(candidates.map((l) => l.lap_time));
  const limit = m === null || !exclude.includes("outlier") ? Infinity : m * (outlierPct / 100);
  return new Set(candidates.filter((l) => l.lap_time <= limit).map((l) => l.id));
}

// Pace figures over the clean laps (in the order driven).
export function paceSummary(laps, clean, { window = 5 } = {}) {
  const times = laps.filter((l) => clean.has(l.id)).map((l) => l.lap_time);
  if (!times.length) return null;
  const best = Math.min(...times);
  return {
    laps: times.length,
    best,
    median: median(times),
    mean: mean(times),
    stdev: stdev(times),
    mad: mad(times),
    within_half_second: times.filter((v) => v - best <= 0.5).length,
    thirds: thirds(times),
    best_window: bestWindow(times, Math.min(window, times.length)),
    trend: times.length >= MIN_DEGRADATION_LAPS ? theilSen(times.map((_, i) => i), times) : null,
    // V0.9.7: consistency WITHOUT the trend. A driver whose laps get 0.05 s slower every lap
    // (tyres) has a large stdev although every lap is exactly on the line — the spread of the
    // residuals around the Theil–Sen trend (robust: MAD × 1.4826 ≈ one sigma) is the
    // lap-to-lap consistency itself.
    consistency: times.length >= MIN_DEGRADATION_LAPS ? detrendedSpread(times) : null,
  };
}

export function detrendedSpread(times) {
  const fit = theilSen(times.map((_, i) => i), times);
  if (!fit) return null;
  const residuals = times.map((v, i) => v - (fit.intercept + fit.slope * i));
  return { sigma: 1.4826 * mad(residuals), n: times.length, trend: fit.slope };
}

// Degradation per stint: Theil–Sen slope of clean lap time over tyre age (s per lap) — the
// combined effect of tyres AND fuel burn-off (a lighter car is faster), labelled so.
export function degradation(laps, clean, stintNumber) {
  const pts = laps.filter((l) => l.stint === stintNumber && clean.has(l.id) && finite(l.tyre_age));
  if (pts.length < MIN_DEGRADATION_LAPS) return { ok: false, n: pts.length };
  const fit = theilSen(pts.map((l) => l.tyre_age), pts.map((l) => l.lap_time));
  return fit ? { ok: true, ...fit } : { ok: false, n: pts.length };
}

// Tyre age and fuel load separated — only possible with laps at different fuel levels for the
// same tyre age (several stints, refuelling): otherwise the two move together.
export function fuelCorrectedDegradation(laps, clean) {
  const pts = laps.filter((l) => clean.has(l.id) && finite(l.tyre_age) && finite(l.fuel_start_l));
  // one stint alone never separates them (age up, fuel down, lap by lap)
  if (new Set(pts.map((l) => l.stint)).size < 2) return { ok: false, reason: "one_stint", n: pts.length };
  const fit = regress2(pts.map((l) => l.tyre_age), pts.map((l) => l.fuel_start_l), pts.map((l) => l.lap_time));
  return fit.ok ? { ...fit, per_lap_tyre: fit.b1, per_litre: fit.b2, per_lap_tyre_ci: fit.ci1, per_litre_ci: fit.ci2, estimated: true } : fit;
}

// ------------------------------------------------------------------ forecast

// The next laps of a stint from its degradation line: predicted lap time with a band of
// ±(1.5 × residual MAD × 1.4826) (≈ a robust 1.5-sigma band). Needs MIN_FORECAST_LAPS clean laps.
export function forecastStint(laps, clean, stintNumber, ahead = [1, 5, 10]) {
  const pts = laps.filter((l) => l.stint === stintNumber && clean.has(l.id) && finite(l.tyre_age));
  if (pts.length < MIN_FORECAST_LAPS) return { ok: false, n: pts.length, needed: MIN_FORECAST_LAPS };
  const fit = theilSen(pts.map((l) => l.tyre_age), pts.map((l) => l.lap_time));
  if (!fit) return { ok: false, n: pts.length, needed: MIN_FORECAST_LAPS };
  const lastAge = Math.max(...laps.filter((l) => l.stint === stintNumber && finite(l.tyre_age)).map((l) => l.tyre_age));
  const band = Math.max(1.5 * 1.4826 * (fit.spread || 0), 0.05);
  return {
    ok: true,
    estimated: true,
    n: pts.length,
    slope: fit.slope,
    points: ahead.map((k) => {
      const age = lastAge + k;
      const value = fit.intercept + fit.slope * age;
      return { laps_ahead: k, tyre_age: age, lap_time: value, low: value - band, high: value + band };
    }),
  };
}

// Fuel and energy: laps left with what is in the tank, the consumption needed to reach the end
// of the session (from the time left and the median clean lap) with a safety margin in laps.
export function fuelPlan({ fuelLeft, perLap, lapTime, timeLeft, lapsLeft, marginLaps = 1 }) {
  const out = { estimated: true };
  if (finite(fuelLeft) && finite(perLap) && perLap > 0) {
    out.laps_possible = fuelLeft / perLap;
    out.laps_possible_with_margin = Math.max(fuelLeft / perLap - marginLaps, 0);
  }
  let remaining = finite(lapsLeft) && lapsLeft > 0 ? lapsLeft : null;
  if (remaining === null && finite(timeLeft) && timeLeft > 0 && finite(lapTime) && lapTime > 0) {
    remaining = Math.ceil(timeLeft / lapTime);   // a timed race: the lap that is started still counts
  }
  if (remaining !== null) {
    out.laps_remaining = remaining;
    if (finite(fuelLeft)) {
      out.target_per_lap = Math.max(fuelLeft - (finite(perLap) ? perLap * marginLaps : 0), 0) / remaining;
      out.enough = finite(perLap) ? perLap * (remaining + marginLaps) <= fuelLeft : null;
      out.missing = finite(perLap) ? Math.max(perLap * (remaining + marginLaps) - fuelLeft, 0) : null;
    }
  }
  return out;
}

// Median consumption per engine map (the map used most of the lap) over the clean laps.
export function byEngineMap(laps, clean, key = "fuel_used_l") {
  const groups = new Map();
  for (const lap of laps) {
    if (!clean.has(lap.id) || !finite(lap[key]) || lap.engine_map === null || lap.engine_map === undefined) continue;
    if (!groups.has(lap.engine_map)) groups.set(lap.engine_map, []);
    groups.get(lap.engine_map).push(lap);
  }
  return [...groups.entries()].sort((a, b) => a[0] - b[0]).map(([map, group]) => ({
    map, laps: group.length, per_lap: median(group.map((l) => l[key])), lap_time: median(group.map((l) => l.lap_time)),
  }));
}

// Median fuel per sector over the clean laps (from the game's sector boundaries).
export function fuelPerSector(laps, clean) {
  const rows = laps.filter((l) => clean.has(l.id) && Array.isArray(l.fuel_sector_l) && l.fuel_sector_l.length === 3);
  if (!rows.length) return null;
  return [0, 1, 2].map((k) => median(rows.map((l) => l.fuel_sector_l[k])));
}

// ------------------------------------------------------------------ vehicle state and traffic

export const STATE_KEYS = [
  { key: "tyre_temp_avg", label: "Tyre temperature (avg)", unit: "°C" },
  { key: "tyre_pressure_avg", label: "Tyre pressure (avg)", unit: "kPa" },
  { key: "wear_left_avg", label: "Tread left (avg)", unit: "%" },
  { key: "fuel_start_l", label: "Fuel at lap start", unit: "L" },
  { key: "track_temp", label: "Track temperature", unit: "°C" },
];

// How each measured state goes along with the lap time over the clean laps — a correlation, not
// a cause (a hotter tyre and a slower lap can both come from traffic, for example).
export function stateCorrelations(laps, clean) {
  const rows = laps.filter((l) => clean.has(l.id));
  return STATE_KEYS.map((s) => {
    const xs = rows.map((l) => l[s.key]);
    const n = xs.filter(finite).length;
    if (n < MIN_CORRELATION_LAPS) return { ...s, ok: false, n };
    const c = pearson(xs, rows.map((l) => l.lap_time));
    return c ? { ...s, ok: true, r: c.r, n: c.n } : { ...s, ok: false, n };
  });
}

// Traffic: lap time of the laps with a measured car close ahead vs the clean median. The
// difference is measured; that the traffic CAUSED it is not proven (the page says so).
export function trafficSummary(laps, clean) {
  const ref = median(laps.filter((l) => clean.has(l.id)).map((l) => l.lap_time));
  const traffic = laps.filter((l) => (l.flags || []).includes("traffic") && finite(l.lap_time)
    && !(l.flags || []).some((f) => ["invalid", "inlap", "outlap", "pit_stop"].includes(f)));
  const blue = laps.filter((l) => (l.flags || []).includes("blue_flag"));
  return {
    traffic_laps: traffic.length,
    traffic_median: median(traffic.map((l) => l.lap_time)),
    difference: ref !== null && traffic.length ? median(traffic.map((l) => l.lap_time)) - ref : null,
    blue_flag_laps: blue.length,
    places_gained: laps.reduce((a, l) => a + (finite(l.places_gained) ? l.places_gained : 0), 0),
    places_lost: laps.reduce((a, l) => a + (finite(l.places_lost) ? l.places_lost : 0), 0),
    measured: laps.some((l) => finite(l.traffic_ahead_share)),
  };
}

// ------------------------------------------------------------------ strategy calculator

// Every plan with stops from the minimum up to minimum + 3: stint lengths (laps as even as
// possible), fuel per stint, the time of each stop, the estimated race time. All inputs are
// numbers the user can see and change; nothing is assumed beyond them.
//   input: { raceLaps | raceMinutes, lapTime, fuelPerLap, tank, minStops, maxStintLaps,
//            maxStintMinutes, refuelling, refuelRate, tyreChange, mandatoryTyreChange, tyreSets,
//            minStop, pitLaneLoss, marginLaps, fuelStart }
export function strategyPlans(input) {
  const lapTime = input.lapTime;
  if (!finite(lapTime) || lapTime <= 0) return { ok: false, reason: "lap_time" };
  if (!finite(input.fuelPerLap) || input.fuelPerLap <= 0) return { ok: false, reason: "fuel" };
  const timed = !finite(input.raceLaps) && finite(input.raceMinutes);
  const raceLapsFor = (stops, stopTime) => (finite(input.raceLaps)
    ? input.raceLaps
    : Math.ceil(Math.max(input.raceMinutes * 60 - stops * stopTime, 0) / lapTime));
  if (!finite(input.raceLaps) && !timed) return { ok: false, reason: "length" };
  const margin = finite(input.marginLaps) ? input.marginLaps : 1;
  const tank = finite(input.tank) && input.tank > 0 ? input.tank : null;
  const lapsPerTank = tank ? Math.floor(tank / input.fuelPerLap - margin) : Infinity;
  const maxByTime = finite(input.maxStintMinutes) ? Math.floor((input.maxStintMinutes * 60) / lapTime) : Infinity;
  const maxStint = Math.min(finite(input.maxStintLaps) ? input.maxStintLaps : Infinity, maxByTime,
    input.refuelling === false ? Infinity : lapsPerTank);
  const minStops = Math.max(finite(input.minStops) ? input.minStops : 0, input.mandatoryTyreChange ? 1 : 0);
  const plans = [];
  for (let stops = minStops; stops <= minStops + 3; stops += 1) {
    // stop time depends on the fuel needed, which depends on the stint length: two passes
    let stopTime = finite(input.pitLaneLoss) ? input.pitLaneLoss : 0;
    let plan = null;
    for (let pass = 0; pass < 2; pass += 1) {
      const total = raceLapsFor(stops, stopTime);
      const stints = stops + 1;
      const base = Math.floor(total / stints);
      const lengths = Array.from({ length: stints }, (_, i) => base + (i < total % stints ? 1 : 0));
      const fuel = lengths.map((n) => (n + margin) * input.fuelPerLap);
      const fuelStart = finite(input.fuelStart) ? input.fuelStart : (tank ? Math.min(fuel[0], tank) : fuel[0]);
      const stopsDetail = lengths.slice(1).map((n, i) => {
        // what is left at the stop is the margin; fill up to what the next stint needs
        const add = input.refuelling === false ? 0 : Math.max(Math.min(fuel[i + 1], tank || Infinity) - margin * input.fuelPerLap, 0);
        const refuelS = input.refuelling === false || !finite(input.refuelRate) ? null : add / input.refuelRate;
        const tyresS = input.mandatoryTyreChange || finite(input.tyreChange) ? (input.tyreChange || 0) : 0;
        const stationary = Math.max(refuelS || 0, tyresS, finite(input.minStop) ? input.minStop : 0);
        return { after_stint: i + 1, fuel_added: input.refuelling === false ? 0 : add, refuel_s: refuelS, stationary_s: stationary,
          total_s: stationary + (finite(input.pitLaneLoss) ? input.pitLaneLoss : 0) };
      });
      const avgStop = stopsDetail.length ? mean(stopsDetail.map((s) => s.total_s)) : 0;
      const feasible = lengths.every((n) => n <= maxStint)
        && (input.refuelling !== false || !tank || total * input.fuelPerLap + margin * input.fuelPerLap <= tank)
        && (!finite(input.tyreSets) || stints <= input.tyreSets || !input.mandatoryTyreChange);
      plan = {
        stops, laps: total, stint_laps: lengths, fuel_per_stint: fuel, fuel_start: fuelStart, stops_detail: stopsDetail,
        race_time_s: total * lapTime + stopsDetail.reduce((a, s) => a + s.total_s, 0),
        feasible, estimated: true, timed,
        why: feasible ? null : (lengths.some((n) => n > maxStint) ? "stint_too_long" : "tank"),
      };
      if (Math.abs(avgStop - stopTime) < 0.01) break;
      stopTime = avgStop;
    }
    plans.push(plan);
  }
  // timed race: more laps in the same time is better; fixed laps: less time is better
  const ok = plans.filter((p) => p.feasible);
  const best = ok.length
    ? ok.reduce((a, b) => (timed ? (b.laps > a.laps || (b.laps === a.laps && b.race_time_s < a.race_time_s) ? b : a)
      : (b.race_time_s < a.race_time_s ? b : a)))
    : null;
  return { ok: true, plans, best: best ? best.stops : null, max_stint_laps: Number.isFinite(maxStint) ? maxStint : null };
}

// The measured pit loss to put into the calculator: median of the measured stops' losses.
export function measuredPitLoss(pitStops) {
  return median((pitStops || []).filter((s) => s.kind === "stop").map((s) => s.loss_s));
}
