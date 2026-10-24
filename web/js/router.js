// router.js — tiny hash router. Each page module exports a default
// async function(container) that renders itself into `container`.

import { store } from "./state.js";
// V0.9.6: every page is loaded when it is first opened (dynamic import), not all at start —
// the landing page no longer waits for the ~40 modules of the analysis, the stint analyzer and
// the admin area. A page module is fetched once and kept by the browser.
const page = (path, name) => () => import(path).then((m) => m[name]);
const loadTeamDetail = page("./pages/team_detail.js", "renderTeamDetail");
const loadDriverProfile = page("./pages/driver.js", "renderDriverProfile");
const loadResetPassword = page("./pages/reset_password.js", "renderResetPassword");
const loadVerifyEmail = page("./pages/verify_email.js", "renderVerifyEmail");
import { startDesktopAppBridge } from "./app_bridge.js";

import { applyStaticTranslations, createLanguageSwitch, ensureLanguageChosen, t } from "./i18n.js";
const routes = {
  landing: page("./pages/landing.js", "renderLanding"),
  leaderboard: page("./pages/leaderboard.js", "renderLeaderboard"),
  dashboard: page("./pages/dashboard.js", "renderDashboard"),
  analysis: page("./pages/analysis.js", "renderAnalysis"),
  // V0.9.4: the Stint Analyzer; #/sessions is the V0.9.3 address of the same page
  stints: page("./pages/stints.js", "renderStints"),
  sessions: page("./pages/stints.js", "renderStints"),
  circuits: page("./pages/circuits.js", "renderCircuits"),
  download: page("./pages/download.js", "renderDownload"),
  // V1.0: the setup guide and the legal pages (both public)
  setup: page("./pages/setup.js", "renderSetup"),
  legal: page("./pages/legal.js", "renderLegal"),
  teams: page("./pages/teams.js", "renderTeams"),
  account: page("./pages/account.js", "renderAccount"),
  admin: page("./pages/admin.js", "renderAdmin"),
};

const app = document.getElementById("app");
const navLinks = document.getElementById("navLinks");
const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");
const pitwall = document.getElementById("pitwall");
const mobileToggle = document.getElementById("mobileToggle");

function currentRoute() {
  const hash = window.location.hash.replace(/^#\//, "");
  const [path, queryString] = hash.split("?");
  // V0.7.2: the empty hash (first visit, or clicking the GARAGE16 logo)
  // now lands on the landing page, not straight into the leaderboard —
  // see pages/landing.js.
  return { path: path || "landing", query: new URLSearchParams(queryString || "") };
}

// "team/123" -> "123", "driver/456" -> "456", else null. Kept as small
// special cases rather than a full route-parameter system — these are
// the only dynamic routes the app has.
function matchTeamDetail(path) {
  const m = /^team\/(\d+)$/.exec(path);
  return m ? m[1] : null;
}

function matchDriverDetail(path) {
  const m = /^driver\/(\d+)$/.exec(path);
  return m ? m[1] : null;
}

function updateNavActive(path) {
  const effective = matchTeamDetail(path) ? "teams" : path === "sessions" ? "stints" : path;
  navLinks.querySelectorAll("a").forEach((a) => {
    a.classList.toggle("active", a.dataset.route === effective);
  });
}

function updateStatus() {
  const session = store.getSession();
  // V0.7.2 fix: was `!!session?.authToken`, which never matched a
  // cookie-based login (see state.js) — the status bar would show "not
  // signed in" for exactly the session type this version made the
  // default. store.isSignedIn() already covers both credential shapes.
  const signedIn = store.isSignedIn();
  statusDot.classList.toggle("on", signedIn);
  statusText.textContent = signedIn ? t("signed in as {0}", [session.displayName]) : t("not signed in");
}

let renderSeq = 0;
async function render() {
  const { path, query } = currentRoute();
  const teamId = matchTeamDetail(path);
  const driverId = matchDriverDetail(path);
  const seq = ++renderSeq;
  updateNavActive(path);
  pitwall.classList.remove("open");
  try {
    // load the page module first (usually instant, cached), THEN clear — no blank flash
    let run;
    if (teamId) {
      const fn = await loadTeamDetail();
      run = () => fn(app, teamId);
    } else if (driverId) {
      const fn = await loadDriverProfile();
      run = () => fn(app, driverId);
    } else if (path === "reset-password") {
      const fn = await loadResetPassword();
      run = () => fn(app, query.get("token") || "");
    } else if (path === "verify-email") {
      const fn = await loadVerifyEmail();
      run = () => fn(app, query.get("token") || "");
    } else {
      const fn = routes[path] ? await routes[path]() : renderNotFound;
      run = () => fn(app);
    }
    if (seq !== renderSeq) return;   // another page was chosen while this one loaded
    app.innerHTML = "";
    app.classList.remove("panel--wide"); // the analysis and circuit pages ask for the full width
    (document.scrollingElement || document.documentElement).scrollTop = 0;
    await run();
  } catch (err) {
    app.innerHTML = "";
    const notice = document.createElement("div");
    notice.className = "notice notice--error";
    notice.textContent = err.message || t("Something went wrong rendering this page.");
    app.appendChild(notice);
  }
}

function renderNotFound(container) {
  const h1 = document.createElement("h1");
  h1.textContent = t("Page not found");
  container.appendChild(h1);
}

window.addEventListener("hashchange", render);
store.subscribe(updateStatus);
mobileToggle.addEventListener("click", () => pitwall.classList.toggle("open"));

// Language (V0.9.1): translate the static menu, add the EN | DE switch, and on the very first
// visit ask which language to use (see i18n.js).
applyStaticTranslations();
document.getElementById("langSwitchSlot").appendChild(createLanguageSwitch());
ensureLanguageChosen();

updateStatus();
render();

// V0.9.2: inside the desktop app's analysis window, sign in through the app (does
// nothing in a normal browser — see app_bridge.js)
startDesktopAppBridge({ onSignedIn: render });
