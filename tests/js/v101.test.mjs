// V1.0.1 — the terms dialog after signing in (accounts that have not accepted the current terms),
// the "Connected PCs" card with disconnecting, the "confirm your email" banner, page titles, and
// {code, message} error answers shown as text.
//   node v101.test.mjs en|de
import { JSDOM } from "jsdom";
import assert from "node:assert/strict";
import fs from "node:fs";

const lang = process.argv[2] === "de" ? "de" : "en";
const de = lang === "de";
const html = fs.readFileSync(new URL("../../web/index.html", import.meta.url), "utf8").replace(/<script[^>]*><\/script>/g, "");
const dom = new JSDOM(html, { url: "https://garage16.example/#/account", pretendToBeVisual: true });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, localStorage: dom.window.localStorage, sessionStorage: dom.window.sessionStorage });
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
dom.window.GARAGE16_API_BASE_URL = "";
dom.window.confirm = () => true;
localStorage.setItem("garage16_language", lang);
localStorage.setItem("lmu_garage_session", JSON.stringify({ driverId: 4, displayName: "Old Hand", viaCookie: true }));

let me = { driver_id: 4, display_name: "Old Hand", email: "old@example.com", email_verified: false,
  terms_version: null, current_terms_version: "2026-10-24", terms_required: true };
let devices = [
  { id: 11, name: "SIM-RIG", created_at: "2026-10-20T10:00:00", last_used_at: "2026-10-25T20:00:00", is_current: false },
  { id: 12, name: "Laptop", created_at: "2026-10-21T10:00:00", last_used_at: "2026-10-22T09:00:00", is_current: false },
];
const calls = [];
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(url, "http://x");
  const method = init.method || "GET";
  calls.push(`${method} ${u.pathname}`);
  const json = (body, status = 200) => ({ ok: status < 400, status, statusText: String(status), json: async () => body, text: async () => JSON.stringify(body) });
  if (u.pathname === "/api/v1/accounts/me") return json(me);
  if (u.pathname === "/api/v1/accounts/accept-terms" && method === "POST") {
    const body = JSON.parse(init.body);
    assert.deepEqual(body, { accept_terms: true, confirm_min_age: true });
    me = { ...me, terms_required: false, terms_version: "2026-10-24" };
    return json(me);
  }
  if (u.pathname === "/api/v1/accounts/devices") return json(devices);
  if (u.pathname === "/api/v1/accounts/devices/12" && method === "DELETE") {
    devices = devices.filter((d) => d.id !== 12);
    return json({ status: "revoked" });
  }
  if (u.pathname === "/api/v1/accounts/devices/11" && method === "DELETE") {
    return json({ detail: { code: "MAINTENANCE", message: "A backup is running — try again in a few minutes." } }, 503);
  }
  if (u.pathname === "/api/v1/accounts/sessions") return json([]);
  if (u.pathname === "/api/v1/accounts/discord") return json({ linked: false });
  if (u.pathname === "/api/v1/public/legal") return json({ operator: null, min_age: 18, terms_version: "2026-10-24", registration: "open" });
  if (u.pathname === "/api/v1/health") return json({ status: "ok" });
  if (u.pathname === "/api/v1/public/activity") return json([]);
  return json({ detail: "unexpected " + u.pathname }, 404);
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const text = (node) => node.textContent.replace(/\s+/g, " ").trim();
const app = () => document.getElementById("app");

await import("../../web/js/main.js");
await sleep(250);

// ---------------------------------------------------------------- page title
assert.equal(document.title, `${de ? "Konto" : "Account"} – Garage16`);

// ---------------------------------------------------------------- terms dialog
const dialog = document.querySelector("[data-terms-dialog]");
assert.ok(dialog, "the terms dialog opens for an account that has not accepted the current terms");
assert.ok(text(dialog).includes(de ? "mindestens 18 Jahre" : "at least 18 years"), "the server's minimum age");
const links = [...dialog.querySelectorAll("a")].map((a) => a.getAttribute("href"));
assert.deepEqual(links, ["/terms", "/datenschutz"]);
const accept = [...dialog.querySelectorAll("button")][0];
accept.click();
await sleep(30);
assert.ok(text(dialog).includes(de ? "beide Haken" : "both boxes"), "both boxes are required");
assert.ok(!calls.includes("POST /api/v1/accounts/accept-terms"));
dialog.querySelector("#termsAge").checked = true;
dialog.querySelector("#termsAccept").checked = true;
accept.click();
await sleep(60);
assert.ok(calls.includes("POST /api/v1/accounts/accept-terms"));
assert.equal(document.querySelector("[data-terms-dialog]"), null, "closed after accepting");

// ---------------------------------------------------------------- confirm-your-email banner
const banner = document.getElementById("verifyBanner");
assert.ok(banner && text(banner).includes("old@example.com"), "unconfirmed email: banner with the address");

// ---------------------------------------------------------------- connected PCs
const card = document.getElementById("devicesCard");
assert.ok(card, "the Connected PCs card");
assert.ok(text(card).includes("SIM-RIG") && text(card).includes("Laptop"));
card.querySelector('[data-device="12"]').click();
await sleep(60);
assert.ok(calls.includes("DELETE /api/v1/accounts/devices/12"));
assert.ok(!text(document.getElementById("devicesCard")).includes("Laptop"), "the list reloads without the disconnected PC");
// an error answer {code, message} is shown as its message, not "[object Object]"
document.getElementById("devicesCard").querySelector('[data-device="11"]').click();
await sleep(60);
const cardText = text(document.getElementById("devicesCard"));
assert.ok(cardText.includes("A backup is running"), cardText);
assert.ok(!cardText.includes("[object"), cardText);

// ---------------------------------------------------------------- titles of other pages
window.location.hash = "#/leaderboard";
await sleep(120);
assert.equal(document.title, "Leaderboard – Garage16");
window.location.hash = "#/";
await sleep(120);
assert.equal(document.title, "Garage16 — Le Mans Ultimate telemetry & leaderboards");

console.log(`V101-${lang.toUpperCase()}-OK`);
process.exit(0);
