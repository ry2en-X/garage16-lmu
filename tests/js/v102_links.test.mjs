// V1.0.2 — readable links: core/link.js builds them, core/routes.js reads them back, old #/ links
// are turned into the new ones, unknown addresses get a real "page not found", and the router
// draws pages on the site's own paths (with the page's title) without reloading.
import { JSDOM } from "jsdom";
import assert from "node:assert/strict";

const dom = new JSDOM(`<!doctype html><body>
  <nav id="pitwall"><div id="navLinks"><a href="/leaderboard" data-route="leaderboard">Leaderboard</a><a href="/impressum" data-route="legal">Legal</a></div>
  <div id="langSwitchSlot"></div><span id="statusDot"></span><span id="statusText"></span></nav>
  <button id="mobileToggle"></button><main id="app"></main></body>`, { url: "http://localhost/#/leaderboard?track=Fuji%20Speedway&class=Hypercar", pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.localStorage = dom.window.localStorage;
globalThis.sessionStorage = dom.window.sessionStorage;
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
dom.window.GARAGE16_API_BASE_URL = "";
localStorage.setItem("garage16_lang", "en");

const calls = [];
globalThis.fetch = async (url) => {
  const u = new URL(url, "http://localhost");
  calls.push(u.pathname);
  const json = (body, status = 200) => ({ ok: status < 400, status, statusText: "x", json: async () => body });
  const p = u.pathname;
  if (p === "/api/v1/public/catalog/tracks") return json(["Fuji Speedway", "Fuji Speedway - Classic"]);
  if (p === "/api/v1/public/catalog/venues") return json([{ venue: "Fuji Speedway", lap_count: 3, layouts: [{ track_name: "Fuji Speedway", lap_count: 3 }] }]);
  if (p === "/api/v1/public/catalog/class-options") return json([{ name: "Hypercar", lap_count: 3 }, { name: "GT3", lap_count: 0 }]);
  if (p === "/api/v1/public/catalog/cars") return json(["Porsche 963"]);
  if (p.startsWith("/api/v1/public/leaderboards/")) return json([{ lap_id: 9, driver_id: 2, driver_name: "Moritz Lachnit", lap_time: 90.1, sector_times: [30, 30, 30.1], is_valid: true, uploaded_at: "2026-11-14T10:00:00" }]);
  if (p === "/api/v1/public/drivers/by-slug/moritz-lachnit") return json({ driver_id: 2, display_name: "Moritz Lachnit", slug: "moritz-lachnit", renamed: false });
  if (p === "/api/v1/public/drivers/by-slug/old-name") return json({ driver_id: 2, display_name: "Moritz Lachnit", slug: "moritz-lachnit", renamed: true });
  if (p === "/api/v1/public/drivers/2") return json({ driver_id: 2, display_name: "Moritz Lachnit", total_laps: 3, tracks_driven: 1, cars_driven: 1, personal_records: [], teams: [], recent_activity: [] });
  if (p.startsWith("/api/v1/public/drivers/by-slug/")) return json({ detail: "Driver not found." }, 404);
  if (p === "/api/v1/public/legal") return json({ operator: null, min_age: 16, registration: "open" });
  if (p === "/api/v1/health") return json({ status: "ok" });
  return json({ detail: `unexpected ${p}` }, 404);
};
const settle = (ms = 80) => new Promise((r) => setTimeout(r, ms));

const { link, slugify } = await import("../../web/js/core/link.js");
const { matchRoute, legacyHashToPath } = await import("../../web/js/core/routes.js");

// ---------------------------------------------------------------- building links
assert.equal(link.leaderboard("Fuji Speedway", "Hypercar"), "/leaderboard/fuji-speedway/hypercar");
assert.equal(link.leaderboard("Fuji Speedway", "Porsche 963"), "/leaderboard/fuji-speedway/porsche-963");
assert.equal(link.leaderboard(null), "/leaderboard");
assert.equal(link.driver(2, "Moritz Lachnit"), "/driver/moritz-lachnit");
assert.equal(link.driver(17, "???"), "/driver/17", "a name without letters links by number");
assert.equal(link.team(5, "HyperMotion Esports"), "/team/hypermotion-esports");
assert.equal(link.analysis({ a: 123, b: 456 }), "/analysis/123/vs/456");
assert.equal(link.analysis({ a: 123 }), "/analysis/123");
assert.equal(link.analysis({ a: 1, b: 2, c: 3 }), "/analysis/1/vs/2?c=3");
assert.equal(link.analysis({ a: 1, ref: "team_best" }), "/analysis/1/vs/team-best");
assert.equal(link.analysis({ quick: "wr", scope: "class", track: "Fuji Speedway", model: "Porsche 963" }), "/analysis/fuji-speedway/porsche-963/vs/class-best");
assert.equal(link.analysis({ quick: "my_best" }), "/analysis/vs/my-best");
assert.equal(link.stints({ track: "Fuji Speedway", date: "2026-11-14" }), "/stints/fuji-speedway/2026-11-14");
assert.equal(link.stints({ track: "Fuji Speedway", date: "2026-11-14", n: 2 }), "/stints/fuji-speedway/2026-11-14-2");
assert.equal(link.stints({ lap: 77 }), "/stints/lap/77");
assert.deepEqual(["imprint", "privacy", "terms", "licenses"].map(link.legal), ["/impressum", "/datenschutz", "/terms", "/licenses"]);
assert.equal(slugify("Müller · Ørsted"), "muller-ørsted");

// ---------------------------------------------------------------- reading them back
assert.equal(matchRoute("/leaderboard/fuji-speedway/hypercar").route.name, "leaderboard");
assert.deepEqual(matchRoute("/analysis/123/vs/456").params, { a: "123", b: "456" });
assert.deepEqual(matchRoute("/analysis/fuji-speedway/porsche-963/vs/class-best").params, { track: "fuji-speedway", model: "porsche-963", against: "class-best" });
assert.equal(matchRoute("/stints/lap/5").params.lap, "5");
assert.equal(matchRoute("/datenschutz").route.tab, "privacy");
assert.equal(matchRoute("/leaderboard/a/b/c"), null, "longer than any page");
assert.equal(matchRoute("/nowhere"), null);

// ---------------------------------------------------------------- the old #/ links
for (const [old, now] of [
  ["#/leaderboard?track=Fuji%20Speedway&class=Hypercar", "/leaderboard/fuji-speedway/hypercar"],
  ["#/leaderboard?track=Fuji+Speedway&car=Porsche+963", "/leaderboard/fuji-speedway/porsche-963"],
  ["#/driver/2", "/driver/2"], ["#/team/5", "/team/5"],
  ["#/analysis?lap=123&ref=456", "/analysis/123/vs/456"],
  ["#/analysis?a=123&b=456&c=7", "/analysis/123/vs/456?c=7"],
  ["#/analysis?quick=wr&track=Fuji%20Speedway&model=Porsche%20963&scope=class", "/analysis/fuji-speedway/porsche-963/vs/class-best"],
  ["#/stints?lap=5", "/stints/lap/5"], ["#/sessions", "/stints"],
  ["#/legal", "/impressum"], ["#/legal?tab=privacy", "/datenschutz"], ["#/legal?tab=terms", "/terms"],
  ["#/reset-password?token=abc", "/reset-password?token=abc"], ["#/", "/"], ["#/account", "/account"],
]) assert.equal(legacyHashToPath(old), now, old);

// ---------------------------------------------------------------- the router, for real
await import("../../web/js/main.js");
await settle(250);
assert.equal(window.location.pathname, "/leaderboard/fuji-speedway/hypercar", "an old #/ link becomes the readable one");
assert.equal(window.location.hash, "");
const app = document.getElementById("app");
assert.equal(document.getElementById("venueSelect").value, "Fuji Speedway", "the address chose the track");
assert.equal(document.getElementById("classSelect").value, "Hypercar", "… and the class");
assert.ok(app.querySelector(".copy-link"), "the leaderboard has a 'Copy link' button");
assert.equal(document.title, "Fuji Speedway · Hypercar – Garage16");
assert.ok(document.querySelector('#navLinks a[data-route="leaderboard"]').classList.contains("active"));
const driverLink = app.querySelector('td a[href^="/driver/"]');
assert.equal(driverLink.getAttribute("href"), "/driver/moritz-lachnit");

// a click on the site's own link: no reload, the page is drawn
driverLink.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true, button: 0 }));
await settle(200);
assert.equal(window.location.pathname, "/driver/moritz-lachnit");
assert.ok(app.textContent.includes("Moritz Lachnit"));
assert.equal(document.title, "Moritz Lachnit – Garage16");
assert.ok(app.querySelector(".copy-link"), "the driver profile has a 'Copy link' button");

// an earlier name: the address is corrected
window.history.pushState(null, "", "/driver/old-name");
window.dispatchEvent(new window.PopStateEvent("popstate"));
await settle(200);
assert.equal(window.location.pathname, "/driver/moritz-lachnit", "a renamed driver's old link shows the new address");

// unknown addresses and unknown drivers: a real "page not found"
for (const path of ["/nowhere", "/driver/nobody"]) {
  window.history.pushState(null, "", path);
  window.dispatchEvent(new window.PopStateEvent("popstate"));
  await settle(150);
  assert.ok(app.textContent.includes("Page not found"), path);
  assert.ok(app.querySelector('.not-found a[href="/"]'), "with a way home");
}
// a moved address
window.history.pushState(null, "", "/legal");
window.dispatchEvent(new window.PopStateEvent("popstate"));
await settle(150);
assert.equal(window.location.pathname, "/impressum");
assert.ok(document.querySelector('#navLinks a[data-route="legal"]').classList.contains("active"));

// back: the previous page again
window.history.back();
await settle(200);

console.log("V102-LINKS-OK");
