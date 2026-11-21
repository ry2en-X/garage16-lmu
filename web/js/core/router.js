// core/router.js — the website's router.
//
// Every page and its address is listed in core/routes.js; links are built by core/link.js. The
// router draws the page for the current path, follows clicks on the site's own links without
// reloading (history.pushState), and turns an old #/ address (bookmarks, Discord posts, desktop
// clients 1.0.1 and older) into the new one. Each page module is loaded when it is first opened
// (dynamic import) — the landing page never waits for the analyzers or the admin area.
//
// Pages read their parameters with routeQuery() and change the address with navigate().

import { store } from "./state.js";
import { startDesktopAppBridge } from "./app_bridge.js";
import { applyStaticTranslations, createLanguageSwitch, ensureLanguageChosen, t } from "./i18n.js";
import { checkTerms } from "./terms_gate.js";
import { legacyHashToPath, matchRoute, MOVED, syncQuery } from "./routes.js";
import { el } from "./format.js";

const byId = (id) => (typeof document !== "undefined" ? document.getElementById(id) : null);

const SITE_TITLE = "Garage16 — Le Mans Ultimate telemetry & leaderboards";
let current = null;      // { query, route, params } of the page shown — set by render()
let started = false;     // startRouter() was called (the website; not a page drawn on its own in a test)

/** The parameters of the page being shown (names, ids — see core/routes.js). Without the router
 * (a page drawn on its own) the address's own query — or that of an old #/ address — is used. */
export function routeQuery() {
  if (current) return new URLSearchParams(current.query);
  if (typeof window === "undefined") return new URLSearchParams();
  const hash = window.location.hash;
  if (hash.startsWith("#/")) return new URLSearchParams(hash.split("?")[1] || "");
  const found = matchRoute(window.location.pathname);
  return found ? syncQuery(found.route, found.params, window.location.search) : new URLSearchParams(window.location.search);
}

/** The route being shown ({ name, tab, … } from core/routes.js), or null for "page not found". */
export function currentRoute() {
  return current ? current.route : null;
}

/** Opens `path` (from core/link.js). `replace`: no new history entry (redirects, a changed filter). */
export function navigate(path, { replace = false, render: doRender = true } = {}) {
  const target = new URL(path, window.location.origin);
  const same = target.pathname + target.search === window.location.pathname + window.location.search;
  if (replace) window.history.replaceState(null, "", target.pathname + target.search);
  else if (!same) window.history.pushState(null, "", target.pathname + target.search);
  if (doRender && started) return render();
  return Promise.resolve();
}

/** Changes the address to `path` without drawing the page again (the page already shows it). */
export function setAddress(path) {
  return navigate(path, { replace: true, render: false });
}

export function setTitle(title) {
  document.title = title ? `${title} – Garage16` : SITE_TITLE;
}

function updateNavActive(route) {
  const effective = route ? route.nav || route.name : null;
  const navLinks = byId("navLinks");
  if (navLinks) navLinks.querySelectorAll("a").forEach((a) => a.classList.toggle("active", a.dataset.route === effective));
}

function updateStatus() {
  const session = store.getSession();
  const signedIn = store.isSignedIn();
  const statusDot = byId("statusDot");
  const statusText = byId("statusText");
  if (statusDot) statusDot.classList.toggle("on", signedIn);
  if (statusText) statusText.textContent = signedIn ? t("signed in as {0}", [session.displayName]) : t("not signed in");
}

function renderNotFound(container) {
  setTitle(t("Page not found"));
  container.appendChild(el("div", { class: "not-found" }, [
    el("h1", {}, t("Page not found")),
    el("p", { class: "subtitle" }, t("This address does not exist (any more). Maybe the link was cut off or the driver or team was renamed or deleted.")),
    el("div", { class: "inline-form" }, [
      el("a", { class: "btn", href: "/" }, t("Home")),
      el("a", { class: "btn btn--ghost", href: "/leaderboard" }, t("Leaderboard")),
    ]),
  ]));
}

let renderSeq = 0;
export async function render() {
  const app = byId("app");
  const pitwall = byId("pitwall");
  const path = window.location.pathname;
  if (MOVED[path]) {
    window.history.replaceState(null, "", MOVED[path] + window.location.search);
    return render();
  }
  const found = matchRoute(path);
  const seq = ++renderSeq;
  updateNavActive(found && found.route);
  if (pitwall) pitwall.classList.remove("open");
  try {
    if (!found) {
      if (seq !== renderSeq) return;
      app.innerHTML = "";
      app.classList.remove("panel--wide");
      current = { query: new URLSearchParams(window.location.search), route: null, params: {} };
      renderNotFound(app);
      return;
    }
    const { route, params } = found;
    const search = window.location.search;
    // load the page module and the address's names/ids first (usually instant), THEN clear — no blank flash
    const [fn, query, resolved] = await Promise.all([
      route.load(),
      route.query ? route.query(params, search) : new URLSearchParams(search),
      route.resolve ? route.resolve(params).catch(() => null) : Promise.resolve(undefined),
    ]);
    if (seq !== renderSeq) return;   // another page was chosen while this one loaded
    if (resolved === null) {
      app.innerHTML = "";
      current = { query: new URLSearchParams(search), route: null, params };
      renderNotFound(app);
      return;
    }
    if (resolved && resolved.canonical) window.history.replaceState(null, "", resolved.canonical + search);
    current = { query, route, params };
    setTitle(route.title ? route.title() : null);
    app.innerHTML = "";
    app.classList.remove("panel--wide"); // the analysis and circuit pages ask for the full width
    (document.scrollingElement || document.documentElement).scrollTop = 0;
    if (resolved) await fn(app, resolved.arg);
    else if (route.tokenArg) await fn(app, query.get("token") || "");
    else await fn(app);
  } catch (err) {
    if (seq !== renderSeq) return;
    app.innerHTML = "";
    app.appendChild(el("div", { class: "notice notice--error" }, err.message || t("Something went wrong rendering this page.")));
  }
}

// a click on one of the site's own links: draw the page without reloading
function onClick(event) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const a = event.target.closest && event.target.closest("a[href]");
  if (!a || (a.target && a.target !== "_self") || a.hasAttribute("download")) return;
  const href = a.getAttribute("href");
  if (href.startsWith("#/")) {          // an old-style link somewhere (e.g. in a text): the new address
    event.preventDefault();
    navigate(legacyHashToPath(href));
    return;
  }
  if (!href.startsWith("/") || href.startsWith("//") || href.startsWith("/api/")) return;
  event.preventDefault();
  navigate(href);
}

// an old #/ address (bookmark, Discord post, desktop client 1.0.1): replace it by the new one
function upgradeLegacyAddress() {
  if (window.location.hash.startsWith("#/")) {
    window.history.replaceState(null, "", legacyHashToPath(window.location.hash));
  }
}

export function startRouter() {
  started = true;
  upgradeLegacyAddress();
  window.addEventListener("popstate", render);
  window.addEventListener("hashchange", () => {
    if (window.location.hash.startsWith("#/")) {
      upgradeLegacyAddress();
      render();
    }
  });
  document.addEventListener("click", onClick);
  store.subscribe(updateStatus);
  store.subscribe(() => { checkTerms(); });
  const mobileToggle = byId("mobileToggle");
  if (mobileToggle) mobileToggle.addEventListener("click", () => byId("pitwall").classList.toggle("open"));

  // Language: translate the static menu, add the EN | DE switch, and on the very first
  // visit ask which language to use (see i18n.js).
  applyStaticTranslations();
  document.getElementById("langSwitchSlot").appendChild(createLanguageSwitch());
  ensureLanguageChosen();

  updateStatus();
  render();
  checkTerms();

  // Inside the desktop app's analysis window, sign in through the app (does nothing in a
  // normal browser — see app_bridge.js)
  startDesktopAppBridge({ onSignedIn: render });
}
