// V0.9.3 — the new analysis windows and the sessions page, in jsdom.
//   * pure logic: coaching hints (thresholds, which values a hint names), event list,
//     histogram plan, tread zones by wheel side, session lap bars, layout rules for the
//     histogram window type
//   * rendering: damper + histogram windows draw from /distribution, the events window lists
//     findings and jumps the cursor, coaching names the measured values, tyres show lap
//     averages without a request, the track map draws event markers, the sessions page and
//     the theoretical best
import { JSDOM } from "jsdom";
import assert from "node:assert/strict";

const dom = new JSDOM(`<!doctype html><body><div id="root"></div></body>`, { url: "http://localhost/#/analysis", pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.localStorage = dom.window.localStorage;
globalThis.sessionStorage = dom.window.sessionStorage;
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
dom.window.LMU_GARAGE_API_BASE_URL = "";
dom.window.localStorage.setItem("lmu_garage_session", JSON.stringify({ driverId: 1, displayName: "Me", viaCookie: true }));
dom.window.localStorage.setItem("garage16_language", "en");

const drawn = { fillRect: 0, fill: 0, fillText: [] };
const ctx2d = new Proxy({}, {
  get(target, prop) {
    if (prop in target) return target[prop];
    if (prop === "fillRect") return () => { drawn.fillRect++; };
    if (prop === "fill") return () => { drawn.fill++; };
    if (prop === "fillText") return (text) => { drawn.fillText.push(String(text)); };
    return () => {};
  },
  set(target, prop, value) { target[prop] = value; return true; },
});
dom.window.HTMLCanvasElement.prototype.getContext = function getContext() { return ctx2d; };
dom.window.HTMLCanvasElement.prototype.getBoundingClientRect = function rect() { return { left: 0, top: 0, width: 600, height: 150, right: 600, bottom: 150 }; };

const calls = [];
globalThis.fetch = async (url) => {
  calls.push(url);
  const u = new URL(url, "http://x");
  const json = (body, status = 200) => ({ ok: status < 400, status, statusText: String(status), json: async () => body, text: async () => JSON.stringify(body) });
  if (u.pathname === "/telemetry/analysis/sessions") return json(SESSIONS);
  if (u.pathname === "/telemetry/analysis/theoretical-best") return json(IDEAL);
  return json({ detail: "unexpected " + u.pathname }, 500);
};

const { coachingPlan, cornerHints, COACH } = await import("../../web/js/analysis/coaching.js");
const { collectEvents, verdictOf, createEventsStore, renderEvents } = await import("../../web/js/analysis/events.js");
const { histogramPlan, renderDamper, renderHistogram, DAMPER_CHANNELS } = await import("../../web/js/analysis/distribution.js");
const { zonesFromLcr, averagesFor, renderTyres } = await import("../../web/js/analysis/tyres.js");
const { renderCoaching } = await import("../../web/js/analysis/coaching.js");
const { addWidget, defaultLayout, sanitizeLayout, setChannel, toStored, requiredChannels, SINGLE_TYPES } = await import("../../web/js/analysis/layout.js");
const { MODULES } = await import("../../web/js/analysis/modules.js");
const { createViewState } = await import("../../web/js/analysis/model.js");
const { createTrackMap } = await import("../../web/js/analysis/trackmap.js");
const { lapBars, renderSessions } = await import("../../web/js/pages/sessions.js");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const text = (node) => node.textContent.replace(/\s+/g, " ").trim();

// ================================================================ coaching (pure)
const corner = (n, delta, a, b, extra = {}) => ({ n, delta, start: n * 1000, entry: n * 1000 + 100, apex: n * 1000 + 300, end: n * 1000 + 900, a, b, ...extra });
const lapVals = (brake, min, throttle, exit) => ({ brake, min, throttle, exit, entry: 250, apex: 0, time: 10 });

let hints = cornerHints(corner(1, 0.2, lapVals(980, 90, 1420, 150), lapVals(1000, 95, 1400, 156)));
assert.deepEqual(hints.map((h) => h.kind), ["brake_later", "min_speed", "throttle_earlier", "exit_speed"]);
assert.match(hints[0].text, /Brake 20 m later/);
assert.match(hints[1].text, /5\.0 km\/h more minimum speed \(95\.0 instead of 90\.0/);
assert.match(hints[2].text, /full throttle 20 m earlier/);

hints = cornerHints(corner(2, 0.1, lapVals(1004, 94, 1405, 154), lapVals(1000, 95, 1400, 156)));
assert.deepEqual(hints, [], "differences below the noise thresholds give no hint");

hints = cornerHints(corner(3, 0.15, lapVals(1012, 90, 1400, 156), lapVals(1000, 95, 1400, 156)));
assert.deepEqual(hints.map((h) => h.kind), ["overbraking"], "late braking that costs apex speed is called what it is");

hints = cornerHints(corner(4, 0.1, lapVals(1000, 95, null, 156), lapVals(1000, 95, 1400, 156)), [{ at: 1050, wheel: "fl", peak: -40 }]);
assert.deepEqual(hints.map((h) => h.kind), ["throttle_missing", "lockup"]);
assert.match(hints[1].text, /^FL locks in the braking zone/);

const plan = coachingPlan([
  corner(1, 0.25, lapVals(980, 90, 1420, 150), lapVals(1000, 95, 1400, 156)),
  corner(2, 0.01, lapVals(1000, 95, 1400, 156), lapVals(1000, 95, 1400, 156)),
  corner(3, -0.12, lapVals(1000, 97, 1390, 158), lapVals(1000, 95, 1400, 156)),
  corner(4, 0.05, lapVals(1000, 93, 1400, 156), lapVals(1000, 95, 1400, 156)),
], [{ at: 1050, wheel: "fr", peak: -30 }, { at: 9999, wheel: "rl", peak: -30 }]);
assert.deepEqual(plan.losses.map((l) => l.corner.n), [1, 4], "biggest loss first; small ones are not listed");
assert.deepEqual(plan.strengths.map((c) => c.n), [3]);
assert.ok(Math.abs(plan.potential - 0.31) < 1e-9);
assert.ok(plan.losses[0].hints.some((h) => h.kind === "lockup"), "a lock-up inside the corner's stretch is attached to it");
assert.equal(COACH.minLoss, 0.03);

// ================================================================ events (pure)
const EVENTS = {
  track_limits: {
    a: { status: "invalid", findings: [
      { code: "track_limits_points", severity: "invalid", source: "track_limits_steps", at: 1200.5, detail: { added: 1, total: 1 } },
      { code: "wheels_off", severity: "info", source: "surface_type", at: 300, until: 320, detail: { wheels: 2 } },
    ], signals: ["track_limits_steps"], review_status: null },
    b: { status: "review", findings: [{ code: "edge_exceeded", severity: "review", source: "track_edge", at: 800, until: 850, detail: { beyond_m: 2.5, seconds: 0.4 } }], signals: [], review_status: "pending" },
  },
  wheels: { a: [{ type: "lockup", wheel: "fl", at: 1100, until: 1110, t: 40, seconds: 0.2, peak: -35 }], b: [] },
};
const events = collectEvents(EVENTS);
assert.deepEqual(events.map((e) => [e.side, e.code]), [["a", "wheels_off"], ["b", "edge_exceeded"], ["a", "lockup"], ["a", "track_limits_points"]], "ordered along the lap");
assert.equal(events[3].text, "Track-limits steps from LMU (+1; this lap)");  // V0.9.3.1 wording
assert.equal(events[2].text, "FL locks under braking (-35 %, 0.2 s)");
assert.deepEqual(verdictOf(EVENTS, "b"), { status: "review", review: "pending", signals: [] });
assert.deepEqual(collectEvents(null), []);

// ================================================================ histogram plan, tread zones, bars (pure)
const DIST = {
  channels: Object.fromEntries(DAMPER_CHANNELS.map((key) => [key, {
    label: key, unit: "mm/s", group: "Dampers", discrete: false, edges: [-100, -50, 0, 50, 100],
    a: { pct: [10, 40, 35, 15], below: 0, above: 0, stats: { p50: 0, p5: -80, p95: 80, max: 120 }, damper: { bump: 50, rebound: 50, low_bump: 35, high_bump: 15, low_rebound: 40, high_rebound: 10, knee: 25 } },
    b: { pct: [5, 45, 40, 10], below: 0, above: 0, stats: { p50: 0, p5: -70, p95: 70, max: 100 }, damper: { bump: 50, rebound: 50, low_bump: 40, high_bump: 10, low_rebound: 45, high_rebound: 5, knee: 25 } },
  }])),
  knee: 25,
};
const hp = histogramPlan(DIST.channels.damper_vel_fl, ["a", "b"]);
assert.equal(hp.bins, 4);
assert.equal(hp.top, 45);
assert.deepEqual(hp.series.map((s) => s.side), ["a", "b"]);
assert.equal(histogramPlan({ edges: null }, ["a"]), null);

assert.deepEqual(zonesFromLcr("fl", [100, 90, 80]), { inner: 80, middle: 90, outer: 100 }, "left wheel: left zone is outer");
assert.deepEqual(zonesFromLcr("rr", [100, 90, 80]), { inner: 100, middle: 90, outer: 80 }, "right wheel: right zone is outer");
assert.deepEqual(averagesFor({ wheels: { fl: { temp_lcr_c: [90, 85, 80], pressure_avg_kpa: 170, wear_used_pct: 0.12, brake_max_c: 550 } } }, "fl"),
  { zones: { inner: 80, middle: 85, outer: 90 }, pressure: 170, wear: 0.12, brake: 550 });

const bars = lapBars([
  { id: 1, lap_number: 1, lap_time: 92, is_valid: true },
  { id: 2, lap_number: 2, lap_time: 90, is_valid: true },
  { id: 3, lap_number: 3, lap_time: 130, is_valid: true },
  { id: 4, lap_number: 4, lap_time: 89, is_valid: false },
], { median: 91 });
assert.equal(bars[1].best, true, "the best VALID lap is marked");
assert.equal(bars[2].outlier, true);
assert.notEqual(bars[3].best, true, "an invalid lap is never the best, even when it is the fastest");
assert.ok(bars[0].height > bars[1].height, "slower = taller");

// ================================================================ layout: new window types
assert.ok(SINGLE_TYPES.includes("coaching") && SINGLE_TYPES.includes("events") && SINGLE_TYPES.includes("damper") && SINGLE_TYPES.includes("tyres"));
let layout = defaultLayout();
layout = addWidget(layout, { type: "histogram", channel: "gear" });
layout = addWidget(layout, { type: "histogram", channel: "gear" });
assert.equal(layout.widgets.filter((w) => w.type === "histogram").length, 1, "one histogram per channel");
layout = addWidget(layout, { type: "chart", channel: "gear" });
assert.equal(layout.widgets.filter((w) => w.channel === "gear").length, 2, "a chart and a histogram of the same channel may coexist");
const hist = layout.widgets.find((w) => w.type === "histogram");
assert.equal(hist.size, "medium");
assert.equal(setChannel(layout, hist.id, "speed").widgets.find((w) => w.id === hist.id).channel, "speed");
assert.deepEqual(requiredChannels(layout), ["speed", "throttle", "brake", "gear"], "histograms fetch their own data, not /compare channels");
const roundTrip = sanitizeLayout(toStored(layout));
assert.ok(roundTrip.widgets.some((w) => w.type === "histogram" && w.channel === "gear"), "stored and loaded again");
assert.ok(!sanitizeLayout({ version: 1, layout: [{ type: "histogram" }] }).widgets.some((w) => w.type === "histogram"), "a histogram without a channel is dropped");
for (const type of ["coaching", "events", "tyres", "damper", "histogram"]) assert.ok(MODULES.some((m) => m.type === type), `registered: ${type}`);

// ================================================================ rendering the windows
const N = 100;
const ramp = (f) => Array.from({ length: N }, (_, i) => f(i));
const data = {
  grid: { start: 0, step: 20, count: N },
  laps: {
    a: { id: 1, driver_id: 1, driver_name: "Me", lap_time: 90, sector_times: [30, 30, 30], is_valid: true,
         stats: { wheels: { fl: { temp_lcr_c: [92, 88, 84], pressure_avg_kpa: 172.5, wear_used_pct: 0.11, brake_max_c: 612 } } } },
    b: { id: 2, driver_id: 2, driver_name: "Rival", lap_time: 89, sector_times: [29, 30, 30], is_valid: true, stats: null },
  },
  delta: ramp((i) => i * 0.01), time: { a: ramp((i) => i), b: ramp((i) => i) },
  channels: {}, unavailable: [], sectors: { a: [], b: [] },
  map: { x_a: ramp((i) => Math.cos(i / 16)), y_a: ramp((i) => Math.sin(i / 16)), x_b: ramp((i) => Math.cos(i / 16)), y_b: ramp((i) => Math.sin(i / 16)) },
  corners: [corner(1, 0.25, lapVals(980, 90, 1420, 150), lapVals(1000, 95, 1400, 156))],
};
const state = createViewState(data);
const pinned = [];
const store = createEventsStore(async () => EVENTS);
await store.ready;
const ctx = {
  state, data, baseData: data, mapState: state, catalog: { channels: [{ key: "gear", label: "Gear", unit: "", group: "x", discrete: true }] },
  events: store, ensureChannels: async () => {}, pinAt: (i) => { pinned.push(i); state.pinCursor(i); },
  pinAtDistance: (m) => pinned.push(`m${m}`),
  fetchDistribution: async (opts) => {
    calls.push(`dist:${opts.channels.join(",")}`);
    if (opts.channels[0] === "gear") {
      return { channels: { gear: { label: "Gear", unit: "", discrete: true, edges: [2.5, 3.5, 4.5, 5.5], a: { pct: [20, 50, 30], below: 0, above: 0, stats: { p50: 4, p5: 3, p95: 5, max: 5 } }, b: null } }, knee: 25 };
    }
    return DIST;
  },
};
const root = document.getElementById("root");

// damper
const damper = renderDamper({ type: "damper", size: "large" }, ctx);
root.appendChild(damper.element);
// drawing waits for an animation frame: wait for it instead of a fixed time (was flaky under load)
for (let i = 0; i < 100 && drawn.fillRect === 0; i++) await sleep(10);
assert.equal(damper.element.querySelectorAll("canvas[data-wheel]").length, 4, "one histogram per wheel");
assert.ok(drawn.fillRect > 0, "bars are drawn");
assert.ok(text(damper.element).includes("High-speed bump"));
assert.equal(damper.element.querySelector('[data-row="bump"]').querySelectorAll("td").length, 1 + 4 * 2);
assert.ok(calls.includes(`dist:${DAMPER_CHANNELS.join(",")}`), "one request for all four wheels");

// histogram
const hw = renderHistogram({ type: "histogram", size: "medium", channel: "gear" }, ctx);
root.appendChild(hw.element);
await sleep(20);
assert.ok(text(hw.element).startsWith("Histogram — Gear"));
assert.ok(text(hw.element).includes("Lap 1: median 4"));

// events: findings + jump
const ev = renderEvents({ type: "events" }, ctx);
root.appendChild(ev.element);
assert.equal(ev.element.querySelectorAll(".event-row").length, 4);
assert.ok(ev.element.querySelector('[data-status="invalid"]'), "lap 1 verdict shown");
ev.element.querySelector('[data-code="track_limits_points"]').click();
assert.equal(pinned.at(-1), 60, "clicking an event pins the cursor at its place (1200.5 m / 20 m)");

// coaching
const co = renderCoaching({ type: "coaching" }, ctx);
root.appendChild(co.element);
assert.ok(text(co.element).includes("Brake 20 m later"));
assert.ok(text(co.element).includes("Corner 1"));
co.element.querySelector(".coach__corner").click();
assert.equal(pinned.at(-1), "m1300", "a corner zooms in and pins its apex");
const coNoB = renderCoaching({ type: "coaching" }, { ...ctx, baseData: { ...data, laps: { a: data.laps.a, b: null } } });
assert.ok(text(coNoB.element).includes("Choose a second lap"));

// tyres: lap averages from the header, no request
const before = calls.length;
const ty = renderTyres({ type: "tyres" }, ctx);
root.appendChild(ty.element);
assert.equal(calls.length, before, "lap averages need no request");
const fl = ty.element.querySelector('.tyres__cars [data-wheel="fl"]');
assert.equal(fl.querySelector(".tyre__zone--outer").textContent, "92.0", "FL outer = left zone");
assert.equal(fl.querySelector(".tyre__zone--inner").textContent, "84.0");
assert.ok(text(fl).includes("172.5 kPa") && text(fl).includes("0.11 %") && text(fl).includes("612 °C"));

// track map draws the event markers (invalid + review + the lock-up; not the info entry)
drawn.fill = 0;
const map = createTrackMap(state, { events: store });
root.appendChild(map.element);
map.redraw();
assert.ok(drawn.fill >= 3, "event markers drawn");

// ================================================================ sessions page
const SESSIONS = {
  total: 1,
  sessions: [{
    track_name: "Le Mans", car_model: "Ferrari 499P", session_type: 10, started: 1700000000, ended: 1700000600,
    laps: [{ id: 11, lap_number: 1, lap_time: 210.5, is_valid: true }, { id: 12, lap_number: 2, lap_time: 209.9, is_valid: true }, { id: 13, lap_number: 3, lap_time: 230.0, is_valid: false }],
    consistency: { laps: 2, clean_laps: 2, best: 209.9, median: 210.2, stdev: 0.424, within_half_second: 2, trend_per_lap: -0.6 },
    fuel_per_lap_l: 4.21, energy_per_lap_pct: 3.1, wear_per_lap_pct: 0.31,
  }],
};
const IDEAL = {
  found: true, laps_without_profile: 1, calculated: true,
  conditions: { selected: "dry", groups: { dry: { laps: 2, best: 209.9 }, wet: { laps: 1, best: 230.1 } }, excluded_laps: 1,
                laps_without_conditions: 0, mixed_on_request: false, grip_levels: [2, 3], track_temp_c: [24.0, 37.5], varying: true },
  result: { time: 209.4, gain: 0.5, reference_lap_id: 12, reference_lap_time: 209.9, step: 50, segments: 272, laps_used: 2,
            from_laps: { 12: 200, 11: 72 }, sector_ideal: { time: 209.7, gain: 0.2, sectors: [70, 70, 69.7] } },
};
const page = document.createElement("div");
root.appendChild(page);
await renderSessions(page);
assert.ok(text(page).includes("Le Mans · Ferrari 499P"));
assert.ok(text(page).includes("± 0.424 s") && text(page).includes("−0.600 s/lap") && text(page).includes("4.21 L"));
assert.equal(page.querySelectorAll(".sess__bar").length, 3);
assert.ok(page.querySelector(".sess__bar--invalid") && page.querySelector(".sess__bar--best").getAttribute("href") === "#/analysis?a=12");
page.querySelector(".sess__ideal-btn").click();
await sleep(20);
assert.ok(text(page).includes("3:29.400"), "the theoretical best");
assert.ok(text(page).includes("272 stretches of 50 m"));
assert.ok(page.querySelector('a[href="#/analysis?a=12&b=11"]'), "compare your best with a lap that holds best stretches");
assert.ok(text(page).includes("1 older lap is not included"));
assert.ok(calls.some((u) => u.includes("theoretical-best") && u.includes("track_name=Le+Mans") && u.includes("car_model=Ferrari+499P")));
// V0.9.3.1: labelled as calculated, conditions never mixed silently
assert.equal(page.querySelector(".sess__ideal-btn").textContent, "Theoretical best (calculated)");
assert.ok(text(page).includes("A calculated figure: stretches of different laps, never driven as one lap."));
assert.ok(text(page).includes("From your dry laps. Not included: 1 lap wet."), text(page));
assert.ok(text(page).includes("Conditions varied between these laps (grip levels 2/3, track 24–37.5 °C)."));

// V0.9.3.1: laps without a car model get no theoretical best (and no request)
const { conditionsNote } = await import("../../web/js/pages/sessions.js");
assert.equal(conditionsNote(null), null);
assert.ok(conditionsNote({ selected: "all", groups: { dry: { laps: 1 }, wet: { laps: 1 } }, mixed_on_request: true }).textContent
  .includes("mixed here on request"));
SESSIONS.sessions[0].car_model = null;
const callsAtSessions = calls.length;
const page2 = document.createElement("div");
root.appendChild(page2);
await renderSessions(page2);
assert.ok(text(page2).includes("No theoretical best: these laps have no car model"));
assert.equal(page2.querySelector(".sess__ideal-btn"), null);
assert.ok(!calls.slice(callsAtSessions).some((u) => u.includes("theoretical-best")));

// V0.9.3.1: findings name their tier and the attribution of late reports
const { findingText, tierText, allFindings } = await import("../../web/js/findings.js");
assert.equal(findingText({ code: "late_track_limits", detail: { seconds_after_line: 2.4, added: 1, points: 0.25, attribution: "ambiguous" } }),
  "Track-limits steps 2.4 s after the finish line (+1 = 0.25 warning points; unclear which lap)");
assert.equal(findingText({ code: "track_limits_points_previous_lap", detail: { added: 2 } }),
  "Track-limits steps right after the line (+2) — counted for the previous lap");
assert.equal(tierText("lmu"), "LMU verdict");
assert.equal(tierText("heuristic"), "measurement (review only)");
assert.deepEqual(allFindings({ findings: [{ code: "a" }], linked: [{ code: "b" }] }).map((f) => f.code), ["a", "b"]);
const tiered = collectEvents({ track_limits: { a: { status: "review", findings: [
  { code: "four_wheels_off", severity: "review", tier: "heuristic", source: "surface_type", at: 10, detail: { seconds: 0.4 } }] } }, wheels: {} });
assert.equal(tiered[0].tierText, "measurement (review only)");

console.log("V093-ANALYSIS-OK");
