// pages/dashboard.js — "YOUR GARAGE" (V0.7.2 §6): a personal cockpit,
// not just a raw laps table. Every number here comes from a real
// endpoint (driver profile §4, team records §21) — nothing fabricated.
// The original filterable "all your laps" browser stays intact below it.

import { api } from "../api.js";
import { store } from "../state.js";
import { el, formatLapTime, formatDate } from "../format.js";
import { renderSignInPrompt } from "./auth.js";

export async function renderDashboard(container) {
  if (!store.isSignedIn()) {
    renderSignInPrompt(container, "Sign in to see your garage.");
    return;
  }

  const session = store.getSession();
  container.appendChild(el("h1", {}, "Garage16"));
  container.appendChild(el("p", { class: "subtitle" }, `Welcome back, ${session.displayName}.`));

  if (session.driverId != null) {
    await renderCockpit(container, session.driverId);
  }
  // No driverId (shouldn't happen post-V0.7.2 — see auth.js's restore
  // flow — but stay defensive rather than throw): skip straight to the
  // laps browser below, which only ever needed the auth token.

  // --- My Laps (unchanged from pre-V0.7.2: filterable, full history) ---
  container.appendChild(el("h2", {}, "My Laps"));
  await renderLapsBrowser(container);
}

async function renderCockpit(container, driverId) {
  let profile, teams;
  try {
    [profile, teams] = await Promise.all([api.driverProfile(driverId), api.myTeams()]);
  } catch (err) {
    container.appendChild(el("div", { class: "notice notice--error" }, err.message));
    return;
  }

  // --- Your Best: fastest valid lap per track, across every car ---
  container.appendChild(el("h2", {}, "Your Best"));
  const bestPerTrack = new Map();
  for (const r of profile.personal_records) {
    const current = bestPerTrack.get(r.track_name);
    if (!current || r.lap_time < current) bestPerTrack.set(r.track_name, r.lap_time);
  }
  if (bestPerTrack.size === 0) {
    container.appendChild(el("div", { class: "empty-state" }, "No valid laps yet — once your recorder client uploads one, it'll show up here."));
  } else {
    const card = el("div", { class: "card" });
    [...bestPerTrack.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .forEach(([track, time]) => {
        card.appendChild(
          el("div", { class: "card-row" }, [el("span", {}, track), el("span", { class: "lap-time" }, formatLapTime(time))])
        );
      });
    container.appendChild(card);
  }

  // --- Your Team(s): records held + closest gap to one you don't hold ---
  if (teams.length) {
    container.appendChild(el("h2", {}, "Your Team"));
    const teamsCard = el("div", { class: "card" });
    for (const t of teams) {
      let records = [];
      try {
        records = await api.teamRecords(t.id);
      } catch {
        continue; // skip a team whose records failed to load rather than break the whole dashboard
      }
      const held = records.filter((r) => r.you_hold_it).length;
      const closest = records
        .filter((r) => !r.you_hold_it && r.gap != null)
        .sort((a, b) => a.gap - b.gap)[0];

      const rows = [
        el("div", { class: "card-row" }, [
          el("a", { href: `#/team/${t.id}` }, [el("strong", {}, t.name)]),
          el("span", {}, `${held} / ${records.length} team best${records.length === 1 ? "" : "s"} held`),
        ]),
      ];
      if (closest) {
        rows.push(
          el("div", { class: "subtitle", style: "margin:2px 0 12px;" }, [
            `Closest to a record: ${closest.track_name} — ${closest.car_model} `,
            el("span", { class: "lap-time" }, `+${closest.gap.toFixed(3)}`),
            ` (held by ${closest.driver_name})`,
          ])
        );
      }
      rows.forEach((r) => teamsCard.appendChild(r));
    }
    container.appendChild(teamsCard);
  }

  // --- Recent Activity ---
  container.appendChild(el("h2", {}, "Recent Activity"));
  if (!profile.recent_activity.length) {
    container.appendChild(el("div", { class: "empty-state" }, "No activity yet."));
  } else {
    const card = el("div", { class: "card" });
    profile.recent_activity.slice(0, 10).forEach((a) => {
      card.appendChild(
        el("div", { class: "card-row" }, [
          el("div", {}, [
            el("span", { class: "tag" }, a.record_type),
            el("span", { style: "margin-left:8px;" }, `${a.track_name} — ${a.car_name}`),
          ]),
          el("div", {}, [
            el("span", { class: "lap-time" }, formatLapTime(a.lap_time)),
            el("div", { class: "subtitle", style: "margin:2px 0 0; text-align:right;" }, formatDate(a.created_at)),
          ]),
        ])
      );
    });
    container.appendChild(card);
  }
}

async function renderLapsBrowser(container) {
  const form = el("div", { class: "inline-form section" });
  const trackField = el("div", { class: "field" }, [
    el("label", { for: "fTrack" }, "Track (optional)"),
    el("input", { id: "fTrack", type: "text" }),
  ]);
  const carField = el("div", { class: "field" }, [
    el("label", { for: "fCar" }, "Car (optional)"),
    el("input", { id: "fCar", type: "text" }),
  ]);
  const filterBtn = el("button", { class: "btn" }, "Filter");
  form.appendChild(trackField);
  form.appendChild(carField);
  form.appendChild(filterBtn);
  container.appendChild(form);

  const resultsBox = el("div");
  container.appendChild(resultsBox);

  async function load() {
    resultsBox.innerHTML = "";
    try {
      const laps = await api.myLaps({
        trackName: trackField.querySelector("input").value.trim() || undefined,
        carName: carField.querySelector("input").value.trim() || undefined,
        limit: 100,
      });
      resultsBox.appendChild(renderLapsTable(laps));
    } catch (err) {
      resultsBox.appendChild(el("div", { class: "notice notice--error" }, err.message));
    }
  }

  filterBtn.addEventListener("click", load);
  await load();
}

function renderLapsTable(laps) {
  if (!laps.length) {
    return el("div", { class: "empty-state" }, "No laps found. Once your recorder client uploads a lap, it'll show up here.");
  }

  const rows = laps.map((lap) =>
    el("tr", {}, [
      el("td", {}, lap.track_name),
      el("td", {}, lap.car_name),
      el("td", {}, `#${lap.lap_number}`),
      el("td", {}, el("span", { class: "lap-time" }, formatLapTime(lap.lap_time))),
      el(
        "td",
        {},
        el("span", { class: `tag ${lap.is_valid ? "tag--valid" : "tag--invalid"}` }, lap.is_valid ? "valid" : "invalid")
      ),
      el("td", {}, formatDate(lap.uploaded_at)),
    ])
  );

  return el("table", { class: "timing-table" }, [
    el("thead", {}, el("tr", {}, ["Track", "Car", "Lap", "Time", "", "Uploaded"].map((h) => el("th", {}, h)))),
    el("tbody", {}, rows),
  ]);
}
