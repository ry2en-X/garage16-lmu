// V0.9.4 — the Stint Analyzer, in jsdom.
//   * statistics: robust figures, Theil–Sen, rolling averages, thirds, filters, degradation,
//     the fuel/tyre separation refusing collinear data, forecasts only with enough laps,
//     fuel plan, strategy calculator, correlations only with enough laps
//   * rendering: the session list, a session dashboard with every section, filters re-render,
//     the strategy calculator takes an admin rule set, nothing says "measured" for an estimate
import { JSDOM } from "jsdom";
import assert from "node:assert/strict";

const dom = new JSDOM(`<!doctype html><body><div id="root"></div></body>`, { url: "http://localhost/#/stints", pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.localStorage = dom.window.localStorage;
globalThis.sessionStorage = dom.window.sessionStorage;
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
dom.window.LMU_GARAGE_API_BASE_URL = "";
dom.window.localStorage.setItem("lmu_garage_session", JSON.stringify({ driverId: 1, displayName: "Me", viaCookie: true }));
dom.window.localStorage.setItem("garage16_language", "en");

const calls = [];
globalThis.fetch = async (url) => {
  calls.push(url);
  const u = new URL(url, "http://x");
  const json = (body, status = 200) => ({ ok: status < 400, status, statusText: String(status), json: async () => body, text: async () => JSON.stringify(body) });
  if (u.pathname === "/telemetry/analysis/stints/sessions") return json(SESSIONS);
  if (u.pathname === "/telemetry/analysis/stints/session") return json(SESSION);
  if (u.pathname === "/telemetry/analysis/stints/drivers") return json(DRIVERS);
  if (u.pathname === "/telemetry/analysis/stints/rule-sets") return json(RULES);
  if (u.pathname === "/telemetry/analysis/theoretical-best") return json({ found: false });
  return json({ detail: "unexpected " + u.pathname }, 500);
};

const S = await import("../../web/js/stints/stats.js");
const { chart, ticks } = await import("../../web/js/stints/chart.js");
const { renderStints, parseStintQuery } = await import("../../web/js/pages/stints.js");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const text = (node) => node.textContent.replace(/\s+/g, " ").trim();
const close = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) <= eps, `${a} != ${b}`);

// ================================================================ statistics
close(S.median([3, 1, 2]), 2);
close(S.median([4, 1, 2, 3]), 2.5);
assert.equal(S.median([]), null);
close(S.mad([1, 2, 3, 4, 100]), 1);   // one bad lap hardly moves it
assert.deepEqual(S.rolling([1, 2, null, 4], 2), [1, 1.5, null, 4]);   // a filtered lap is skipped, not averaged in
assert.deepEqual(S.thirds([1, 2, 3, 4, 5, 6]), [1.5, 3.5, 5.5]);
assert.equal(S.thirds([1, 2]), null);
assert.deepEqual(S.bestWindow([5, 1, 1, 9, 0.5, 0.5], 2), { average: 0.5, start: 4, n: 2 });

// Theil–Sen ignores an outlier that least squares would follow
const xs = [0, 1, 2, 3, 4, 5, 6, 7];
const ys = xs.map((x) => 90 + 0.1 * x);
ys[3] = 99;
const ts = S.theilSen(xs, ys);
close(ts.slope, 0.1, 1e-9);
close(ts.intercept, 90, 1e-9);

// clean laps: flags and the outlier threshold
const LAPS = [];
for (let i = 1; i <= 12; i += 1) {
  LAPS.push({ id: i, lap_number: i, lap_time: 90 + 0.05 * i, stint: i <= 6 ? 1 : 2, tyre_age: i <= 6 ? i : i - 6,
    fuel_start_l: 60 - 2 * i, fuel_used_l: 2, flags: [], engine_map: i % 2 ? 1 : 2, fuel_sector_l: [0.7, 0.6, 0.7],
    tyre_temp_avg: 80 + i, track_temp: 30, wear_left_avg: 100 - i, tyre_pressure_avg: 180 });
}
LAPS[2].flags = ["traffic"]; LAPS[2].lap_time = 91.5;
LAPS[6].flags = ["outlap", "pit_stop"]; LAPS[6].lap_time = 115;
LAPS[9].lap_time = 100;   // a mistake: the outlier rule takes it out
let clean = S.cleanIds(LAPS);
assert.ok(!clean.has(3) && !clean.has(7) && !clean.has(10) && clean.has(1));
assert.ok(S.cleanIds(LAPS, { exclude: S.NOT_CLEAN_DEFAULT.filter((f) => f !== "traffic") }).has(3), "the user can let traffic laps count");
assert.ok(S.cleanIds(LAPS, { exclude: S.NOT_CLEAN_DEFAULT.filter((f) => f !== "outlier") }).has(10));
assert.ok(S.cleanIds(LAPS, { outlierPct: 115 }).has(10));

const pace = S.paceSummary(LAPS, clean);
assert.equal(pace.laps, 9);
close(pace.best, 90.05);
assert.ok(pace.trend && pace.trend.slope > 0);

const deg = S.degradation(LAPS, clean, 1);
assert.ok(deg.ok);
close(deg.slope, 0.05, 1e-9);
assert.equal(S.degradation(LAPS, clean, 2).ok, true);
assert.equal(S.degradation(LAPS.slice(0, 3), clean, 1).ok, false, "fewer than 4 clean laps: no degradation");

// tyre age and fuel go down together in one stint -> refused; two stints with refuelling -> separated
const one = S.fuelCorrectedDegradation(LAPS.filter((l) => l.stint === 1).concat(LAPS.filter((l) => l.stint === 1)), clean);
assert.equal(one.ok, false);
const MIX = [];
for (let s = 0; s < 3; s += 1) {
  for (let age = 1; age <= 6; age += 1) {
    const fuel = [70, 40, 55][s] - 2 * age;
    MIX.push({ id: 100 + s * 10 + age, lap_time: 88 + 0.06 * age + 0.03 * fuel, tyre_age: age, fuel_start_l: fuel, stint: s + 1, flags: [] });
  }
}
const sep = S.fuelCorrectedDegradation(MIX, new Set(MIX.map((l) => l.id)));
assert.ok(sep.ok, JSON.stringify(sep));
close(sep.per_lap_tyre, 0.06, 1e-6);
close(sep.per_litre, 0.03, 1e-6);
assert.equal(sep.estimated, true);

// forecast: needs 5 clean laps in the stint
const fc = S.forecastStint(LAPS, clean, 1);
assert.ok(fc.ok && fc.estimated);
assert.equal(fc.points[0].tyre_age, 7);
assert.ok(fc.points[0].low < fc.points[0].lap_time && fc.points[0].lap_time < fc.points[0].high);
assert.equal(S.forecastStint(LAPS, clean, 2).ok, false, "stint 2 has 4 clean laps");
assert.equal(S.forecastStint(LAPS, clean, 2).needed, S.MIN_FORECAST_LAPS);

// fuel plan
const plan = S.fuelPlan({ fuelLeft: 20, perLap: 2, lapTime: 100, timeLeft: 1050, marginLaps: 1 });
close(plan.laps_possible, 10);
close(plan.laps_possible_with_margin, 9);
assert.equal(plan.laps_remaining, 11);
close(plan.target_per_lap, 18 / 11);
assert.equal(plan.enough, false);
close(plan.missing, 4);
assert.deepEqual(S.fuelPerSector(LAPS, clean), [0.7, 0.6, 0.7]);
assert.equal(S.byEngineMap(LAPS, clean).length, 2);

// correlations only with 8 laps or more
const corr = S.stateCorrelations(LAPS, clean);
const temp = corr.find((c) => c.key === "tyre_temp_avg");
assert.ok(temp.ok && temp.r > 0.5, JSON.stringify(temp));
assert.equal(corr.find((c) => c.key === "track_temp").ok, false, "no variation, no number");
assert.equal(S.stateCorrelations(LAPS.slice(0, 5), clean)[0].ok, false);

// strategy calculator
const strat = S.strategyPlans({ raceLaps: 60, lapTime: 100, fuelPerLap: 3, tank: 90, minStops: 1, refuelling: true,
  refuelRate: 2, tyreChange: 20, pitLaneLoss: 25, marginLaps: 1 });
assert.ok(strat.ok);
assert.equal(strat.max_stint_laps, 29);
const p1 = strat.plans.find((p) => p.stops === 1);
assert.equal(p1.feasible, false, "two stints of 30 laps do not fit in 90 L");
const p2 = strat.plans.find((p) => p.stops === 2);
assert.ok(p2.feasible);
assert.deepEqual(p2.stint_laps, [20, 20, 20]);
close(p2.stops_detail[0].fuel_added, 60);           // 21 laps worth minus the 1-lap margin left
close(p2.stops_detail[0].stationary_s, 30);          // 60 L at 2 L/s > 20 s tyres
close(p2.stops_detail[0].total_s, 55);
assert.equal(strat.best, 2);
assert.ok(strat.plans.every((p) => p.estimated));
const timed = S.strategyPlans({ raceMinutes: 60, lapTime: 100, fuelPerLap: 3, tank: 200, minStops: 0, pitLaneLoss: 30, marginLaps: 1 });
assert.equal(timed.plans[0].laps, 36);
assert.equal(timed.best, 0, "a timed race: no stop, most laps");
assert.equal(S.strategyPlans({ lapTime: 100, fuelPerLap: 3 }).reason, "length");
close(S.measuredPitLoss([{ kind: "stop", loss_s: 30 }, { kind: "stop", loss_s: 34 }, { kind: "drive_through", loss_s: 20 }]), 32);

// chart helpers
assert.deepEqual(ticks(0, 10, 5), [0, 2, 4, 6, 8, 10]);
const svgNode = chart([{ kind: "dots", points: [[1, 90], [2, 91]] }, { kind: "line", points: [[1, 90], [2, null], [3, 92]], color: "#fff" }]);
assert.equal(svgNode.querySelectorAll("circle").length, 2);
assert.equal(svgNode.querySelectorAll("polyline").length, 0, "a gap splits the line; single points draw nothing");

assert.deepEqual(parseStintQuery("#/stints?lap=42&driver=7"), { lap: 42, driver: 7, compareLap: null });
assert.deepEqual(parseStintQuery("#/sessions"), { lap: null, driver: null, compareLap: null });

// ================================================================ rendering
function sessionLaps() {
  const laps = [];
  for (let i = 1; i <= 10; i += 1) {
    const stint = i <= 5 ? 1 : 2;
    laps.push({
      id: 500 + i, lap_number: i, lap_time: 90 + 0.05 * i + (i === 5 ? 8 : 0) + (i === 6 ? 25 : 0), is_valid: true,
      sector_times: [30, 30, 30], condition: "dry", stint, tyre_set: stint, tyre_age: stint === 1 ? i : i - 5,
      pit: { lane_s: i === 5 ? 12 : i === 6 ? 30 : 0, stationary_s: i === 6 ? 20 : 0, starts_in_pits: i === 6, ends_in_pits: i === 5 },
      pitstops: i < 6 ? 0 : 1, place: 3, compound: "Medium", fuel_start_l: 62 - 2 * i, fuel_end_l: 60 - 2 * i, fuel_used_l: 2,
      fuel_added_l: i === 6 ? 30 : null, fuel_sector_l: [0.7, 0.6, 0.7], fuel_capacity_l: 90, energy_used_pct: 3, energy_end_pct: 70 - 3 * i,
      engine_map: 1, tyres_changed: i === 6 ? ["fl", "fr", "rl", "rr"] : null, wear_left_pct: { fl: 99, fr: 99, rl: 99, rr: 99 },
      wear_left_avg: 100 - i, wear_used_avg: 0.5, tyre_temp_c: null, tyre_temp_avg: 80 + i, tyre_pressure_avg: 180, brake_max_c: null,
      traffic_ahead_share: i === 3 ? 0.3 : 0, traffic_behind_share: 0, blue_flag_share: 0, places_gained: 0, places_lost: 0,
      damage: { dent_max: 0, overheating: false, detached: false }, session_left_s: 3000 - 90 * i, max_laps: 0,
      flags: i === 3 ? ["traffic"] : i === 5 ? ["inlap"] : i === 6 ? ["outlap", "pit_stop"] : [],
      clean: ![3, 5, 6].includes(i),
    });
  }
  return laps;
}
const SESSION = {
  laps: sessionLaps(),
  stints: [
    { number: 1, reasons: ["session_start"], lap_ids: [501, 502, 503, 504, 505], first_lap: 1, last_lap: 5, laps: 5, clean_laps: 3, compound: "Medium", tyre_set: 1, wear_left_start: 99, wear_left_end: 95, fuel_start_l: 60, fuel_end_l: 50, duration_s: 460 },
    { number: 2, reasons: ["pit"], lap_ids: [506, 507, 508, 509, 510], first_lap: 6, last_lap: 10, laps: 5, clean_laps: 4, compound: "Medium", tyre_set: 2, wear_left_start: 94, wear_left_end: 90, fuel_start_l: 50, fuel_end_l: 40, duration_s: 480 },
  ],
  pit_stops: [{ kind: "stop", lap_ids: [505, 506], lap_number: 5, lane_s: 42, stationary_s: 20, fuel_added_l: 30, tyres_changed: true, compound_before: "Medium", compound_after: "Medium", loss_s: 33.0, loss_estimated: true }],
  tyre_sets: [{ number: 1, compound: "Medium", stints: [1], wear_left_start: 99, laps: 5, new_at_start: null, wear_left_end: 95 },
    { number: 2, compound: "Medium", stints: [2], wear_left_start: 99, laps: 5, new_at_start: true, wear_left_end: 95 }],
  reference_lap_time: 90.3,
  summary: { laps: 10, clean_laps: 7, valid_laps: 10, best: 90.05, median_clean: 90.3, fuel_per_lap_l: 2, energy_per_lap_pct: 3, fuel_capacity_l: 90,
    fuel_end_l: 40, energy_end_pct: 40, session_left_s: 2100, max_laps: 0, has_pit_data: true, has_context: true, has_traffic: true },
  driver: { id: 1, name: "Me", own: true }, partial: false, track_name: "Le Mans", car_model: "Ferrari 499P", session_type: 10, started: 1700000000, ended: 1700001000,
};
const SESSIONS = {
  driver_id: 1, own: true, total: 1,
  drivers: [{ id: 1, name: "Me", own: true }, { id: 2, name: "Mate", own: false }],
  sessions: [{ track_name: "Le Mans", car_model: "Ferrari 499P", session_type: 10, started: 1700000000, ended: 1700001000, key_lap_id: 510,
    laps: SESSION.laps.map((l) => ({ id: l.id, lap_number: l.lap_number, lap_time: l.lap_time, is_valid: true })),
    consistency: { laps: 10, clean_laps: 7, best: 90.05, median: 90.3, stdev: 0.1, within_half_second: 7 }, fuel_per_lap_l: 2, energy_per_lap_pct: 3, wear_per_lap_pct: 0.5 }],
};
const DRIVERS = { track_name: "Le Mans", valid_laps_only: true, drivers: [
  { driver_id: 1, name: "Me", own: true, consistency: { laps: 10, clean_laps: 9, best: 90.05, median: 90.3, stdev: 0.1, within_half_second: 8 }, fuel_per_lap_l: 2, energy_per_lap_pct: 3, wear_per_lap_pct: 0.5 },
  { driver_id: 2, name: "Mate", own: false, consistency: { laps: 6, clean_laps: 6, best: 89.9, median: 90.1, stdev: 0.08, within_half_second: 6 }, fuel_per_lap_l: 2.1, energy_per_lap_pct: 3.1, wear_per_lap_pct: 0.6 },
] };
const RULES = { rule_sets: [{ id: 9, name: "Sprint 1h", track_name: "Le Mans", rules: { race_minutes: 60, min_pit_stops: 1, refuel_l_per_s: 2, tyre_change_s: 20, pit_lane_loss_s: 30, fuel_margin_laps: 1, mandatory_tyre_change: true } }] };

// --- the list
dom.window.location.hash = "#/stints";
let root = document.getElementById("root");
await renderStints(root);
assert.match(text(root), /Stint Analyzer/);
assert.ok(root.querySelector('a[href="#/stints?lap=510"]'), "each session opens in the Stint Analyzer");
assert.ok(root.querySelector("select"), "team mates can be picked");

// --- one session
dom.window.location.hash = "#/stints?lap=510";
root.innerHTML = "";
await renderStints(root);
await sleep(20);
for (const id of ["stint-pace", "stint-stints", "stint-tyres", "stint-fuel", "stint-state", "stint-traffic", "stint-pits", "stint-forecast", "stint-strategy", "stint-compare", "stint-drivers", "stint-laps"]) {
  assert.ok(root.querySelector(`#${id}`), `section ${id}`);
}
const pits = root.querySelector("#stint-pits");
assert.match(text(pits), /\+33\.0 s/);
assert.ok(pits.querySelector(".stint-tag--estimate"), "the pit loss is marked as an estimate");
assert.match(text(root.querySelector("#stint-forecast")), /No forecast: 4 clean laps in the last stint, at least 5 needed/);
assert.match(text(root.querySelector("#stint-traffic")), /Laps in traffic/);
assert.match(text(root.querySelector("#stint-drivers")), /Mate/);
assert.match(text(root.querySelector("#stint-tyres")), /cannot be told apart|Too few clean laps|did not vary/);
const fuelText = text(root.querySelector("#stint-fuel"));
assert.match(fuelText, /About \d+ laps to go/);
assert.equal(root.querySelector("#stint-pace").querySelectorAll("circle").length, 10);

// filters: letting the traffic lap count changes the clean set (and therefore the forecast)
const trafficBox = [...root.querySelectorAll(".stint-filter")].find((l) => /traffic/.test(l.textContent)).querySelector("input");
trafficBox.checked = false;
trafficBox.dispatchEvent(new dom.window.Event("change"));
assert.equal(root.querySelectorAll(".stint-row--filtered").length, 2, "only in- and out-lap remain filtered");
await sleep(20);

// strategy calculator: measured values are filled in, a rule set fills the rest
const strategy = root.querySelector("#stint-strategy");
const lapInput = [...strategy.querySelectorAll("label")].find((l) => /Lap time \(s\)/.test(l.textContent)).querySelector("input");
assert.ok(Math.abs(Number(lapInput.value) - 90.25) < 0.2, lapInput.value);
const ruleSelect = strategy.querySelector("select");
assert.equal(ruleSelect.options.length, 2);
ruleSelect.value = "9";
ruleSelect.dispatchEvent(new dom.window.Event("change"));
assert.match(text(strategy), /fastest/);
assert.ok(strategy.querySelector(".stint-tag--estimate"));

// admin: the rule-set form leaves empty values out and sends numbers as numbers
const { ruleSetBody } = await import("../../web/js/pages/admin_rules.js");
assert.deepEqual(ruleSetBody({ name: " Sprint ", race_minutes: "60", min_pit_stops: "1.0", max_stint_laps: "", refuel_l_per_s: "2.5", mandatory_tyre_change: true, refuelling: true, track_name: " " }), {
  name: "Sprint", description: null, track_name: null, car_class: null,
  rules: { race_minutes: 60, min_pit_stops: 1, refuel_l_per_s: 2.5, mandatory_tyre_change: true, refuelling: true },
});

console.log("V094-STINTS-OK");
