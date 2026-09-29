// router.js — tiny hash router. Each page module exports a default
// async function(container) that renders itself into `container`.

import { store } from "./state.js";
import { renderAuth } from "./pages/auth.js";
import { renderLeaderboard } from "./pages/leaderboard.js";
import { renderDashboard } from "./pages/dashboard.js";
import { renderTeams } from "./pages/teams.js";
import { renderTeamDetail } from "./pages/team_detail.js";
import { renderAccount } from "./pages/account.js";
import { renderResetPassword } from "./pages/reset_password.js";
import { renderVerifyEmail } from "./pages/verify_email.js";
import { renderDriverProfile } from "./pages/driver.js";
import { renderAdmin } from "./pages/admin.js";
import { renderLanding } from "./pages/landing.js";

const routes = {
  landing: renderLanding,
  leaderboard: renderLeaderboard,
  dashboard: renderDashboard,
  teams: renderTeams,
  account: renderAccount,
  admin: renderAdmin,
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
  const effective = matchTeamDetail(path) ? "teams" : path;
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
  statusText.textContent = signedIn ? `signed in as ${session.displayName}` : "not signed in";
}

async function render() {
  const { path, query } = currentRoute();
  const teamId = matchTeamDetail(path);
  const driverId = matchDriverDetail(path);
  updateNavActive(path);
  app.innerHTML = "";
  pitwall.classList.remove("open");
  try {
    if (teamId) {
      await renderTeamDetail(app, teamId);
    } else if (driverId) {
      await renderDriverProfile(app, driverId);
    } else if (path === "reset-password") {
      await renderResetPassword(app, query.get("token") || "");
    } else if (path === "verify-email") {
      await renderVerifyEmail(app, query.get("token") || "");
    } else {
      const handler = routes[path] || renderNotFound;
      await handler(app);
    }
  } catch (err) {
    app.innerHTML = "";
    const notice = document.createElement("div");
    notice.className = "notice notice--error";
    notice.textContent = err.message || "Something went wrong rendering this page.";
    app.appendChild(notice);
  }
}

function renderNotFound(container) {
  const h1 = document.createElement("h1");
  h1.textContent = "Page not found";
  container.appendChild(h1);
}

window.addEventListener("hashchange", render);
store.subscribe(updateStatus);
mobileToggle.addEventListener("click", () => pitwall.classList.toggle("open"));

updateStatus();
render();
