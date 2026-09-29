// Real DOM test (jsdom) for the leaderboard's search-first track picker
// (V0.8.9): searchable venues with all layouts, every class always offered,
// known-but-undriven tracks still selectable.
import { JSDOM } from "jsdom";
import assert from "node:assert/strict";

const dom = new JSDOM(`<!doctype html><body><div id="root"></div></body>`, { url: "http://localhost/" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.localStorage = dom.window.localStorage;
globalThis.sessionStorage = dom.window.sessionStorage;
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
dom.window.LMU_GARAGE_API_BASE_URL = "";

const CLASSES = ["Hypercar", "LMP2 WEC", "LMP2 ELMS", "LMP3", "GTE", "GT3"];
const VENUES = [
  { venue: "Circuit de la Sarthe (Le Mans)", lap_count: 0, layouts: [] },
  { venue: "Fuji Speedway", lap_count: 6, layouts: [
    { track_name: "Fuji Speedway", lap_count: 1 },
    { track_name: "Fuji Speedway - Classic", lap_count: 5 },
  ] },
  { venue: "Monza", lap_count: 0, layouts: [] },
  { venue: "Spa-Francorchamps", lap_count: 2, layouts: [{ track_name: "Spa-Francorchamps", lap_count: 2 }] },
];
const calls = [];
globalThis.fetch = async (url) => {
  calls.push(url);
  const u = new URL(url, "http://x");
  const json = (body, status = 200) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });
  if (u.pathname === "/leaderboard/catalog/venues") {
    const q = (u.searchParams.get("q") || "").toLowerCase();
    return json(VENUES.filter((v) => !q || v.venue.toLowerCase().includes(q) || v.layouts.some((l) => l.track_name.toLowerCase().includes(q))));
  }
  if (u.pathname === "/leaderboard/catalog/class-options") {
    const track = u.searchParams.get("track_name");
    return json(CLASSES.map((name) => ({ name, lap_count: track === "Fuji Speedway - Classic" && name === "Hypercar" ? 5 : 0 })));
  }
  if (u.pathname === "/leaderboard/catalog/cars") return json(track_cars(u.searchParams.get("track_name")));
  if (u.pathname.startsWith("/leaderboard/class/")) {
    return json([{ driver_id: 1, driver_name: "Jordan Example", lap_time: 90.5, sector_times: [30, 30, 30.5], is_valid: true, uploaded_at: "2026-09-28T10:00:00" }]);
  }
  return json({ detail: "unexpected " + url }, 404);
};
function track_cars(track) { return track === "Fuji Speedway - Classic" ? ["397_25_499P"] : []; }

const { renderLeaderboard } = await import("../../web/js/pages/leaderboard.js");
const root = document.getElementById("root");
const settle = (ms = 60) => new Promise((r) => setTimeout(r, ms));
const $ = (id) => document.getElementById(id);
const labels = (select) => [...select.options].map((o) => o.textContent);

await renderLeaderboard(root);

// 1. every known track is offered straight away, incl. ones nobody drove
assert.ok(labels($("venueSelect")).some((l) => l.startsWith("Monza") && l.includes("no laps yet")), labels($("venueSelect")).join("|"));
assert.ok(labels($("venueSelect")).some((l) => l.startsWith("Circuit de la Sarthe")));
assert.equal($("classSelect").disabled, true);

// 2. typing "fuji" narrows to the WHOLE track, selects it, and shows its variants
$("trackSearch").value = "fuji";
$("trackSearch").dispatchEvent(new dom.window.Event("input"));
await settle(500);
const venueLabels = labels($("venueSelect")).filter((l) => l !== "Choose a track…");
assert.equal(venueLabels.length, 1, venueLabels.join("|"));
assert.ok(venueLabels[0].startsWith("Fuji Speedway"));
assert.equal($("venueSelect").value, "Fuji Speedway");
assert.notEqual($("layoutSelect").parentElement.style.display, "none");
assert.deepEqual(labels($("layoutSelect")), ["Fuji Speedway · 1 lap", "Fuji Speedway - Classic · 5 laps"]);
assert.equal($("layoutSelect").value, "Fuji Speedway - Classic"); // the most-driven variant is preselected

// 3. ALL six classes are offered, with what has laps
const classLabels = labels($("classSelect")).filter((l) => l !== "Choose…");
assert.deepEqual(classLabels.map((l) => l.split(" · ")[0]), CLASSES);
assert.ok(classLabels[0].includes("5 laps") && classLabels[5].includes("no laps yet"));

// 4. picking a class and searching queries the chosen layout + readable class name
$("classSelect").value = "Hypercar";
[...root.querySelectorAll("button")].find((b) => b.textContent === "Search").click();
await settle();
assert.ok(calls.some((c) => c.startsWith("/leaderboard/class/Fuji%20Speedway%20-%20Classic/Hypercar")), calls.join("\n"));
assert.ok(root.textContent.includes("Jordan Example"));

// 5. a known track with no laps is selectable: friendly hint + every class still listed
$("trackSearch").value = "monza";
$("trackSearch").dispatchEvent(new dom.window.Event("input"));
await settle(500);
assert.equal($("venueSelect").value, "Monza");
assert.ok(root.textContent.includes("No laps recorded at Monza yet"));
assert.deepEqual(labels($("classSelect")).filter((l) => l !== "Choose…").map((l) => l.split(" · ")[0]), CLASSES);

// 6. a search with no match says so plainly instead of leaving a broken form
$("trackSearch").value = "zzzz";
$("trackSearch").dispatchEvent(new dom.window.Event("input"));
await settle(500);
assert.ok(labels($("venueSelect"))[0].includes('No track matches "zzzz"'));

console.log("LEADERBOARD-PICKER-OK");
process.exit(0);
