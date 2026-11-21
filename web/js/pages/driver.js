// pages/driver.js — public driver profile. No auth required,
// linked to from leaderboard rows and team member lists (§5).

import { api } from "../core/api.js";
import { renderActivityList } from "../core/activity.js";
import { el, formatLapTime, formatDate, recordTypeLabel, roleLabel } from "../core/format.js";

import { t } from "../core/i18n.js";
import { loadTrackNames, trackLabel } from "../core/tracks.js";
import { link } from "../core/link.js";
import { copyLinkButton } from "../core/copy_link.js";
import { setTitle } from "../core/router.js";
export async function renderDriverProfile(container, driverId) {
  let profile;
  try {
    [profile] = await Promise.all([api.driverProfile(driverId), loadTrackNames()]);
  } catch (err) {
    container.appendChild(el("div", { class: "notice notice--error" }, err.message));
    return;
  }

  container.appendChild(el("div", { class: "page-head" }, [
    el("h1", {}, profile.display_name),
    copyLinkButton(() => link.driver(profile.driver_id || driverId, profile.display_name)),   // V1.0.2
  ]));
  container.appendChild(el("p", { class: "subtitle" }, t("Garage16 Driver")));
  setTitle(profile.display_name);

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
          el("a", { href: link.team(membership.team_id, membership.team_name) }, [el("strong", {}, membership.team_name)]),
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
        el("td", {}, trackLabel(r.track_name)),
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
  // One row per lap, every record it set as a badge (activity.js)
  container.appendChild(renderActivityList(profile.recent_activity, { limit: 15 }));
}

function _stat(value, label) {
  return el("div", { style: "min-width:90px;" }, [
    el("div", { style: "font-size:1.6em; font-weight:700;" }, String(value)),
    el("div", { class: "subtitle" }, label),
  ]);
}
