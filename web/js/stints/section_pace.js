// stints/section_pace.js — the session axis shared by the charts (lap in session, stint bands, pit markers) and "Pace and consistency".
// (V1.0.3: split out of pages/stints.js.)

import { el, formatLapTime } from "../core/format.js";
import { t } from "../core/i18n.js";
import { link } from "../core/link.js";
import { navigate } from "../core/router.js";
import { TEXT_LINE } from "../core/chart_core.js";
import * as S from "./stats.js";
import { FLAG_LABELS, GREY, card, chart, ciText, dash, estimateTag, fin, lapTime, legend, note, signed, stat, stintColor } from "./ui.js";

export const sessionX = (data) => { const m = new Map(); data.laps.forEach((l, i) => m.set(l.id, i + 1)); return m; };

// "Lap 12 · Stint 2 · out-lap" — the head of the value box of the session charts
export function lapTitle(data) {
  const labels = FLAG_LABELS();
  return (x) => {
    const l = data.laps[x - 1];
    if (!l) return "";
    return [t("Lap {0}", [l.lap_number]), l.stint ? t("Stint {0}", [l.stint]) : null, ...l.flags.map((fl) => labels[fl] || fl)].filter(Boolean).join(" · ");
  };
}

// A light band per stint and a marker per pit stop: the same on every session chart.
export function sessionDecor(data) {
  const xOf = sessionX(data);
  const regions = data.stints.map((st) => {
    const xs = st.lap_ids.map((id) => xOf.get(id)).filter(fin);
    return xs.length ? { x0: Math.min(...xs) - 0.5, x1: Math.max(...xs) + 0.5, color: stintColor(st.number), label: t("Stint {0}", [st.number]) } : null;
  }).filter(Boolean);
  const markers = data.pit_stops.map((p) => {
    const i = data.laps.findIndex((l) => l.lap_number === p.lap_number);
    if (i < 0) return null;
    return { x: i + 1.5, label: p.kind === "stop" && fin(p.stationary_s) ? t("Pit {0} s", [p.stationary_s.toFixed(0)]) : t("Pit") };
  }).filter(Boolean);
  return { regions, markers };
}

// Rolling mean and spread over the last `n` clean laps (spread = standard deviation).
function rollingBand(values, n) {
  return values.map((_, k) => {
    const w = values.slice(Math.max(0, k - n + 1), k + 1);
    if (w.length < Math.min(3, n)) return [null, null];
    const m = S.mean(w);
    const sd = S.stdev(w);
    return fin(m) && fin(sd) ? [m - sd, m + sd] : [null, null];
  });
}

const PACE_MODES = () => ({
  time: { label: t("Lap time"), help: t("The lap times as driven.") },
  gap: { label: t("Gap to best"), help: t("Each lap minus the best clean lap of the session: how far off your best you were.") },
  fuel: { label: t("Fuel-corrected"), help: t("Lap times as if every lap had been driven with the fuel of the lightest clean lap — the effect of the fuel load measured in this session taken out, so what is left is tyres and driving.") },
});

export function paceSection({ data, state, clean, rerender, group }) {
  const laps = data.laps;
  const p = S.paceSummary(laps, clean, { window: state.window });
  const fc = S.fuelCorrectedDegradation(laps, clean);
  const fuelOk = fc.ok && fin(fc.per_litre) && laps.some((l) => fin(l.fuel_start_l));
  const mode = state.paceMode === "fuel" && !fuelOk ? "time" : state.paceMode;
  const lightest = fuelOk ? Math.min(...laps.filter((l) => clean.has(l.id) && fin(l.fuel_start_l)).map((l) => l.fuel_start_l)) : null;
  const best = p ? p.best : null;
  // the value drawn for a lap in the chosen mode
  const valueOf = (l) => {
    if (!fin(l.lap_time)) return null;
    if (mode === "gap") return fin(best) ? l.lap_time - best : null;
    if (mode === "fuel") return fin(l.fuel_start_l) ? l.lap_time - fc.per_litre * (l.fuel_start_l - lightest) : null;
    return l.lap_time;
  };
  const fmt = mode === "gap" ? (v) => signed(v, 3, "s") : (v) => formatLapTime(v);

  const modes = PACE_MODES();
  const switcher = el("div", { class: "seg stint-toolbar", role: "group", "aria-label": t("What the chart shows") }, Object.entries(modes).map(([key, m]) => {
    const disabled = key === "fuel" && !fuelOk;
    const button = el("button", { type: "button", class: `seg__btn${key === mode ? " seg__btn--on" : ""}`, "aria-pressed": key === mode ? "true" : "false",
      title: disabled ? t("Needs clean laps with different fuel loads (see Tyres and degradation).") : m.help, ...(disabled ? { disabled: "disabled" } : {}) }, m.label);
    button.addEventListener("click", () => { state.paceMode = key; rerender(); });
    return button;
  }));

  const xOf = sessionX(data);
  const labels = FLAG_LABELS();
  const pointOf = (l) => [xOf.get(l.id), valueOf(l), {
    id: l.id, label: t("Lap time"), note: mode === "time" ? null : lapTime(l.lap_time),
    title: `${t("Lap {0}", [l.lap_number])}: ${lapTime(l.lap_time)}${l.flags.length ? ` (${l.flags.map((x) => labels[x] || x).join(", ")})` : ""}`,
  }];
  const open = (meta) => { if (meta && meta.id) navigate(link.analysis({ a: meta.id })); };
  const dotSeries = data.stints.map((st) => ({
    kind: "dots", color: stintColor(st.number), label: t("Stint {0}", [st.number]), onClick: open, format: fmt,
    points: laps.filter((l) => l.stint === st.number && clean.has(l.id)).map(pointOf),
  }));
  dotSeries.push({ kind: "dots", color: GREY, label: t("not clean (filtered)"), onClick: open, format: fmt, radius: 3,
    points: laps.filter((l) => !clean.has(l.id)).map((l) => { const q = pointOf(l); q[2].faded = true; q[2].label = t("not clean"); return q; }) });
  // rolling average (and its spread) over the last N CLEAN laps, drawn through the clean laps
  // only: a filtered lap neither breaks the line nor enters the average
  const cleanLaps = laps.filter((l) => clean.has(l.id) && fin(valueOf(l)));
  const cleanVals = cleanLaps.map(valueOf);
  const avg = S.rolling(cleanVals, state.window);
  const band = rollingBand(cleanVals, state.window);
  const cleanMax = cleanVals.length ? Math.max(...cleanVals) : null;
  const cleanMed = S.median(cleanVals);
  // the top of the axis: just above the slowest clean lap, so a 30 s in-lap does not squash the rest
  const cap = !fin(cleanMax) ? undefined : (mode === "gap" ? cleanMax + 1.5 : Math.min(cleanMax + 1.5, (cleanMed || cleanMax) * 1.08));
  const { regions, markers } = sessionDecor(data);
  const svgChart = chart([
    { kind: "band", color: TEXT_LINE, opacity: 0.1, label: t("spread (± 1 σ)"), format: fmt,
      band: cleanLaps.map((l, k) => [xOf.get(l.id), band[k][0], band[k][1]]) },
    ...dotSeries,
    { kind: "line", color: TEXT_LINE, width: 1.5, label: t("rolling average ({0})", [state.window]), format: fmt,
      points: cleanLaps.map((l, k) => [xOf.get(l.id), avg[k]]) },
  ], {
    group, legend: true, integerX: true, tooltipTitle: lapTitle(data), regions, markers,
    yFormat: fmt, xLabel: t("lap in session"), yLabel: modes[mode].label,
    yMin: mode === "gap" ? -0.2 : (fin(best) && mode === "time" ? best - 0.5 : undefined), yMax: fin(cap) ? cap : undefined,
    hlines: mode === "gap" ? [{ y: 0, label: t("best"), color: TEXT_LINE }] : [],
    title: t("Lap times of the session"),
  });
  const stats = p ? [
    stat(t("Best (clean)"), lapTime(p.best)),
    stat(t("Median"), lapTime(p.median)),
    stat(t("Average"), lapTime(p.mean)),
    stat(t("Spread"), fin(p.stdev) ? `± ${p.stdev.toFixed(3)} s` : dash, t("Standard deviation of the clean laps.")),
    stat(t("Robust spread (MAD)"), fin(p.mad) ? `± ${p.mad.toFixed(3)} s` : dash, t("Median absolute deviation: hardly moved by a single bad lap.")),
    stat(t("Within 0.5 s of best"), `${p.within_half_second} / ${p.laps}`),
    stat(t("Best {0} in a row", [p.best_window ? p.best_window.n : state.window]), p.best_window ? lapTime(p.best_window.average) : dash, t("Fastest average over consecutive clean laps — your consistency window.")),
    stat(t("Trend"), p.trend ? t("{0} s/lap", [signed(p.trend.slope, 3, "")]) + ciText(p.trend.ci) : dash, t("Theil–Sen slope of the clean lap times over the session: + = getting slower. In brackets: the 95 % range of the slope.")),
    stat(t("Consistency (trend removed)"), p.consistency ? `± ${p.consistency.sigma.toFixed(3)} s` : dash, t("Lap-to-lap spread around the trend line (robust): how evenly you drive, without the slow change from tyres and fuel.")),
  ] : [];
  const parts = [switcher, svgChart];
  if (mode === "fuel") parts.push(el("p", {}, [estimateTag(), " ", t("Fuel effect measured in this session: {0} per litre.", [signed(fc.per_litre, 3, "s")])]));
  if (p) {
    parts.push(el("div", { class: "sess__stats" }, stats));
    if (p.thirds) parts.push(note(t("Median by thirds of the session: {0} → {1} → {2}.", p.thirds.map(lapTime))));
  } else parts.push(note(t("No clean lap with the current filter.")));
  parts.push(note(t("Click a point to open that lap in the LapTime Analyzer.")));
  return card(t("Pace and consistency"), parts, { id: "stint-pace" });
}
