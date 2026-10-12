// V0.9.4 — the LapTime Analyzer's new parts, in jsdom.
//   * racing-line map: camera zoom keeps the point under the pointer, scale bar, the sentence at
//     the cursor, zoom to a corner (selects it, sets the charts' view, pins the apex), the high
//     resolution lines are fetched only when zoomed in, the pinch zooms in 2D
//   * corner window: phases add up, every measured value of both laps, the earlier laps
//   * coach: hints grouped by phase, "brake later" only when Lap 2 is not slower at apex/exit
//   * extra laps: charts draw Lap 3 dashed, the delta window draws its delta and has a time view
//   * refinement accepts only answers whose extra laps are on the same grid
//   * the new windows are in the layout and the module list; the pair channel has no histogram
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
dom.window.localStorage.setItem("garage16_language", "en");

const drawn = { fillText: [], dash: [] };
const ctx2d = new Proxy({}, {
  get(target, prop) {
    if (prop in target) return target[prop];
    if (prop === "fillText") return (text) => { drawn.fillText.push(String(text)); };
    if (prop === "setLineDash") return (d) => { drawn.dash.push(JSON.stringify(d)); };
    return () => {};
  },
  set(target, prop, value) { target[prop] = value; return true; },
});
dom.window.HTMLCanvasElement.prototype.getContext = function getContext() { return ctx2d; };
dom.window.HTMLCanvasElement.prototype.getBoundingClientRect = function rect() { return { left: 0, top: 0, width: 600, height: 440, right: 600, bottom: 440 }; };

const { scaleBar, createCamera, offsetText, cornerStretch, createLineMap, boundsOf } = await import("../../web/js/analysis/linemap.js");
const { createSelection, createLazy, historyFor, historySentences, renderCornerDetail, renderSectors } = await import("../../web/js/analysis/corner_detail.js");
const { cornerHints, hintsByPhase, renderCoaching } = await import("../../web/js/analysis/coaching.js");
const { createViewState } = await import("../../web/js/analysis/model.js");
const { renderChart, renderDelta, renderSummary } = await import("../../web/js/analysis/widgets.js");
const { isUsable } = await import("../../web/js/analysis/refine.js");
const { defaultLayout, SINGLE_TYPES } = await import("../../web/js/analysis/layout.js");
const { MODULES } = await import("../../web/js/analysis/modules.js");
const { parseAnalysisExtras } = await import("../../web/js/pages/analysis.js");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const text = (node) => node.textContent.replace(/\s+/g, " ").trim();
const close = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) <= eps, `${a} != ${b}`);

// ================================================================ pure helpers
assert.deepEqual(scaleBar(1), { metres: 100, px: 100 });
assert.equal(scaleBar(20).metres, 5);
assert.equal(scaleBar(0.05).metres, 2000);

const cam = createCamera(600, 400);
cam.fit({ minX: 0, maxX: 1000, minY: 0, maxY: 500 });
const wx = cam.fromX(450);
const wy = cam.fromY(120);
cam.zoomAt(3, 450, 120, 0.01, 100);
close(cam.toX(wx), 450, 1e-6);
close(cam.toY(wy), 120, 1e-6);   // the point under the pointer stays there
cam.panBy(10, -5);
close(cam.toX(wx), 460, 1e-6);
close(cam.toY(wy), 115, 1e-6);   // y up in the world, down on screen

assert.equal(offsetText(1.43, 1), "Lap 2 1.4 m further inside (left-hand corner)");
assert.equal(offsetText(-0.6, -1), "Lap 2 0.6 m further outside (right-hand corner)");
assert.equal(offsetText(0.05, 1), "Lap 2 on the same line as Lap 1");
assert.equal(offsetText(null, 1), null);
assert.deepEqual(cornerStretch({ start: 900, end: 1300, apex: 1000, entry: 920, a: { brake: 880, throttle: 1100 }, b: { brake: 890, throttle: 1120 } }), [830, 1170]);
assert.deepEqual(boundsOf([0, null, 4], [1, null, -2]), { minX: 0, maxX: 4, minY: -2, maxY: 1 });

// ================================================================ fixture: a loop with two laps + Lap 3
const N = 500;
const grid = { start: 0, step: 4, count: N };
const R = 300;
const xa = []; const ya = []; const xb = []; const yb = []; const xc = []; const yc = [];
const off = []; const side = [];
for (let i = 0; i < N; i++) {
  const th = (i / N) * 2 * Math.PI;
  xa.push(R * Math.cos(th)); ya.push(R * Math.sin(th));
  xb.push((R - 1.5) * Math.cos(th)); yb.push((R - 1.5) * Math.sin(th));
  xc.push((R + 1) * Math.cos(th)); yc.push((R + 1) * Math.sin(th));
  off.push(1.5); side.push(1);
}
const phases = [
  { phase: "braking", from: 560, to: 640, delta: 0.08 }, { phase: "entry", from: 640, to: 680, delta: 0.03 },
  { phase: "apex", from: 680, to: 720, delta: 0.05 }, { phase: "exit", from: 720, to: 800, delta: -0.01 },
  { phase: "straight", from: 800, to: 900, delta: 0.0 },
];
const corner = {
  n: 1, start: 560, entry: 600, apex: 700, end: 900, delta: 0.15, phases,
  a: { brake: 620, brake_speed: 250.2, brake_len: 70, brake_max: 95, decel_peak: 2.4, turn_in: 655, trail_m: 30, coast_m: 12, min: 101.5, apex: 702, throttle_on: 720, throttle_50: 735, throttle: 770, exit: 160.1, accel_exit: 0.45 },
  b: { brake: 640, brake_speed: 251.0, brake_len: 55, brake_max: 98, decel_peak: 2.6, turn_in: 660, trail_m: 25, coast_m: 2, min: 104.0, apex: 698, throttle_on: 712, throttle_50: 722, throttle: 750, exit: 163.0, accel_exit: 0.5 },
};
const time = (scale) => Array.from({ length: N }, (_, i) => i * 0.04 * scale);
const DATA = {
  grid, laps: {
    a: { id: 1, driver_id: 1, driver_name: "Me", car_model: "Ferrari 499P", lap_number: 3, lap_time: 90.5, sector_times: [30, 30, 30.5], is_valid: true },
    b: { id: 2, driver_id: 2, driver_name: "Fast", car_model: "Ferrari 499P", lap_number: 7, lap_time: 90.1, sector_times: [30, 30, 30.1], is_valid: true },
    c: { id: 3, driver_id: 1, driver_name: "Me", car_model: "Ferrari 499P", lap_number: 9, lap_time: 91.0, sector_times: [30, 30, 31], is_valid: true },
  },
  extra_sides: ["c"],
  delta: Array.from({ length: N }, (_, i) => i * 0.0008),
  extra_delta: { c: Array.from({ length: N }, (_, i) => i * 0.0018) },
  time: { a: time(1), b: time(0.995), c: time(1.01) },
  channels: {
    speed: { key: "speed", label: "Speed", unit: "km/h", a: Array(N).fill(200), b: Array(N).fill(201), c: Array(N).fill(199), discrete: false, fixed_range: null },
    line_offset: { key: "line_offset", label: "Line of Lap 2 vs Lap 1 (inside = +)", unit: "m", pair: true, a: off, b: null, discrete: false, fixed_range: null },
  },
  map: { x_a: xa, y_a: ya, x_b: xb, y_b: yb, x_c: xc, y_c: yc, offset_b: off, side, mirrored: false },
  sectors: { a: [600, 1300], b: [600, 1300] },
  corners: [corner],
  unavailable: [],
  recording: null,
};

// ================================================================ line map
const lineCalls = [];
const state = createViewState(DATA);
const selection = createSelection();
const charts = createViewState(DATA);
const lm = createLineMap(state, {
  selection, viewState: charts,
  fetchLine: async (opts) => {
    lineCalls.push(opts);
    const n = Math.round((opts.end - opts.start) / 0.5) + 1;
    return { grid: { start: opts.start, step: 0.5, count: n }, x_a: Array(n).fill(0), y_a: Array(n).fill(0), x_b: Array(n).fill(0), y_b: Array(n).fill(0), offset_b: Array(n).fill(0.7), side: Array(n).fill(1) };
  },
});
document.getElementById("root").appendChild(lm.element);
await sleep(30);
assert.ok(lm._camera(), "camera set up");
assert.equal(lineCalls.length, 0, "the whole lap needs no high-resolution lines");
assert.ok(lm.element.querySelectorAll(".linemap__chip").length === 1);
assert.match(text(lm.element), /Lap 1.*Lap 2.*Lap 3/);
state.setCursor(10);
await sleep(20);
assert.match(lm._info.textContent, /Lap 2 1\.5 m further inside/);

// zoom to the corner: selected, charts zoomed, apex pinned, then high resolution after 250 ms
lm.element.querySelector(".linemap__chip").click();
assert.equal(selection.get(), 1);
const [vlo, vhi] = charts.getView();
assert.ok(vlo <= 600 && vhi >= 800, `${vlo}-${vhi}`);
assert.ok(state.isPinned());
assert.equal(state.getCursor(), 175);   // 700 m / 4 m
await sleep(350);
assert.equal(lineCalls.length, 1, "zoomed into a corner: the lines are loaded in high resolution");
assert.equal(lineCalls[0].step, 0.5);
assert.ok(lineCalls[0].end - lineCalls[0].start < 900);
await sleep(20);
assert.ok(lm._detail());
assert.match(lm._info.textContent, /high resolution/);
assert.match(lm._info.textContent, /0\.7 m further inside/, "the offset at the cursor comes from the detail");

// a two-finger pinch zooms around its centre (2D)
const canvas = lm.element.querySelector("canvas");
const before = lm._camera().scale;
const touchEvent = (type, pts) => {
  const ev = new dom.window.Event(type, { cancelable: true });
  ev.touches = pts.map(([x, y]) => ({ clientX: x, clientY: y }));
  canvas.dispatchEvent(ev);
};
touchEvent("touchstart", [[200, 200], [300, 260]]);
touchEvent("touchmove", [[150, 170], [350, 290]]);
touchEvent("touchend", []);
assert.ok(lm._camera().scale > before * 1.5, "fingers apart = zoom in");
lm.destroy();

// ================================================================ corner detail + history
const HISTORY = { lap_id: 1, corners: [[700, 620, 101.5, 770, 160.1]], laps: [
  { id: 11, lap_number: 1, lap_time: 91, corners: [[705, 630, 103, 760, 164]] },
  { id: 12, lap_number: 2, lap_time: 91, corners: [[698, 632, 103.5, 765, 165]] },
  { id: 13, lap_number: 4, lap_time: 91, corners: [[702, 610, 100, 780, 158]] },
  { id: 14, lap_number: 5, lap_time: 91, corners: [[400, 300, 90, 450, 150]] },   // another corner only
] };
const rows = historyFor(corner, HISTORY);
assert.equal(rows.length, 3);
assert.deepEqual(historySentences(corner, rows), [
  "In 2 of your last 3 laps you braked later than here.",
  "In 2 of your last 3 laps you left the corner faster.",
]);

const base = { state, data: DATA, baseData: DATA, selection, history: createLazy(async () => HISTORY), lapSectors: createLazy(async () => SECTORS) };
const SECTORS = { lap_id: 1, laps: [{ id: 1, lap_number: 3, lap_time: 90.5, is_valid: true, sector_times: [30, 30, 30.5] }, { id: 5, lap_number: 4, lap_time: 90.2, is_valid: true, sector_times: [29.9, 30.1, 30.2] }],
  session_best: [{ sector: 1, time: 29.9, lap_id: 5, lap_number: 4 }, { sector: 2, time: 30, lap_id: 1, lap_number: 3 }, { sector: 3, time: 30.2, lap_id: 5, lap_number: 4 }],
  session_ideal: 90.1, personal_best: null, personal_ideal: null, potential_session: [0.1, 0, 0.3], potential_personal: null, calculated: true };
const cd = renderCornerDetail({ type: "corner_detail" }, base);
document.getElementById("root").appendChild(cd.element);
await sleep(20);
const phaseCells = cd.element.querySelectorAll(".cdetail__phase");
assert.equal(phaseCells.length, 5);
assert.ok(cd.element.querySelector('[data-phase="braking"]').classList.contains("cdetail__phase--worst"));
assert.match(text(cd.element), /\+0\.150 s in total/);
const brakeRow = cd.element.querySelector('tr[data-metric="brake"]');
assert.match(text(brakeRow), /−20 m/);
assert.ok(brakeRow.lastChild.classList.contains("analysis-diff--loss"), "braking 20 m earlier is marked as a loss");
assert.ok(cd.element.querySelector('tr[data-metric="turn_in"] .stint-tag--estimate'), "turn-in is an estimate");
assert.match(text(cd.element), /At the apex Lap 2 is 1\.5 m further inside than Lap 1/);
assert.match(text(cd.element), /This corner on 3 earlier laps/);
assert.match(text(cd.element), /In 2 of your last 3 laps you braked later than here/);

const sec = renderSectors({ type: "sectors" }, base);
await sleep(10);
assert.equal(sec.element.querySelectorAll(".lsectors__best").length, 3);
assert.match(text(sec.element), /Best sectors of the session \(calculated\): 1:30\.100/);

// ================================================================ coach
const lapVals = (brake, min, throttle, exit) => ({ brake, min, throttle, exit });
let hints = cornerHints({ a: lapVals(980, 95, 1400, 156), b: lapVals(1000, 90, 1400, 156) });
assert.ok(!hints.some((h) => h.kind === "brake_later"), "Lap 2 brakes later but is slower at the apex: no 'brake later'");
hints = cornerHints({ a: lapVals(980, 95, 1400, 156), b: lapVals(1000, 95, 1400, 150) });
assert.ok(!hints.some((h) => h.kind === "brake_later"), "... nor when it is slower on the exit");
hints = cornerHints({ a: lapVals(980, 95, 1400, 156), b: lapVals(1000, 95.5, 1400, 156) });
assert.ok(hints.some((h) => h.kind === "brake_later"));
const grouped = hintsByPhase(corner, cornerHints(corner));
assert.deepEqual(grouped.map((g) => g.phase), ["braking", "entry", "apex", "exit"]);   // exit: no loss, but a measured hint (throttle 20 m later)
assert.equal(grouped.find((g) => g.phase === "straight"), undefined, "a phase without loss and without hint is left out");
assert.ok(grouped[0].hints.some((h) => h.kind === "brake_later"));
assert.ok(grouped[2].hints.some((h) => h.kind === "min_speed"));
const coach = renderCoaching({ type: "coaching" }, base);
await sleep(20);
assert.match(text(coach.element), /Braking \+0\.080 s/);
assert.match(text(coach.element), /In 2 of your last 3 laps you braked later than here/);

// ================================================================ extra laps in charts, delta, summary
drawn.dash.length = 0;
const ch = renderChart({ type: "chart", channel: "speed", size: "large" }, { ...base, catalog: { channels: [] } });
document.getElementById("root").appendChild(ch.element);
await sleep(20);
assert.equal(ch.element.querySelectorAll(".chart__val").length, 3, "Lap 1, Lap 2, Lap 3");
assert.ok(drawn.dash.includes("[7,4]"), "Lap 3 is dashed");
const pair = renderChart({ type: "chart", channel: "line_offset", size: "large" }, { ...base, catalog: { channels: [] } });
assert.equal(pair.element.querySelectorAll(".chart__val").length, 1, "the line offset is one series");
const dl = renderDelta({ type: "delta", size: "large" }, { ...base, pinAt: () => {} });
document.getElementById("root").appendChild(dl.element);
assert.equal(dl.element.querySelectorAll(".chart__val").length, 2, "Lap 1 − Lap 2, and Lap 3 − reference");
const modeSelect = dl.element.querySelector(".delta-widget__mode");
modeSelect.value = "time";
modeSelect.dispatchEvent(new dom.window.Event("change"));
assert.equal(dl.element.querySelectorAll(".chart__val").length, 3, "elapsed time of all three laps");
const sum = renderSummary({ type: "summary" }, base);
assert.match(text(sum.element), /LAP 3/);

// ================================================================ refinement, layout, URL
const resp = { grid: { start: 100, step: 1, count: 201 }, laps: { a: {} }, time: { a: Array(201).fill(0), c: Array(200).fill(0) }, channels: {} };
assert.equal(isUsable(resp, { start: 100, end: 300, extraSides: ["c"] }), false, "Lap 3 on a different grid is refused");
resp.time.c = Array(201).fill(0);
assert.equal(isUsable(resp, { start: 100, end: 300, extraSides: ["c"] }), true);

for (const type of ["line", "corner_detail", "sectors"]) {
  assert.ok(SINGLE_TYPES.includes(type));
  assert.ok(MODULES.find((m) => m.type === type), type);
}
assert.ok(defaultLayout().widgets.some((w) => w.type === "line"), "the racing lines are in the standard layout");
assert.deepEqual(parseAnalysisExtras("#/analysis?a=1&b=2&c=7&d=7").laps, [7]);
assert.deepEqual(parseAnalysisExtras("#/analysis?a=1&c=x&d=9").laps, [9]);

console.log("V094-LAPTIME-OK");
