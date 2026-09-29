// Real DOM test (jsdom) for the account page's "Link Discord" card
// (V0.8.7): drives web/js/pages/account.js's renderDiscordCard against a
// tiny in-memory backend that mimics the three /accounts/discord endpoints
// — including the bot redeeming the code out-of-band.
import { JSDOM } from "jsdom";
import assert from "node:assert/strict";

const dom = new JSDOM(`<!doctype html><body><div id="card"></div></body>`, { url: "http://localhost/" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.localStorage = dom.window.localStorage;
globalThis.sessionStorage = dom.window.sessionStorage;
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
dom.window.LMU_GARAGE_API_BASE_URL = "";

// ---- in-memory backend ----
const backend = { linked: false, calls: [], failNextCodeWith409: false };
globalThis.fetch = async (url, opts = {}) => {
  const method = opts.method || "GET";
  backend.calls.push(`${method} ${url}`);
  const json = (status, body) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });
  if (url === "/accounts/discord" && method === "GET") return json(200, { linked: backend.linked });
  if (url === "/accounts/discord" && method === "DELETE") {
    const was = backend.linked;
    backend.linked = false;
    return json(200, { status: was ? "unlinked" : "not_linked" });
  }
  if (url === "/accounts/discord/link-code" && method === "POST") {
    if (backend.failNextCodeWith409) {
      backend.failNextCodeWith409 = false;
      return json(409, { detail: "Your account is already linked to a Discord account. Unlink it first to link a different one." });
    }
    return json(200, { code: "ABCD-EFGH", expires_in_seconds: 600, command: "/link ABCD-EFGH" });
  }
  return json(404, { detail: "unexpected " + url });
};

const { store } = await import("../../web/js/state.js");
store.setSession({ driverId: 1, displayName: "Tester", viaCookie: true });
const { renderDiscordCard } = await import("../../web/js/pages/account.js");

const card = document.getElementById("card");
const text = () => card.textContent;
const buttons = () => [...card.querySelectorAll("button")].map((b) => b.textContent);
const click = (label) => [...card.querySelectorAll("button")].find((b) => b.textContent === label).click();
const settle = (ms = 30) => new Promise((r) => setTimeout(r, ms));

// 1. not linked: shows how it works + a generate button, no unlink
await renderDiscordCard(card);
assert.ok(text().includes("Link Discord"));
assert.ok(text().includes("works once and expires after 10 minutes"));
assert.ok(text().includes("Only ever use codes you generated yourself"));
assert.deepEqual(buttons(), ["Generate link code"]);

// 2. generating shows the code, the exact command, a countdown, a copy button
click("Generate link code");
await settle();
assert.ok(text().includes("/link ABCD-EFGH"), text());
assert.match(text(), /expires in (9|10):\d\d/);
assert.ok(buttons().includes("Copy command"));
assert.ok(backend.calls.includes("POST /accounts/discord/link-code"));

// 3. the bot redeems the code (out-of-band): the card flips to "linked"
//    by itself via the status poll — no manual refresh
backend.linked = true;
await settle(3400);
assert.ok(text().includes("linked"), text());
assert.deepEqual(buttons(), ["Unlink Discord"]);
assert.ok(!text().includes("/link ABCD-EFGH"));

// 4. unlink is confirmed first: declining sends NO request
const deletesBefore = backend.calls.filter((c) => c.startsWith("DELETE")).length;
window.confirm = () => false;
click("Unlink Discord");
await settle();
assert.equal(backend.calls.filter((c) => c.startsWith("DELETE")).length, deletesBefore);
assert.deepEqual(buttons(), ["Unlink Discord"]);

// 5. accepting unlinks and returns to the "generate a code" state
window.confirm = () => true;
click("Unlink Discord");
await settle();
assert.equal(backend.linked, false);
assert.deepEqual(buttons(), ["Generate link code"]);

// 6. a server refusal (409 already linked) is shown as a plain message
backend.failNextCodeWith409 = true;
click("Generate link code");
await settle();
assert.ok(text().includes("already linked to a Discord account"), text());
assert.deepEqual(buttons(), ["Generate link code"]);

// 7. a status-load failure shows an error instead of a broken card
const realFetch = globalThis.fetch;
globalThis.fetch = async () => ({ ok: false, status: 500, json: async () => ({ detail: "boom" }), text: async () => "boom" });
await renderDiscordCard(card);
assert.ok(card.querySelector(".notice--error"), text());
globalThis.fetch = realFetch;

console.log("DISCORD-CARD-OK");
process.exit(0);
