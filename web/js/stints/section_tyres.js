// stints/section_tyres.js — stints, tyres and degradation, the four tyres per wheel with the wear forecast.
// (V1.0.3: split out of pages/stints.js.)

import { el, formatLapTime } from "../core/format.js";
import { t } from "../core/i18n.js";
import { TEXT_LINE, WHEEL_COLORS } from "../core/chart_core.js";
import * as S from "./stats.js";
import { sessionX, lapTitle, sessionDecor } from "./section_pace.js";
import { REASON_LABELS, card, chart, ciText, dash, duration, estimateTag, f, fin, lapTime, legend, note, signed, stintColor, table } from "./ui.js";

export function stintsSection({ data, clean }) {
  const reasons = REASON_LABELS();
  const rows = data.stints.map((st) => {
    const deg = S.degradation(data.laps, clean, st.number);
    const members = data.laps.filter((l) => l.stint === st.number && clean.has(l.id));
    return {
      cells: [
        el("span", { class: "stint-dot", style: `background:${stintColor(st.number)}` }, String(st.number)),
        `${st.first_lap}–${st.last_lap}`,
        `${members.length} / ${st.laps}`,
        st.compound || dash,
        String(st.tyre_set || dash),
        lapTime(members.length ? Math.min(...members.map((l) => l.lap_time)) : null),
        lapTime(S.median(members.map((l) => l.lap_time))),
        deg.ok ? el("span", { title: deg.ci ? t("95 % range: {0} to {1} s", [deg.ci[0].toFixed(3), deg.ci[1].toFixed(3)]) : "" }, signed(deg.slope, 3, "s")) : el("span", { class: "subtitle", title: t("At least {0} clean laps needed.", [S.MIN_DEGRADATION_LAPS]) }, dash),
        f(S.median(members.map((l) => l.fuel_used_l)), 2, "L"),
        f(S.median(members.map((l) => l.energy_used_pct)), 2, "%"),
        f(S.median(members.map((l) => l.wear_used_avg)), 3, "%"),
        `${f(st.wear_left_start, 1)} → ${f(st.wear_left_end, 1)} %`,
        duration(st.duration_s),
        st.reasons.map((r) => reasons[r] || r).join(", "),
      ],
    };
  });
  return card(t("Stints"), [
    table([t("Stint"), t("Laps"), { text: t("Clean"), title: t("Clean laps under the current filter / all laps") }, t("Compound"), t("Tyre set"), t("Best"), t("Median"),
      { text: t("Change / lap"), title: t("Theil–Sen slope of the clean lap times over the tyre age: tyres AND the car getting lighter together.") },
      t("Fuel / lap"), t("Energy / lap"), t("Tyre wear / lap"), t("Tread left"), t("Duration"), t("Why a new stint")], rows),
    note(t("A new stint starts after a pit stop; for laps without pit lane data where new tyres, fuel or another compound were measured.")),
  ], { id: "stint-stints" });
}

export function tyresSection({ data, clean }) {
  const laps = data.laps;
  const series = data.stints.map((st) => ({
    kind: "dots", color: stintColor(st.number), label: t("Stint {0}", [st.number]),
    points: laps.filter((l) => l.stint === st.number && clean.has(l.id)).map((l) => [l.tyre_age, l.lap_time, { title: `${t("Lap {0}", [l.lap_number])}: ${lapTime(l.lap_time)} · ${t("tyre age {0}", [l.tyre_age])}`, label: t("Lap {0}", [l.lap_number]) }]),
  }));
  const lines = data.stints.map((st) => {
    const deg = S.degradation(laps, clean, st.number);
    if (!deg.ok) return null;
    const ages = laps.filter((l) => l.stint === st.number).map((l) => l.tyre_age);
    const lo = Math.min(...ages);
    const hi = Math.max(...ages);
    return { kind: "line", color: stintColor(st.number), dashed: true, width: 1.5, legend: false, tooltip: false, points: [[lo, deg.intercept + deg.slope * lo], [hi, deg.intercept + deg.slope * hi]] };
  }).filter(Boolean);
  const parts = [chart([...series, ...lines], { legend: true, integerX: true, yFormat: (v) => formatLapTime(v), xLabel: t("tyre age (laps)"), yLabel: t("lap time"), title: t("Lap time over tyre age"),
    tooltipTitle: (x) => t("tyre age {0}", [x]), legendExtra: [{ label: t("trend per stint"), color: TEXT_LINE, dashed: true }] })];
  const fc = S.fuelCorrectedDegradation(laps, clean);
  if (fc.ok) {
    parts.push(el("p", {}, [estimateTag(), " ", t("Separated from the fuel load ({0} laps): tyres {1} per lap of age, fuel {2} per litre.", [fc.n, signed(fc.per_lap_tyre, 3, "s") + ciText(fc.per_lap_tyre_ci), signed(fc.per_litre, 3, "s") + ciText(fc.per_litre_ci)]),
      fc.per_lap_tyre_ci && fc.per_lap_tyre_ci[0] < 0 && fc.per_lap_tyre_ci[1] > 0 ? ` ${t("The tyre effect is not distinguishable from zero with these laps.")}` : ""]));
  } else {
    const why = {
      collinear: t("Tyre age and fuel load went down together in these laps, so their effects cannot be told apart. That needs laps with different fuel on tyres of the same age (e.g. several stints with refuelling)."),
      too_few: t("Too few clean laps with fuel data to separate tyres from fuel."),
      one_stint: t("Tyre age and fuel load went down together in these laps, so their effects cannot be told apart. That needs laps with different fuel on tyres of the same age (e.g. several stints with refuelling)."),
      no_variation: t("The fuel load did not vary enough to separate it from the tyre age."),
      singular: t("The fuel load did not vary enough to separate it from the tyre age."),
    }[fc.reason];
    parts.push(note(`${t("Change / lap shows tyres and fuel together.")} ${why || ""}`));
  }
  parts.push(table([t("Tyre set"), t("Compound"), t("Stints"), t("Laps"), t("Tread at start"), t("Tread at end"), t("Fitted")],
    data.tyre_sets.map((ts) => ({ cells: [String(ts.number), ts.compound || dash, ts.stints.join(", "), String(ts.laps), f(ts.wear_left_start, 1, "%"), f(ts.wear_left_end, 1, "%"),
      ts.new_at_start === true ? t("new (measured)") : (ts.new_at_start === null ? t("at session start") : dash)] }))));
  const wheels = data.laps[data.laps.length - 1].wear_left_pct;
  if (wheels) parts.push(note(t("Tread left after the last lap: FL {0} · FR {1} · RL {2} · RR {3} %.", ["fl", "fr", "rl", "rr"].map((w) => f(wheels[w], 1)))));
  return card(t("Tyres and degradation"), parts, { id: "stint-tyres" });
}

// The four tyres over the session (V1.0.3): temperature, pressure, tread left or brakes, one line
// per wheel; and how fast the current set wears, with an estimate of when it reaches a limit.
const WHEEL_VALUES = () => ({
  temp: { label: t("Tyre temperature"), unit: "°C", key: "tyre_temp_c", digits: 1 },
  pressure: { label: t("Tyre pressure"), unit: "kPa", key: "tyre_pressure_kpa", digits: 1 },
  wear: { label: t("Tread left"), unit: "%", key: "wear_left_pct", digits: 1 },
  brakes: { label: t("Brake temperature (max)"), unit: "°C", key: "brake_max_c", digits: 0 },
});
const WHEEL_NAMES = () => ({ fl: t("front left"), fr: t("front right"), rl: t("rear left"), rr: t("rear right") });

export function wheelsSection({ data, state, rerender, group }) {
  const laps = data.laps;
  const values = WHEEL_VALUES();
  const available = Object.entries(values).filter(([, v]) => laps.some((l) => l[v.key] && Object.keys(l[v.key]).length));
  if (!available.length) return null;
  const key = available.some(([k]) => k === state.wheelValue) ? state.wheelValue : available[0][0];
  const v = values[key];
  const names = WHEEL_NAMES();
  const switcher = el("div", { class: "seg stint-toolbar", role: "group", "aria-label": t("What the chart shows") }, available.map(([k, m]) => {
    const button = el("button", { type: "button", class: `seg__btn${k === key ? " seg__btn--on" : ""}`, "aria-pressed": k === key ? "true" : "false" }, m.label);
    button.addEventListener("click", () => { state.wheelValue = k; rerender(); });
    return button;
  }));
  const xOf = sessionX(data);
  const fmt = (x) => `${x.toFixed(v.digits)} ${v.unit}`;
  const series = ["fl", "fr", "rl", "rr"].map((w) => ({
    kind: "line", color: WHEEL_COLORS[w], label: names[w], width: 1.8, format: fmt, dashed: w === "fr" || w === "rr",
    points: laps.map((l) => [xOf.get(l.id), l[v.key] && fin(l[v.key][w]) ? l[v.key][w] : null]),
  }));
  const { regions, markers } = sessionDecor(data);
  const parts = [switcher, chart(series, {
    group, legend: true, integerX: true, tooltipTitle: lapTitle(data), regions, markers, height: 200,
    yFormat: (x) => x.toFixed(v.digits > 0 && key !== "temp" && key !== "brakes" ? 1 : 0), xLabel: t("lap in session"), yLabel: `${v.label} (${v.unit})`, title: v.label,
  })];
  if (key === "temp" || key === "pressure") {
    // front/rear and left/right balance over the clean laps: where the car loads its tyres
    const avgOf = (ws) => S.median(laps.map((l) => (l[v.key] ? S.mean(ws.map((w) => l[v.key][w]).filter(fin)) : null)).filter(fin));
    const front = avgOf(["fl", "fr"]);
    const rear = avgOf(["rl", "rr"]);
    const left = avgOf(["fl", "rl"]);
    const right = avgOf(["fr", "rr"]);
    if ([front, rear, left, right].every(fin)) {
      parts.push(note(t("Median of the session: front {0} · rear {1} · left {2} · right {3}.", [fmt(front), fmt(rear), fmt(left), fmt(right)])));
    }
  }
  if (key === "wear") parts.push(wearForecast(data));
  return card(t("Tyres per wheel"), parts, { id: "stint-wheels" });
}

// How many more laps until the most worn tyre of the current set reaches the limit.
function wearForecast(data) {
  const out = el("div");
  const limit = { value: 50 };
  const input = el("input", { type: "number", class: "input stint-input--short", min: "5", max: "95", step: "5", value: "50", "aria-label": t("Tread limit in percent") });
  const draw = () => {
    out.innerHTML = "";
    const lastSet = data.laps.length ? data.laps[data.laps.length - 1].tyre_set : null;
    const laps = data.laps.filter((l) => l.tyre_set === lastSet && l.wear_left_pct && fin(l.tyre_age));
    const fc = S.wearForecast(laps, limit.value);
    if (!fc.ok) {
      out.appendChild(note(fc.reason === "too_few" ? t("Wear forecast: at least {0} laps on the current tyre set needed.", [S.MIN_WEAR_LAPS]) : t("Wear forecast: the tread did not go down on the current tyre set.")));
      return;
    }
    const names = WHEEL_NAMES();
    out.appendChild(el("p", {}, [estimateTag(), " ", t("Current tyre set: {0} wears fastest, {1} per lap. {2} left now; at {3} % after about {4} more laps.", [
      names[fc.wheel], f(-fc.per_lap, 2, "%"), f(fc.now, 1, "%"), String(limit.value), fin(fc.laps_to_limit) ? String(Math.max(0, Math.floor(fc.laps_to_limit))) : dash,
    ])]));
  };
  input.addEventListener("change", () => { const x = Number(input.value); if (x >= 5 && x <= 95) { limit.value = x; draw(); } });
  draw();
  return el("div", {}, [el("label", { class: "stint-toolbar" }, [t("Tread limit"), " ", input, " %"]), out]);
}
