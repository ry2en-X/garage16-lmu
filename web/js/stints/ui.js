// stints/ui.js — the building blocks every section of the Stint Analyzer uses (V1.0.3: split out of
// pages/stints.js): formatting, cards, tables, tags, the flag names, and the chart module, which is
// loaded with the first session that is opened (the session list needs no charts).

import { el, formatDate, formatLapTime } from "../core/format.js";
import { t } from "../core/i18n.js";
import { NEUTRAL, SERIES_COLORS } from "../core/chart_core.js";

let charts = null;
export async function loadCharts() {
  if (!charts) charts = await import("./chart.js");
  return charts;
}
export const chart = (series, options = {}) => charts.chart(series, options);
export const legend = (items) => charts.legend(items);
export const createGroup = () => charts.createChartGroup();

export const COLORS = SERIES_COLORS;
export const GREY = NEUTRAL;
export const fin = (v) => typeof v === "number" && Number.isFinite(v);
export const dash = "—";
export const f = (v, d = 2, unit = "") => (fin(v) ? `${v.toFixed(d)}${unit ? ` ${unit}` : ""}` : dash);
export const signed = (v, d = 3, unit = "s") => (fin(v) ? `${v > 0 ? "+" : v < 0 ? "−" : "±"}${Math.abs(v).toFixed(d)}${unit ? ` ${unit}` : ""}` : dash);
export const lapTime = (v) => (fin(v) ? formatLapTime(v) : dash);
export const dateOf = (unix) => (unix ? formatDate(new Date(unix * 1000).toISOString()) : dash);
export const duration = (s) => (fin(s) ? `${Math.floor(s / 3600) ? `${Math.floor(s / 3600)} h ` : ""}${Math.floor((s % 3600) / 60)} min` : dash);

export const FLAG_LABELS = () => ({
  invalid: t("invalid"), outlap: t("out-lap"), inlap: t("in-lap"), pit_stop: t("pit stop"), traffic: t("traffic"),
  blue_flag: t("blue flag"), race_start: t("race start"), wet: t("wet"), damage: t("damage"),
  gap_before: t("laps missing before"), outlier: t("outlier"),
});
export const FLAG_HELP = () => ({
  invalid: t("The lap is not valid."),
  outlap: t("Started in the pit lane (measured)."),
  inlap: t("Ended in the pit lane (measured)."),
  pit_stop: t("Stood still in the pit lane for at least 1 s (measured)."),
  traffic: t("At least 10 % of the lap less than 1 s behind another car (LMU's time gap)."),
  blue_flag: t("A blue flag was shown for at least 5 % of the lap."),
  race_start: t("First lap of a race."),
  wet: t("Wet or partly wet track (measured per wheel)."),
  damage: t("LMU reported more body damage than on the lap before."),
  outlier: t("Slower than the threshold relative to the median of the other clean laps."),
});
export const REASON_LABELS = () => ({
  session_start: t("session start"), pit: t("pit stop"), pit_counter: t("pit stop (LMU counter)"),
  tyres: t("new tyres (measured)"), refuel: t("refuelled (measured)"), compound: t("compound changed"),
});

export function card(title, children, extra = {}) {
  return el("section", { class: `card stint-card ${extra.class || ""}`, id: extra.id || null }, [
    el("h2", { class: "stint-card__title" }, title), ...[].concat(children).filter(Boolean)]);
}
export function note(text, cls = "") { return el("p", { class: `subtitle stint-note ${cls}` }, text); }
export function estimateTag() { return el("span", { class: "stint-tag stint-tag--estimate", title: t("Calculated from measured values; not measured itself.") }, t("estimate")); }
export function measuredTag() { return el("span", { class: "stint-tag stint-tag--measured" }, t("measured")); }
export function stat(label, value, title, tag) {
  return el("div", { class: "sess__stat", title: title || null }, [
    el("div", { class: "sess__stat-label" }, [label, tag ? " " : null, tag || null]),
    el("div", { class: "sess__stat-value" }, value),
  ]);
}
export function table(head, rows, cls = "") {
  return el("div", { class: "stint-table-wrap" }, el("table", { class: `table stint-table ${cls}` }, [
    el("thead", {}, el("tr", {}, head.map((h) => el("th", { title: (h && h.title) || null }, (h && h.text) || h)))),
    el("tbody", {}, rows.map((r) => el("tr", { class: r.cls || null }, r.cells.map((c) => el("td", {}, c))))),
  ]));
}

// ------------------------------------------------------------------ query

export function stintColor(n) { return COLORS[(n - 1) % COLORS.length]; }

// " (0.021 … 0.068)" — the 95 % range of a slope
export function ciText(ci, digits = 3) {
  return ci && fin(ci[0]) && fin(ci[1]) ? ` (${ci[0].toFixed(digits)} … ${ci[1].toFixed(digits)})` : "";
}

// x of a lap in the charts over the whole session: its position in the session (1, 2, …)
