// V0.9.6 — overview & usability pass: grouped menu, self-hosted fonts, pages loaded on demand,
// one row per lap in the record feeds, the dashboard cockpit (quick actions, lap filter without a
// request), the team page order (folded settings, sections loaded in parallel), the account page
// order (rarely needed parts folded), the lap picker grouped per track.
import { JSDOM } from "jsdom";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const html = readFileSync(new URL("../../web/index.html", import.meta.url), "utf-8");
const css = readFileSync(new URL("../../web/css/styles.css", import.meta.url), "utf-8");

// ---------------------------------------------------------------- the shell
assert.ok(!/fonts\.googleapis|fonts\.gstatic/.test(html), "no request to Google for fonts");
assert.ok(/@font-face\s*\{[^}]*Oswald[^}]*\.\.\/fonts\/oswald-latin-wght\.woff2/.test(css), "Oswald from web/fonts");
for (const w of [400, 500, 600]) assert.ok(css.includes(`ibm-plex-mono-latin-${w}.woff2`), `IBM Plex Mono ${w}`);
for (const f of ["oswald-latin-wght.woff2", "ibm-plex-mono-latin-400.woff2", "OFL-Oswald.txt", "OFL-IBM-Plex-Mono.txt"]) {
  assert.ok(readFileSync(new URL(`../../web/fonts/${f}`, import.meta.url)).length > 1000, f);
}
assert.ok(html.includes("<title>Garage16</title>"));
const shell = new JSDOM(html);
const groups = [...shell.window.document.querySelectorAll("#navLinks .nav-group")].map((g) => [...g.querySelectorAll("a")].map((a) => a.dataset.route));
assert.deepEqual(groups, [["dashboard", "teams"], ["analysis", "stints"], ["leaderboard", "circuits"], ["account", "download"]], "the menu in groups");
const router = readFileSync(new URL("../../web/js/router.js", import.meta.url), "utf-8");
assert.ok(!/^import .*\.\/pages\//m.test(router), "no page module is imported up front");
assert.ok(router.includes('import(path)'), "pages are loaded on demand");

// ---------------------------------------------------------------- a DOM with a canned backend
const dom = new JSDOM(`<!doctype html><body><main id="app"></main></body>`, { url: "http://localhost/#/dashboard", pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.localStorage = dom.window.localStorage;
globalThis.sessionStorage = dom.window.sessionStorage;
globalThis.Event = dom.window.Event;
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
dom.window.LMU_GARAGE_API_BASE_URL = "";
dom.window.HTMLCanvasElement.prototype.getContext = () => null;

const lap = (id, track, time, extra = {}) => ({ id, track_name: track, car_name: "499P Custom Team 2025 #397", car_model: "499P (2025)", lap_number: id, lap_time: time, is_valid: true, uploaded_at: "2026-10-10T10:00:00", data_level: "full", ...extra });
const LAPS = [lap(9, "Le Mans", 210.4), lap(8, "Le Mans", 211.0, { is_valid: false }), lap(7, "Spa", 136.2), lap(6, "Le Mans", 209.9)];
const ACTIVITY = [
  { record_type: "PR", track_name: "Le Mans", car_name: "499P Custom Team 2025 #397", car_model: "499P (2025)", lap_time: 209.9, lap_id: 6, created_at: "2026-10-10T10:00:00", driver_name: "Me" },
  { record_type: "WR", track_name: "Le Mans", car_name: "499P Custom Team 2025 #397", car_model: "499P (2025)", lap_time: 209.9, lap_id: 6, created_at: "2026-10-10T10:00:00", driver_name: "Me" },
  { record_type: "TEAM_BEST", track_name: "Le Mans", car_name: "499P Custom Team 2025 #397", car_model: "499P (2025)", lap_time: 209.9, lap_id: 6, created_at: "2026-10-10T10:00:00", driver_name: "Me" },
  { record_type: "PR", track_name: "Spa", car_name: "x", car_model: "499P (2025)", lap_time: 136.2, lap_id: 7, created_at: "2026-10-09T10:00:00", driver_name: "Me" },
];
const calls = [];
const order = [];
let release = null;
let paused = false;
globalThis.fetch = async (url) => {
  const p = new URL(url, "http://x").pathname;
  calls.push(p);
  const json = (body, status = 200) => ({ ok: status < 400, status, headers: { get: () => null }, json: async () => body, text: async () => JSON.stringify(body) });
  if (p === "/telemetry/laps") return json(LAPS);
  if (p === "/drivers/1/public") return json({ driver_id: 1, display_name: "Me", personal_records: [{ track_name: "Le Mans", car_name: "x", car_class: "Hypercar", car_model: "499P (2025)", lap_time: 209.9, set_at: "2026-10-10T10:00:00" }], recent_activity: ACTIVITY, teams: [] });
  if (p === "/teams/mine") return json([{ id: 3, name: "Red Team" }, { id: 4, name: "Blue Team" }]);
  if (p.startsWith("/teams/") && p.endsWith("/records")) {
    order.push(`start ${p}`);
    if (p === "/teams/3/records" && !paused) { paused = true; await new Promise((r) => { release = r; }); }
    order.push(`end ${p}`);
    return json([{ track_name: "Le Mans", car_model: "499P (2025)", you_hold_it: true, gap: null, driver_name: "Me" }]);
  }
  if (p === "/teams/3") return json({ id: 3, name: "Red Team", my_role: "owner", member_count: 2, active_driver_count: 2, total_laps: 4, pbs_this_week: 1, team_best_count: 1, description: "" });
  if (p === "/teams/3/members") return json([{ driver_id: 1, display_name: "Me", role: "owner", status: "active", lap_count: 4, pb_count: 1, last_activity: "2026-10-10T10:00:00" }]);
  if (p === "/teams/3/stats") return json({ laps: 4, pbs: 1, active_drivers: 2, records: 1 });
  if (p === "/teams/3/activity") return json(ACTIVITY);
  if (p === "/teams/3/invitations") return json([]);
  if (p === "/leaderboard/catalog/venues") return json([]);
  if (p === "/accounts/discord") return json({ linked: false });
  if (p === "/accounts/sessions") return json([]);
  if (p === "/accounts/me") return json({ driver_id: 1, display_name: "Me", email: null });
  return json({ detail: `unexpected ${p}` }, 404);
};
const settle = (ms = 40) => new Promise((r) => setTimeout(r, ms));
const { store } = await import("../../web/js/state.js");
store.setSession({ driverId: 1, displayName: "Me", viaCookie: true });
const app = document.getElementById("app");
const text = (n) => n.textContent.replace(/\s+/g, " ");

// ---------------------------------------------------------------- record feed: one row per lap
const { groupActivity } = await import("../../web/js/activity.js");
const grouped = groupActivity(ACTIVITY);
assert.equal(grouped.length, 2, "three records of lap 6 are ONE entry");
assert.deepEqual(grouped[0].types, ["WR", "TEAM_BEST", "PR"], "most important first");
assert.deepEqual(groupActivity([{ ...ACTIVITY[0], lap_id: undefined }, { ...ACTIVITY[1], lap_id: undefined }]).length, 1, "without a lap id: same driver, track, car and time");

// ---------------------------------------------------------------- dashboard
const { renderDashboard } = await import("../../web/js/pages/dashboard.js");
const done = renderDashboard(app);
await settle();
assert.ok(order.includes("start /teams/4/records") && !order.includes("end /teams/3/records"), "team records load in parallel, not one after the other");
release();
await done;
await settle();
const actions = [...app.querySelectorAll(".quick-action")];
assert.equal(actions.length, 3);
assert.equal(actions[0].getAttribute("href"), "#/analysis?a=9", "'Analyse last lap' opens the newest lap");
assert.ok(actions[1].getAttribute("href").startsWith("#/analysis?quick=wr&track=Le%20Mans"));
assert.equal(app.querySelectorAll(".activity__row").length, 2, "the feed has one row per lap");
assert.equal(app.querySelectorAll(".activity__row")[0].querySelectorAll(".tag").length, 3, "with every record as a badge");
assert.ok(text(app.querySelector(".activity")).includes("499P (2025)") && !text(app.querySelector(".activity")).includes("#397"), "the car model, not the livery");
assert.equal(app.querySelector(".activity__row a").getAttribute("href"), "#/analysis?a=6", "the time opens the lap");
// lap filter: in the browser, no request
const before = calls.length;
assert.equal(app.querySelectorAll(".timing-table tbody tr").length, 4);
const track = app.querySelector("#fTrack");
track.value = "Le Mans";
track.dispatchEvent(new dom.window.Event("change"));
assert.equal(app.querySelectorAll(".timing-table tbody tr").length, 3);
const valid = app.querySelector("#fValid");
valid.checked = true;
valid.dispatchEvent(new dom.window.Event("change"));
assert.equal(app.querySelectorAll(".timing-table tbody tr").length, 2);
assert.equal(calls.length, before, "filtering makes no request");
assert.ok(text(app).includes("2 laps"));

// ---------------------------------------------------------------- team page
app.innerHTML = "";
const { renderTeamDetail } = await import("../../web/js/pages/team_detail.js");
await renderTeamDetail(app, 3);
await settle();
const headings = [...app.querySelectorAll(":scope > h2")].map((h) => h.textContent);
assert.deepEqual(headings, ["Team bests", "Team leaderboard", "Recent activity", "Members", "Invitations"], "results first, people next");
const fold = app.querySelector("details.fold");
assert.ok(fold && fold.querySelector("summary").textContent === "Team settings", "settings folded at the end");
assert.equal(fold.open, false);
assert.ok(fold.textContent.includes("Delete team"));
assert.equal(app.lastElementChild, fold);
assert.equal(app.querySelectorAll(".team-overview").length, 1, "one overview card (was two with the same numbers)");
assert.equal(app.querySelectorAll(".activity__row").length, 2);

// ---------------------------------------------------------------- account page
app.innerHTML = "";
const { renderAccount } = await import("../../web/js/pages/account.js");
await renderAccount(app);
await settle();
const top = [...app.children].map((n) => n.tagName.toLowerCase() + (n.id ? `#${n.id}` : ""));
assert.ok(app.querySelector(".account-profile").textContent.includes("Sign out"), "sign out at the top");
assert.equal(app.querySelector("#accountAdvanced").open, false, "desktop-client credentials folded");
assert.ok(app.querySelector("#accountAdvanced").textContent.includes("Desktop Client Credentials"));
assert.ok(app.querySelector("#accountData").textContent.includes("Data & Privacy"));
assert.ok(top.indexOf("details#accountAdvanced") > top.indexOf("div"), top.join(","));

// ---------------------------------------------------------------- lap picker: per track, best marked
app.innerHTML = "";
const { renderLapPicker } = await import("../../web/js/pages/analysis_picker.js");
let picked = null;
renderLapPicker(app, { title: "Choose Lap 1", onPick: (id) => { picked = id; } });
await settle();
const tracks = [...app.querySelectorAll(".picker__group")].map((g) => g.dataset.track);
assert.deepEqual(tracks, ["Le Mans", "Spa"], "grouped per track");
assert.equal(app.querySelector(".picker__group").open, true, "the first group open");
assert.equal(app.querySelector(".picker__tabs").style.display, "none", "a single tab is not shown as a choice");
const best = app.querySelector(".picker__row--best");
assert.ok(best && best.textContent.includes("3:29.900"), "the fastest valid lap is marked");
app.querySelector(".picker__group .picker__row").click();
assert.equal(picked, 9, "a click on the row selects the lap");

console.log("V096-UX-OK");
