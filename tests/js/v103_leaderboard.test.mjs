// V1.0.3: leaderboard starts at the track of the most recent activity; the activity feed of the
// dashboard collapses consecutive records at one track into one line.
import { JSDOM } from "jsdom";
import assert from "node:assert/strict";

const dom = new JSDOM(`<!doctype html><body><div id="root"></div></body>`, { url: "http://localhost/leaderboard" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.localStorage = dom.window.localStorage;
globalThis.sessionStorage = dom.window.sessionStorage;
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
dom.window.GARAGE16_API_BASE_URL = "";

const VENUES = [
  { venue: "Monza", lap_count: 50, layouts: [{ track_name: "Monza", lap_count: 50 }] },
  { venue: "Spa-Francorchamps", lap_count: 3, layouts: [{ track_name: "Spa-Francorchamps", lap_count: 3 }] },
];
const calls = [];
globalThis.fetch = async (url) => {
  calls.push(url);
  const u = new URL(url, "http://x");
  const json = (body) => ({ ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) });
  if (u.pathname === "/api/v1/public/catalog/venues") return json(VENUES);
  if (u.pathname === "/api/v1/public/activity") return json([{ driver_id: 1, driver_name: "A", track_name: "Spa-Francorchamps", car_name: "x", lap_time: 100, uploaded_at: "2026-11-20T10:00:00" }]);
  if (u.pathname === "/api/v1/public/catalog/class-options") return json([{ name: "GT3", lap_count: 1 }, { name: "Hypercar", lap_count: 2 }]);
  if (u.pathname === "/api/v1/public/catalog/cars") return json(["Car"]);
  if (u.pathname.startsWith("/api/v1/public/leaderboards/class/")) {
    return json([
      { driver_id: 1, driver_name: "A", lap_time: 90.5, sector_times: [30, 30, 30.5], is_valid: true, uploaded_at: "2026-09-28T10:00:00", lap_id: 5 },
      { driver_id: 2, driver_name: "B", lap_time: 91.5, sector_times: [30, 30, 31.5], is_valid: true, uploaded_at: "2026-09-28T10:00:00" },
    ]);
  }
  return { ok: false, status: 404, json: async () => ({ detail: "unexpected" }), text: async () => "" };
};

const { renderLeaderboard } = await import("../../web/js/pages/leaderboard.js");
const { renderActivityList, collapseActivity } = await import("../../web/js/core/activity.js");
const root = document.getElementById("root");
await renderLeaderboard(root);
await new Promise((r) => setTimeout(r, 50));

// 1. the track of the newest lap wins over the busiest track; the class most driven there is chosen
assert.equal(root.querySelector("#venueSelect").value, "Spa-Francorchamps");
assert.equal(root.querySelector("#classSelect").value, "Hypercar");
assert.equal(window.location.pathname, "/leaderboard/spa-francorchamps/hypercar");
assert.equal(window.history.length, 1, "the address was replaced, no history entry");
assert.ok(!root.textContent.includes("Pick a track"), "no empty-state text next to a loaded table");
// 2. headers for every column, P1 gap shown
const heads = [...root.querySelectorAll("thead th")].map((th) => th.textContent);
assert.ok(heads.every((h) => h.length > 0), heads.join("|"));
assert.equal(root.querySelector("tbody tr .lap-gap").textContent, "—");

// 3. the dashboard feed collapses consecutive records at one track
const items = [3, 2, 1].map((i) => ({ record_type: "PR", track_name: "Fuji Speedway", car_name: "c", car_model: "963", lap_time: 100 + i, lap_id: i, created_at: `2026-11-20T10:0${i}:00` }));
items.push({ record_type: "WR", track_name: "Monza", car_name: "c", car_model: "963", lap_time: 90, lap_id: 9, created_at: "2026-11-19T10:00:00" });
const collapsed = collapseActivity((await import("../../web/js/core/activity.js")).groupActivity(items));
assert.equal(collapsed.length, 2);
assert.equal(collapsed[0].count, 3);
assert.equal(collapsed[0].best_time, 101);
assert.equal(collapsed[0].best_lap_id, 1);
const card = renderActivityList(items, { collapse: true });
assert.equal(card.querySelectorAll(".activity__row").length, 2);
assert.ok(card.querySelector(".activity__where").textContent.startsWith("3 records"));
assert.equal(renderActivityList(items).querySelectorAll(".activity__row").length, 4, "without collapse: one row per lap");
console.log("V103-LEADERBOARD-OK");
