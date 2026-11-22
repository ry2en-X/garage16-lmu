// V1.0.3 — the interactive charts of the Stint Analyzer (web/js/stints/chart.js, core/chart_core.js)
// and the new sections (pace modes, tyres per wheel, fuel tank, sector heat map, positions), in jsdom.
//   * core: ticks, scales, nearest, zoom/pan windows, the chart group (cursor, pin, window)
//   * chart: value box on pointer move, the same lap on every chart of a group, legend hides a
//     series, drag zooms all charts of the group, double-click and Escape reset, keys move the cursor
//   * page: pace modes (gap to best, fuel-corrected only with a measured fuel effect), wheels,
//     wear forecast, tank chart, sector heat map, position chart
import { JSDOM } from "jsdom";
import assert from "node:assert/strict";

const dom = new JSDOM(`<!doctype html><body><div id="root"></div></body>`, { url: "http://localhost/stints?lap=1010", pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.localStorage = dom.window.localStorage;
globalThis.sessionStorage = dom.window.sessionStorage;
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
globalThis.history = dom.window.history;
globalThis.location = dom.window.location;
dom.window.GARAGE16_API_BASE_URL = "";
dom.window.localStorage.setItem("lmu_garage_session", JSON.stringify({ driverId: 1, displayName: "Me", viaCookie: true }));
dom.window.localStorage.setItem("garage16_language", "en");
const { Event, MouseEvent, KeyboardEvent } = dom.window;
// jsdom has no PointerEvent: a MouseEvent with the pointer fields is enough for the handlers
const pointer = (type, clientX, extra = {}) => {
  const e = new MouseEvent(type, { bubbles: true, clientX, clientY: 50, ...extra });
  Object.defineProperty(e, "pointerType", { value: extra.pointerType || "mouse" });
  Object.defineProperty(e, "pointerId", { value: 1 });
  return e;
};

// ---------------------------------------------------------------- a realistic session
const W = ["fl", "fr", "rl", "rr"];
const rnd = (i, k) => { const x = Math.sin(i * 12.9898 + k * 78.233) * 43758.5453; return x - Math.floor(x); };
function sessionLaps() {
  const laps = [];
  let fuel = 90;
  let wear = { fl: 100, fr: 100, rl: 100, rr: 100 };
  let stint = 1;
  let age = 0;
  for (let i = 1; i <= 34; i += 1) {
    if (i === 13 || i === 25) { stint += 1; age = 0; fuel = Math.min(90, fuel + (i === 13 ? 0 : 60)); // tyres only at the first stop: fuel and tyre age separable
      wear = { fl: 100, fr: 100, rl: 100, rr: 100 }; }
    age += 1;
    const used = 2.4 + rnd(i, 1) * 0.2;
    const start = fuel;
    fuel -= used;
    for (const w of W) wear[w] -= (w[0] === "f" ? 0.55 : 0.4) + rnd(i, w.charCodeAt(1)) * 0.1;
    const outlap = i === 13 || i === 25;
    const inlap = i === 12 || i === 24;
    const traffic = [5, 17, 29].includes(i);
    // a measurable fuel effect (0.035 s per litre) and tyre wear (0.06 s per lap of age)
    const t = 103.2 + 0.035 * start + 0.06 * age + (rnd(i, 2) - 0.5) * 0.3 + (outlap ? 18 : 0) + (inlap ? 14 : 0) + (traffic ? 1.4 : 0);
    const flags = [...(outlap ? ["outlap", "pit_stop"] : []), ...(inlap ? ["inlap"] : []), ...(traffic ? ["traffic"] : [])];
    laps.push({
      id: 1000 + i, lap_number: i, lap_time: t, is_valid: true, sector_times: [t * 0.31 + rnd(i, 3) * 0.2, t * 0.38 + rnd(i, 4) * 0.3, t * 0.31],
      condition: "dry", stint, tyre_set: stint, tyre_age: age,
      pit: { lane_s: outlap ? 25 : inlap ? 15 : 0, stationary_s: outlap ? 28 : 0, starts_in_pits: outlap, ends_in_pits: inlap },
      pitstops: stint - 1, place: Math.max(1, 6 - Math.floor(i / 7) + (traffic ? 1 : 0)), compound: "Medium",
      fuel_start_l: start, fuel_end_l: fuel, fuel_used_l: used, fuel_added_l: outlap ? 60 : null, fuel_sector_l: [0.8, 0.9, 0.8], fuel_capacity_l: 90,
      energy_used_pct: 3.1, energy_end_pct: 90 - 2 * i, engine_map: 1, tyres_changed: outlap ? W : null,
      wear_left_pct: { ...wear }, wear_left_avg: (wear.fl + wear.fr + wear.rl + wear.rr) / 4, wear_used_avg: 0.48,
      tyre_temp_c: { fl: 88 + age * 0.3, fr: 91 + age * 0.3, rl: 84, rr: 86 }, tyre_temp_avg: 87,
      tyre_pressure_kpa: { fl: 182, fr: 184, rl: 176, rr: 177 }, tyre_pressure_avg: 180, brake_max_c: { fl: 620, fr: 640, rl: 480, rr: 470 },
      track_temp: 28 + i * 0.1, traffic_ahead_share: traffic ? 0.3 : 0, traffic_behind_share: 0, blue_flag_share: 0, places_gained: 0, places_lost: 0,
      damage: { dent_max: 0, overheating: false, detached: false }, session_left_s: 7200 - 105 * i, max_laps: 0, flags, clean: flags.length === 0,
    });
  }
  return laps;
}
const laps = sessionLaps();
const SESSION = {
  laps,
  stints: [1, 2, 3].map((n) => {
    const ls = laps.filter((l) => l.stint === n);
    return { number: n, reasons: n === 1 ? ["session_start"] : ["pit"], lap_ids: ls.map((l) => l.id), first_lap: ls[0].lap_number, last_lap: ls[ls.length - 1].lap_number,
      laps: ls.length, clean_laps: ls.filter((l) => l.clean).length, compound: "Medium", tyre_set: n, wear_left_start: 100, wear_left_end: ls[ls.length - 1].wear_left_avg,
      fuel_start_l: ls[0].fuel_start_l, fuel_end_l: ls[ls.length - 1].fuel_end_l, duration_s: 1300 };
  }),
  pit_stops: [12, 24].map((n) => ({ kind: "stop", lap_ids: [1000 + n, 1001 + n], lap_number: n, lane_s: 40, stationary_s: 28, fuel_added_l: 60, tyres_changed: true,
    compound_before: "Medium", compound_after: "Medium", loss_s: 31, loss_estimated: true })),
  tyre_sets: [1, 2, 3].map((n) => ({ number: n, compound: "Medium", stints: [n], wear_left_start: 100, laps: 12, new_at_start: n > 1 ? true : null, wear_left_end: 94 })),
  summary: { laps: 34, clean_laps: 28, valid_laps: 34, fuel_capacity_l: 90, fuel_end_l: laps[laps.length - 1].fuel_end_l, energy_end_pct: 20, session_left_s: 3600, max_laps: 0,
    has_pit_data: true, has_context: true, has_traffic: true },
  driver: { id: 1, name: "Me", own: true }, partial: false, track_name: "Fuji Speedway", car_model: "Porsche 963", session_type: 10, started: 1700000000, ended: 1700004000,
};
globalThis.fetch = async (url) => {
  const u = new URL(url, "http://x");
  const json = (body, status = 200) => ({ ok: status < 400, status, statusText: String(status), json: async () => body, text: async () => JSON.stringify(body) });
  if (u.pathname === "/api/v1/stints/session") return json(SESSION);
  if (u.pathname === "/api/v1/stints/drivers") return json({ drivers: [] });
  if (u.pathname === "/api/v1/stints/rule-sets") return json({ rule_sets: [] });
  if (u.pathname === "/api/v1/analysis/theoretical-best") return json({ found: false });
  return json({}, 200);
};

// ================================================================ core
const C = await import("../../web/js/core/chart_core.js");
assert.deepEqual(C.niceTicks(0, 10, 5), [0, 2, 4, 6, 8, 10]);
assert.deepEqual(C.niceTicks(0.1, 0.7, 3), [0.2, 0.4, 0.6], "no float noise in the labels");
assert.ok(C.integerTicks(1, 4, 8).every(Number.isInteger), "laps have no half ticks");
const sc = C.linearScale([0, 10], [100, 200]);
assert.equal(sc(5), 150);
assert.equal(sc.invert(150), 5);
assert.equal(C.nearestIndex([1, 2, 5, 9], 6), 2);
assert.deepEqual(C.paddedRange([5, 5]), [4.5, 5.5]);
assert.equal(C.zoomWindow([0, 30], 2, 15, [0, 30]), null, "zooming out past everything = no zoom");
const z = C.zoomWindow([0, 30], 0.5, 15, [0, 30]);
assert.deepEqual(z, [7.5, 22.5], "zoom in around the pointer");
assert.deepEqual(C.panWindow([20, 28], 5, [0, 30]), [22, 30], "panning stops at the end");
assert.deepEqual(C.zoomWindow([0, 30], 0.01, 0, [0, 30], 2), [0, 2], "never narrower than the minimum");
const g = C.createChartGroup();
const seen = [];
g.subscribe((st) => seen.push(st.cursor));
g.setCursor(3);
g.pin(4);
g.setCursor(5);
assert.equal(g.state.cursor, 4, "a pinned cursor does not follow the pointer");
g.pin(4);
assert.equal(g.state.pinned, false, "pinning the same lap again releases it");
g.setWindow([5, 2]);
assert.equal(g.state.window, null, "an empty window is no window");

// ================================================================ the chart component
const { chart } = await import("../../web/js/stints/chart.js");
const root = document.getElementById("root");
const group = C.createChartGroup();
const a = chart([
  { kind: "dots", label: "Stint 1", color: "#f00", points: [[1, 90], [2, 91], [3, 90.5]] },
  { kind: "line", label: "avg", color: "#fff", points: [[1, 90], [2, 90.5], [3, 90.5]] },
], { group, legend: true, width: 600, height: 200, xLabel: "lap", tooltipTitle: (x) => `Lap ${x}` });
const b = chart([{ kind: "bars", label: "fuel", color: "#ff0", points: [[1, 2], [2, 2.1], [3, 2.2]], format: (v) => `${v} L` }],
  { group, width: 600, height: 120, yZero: true });
root.append(a, b);
const svgA = a.querySelector("svg");
assert.equal(svgA.querySelectorAll("circle").length, 3);
assert.equal(b.querySelectorAll("rect.schart__ground").length, 1);
assert.equal(b.querySelectorAll(".schart__bars rect").length, 3);
// pointer at x ≈ lap 2: (600 - 58 - 14) / 2 + 58 = 322 px
svgA.dispatchEvent(pointer("pointermove", 322));
const tipA = a.querySelector(".schart__tip");
const tipB = b.querySelector(".schart__tip");
assert.equal(tipA.hidden, false);
assert.match(tipA.textContent, /Lap 2/);
assert.match(tipA.textContent, /91/);
assert.equal(tipB.hidden, false, "the other chart of the group shows the same lap");
assert.match(tipB.textContent, /2\.1 L/);
assert.ok(b.querySelector(".schart__crosshair"));
svgA.dispatchEvent(pointer("pointerleave", 0));
assert.equal(tipA.hidden, true);
assert.equal(tipB.hidden, true);
// legend: hide the dots
const toggle = [...a.querySelectorAll(".stint-legend__toggle")].find((x) => /Stint 1/.test(x.textContent));
toggle.click();
assert.equal(a.querySelectorAll(".stint-chart__dots circle").length, 0);
assert.equal(toggle.getAttribute("aria-pressed"), "false");
toggle.click();
assert.equal(a.querySelectorAll(".stint-chart__dots circle").length, 3);
// keys move the cursor
svgA.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
assert.equal(group.state.cursor, 1);
svgA.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
assert.equal(group.state.cursor, 2);
svgA.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));

// zoom by dragging needs a range of laps: a longer chart
const longGroup = C.createChartGroup();
const pts = Array.from({ length: 30 }, (_, i) => [i + 1, 90 + (i % 5) * 0.2]);
const c1 = chart([{ kind: "dots", label: "laps", color: "#f00", points: pts }], { group: longGroup, width: 600, height: 200 });
const c2 = chart([{ kind: "line", label: "x", color: "#0f0", points: pts }], { group: longGroup, width: 600, height: 120 });
root.append(c1, c2);
const s1 = c1.querySelector("svg");
const px = (lap) => 58 + ((lap - 1) / 29) * (600 - 58 - 14);
s1.dispatchEvent(pointer("pointerdown", px(10)));
s1.dispatchEvent(pointer("pointermove", px(15)));
assert.ok(c1.querySelector(".schart__brush"), "dragging shows the range");
s1.dispatchEvent(pointer("pointerup", px(20)));
assert.ok(longGroup.state.window && Math.abs(longGroup.state.window[0] - 10) < 0.01 && Math.abs(longGroup.state.window[1] - 20) < 0.01, JSON.stringify(longGroup.state.window));
assert.equal(c1.querySelectorAll(".stint-chart__dots circle").length, 11, "only laps 10–20 drawn");
assert.equal(c2.querySelector(".schart__reset").hidden, false, "the other chart zoomed too and offers the way back");
s1.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
assert.equal(longGroup.state.window, null);
assert.equal(c1.querySelectorAll(".stint-chart__dots circle").length, 30);
// Ctrl + wheel zooms, a plain wheel does not (the page must scroll)
const wheel = (ctrl, dx = 0, dy = -100) => { const e = new dom.window.WheelEvent("wheel", { bubbles: true, cancelable: true, ctrlKey: ctrl, deltaX: dx, deltaY: dy, clientX: px(15) }); s1.dispatchEvent(e); return e; };
const plain = wheel(false);
assert.equal(plain.defaultPrevented, false);
assert.equal(longGroup.state.window, null);
wheel(true);
assert.ok(longGroup.state.window, "Ctrl + wheel zooms");
// a chart removed from the page leaves the group at the next change
longGroup.setCursor(4);
const before = c2.querySelector(".schart__tip").textContent;
c2.remove();
longGroup.setCursor(5);
longGroup.setCursor(6);
assert.equal(c2.querySelector(".schart__tip").textContent, before, "a removed chart is no longer updated");
// a click pins
longGroup.setWindow(null);
s1.dispatchEvent(pointer("pointerdown", px(7)));
s1.dispatchEvent(pointer("pointerup", px(7)));
assert.equal(longGroup.state.pinned, true);
assert.equal(longGroup.state.cursor, 7);
// an empty chart draws nothing and does not throw
assert.ok(chart([], {}).querySelector("svg"));
// an inverted axis (positions: 1 on top)
const pos = chart([{ kind: "line", color: "#00f", points: [[1, 1], [2, 5]] }], { invertY: true, width: 300, height: 100 });
const line = pos.querySelector("polyline").getAttribute("points").split(" ").map((q) => Number(q.split(",")[1]));
assert.ok(line[0] < line[1], "P1 is drawn above P5");
root.innerHTML = "";

// ================================================================ the page
const { renderStints } = await import("../../web/js/pages/stints.js");
await renderStints(root);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
for (let i = 0; i < 40 && !root.querySelector("#stint-pace"); i += 1) await sleep(10);
const text = (node) => (node ? node.textContent.replace(/\s+/g, " ") : "");
const pace = root.querySelector("#stint-pace");
assert.ok(pace, "the session dashboard rendered");
assert.ok(pace.querySelectorAll(".schart__region").length >= 3, "one band per stint");
assert.ok([...pace.querySelectorAll(".stint-chart__marker-label")].some((m) => /Pit 28 s/.test(m.textContent)), "pit stops are marked with the standing time");
const modeButtons = [...pace.querySelectorAll(".seg__btn")];
assert.deepEqual(modeButtons.map((x) => x.textContent), ["Lap time", "Gap to best", "Fuel-corrected"]);
const fuelButton = modeButtons[2];
assert.equal(fuelButton.hasAttribute("disabled"), false, "three stints with refuelling: the fuel effect can be measured");
// hovering the pace chart moves the cursor on the fuel and wheel charts too
const paceSvg = pace.querySelector("svg");
paceSvg.dispatchEvent(pointer("pointermove", 300));
const wheels = root.querySelector("#stint-wheels");
assert.ok(wheels, "tyres per wheel");
assert.equal(wheels.querySelector(".schart__tip").hidden, false, "the session charts share one cursor");
assert.match(text(wheels.querySelector(".schart__tip")), /front left/);
paceSvg.dispatchEvent(pointer("pointerleave", 0));
// gap mode: the best lap is at 0
modeButtons[1].click();
const pace2 = root.querySelector("#stint-pace");
assert.match(text(pace2.querySelector(".schart__hline-label")), /best/);
assert.equal(pace2.querySelector(".seg__btn--on").textContent, "Gap to best");
// fuel-corrected: the measured fuel effect is named and marked as an estimate
[...pace2.querySelectorAll(".seg__btn")][2].click();
const pace3 = root.querySelector("#stint-pace");
assert.match(text(pace3), /Fuel effect measured in this session: \+0\.0\d\d s per litre/);
assert.ok(pace3.querySelector(".stint-tag--estimate"));
// wheels: switch to tread left → wear forecast
const wearButton = [...root.querySelectorAll("#stint-wheels .seg__btn")].find((x) => x.textContent === "Tread left");
wearButton.click();
const wheels2 = root.querySelector("#stint-wheels");
assert.match(text(wheels2), /Current tyre set: front (left|right) wears fastest, 0\.\d\d % per lap/);
assert.ok(wheels2.querySelector(".stint-tag--estimate"));
// fuel: the tank chart and the consumption bars
const fuel = root.querySelector("#stint-fuel");
assert.ok([...fuel.querySelectorAll("svg")].length >= 2);
assert.ok(fuel.querySelectorAll(".schart__bars rect").length === 34);
// sectors: heat map with the sector that costs most
const sectors = root.querySelector("#stint-sectors");
assert.equal(sectors.querySelectorAll(".heat").length, 34 * 3);
assert.match(text(sectors), /The most time to find: sector \d/);
// traffic: position over the session, P1 on top
const traffic = root.querySelector("#stint-traffic");
assert.match(text(traffic), /P\d/);
assert.ok(traffic.querySelector("svg"));
// filters keep the zoom: zoom, change a filter, still zoomed
const ps = root.querySelector("#stint-pace svg");
ps.dispatchEvent(pointer("pointerdown", 100));
ps.dispatchEvent(pointer("pointermove", 250));
ps.dispatchEvent(pointer("pointerup", 250));
const resetButtons = () => [...root.querySelectorAll(".schart__reset")].filter((x) => !x.hidden).length;
assert.ok(resetButtons() >= 3, "every session chart zoomed");
const trafficBox = [...root.querySelectorAll(".stint-filter")].find((l) => /traffic/.test(l.textContent)).querySelector("input");
trafficBox.checked = false;
trafficBox.dispatchEvent(new Event("change"));
assert.ok(resetButtons() >= 3, "a filter change keeps the zoom");

console.log("V103-CHARTS-OK");
