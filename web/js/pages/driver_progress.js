// pages/driver_progress.js — V1.0.3: "Progress" on the public driver profile: how the driver's best
// lap developed per track + car model. One dot per day (the best valid lap of that day), a step
// line for the personal best so far, a faint line for the median of the day.
// Data: GET /api/v1/public/drivers/{id}/progress (one row per day).

import { api } from "../core/api.js";
import { el, formatLapTime } from "../core/format.js";
import { locale, t } from "../core/i18n.js";
import { trackLabel } from "../core/tracks.js";

const DAY_MS = 86400000;

// "2026-11-14" -> whole days since 1970 (UTC, so no daylight-saving shifts)
export const dayNumber = (iso) => Math.round(Date.parse(`${iso}T00:00:00Z`) / DAY_MS);

// day number -> "14 Nov 2026" (a date only; formatDate would add a time of day)
export function dayLabel(n) {
  return new Date(n * DAY_MS).toLocaleDateString(locale(), { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

// rows -> { dots, pb (step line), median, improved, first, last, days } with x = days since the first day
export function progressModel(rows) {
  const days = (rows || []).filter((r) => r && Number.isFinite(r.best)).sort((a, b) => (a.day < b.day ? -1 : 1));
  if (!days.length) return null;
  const base = dayNumber(days[0].day);
  const dots = [];
  const median = [];
  const pb = [];
  let best = Infinity;
  for (const r of days) {
    const x = dayNumber(r.day) - base;
    const before = best;
    if (r.best < best) {
      if (Number.isFinite(before)) pb.push([x, before]); // the line stays flat, then falls
      best = r.best;
      pb.push([x, best]);
    }
    dots.push([x, r.best, { note: `${t("Personal best")}: ${formatLapTime(best)} · ${r.laps} ${r.laps === 1 ? t("lap") : t("laps")}` }]);
    if (Number.isFinite(r.median)) median.push([x, r.median]);
  }
  const lastX = dots[dots.length - 1][0];
  if (pb[pb.length - 1][0] < lastX) pb.push([lastX, best]);
  return { base, dots, pb, median, first: days[0].best, best, improved: days[0].best - best, firstDay: base, days: days.length };
}

export function progressSection(driverId) {
  const root = el("div", { class: "driver-progress" });
  const wrap = el("div", {}, [el("h2", {}, t("Progress")), root]);
  root.appendChild(el("p", { class: "subtitle" }, t("Loading …")));
  let token = 0;

  const draw = async (data, key) => {
    const mine = ++token;
    root.replaceChildren();
    if (!data.combos.length) {
      root.appendChild(el("div", { class: "empty-state" }, t("Not enough valid laps yet — a track and car need at least 2.")));
      return;
    }
    const select = el("select", { id: "progressSelect", "aria-label": t("Track and car") },
      data.combos.map((c, i) => el("option", { value: String(i) },
        `${trackLabel(c.track_name)} · ${c.car_name || c.car_model} (${c.laps})`)));
    select.value = String(Math.max(0, data.combos.findIndex((c) => c.track_name === key.track_name && c.car_model === key.car_model)));
    select.addEventListener("change", async () => {
      const c = data.combos[Number(select.value)];
      try {
        const next = await api.driverProgress(driverId, c.track_name, c.car_model);
        if (mine === token) await draw({ ...next, combos: data.combos }, next.selected);
      } catch (err) {
        body.replaceChildren(el("div", { class: "notice notice--error" }, err.message));
      }
    });
    const body = el("div", { class: "driver-progress__chart" });
    root.appendChild(el("div", { class: "driver-progress__pick" }, [select]));
    root.appendChild(body);

    const model = progressModel(data.days);
    if (!model) {
      body.appendChild(el("div", { class: "empty-state" }, t("No valid laps for this combination.")));
      return;
    }
    const { chart } = await import("../stints/chart.js");
    if (mine !== token) return;
    body.appendChild(chart([
      { kind: "line", color: "#4C9BE8", width: 2, label: t("Personal best so far"), format: formatLapTime, points: model.pb, tooltip: false },
      { kind: "line", color: "#4C9BE8", width: 1, dashed: true, label: t("Median of the day"), format: formatLapTime, points: model.median },
      { kind: "dots", color: "#D8A83E", radius: 4, label: t("Best lap of the day"), format: formatLapTime, points: model.dots },
    ], {
      legend: true, integerX: true, height: 260, yFormat: formatLapTime,
      xFormat: (x) => dayLabel(model.base + x), tooltipTitle: (x) => dayLabel(model.base + x),
      xLabel: t("date"), yLabel: t("lap time"),
    }));
    body.appendChild(el("p", { class: "subtitle driver-progress__summary" }, model.improved > 0
      ? t("Personal best improved by {0} s since {1} ({2} days with laps)", [model.improved.toFixed(3), dayLabel(model.base), model.days])
      : t("No improvement since {0} yet ({1} days with laps)", [dayLabel(model.base), model.days])));
  };

  api.driverProgress(driverId).then((data) => {
    return draw(data, data.selected || {});
  }).catch((err) => {
    root.replaceChildren(el("div", { class: "notice notice--error" }, err.message));
  });
  return wrap;
}
