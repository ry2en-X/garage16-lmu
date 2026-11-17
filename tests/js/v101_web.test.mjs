// V1.0.1 (website pass, 17.11.2026) — landing page (features, sessions instead of single laps),
// leaderboard (loading notice instead of "pick a track", tracks/classes without laps in their own
// group, column headings), one track name everywhere (tracks.js), "Your Best" as a table.
//   node v101_web.test.mjs en|de
import { JSDOM } from "jsdom";
import assert from "node:assert/strict";
import fs from "node:fs";

const lang = process.argv[2] === "de" ? "de" : "en";
const de = lang === "de";
const html = fs.readFileSync(new URL("../../web/index.html", import.meta.url), "utf8").replace(/<script[^>]*><\/script>/g, "");
const dom = new JSDOM(html, { url: "https://garage16.example/#/", pretendToBeVisual: true });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, localStorage: dom.window.localStorage, sessionStorage: dom.window.sessionStorage });
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
dom.window.LMU_GARAGE_API_BASE_URL = "";
localStorage.setItem("garage16_language", lang);

const VENUES = [
  { venue: "Portimão (Algarve)", lap_count: 40, layouts: [{ track_name: "Algarve International Circuit", lap_count: 40 }] },
  { venue: "Silverstone", lap_count: 11, layouts: [
    { track_name: "Silverstone National Circuit", lap_count: 6 },
    { track_name: "Silverstone Grand Prix Circuit - ELMS", lap_count: 5 },
  ] },
  { venue: "Lusail International Circuit", lap_count: 0, layouts: [] },
];
const H = (min) => new Date(Date.UTC(2026, 10, 14, 20, 0) - min * 60000).toISOString();
const RECENT = [
  // Mika, Algarve, one session of three laps, the middle one a Garage16 Best
  { driver_id: 1, driver_name: "Mika", track_name: "Algarve International Circuit", car_name: "#7", car_model: "499P", lap_time: 99.9, uploaded_at: H(0), lap_id: 30, records: [] },
  { driver_id: 2, driver_name: "Nito", track_name: "Silverstone National Circuit", car_name: "#9", car_model: "963", lap_time: 61.2, uploaded_at: H(1), lap_id: 29, records: ["PR"] },
  { driver_id: 1, driver_name: "Mika", track_name: "Algarve International Circuit", car_name: "#7", car_model: "499P", lap_time: 99.1, uploaded_at: H(3), lap_id: 28, records: ["PR", "WR"] },
  { driver_id: 1, driver_name: "Mika", track_name: "Algarve International Circuit", car_name: "#7", car_model: "499P", lap_time: 99.5, uploaded_at: H(5), lap_id: 27, records: [] },
  // the next day's laps of Mika at the same track are another session (more than 90 min apart)
  { driver_id: 1, driver_name: "Mika", track_name: "Algarve International Circuit", car_name: "#7", car_model: "499P", lap_time: 100.4, uploaded_at: H(24 * 60), lap_id: 10, records: [] },
];

let releaseBoard;
const boardGate = new Promise((r) => { releaseBoard = r; });
const calls = [];
globalThis.fetch = async (url) => {
  const u = new URL(url, "http://x");
  calls.push(u.pathname);
  const json = (body, status = 200) => ({ ok: status < 400, status, statusText: String(status), json: async () => body, text: async () => JSON.stringify(body) });
  const p = u.pathname;
  if (p === "/leaderboard/catalog/venues") return json(VENUES);
  if (p === "/leaderboard/recent") return json(RECENT);
  if (p === "/leaderboard/catalog/class-options") return json([{ name: "GT3", lap_count: 0 }, { name: "Hypercar", lap_count: 40 }, { name: "LMP3", lap_count: 2 }]);
  if (p === "/leaderboard/catalog/cars") return json(["499P"]);
  if (p.startsWith("/leaderboard/class/")) {
    await boardGate;
    return json([
      { driver_id: 1, driver_name: "Mika", lap_time: 99.1, sector_times: [30, 34, 35.1], is_valid: true, uploaded_at: H(3), lap_id: 28 },
      { driver_id: 2, driver_name: "Nito", lap_time: 99.6, sector_times: [30.2, 34.1, 35.3], is_valid: true, uploaded_at: H(3), lap_id: 21 },
    ]);
  }
  if (p === "/drivers/1/public") return json({ driver_id: 1, display_name: "Mika", total_laps: 5, tracks_driven: 2, cars_driven: 1, teams: [], recent_activity: [],
    personal_records: [
      { track_name: "Algarve International Circuit", car_name: "#7", car_class: "Hypercar", car_model: "499P", lap_time: 99.1, set_at: H(3), class_best_time: 99.1, class_best_driver_id: 1, class_best_driver_name: "Mika" },
      // a second Hypercar at Algarve (slower: not shown) and a GT3 there — its own row, against the GT3 record
      { track_name: "Algarve International Circuit", car_name: "#8", car_class: "Hypercar", car_model: "963", lap_time: 100.3, set_at: H(4), class_best_time: 99.1, class_best_driver_id: 1, class_best_driver_name: "Mika" },
      { track_name: "Algarve International Circuit", car_name: "#91", car_class: "GT3", car_model: "911 GT3 R", lap_time: 112.4, set_at: H(9), class_best_time: 111.9, class_best_driver_id: 2, class_best_driver_name: "Nito" },
      { track_name: "Silverstone National Circuit", car_name: "#7", car_class: "Hypercar", car_model: "499P", lap_time: 62.0, set_at: H(60), class_best_time: 61.2, class_best_driver_id: 2, class_best_driver_name: "Nito" },
    ] });
  if (p === "/teams/mine") return json([]);
  if (p === "/telemetry/laps") return json([{ id: 28, track_name: "Algarve International Circuit", car_name: "#7", car_model: "499P", lap_number: 3, lap_time: 99.1, is_valid: true, uploaded_at: H(3) }]);
  return json({ detail: `unexpected ${p}` }, 404);
};
const settle = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (n) => n.textContent.replace(/\s+/g, " ").trim();
const app = document.getElementById("app");

// ---------------------------------------------------------------- tracks.js
const { loadTrackNames, trackLabel, _resetTrackNames } = await import("../../web/js/tracks.js");
assert.equal(trackLabel("Algarve International Circuit"), "Algarve International Circuit", "before loading: LMU's name, never blank");
await loadTrackNames();
assert.equal(trackLabel("Algarve International Circuit"), "Portimão (Algarve)", "one layout: the venue name, like the leaderboard");
assert.equal(trackLabel("Silverstone National Circuit"), "Silverstone National Circuit", "several layouts: the layout's own name");
assert.equal(trackLabel("Unknown Ring"), "Unknown Ring");
const n = calls.filter((c) => c === "/leaderboard/catalog/venues").length;
await loadTrackNames();
assert.equal(calls.filter((c) => c === "/leaderboard/catalog/venues").length, n, "loaded once per page load");

// ---------------------------------------------------------------- landing: sessions
const { renderLanding, groupRecentSessions } = await import("../../web/js/pages/landing.js");
const sessions = groupRecentSessions(RECENT);
assert.equal(sessions.length, 3, "Mika today, Nito, Mika the day before");
assert.equal(sessions[0].laps, 3);
assert.equal(sessions[0].best.lap_time, 99.1);
assert.equal(sessions[0].best.lap_id, 28);
assert.deepEqual(sessions[0].records, ["WR", "PR"], "the session's records, Garage16 Best first");
assert.equal(sessions[2].laps, 1, "laps far apart are another session");

await renderLanding(app);
await settle();
const rows = [...app.querySelectorAll(".activity__row")];
assert.equal(rows.length, 3);
assert.ok(text(rows[0]).includes("Portimão (Algarve)"), "the venue name, not LMU's string");
assert.ok(text(rows[0]).includes(de ? "3 Runden" : "3 laps"));
assert.ok(text(rows[0]).includes("1:39.100"));
assert.equal(rows[0].querySelector(".activity__side a").getAttribute("href"), "#/analysis?a=28", "the best lap of the session opens the analysis");
assert.equal(rows[0].querySelectorAll(".tag").length, 2);
assert.ok(text(rows[0].querySelector(".tag")).includes("Garage16 Best"));
assert.equal(app.querySelectorAll(".feature").length, 4, "four features, each with a drawing");
assert.equal(app.querySelectorAll(".feature svg.feature__art").length, 4);
assert.ok(text(app.querySelector(".features")).includes(de ? "Fahrlinien auf der Strecke" : "Racing lines on the track"));
// the features come before the activity
const h2 = [...app.querySelectorAll("h2")].map((h) => h.textContent);
assert.deepEqual(h2, de ? ["Was Garage16 kann", "Letzte Aktivität"] : ["What Garage16 Does", "Recent Activity"]);

// ---------------------------------------------------------------- leaderboard
app.innerHTML = "";
_resetTrackNames();
const { renderLeaderboard } = await import("../../web/js/pages/leaderboard.js");
window.location.hash = "#/leaderboard";
const rendering = renderLeaderboard(app);
await settle();
const loading = app.querySelector(".empty-state--loading");
assert.ok(loading, "while the table loads: a loading notice …");
assert.equal(text(loading), de ? "Rangliste wird geladen…" : "Loading standings…");
assert.ok(!text(app).includes(de ? "Wähle eine Strecke und eine Klasse" : "Pick a track and class"), "… not 'pick a track' although one is picked");
releaseBoard();
await rendering;
await settle();
assert.ok(!app.querySelector(".empty-state--loading"));
const venueSelect = app.querySelector("#venueSelect");
assert.equal(venueSelect.value, "Portimão (Algarve)", "the busiest track is chosen");
const groups = [...venueSelect.querySelectorAll("optgroup")];
assert.equal(groups.length, 1);
assert.equal(groups[0].label, de ? "Noch keine Runden" : "No laps yet");
assert.deepEqual([...groups[0].querySelectorAll("option")].map((o) => o.value), ["Lusail International Circuit"], "tracks nobody drove at the end");
const classSelect = app.querySelector("#classSelect");
assert.equal(classSelect.value, "Hypercar");
assert.deepEqual([...classSelect.querySelectorAll("optgroup option")].map((o) => o.value), ["GT3"]);
const heads = [...app.querySelectorAll(".timing-table thead th")].map((th) => th.textContent);
assert.deepEqual(heads.slice(5), de ? ["Status", "Gefahren", "Analyse"] : ["Status", "Set", "Analysis"], "every column has a heading");
assert.equal(app.querySelector(".timing-table tbody tr .lap-gap").textContent, "—", "P1's gap");
assert.equal(text(app.querySelector(".board-title")), "Portimão (Algarve) · Hypercar");

// ---------------------------------------------------------------- dashboard: "Your Best" as a table
app.innerHTML = "";
const { store } = await import("../../web/js/state.js");
store.setSession({ driverId: 1, displayName: "Mika", viaCookie: true });
const { renderDashboard } = await import("../../web/js/pages/dashboard.js");
await renderDashboard(app);
await settle();
const best = app.querySelector(".best-table");
assert.ok(best, "Your Best is a table");
assert.ok(best.closest(".dash-grid__col--wide"), "over the full width");
// V1.0.1: one row per track AND class (the GT3 lap is no longer hidden behind the Hypercar)
const bestRows = [...best.querySelectorAll("tbody tr")].map((tr) => [...tr.children].slice(0, 3).map((td) => text(td)));
assert.deepEqual(bestRows, [
  ["Portimão (Algarve)", "Hypercar", "499P"],
  ["", "GT3", "911 GT3 R"],
  ["Silverstone National Circuit", "Hypercar", "499P"],
]);
const bestCells = [...best.querySelectorAll("tbody tr")].map((tr) => text(tr.children[4]));
assert.equal(bestCells[0], de ? "Dein Rekord" : "Your record", "own class record marked");
assert.equal(bestCells[1], "1:51.900 +0.500", "the GT3 lap against the GT3 record, not the Hypercar time");
assert.equal(bestCells[2], "1:01.200 +0.800");
const gt3Link = new URL(best.querySelectorAll("tbody tr")[1].querySelector("a").getAttribute("href").slice(1), "https://x");
assert.equal(gt3Link.searchParams.get("scope"), "class");
assert.equal(gt3Link.searchParams.get("model"), "911 GT3 R");
assert.equal(gt3Link.searchParams.get("track"), "Algarve International Circuit");
const { parseAnalysisExtras } = await import("../../web/js/pages/analysis.js");
assert.equal(parseAnalysisExtras("#/analysis?quick=wr&track=X&model=Y&scope=class").scope, "class");
assert.equal(parseAnalysisExtras("#/analysis?quick=wr&track=X&scope=evil").scope, null);
assert.ok(text(app.querySelector(".quick-action")).includes("Portimão (Algarve)"));
assert.equal(app.querySelector("#fTrack option[value='Algarve International Circuit']").textContent, "Portimão (Algarve)", "the filter keeps LMU's value, shows the readable name");

console.log(`V101-WEB-${lang.toUpperCase()}-OK`);
process.exit(0);
