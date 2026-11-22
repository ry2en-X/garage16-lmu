// activity.js — the record feed (personal best, team best, Garage16 Best) shared by the dashboard,
// the driver profile and the team page.
//
// One lap often sets several records at once (a new Garage16 Best is also a personal best and
// usually a team best). Up to V0.9.5 every record was its own row, so the same lap showed up two or
// three times. Now: ONE row per lap, with every record it set as a badge, the car MODEL (not the
// livery name) and — where the lap is known — a link straight into the analysis.

import { el, formatDate, formatLapTime, recordTypeLabel } from "./format.js";
import { t, tp } from "./i18n.js";
import { trackLabel } from "./tracks.js";
import { link } from "./link.js";

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

const SESSION_GAP_MS = 90 * 60 * 1000; // laps further apart than this are a new session

// Consecutive groups (newest first) of the same driver, track and car, close together in time, become one
// entry: { ...newest, count, best_time, best_lap_id, types (union), oldest_at }. Others pass as count 1.
export function collapseActivity(groups, gapMs = SESSION_GAP_MS) {
  const out = [];
  for (const g of groups || []) {
    const car = g.car_model || g.car_name || "";
    const key = `${g.driver_name || ""}|${g.track_name}|${car}`;
    const last = out[out.length - 1];
    const time = Date.parse(g.created_at);
    if (last && last.key === key && Number.isFinite(time) && Number.isFinite(last.oldest) && last.oldest - time <= gapMs) {
      last.entry.count += 1;
      last.oldest = time;
      last.entry.oldest_at = g.created_at;
      for (const type of g.types) if (!last.entry.types.includes(type)) last.entry.types.push(type);
      if (g.lap_time < last.entry.best_time) { last.entry.best_time = g.lap_time; last.entry.best_lap_id = g.lap_id; }
      continue;
    }
    out.push({ key, oldest: time, entry: { ...g, types: [...g.types], count: 1, best_time: g.lap_time, best_lap_id: g.lap_id, oldest_at: g.created_at } });
  }
  for (const { entry } of out) entry.types.sort((a, b) => (RANK[a] ?? 9) - (RANK[b] ?? 9));
  return out.map((o) => o.entry);
}

const badgeClass = (type) => (type === "WR" ? "tag tag--gold" : type === "TEAM_BEST" ? "tag tag--team" : "tag");

// The list as a card. opts.showDriver: put the driver's name first (team feed); opts.limit.
// opts.collapse: several records in a row at the same track with the same car become one line
export function renderActivityList(items, { showDriver = false, limit = 10, emptyText, collapse = false } = {}) {
  const groups = (collapse ? collapseActivity(groupActivity(items)) : groupActivity(items).map((g) => ({ ...g, count: 1, best_time: g.lap_time, best_lap_id: g.lap_id }))).slice(0, limit);
  if (!groups.length) return el("div", { class: "empty-state" }, emptyText || t("No activity yet."));
  const card = el("div", { class: "card activity" });
  for (const a of groups) {
    const car = a.car_model || a.car_name;
    const what = [
      showDriver && a.driver_name ? el("strong", { class: "activity__who" }, a.driver_name) : null,
      el("span", { class: "activity__where" }, a.count > 1
        ? `${tp("{0} record", "{0} records", a.count, [a.count])} · ${trackLabel(a.track_name)} · ${car}`
        : `${trackLabel(a.track_name)} · ${car}`),
    ];
    const time = el("span", { class: "lap-time" }, formatLapTime(a.best_time));
    card.appendChild(el("div", { class: "card-row activity__row", "data-lap": a.best_lap_id != null ? String(a.best_lap_id) : null }, [
      el("div", { class: "activity__main" }, [
        el("div", { class: "activity__badges" }, a.types.map((type) => el("span", { class: badgeClass(type) }, recordTypeLabel(type)))),
        el("div", { class: "activity__what" }, what),
      ]),
      el("div", { class: "activity__side" }, [
        a.best_lap_id != null ? el("a", { href: link.analysis({ a: a.best_lap_id }), title: t("Analyse this lap") }, time) : time,
        el("div", { class: "subtitle activity__when" }, formatDate(a.created_at)),
      ]),
    ]));
  }
  return card;
}
