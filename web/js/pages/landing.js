// pages/landing.js — Garage16's actual homepage (the original master
// plan's §"Landing Page": not just a login form + dashboard, but a real
// racing-platform front page). Public, no auth required.
//
// V1.0.1:
//   - "What Garage16 does" comes right after the hero and shows the analysis — the reason to use
//     Garage16 — with a small drawing per feature (LapTime Analyzer, racing lines, Stint Analyzer,
//     leaderboards). The drawings are illustrations (inline SVG, no image files, no data), marked
//     as such for screen readers.
//   - "Recent activity" shows SESSIONS, not ten single laps of the same driver at the same track:
//     the laps of one driver with one car at one track, close together in time, become one row
//     (laps, best lap, the records the session brought). Data: GET /leaderboard/recent (the
//     server says which records each lap set).

import { api } from "../api.js";
import { store } from "../state.js";
import { el, formatLapTime, formatDate, recordTypeLabel } from "../format.js";
import { loadTrackNames, trackLabel } from "../tracks.js";

import { t, tp } from "../i18n.js";

const RECENT_LAPS = 50;          // what the server sends at most
const SESSIONS_SHOWN = 6;
const SESSION_GAP_MS = 90 * 60 * 1000; // laps further apart than this are a new session
const RECORD_RANK = { WR: 0, TEAM_BEST: 1, PR: 2 };

export async function renderLanding(container) {
  const signedIn = store.isSignedIn();

  container.appendChild(
    el("div", { class: "hero" }, [
      el("h1", { class: "hero__title" }, t("GARAGE16")),
      el("p", { class: "subtitle hero__claim" }, t("Your LMU Data. Your Records. Your Team.")),
      el("p", { class: "hero__lead" }, t("Record every lap in Le Mans Ultimate, compare it channel by channel with your best or the fastest lap, and race your friends on the leaderboards.")),
      // V0.9.6: the main button is what this visitor most likely wants next
      el("div", { class: "inline-form hero__actions" }, signedIn ? [
        el("a", { href: "#/dashboard", class: "btn" }, t("Go to your Garage")),
        el("a", { href: "#/analysis", class: "btn btn--ghost" }, t("LapTime Analyzer")),
        el("a", { href: "#/leaderboard", class: "btn btn--ghost" }, t("Explore Leaderboards")),
      ] : [
        el("a", { href: "#/account", class: "btn" }, t("Get Garage16")),
        el("a", { href: "#/leaderboard", class: "btn btn--ghost" }, t("Explore Leaderboards")),
        el("a", { href: "#/setup", class: "btn btn--ghost" }, t("How it works")),
      ]),
    ])
  );

  // --- What Garage16 does: the analysis first, it is what Garage16 is for ---
  container.appendChild(el("h2", {}, t("What Garage16 Does")));
  container.appendChild(el("div", { class: "features" }, [
    feature(drawTraces(), t("LapTime Analyzer"), t("Your lap against your best, a team mate's or the fastest lap: speed, throttle, brake, gear, steering, tyres and suspension — every channel, with the time gained and lost corner by corner.")),
    feature(drawLines(), t("Racing lines on the track"), t("Both laps drawn on the track map: where you brake, turn in and get back on the throttle, and how far your line is from the other one.")),
    feature(drawStint(), t("Stint Analyzer"), t("Whole sessions at a glance: pace over the stint, tyre wear and temperatures, fuel per lap and pit stops.")),
    feature(drawBoard(), t("Leaderboards & records"), t("By track and class or by exact car, team bests and personal bests — every lap is checked for track limits before it counts.")),
  ]));
  container.appendChild(el("p", { class: "subtitle features__more" }, t("The desktop client records in the background and uploads every lap by itself. Records can be announced in your Discord.")));

  // --- Recent activity: sessions, platform-wide ---
  container.appendChild(el("h2", {}, t("Recent Activity")));
  const activity = el("div", { "aria-busy": "true" }, el("div", { class: "empty-state empty-state--loading" }, t("Loading…")));
  container.appendChild(activity);
  try {
    const [entries] = await Promise.all([api.recentActivity(RECENT_LAPS), loadTrackNames()]);
    activity.innerHTML = "";
    const sessions = groupRecentSessions(entries).slice(0, SESSIONS_SHOWN);
    activity.appendChild(sessions.length
      ? renderSessions(sessions)
      : el("div", { class: "empty-state" }, t("No laps uploaded yet — be the first.")));
  } catch (err) {
    activity.innerHTML = "";
    activity.appendChild(el("div", { class: "notice notice--error" }, err.message));
  } finally {
    activity.removeAttribute("aria-busy");
  }

  // V1.0: the legal pages, one click from the front page
  container.appendChild(el("footer", { class: "site-footer" }, [
    el("a", { href: "#/legal" }, t("Legal notice")),
    el("a", { href: "#/legal?tab=privacy" }, t("Privacy")),
    el("a", { href: "#/legal?tab=terms" }, t("Terms of use")),
    el("span", {}, t("Garage16 is a private hobby project and not affiliated with Studio 397 or Le Mans Ultimate.")),
  ]));
}

// ------------------------------------------------------------------ recent sessions

// entries: GET /leaderboard/recent, newest first.
// -> [{ driver_id, driver_name, track_name, car, laps, best, newest, records }] newest first.
// A lap joins the session of the same driver, track and car if it is at most SESSION_GAP_MS
// older than that session's oldest lap so far; anything else starts a new session.
export function groupRecentSessions(entries, gapMs = SESSION_GAP_MS) {
  const sessions = [];
  const open = new Map();
  for (const e of entries || []) {
    const car = e.car_model || e.car_name || "";
    const key = `${e.driver_id}|${e.track_name}|${car}`;
    const time = Date.parse(e.uploaded_at);
    let session = open.get(key);
    if (session && !(Number.isFinite(time) && Number.isFinite(session.oldest) && session.oldest - time <= gapMs)) session = null;
    if (!session) {
      session = {
        driver_id: e.driver_id, driver_name: e.driver_name, track_name: e.track_name, car,
        laps: 0, best: null, newest: e.uploaded_at, oldest: time, records: [],
      };
      open.set(key, session);
      sessions.push(session);
    }
    session.laps += 1;
    session.oldest = time;
    if (!session.best || e.lap_time < session.best.lap_time) session.best = { lap_time: e.lap_time, lap_id: e.lap_id };
    for (const r of e.records || []) if (!session.records.includes(r)) session.records.push(r);
  }
  for (const s of sessions) s.records.sort((a, b) => (RECORD_RANK[a] ?? 9) - (RECORD_RANK[b] ?? 9));
  return sessions;
}

function renderSessions(sessions) {
  const card = el("div", { class: "card activity" });
  for (const s of sessions) {
    const time = el("span", { class: "lap-time" }, formatLapTime(s.best.lap_time));
    card.appendChild(el("div", { class: "card-row activity__row" }, [
      el("div", { class: "activity__main" }, [
        s.records.length ? el("div", { class: "activity__badges" }, s.records.map((type) => el("span", { class: type === "WR" ? "tag tag--gold" : type === "TEAM_BEST" ? "tag tag--team" : "tag" }, recordTypeLabel(type)))) : null,
        el("div", { class: "activity__what" }, [
          el("a", { href: `#/driver/${s.driver_id}` }, el("strong", { class: "activity__who" }, s.driver_name)),
          el("span", { class: "activity__where" }, `${trackLabel(s.track_name)} · ${s.car}`),
        ]),
      ]),
      el("div", { class: "activity__side" }, [
        s.best.lap_id != null ? el("a", { href: `#/analysis?a=${s.best.lap_id}`, title: t("Analyse this lap") }, time) : time,
        el("div", { class: "subtitle activity__when" }, `${tp("{0} lap", "{0} laps", s.laps, [s.laps])} · ${formatDate(s.newest)}`),
      ]),
    ]));
  }
  return card;
}

// ------------------------------------------------------------------ features

function feature(drawing, title, body) {
  return el("div", { class: "feature" }, [
    drawing,
    el("strong", { class: "feature__title" }, title),
    el("p", { class: "subtitle feature__body" }, body),
  ]);
}

// The drawings: inline SVG, the analysis colours (lap A red, lap B blue), no data, no files.
const SVG_NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}, children = []) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  for (const c of children) node.appendChild(c);
  return node;
}
function frame(children) {
  return svg("svg", { class: "feature__art", viewBox: "0 0 240 120", "aria-hidden": "true", focusable: "false" }, children);
}
const A = "#e0472b";
const B = "#4fb3e8";
const GRID = "#2c3136";

function drawTraces() {
  return frame([
    ...[30, 60, 90].map((y) => svg("line", { x1: 8, x2: 232, y1: y, y2: y, stroke: GRID, "stroke-width": 1 })),
    svg("path", { d: "M8 90 C40 88 52 30 80 28 S110 80 128 86 S160 34 190 30 S222 70 232 74", fill: "none", stroke: B, "stroke-width": 2.5 }),
    svg("path", { d: "M8 92 C42 90 56 26 84 24 S112 84 132 90 S166 30 194 26 S224 66 232 70", fill: "none", stroke: A, "stroke-width": 2.5 }),
    svg("line", { x1: 132, x2: 132, y1: 10, y2: 110, stroke: "#ecedee", "stroke-width": 1, "stroke-dasharray": "3 3" }),
  ]);
}

function drawLines() {
  const track = "M40 92 C20 92 16 60 34 50 L92 22 C108 14 126 18 132 32 L146 60 C152 72 168 74 182 66 L204 52 C222 42 232 62 222 78 C212 96 190 100 170 100 Z";
  return frame([
    svg("path", { d: track, fill: "none", stroke: "#33383d", "stroke-width": 16, "stroke-linejoin": "round" }),
    svg("path", { d: track, fill: "none", stroke: B, "stroke-width": 2, transform: "translate(1.5 -1.5)" }),
    svg("path", { d: track, fill: "none", stroke: A, "stroke-width": 2, transform: "translate(-1.5 1.5)" }),
    svg("circle", { cx: 146, cy: 60, r: 4, fill: "#ecedee" }),
  ]);
}

function drawStint() {
  const laps = [62, 50, 48, 49, 51, 52, 54, 56, 58, 30, 47, 49, 50, 52];
  return frame([
    svg("line", { x1: 8, x2: 232, y1: 104, y2: 104, stroke: GRID, "stroke-width": 1 }),
    ...laps.map((h, i) => svg("rect", { x: 10 + i * 16, y: 104 - h - 20, width: 10, height: h + 20, fill: i === 9 ? "#8b9199" : A, opacity: i === 9 ? 0.5 : 0.85 })),
    svg("path", { d: "M10 24 L232 70", fill: "none", stroke: B, "stroke-width": 2, "stroke-dasharray": "4 3" }),
  ]);
}

function drawBoard() {
  return frame([
    svg("rect", { x: 92, y: 40, width: 56, height: 72, fill: "#d8a83e", opacity: 0.85 }),
    svg("rect", { x: 34, y: 62, width: 56, height: 50, fill: "#8b9199", opacity: 0.6 }),
    svg("rect", { x: 150, y: 78, width: 56, height: 34, fill: A, opacity: 0.7 }),
    svg("text", { x: 120, y: 30, "text-anchor": "middle", fill: "#ecedee", "font-size": 18, "font-family": "Oswald, sans-serif" }, [document.createTextNode("1")]),
    svg("text", { x: 62, y: 54, "text-anchor": "middle", fill: "#8b9199", "font-size": 16, "font-family": "Oswald, sans-serif" }, [document.createTextNode("2")]),
    svg("text", { x: 178, y: 70, "text-anchor": "middle", fill: "#8b9199", "font-size": 16, "font-family": "Oswald, sans-serif" }, [document.createTextNode("3")]),
  ]);
}
