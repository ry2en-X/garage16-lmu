// V1.0.3 — "Progress" on the public driver profile: model (step line of the personal best, dots per
// day), the page section with selector, chart and summary line, in jsdom against a mocked API.
import { JSDOM } from "jsdom";
import assert from "node:assert/strict";

const dom = new JSDOM(`<!doctype html><body><div id="root"></div></body>`, { url: "http://localhost/driver/pro", pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.localStorage = dom.window.localStorage;
globalThis.sessionStorage = dom.window.sessionStorage;
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
globalThis.history = dom.window.history;
globalThis.location = dom.window.location;
dom.window.GARAGE16_API_BASE_URL = "";
dom.window.localStorage.setItem("garage16_language", "en");

const COMBOS = [
  { track_name: "Fuji Speedway", car_model: "499P", car_name: "Ferrari 499P", car_class: "Hypercar", laps: 5, last_day: "2026-11-10" },
  { track_name: "Monza", car_model: "296", car_name: "Ferrari 296 GT3", car_class: "GT3", laps: 3, last_day: "2026-10-01" },
];
const DAYS = {
  Fuji: [
    { day: "2026-11-01", best: 100.0, median: 101.5, laps: 2 },
    { day: "2026-11-03", best: 101.0, median: 102.0, laps: 2 },
    { day: "2026-11-10", best: 98.5, median: 99.2, laps: 1 },
  ],
  Monza: [{ day: "2026-10-01", best: 110.0, median: 111.0, laps: 2 }, { day: "2026-10-02", best: 110.0, median: 110.0, laps: 1 }],
};
const calls = [];
globalThis.fetch = async (url) => {
  const u = new URL(url, "http://localhost");
  calls.push(u.pathname + u.search);
  const json = (body, status = 200) => ({ ok: status < 400, status, statusText: "x", json: async () => body });
  if (u.pathname === "/api/v1/public/drivers/7/progress") {
    const track = u.searchParams.get("track");
    if (!track) return json({ combos: COMBOS, selected: { track_name: "Fuji Speedway", car_model: "499P" }, days: DAYS.Fuji });
    return json({ combos: COMBOS, selected: { track_name: track, car_model: u.searchParams.get("car_model") }, days: track === "Monza" ? DAYS.Monza : [] });
  }
  if (u.pathname === "/api/v1/public/drivers/8/progress") return json({ combos: [], selected: null, days: [] });
  if (u.pathname === "/api/v1/public/drivers/9/progress") return json({ detail: "Driver not found." }, 404);
  return json({ detail: `unexpected ${u.pathname}` }, 404);
};
const settle = (ms = 60) => new Promise((r) => setTimeout(r, ms));

const { progressModel, progressSection, dayNumber } = await import("../../web/js/pages/driver_progress.js");

// ---------------------------------------------------------------- model
assert.equal(progressModel([]), null);
const m = progressModel(DAYS.Fuji);
assert.deepEqual(m.dots.map((p) => p.slice(0, 2)), [[0, 100], [2, 101], [9, 98.5]], "x = days since the first day");
// step line: flat at 100 until day 9, then falls to 98.5; the slower day 3 does not touch it
assert.deepEqual(m.pb, [[0, 100], [9, 100], [9, 98.5]]);
assert.equal(m.improved, 1.5);
assert.equal(m.days, 3);
assert.deepEqual(m.median.map((p) => p[1]), [101.5, 102, 99.2]);
assert.equal(dayNumber("2026-11-02") - dayNumber("2026-11-01"), 1);
const flat = progressModel(DAYS.Monza);
assert.equal(flat.improved, 0);
assert.deepEqual(flat.pb, [[0, 110], [1, 110]], "the line reaches the last day");

// ---------------------------------------------------------------- section
const root = document.getElementById("root");
root.appendChild(progressSection(7));
await settle(150);
assert.ok(root.querySelector("h2").textContent === "Progress");
const select = root.querySelector("select");
assert.equal(select.options.length, 2);
assert.equal(select.value, "0", "the most recently driven combination is selected");
assert.deepEqual(calls, ["/api/v1/public/drivers/7/progress"]);
assert.ok(root.querySelector(".schart svg"), "the chart is drawn");
assert.match(root.querySelector(".driver-progress__summary").textContent, /improved by 1\.500 s since .*2026.*\(3 days with laps\)/);

select.value = "1";
select.dispatchEvent(new dom.window.Event("change"));
await settle(150);
assert.ok(calls.at(-1).includes("track=Monza") && calls.at(-1).includes("car_model=296"));
assert.equal(root.querySelector("select").value, "1");
assert.match(root.querySelector(".driver-progress__summary").textContent, /No improvement since/);
assert.equal(root.querySelectorAll(".schart").length, 1, "redrawn, not duplicated");

// no combination with 2 laps; unknown driver
const empty = document.createElement("div");
empty.appendChild(progressSection(8));
await settle();
assert.ok(empty.querySelector(".empty-state") && !empty.querySelector("select"));
const failing = document.createElement("div");
failing.appendChild(progressSection(9));
await settle();
assert.ok(failing.querySelector(".notice--error"));

console.log("V103-DRIVER-PROGRESS-OK");
