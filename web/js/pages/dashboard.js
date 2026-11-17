// pages/dashboard.js — "YOUR GARAGE": the personal cockpit (V0.7.2 §6, reorganised in V0.9.6).
//
//   1. quick actions — the three things you come here for: analyse the last lap, you vs. the
//      fastest lap, the session in the Stint Analyzer
//   2. your bests per track (each with "vs. Garage16 Best") and your team(s), side by side
//   3. your recent records — one row per lap (activity.js)
//   4. all your laps, filtered in the browser (track, valid only) — one request, no "Filter" button
//
// Every number comes from a real endpoint (driver profile, team records, /telemetry/laps); the
// three requests run in parallel, team records too.

import { api } from "../api.js";
import { store } from "../state.js";
import { renderActivityList } from "../activity.js";
import { el, formatLapTime, formatDate } from "../format.js";
import { renderSignInPrompt } from "./auth.js";
import { loadTrackNames, trackLabel } from "../tracks.js";

import { t, tp } from "../i18n.js";

const LAPS_LIMIT = 200;

export async function renderDashboard(container) {
  if (!store.isSignedIn()) {
    renderSignInPrompt(container, t("Sign in to see your garage."));
    return;
  }

  const session = store.getSession();
  container.appendChild(el("h1", {}, t("Garage16")));
  container.appendChild(el("p", { class: "subtitle" }, t("Welcome back, {0}.", [session.displayName])));

  const actions = el("div", { class: "quick-actions" });
  container.appendChild(actions);
  const cockpit = el("div");
  container.appendChild(cockpit);

  const [profile, teams, laps] = await Promise.all([
    session.driverId != null ? api.driverProfile(session.driverId).catch((err) => ({ error: err })) : Promise.resolve(null),
    api.myTeams().catch(() => []),
    api.myLaps({ limit: LAPS_LIMIT }).catch((err) => ({ error: err })),
    loadTrackNames(),   // V1.0.1: the same track names as the leaderboard (tracks.js)
  ]);

  renderQuickActions(actions, Array.isArray(laps) ? laps : []);
  if (profile && profile.error) cockpit.appendChild(el("div", { class: "notice notice--error" }, profile.error.message));
  else if (profile) await renderCockpit(cockpit, profile, teams);

  container.appendChild(el("h2", {}, t("My Laps")));
  if (laps && laps.error) container.appendChild(el("div", { class: "notice notice--error" }, laps.error.message));
  else renderLapsBrowser(container, laps);
}

// ---------------------------------------------------------------- 1. quick actions

function renderQuickActions(host, laps) {
  const last = laps[0];   // newest first
  const action = (href, title, hint, primary = false) => el("a", { href, class: `quick-action${primary ? " quick-action--primary" : ""}` }, [
    el("strong", {}, title), el("span", {}, hint),
  ]);
  host.appendChild(last
    ? action(`#/analysis?a=${last.id}`, t("Analyse last lap"), `${trackLabel(last.track_name)} · ${formatLapTime(last.lap_time)}`, true)
    : action("#/download", t("Record your first lap"), t("Install the desktop client and drive"), true));
  host.appendChild(action(`#/analysis?quick=wr${last ? `&track=${encodeURIComponent(last.track_name)}` : ""}`, t("You vs. Garage16 Best"), t("Your best lap against the fastest one")));
  host.appendChild(action("#/stints", t("Stint Analyzer"), t("Whole sessions: pace, tyres, fuel")));
}

// ---------------------------------------------------------------- 2. bests + team, 3. records

async function renderCockpit(container, profile, teams) {
  const grid = el("div", { class: "dash-grid" });
  container.appendChild(grid);

  // --- Your Best: your fastest valid lap per track AND class, each against that class's
  // Garage16 Best (V1.0.1 — before: one row per track across every car, so a GT3 lap was hidden
  // behind your Hypercar lap and "vs. Garage16 Best" compared with the overall fastest car).
  // V1.0.1: a table over the full width; the team block follows below it.
  const bestCol = el("section", { class: "dash-grid__col dash-grid__col--wide" }, [el("h2", {}, t("Your Best"))]);
  grid.appendChild(bestCol);
  const rows = bestPerTrackAndClass(profile.personal_records);
  if (rows.length === 0) {
    bestCol.appendChild(el("div", { class: "empty-state" }, t("No valid laps yet — once your recorder client uploads one, it'll show up here.")));
  } else {
    bestCol.appendChild(el("p", { class: "subtitle" }, t("One row per track and class — each class has its own Garage16 Best.")));
    let previousTrack = null;
    const body = rows.map((r) => {
      const sameTrack = r.track_name === previousTrack;
      previousTrack = r.track_name;
      return el("tr", { class: sameTrack ? "best-table__more" : "" }, [
        el("td", {}, sameTrack ? "" : trackLabel(r.track_name)),
        el("td", {}, r.car_class ? el("span", { class: "tag tag--basic" }, r.car_class) : "—"),
        el("td", { title: r.car_name }, r.car_model || r.car_name),
        el("td", {}, el("span", { class: "lap-time" }, formatLapTime(r.lap_time))),
        el("td", {}, classBestCell(r)),
        el("td", { class: "best-table__when" }, formatDate(r.set_at)),
        el("td", { class: "best-table__link" }, el("a", { class: "small-link", href: compareHref(r) }, t("vs. Garage16 Best →"))),
      ]);
    });
    bestCol.appendChild(el("div", { class: "table-scroll" }, el("table", { class: "timing-table best-table" }, [
      el("thead", {}, el("tr", {}, [t("Track"), t("Class"), t("Car"), t("Your time"), t("Garage16 Best"), t("Set"), ""].map((h) => el("th", {}, h)))),
      el("tbody", {}, body),
    ])));
  }

  // --- Your Team(s): records held + closest gap to one you don't hold (records in parallel)
  const teamCol = el("section", { class: "dash-grid__col dash-grid__col--wide" }, [el("h2", {}, t("Your Team"))]);
  grid.appendChild(teamCol);
  if (!teams.length) {
    teamCol.appendChild(el("div", { class: "empty-state" }, [t("You are not in a team yet. "), el("a", { href: "#/teams" }, t("Create or join one →"))]));
  } else {
    const records = await Promise.all(teams.map((team) => api.teamRecords(team.id).catch(() => null)));
    const teamsCard = el("div", { class: "card" });
    teams.forEach((myTeam, i) => {
      const list = records[i];
      if (!list) return; // a team whose records failed to load is skipped, the rest still shows
      const held = list.filter((r) => r.you_hold_it).length;
      const closest = list.filter((r) => !r.you_hold_it && r.gap != null).sort((a, b) => a.gap - b.gap)[0];
      teamsCard.appendChild(el("div", { class: "card-row" }, [
        el("a", { href: `#/team/${myTeam.id}` }, [el("strong", {}, myTeam.name)]),
        el("span", {}, tp("{0} / {1} team best held", "{0} / {1} team bests held", list.length, [held, list.length])),
      ]));
      if (closest) {
        teamsCard.appendChild(el("div", { class: "subtitle", style: "margin:2px 0 12px;" }, [
          t("Closest to a record: {0} — {1} ", [trackLabel(closest.track_name), closest.car_model]),
          el("span", { class: "lap-time" }, `+${closest.gap.toFixed(3)}`),
          t(" (held by {0})", [closest.driver_name]),
        ]));
      }
    });
    teamCol.appendChild(teamsCard);
  }

  // --- 3. Recent records, one row per lap
  container.appendChild(el("h2", {}, t("Recent Activity")));
  container.appendChild(renderActivityList(profile.recent_activity, { limit: 8 }));
}

// ---------------------------------------------------------------- 4. all laps

function renderLapsBrowser(container, laps) {
  const tracks = [...new Set(laps.map((l) => l.track_name))].sort((a, b) => trackLabel(a).localeCompare(trackLabel(b)));
  const trackSelect = el("select", { id: "fTrack", "aria-label": t("Track") }, [
    el("option", { value: "" }, t("All tracks")),
    ...tracks.map((name) => el("option", { value: name }, trackLabel(name))),
  ]);
  const validOnly = el("input", { type: "checkbox", id: "fValid" });
  const count = el("span", { class: "subtitle laps-filter__count" });
  container.appendChild(el("div", { class: "inline-form laps-filter" }, [
    el("div", { class: "field" }, [el("label", { for: "fTrack" }, t("Track")), trackSelect]),
    el("label", { class: "laps-filter__check", for: "fValid" }, [validOnly, ` ${t("Valid laps only")}`]),
    count,
  ]));
  const resultsBox = el("div");
  container.appendChild(resultsBox);
  const draw = () => {
    const shown = laps.filter((l) => (!trackSelect.value || l.track_name === trackSelect.value) && (!validOnly.checked || l.is_valid));
    count.textContent = tp("{0} lap", "{0} laps", shown.length, [shown.length]);
    resultsBox.innerHTML = "";
    resultsBox.appendChild(renderLapsTable(shown));
  };
  trackSelect.addEventListener("change", draw);
  validOnly.addEventListener("change", draw);
  draw();
}

function renderLapsTable(laps) {
  if (!laps.length) {
    return el("div", { class: "empty-state" }, t("No laps found. Once your recorder client uploads a lap, it'll show up here."));
  }

  const rows = laps.map((lap) =>
    el("tr", {}, [
      el("td", {}, trackLabel(lap.track_name)),
      el("td", { title: lap.car_name }, lap.car_model || lap.car_name),
      el("td", {}, `#${lap.lap_number}`),
      el("td", {}, el("span", { class: "lap-time" }, formatLapTime(lap.lap_time))),
      el(
        "td",
        {},
        [
          el("span", {
            class: `tag ${lap.is_valid ? "tag--valid" : "tag--invalid"}`,
            title: !lap.is_valid && lap.invalid_reason ? lap.invalid_reason.join(" · ") : null,
          }, lap.is_valid ? t("valid") : t("invalid")),
          // V0.9.3: the track-limits check left this lap to an admin; it counts until then
          lap.review_status === "pending"
            ? el("span", { class: "tag tag--basic", style: "margin-left:6px;", title: t("The automatic track-limits check asked an admin to look at this lap. It counts until then.") }, t("under review"))
            : null,
          // V0.9.9: held = waiting for an admin, not counted yet; quarantined = the file failed its check
          lap.review_status === "held"
            ? el("span", { class: "tag tag--basic", style: "margin-left:6px;", "data-review": "held", title: t("The check could not confirm this lap on its own (see the analysis: track limits & events). It is stored and can be analysed, but it counts for leaderboards and records only after an admin has looked at it.") }, t("waiting for review"))
            : null,
          lap.review_status === "quarantined"
            ? el("span", { class: "tag tag--invalid", style: "margin-left:6px;", "data-review": "quarantined", title: t("The stored telemetry file no longer matches what was uploaded. The lap was set aside until an admin has checked it.") }, t("set aside"))
            : null,
          lap.data_level === "basic"
            ? el("span", { class: "tag tag--basic", style: "margin-left:6px;", title: t("Recorded by a client older than 0.9.0 — only the basic channels in the analysis") }, t("basic data"))
            : null,
        ]
      ),
      el("td", {}, formatDate(lap.uploaded_at)),
      el("td", {}, el("a", { href: `#/analysis?a=${lap.id}` }, t("Analyse"))),
    ])
  );

  const table = el("table", { class: "timing-table" }, [
    el("thead", {}, el("tr", {}, [t("Track"), t("Car"), t("Lap"), t("Time"), "", t("Uploaded"), ""].map((h) => el("th", {}, h)))),
    el("tbody", {}, rows),
  ]);
  if (!laps.some((lap) => lap.data_level === "basic")) return table;
  // Some laps come from a client older than 0.9.0: say what that means, once.
  return el("div", {}, [
    el("div", { class: "notice notice--basic" },
      t("Laps tagged \"basic data\" were recorded with a Garage16 client older than 0.9.0. They still count for the leaderboard and can be analysed, but contain fewer channels (no suspension, tyre forces, G-forces or TC/ABS). Update the client and drive again to get the full data.")),
    table,
  ]);
}


// Order of the classes in the UI — the same as the leaderboard (server/catalog.py CLASSES).
const CLASS_ORDER = ["Hypercar", "LMP2 WEC", "LMP2 ELMS", "LMP2", "LMP3", "GTE", "GT3"];
const classRank = (name) => {
  const i = CLASS_ORDER.indexOf(name);
  return i === -1 ? CLASS_ORDER.length : i;
};

// Your personal records are per (track, car model); "Your Best" keeps the fastest per
// (track, class), sorted by track name, then class (Hypercar first).
export function bestPerTrackAndClass(records) {
  const best = new Map();
  for (const r of records || []) {
    const key = `${r.track_name}\u0000${r.car_class || ""}`;
    const current = best.get(key);
    if (!current || r.lap_time < current.lap_time) best.set(key, r);
  }
  return [...best.values()].sort((a, b) =>
    trackLabel(a.track_name).localeCompare(trackLabel(b.track_name))
    || classRank(a.car_class) - classRank(b.car_class)
    || String(a.car_class || "").localeCompare(String(b.car_class || "")));
}

// The class's Garage16 Best: "Record" when it is yours, otherwise the gap and who holds it.
function classBestCell(r) {
  if (r.class_best_time == null) return "—";
  const gap = r.lap_time - r.class_best_time;
  if (gap <= 0.0005) return el("span", { class: "tag tag--valid" }, t("Your record"));
  return el("span", { title: r.class_best_driver_name ? `${formatLapTime(r.class_best_time)} · ${r.class_best_driver_name}` : formatLapTime(r.class_best_time) }, [
    el("span", { class: "lap-time" }, formatLapTime(r.class_best_time)),
    el("span", { class: "best-table__gap" }, ` +${gap.toFixed(3)}`),
  ]);
}

// "vs. Garage16 Best" for exactly this row: your best lap in this car model, against the fastest
// lap of the same class on this track (scope=class).
function compareHref(r) {
  const params = new URLSearchParams({ quick: "wr", track: r.track_name, scope: "class" });
  if (r.car_model) params.set("model", r.car_model);
  return `#/analysis?${params.toString()}`;
}
