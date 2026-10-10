// V0.9.2 — the web app inside the desktop client's analysis window, and the download page.
//   * app_bridge.js: in the app window (window.pywebview) a signed-out page signs itself in
//     through the desktop client; in a normal browser nothing happens at all
//   * the REAL router in "app" mode: the bridge arrives after the first render, the page
//     re-renders signed in; an expired session is renewed once, failures pause
//   * pages/download.js: two editions side by side, links only from /health, never a
//     broken button
import { JSDOM } from "jsdom";
import assert from "node:assert/strict";
import fs from "node:fs";

const html = fs.readFileSync(new URL("../../web/index.html", import.meta.url), "utf8").replace(/<script[^>]*><\/script>/g, "");
const dom = new JSDOM(html, { url: "http://localhost/#/download", pretendToBeVisual: true });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, localStorage: dom.window.localStorage, sessionStorage: dom.window.sessionStorage });
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
dom.window.LMU_GARAGE_API_BASE_URL = "";
localStorage.setItem("garage16_language", "en");

const escaped = [];
process.on("unhandledRejection", (e) => escaped.push(e));
dom.window.addEventListener("error", (e) => escaped.push(e.error || e.message));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const text = (node) => node.textContent.replace(/\s+/g, " ").trim();

// ---------------------------------------------------------------- canned backend
const calls = [];
let health = { status: "ok", version: "0.9.2", client_download_url: "https://dl.example/Garage16-Client-Setup.exe" };
let activateStatus = 200;
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(url, "http://localhost");
  calls.push({ path: u.pathname, method: init.method || "GET", body: init.body, credentials: init.credentials });
  const json = (body, status = 200) => ({ ok: status < 400, status, statusText: String(status), json: async () => body, text: async () => JSON.stringify(body) });
  if (u.pathname === "/health") return json(health);
  if (u.pathname === "/accounts/app-session/activate") {
    return activateStatus === 200 ? json({ driver_id: 9, display_name: "Michael" }) : json({ detail: "Session expired" }, activateStatus);
  }
  if (u.pathname === "/leaderboard/recent") return json([]);
  return json({ detail: "not mocked" }, 404);
};

const { startDesktopAppBridge, bridgeApi, inDesktopApp } = await import("../../web/js/app_bridge.js");
const { store } = await import("../../web/js/state.js");

// ================================================================ the bridge alone
function fakeWindow() {
  const listeners = {};
  return {
    addEventListener: (name, fn) => { (listeners[name] ||= []).push(fn); },
    removeEventListener: (name, fn) => { listeners[name] = (listeners[name] || []).filter((f) => f !== fn); },
    fire: (name) => (listeners[name] || []).forEach((fn) => fn()),
    listeners,
  };
}

// a normal browser: no pywebview -> nothing, no request
{
  store.signOut();
  const win = fakeWindow();
  const before = calls.length;
  const stop = startDesktopAppBridge({ win, onSignedIn: () => assert.fail("no sign-in in a browser") });
  win.fire("pywebviewready");
  store.signOut();
  await sleep(10);
  assert.equal(calls.length, before, "a normal browser never calls the app endpoints");
  assert.equal(inDesktopApp(win), false);
  assert.equal(bridgeApi(win), null);
  stop();
}

// the app window: the bridge arrives AFTER the start (pywebviewready), the page signs in
{
  store.signOut();
  const win = fakeWindow();
  let asked = 0;
  let signedIn = 0;
  const stop = startDesktopAppBridge({ win, onSignedIn: () => signedIn++ });
  await sleep(5);
  assert.equal(signedIn, 0, "no bridge yet");
  win.pywebview = { api: { app_session: async () => { asked++; return { session_token: "T".repeat(43), driver_id: 9, display_name: "Michael" }; } } };
  win.fire("pywebviewready");
  await sleep(10);
  assert.equal(asked, 1);
  assert.equal(signedIn, 1);
  const activate = calls.filter((c) => c.path === "/accounts/app-session/activate").at(-1);
  assert.equal(activate.method, "POST");
  assert.equal(JSON.parse(activate.body).session_token, "T".repeat(43));
  assert.equal(activate.credentials, "include", "the cookie lands in the window's own cookie jar");
  assert.deepEqual(store.getSession(), { driverId: 9, displayName: "Michael", viaCookie: true });
  assert.ok(!JSON.stringify(localStorage).includes("TTTT"), "the token is never stored by the page");
  // already signed in: ready again (e.g. a reload) asks nothing
  win.fire("pywebviewready");
  await sleep(5);
  assert.equal(asked, 1);
  // the session ends (expired after a month -> api.js signs out) -> renewed once
  store.signOut();
  await sleep(10);
  assert.equal(asked, 2);
  assert.equal(signedIn, 2);
  stop();
  store.signOut();
  await sleep(5);
  assert.equal(asked, 2, "stopped: no more sign-ins");
}

// failures: the bridge reports an error -> nothing stored, and no retry for a minute
{
  store.signOut();
  const win = fakeWindow();
  let clock = 1_000;
  let asked = 0;
  const errors = [];
  win.pywebview = { api: { app_session: async () => { asked++; return { error: "Garage16 did not accept this app's login." }; } } };
  const stop = startDesktopAppBridge({ win, now: () => clock, onError: (e) => errors.push(e.message) });
  await sleep(5);
  assert.equal(asked, 1);
  assert.equal(store.isSignedIn(), false);
  assert.match(errors[0], /did not accept/);
  win.fire("pywebviewready");
  await sleep(5);
  assert.equal(asked, 1, "paused after a failure — no request storm");
  clock += 61_000;
  win.fire("pywebviewready");
  await sleep(5);
  assert.equal(asked, 2, "tries again after the pause");
  stop();
}

// the server refuses the token (expired / revoked) -> signed out, paused, no loop
{
  store.signOut();
  activateStatus = 401;
  const win = fakeWindow();
  let asked = 0;
  win.pywebview = { api: { app_session: async () => { asked++; return { session_token: "U".repeat(43) }; } } };
  const stop = startDesktopAppBridge({ win });
  await sleep(20);
  assert.equal(asked, 1, "a refused token does not trigger an endless sign-in loop");
  assert.equal(store.isSignedIn(), false);
  stop();
  activateStatus = 200;
}

// ================================================================ the download page (real router)
store.signOut();
await import("../../web/js/router.js");
await sleep(150);
const app = document.getElementById("app");
const cards = [...app.querySelectorAll(".download-card")];
assert.deepEqual(cards.map((c) => c.dataset.edition), ["standard", "analysis"], "two editions, side by side");
assert.ok(app.querySelector(".download-grid"), "in one grid row (stacks on phones)");
const standard = app.querySelector('[data-download="standard"]');
assert.equal(standard.tagName, "A");
assert.equal(standard.getAttribute("href"), "https://dl.example/Garage16-Client-Setup.exe");
const analysis = app.querySelector('[data-download="analysis"]');
assert.equal(analysis.tagName, "BUTTON", "no app download configured -> a disabled button, never a broken link");
assert.ok(analysis.disabled);
assert.ok(text(cards[1]).includes("Not available yet") && text(cards[1]).includes("Ask whoever runs your Garage16 server."));
assert.ok(text(cards[1]).includes("WebView2"), "the requirement is said before the download");
assert.ok(text(app).includes("install the other download over the existing one"), "switching is explained");
assert.ok(document.querySelector('#navLinks a[data-route="download"]').classList.contains("active"));

// both configured; an unsafe value is never turned into a link
health = { status: "ok", client_download_url: "javascript:alert(1)", client_app_download_url: "https://dl.example/Garage16-Client-Analysis-Setup.exe" };
window.location.hash = "#/leaderboard";
await sleep(60);
window.location.hash = "#/download";
await sleep(150);
assert.equal(app.querySelector('[data-download="standard"]').tagName, "BUTTON", "javascript: is not a download link");
assert.equal(app.querySelector('[data-download="analysis"]').getAttribute("href"), "https://dl.example/Garage16-Client-Analysis-Setup.exe");

// the server is down -> an error notice, both buttons disabled, no crash
health = null;
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, init) => (new URL(url, "http://localhost").pathname === "/health" ? Promise.reject(new Error("offline")) : realFetch(url, init));
window.location.hash = "#/leaderboard";
await sleep(60);
window.location.hash = "#/download";
await sleep(150);
assert.ok(app.querySelector(".notice--error"));
assert.equal(app.querySelectorAll("button[data-download][disabled]").length, 2);
globalThis.fetch = realFetch;

// ================================================================ the real router inside the app window
health = { status: "ok" };
store.signOut();
const before = calls.filter((c) => c.path === "/accounts/app-session/activate").length;
window.pywebview = { api: { app_session: async () => ({ session_token: "V".repeat(43), driver_id: 9, display_name: "Michael" }) } };
window.dispatchEvent(new window.Event("pywebviewready"));
await sleep(150);
assert.equal(calls.filter((c) => c.path === "/accounts/app-session/activate").length, before + 1);
assert.ok(text(document.getElementById("statusText")).includes("Michael"), "the menu shows the driver signed in");
assert.ok(text(app).includes("You are looking at this page in the Garage16 analysis app."), "the page re-rendered inside the app");

assert.deepEqual(escaped, [], "no uncaught error");
console.log("DESKTOP-APP-OK");
