// jsdom test of the V0.9.1 analysis UI: the standard layout, the layout editor (order,
// size, channel, colours, reset), the "Compare lap" dialog, the "You vs ..." quick
// starts, the delta / sector summary, old-client laps and — hard requirement — that moving
// the cursor never causes a request. The network is a canned fetch that records every call.
import { JSDOM } from "jsdom";
import assert from "node:assert/strict";
import fs from "node:fs";

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

// no JavaScript error may escape anywhere in this test ("Keine JavaScript-Fehler")
const escaped = [];
process.on("unhandledRejection", (e) => escaped.push(e));
dom.window.addEventListener("error", (e) => escaped.push(e.error || e.message));

const drawn = { stroke: 0, drawImage: 0 };
const ctx = new Proxy({}, {
  get(target, prop) {
    if (prop in target) return target[prop];
    if (prop === "stroke") return () => { drawn.stroke++; };
    if (prop === "drawImage") return () => { drawn.drawImage++; };
    return () => {};
  },
  set(target, prop, value) { target[prop] = value; return true; },
});
dom.window.HTMLCanvasElement.prototype.getContext = function getContext() { return ctx; };
dom.window.HTMLElement.prototype.getBoundingClientRect = function rect() {
  // windows are stacked 100px apart in layout order (for drag-and-drop hit tests); everything else is 600x120
  if (this.classList && this.classList.contains("awin")) {
    const top = Number(this.getAttribute("data-index")) * 100;
    return { left: 0, right: 600, top, bottom: top + 90, width: 600, height: 90 };
  }
  return { left: 0, top: 0, width: 600, height: 120, right: 600, bottom: 120 };
};
dom.window.confirm = () => true;

// ---------------------------------------------------------------- canned backend
const N = 200;
const grid = { start: 2, step: 12, count: N };
const ramp = (f) => Array.from({ length: N }, (_, i) => f(i));
const theta = (i) => (i / N) * Math.PI * 2;
const channel = (label, unit, group, a, b, extra = {}) => ({ label, unit, group, discrete: false, fixed_range: null, a, b, ...extra });
const BASE = {
  speed: () => channel("Speed", "km/h", "Speed & inputs", ramp((i) => 200 + 50 * Math.sin(theta(i))), ramp((i) => 205 + 50 * Math.sin(theta(i)))),
  throttle: () => channel("Throttle", "%", "Speed & inputs", ramp((i) => (i % 40) * 2.5), ramp((i) => (i % 40) * 2.5), { fixed_range: [0, 100] }),
  brake: () => channel("Brake", "%", "Speed & inputs", ramp(() => 0), ramp(() => 0), { fixed_range: [0, 100] }),
  rpm: () => channel("Engine speed", "rpm", "Speed & inputs", ramp(() => 8000), ramp(() => 8100)),
  gear: () => channel("Gear", "", "Speed & inputs", ramp(() => 3), ramp(() => 4), { discrete: true }),
  lat_g: () => channel("Lateral G (right turn = +)", "G", "Motion & G", ramp((i) => 1.5 * Math.sin(theta(i))), ramp((i) => 1.4 * Math.sin(theta(i)))),
  yaw_rate: () => channel("Yaw rate", "°/s", "Motion & G", ramp(() => 1), ramp(() => 2)),
};
const lapHeader = (id, name, time, sectors, extra = {}) => ({ id, driver_id: id === 1 ? 1 : 9, driver_name: name, track_name: "Le Mans", car_name: "#7", car_class: "Hypercar", car_model: "Ferrari 499P", lap_number: 5, lap_time: time, sector_times: sectors, is_valid: true, ...extra });
const world = { aBasic: false, bothMissing: [], fail: null };
function compareBody(requested, withB, g = grid) {
  const rampN = (f) => Array.from({ length: g.count }, (_, i) => f(i));
  const channels = {};
  for (const key of requested) {
    const entry = (BASE[key] || (() => channel(key, "", "Other", ramp(() => 1), ramp(() => 2))))();
    const missing = world.bothMissing.includes(key);
    if (key === "speed" && g === grid && withB && !missing) { entry.a_spikes = { i: [50, 51], lo: [150, 150], hi: [262.5, 262.5] }; }
    channels[key] = { ...entry, a: missing || (world.aBasic && key === "yaw_rate") ? null : (g === grid ? entry.a : rampN((i) => entry.a[i % N])), b: !withB || missing ? null : (g === grid ? entry.b : rampN((i) => entry.b[i % N])) };
  }
  const delta = withB ? rampN((i) => 0.3 * Math.sin(theta(i) * 2) + i * 0.001) : null;
  return {
    grid: g,
    laps: { a: lapHeader(1, "Me", 90.0, [30.0, 30.5, 29.5]), b: withB ? lapHeader(2, "Rival", 85.0, [28.0, 29.0, 28.0]) : null },
    recording: {
      a: world.aBasic ? { level: "basic", client_version: "0.8.6", channels_available: 31, channels_total: 96 } : { level: "full", client_version: "0.9.0", channels_available: 96, channels_total: 96 },
      b: withB ? { level: "full", client_version: "0.9.0", channels_available: 96, channels_total: 96 } : null,
    },
    delta, time: { a: rampN((i) => i * 0.45), b: withB ? rampN((i) => i * 0.44) : null }, channels,
    unavailable: requested.filter((k) => world.bothMissing.includes(k)),
    map: { mirrored: false, a_decided: true, x_a: ramp((i) => 500 * Math.cos(theta(i))), y_a: ramp((i) => 300 * Math.sin(theta(i))), x_b: null, y_b: null },
    sectors: { a: [800, 1600], b: withB ? [800, 1600] : [] },
    corners: withB && g === grid ? [
      { n: 1, start: 400, apex: 620, end: 1300, delta: 0.412, a: { entry: 250, min: 98.2, apex: 618, time: 20.1, brake: 480, throttle: 760, exit: 151.0 }, b: { entry: 251, min: 101.5, apex: 622, time: 19.688, brake: 492, throttle: 742, exit: 155.4 } },
      { n: 2, start: 1300, apex: 1500, end: 2390, delta: -0.150, a: { entry: 240, min: 120.0, apex: 1500, time: 25.0, brake: 1380, throttle: 1600, exit: 170.0 }, b: { entry: 240, min: 118.0, apex: 1500, time: 25.15, brake: 1380, throttle: 1610, exit: 168.0 } },
    ] : [],
    orientation: { a: { decided_from_data: {} }, b: null },
    analysis_version: 1,
  };
}
const CATALOG = { channels: Object.entries({ speed: ["Speed", "km/h", "Speed & inputs"], throttle: ["Throttle", "%", "Speed & inputs"], brake: ["Brake", "%", "Speed & inputs"], gear: ["Gear", "", "Speed & inputs"], rpm: ["Engine speed", "rpm", "Speed & inputs"], lat_g: ["Lateral G (right turn = +)", "G", "Motion & G"], yaw_rate: ["Yaw rate", "°/s", "Motion & G"] }).map(([key, [label, unit, group]]) => ({ key, label, unit, group, discrete: key === "gear", fixed_range: null })), default: [] };

const calls = [];
const refineLog = [];                                  // every zoom-refinement request the page made
const refineBehaviour = { delays: [], fail: false, status: 500 };   // per-test: delay answers / make them fail
const replies = { reference: {}, quick: null, lap: {} };   // per-test answers
globalThis.fetch = async (url) => {
  calls.push(url);
  const u = new URL(url, "http://x");
  const json = (body, status = 200) => ({ ok: status < 400, status, statusText: String(status), json: async () => body, text: async () => JSON.stringify(body) });
  const p = u.pathname;
  if (world.fail && p === "/telemetry/analysis/compare") return json({ detail: world.fail }, 404);
  if (p === "/telemetry/analysis/channels") return json(CATALOG);
  if (p === "/telemetry/analysis/compare") {
    const requested = (u.searchParams.get("channels") || "").split(",").filter(Boolean);
    const withB = u.searchParams.get("lap_b") !== null;
    if (u.searchParams.has("start") && u.searchParams.has("end")) {
      refineLog.push({ start: Number(u.searchParams.get("start")), end: Number(u.searchParams.get("end")), step: Number(u.searchParams.get("step")), channels: requested, maxPoints: u.searchParams.get("max_points") });
      const delay = refineBehaviour.delays.shift() || 0;
      if (delay) await new Promise((r) => setTimeout(r, delay));
      if (refineBehaviour.fail) return json({ detail: "boom" }, refineBehaviour.status);
      const start = Number(u.searchParams.get("start"));
      const step = Math.max(Number(u.searchParams.get("step")), 0.5);
      const count = Math.floor((Number(u.searchParams.get("end")) - start) / step) + 1;
      return json(compareBody(requested, withB, { start, step, count }));
    }
    return json(compareBody(requested, withB));
  }
  if (p === "/telemetry/analysis/reference") {
    const answer = replies.reference[u.searchParams.get("kind")];
    return answer ? json(answer) : json({ detail: "unexpected" }, 500);
  }
  if (p === "/telemetry/analysis/quick") return replies.quick ? json(replies.quick) : json({ detail: "unexpected" }, 500);
  const lapMatch = p.match(/^\/telemetry\/analysis\/laps\/(\d+)$/);
  if (lapMatch) {
    const answer = replies.lap[lapMatch[1]];
    if (answer) return answer.status ? json({ detail: answer.detail }, answer.status) : json(answer.body);
    return json({ lap: lapHeader(Number(lapMatch[1]), "Other", 88, [29, 30, 29]), available_channels: ["speed"] });
  }
  if (p === "/telemetry/analysis/drivers/1/laps") return json({ driver_id: 1, driver_name: "Me", laps: [{ id: 11, lap_number: 3, lap_time: 91.2, is_valid: true, track_name: "Le Mans", car_name: "#7", car_model: "Ferrari 499P", uploaded_at: "2026-10-01T10:00:00", data_level: "full" }] });
  if (p === "/telemetry/laps") return json([]);
  if (p === "/drivers/1/public") return json({ personal_records: [], recent_activity: [] });
  if (p === "/teams/mine") return json([]);
  if (p === "/leaderboard/catalog/class-options") return json([{ name: "Hypercar", lap_count: 2 }]);
  if (p.startsWith("/leaderboard/")) return json([]);
  return json({ detail: `unexpected ${p}` }, 404);
};

const settle = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const { renderAnalysis, parseAnalysisExtras } = await import("../../web/js/pages/analysis.js");
const { store } = await import("../../web/js/state.js");
const { LAP_COLORS, DEFAULT_COLORS } = await import("../../web/js/analysis/model.js");
const text = (node) => node.textContent.replace(/\s+/g, " ");
const fresh = () => { document.body.innerHTML = `<main class="panel" id="app"></main>`; return document.getElementById("app"); };
const LAYOUT_KEY = "garage16_analysis_layout";
const compareCalls = () => calls.filter((u) => u.includes("/telemetry/analysis/compare")).map((u) => new URL(u, "http://x"));
const order = (app) => [...app.querySelectorAll(".awin")].map((w) => w.getAttribute("data-channel") ? `chart:${w.getAttribute("data-channel")}` : w.getAttribute("data-type"));
const stored = () => JSON.parse(localStorage.getItem(LAYOUT_KEY));
async function open(hash = "#/analysis?a=1&b=2") {
  const app = fresh();
  window.location.hash = hash;
  await renderAnalysis(app);
  await settle(70);
  return app;
}
const change = (node, value) => { node.value = value; node.dispatchEvent(new window.Event("change", { bubbles: true })); };
const mouse = (type, init = {}) => new window.MouseEvent(type, { bubbles: true, cancelable: true, ...init });

store.setSession({ driverId: 1, displayName: "Me", viaCookie: true });
localStorage.clear();
localStorage.setItem("lmu_garage_session", JSON.stringify({ driverId: 1, displayName: "Me", viaCookie: true }));

// ================================================================ first open: the standard layout
calls.length = 0;
let app = await open();
assert.deepEqual(order(app), ["summary", "delta", "corners", "chart:speed", "chart:throttle", "chart:brake", "trackmap", "cursor_table", "physics"]);
let cu = compareCalls();
assert.equal(cu.length, 1, "one request opens the analysis");
assert.equal(cu[0].searchParams.get("channels"), "speed,throttle,brake", "only what the standard layout shows is requested");
assert.equal(cu[0].searchParams.get("step"), "2");
const maxPoints = Number(cu[0].searchParams.get("max_points"));
assert.ok(maxPoints >= 1000 && maxPoints <= 3000, `server-side downsampling is asked for (${maxPoints} points per series)`);
assert.equal(app.querySelectorAll(".chart").length, 4, "delta + speed, throttle, brake");
assert.equal(app.querySelector(".trackmap select").value, "delta", "the map shows Delta first");
assert.ok(app.querySelector(".analysis-compare-open"), "'Compare lap' is the primary button");
assert.equal(text(app.querySelector(".analysis-compare-open")), "Compare lap");
assert.equal(text(app.querySelector(".analysis-customize")), "Customise view");
assert.equal(app.querySelector("[data-editor]"), null, "no setup assistant, no editor until asked for");
assert.equal(app.querySelectorAll(".awin__handle").length, 0, "no drag handles outside edit mode: a normal drag can never move a window");
assert.equal(app.querySelectorAll(".awin__bar").length, 0);
assert.equal(localStorage.getItem(LAYOUT_KEY), null, "opening the analysis stores nothing");
assert.ok(drawn.stroke > 10, "charts were drawn");

// ---------------------------------------------------------------- delta facts and sectors
const gain = app.querySelector('[data-fact="gain"]');
const loss = app.querySelector('[data-fact="loss"]');
const net = app.querySelector('[data-fact="net"]');
assert.ok(/^Biggest gain: −\d\.\d{3} s @ /.test(text(gain)), text(gain));
assert.ok(/^Biggest loss: \+\d\.\d{3} s @ /.test(text(loss)), text(loss));
assert.ok(/^Net: [+−±]\d\.\d{3} s$/.test(text(net)), text(net));
const state = app._analysis.state;
const before = state.getCursor();
gain.click();
assert.notEqual(state.getCursor(), before, "clicking a fact moves the shared cursor there");
assert.equal(state.isPinned(), true);
state.unpin();
const sectors = app.querySelector(".sector-table");
assert.ok(text(sectors).includes("S1 +2.000 s") && text(sectors).includes("Total +5.000 s"));
// Lap 1 loses time in ALL three sectors here (+2.0 / +1.5 / +1.5). The least-bad sector is the
// "best sector" — but it is not a gain, so it is neither called "biggest gain" nor tinted green.
// (Corrected after the review in a real browser: the first version did both.)
const bestRow = sectors.querySelector('[data-rank="best"]');
const worstRow = sectors.querySelector('[data-rank="worst"]');
assert.equal(worstRow.getAttribute("data-sector"), "S1", "S1 lost the most");
assert.ok(text(worstRow).includes("worst sector · biggest loss"), text(worstRow));
assert.ok(worstRow.classList.contains("sector-row--worst"), "a real loss is tinted red");
assert.ok(text(bestRow).includes("best sector") && !text(bestRow).includes("biggest gain"), text(bestRow));
assert.equal(sectors.querySelectorAll(".sector-row--best").length, 0, "no green tint without a gain");
assert.equal(bestRow.querySelector(".sector-row__badge--gain"), null);

// ================================================================ corner analysis (V0.9.1 review)
const cornerRows = [...app.querySelectorAll(".corners__table tbody tr")];
assert.equal(cornerRows.length, 3, "two corners and a total");
assert.deepEqual([...cornerRows[0].cells].map((c) => c.textContent), ["1", "620 m", "+0.412 s", "12 m earlier", "98.2 / 101.5", "151.0 / 155.4"]);
assert.ok(cornerRows[0].classList.contains("corner-row--loss"), "the corner that lost the most is marked");
assert.ok(cornerRows[1].classList.contains("corner-row--gain"), "the corner that gained the most is marked");
assert.equal(cornerRows[1].cells[3].textContent, "same point");
assert.deepEqual([cornerRows[2].cells[0].textContent, cornerRows[2].cells[1].textContent], ["Total", "+0.262 s"], "the corners add up");
cornerRows[0].click();
const cornerView = state.getView();
assert.ok(cornerView[0] <= 480 - 50 && cornerView[1] >= 760 && cornerView[1] <= 1300, `zoomed onto braking, apex and exit (${cornerView})`);
assert.equal(state.isPinned(), true);
assert.ok(Math.abs(state.data.grid.start + state.getCursor() * state.data.grid.step - 620) <= state.data.grid.step, "the cursor is pinned at the apex");
state.unpin();
state.resetView();
await settle(30);

// cursor moves only copy the cached chart layer: no line of any chart is drawn again
const strokesBefore = drawn.stroke;
const imagesBefore = drawn.drawImage;
for (let i = 0; i < 10; i++) { state.setCursor(20 + i * 7); await settle(5); }
const strokesPerMove = (drawn.stroke - strokesBefore) / 10;
assert.ok(drawn.drawImage > imagesBefore, "the cached layer is copied");
assert.ok(strokesPerMove <= 8, `a cursor move strokes only the cursor lines (${strokesPerMove} per move for 4 charts + map)`);

// a zoom gesture draws straight onto the canvas (copying a layer per frame would double the
// work); once the view has settled the layer is rebuilt, so the next cursor move is cheap again
const imagesBeforeZoom = drawn.drawImage;
for (let i = 0; i < 6; i++) { state.zoom(i % 2 ? 1.1 : 0.9, 900); await settle(5); }
assert.equal(drawn.drawImage, imagesBeforeZoom, "no layer copies while the view changes every frame");
await settle(200);                                   // > LAYER_SETTLE_MS
const strokesAfterSettle = drawn.stroke;
for (let i = 0; i < 5; i++) { state.setCursor(30 + i * 5); await settle(5); }
assert.ok((drawn.stroke - strokesAfterSettle) / 5 <= 8, "after the gesture the layer is ready before the first cursor move");
state.resetView();
await settle(30);

// ================================================================ one cursor, no requests
const speedCanvas = app.querySelector('[data-chart="speed"] canvas');
const callsBefore = calls.length;
for (let x = 60; x < 560; x += 10) speedCanvas.dispatchEvent(mouse("mousemove", { clientX: x }));
app.querySelector('[data-chart="delta"] canvas').dispatchEvent(mouse("mousemove", { clientX: 200 }));
window.dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowRight" }));
window.dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowLeft", shiftKey: true }));
speedCanvas.dispatchEvent(mouse("click", { clientX: 300 }));
for (const type of ["touchstart", "touchmove", "touchend"]) {
  const ev = new window.Event(type, { bubbles: true, cancelable: true });
  ev.touches = type === "touchend" ? [] : [{ clientX: 250 + (type === "touchmove" ? 40 : 0), clientY: 10 }];
  speedCanvas.dispatchEvent(ev);
}
app.querySelector(".trackmap__canvas").dispatchEvent(mouse("mousemove", { clientX: 5, clientY: 5 }));
app.querySelector(".trackmap__canvas").dispatchEvent(mouse("click", { clientX: 5, clientY: 5 }));
await settle(40);
assert.equal(calls.length, callsBefore, "hovering, clicking, touching and the arrow keys cause no request at all");

// at the very start of the lap the time is 0:00.000, not "—" (that means "no time")
state.unpin();
state.setCursor(0);
const timeRow = [...app.querySelectorAll(".cursor-table tr")].find((r) => r.cells[0] && r.cells[0].textContent === "Time");
assert.deepEqual([timeRow.cells[1].textContent, timeRow.cells[2].textContent], ["0:00.000", "0:00.000"]);

// a single shared cursor: every window shows the same position
state.unpin();
state.setCursor(120);
const metres = (120 * 12 + 2) / 1000;
assert.ok(text(app.querySelector(".cursor-table")).includes(`${metres.toFixed(2)} km`), "the values table follows the cursor");
assert.equal(app.querySelector('[data-chart="speed"] .chart__val--a').textContent, app.querySelector('[data-chart="speed"] .chart__val--a').textContent);
const legendNow = (key) => app.querySelector(`[data-chart="${key}"] .chart__val--a`).textContent;
assert.ok(legendNow("speed") !== "—" && legendNow("throttle").endsWith("%"), "charts show values at the cursor");
const drawsBefore = drawn.drawImage;
state.setCursor(30);
await settle(30);
assert.ok(drawn.drawImage > drawsBefore, "the map redraws for the same cursor");

// ================================================================ the editor
const customize = app.querySelector(".analysis-customize");
customize.click();
assert.ok(app.querySelector("[data-editor]"), "'Customise view' opens the editor");
assert.equal(app.querySelectorAll(".awin__handle").length, 9, "every window gets a drag handle in edit mode");
assert.equal(text(customize), "Done");
assert.equal(state.getCursor(), 30, "the shared state survives a rebuild");

// --- reorder with the buttons
const idOf = (key) => [...app.querySelectorAll(".awin")].find((w) => (w.getAttribute("data-channel") || w.getAttribute("data-type")) === key).getAttribute("data-widget");
calls.length = 0;
app.querySelector(`[data-widget="${idOf("speed")}"] .awin__up`).click();
assert.deepEqual(order(app).slice(0, 4), ["summary", "delta", "chart:speed", "corners"]);
assert.deepEqual(stored().layout.map((w) => w.position), [0, 1, 2, 3, 4, 5, 6, 7, 8], "positions stored");
assert.equal(stored().layout[2].channel, "speed", "the new order is stored");
assert.equal(compareCalls().length, 0, "reordering needs no data");
app.querySelector(`[data-widget="${idOf("speed")}"] .awin__down`).click();
assert.deepEqual(order(app).slice(0, 4), ["summary", "delta", "corners", "chart:speed"], "…and back");
assert.equal(app.querySelector('[data-widget="w1"] .awin__up').disabled, true);

// --- reorder by dragging the handle (pointer events, as mouse / pen / touch all send them)
// (the test's fake layout stacks windows 100 px apart in order: window k sits at y = 100*k)
const handle = (id) => app.querySelector(`[data-widget="${id}"] .awin__handle`);
let current = order(app);
let speedId = idOf("speed");
handle(speedId).dispatchEvent(mouse("pointerdown", { clientX: 10, clientY: 310, button: 0 }));
assert.ok(app.querySelector(".awin--dragging"), "the dragged window is marked");
handle(speedId).dispatchEvent(mouse("pointermove", { clientX: 30, clientY: 510 }));   // y = 510 -> window index 5
assert.equal(app.querySelectorAll(".awin--drop-target").length, 1, "the drop target is marked");
handle(speedId).dispatchEvent(mouse("pointerup", { clientX: 30, clientY: 510 }));
assert.deepEqual(order(app), ["summary", "delta", "corners", "chart:throttle", "chart:brake", "chart:speed", "trackmap", "cursor_table", "physics"], "dropped on the 6th window: speed moves there");
assert.equal(stored().layout[5].channel, "speed");
// cancelling or dropping on itself changes nothing
speedId = idOf("speed");
current = order(app);
handle(speedId).dispatchEvent(mouse("pointerdown", { clientX: 10, clientY: 510 }));
handle(speedId).dispatchEvent(mouse("pointermove", { clientX: 10, clientY: 10 }));
handle(speedId).dispatchEvent(mouse("pointercancel"));
assert.deepEqual(order(app), current, "a cancelled drag changes nothing");
handle(speedId).dispatchEvent(mouse("pointerdown", { clientX: 10, clientY: 510 }));
handle(speedId).dispatchEvent(mouse("pointerup", { clientX: 10, clientY: 510 }));
assert.deepEqual(order(app), current, "dropping a window on itself changes nothing");
// keyboard on the handle
handle(speedId).dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
assert.equal(order(app)[4], "chart:speed", "the handle also works from the keyboard");

// --- sizes
const sizeOf = (key) => app.querySelector(`[data-channel="${key}"]`).getAttribute("data-size");
assert.equal(sizeOf("throttle"), "large");
change(app.querySelector('[data-channel="throttle"] .awin__size'), "small");
assert.equal(sizeOf("throttle"), "small");
assert.ok(app.querySelector('[data-channel="throttle"]').classList.contains("awin--small"));
assert.equal(stored().layout.find((w) => w.channel === "throttle").size, "small");
change(app.querySelector('[data-channel="throttle"] .awin__size'), "medium");
assert.equal(sizeOf("throttle"), "medium");
assert.equal(compareCalls().length, 0);

// --- a window's channel
const brakeSelect = app.querySelector('[data-channel="brake"] .awin__channel');
assert.ok([...brakeSelect.querySelectorAll("option")].find((o) => o.value === "speed").disabled, "a channel already shown elsewhere cannot be picked twice");
calls.length = 0;
change(brakeSelect, "rpm");
await settle(60);
assert.deepEqual(compareCalls().map((c) => c.searchParams.get("channels")), ["rpm"], "only the new channel is requested");
assert.ok(app.querySelector('[data-channel="rpm"] .chart'), "the window shows the new channel");
assert.equal(app.querySelector('[data-channel="brake"]'), null);
assert.ok(stored().layout.some((w) => w.channel === "rpm"));
// switching back needs no request: the data is already here
calls.length = 0;
change(app.querySelector('[data-channel="rpm"] .awin__channel'), "brake");
await settle(30);
assert.equal(compareCalls().length, 0, "a channel that was loaded before is not requested again");

// --- add and remove a window
calls.length = 0;
const addSelect = app.querySelector(".editor__add");
change(addSelect, "chart:gear");
await settle(60);
assert.deepEqual(compareCalls().map((c) => c.searchParams.get("channels")), ["gear"]);
assert.ok(app.querySelector('[data-channel="gear"]'));
app.querySelector('[data-channel="gear"] .awin__remove').click();
assert.equal(app.querySelector('[data-channel="gear"]'), null);
assert.ok(![...app.querySelector(".editor__add").options].some((o) => o.value === "type:summary"), "windows that exist are not offered again");

// --- the track map's colour mode: loaded on demand, remembered
calls.length = 0;
change(app.querySelector("#mapColorMode"), "lat_g");
await settle(60);
assert.deepEqual(compareCalls().map((c) => c.searchParams.get("channels")), ["lat_g"]);
assert.equal(stored().layout.find((w) => w.type === "trackmap").mode, "lat_g");
assert.ok(text(app.querySelector(".trackmap__legend")).includes("G"), "legend shows the channel's range");

// --- colours
const colorA = app.querySelector("#editor_color_a");
colorA.value = "#00ff00";
colorA.dispatchEvent(new window.Event("change", { bubbles: true }));
assert.equal(stored().colors.lapA, "#00ff00");
assert.equal(LAP_COLORS.a, "#00ff00");
assert.ok(/#00ff00|rgb\(0, 255, 0\)/i.test(app.querySelector('[data-chart="speed"] .chart__val--a').getAttribute("style")), "charts use the chosen colour");
assert.equal(LAP_COLORS.b, DEFAULT_COLORS.b, "untouched colours stay default");
const deltaColor = app.querySelector("#editor_color_delta");
deltaColor.value = "#123456";
deltaColor.dispatchEvent(new window.Event("change", { bubbles: true }));
assert.equal(stored().colors.delta, "#123456");
// a window's own colours
const speedColors = [...app.querySelectorAll('[data-channel="speed"] .awin__colors input[type="color"]')];
assert.equal(speedColors.length, 2);
speedColors[1].value = "#abcdef";
speedColors[1].dispatchEvent(new window.Event("change", { bubbles: true }));
assert.deepEqual(stored().layout.find((w) => w.channel === "speed").colors, { b: "#abcdef" });
assert.ok(/#abcdef|rgb\(171, 205, 239\)/i.test(app.querySelector('[data-chart="speed"] .chart__val--b').getAttribute("style")));
assert.ok(!/#abcdef|rgb\(171, 205, 239\)/i.test(app.querySelector('[data-chart="throttle"] .chart__val--b').getAttribute("style")), "other windows keep their colours");
app.querySelector('[data-channel="speed"] .awin__color-reset').click();
assert.equal(stored().layout.find((w) => w.channel === "speed").colors, undefined);
// the layout is remembered across page loads
const rememberedOrder = order(app);
app = await open();
assert.deepEqual(order(app), rememberedOrder, "reopening the analysis shows the personal layout");
assert.equal(app.querySelector("[data-editor]"), null, "…but not in edit mode");
assert.equal(LAP_COLORS.a, "#00ff00");

// --- reset: the standard layout is back, nothing but the UI preference is touched
localStorage.setItem("some_other_key", "keep me");
app.querySelector(".analysis-customize").click();
calls.length = 0;
app.querySelector(".editor__reset").click();
await settle(60);
assert.deepEqual(order(app), ["summary", "delta", "corners", "chart:speed", "chart:throttle", "chart:brake", "trackmap", "cursor_table", "physics"], "'Restore standard layout'");
assert.equal(localStorage.getItem(LAYOUT_KEY), null);
assert.equal(localStorage.getItem("some_other_key"), "keep me");
assert.equal(localStorage.getItem("lmu_garage_session") !== null, true, "the sign-in is untouched");
assert.deepEqual({ ...LAP_COLORS }, { ...DEFAULT_COLORS }, "default colours are back");
assert.ok(text(app).includes("Standard layout restored."));
assert.equal(compareCalls().filter((c) => c.searchParams.get("lap_a") === null).length, 0);
dom.window.confirm = () => false;   // (edit mode is still on after the reset)
app.querySelector('[data-widget="w3"] .awin__remove').click();
const afterRemove = order(app);
app.querySelector(".editor__reset").click();
await settle(30);
assert.deepEqual(order(app), afterRemove, "declining the confirmation resets nothing");
dom.window.confirm = () => true;
localStorage.removeItem(LAYOUT_KEY);

// ================================================================ zoom refinement
const { REFINE } = await import("../../web/js/analysis/refine.js");
REFINE.debounceMs = 25;
const { indexForDistance, distanceAt } = await import("../../web/js/analysis/model.js");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const zoomApp = async () => { refineLog.length = 0; localStorage.removeItem(LAYOUT_KEY); return open(); };

app = await zoomApp();
let rs = app._analysis.state;
const baseGrid = rs.data.grid;
assert.deepEqual(baseGrid, { start: 2, step: 12, count: 200 });
const loadedChannels = Object.keys(rs.data.channels).sort();

// zoomed out and moving the cursor: nothing is refined
calls.length = 0;
rs.setCursor(80);
await sleep(40);
assert.equal(refineLog.length, 0, "no refinement without zoom");
assert.equal(rs.data.grid.step, 12);

// zoom in far: after the debounce exactly ONE request for that stretch, at a finer step
rs.setCursor(83);                                      // 998 m
const speedBefore = app.querySelector('[data-chart="speed"]');
const callsBeforeZoom = calls.length;
rs.setView(1000, 1200);
rs.setView(1000, 1200);
rs.zoom(1, 1100);
await sleep(8);
assert.equal(refineLog.length, 0, "the page waits for the zoom to settle before asking");
await sleep(80);
assert.equal(refineLog.length, 1, "one request, however many view events the gesture produced");
assert.deepEqual({ start: refineLog[0].start, end: refineLog[0].end, step: refineLog[0].step }, { start: 950, end: 1250, step: 0.5 }, "the visible stretch plus a margin, at the finest step");
assert.deepEqual([...refineLog[0].channels].sort(), loadedChannels, "every channel that is on screen comes along");
assert.ok(Number(refineLog[0].maxPoints) >= 1000, "the request is size-bounded");
assert.equal(calls.length - callsBeforeZoom, 1, "that was the only request");
assert.equal(rs.data.grid.step, 0.5, "the charts now draw the finer grid");
assert.equal(rs.data.grid.start, 950);
assert.equal(rs.data.grid.count, 601);
assert.deepEqual(rs.getView(), [1000, 1200], "the view did not move");
assert.equal(distanceAt(rs.data.grid, rs.getCursor()), 998, "the cursor stayed at 998 m");
// V0.9.1 (review): the finer data is swapped INTO the existing charts — no window is rebuilt
// (a rebuild cost ~60 ms and cut off a drag in progress)
assert.equal(app.querySelector('[data-chart="speed"]'), speedBefore, "the same chart, now drawing the finer data");
assert.equal(app.querySelectorAll(".chart").length, 4);
assert.ok(text(app.querySelector(".cursor-table")).includes("0.998 km") || /998 m/.test(text(app.querySelector(".cursor-table"))), "the values table follows");
assert.ok(legendNow("speed") !== "—", "charts show values on the refined grid");

// what is about the WHOLE lap did not change
assert.ok(text(app.querySelector(".sector-table")).includes("S1 +2.000 s") && text(app.querySelector(".sector-table")).includes("Total +5.000 s"));
assert.ok(/^Biggest gain: −\d\.\d{3} s @ /.test(text(app.querySelector('[data-fact="gain"]'))), "biggest gain is still the lap's, not the stretch's");
assert.ok(app.querySelector(".trackmap__canvas"), "the track map is still there");
// panning inside the fetched stretch needs no request; neither does zooming further in
rs.setView(960, 1160);
rs.setView(1100, 1150);
await sleep(60);
assert.equal(refineLog.length, 1, "inside the stretch: no new request");
assert.equal(rs.data.grid.step, 0.5);

// moving the cursor over the refined charts is still free
const callsBeforeHover = calls.length;
for (let x = 70; x < 560; x += 20) app.querySelector('[data-chart="speed"] canvas').dispatchEvent(mouse("mousemove", { clientX: x }));
await sleep(20);
assert.equal(calls.length, callsBeforeHover, "hover on refined data: no request");

// pan OUT of the stretch: the overview is back immediately, then a new stretch arrives
rs.setView(1800, 2000);
assert.equal(rs.data.grid.step, 12, "never a half-empty chart: the overview is shown at once");
assert.equal(app.querySelectorAll(".chart").length, 4);
await sleep(80);
assert.equal(refineLog.length, 2);
assert.deepEqual({ start: refineLog[1].start, end: refineLog[1].end }, { start: 1750, end: 2050 });
assert.equal(rs.data.grid.start, 1750);

// zoom all the way out: back to the overview, nothing requested
rs.resetView();
await sleep(60);
assert.equal(rs.data.grid.step, 12, "zoomed out: the overview");
assert.equal(refineLog.length, 2);

// a channel added while zoomed in is loaded for BOTH grids (no rebuild under the user's hands)
rs.setView(1000, 1200);
await sleep(80);
assert.equal(rs.data.grid.step, 0.5);
refineLog.length = 0;
calls.length = 0;
app.querySelector(".analysis-customize").click();
await sleep(5);
rs = app._analysis.state;
change(app.querySelector(".editor__add"), "chart:gear");
await sleep(80);
const gearRequests = compareCalls().filter((c) => c.searchParams.get("channels") === "gear");
assert.equal(gearRequests.length, 2, "once for the overview, once for the refined stretch");
assert.ok(gearRequests.some((c) => c.searchParams.has("start")) && gearRequests.some((c) => !c.searchParams.has("start")));
assert.ok(app.querySelector('[data-channel="gear"] .chart'), "the new window shows data on the refined grid");
assert.equal(rs.data.grid.step, 0.5);
assert.ok(rs.data.channels.gear && app._analysis.state.data.channels.gear, "the refined data has the channel");
localStorage.removeItem(LAYOUT_KEY);

// physics opened while zoomed in works and keeps its window
app = await zoomApp();
rs = app._analysis.state;
rs.setView(1000, 1200);
await sleep(80);
assert.equal(rs.data.grid.step, 0.5);
const physicsBox = app.querySelector(".analysis-physics");
physicsBox.open = true;
physicsBox.dispatchEvent(new window.Event("toggle"));
await sleep(100);
assert.equal(app.querySelector(".analysis-physics"), physicsBox, "the window that asked for the channels was not torn down");
assert.equal(app.querySelectorAll(".phys__wheel").length, 8, "four wheels per lap, on refined data");

// clicking "biggest gain" while zoomed in somewhere else takes the user to that spot
app = await zoomApp();
rs = app._analysis.state;
rs.setView(1000, 1200);
await sleep(80);
assert.equal(rs.data.grid.step, 0.5);
const gainText = text(app.querySelector('[data-fact="gain"]'));
const [, gainValue, gainUnit] = /@ ([\d.]+) (km|m)/.exec(gainText);
const gainMetres = Number(gainValue) * (gainUnit === "km" ? 1000 : 1);
assert.ok(gainMetres < 950 || gainMetres > 1250, "the fact lies outside the refined stretch");
app.querySelector('[data-fact="gain"]').click();
assert.equal(rs.isPinned(), true);
const viewNow = rs.getView();
assert.ok(viewNow[0] <= gainMetres && gainMetres <= viewNow[1], `the view moved to the spot (${viewNow} for ${gainMetres} m)`);
assert.ok(Math.abs(viewNow[1] - viewNow[0] - 200) < 1, "same zoom level, just somewhere else");
const pinnedAt = distanceAt(rs.data.grid, rs.getCursor());
assert.ok(Math.abs(pinnedAt - gainMetres) <= 12, "the cursor is pinned at the fact (to the overview's 12 m grid)");
await sleep(80);
assert.equal(rs.data.grid.step, 0.5, "and the finer data for the new place arrives");
assert.ok(Math.abs(distanceAt(rs.data.grid, rs.getCursor()) - pinnedAt) <= 0.5, "the swap did not move the pin");
assert.equal(rs.isPinned(), true);

// the answer to an old zoom never overwrites a newer one
app = await zoomApp();
rs = app._analysis.state;
refineBehaviour.delays = [90, 0];
rs.setView(1000, 1200);
await sleep(30);                                       // the slow request is on its way
rs.setView(1800, 2000);                                // the user went elsewhere
await sleep(200);
assert.equal(refineLog.length, 2);
assert.equal(rs.data.grid.start, 1750, "the newer stretch won; the stale answer was discarded");
assert.deepEqual(rs.getView(), [1800, 2000]);

// going back to a stretch the browser already has costs no request
app = await zoomApp();
rs = app._analysis.state;
rs.setView(1000, 1200);
await sleep(80);
assert.equal(refineLog.length, 1);
rs.setView(1800, 2000);
await sleep(80);
assert.equal(refineLog.length, 2);
rs.setView(1000, 1200);                                // exactly where we were
await sleep(80);
assert.equal(refineLog.length, 2, "the first stretch was remembered: no third request");
assert.equal(rs.data.grid.start, 950, "...and it is back, refined");
assert.equal(rs.data.grid.step, 0.5);
// a different channel set is a different question: it is asked again
app.querySelector(".analysis-customize").click();
change(app.querySelector(".editor__add"), "chart:gear");
await sleep(80);
refineLog.length = 0;
rs.setView(1800, 2000);
await sleep(80);
rs.setView(1000, 1200);
await sleep(80);
assert.equal(refineLog.length >= 1, true, "after the channel set changed the remembered answers no longer fit");
localStorage.removeItem(LAYOUT_KEY);

// "too many requests": the page stops asking for a while instead of retrying on every gesture
app = await zoomApp();
rs = app._analysis.state;
REFINE.backoffMs = 400;
refineBehaviour.fail = true;
refineBehaviour.status = 429;
rs.setView(1000, 1200);
await sleep(80);
assert.equal(refineLog.length, 1, "one attempt");
rs.setView(1100, 1300);
await sleep(80);
rs.setView(1200, 1400);
await sleep(60);
assert.equal(refineLog.length, 1, "no more attempts while the server asked us to slow down");
assert.equal(rs.data.grid.step, 12, "the overview keeps working");
assert.equal(app.querySelector(".notice--error"), null);
refineBehaviour.fail = false;
refineBehaviour.status = 500;
await sleep(300);                                      // the pause is over
rs.setView(1000, 1200);
await sleep(80);
assert.equal(refineLog.length, 2, "asking again after the pause");
assert.equal(rs.data.grid.step, 0.5);
REFINE.backoffMs = 60000;

// a failing server leaves the overview in place: less detail, nothing broken, no error screen
app = await zoomApp();
rs = app._analysis.state;
refineBehaviour.fail = true;
rs.setView(1000, 1200);
await sleep(80);
assert.equal(refineLog.length, 1);
assert.equal(rs.data.grid.step, 12, "still the overview");
assert.equal(app.querySelectorAll(".chart").length, 4);
assert.equal(app.querySelector(".notice--error"), null, "a failed refinement is not an error the user needs to see");
refineBehaviour.fail = false;
rs.setView(1000, 1210);                                // a later zoom tries again
await sleep(80);
assert.equal(refineLog.length, 2);
assert.equal(rs.data.grid.step, 0.5);

// leaving the page: a pending timer must not touch the old page
app = await zoomApp();
rs = app._analysis.state;
rs.setView(1000, 1200);
document.body.innerHTML = "";                          // the router replaced the page
await sleep(80);
assert.equal(refineLog.length, 0, "a page that is gone asks for nothing");
REFINE.debounceMs = 300;

// ================================================================ hostile storage
for (const bad of ["{oops", "null", "[]", JSON.stringify({ version: 1, layout: [{ type: "chart", channel: "../../etc/passwd", size: "x" }], colors: { lapA: "url(javascript:1)", lapB: "red", delta: 5 } }), JSON.stringify({ version: 999, layout: [] })]) {
  localStorage.setItem(LAYOUT_KEY, bad);
  app = await open();
  assert.deepEqual(order(app).slice(0, 4), ["summary", "delta", "corners", "chart:speed"], `bad stored layout ${bad.slice(0, 30)} -> standard layout`);
  assert.deepEqual({ ...LAP_COLORS }, { ...DEFAULT_COLORS }, "the default colours survive a hostile stored configuration");
  assert.ok(app.querySelectorAll(".chart").length >= 4);
}
localStorage.removeItem(LAYOUT_KEY);

// storage that refuses writes: the view still works and says so
const realSetItem = dom.window.Storage.prototype.setItem;
dom.window.Storage.prototype.setItem = function setItem(k, v) { if (k === LAYOUT_KEY) throw new Error("quota"); return realSetItem.call(this, k, v); };
app = await open();
app.querySelector(".analysis-customize").click();
app.querySelector('[data-widget="w3"] .awin__up').click();
assert.deepEqual(order(app).slice(0, 3), ["summary", "corners", "delta"], "the change applies for this session");
assert.ok(app.querySelector(".editor__storage"), "…and the user is told it cannot be saved");
dom.window.Storage.prototype.setItem = realSetItem;
localStorage.removeItem(LAYOUT_KEY);

// ================================================================ responsive CSS (jsdom has no layout engine: assert the rules)
const css = fs.readFileSync(new URL("../../web/css/styles.css", import.meta.url), "utf8");
assert.match(css, /\.awin-grid\s*\{[^}]*grid-template-columns:\s*repeat\(12,\s*minmax\(0,\s*1fr\)\)/, "a 12-column grid whose tracks can shrink (no overflow)");
assert.match(css, /\.awin--small\s*\{\s*grid-column:\s*span 4/);
assert.match(css, /\.awin--medium\s*\{\s*grid-column:\s*span 6/);
assert.match(css, /\.awin--large\s*\{\s*grid-column:\s*span 12/);
assert.match(css, /@media \(max-width: 1280px\)[^}]*\.awin--small \{ grid-column: span 6/, "laptop: fewer columns");
assert.match(css, /@media \(max-width: 900px\)\s*\{[^@]*\.awin--small \{ grid-column: span 6;[^@]*\.awin--medium \{ grid-column: span 12/, "tablet: small windows two per row, medium ones full width");
assert.match(css, /@media \(max-width: 640px\)[^}]*\.awin--small,[^}]*\.awin--medium,[^}]*\.awin--large \{ grid-column: span 12/, "phone: one column");
assert.match(css, /\.awin\s*\{[^}]*min-width:\s*0/, "a window can shrink below its content");
assert.match(css, /\.awin__body\s*\{[^}]*overflow-x:\s*auto/, "wide content scrolls inside its window, not the page");
assert.match(css, /\.awin__handle\s*\{[^}]*touch-action:\s*none/, "the drag handle owns touch gestures");
assert.match(css, /\.chart__canvas\s*\{[^}]*touch-action:\s*pan-y/, "charts leave vertical scrolling to the page");

// ================================================================ "Compare lap"
const dialog = () => document.querySelector("[data-compare-dialog]");
const labels = () => [...dialog().querySelectorAll(".compare-dialog__option strong")].map((n) => n.textContent);
async function openDialog() {
  app = await open();
  app.querySelector(".analysis-compare-open").click();
  assert.ok(dialog(), "the dialog opens");
}
const pick = (kind) => { const r = dialog().querySelector(`#compare_${kind}`); r.checked = true; r.dispatchEvent(new window.Event("change", { bubbles: true })); };
const go = async () => { dialog().querySelector(".compare-dialog__go").click(); await settle(40); };
const message = () => (dialog() ? dialog().querySelector(".compare-dialog__message").textContent : null);
const refAnswer = (over) => ({ kind: "wr", label: "Garage16 Best", scope: "model", found: true, lap: lapHeader(7, "Fast", 80, [27, 27, 26]), reason: null, message: null, already_best: false, ...over });

await openDialog();
assert.equal(dialog().querySelector("h2").textContent, "Compare with");
assert.deepEqual(labels(), ["Garage16 Best", "Team Best", "My Best", "Lap ID"]);
assert.ok(!text(dialog()).includes("World Record") && !text(dialog()).includes("Weltrekord ") || text(dialog()).includes("kein offizieller Weltrekord"), "never claims a world record");
assert.equal(dialog().querySelector("#compare_wr").checked, true, "the first choice is preselected");
assert.equal(dialog().querySelector(".compare-dialog__lapid").hidden, true, "the Lap ID field appears only for 'Lap ID'");
pick("lap_id");
assert.equal(dialog().querySelector(".compare-dialog__lapid").hidden, false);
assert.ok(text(dialog().querySelector(".compare-dialog__lapid")).startsWith("Lap ID:"));
pick("wr");
assert.equal(dialog().querySelector(".compare-dialog__lapid").hidden, true);

// close paths
dialog().querySelector(".compare-dialog__cancel").click();
assert.equal(dialog(), null, "Cancel closes it");
app.querySelector(".analysis-compare-open").click();
document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
assert.equal(dialog(), null, "Escape closes it");
app.querySelector(".analysis-compare-open").click();
dialog().dispatchEvent(mouse("click"));
assert.equal(dialog(), null, "a click outside closes it");

// Garage16 Best found -> navigates with lap 1 + the reference
replies.reference.wr = refAnswer({});
app.querySelector(".analysis-compare-open").click();
calls.length = 0;
await go();
assert.ok(calls.some((u) => u.includes("/telemetry/analysis/reference") && u.includes("kind=wr") && u.includes("lap_id=1")));
assert.equal(dialog(), null);
assert.equal(window.location.hash, "#/analysis?a=1&b=7");

// not found: a plain message, no navigation, no substitute lap
window.location.hash = "#/analysis?a=1&b=2";
replies.reference.wr = refAnswer({ found: false, lap: null, reason: "no_lap" });
replies.reference.team_best = refAnswer({ kind: "team_best", found: false, lap: null, reason: "no_team" });
replies.reference.my_best = refAnswer({ kind: "my_best", found: false, lap: null, reason: "no_own_lap" });
await openDialog();
await go();
assert.equal(message(), "No suitable Garage16 Best lap available.");
assert.equal(window.location.hash, "#/analysis?a=1&b=2", "no wrong lap was chosen");
assert.ok(dialog(), "the dialog stays open so another choice can be made");
pick("team_best");
await go();
assert.ok(message().startsWith("No Team Best available."), message());
assert.ok(message().includes("not in a team"));
pick("my_best");
await go();
assert.equal(message(), "You have no analysable lap on this layout yet.");
replies.reference.team_best = refAnswer({ kind: "team_best", found: false, lap: null, reason: "no_team_lap" });
pick("team_best");
await go();
assert.equal(message(), "No Team Best available.");
assert.equal(window.location.hash, "#/analysis?a=1&b=2");

// "you already are the best": say so, offer the next lap, never the same lap twice
replies.reference.team_best = refAnswer({ kind: "team_best", already_best: true, lap: lapHeader(8, "Mate", 91, [30, 31, 30]) });
pick("team_best");
await go();
assert.equal(message(), "You already are Team Best.");
assert.equal(window.location.hash, "#/analysis?a=1&b=2", "nothing happens until the user confirms");
assert.equal(text(dialog().querySelector(".compare-dialog__go")), "Compare with the next-fastest lap");
await go();
assert.equal(dialog(), null);
assert.equal(window.location.hash, "#/analysis?a=1&b=8&note=already_best_team_best");
app = await open(window.location.hash);
assert.ok(text(app).includes("You already are Team Best — compared with the next-fastest team lap."), "the analysis explains why this lap was picked");
assert.deepEqual(parseAnalysisExtras("#/analysis?a=1&b=8&note=<script>"), { quick: null, ref: null, track: null, model: null, note: null }, "the note parameter is whitelisted");

// server trouble: a safe sentence, never the raw error
const realFetch = globalThis.fetch;
globalThis.fetch = async (url) => {
  if (String(url).includes("/reference")) return { ok: false, status: 500, statusText: "x", json: async () => ({ detail: "Traceback (most recent call last): File server.py line 1" }) };
  return realFetch(url);
};
await openDialog();
await go();
assert.equal(message(), "That did not work. Please try again.");
assert.ok(!text(dialog()).includes("Traceback"));
globalThis.fetch = realFetch;
dialog().querySelector(".compare-dialog__cancel").click();

// Lap ID
await openDialog();
pick("lap_id");
const lapInput = dialog().querySelector("#compare_lap_id_value");
assert.equal(document.querySelectorAll("[id]").length, new Set([...document.querySelectorAll("[id]")].map((n) => n.id)).size, "no duplicate ids in the dialog");
assert.equal(dialog().querySelector('label[for="compare_lap_id_value"]').textContent.trim(), "Lap ID:");
calls.length = 0;
for (const bad of ["", "abc", "0", "-3", "1.5", "1e3", "99999999999", "12 34", "<script>", "0x10"]) {
  lapInput.value = bad;
  await go();
  assert.equal(message(), "Please enter a valid lap ID (a whole number greater than 0).", `invalid id ${JSON.stringify(bad)}`);
}
assert.equal(calls.filter((u) => u.includes("/laps/")).length, 0, "an invalid id never reaches the server");
lapInput.value = "1";
await go();
assert.equal(message(), "That is the lap that is already open.");
replies.lap["404"] = { status: 404, detail: "Lap 404 not found." };
replies.lap["422"] = { status: 422, detail: "Lap 422: Telemetry file could not be read: Traceback boom" };
replies.lap["429"] = { status: 429, detail: "Too many requests" };
replies.lap["500"] = { status: 500, detail: "Traceback (most recent call last)" };
replies.lap["600"] = { body: { lap: lapHeader(600, "Elsewhere", 88, [29, 30, 29], { track_name: "Spa" }), available_channels: [] } };
for (const [id, expected] of [["404", "Lap not found."], ["422", "This lap cannot be analysed."], ["429", "Too many requests. Please wait a moment and try again."], ["500", "That did not work. Please try again."], ["600", "That lap is from a different track and cannot be compared."]]) {
  lapInput.value = id;
  await go();
  assert.equal(message(), expected, `lap ${id}`);
  assert.ok(!/Traceback|boom/.test(text(dialog())), "no stack trace is ever shown");
  assert.equal(window.location.hash, "#/analysis?a=1&b=2", "no analysis opens for a lap that failed the check");
}
lapInput.value = " 55 ";
await go();
assert.equal(dialog(), null, "a valid lap closes the dialog");
assert.equal(window.location.hash, "#/analysis?a=1&b=55");
window.location.hash = "#/analysis?a=1&b=2";

// ================================================================ quick starts
replies.quick = { mode: "wr", label: "Garage16 Best", found: true, lap_a: lapHeader(11, "Me", 91.2, [30, 31, 30]), lap_b: lapHeader(7, "Fast", 80, [27, 27, 26]), reason: null, message: null, already_best: false };
app = await open("#/analysis");
assert.deepEqual([...app.querySelectorAll(".quick__button")].map((b) => b.textContent), ["You vs. Garage16 Best", "You vs. Team Best", "You vs. My Best"]);
assert.ok(text(app).includes("Choose Lap 1"), "the lap picker is still there");
calls.length = 0;
app.querySelector('[data-quick="wr"]').click();
await settle(40);
assert.ok(calls.some((u) => u.includes("/telemetry/analysis/quick") && u.includes("mode=wr") && !u.includes("track_name")));
assert.equal(window.location.hash, "#/analysis?a=11&b=7");

// nothing to pair: the message says WHERE (the server picked the track of the latest lap) and
// offers to look at that lap alone
replies.quick = { mode: "wr", label: "Garage16 Best", found: false, lap_a: lapHeader(11, "Me", 91.2, [30, 31, 30], { track_name: "Spa" }), lap_b: null, reason: "already_best", message: "x", already_best: true };
app = await open("#/analysis");
app.querySelector('[data-quick="wr"]').click();
await settle(40);
assert.equal(app.querySelector(".quick__status").textContent, "At Spa: You already drive the fastest lap (Garage16 Best).");
const alone = app.querySelector(".quick__alone");
assert.ok(alone && alone.textContent === "Analyse Lap 1 alone", "a way on");
alone.click();
assert.equal(window.location.hash, "#/analysis?a=11");
replies.quick = { mode: "wr", label: "Garage16 Best", found: true, lap_a: lapHeader(11, "Me", 91.2, [30, 31, 30]), lap_b: lapHeader(7, "Fast", 80, [27, 27, 26]), reason: null, message: null, already_best: false };

app = await open("#/analysis?quick=wr&track=Le%20Mans");
assert.ok(calls.some((u) => u.includes("/quick") && u.includes("track_name=Le+Mans")), "the circuit link passes the track");
assert.equal(window.location.hash, "#/analysis?a=11&b=7");

replies.quick = { mode: "team_best", found: false, lap_a: lapHeader(11, "Me", 91.2, [30, 31, 30]), lap_b: null, reason: "already_best", message: "x", already_best: true };
app = await open("#/analysis?quick=team_best");
assert.ok(text(app).includes("You already are Team Best."));
assert.ok([...app.querySelectorAll("button")].some((b) => b.textContent === "Analyse Lap 1 alone"));
assert.equal(app.querySelectorAll(".chart").length, 0, "no analysis opens without a pairing");
replies.quick = { mode: "wr", found: false, lap_a: null, lap_b: null, reason: "no_own_lap", message: "x", already_best: false };
app = await open("#/analysis?quick=wr");
assert.ok(text(app).includes("You have no analysable lap on this layout yet."));
assert.equal(app.querySelectorAll(".chart").length, 0);
app = await open("#/analysis?quick=hacker&track=" + "x".repeat(300));
assert.ok(text(app).includes("Choose Lap 1"), "an unknown quick mode is ignored, not trusted");

// "?a=<id>&ref=": resolved on the server, then replaced by the real URL
replies.reference.my_best = refAnswer({ kind: "my_best", lap: lapHeader(11, "Me", 91.2, [30, 31, 30]) });
app = await open("#/analysis?a=1&ref=my_best");
assert.equal(window.location.hash, "#/analysis?a=1&b=11");

// ================================================================ laps from old clients, missing channels
world.aBasic = true;
localStorage.setItem(LAYOUT_KEY, JSON.stringify({ version: 1, layout: [{ type: "delta", size: "large" }, { type: "chart", channel: "speed", size: "large" }, { type: "chart", channel: "yaw_rate", size: "medium" }, { type: "trackmap" }, { type: "cursor_table" }, { type: "physics" }] }));
app = await open();
assert.ok(app.querySelector('[data-notice="basic-data"]'), "the old-client notice is still shown");
assert.equal(app.querySelector('[data-chart="yaw_rate"] .chart__val--a').textContent, "no data", "a channel the basic lap lacks is marked, not drawn as zeros");
assert.ok(app.querySelector('[data-chart="speed"]'), "channels it has work as usual");
world.bothMissing = ["yaw_rate"];
app = await open();
const window2 = app.querySelector('[data-channel="yaw_rate"]');
assert.ok(text(window2).includes("No data in these laps"), "no data in either lap: said plainly");
assert.equal(window2.querySelector(".chart"), null);
app = await open("#/analysis?a=1");
assert.ok(text(app.querySelector('[data-channel="yaw_rate"]')).includes("No data in this lap"));
assert.ok(app.querySelector('[data-type="delta"]').textContent.includes("second lap"), "the delta window explains it needs a second lap");
assert.equal(app.querySelector('[data-type="summary"]'), null, "a window the user removed stays removed");
world.aBasic = false;
world.bothMissing = [];
localStorage.removeItem(LAYOUT_KEY);

// ================================================================ errors keep the way out
world.fail = "The telemetry of this lap is no longer stored.";
app = await open();
assert.ok(text(app).includes("no longer stored"));
assert.ok([...app.querySelectorAll("button")].some((b) => b.textContent === "Analyse Lap 1 alone"));
world.fail = null;

assert.deepEqual(escaped, [], "no JavaScript error escaped anywhere");
console.log("ANALYSIS-LAYOUT-PAGE-OK");
