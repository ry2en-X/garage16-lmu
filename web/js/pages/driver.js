// pages/driver.js — public driver profile (V0.7.2 §4). No auth required,
// linked to from leaderboard rows and team member lists (§5).

import { api } from "../api.js";
import { renderActivityList } from "../activity.js";
import { el, formatLapTime, formatDate, recordTypeLabel, roleLabel } from "../format.js";

import { t } from "../i18n.js";
export async function renderDriverProfile(container, driverId) {
  let profile;
  try {
    profile = await api.driverProfile(driverId);
  } catch (err) {
    container.appendChild(el("div", { class: "notice notice--error" }, err.message));
    return;
  }

  container.appendChild(el("h1", {}, profile.display_name));
  container.appendChild(el("p", { class: "subtitle" }, t("Garage16 Driver")));

  // --- Statistics ---
  container.appendChild(
    el("div", { class: "card", style: "display:flex; gap:24px; flex-wrap:wrap;" }, [
      _stat(profile.total_laps, t("Laps")),
      _stat(profile.tracks_driven, t("Tracks")),
      _stat(profile.cars_driven, t("Cars")),
      _stat(profile.personal_records.length, t("Personal Records")),
    ])
  );

  // --- Teams ---
  if (profile.teams.length) {
    container.appendChild(el("h2", {}, t("Teams")));
    const teamsCard = el("div", { class: "card" });
    profile.teams.forEach((membership) => {
      teamsCard.appendChild(
        el("div", { class: "card-row" }, [
          el("a", { href: `#/team/${membership.team_id}` }, [el("strong", {}, membership.team_name)]),
          el("span", { class: "tag" }, roleLabel(membership.role)),
        ])
      );
    });
    container.appendChild(teamsCard);
  }

  // --- Personal Records ---
  container.appendChild(el("h2", {}, t("Personal Records")));
  if (!profile.personal_records.length) {
    container.appendChild(el("div", { class: "empty-state" }, t("No valid laps recorded yet.")));
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
        el("thead", {}, el("tr", {}, [t("Track"), t("Car"), t("Class"), t("Time"), t("Set")].map((h) => el("th", {}, h)))),
        el("tbody", {}, rows),
      ])
    );
  }

  // --- Recent Activity ---
  container.appendChild(el("h2", {}, t("Recent Activity")));
  // V0.9.6: one row per lap, every record it set as a badge (activity.js)
  container.appendChild(renderActivityList(profile.recent_activity, { limit: 15 }));
}

function _stat(value, label) {
  return el("div", { style: "min-width:90px;" }, [
    el("div", { style: "font-size:1.6em; font-weight:700;" }, String(value)),
    el("div", { class: "subtitle" }, label),
  ]);
}
