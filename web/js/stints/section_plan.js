// stints/section_plan.js — the forecast of the last stint and the strategy calculator.
// (V1.0.3: split out of pages/stints.js.)

import { api } from "../core/api.js";
import { el, formatLapTime } from "../core/format.js";
import { t } from "../core/i18n.js";
import * as S from "./stats.js";
import { card, chart, dash, estimateTag, f, fin, lapTime, legend, measuredTag, note, stintColor, table } from "./ui.js";

export function forecastSection({ data, clean }) {
  const last = data.stints[data.stints.length - 1];
  if (!last) return null;
  const fc = S.forecastStint(data.laps, clean, last.number);
  if (!fc.ok) {
    return card(t("Forecast"), note(t("No forecast: {0} clean laps in the last stint, at least {1} needed.", [fc.n, fc.needed])), { id: "stint-forecast" });
  }
  const laps = data.laps.filter((l) => l.stint === last.number);
  // the clean laps the line is made of (a filtered in-/out-lap would only stretch the axis)
  const actual = laps.filter((l) => clean.has(l.id)).map((l) => [l.tyre_age, l.lap_time, { color: stintColor(last.number) }]);
  const maxAge = Math.max(...laps.map((l) => l.tyre_age));
  const ahead = fc.points;
  const svgChart = chart([
    { kind: "band", color: "#D8A83E", band: [[maxAge, ahead[0].lap_time, ahead[0].lap_time], ...ahead.map((p) => [p.tyre_age, p.low, p.high])], label: t("forecast range"), format: (v) => formatLapTime(v) },
    { kind: "dots", points: actual, color: stintColor(last.number), label: t("measured"), format: (v) => formatLapTime(v) },
    { kind: "line", color: "#D8A83E", dashed: true, label: t("forecast"), format: (v) => formatLapTime(v), points: [[maxAge, ahead[0].lap_time - fc.slope], ...ahead.map((p) => [p.tyre_age, p.lap_time])] },
  ], { legend: true, integerX: true, yFormat: (v) => formatLapTime(v), xLabel: t("tyre age (laps)"), yLabel: t("lap time"), title: t("Forecast of the last stint"), tooltipTitle: (x) => t("tyre age {0}", [x]) });
  return card(t("Forecast"), [
    el("p", {}, [estimateTag(), " ", t("From the {0} clean laps of stint {1}, if it goes on like this:", [fc.n, last.number])]),
    table([t("Laps ahead"), t("Tyre age"), t("Lap time"), t("Range")], ahead.map((p) => ({ cells: [`+${p.laps_ahead}`, String(p.tyre_age), lapTime(p.lap_time), `${lapTime(p.low)} – ${lapTime(p.high)}`] }))),
    svgChart,
    note(t("A straight line through the measured laps (Theil–Sen). It does not know about a tyre falling off a cliff, rain or traffic — the further ahead, the less reliable.")),
  ], { id: "stint-forecast" });
}

export function strategySection({ data, clean }) {
  const s = data.summary;
  const box = el("div");
  const measured = {
    lapTime: S.median(data.laps.filter((l) => clean.has(l.id)).map((l) => l.lap_time)),
    fuelPerLap: S.median(data.laps.filter((l) => clean.has(l.id)).map((l) => l.fuel_used_l)),
    tank: s.fuel_capacity_l,
    pitLaneLoss: S.measuredPitLoss(data.pit_stops),
  };
  const fields = [
    ["raceLaps", t("Race length (laps)"), null, 1],
    ["raceMinutes", t("or race length (minutes)"), null, 1],
    ["lapTime", t("Lap time (s)"), measured.lapTime, 0.1],
    ["fuelPerLap", t("Fuel per lap (L)"), measured.fuelPerLap, 0.01],
    ["tank", t("Tank (L)"), measured.tank, 1],
    ["minStops", t("Minimum pit stops"), 0, 1],
    ["maxStintLaps", t("Max. stint (laps)"), null, 1],
    ["maxStintMinutes", t("Max. stint (minutes)"), null, 1],
    ["refuelRate", t("Refuelling (L/s)"), null, 0.1],
    ["tyreChange", t("Tyre change (s)"), null, 0.5],
    ["minStop", t("Minimum stop (s)"), null, 0.5],
    ["pitLaneLoss", t("Pit lane loss (s)"), measured.pitLaneLoss, 0.5],
    ["marginLaps", t("Fuel margin (laps)"), 1, 0.5],
  ];
  const inputs = {};
  const grid = el("div", { class: "stint-form" });
  for (const [key, label, value, step] of fields) {
    const input = el("input", { type: "number", class: "input", step: String(step), min: "0", value: fin(value) ? String(Math.round(value * 1000) / 1000) : "", "aria-label": label });
    inputs[key] = input;
    // the pit lane loss comes from in-/out-lap times: an estimate, not a measurement
    const tag = !fin(value) ? null : (["lapTime", "fuelPerLap", "tank"].includes(key) ? measuredTag() : key === "pitLaneLoss" ? estimateTag() : null);
    grid.appendChild(el("label", { class: "stint-form__field" }, [el("span", {}, [label, tag ? " " : null, tag]), input]));
  }
  const refuel = el("input", { type: "checkbox", checked: "checked" });
  const tyres = el("input", { type: "checkbox" });
  grid.appendChild(el("label", { class: "stint-form__check" }, [refuel, ` ${t("Refuelling allowed")}`]));
  grid.appendChild(el("label", { class: "stint-form__check" }, [tyres, ` ${t("Tyre change mandatory")}`]));
  const ruleSelect = el("select", { class: "input", "aria-label": t("Rule set") }, [el("option", { value: "" }, t("— own values —"))]);
  const result = el("div", { class: "stint-strategy" });
  let ruleSets = [];
  const apply = (rules) => {
    const map = { race_laps: "raceLaps", race_minutes: "raceMinutes", min_pit_stops: "minStops", max_stint_laps: "maxStintLaps", max_stint_minutes: "maxStintMinutes",
      refuel_l_per_s: "refuelRate", tyre_change_s: "tyreChange", min_stop_s: "minStop", pit_lane_loss_s: "pitLaneLoss", fuel_margin_laps: "marginLaps" };
    for (const [k, field] of Object.entries(map)) if (rules[k] !== undefined && rules[k] !== null) inputs[field].value = String(rules[k]);
    if (rules.race_laps === undefined) inputs.raceLaps.value = "";
    if (rules.race_minutes === undefined) inputs.raceMinutes.value = "";
    refuel.checked = rules.refuelling !== false;
    tyres.checked = !!rules.mandatory_tyre_change;
  };
  ruleSelect.addEventListener("change", () => {
    const rs = ruleSets.find((r) => String(r.id) === ruleSelect.value);
    if (rs) apply(rs.rules);
    calc();
  });
  const value = (k) => (inputs[k].value === "" ? null : Number(inputs[k].value));
  function calc() {
    result.innerHTML = "";
    const res = S.strategyPlans({
      raceLaps: value("raceLaps"), raceMinutes: value("raceMinutes"), lapTime: value("lapTime"), fuelPerLap: value("fuelPerLap"), tank: value("tank"),
      minStops: value("minStops"), maxStintLaps: value("maxStintLaps"), maxStintMinutes: value("maxStintMinutes"), refuelling: refuel.checked,
      refuelRate: value("refuelRate"), tyreChange: value("tyreChange"), mandatoryTyreChange: tyres.checked, minStop: value("minStop"),
      pitLaneLoss: value("pitLaneLoss"), marginLaps: value("marginLaps"),
    });
    if (!res.ok) {
      result.appendChild(note({ lap_time: t("Enter a lap time."), fuel: t("Enter the fuel per lap."), length: t("Enter the race length in laps or minutes.") }[res.reason]));
      return;
    }
    result.appendChild(table([t("Stops"), t("Laps"), t("Stint lengths"), t("Fuel per stop"), t("Stop time"), t("Race time"), ""], res.plans.map((p) => ({
      cls: p.stops === res.best ? "stint-row--best" : (p.feasible ? null : "stint-row--impossible"),
      cells: [
        String(p.stops), String(p.laps), p.stint_laps.join(" · "),
        p.stops_detail.length ? p.stops_detail.map((d) => f(d.fuel_added, 1)).join(" · ") + " L" : dash,
        p.stops_detail.length ? p.stops_detail.map((d) => f(d.total_s, 0)).join(" · ") + " s" : dash,
        `${Math.floor(p.race_time_s / 3600)}:${String(Math.floor((p.race_time_s % 3600) / 60)).padStart(2, "0")}:${String(Math.floor(p.race_time_s % 60)).padStart(2, "0")}`,
        p.stops === res.best ? t("fastest") : (p.feasible ? "" : ({ stint_too_long: t("stint too long"), tank: t("tank too small") })[p.why] || t("not possible")),
      ],
    }))));
    result.appendChild(el("p", {}, [estimateTag(), " ", t("A plan from the numbers above: constant lap time and consumption, no safety car, no traffic. Stop time = the longest of refuelling, tyre change and minimum stop, plus the pit lane loss.")]));
    if (fin(res.max_stint_laps)) result.appendChild(note(t("Longest possible stint: {0} laps.", [res.max_stint_laps])));
  }
  Object.values(inputs).forEach((i) => i.addEventListener("change", calc));
  refuel.addEventListener("change", calc);
  tyres.addEventListener("change", calc);
  box.appendChild(el("div", { class: "stint-toolbar" }, [el("label", {}, [t("Rule set"), " ", ruleSelect])]));
  box.appendChild(grid);
  box.appendChild(result);
  calc();
  api.stintRuleSets(data.track_name).then((res) => {
    ruleSets = res.rule_sets || [];
    for (const rs of ruleSets) ruleSelect.appendChild(el("option", { value: rs.id }, rs.track_name ? rs.name : `${rs.name} (${t("all tracks")})`));
  }).catch(() => {});
  return card(t("Strategy calculator"), [note(t("Measured values of this session are filled in; change any of them or pick an admin's rule set.")), box], { id: "stint-strategy" });
}
