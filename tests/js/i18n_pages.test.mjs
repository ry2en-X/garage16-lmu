// V0.9.1 — the REAL router and pages rendered in a chosen language (jsdom, canned backend).
//   node i18n_pages.test.mjs de     German stored  -> menu, status, pages are German
//   node i18n_pages.test.mjs en     English stored -> everything English, as before
//   node i18n_pages.test.mjs none   nothing stored -> English underneath AND the language popup on top
// (One language per process: the pages read it when they load, and changing it reloads the page.)
// Besides the right words it checks the failures that only show when a page really renders:
// a leftover "{0}", "undefined", a thrown error, an English text in the German pages.
import { JSDOM } from "jsdom";
import assert from "node:assert/strict";
import fs from "node:fs";

const mode = process.argv[2];
assert.ok(["de", "en", "none"].includes(mode), "usage: node i18n_pages.test.mjs de|en|none");

const html = fs.readFileSync(new URL("../../web/index.html", import.meta.url), "utf8").replace(/<script[^>]*><\/script>/g, "");
const dom = new JSDOM(html, { url: "http://localhost/#/", pretendToBeVisual: true });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, localStorage: dom.window.localStorage, sessionStorage: dom.window.sessionStorage });
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
dom.window.LMU_GARAGE_API_BASE_URL = "";
if (mode !== "none") localStorage.setItem("garage16_language", mode);
localStorage.setItem("lmu_garage_session", JSON.stringify({ driverId: 1, displayName: "Mika", viaCookie: true }));

const escaped = [];
process.on("unhandledRejection", (e) => escaped.push(e));
dom.window.addEventListener("error", (e) => escaped.push(e.error || e.message));

const unexpected = [];
globalThis.fetch = async (url) => {
  const p = new URL(url, "http://x").pathname;
  const json = (body, status = 200) => ({ ok: status < 400, status, statusText: String(status), json: async () => body, text: async () => JSON.stringify(body) });
  if (p === "/leaderboard/recent") return json([{ driver_name: "Nito", track_name: "Le Mans", car_name: "#7", lap_time: 211.5, created_at: "2026-10-01T10:00:00" }]);
  if (p === "/teams/mine") return json([{ id: 5, name: "Red", member_count: 3, role: "admin", invite_code: "ABCD" }]);
  if (p === "/invitations/mine") return json([]);
  if (p === "/accounts/me") return json({ driver_id: 1, display_name: "Mika", email: "m@x.de", email_verified: false, discord_linked: false, has_password: true });
  if (p === "/accounts/sessions") return json([]);
  if (p === "/accounts/discord") return json({ linked: false });
  if (p === "/drivers/1/public") return json({ driver_id: 1, display_name: "Mika", total_laps: 4, tracks_driven: 2, cars_driven: 1, personal_records: [], teams: [{ team_id: 5, team_name: "Red", role: "owner" }], recent_activity: [
    { record_type: "WR", track_name: "Spa", car_name: "#7", lap_time: 139.0, created_at: "2026-10-01T10:00:00" },
    { record_type: "TEAM_BEST", track_name: "Spa", car_name: "#7", lap_time: 139.0, created_at: "2026-10-01T10:00:00" },
    { record_type: "PR", track_name: "Spa", car_name: "#7", lap_time: 139.0, created_at: "2026-10-01T10:00:00" }] });
  if (p === "/telemetry/laps") return json([]);
  if (p === "/teams/5/records") return json([]);
  if (p === "/leaderboard/catalog/venues") return json([{ venue: "Le Mans", lap_count: 3, layouts: [] }]);
  if (p === "/leaderboard/catalog/class-options") return json([{ name: "Hypercar", lap_count: 3 }]);  // V0.9.6: a track is preselected
  if (p === "/telemetry/analysis/circuits") return json({ venues: [{ venue: "Le Mans", lap_count: 0, layouts: [] }] });
  if (p === "/telemetry/analysis/channels") return json({ channels: [], default: [] });
  if (p === "/health") return json({ status: "ok", version: "0.9.2", client_download_url: "https://dl.example/Garage16-Client-Setup.exe" });
  if (p === "/legal") return json({ operator: { name: "Erika Mustermann", address: ["Musterstraße 1", "12345 Musterstadt"], email: "erika@example.com" }, min_age: 16, terms_version: "2026-10-24", registration: "open" });
  unexpected.push(p);
  return json({ detail: "not mocked" }, 404);
};

await import("../../web/js/router.js");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
await sleep(80);
const text = (node) => node.textContent.replace(/\s+/g, " ").trim();
const app = () => document.getElementById("app");
const page = async (hash) => { window.location.hash = hash; await sleep(140); return text(app()); };

// words for the two languages, in the order of the pages below
const WORDS = {
  en: {
    // V0.9.2: "Download" added to the menu (the two client editions, pages/download.js)
    // V0.9.4: two dashboards — "LapTime Analyzer" (#/analysis) and "Stint Analyzer" (#/stints)
    // V1.0: "Setup guide" and "Legal" at the foot of the menu
    nav: ["Dashboard", "Teams", "LapTime Analyzer", "Stint Analyzer", "Leaderboard", "Circuits", "Account", "Download", "Setup guide", "Legal"], status: "signed in as Mika",
    pages: {
      "#/": ["Your LMU Data. Your Records. Your Team.", "Recent Activity", "Explore Leaderboards", "What Garage16 Does"],
      "#/teams": ["Your teams", "3 member(s) — admin", "invite: ABCD", "Create a team", "Join a team"],
      "#/dashboard": ["Welcome back, Mika.", "0 / 0 team bests held", "Recent Activity", "Garage16 Best", "Personal best"],
      "#/driver/1": ["Garage16 Driver", "Personal Records", "owner", "Tracks", "Garage16 Best", "Team Best", "Personal best"],
      "#/account": ["Account", "Display name", "Desktop Client Credentials"],
      "#/leaderboard": ["Leaderboard", "By class", "Le Mans · 3 laps"],
      "#/circuits": ["Circuits", "Find a track", "No laps yet — be the first!"],
      "#/analysis": ["LapTime Analyzer", "Quick start", "You vs. Garage16 Best", "You vs. Team Best", "You vs. My Best", "Choose Lap 1"],
      "#/download": ["Download Garage16", "Client + analysis app", "Download Client", "The analysis in its own window — no browser needed", "Not available yet"],
      "#/setup": ["Set up Garage16", "Done — you are signed in.", "Connect the client to your account", "If something does not work"],
      "#/legal": ["Legal notice", "Information pursuant to § 5", "Erika Mustermann", "Musterstraße 1"],
      "#/legal?tab=privacy": ["Who is responsible?", "garage16_session", "Cloudflare, Inc."],
      "#/legal?tab=terms": ["Fair play", "at least 16 years old"],
      "#/nowhere": ["Page not found"],
    },
  },
  de: {
    nav: ["Dashboard", "Teams", "LapTime Analyzer", "Stint Analyzer", "Leaderboard", "Strecken", "Konto", "Download", "Einrichtung", "Rechtliches"], status: "angemeldet als Mika",
    pages: {
      "#/": ["Deine LMU-Daten. Deine Rekorde. Dein Team.", "Letzte Aktivität", "Leaderboards ansehen", "Was Garage16 kann"],
      "#/teams": ["Deine Teams", "3 Mitglied(er) — Admin", "Einladung: ABCD", "Team erstellen", "Einem Team beitreten"],
      "#/dashboard": ["Willkommen zurück, Mika.", "0 / 0 Team-Bestzeiten gehalten", "Letzte Aktivität", "Garage16 Best", "Persönliche Bestzeit"],
      "#/driver/1": ["Garage16-Fahrer", "Persönliche Rekorde", "Besitzer", "Strecken", "Garage16 Best", "Team Best", "Persönliche Bestzeit"],
      "#/account": ["Konto", "Anzeigename", "Zugangsdaten des Desktop-Clients"],
      "#/leaderboard": ["Leaderboard", "Nach Klasse", "Le Mans · 3 Runden"],
      "#/circuits": ["Strecken", "Strecke finden", "Noch keine Runden — sei der Erste!"],
      "#/analysis": ["LapTime Analyzer", "Schnellstart", "Du vs. Garage16 Best", "Du vs. Team Best", "Du vs. Meine Beste", "Runde 1 wählen"],
      "#/download": ["Garage16 herunterladen", "Client + Analysetool", "Client herunterladen", "Die Analyse im eigenen Fenster — ganz ohne Browser", "Noch nicht verfügbar"],
      "#/setup": ["Garage16 einrichten", "Erledigt — du bist angemeldet.", "Client mit deinem Konto verbinden", "Wenn etwas nicht klappt"],
      "#/legal": ["Impressum", "Angaben gemäß § 5", "Erika Mustermann", "Musterstraße 1"],
      "#/legal?tab=privacy": ["Wer ist verantwortlich?", "garage16_session", "Cloudflare, Inc."],
      "#/legal?tab=terms": ["Fair Play", "mindestens 16 Jahre alt"],
      "#/nowhere": ["Seite nicht gefunden"],
    },
  },
};
// English phrases that must be gone from the German pages (a few that are easy to leave behind)
const ENGLISH_LEFTOVERS = ["Recent Activity", "Create a team", "Join a team", "Welcome back", "Personal Records", "Quick start", "Choose Lap", "Loading", "No activity yet", "Page not found", "member(s) —", "Display name", "Find a track", "Sign in", "Save", "Cancel"];

const lang = mode === "de" ? "de" : "en";
const words = WORDS[lang];

// ---------------------------------------------------------------- the shell
const nav = [...document.querySelectorAll("#navLinks a")].map((a) => a.textContent);
assert.deepEqual(nav, words.nav, "menu labels");
assert.equal(document.getElementById("statusText").textContent, words.status);
assert.equal(document.documentElement.lang, lang);
assert.equal(document.querySelector(".mobile-bar__toggle").getAttribute("aria-label"), lang === "de" ? "Menü umschalten" : "Toggle menu");
const buttons = [...document.querySelectorAll(".lang-switch button")];
assert.deepEqual(buttons.map((b) => b.textContent), ["EN", "DE"], "the EN | DE switch is in the menu");
assert.deepEqual(buttons.map((b) => b.getAttribute("aria-pressed")), lang === "de" ? ["false", "true"] : ["true", "false"]);

// ---------------------------------------------------------------- the popup
const popup = document.querySelector("[data-language-popup]");
if (mode === "none") {
  assert.ok(popup, "first visit: the real app asks which language to use");
  assert.deepEqual([...popup.querySelectorAll("button")].map((b) => b.textContent), ["English", "Deutsch"]);
  assert.ok(text(app()).includes("Your LMU Data"), "...while the page underneath is English");
} else {
  assert.equal(popup, null, "a stored language: no popup");
}

// ---------------------------------------------------------------- every page
for (const [hash, expected] of Object.entries(words.pages)) {
  const body = await page(hash);
  for (const phrase of expected) assert.ok(body.includes(phrase), `${hash} should contain "${phrase}" — got: ${body.slice(0, 300)}`);
  for (const bad of ["{0}", "{1}", "undefined", "[object", "NaN", "null"]) assert.ok(!body.includes(bad), `${hash} shows "${bad}": ${body.slice(0, 300)}`);
  // the server's record codes are never shown raw, and nothing is called a world record (spec §51)
  for (const code of ["TEAM_BEST", " WR ", "world record", "Weltrekord"]) assert.ok(!(" " + body + " ").includes(code), `${hash} shows "${code}"`);
  assert.equal(app().querySelector(".notice--error"), null, `${hash} shows an error: ${text(app())}`);
  if (lang === "de") {
    for (const english of ENGLISH_LEFTOVERS) assert.ok(!body.includes(english), `${hash} still has English "${english}": ${body.slice(0, 300)}`);
  }
}
if (mode === "none") {
  // choosing a language (done last: it switches this process to German)
  let reloaded = 0;
  const { hooks } = await import("../../web/js/i18n.js");
  hooks.reload = () => { reloaded += 1; };
  document.querySelector('[data-language-popup] [data-lang="de"]').click();
  assert.equal(localStorage.getItem("garage16_language"), "de");
  assert.equal(reloaded, 1, "the page reloads after the choice");
  assert.equal(document.querySelector("[data-language-popup]"), null);
}
assert.deepEqual(unexpected, [], "every request the pages made was one the test knows about");
assert.deepEqual(escaped, [], "no JavaScript error escaped anywhere");
console.log(`I18N-PAGES-${mode.toUpperCase()}-OK`);
process.exit(0);
