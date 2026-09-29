// pages/landing.js — Garage16's actual homepage (the original master
// plan's §"Landing Page": not just a login form + dashboard, but a real
// racing-platform front page). Public, no auth required. Every section
// below the hero uses real data (GET /leaderboard/recent) — nothing
// fabricated, and deliberately not overloaded: recent activity plus a
// couple of entry points, not a dozen widgets.

import { api } from "../api.js";
import { store } from "../state.js";
import { el, formatLapTime, formatDate } from "../format.js";

export async function renderLanding(container) {
  const signedIn = store.isSignedIn();

  container.appendChild(
    el("div", { class: "hero" }, [
      el("h1", { style: "font-size:2.4em; margin-bottom:4px;" }, "GARAGE16"),
      el("p", { class: "subtitle", style: "font-size:1.2em;" }, "Your LMU Data. Your Records. Your Team."),
      el("div", { class: "inline-form", style: "margin-top:16px;" }, [
        el("a", { href: "#/leaderboard", class: "btn" }, "Explore Leaderboards"),
        el("a", { href: signedIn ? "#/dashboard" : "#/account", class: "btn btn--ghost" }, signedIn ? "Go to your Garage" : "Get Garage16"),
      ]),
    ])
  );

  // --- Recent Activity: real, platform-wide, most recent valid laps ---
  container.appendChild(el("h2", {}, "Recent Activity"));
  try {
    const entries = await api.recentActivity(10);
    if (!entries.length) {
      container.appendChild(el("div", { class: "empty-state" }, "No laps uploaded yet — be the first."));
    } else {
      const card = el("div", { class: "card" });
      entries.forEach((e) => {
        card.appendChild(
          el("div", { class: "card-row" }, [
            el("div", {}, [
              el("a", { href: `#/driver/${e.driver_id}` }, [el("strong", {}, e.driver_name)]),
              el("div", { class: "subtitle", style: "margin:2px 0 0;" }, `${e.track_name} — ${e.car_name}`),
            ]),
            el("div", {}, [
              el("span", { class: "lap-time" }, formatLapTime(e.lap_time)),
              el("div", { class: "subtitle", style: "margin:2px 0 0; text-align:right;" }, formatDate(e.uploaded_at)),
            ]),
          ])
        );
      });
      container.appendChild(card);
    }
  } catch (err) {
    container.appendChild(el("div", { class: "notice notice--error" }, err.message));
  }

  // --- Explore: entry points, not a full nav duplicate ---
  container.appendChild(el("h2", {}, "Explore"));
  container.appendChild(
    el("div", { class: "card", style: "display:flex; gap:24px; flex-wrap:wrap;" }, [
      el("a", { href: "#/leaderboard" }, "Leaderboards →"),
      el("a", { href: "#/teams" }, "Teams →"),
    ])
  );

  // --- What Garage16 does (static — no data needed) ---
  container.appendChild(el("h2", {}, "What Garage16 Does"));
  container.appendChild(
    el("div", { class: "card", style: "display:flex; gap:24px; flex-wrap:wrap;" }, [
      _feature("Telemetry Recording", "The desktop client reads LMU's shared memory directly, validates every lap server-side, and uploads it automatically."),
      _feature("Team Leaderboards & Records", "Track+class and exact-car leaderboards, team bests, and personal records — all derived live, nothing pre-computed to go stale."),
      _feature("Discord Integration", "Link your driver to Discord for personal-best and team-record announcements as they happen."),
    ])
  );
}

function _feature(title, body) {
  return el("div", { style: "flex:1; min-width:200px;" }, [
    el("strong", {}, title),
    el("p", { class: "subtitle", style: "margin-top:4px;" }, body),
  ]);
}
