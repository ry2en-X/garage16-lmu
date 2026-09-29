// pages/driver.js — public driver profile (V0.7.2 §4). No auth required,
// linked to from leaderboard rows and team member lists (§5).

import { api } from "../api.js";
import { el, formatLapTime, formatDate } from "../format.js";

export async function renderDriverProfile(container, driverId) {
  let profile;
  try {
    profile = await api.driverProfile(driverId);
  } catch (err) {
    container.appendChild(el("div", { class: "notice notice--error" }, err.message));
    return;
  }

  container.appendChild(el("h1", {}, profile.display_name));
  container.appendChild(el("p", { class: "subtitle" }, "Garage16 Driver"));

  // --- Statistics ---
  container.appendChild(
    el("div", { class: "card", style: "display:flex; gap:24px; flex-wrap:wrap;" }, [
      _stat(profile.total_laps, "Laps"),
      _stat(profile.tracks_driven, "Tracks"),
      _stat(profile.cars_driven, "Cars"),
      _stat(profile.personal_records.length, "Personal Records"),
    ])
  );

  // --- Teams ---
  if (profile.teams.length) {
    container.appendChild(el("h2", {}, "Teams"));
    const teamsCard = el("div", { class: "card" });
    profile.teams.forEach((t) => {
      teamsCard.appendChild(
        el("div", { class: "card-row" }, [
          el("a", { href: `#/team/${t.team_id}` }, [el("strong", {}, t.team_name)]),
          el("span", { class: "tag" }, t.role),
        ])
      );
    });
    container.appendChild(teamsCard);
  }

  // --- Personal Records ---
  container.appendChild(el("h2", {}, "Personal Records"));
  if (!profile.personal_records.length) {
    container.appendChild(el("div", { class: "empty-state" }, "No valid laps recorded yet."));
  } else {
    const rows = profile.personal_records.map((r) =>
      el("tr", {}, [
        el("td", {}, r.track_name),
        el("td", {}, r.car_name),
        el("td", {}, r.car_class || "—"),
        el("td", {}, el("span", { class: "lap-time" }, formatLapTime(r.lap_time))),
        el("td", {}, formatDate(r.set_at)),
      ])
    );
    container.appendChild(
      el("table", { class: "timing-table" }, [
        el("thead", {}, el("tr", {}, ["Track", "Car", "Class", "Time", "Set"].map((h) => el("th", {}, h)))),
        el("tbody", {}, rows),
      ])
    );
  }

  // --- Recent Activity ---
  container.appendChild(el("h2", {}, "Recent Activity"));
  if (!profile.recent_activity.length) {
    container.appendChild(el("div", { class: "empty-state" }, "No activity yet."));
  } else {
    const card = el("div", { class: "card" });
    profile.recent_activity.forEach((a) => {
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

function _stat(value, label) {
  return el("div", { style: "min-width:90px;" }, [
    el("div", { style: "font-size:1.6em; font-weight:700;" }, String(value)),
    el("div", { class: "subtitle" }, label),
  ]);
}
