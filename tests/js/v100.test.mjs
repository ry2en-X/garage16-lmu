// V1.0 — the legal pages (operator from the server, both languages, nothing invented when it is
// missing), the one-step sign-up form, the setup guide and the download page's next step.
//   node v100.test.mjs en|de
import { JSDOM } from "jsdom";
import assert from "node:assert/strict";
import fs from "node:fs";

const lang = process.argv[2] === "de" ? "de" : "en";
const html = fs.readFileSync(new URL("../../web/index.html", import.meta.url), "utf8").replace(/<script[^>]*><\/script>/g, "");
const dom = new JSDOM(html, { url: "https://garage16.example/#/legal", pretendToBeVisual: true });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, localStorage: dom.window.localStorage, sessionStorage: dom.window.sessionStorage });
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
dom.window.LMU_GARAGE_API_BASE_URL = "";
localStorage.setItem("garage16_language", lang);

let legal = { operator: null, min_age: 16, terms_version: "2026-10-24", registration: "open" };
let health = { status: "ok", client_download_url: "https://github.com/o/r/releases/latest/download/Garage16-Client-Setup.exe",
  client_app_download_url: "https://github.com/o/r/releases/latest/download/Garage16-Client-Analysis-Setup.exe",
  client_release_page_url: "https://github.com/o/r/releases/latest", registration: "open" };
let signupAnswer = { status: 200, body: { driver_id: 9, display_name: "Rita" } };
const posted = [];
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(url, "http://x");
  const json = (body, status = 200) => ({ ok: status < 400, status, statusText: String(status), json: async () => body, text: async () => JSON.stringify(body) });
  if (u.pathname === "/legal") return legal === null ? json({ detail: "down" }, 503) : json(legal);
  if (u.pathname === "/health") return json(health);
  if (u.pathname === "/accounts/signup") {
    posted.push({ body: JSON.parse(init.body), headers: init.headers });
    return json(signupAnswer.body, signupAnswer.status);
  }
  if (u.pathname === "/leaderboard/recent") return json([]);
  return json({ detail: "unexpected " + u.pathname }, 404);
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const text = (node) => node.textContent.replace(/\s+/g, " ").trim();
const app = () => document.getElementById("app");
const go = async (hash) => { window.location.hash = hash; await sleep(120); return text(app()); };
const de = lang === "de";

await import("../../web/js/router.js");
await sleep(150);

// ---------------------------------------------------------------- legal notice
// not configured: says so, invents nothing
let body = text(app());
assert.ok(body.includes(de ? "Angaben gemäß § 5 Digitale-Dienste-Gesetz" : "Information pursuant to § 5"), body.slice(0, 200));
assert.ok(body.includes(de ? "noch nicht eingetragen" : "have not been entered"), "missing operator is said");
assert.equal(app().querySelector("address"), null);
assert.deepEqual([...app().querySelectorAll(".legal-tabs a")].map((a) => a.dataset.tab), ["imprint", "privacy", "terms", "licenses"]);
assert.equal(app().querySelector('.legal-tabs a[data-tab="imprint"]').getAttribute("aria-current"), "page");
assert.ok(document.querySelector('#navLinks a[data-route="legal"]').classList.contains("active"));

// configured: name, address lines, mailto
legal = { ...legal, operator: { name: "Erika Mustermann", address: ["Musterstraße 1", "12345 Musterstadt"], email: "erika@example.com" } };
body = await go("#/legal?tab=terms");
body = await go("#/legal");
const address = app().querySelector("address.legal-operator");
assert.ok(address, "operator block");
assert.deepEqual([...address.children].slice(0, 3).map((n) => n.textContent), ["Erika Mustermann", "Musterstraße 1", "12345 Musterstadt"]);
assert.equal(address.querySelector("a").getAttribute("href"), "mailto:erika@example.com");
assert.ok(body.includes("Studio 397"), "not an official product");

// privacy: every recipient, the cookie table, the 7-day access log, the age; the English page says the German one binds
body = await go("#/legal?tab=privacy");
for (const needle of ["Cloudflare, Inc.", "ALL-INKL.COM", "GitHub, Inc.", "Google Ireland Limited", "Discord Inc.", "garage16_session", "lmu_garage_session", "garage16_language", "Art. 77", "Erika Mustermann"]) {
  assert.ok(body.includes(needle), `privacy policy should mention ${needle}`);
}
assert.ok(body.includes(de ? "Zugriffsprotokoll: 7 Tage" : "access log: 7 days"));
assert.ok(body.includes(de ? "erst ab 16 Jahren" : "from the age of 16"));
assert.equal(body.includes("German version is the binding one"), !de);
assert.ok(app().querySelectorAll("table.legal-table").length >= 3);
for (const bad of ["{min_age}", "{date}", "undefined", "[object"]) assert.ok(!body.includes(bad), bad);

// the minimum age comes from the server
legal = { ...legal, min_age: 18 };
body = await go("#/legal?tab=terms");
assert.ok(body.includes(de ? "mindestens 18 Jahre alt" : "at least 18 years old"));
legal = { ...legal, min_age: 16 };

// licences: links to the font licences and the notices
body = await go("#/legal?tab=licenses");
const hrefs = [...app().querySelectorAll(".legal-links a")].map((a) => a.getAttribute("href"));
assert.ok(hrefs.includes("fonts/OFL-Oswald.txt") && hrefs.some((h) => h.endsWith("THIRD_PARTY_NOTICES.md")));
assert.ok(fs.existsSync(new URL("../../web/fonts/OFL-Oswald.txt", import.meta.url)), "the linked licence file exists");

// server down: the legal notice says it could not load (no fake address), the terms still render
legal = null;
body = await go("#/legal");
assert.ok(app().querySelector(".notice--error"));
body = await go("#/legal?tab=terms");
assert.ok(body.includes(de ? "mindestens 16 Jahre alt" : "at least 16 years old"));
legal = { operator: null, min_age: 16, terms_version: "2026-10-24", registration: "open" };

// ---------------------------------------------------------------- sign-up form
await go("#/account");
const card = app().querySelector(".signup-card");
assert.ok(card, "sign-up card on the account page when signed out");
assert.equal(card.querySelector("#regSecret").closest(".field").hidden, true, "open registration: no secret field");
const termsLinks = [...card.querySelectorAll(".check-row a")].map((a) => a.getAttribute("href"));
assert.deepEqual(termsLinks, ["#/legal?tab=terms", "#/legal?tab=privacy"]);
assert.ok(text(card).includes(de ? "Ich akzeptiere die Nutzungsbedingungen und habe die Datenschutzerklärung gelesen." : "I accept the terms of use and have read the privacy policy."));
assert.ok(app().querySelector("details.advanced-signin"), "the token sign-in is folded away");
const set = (id, v) => { card.querySelector(`#${id}`).value = v; };
const press = async () => { card.querySelector("button.btn").click(); await sleep(30); return text(card); };
set("displayName", "Rita"); set("signupEmail", "rita@example.com"); set("signupPw", "Correct-Horse-42"); set("signupPw2", "Correct-Horse-43");
assert.ok((await press()).includes(de ? "stimmen nicht überein" : "are not the same"));
set("signupPw2", "Correct-Horse-42");
assert.ok((await press()).includes(de ? "bestätige dein Alter" : "confirm your age"));
card.querySelector("#signupAge").checked = true;
assert.ok((await press()).includes(de ? "akzeptiere die Nutzungsbedingungen" : "accept the terms of use"));
assert.equal(posted.length, 0, "nothing is sent before the form is complete");
card.querySelector("#signupTerms").checked = true;
signupAnswer = { status: 409, body: { detail: "That email address already has an account." } };
assert.ok((await press()).includes("already has an account"));
signupAnswer = { status: 422, body: { detail: [{ msg: "String should have at most 60 characters" }] } };
assert.ok((await press()).includes("at most 60 characters"), "validation lists are shown as text");
signupAnswer = { status: 200, body: { driver_id: 9, display_name: "Rita" } };
card.querySelector("button.btn").click();
await sleep(200);
assert.deepEqual(posted.at(-1).body, { display_name: "Rita", email: "rita@example.com", password: "Correct-Horse-42", accept_terms: true, confirm_min_age: true });
assert.equal(posted.at(-1).headers["X-Registration-Secret"], undefined);
assert.equal(window.location.hash, "#/setup?welcome=1", "after sign-up: the setup guide");
assert.equal(JSON.parse(localStorage.getItem("lmu_garage_session")).viaCookie, true);

// ---------------------------------------------------------------- setup guide
body = text(app());
assert.ok(body.includes(de ? "Dein Konto ist fertig" : "Your account is ready"));
assert.equal(app().querySelectorAll(".setup-step").length, 6);
assert.ok(app().querySelector('.setup-step[data-step="1"]').classList.contains("setup-step--done"));
assert.ok(body.includes("https://garage16.example"), "the server address is the page's own origin");
assert.equal(app().querySelector('[data-download="standard"]').getAttribute("href"), health.client_download_url);
assert.equal(app().querySelector('[data-download="analysis"]').getAttribute("href"), health.client_app_download_url);
assert.ok([...app().querySelectorAll("a")].some((a) => a.getAttribute("href") === health.client_release_page_url));

// ---------------------------------------------------------------- download page: next step + all downloads
body = await go("#/download");
const next = app().querySelector(".download-next");
assert.ok(next && next.querySelector('a[href="#/setup"]'));
assert.ok([...app().querySelectorAll(".download-notes a")].some((a) => a.getAttribute("href") === health.client_release_page_url));
const standardLink = app().querySelector('[data-download="standard"]');
standardLink.addEventListener("click", (e) => e.preventDefault());   // jsdom cannot navigate away
standardLink.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
assert.ok(next.classList.contains("notice--success"), "the next step lights up after a click");

// a secret-only server: the form asks for the secret and sends it
const { store } = await import("../../web/js/state.js");
store.signOut();
legal = { ...legal, registration: "secret" };
await go("#/account");
const card2 = app().querySelector(".signup-card");
assert.equal(card2.querySelector("#regSecret").closest(".field").hidden, false);

// landing page footer
body = await go("#/");
assert.deepEqual([...app().querySelectorAll(".site-footer a")].map((a) => a.getAttribute("href")), ["#/legal", "#/legal?tab=privacy", "#/legal?tab=terms"]);

console.log(`V100-${lang.toUpperCase()}-OK`);
