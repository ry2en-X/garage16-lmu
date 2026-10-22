// activity.js — the record feed (personal best, team best, Garage16 Best) shared by the dashboard,
// the driver profile and the team page (V0.9.6).
//
// One lap often sets several records at once (a new Garage16 Best is also a personal best and
// usually a team best). Up to V0.9.5 every record was its own row, so the same lap showed up two or
// three times. Now: ONE row per lap, with every record it set as a badge, the car MODEL (not the
// livery name) and — where the lap is known — a link straight into the analysis.

import { el, formatDate, formatLapTime, recordTypeLabel } from "./format.js";
import { t } from "./i18n.js";

const RANK = { WR: 0, TEAM_BEST: 1, PR: 2 };

// items: [{record_type, track_name, car_name, car_model?, lap_time, lap_id?, created_at, driver_name?}]
// -> [{...first item, types: [record types, most important first]}] in the original order
export function groupActivity(items) {
  const out = [];
  const byKey = new Map();
  for (const item of items || []) {
    const key = item.lap_id != null
      ? `lap:${item.lap_id}`
      : `${item.driver_name || ""}|${item.track_name}|${item.car_name}|${item.lap_time}`;
    const found = byKey.get(key);
    if (found) {
      if (!found.types.includes(item.record_type)) found.types.push(item.record_type);
      continue;
    }
    const entry = { ...item, types: [item.record_type] };
    byKey.set(key, entry);
    out.push(entry);
  }
  for (const entry of out) entry.types.sort((a, b) => (RANK[a] ?? 9) - (RANK[b] ?? 9));
  return out;
}

const badgeClass = (type) => (type === "WR" ? "tag tag--gold" : type === "TEAM_BEST" ? "tag tag--team" : "tag");

// The list as a card. opts.showDriver: put the driver's name first (team feed); opts.limit.
export function renderActivityList(items, { showDriver = false, limit = 10, emptyText } = {}) {
  const groups = groupActivity(items).slice(0, limit);
  if (!groups.length) return el("div", { class: "empty-state" }, emptyText || t("No activity yet."));
  const card = el("div", { class: "card activity" });
  for (const a of groups) {
    const car = a.car_model || a.car_name;
    const what = [
      showDriver && a.driver_name ? el("strong", { class: "activity__who" }, a.driver_name) : null,
      el("span", { class: "activity__where" }, `${a.track_name} · ${car}`),
    ];
    const time = el("span", { class: "lap-time" }, formatLapTime(a.lap_time));
    card.appendChild(el("div", { class: "card-row activity__row", "data-lap": a.lap_id != null ? String(a.lap_id) : null }, [
      el("div", { class: "activity__main" }, [
        el("div", { class: "activity__badges" }, a.types.map((type) => el("span", { class: badgeClass(type) }, recordTypeLabel(type)))),
        el("div", { class: "activity__what" }, what),
      ]),
      el("div", { class: "activity__side" }, [
        a.lap_id != null ? el("a", { href: `#/analysis?a=${a.lap_id}`, title: t("Analyse this lap") }, time) : time,
        el("div", { class: "subtitle activity__when" }, formatDate(a.created_at)),
      ]),
    ]));
  }
  return card;
}
