// DOM test (jsdom) of the lap-analysis page, the charts, the track map, the physics
// view and the circuit library (V0.9.0). The network is a canned fetch; the 2D canvas
// context is a recorder, so the test can tell that something was actually drawn.
//
// V0.9.1 — what changed in THIS file, and why (nothing was loosened to get it green):
//   1. The Garage16 standard layout is now delta + speed/throttle/brake (+ map, values,
//      physics), as specified, instead of eight default channels. The sections that
//      exercise the other V0.9.0 channels (gear, RPM, steering, G) therefore start from
//      a V0.9.0 user's stored channel selection, which the layout store migrates into a
//      layout — so every assertion about those charts still holds, and the migration
//      itself gets tested. The new default is asserted in analysis_layout_page.test.mjs.
//   2. The V0.9.0 "Channels" chooser (checkbox list + Apply) is replaced by the layout
//      editor ("Ansicht anpassen"): the same user goal — add a channel, only that
//      request is made, the choice is remembered — asserted in the new form below.
//   3. The circuit card's "Compare my lap with it" now pairs MY best lap with the fastest
//      one on the server (?quick=wr&track=...) instead of opening the fastest lap as
//      Lap 2 (?b=<id>) and asking me to pick mine.
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

// ---- a 2D context that records what is drawn
const drawn = { stroke: 0, fill: 0, fillText: [], arc: 0, drawImage: 0 };
const ctx = new Proxy({}, {
  get(target, prop) {
    if (prop in target) return target[prop];
    if (prop === "stroke") return () => { drawn.stroke++; };
    if (prop === "fill") return () => { drawn.fill++; };
    if (prop === "arc") return () => { drawn.arc++; };
    if (prop === "drawImage") return () => { drawn.drawImage++; };
    if (prop === "fillText") return (text) => { drawn.fillText.push(String(text)); };
    return () => {};
  },
  set(target, prop, value) { target[prop] = value; return true; },
});
dom.window.HTMLCanvasElement.prototype.getContext = function getContext() { return ctx; };
dom.window.HTMLCanvasElement.prototype.getBoundingClientRect = function rect() {
  return { left: 0, top: 0, width: 600, height: 120, right: 600, bottom: 120 };
};

// ---- canned backend
const N = 200;
const grid = { start: 2, step: 12, count: N };
const ramp = (f) => Array.from({ length: N }, (_, i) => f(i));
const theta = (i) => (i / N) * Math.PI * 2;
const channel = (label, unit, group, a, b, extra = {}) => ({ label, unit, group, discrete: false, fixed_range: null, a, b, ...extra });
function compareBody(requested, { withB = true, aLacks = [], basicA = false } = {}) {
  const base = {
    speed: channel("Speed", "km/h", "Speed & inputs", ramp((i) => 200 + 50 * Math.sin(theta(i))), ramp((i) => 205 + 50 * Math.sin(theta(i)))),
    throttle: channel("Throttle", "%", "Speed & inputs", ramp((i) => (i % 40) * 2.5), ramp((i) => (i % 40) * 2.5), { fixed_range: [0, 100] }),
    brake: channel("Brake", "%", "Speed & inputs", ramp(() => 0), ramp(() => 0), { fixed_range: [0, 100] }),
    gear: channel("Gear", "", "Speed & inputs", ramp((i) => 3 + (i > 100 ? 1 : 0)), ramp(() => 4), { discrete: true }),
    steering: channel("Steering (right = +)", "%", "Speed & inputs", ramp((i) => 40 * Math.sin(theta(i))), ramp((i) => 38 * Math.sin(theta(i)))),
    rpm: channel("Engine speed", "rpm", "Speed & inputs", ramp(() => 8000), ramp(() => 8100)),
    lat_g: channel("Lateral G (right turn = +)", "G", "Motion & G", ramp((i) => 1.5 * Math.sin(theta(i))), ramp((i) => 1.4 * Math.sin(theta(i)))),
    long_g: channel("Longitudinal G (accelerating = +)", "G", "Motion & G", ramp((i) => 0.5 * Math.cos(theta(i))), ramp((i) => 0.5 * Math.cos(theta(i)))),
  };
  const channels = {};
  for (const key of requested) {
    const entry = base[key] || channel(key, "", "Other", ramp(() => 1), ramp(() => 2));
    channels[key] = { ...entry, b: withB ? entry.b : null, a: aLacks.includes(key) ? null : entry.a };
  }
  const timeA = ramp((i) => i * 0.45);
  const timeB = ramp((i) => i * 0.44);
  return {
    grid,
    laps: {
      a: { id: 1, driver_id: 1, driver_name: "Me", track_name: "Le Mans", car_name: "#7", car_class: "Hypercar", car_model: "Ferrari 499P", lap_number: 5, lap_time: 90.0, sector_times: [30.0, 30.5, 29.5], is_valid: true },
      b: withB ? { id: 2, driver_id: 9, driver_name: "Rival", track_name: "Le Mans", car_name: "#8", car_class: "Hypercar", car_model: "Ferrari 499P", lap_number: 2, lap_time: 85.0, sector_times: [28.0, 29.0, 28.0], is_valid: true } : null,
    },
    recording: {
      a: basicA ? { level: "basic", client_version: "0.8.6", channels_available: 31, channels_total: 96 } : { level: "full", client_version: "0.9.0", channels_available: 96, channels_total: 96 },
      b: withB ? { level: "full", client_version: "0.9.0", channels_available: 96, channels_total: 96 } : null,
    },
    delta: withB ? ramp((i) => i * 0.025) : null,
    time: { a: timeA, b: withB ? timeB : null },
    channels,
    unavailable: aLacks.filter((k) => requested.includes(k)),
    map: {
      mirrored: false, a_decided: true,
      x_a: ramp((i) => 500 * Math.cos(theta(i))), y_a: ramp((i) => 300 * Math.sin(theta(i))),
      x_b: withB ? ramp((i) => 500 * Math.cos(theta(i)) + 3) : null, y_b: withB ? ramp((i) => 300 * Math.sin(theta(i)) + 3) : null,
    },
    sectors: { a: [800, 1600], b: withB ? [800, 1600] : [] },
    orientation: { a: { decided_from_data: {} }, b: null },
  };
}
const CATALOG = {
  channels: [
    ["speed", "Speed", "km/h", "Speed & inputs"], ["throttle", "Throttle", "%", "Speed & inputs"], ["brake", "Brake", "%", "Speed & inputs"],
    ["gear", "Gear", "", "Speed & inputs"], ["steering", "Steering (right = +)", "%", "Speed & inputs"], ["rpm", "Engine speed", "rpm", "Speed & inputs"],
    ["lat_g", "Lateral G (right turn = +)", "G", "Motion & G"], ["long_g", "Longitudinal G (accelerating = +)", "G", "Motion & G"],
    ["yaw_rate", "Yaw rate", "°/s", "Motion & G"], ["susp_travel_fl", "Suspension travel FL", "mm", "Suspension"],
  ].map(([key, label, unit, group]) => ({ key, label, unit, group, discrete: key === "gear", fixed_range: null })),
  default: [],
};
const calls = [];
let lapAHasNoPhysics = false;
let lapABasic = false;       // lap A comes from a client < 0.9.0
let rivalBasic = false;      // the leaderboard's number one does
let myLapsBasic = false;     // my own lap list contains an old-client lap
globalThis.fetch = async (url) => {
  calls.push(url);
  const u = new URL(url, "http://x");
  const json = (body, status = 200) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });
  const p = u.pathname;
  if (p === "/telemetry/analysis/channels") return json(CATALOG);
  if (p === "/telemetry/analysis/compare") {
    const requested = (u.searchParams.get("channels") || "").split(",").filter(Boolean);
    const withB = u.searchParams.get("lap_b") !== null;
    return json(compareBody(requested, { withB, basicA: lapABasic, aLacks: lapAHasNoPhysics || lapABasic ? requested.filter((k) => k.startsWith("susp") || k.startsWith("tire") || k.startsWith("slip") || k.startsWith("grip")) : [] }));
  }
  if (p === "/telemetry/analysis/laps/2") return json({ lap: compareBody(["speed"]).laps.b, available_channels: ["speed"] });
  if (p === "/telemetry/analysis/drivers/1/laps") {
    return json({ driver_id: 1, driver_name: "Me", laps: [{ id: 11, lap_number: 3, lap_time: 91.2, is_valid: true, track_name: "Le Mans", car_name: "#7", car_model: "Ferrari 499P", uploaded_at: "2026-10-01T10:00:00", data_level: myLapsBasic ? "basic" : "full" }] });
  }
  if (p === "/telemetry/laps") return json([{ id: 12, track_name: "Le Mans", car_name: "#7", lap_number: 4, lap_time: 92.0, is_valid: true, uploaded_at: "2026-10-01T10:00:00", data_level: myLapsBasic ? "basic" : "full" }]);
  if (p === "/drivers/1/public") return json({ personal_records: [], recent_activity: [] });
  if (p === "/teams/mine") return json([]);
  if (p === "/leaderboard/catalog/class-options") return json([{ name: "Hypercar", lap_count: 2 }, { name: "GT3", lap_count: 0 }]);
  if (p.startsWith("/leaderboard/car/") || p.startsWith("/leaderboard/class/")) {
    return json([
      { lap_id: 2, driver_id: 9, driver_name: "Rival", lap_time: 85.0, sector_times: [28, 29, 28], is_valid: true, uploaded_at: "2026-10-01T10:00:00", data_level: rivalBasic ? "basic" : "full" },
      { lap_id: 1, driver_id: 1, driver_name: "Me", lap_time: 90.0, sector_times: [30, 30.5, 29.5], is_valid: true, uploaded_at: "2026-10-01T10:00:00", data_level: "full" },
    ]);
  }
  if (p === "/telemetry/analysis/circuits") {
    return json({ venues: [
      { venue: "Circuit de la Sarthe (Le Mans)", lap_count: 2, layouts: [{ track_name: "Le Mans", lap_count: 2, driver_count: 2, classes: ["Hypercar"], fastest: { lap_id: 7, lap_time: 85.0, driver_id: 9, driver_name: "Rival", car_model: "Ferrari 499P", car_class: "Hypercar" } }] },
      { venue: "Monza", lap_count: 0, layouts: [] },
    ] });
  }
  if (p === "/telemetry/analysis/circuits/outline") {
    const c = compareBody(["speed"]);
    return json({ track_name: "Le Mans", source_lap_id: 7, length_m: 13626.0, x: c.map.x_a, y: c.map.y_a, grid, sectors: [800, 1600], mirrored: false });
  }
  return json({ detail: `unexpected ${p}` }, 404);
};

const settle = (ms = 30) => new Promise((r) => setTimeout(r, ms));

// A V0.9.0 user's stored channel selection (the old default eight). The layout store turns
// it into a layout (see analysis/layout.js loadLayout), so the V0.9.0 assertions below run
// unchanged against it.
const V090_DEFAULT_CHANNELS = ["speed", "throttle", "brake", "gear", "steering", "rpm", "lat_g", "long_g"];
const useV090Selection = () => {
  localStorage.removeItem("garage16_analysis_layout");
  localStorage.setItem("garage16_analysis_channels", JSON.stringify(V090_DEFAULT_CHANNELS));
};
const { renderAnalysis, parseAnalysisQuery } = await import("../../web/js/pages/analysis.js");
const { renderCircuits } = await import("../../web/js/pages/circuits.js");
const { store } = await import("../../web/js/state.js");

function fresh() {
  document.body.innerHTML = `<main class="panel" id="app"></main>`;
  return document.getElementById("app");
}
const text = (node) => node.textContent.replace(/\s+/g, " ");

// ---------------------------------------------------------------- query parsing
assert.deepEqual(parseAnalysisQuery("#/analysis?a=5&b=7"), { a: 5, b: 7 });
assert.deepEqual(parseAnalysisQuery("#/analysis?b=7"), { a: null, b: 7 });
assert.deepEqual(parseAnalysisQuery("#/analysis"), { a: null, b: null });
assert.deepEqual(parseAnalysisQuery("#/analysis?a=abc&b=-3"), { a: null, b: null });   // never trust the URL
assert.deepEqual(parseAnalysisQuery("#/analysis?a=1.5"), { a: null, b: null });

// ---------------------------------------------------------------- signed out
store.signOut();
let app = fresh();
window.location.hash = "#/analysis?a=1&b=2";
await renderAnalysis(app);
assert.ok(/sign in/i.test(text(app)), "signed-out visitors are asked to sign in");
assert.equal(app.querySelectorAll(".chart").length, 0);
assert.ok(!calls.some((u) => u.includes("/compare")), "no telemetry request without a session");
store.setSession({ driverId: 1, displayName: "Me", viaCookie: true });

// ---------------------------------------------------------------- nothing chosen: pick Lap 1
app = fresh();
window.location.hash = "#/analysis";
await renderAnalysis(app);
await settle();
assert.ok(text(app).includes("Choose Lap 1"));
assert.ok(text(app).includes("#4"), "my laps are listed");
const pickBtn = [...app.querySelectorAll("button")].find((b) => b.textContent === "Select");
pickBtn.click();
assert.equal(window.location.hash, "#/analysis?a=12");

// ---------------------------------------------------------------- only Lap 2 given: pick Lap 1 at that track
app = fresh();
window.location.hash = "#/analysis?b=2";
await renderAnalysis(app);
await settle();
assert.ok(text(app).includes("Rival"), "Lap 2's driver is named");
assert.ok(text(app).includes("Choose Lap 1"));
const tabs = [...app.querySelectorAll(".picker__tabs button")].map((b) => b.textContent);
assert.deepEqual(tabs, ["Leaderboard", "My laps", "Another driver"]);
const mineTab = [...app.querySelectorAll(".picker__tabs button")].find((b) => b.textContent === "My laps");
mineTab.click();
await settle();
assert.ok(text(app).includes("91.2") || text(app).includes("1:31.200"), "my laps at that track listed");
[...app.querySelectorAll("button")].find((b) => b.textContent === "Select").click();
assert.equal(window.location.hash, "#/analysis?a=11&b=2");

// ---------------------------------------------------------------- full comparison
useV090Selection();
for (const key of Object.keys(drawn)) drawn[key] = Array.isArray(drawn[key]) ? [] : 0;
calls.length = 0;
app = fresh();
window.location.hash = "#/analysis?a=1&b=2";
await renderAnalysis(app);
await settle(60);

const compareCall = calls.find((u) => u.includes("/telemetry/analysis/compare"));
const cu = new URL(compareCall, "http://x");
assert.equal(cu.searchParams.get("lap_a"), "1");
assert.equal(cu.searchParams.get("lap_b"), "2");
assert.equal(cu.searchParams.get("channels"), "speed,throttle,brake,gear,steering,rpm,lat_g,long_g");
assert.equal(cu.searchParams.get("step"), "2");

const page = text(app);
assert.ok(page.includes("Me") && page.includes("Rival"));
assert.ok(page.includes("1:30.000") && page.includes("1:25.000"));
assert.ok(page.includes("+5.000 s"), "lap difference shown, positive = Lap 1 slower");
assert.ok(page.includes("S1 +2.000"), "sector differences from the game's own sector times");
assert.equal(app.querySelectorAll(".chart").length, 9, "delta chart + the 8 default channels");
assert.ok(app.querySelector('[data-chart="delta"]'));
assert.ok(app.querySelector('[data-chart="speed"] .chart__val--a'));
assert.ok(app.querySelector('[data-chart="speed"] .chart__val--b'));
assert.ok(app.querySelector(".trackmap__canvas"), "track map present");
assert.ok(app.querySelector(".cursor-table"));
assert.ok(drawn.stroke > 20, "charts and map were drawn");
assert.ok(drawn.fillText.some((t) => t.includes("m")), "distance axis labelled");
assert.ok(!app.querySelector(".phys"), "physics loads only when opened");
assert.equal(app.querySelector('[data-notice="basic-data"]'), null, "full recordings: no basic-data notice");
assert.equal(app.querySelector(".tag--basic"), null, "full recordings: no basic-data tag");

// legends start at the first point, then follow the cursor
const legendA = () => app.querySelector('[data-chart="speed"] .chart__val--a').textContent;
const legendB = () => app.querySelector('[data-chart="speed"] .chart__val--b').textContent;
const state = app._analysis.state;
assert.equal(state.getCursor(), 0);
assert.equal(legendA(), "200.0 km/h");
assert.equal(legendB(), "205.0 km/h");

// hover a chart: x = 330 of 600 -> inside the plot area -> cursor moves, everything follows
const speedCanvas = app.querySelector('[data-chart="speed"] canvas');
speedCanvas.dispatchEvent(new window.MouseEvent("mousemove", { clientX: 330, bubbles: true }));
const moved = state.getCursor();
assert.ok(moved > 60 && moved < 140, `cursor moved to the middle of the lap (got ${moved})`);
assert.notEqual(legendA(), "200.0 km/h");
assert.ok(/m/.test(app.querySelector(".cursor-meta td:nth-child(2)").textContent), "value table shows the distance");
const throttleLegend = app.querySelector('[data-chart="throttle"] .chart__val--a').textContent;
assert.ok(throttleLegend.endsWith("%"));
assert.equal(app.querySelector('[data-chart="gear"] .chart__val--a').textContent, moved > 100 ? "4" : "3");

// click pins; hover no longer moves; clicking the same spot releases
speedCanvas.dispatchEvent(new window.MouseEvent("click", { clientX: 330, bubbles: true }));
assert.equal(state.isPinned(), true);
assert.ok(text(app.querySelector(".cursor-table")).includes("pinned"));
const pinnedAt = state.getCursor();
app.querySelector('[data-chart="rpm"] canvas').dispatchEvent(new window.MouseEvent("mousemove", { clientX: 80, bubbles: true }));
assert.equal(state.getCursor(), pinnedAt);
speedCanvas.dispatchEvent(new window.MouseEvent("click", { clientX: 330, bubbles: true }));
assert.equal(state.isPinned(), false);

// keyboard
window.dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowRight" }));
assert.equal(state.getCursor(), pinnedAt + 1);
window.dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowLeft", shiftKey: true }));
assert.equal(state.getCursor(), pinnedAt - 9);

// wheel zoom narrows every chart's range; double-click resets
const before = state.getView();
speedCanvas.dispatchEvent(new window.WheelEvent("wheel", { clientX: 330, deltaY: -100, bubbles: true, cancelable: true }));
const after = state.getView();
assert.ok(after[1] - after[0] < before[1] - before[0], "zoomed in");
speedCanvas.dispatchEvent(new window.MouseEvent("dblclick", { bubbles: true }));
assert.deepEqual(state.getView(), before);

// drag pans (and does not pin)
speedCanvas.dispatchEvent(new window.WheelEvent("wheel", { clientX: 300, deltaY: -100, bubbles: true, cancelable: true }));
const zoomed = state.getView();
speedCanvas.dispatchEvent(new window.MouseEvent("mousedown", { clientX: 300, button: 0, bubbles: true }));
speedCanvas.dispatchEvent(new window.MouseEvent("mousemove", { clientX: 250, bubbles: true }));
window.dispatchEvent(new window.MouseEvent("mouseup"));
speedCanvas.dispatchEvent(new window.MouseEvent("click", { clientX: 250, bubbles: true }));
assert.ok(state.getView()[0] > zoomed[0], "dragging left moved the window forward along the lap");
assert.equal(state.isPinned(), false, "a drag is not a click");
state.resetView();

// track map: hovering near a point moves the cursor; far from the line it does not
const mapCanvas = app.querySelector(".trackmap__canvas");
const cursorBeforeMap = state.getCursor();
mapCanvas.dispatchEvent(new window.MouseEvent("mousemove", { clientX: 5, clientY: 5, bubbles: true }));
assert.equal(state.getCursor(), cursorBeforeMap, "pointer far from the track leaves the cursor alone");

// colour-by control on the map
const mode = app.querySelector("#mapColorMode");
assert.ok([...mode.options].some((o) => o.value === "delta"));
assert.ok([...mode.options].some((o) => o.value === "speed"));
mode.value = "speed";
mode.dispatchEvent(new window.Event("change"));
await settle();
assert.ok(text(app.querySelector(".trackmap__legend")).includes("km/h"), "legend shows the colour scale's range");

// layout editor (replaces the V0.9.0 channel chooser): add a window -> only the missing
// channel is requested, the new window appears, the layout is remembered
const customize = app.querySelector(".analysis-customize");
assert.ok(customize, "the layout editor is offered, quietly");
customize.click();
const editor = app.querySelector("[data-editor]");
assert.ok(editor, "edit mode shows the editor");
const addSelect = editor.querySelector(".editor__add");
assert.ok([...addSelect.options].some((o) => o.value === "chart:yaw_rate"), "channels that are not shown yet can be added");
assert.ok(![...addSelect.options].some((o) => o.value === "chart:speed"), "a channel already shown is not offered twice");
calls.length = 0;
addSelect.value = "chart:yaw_rate";
addSelect.dispatchEvent(new window.Event("change"));
await settle(80);
const reloaded = new URL(calls.find((u) => u.includes("/compare")), "http://x");
assert.equal(reloaded.searchParams.get("channels"), "yaw_rate", "only the channel that was missing is requested");
assert.equal(JSON.parse(localStorage.getItem("garage16_analysis_layout")).layout.some((w) => w.channel === "yaw_rate"), true, "layout remembered");
assert.equal(app.querySelectorAll(".chart").length, 10);
useV090Selection();

// ---------------------------------------------------------------- physics view loads on demand
app = fresh();
window.location.hash = "#/analysis?a=1&b=2";
await renderAnalysis(app);
await settle(60);
calls.length = 0;
const physics = app.querySelector(".analysis-physics");
physics.open = true;
physics.dispatchEvent(new window.Event("toggle"));
await settle(80);
const physCall = new URL(calls.find((u) => u.includes("/compare")), "http://x");
assert.ok(physCall.searchParams.get("channels").includes("susp_travel_fl"));
assert.ok(physCall.searchParams.get("channels").split(",").length <= 24);
assert.equal(app.querySelectorAll(".phys__wheel").length, 8, "4 wheels for each of the 2 laps");
assert.ok(app.querySelector(".phys__gg"));
assert.ok(text(app.querySelector(".phys__gg-readout")).includes("Lap 1"));
app._analysis.state.setCursor(50);
assert.ok(text(app.querySelector(".phys")).includes("mm"), "wheel values follow the cursor");

// a lap without suspension/tyre data says so instead of showing zeros
lapAHasNoPhysics = true;
app = fresh();
await renderAnalysis(app);
await settle(60);
app.querySelector(".analysis-physics").open = true;
app.querySelector(".analysis-physics").dispatchEvent(new window.Event("toggle"));
await settle(80);
assert.ok(text(app).includes("has no suspension/tyre data"));
lapAHasNoPhysics = false;

// ---------------------------------------------------------------- one lap only: picker for Lap 2 is open right away
calls.length = 0;
app = fresh();
window.location.hash = "#/analysis?a=1";
await renderAnalysis(app);
await settle(80);
assert.ok(!new URL(calls.find((u) => u.includes("/compare")), "http://x").searchParams.has("lap_b"));
assert.equal(app.querySelector('[data-chart="delta"]'), null, "no delta without a second lap");
assert.equal(app.querySelectorAll(".chart").length, 8);
assert.ok(text(app).includes("Choose Lap 2"), "pick the comparison lap right away");
assert.ok(text(app).includes("Rival"), "leaderboard tab lists the number one");
const selectButtons = [...app.querySelectorAll(".picker button")].filter((b) => b.textContent === "Select" || b.textContent === "Already chosen");
assert.ok(selectButtons.some((b) => b.textContent === "Already chosen" && b.disabled), "the lap already open cannot be chosen twice");
selectButtons.find((b) => b.textContent === "Select").click();
assert.equal(window.location.hash, "#/analysis?a=1&b=2", "one click on the leaderboard's number one compares against it");

// ---------------------------------------------------------------- channel missing for lap A
app = fresh();
window.location.hash = "#/analysis?a=1&b=2";
localStorage.removeItem("garage16_analysis_layout");
localStorage.setItem("garage16_analysis_channels", JSON.stringify(["speed", "susp_travel_fl"]));
lapAHasNoPhysics = true;
await renderAnalysis(app);
await settle(60);
assert.ok(text(app).includes("Not available for every lap"));
assert.equal(app.querySelector('[data-chart="susp_travel_fl"] .chart__val--a').textContent, "no data");
assert.notEqual(app.querySelector('[data-chart="susp_travel_fl"] .chart__val--b').textContent, "no data");
lapAHasNoPhysics = false;
useV090Selection();

// ---------------------------------------------------------------- server error is shown, with a way out
const realFetch = globalThis.fetch;
globalThis.fetch = async (url) => {
  const u = new URL(url, "http://x");
  if (u.pathname === "/telemetry/analysis/compare") return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "The telemetry of this lap is no longer stored." }) };
  return realFetch(url);
};
app = fresh();
window.location.hash = "#/analysis?a=1&b=2";
await renderAnalysis(app);
await settle(40);
assert.ok(text(app).includes("no longer stored"));
assert.ok([...app.querySelectorAll("button")].some((b) => b.textContent === "Analyse Lap 1 alone"));
globalThis.fetch = realFetch;

// ---------------------------------------------------------------- laps from clients older than 0.9.0
lapABasic = true;
app = fresh();
window.location.hash = "#/analysis?a=1&b=2";
await renderAnalysis(app);
await settle(60);
const basicBox = app.querySelector('[data-notice="basic-data"]');
assert.ok(basicBox, "a notice explains the missing channels");
const basicText = text(basicBox);
assert.ok(basicText.includes("Lap 1 (Me)") && basicText.includes("client 0.8.6") && basicText.includes("31 of 96"));
assert.ok(!basicText.includes("Lap 2 (Rival)"), "only the old lap is named");
assert.ok(basicText.includes("0.9.0 or newer") && basicText.includes("drive the lap again"));
assert.ok(/suspension/.test(basicText) && /TC\/ABS/.test(basicText));
const lapCards = [...app.querySelectorAll(".analysis-lap")];
assert.ok(text(lapCards[0]).includes("Basic data · client 0.8.6"), "Lap 1's card carries the tag");
assert.ok(!text(lapCards[1]).includes("Basic data"), "Lap 2 (full) does not");
assert.equal(app.querySelectorAll(".tag--basic").length, 1);
// The comparison itself still works on the basic channels
assert.equal(app.querySelectorAll(".chart").length, 9);
assert.ok(app.querySelector(".trackmap__canvas"));
lapABasic = false;

// one lap only, old client: same notice, no "Lap 2"
lapABasic = true;
app = fresh();
window.location.hash = "#/analysis?a=1";
await renderAnalysis(app);
await settle(60);
assert.ok(text(app.querySelector('[data-notice="basic-data"]')).includes("Lap 1 (Me)"));
lapABasic = false;

// picker: leaderboard rows and my laps are tagged, with an explanatory legend
rivalBasic = true;
myLapsBasic = true;
app = fresh();
window.location.hash = "#/analysis?a=1";
await renderAnalysis(app);
await settle(80);
const pickerTags = [...app.querySelectorAll(".picker .tag--basic")].map((t) => t.textContent);
assert.ok(pickerTags.includes("basic data"));
assert.ok(text(app.querySelector(".picker")).includes("older than 0.9.0"), "legend explains the tag");
const rivalRow = [...app.querySelectorAll(".picker tbody tr")].find((r) => r.textContent.includes("Rival"));
assert.ok(rivalRow.querySelector(".tag--basic"), "the old-client rival is tagged in the leaderboard list");
const meRow = [...app.querySelectorAll(".picker tbody tr")].find((r) => r.textContent.includes("Me"));
assert.equal(meRow.querySelector(".tag--basic"), null, "the full-data lap is not");
// ... and an old lap can still be chosen
const rivalSelect = [...rivalRow.querySelectorAll("button")].find((b) => b.textContent === "Select");
rivalSelect.click();
assert.equal(window.location.hash, "#/analysis?a=1&b=2");

// "My laps" tab
const mineTab2 = [...app.querySelectorAll(".picker__tabs button")].find((b) => b.textContent === "My laps");
mineTab2.click();
await settle();
assert.ok(app.querySelector(".picker tbody .tag--basic"), "my old-client lap is tagged");

// dashboard "My Laps"
const { renderDashboard } = await import("../../web/js/pages/dashboard.js");
app = fresh();
await renderDashboard(app);
await settle(60);
assert.ok(app.querySelector(".timing-table .tag--basic"), "dashboard lap table tags old-client laps");
assert.ok(text(app.querySelector(".notice--basic")).includes("older than 0.9.0"));
myLapsBasic = false;
app = fresh();
await renderDashboard(app);
await settle(60);
assert.equal(app.querySelector(".tag--basic"), null, "no tag, no notice when every lap has full data");
assert.equal(app.querySelector(".notice--basic"), null);
rivalBasic = false;

// ---------------------------------------------------------------- circuit library
calls.length = 0;
app = fresh();
await renderCircuits(app);
await settle(80);
assert.ok(text(app).includes("Circuit de la Sarthe (Le Mans)"));
assert.ok(text(app).includes("Monza") && text(app).includes("No laps yet"));
assert.ok(text(app).includes("2 laps") && text(app).includes("2 drivers") && text(app).includes("Hypercar"));
assert.ok(text(app).includes("Rival") && text(app).includes("1:25.000"));
assert.ok(text(app).includes("13.63 km"), "length from the outline");
assert.ok(calls.some((u) => u.includes("/circuits/outline") && u.includes("track_name=Le+Mans")));
assert.equal(app.querySelectorAll(".circuit__canvas").length, 1, "only layouts with laps get an outline");
const link = [...app.querySelectorAll("a")].find((a) => a.textContent === "Compare my lap with it");
assert.equal(link.getAttribute("href"), "#/analysis?quick=wr&track=Le%20Mans", "the server pairs my best lap here with the fastest one");
assert.ok(text(app).includes("My Best") && text(app).includes("1:31.200"), "my best lap on the layout is shown");
const first = [...app.querySelectorAll(".circuit h2")].map((h) => h.textContent);
assert.equal(first[0], "Circuit de la Sarthe (Le Mans)", "driven tracks are listed before empty ones");

store.signOut();
calls.length = 0;
app = fresh();
await renderCircuits(app);
await settle(60);
assert.ok(text(app).includes("Sign in to see the outline"));
assert.ok(!calls.some((u) => u.includes("/outline")), "no outline request without a session");

console.log("ANALYSIS-PAGE-OK");
