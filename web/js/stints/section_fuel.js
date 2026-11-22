// stints/section_fuel.js — fuel and energy, vehicle state, traffic and positions, pit stops.
// (V1.0.3: split out of pages/stints.js.)

import { el } from "../core/format.js";
import { t } from "../core/i18n.js";
import { TEXT_LINE } from "../core/chart_core.js";
import * as S from "./stats.js";
import { lapTitle, sessionDecor } from "./section_pace.js";
import { GREY, card, chart, dash, estimateTag, f, fin, lapTime, legend, measuredTag, note, signed, stat, stintColor, table } from "./ui.js";

export function fuelSection({ data, clean, group, state }) {
  const laps = data.laps;
  const s = data.summary;
  const perLap = S.median(laps.filter((l) => clean.has(l.id)).map((l) => l.fuel_used_l));
  const energyPerLap = S.median(laps.filter((l) => clean.has(l.id)).map((l) => l.energy_used_pct));
  const medianLap = S.median(laps.filter((l) => clean.has(l.id)).map((l) => l.lap_time));
  // the margin lives in the page's state, so it survives a re-render (a filter change)
  const margin = { get value() { return state.fuelMargin; }, set value(v) { state.fuelMargin = v; } };
  const out = el("div");
  const marginInput = el("input", { type: "number", class: "input stint-input--short", min: "0", max: "10", step: "0.5", value: String(margin.value), "aria-label": t("Safety margin in laps") });
  const draw = () => {
    for (const node of out.querySelectorAll(".schart")) if (node.dispose) node.dispose();
    out.innerHTML = "";
    const fuel = S.fuelPlan({ fuelLeft: s.fuel_end_l, perLap, lapTime: medianLap, timeLeft: s.session_left_s, lapsLeft: s.max_laps ? s.max_laps - (laps[laps.length - 1].lap_number || 0) : null, marginLaps: margin.value });
    const energy = S.fuelPlan({ fuelLeft: s.energy_end_pct, perLap: energyPerLap, lapTime: medianLap, timeLeft: s.session_left_s, lapsLeft: s.max_laps ? s.max_laps - (laps[laps.length - 1].lap_number || 0) : null, marginLaps: margin.value });
    out.appendChild(el("div", { class: "sess__stats" }, [
      stat(t("Fuel / lap"), f(perLap, 2, "L"), t("Median of the clean laps."), measuredTag()),
      stat(t("Fuel left"), f(s.fuel_end_l, 1, "L"), null, measuredTag()),
      stat(t("Tank"), f(s.fuel_capacity_l, 0, "L")),
      stat(t("Laps with this fuel"), fin(fuel.laps_possible) ? `${fuel.laps_possible.toFixed(1)} (${t("{0} with margin", [fuel.laps_possible_with_margin.toFixed(1)])})` : dash, null, estimateTag()),
      stat(t("Energy / lap"), f(energyPerLap, 2, "%"), null, measuredTag()),
      stat(t("Energy left"), f(s.energy_end_pct, 1, "%"), null, measuredTag()),
      stat(t("Laps with this energy"), fin(energy.laps_possible) ? energy.laps_possible.toFixed(1) : dash, null, estimateTag()),
    ]));
    if (fin(fuel.laps_remaining)) {
      out.appendChild(el("p", {}, [estimateTag(), " ", t("About {0} laps to go in this session (time left ÷ median lap). Fuel target to finish with {1} laps margin: {2} per lap{3}.", [
        fuel.laps_remaining, margin.value, f(fuel.target_per_lap, 2, "L"),
        fuel.enough === false ? ` — ${t("{0} short at the current consumption", [f(fuel.missing, 1, "L")])}` : "",
      ])]));
      if (fin(energy.target_per_lap)) out.appendChild(el("p", {}, [estimateTag(), " ", t("Energy target: {0} per lap.", [f(energy.target_per_lap, 2, "%")])]));
    } else {
      out.appendChild(note(t("No target consumption: the time left in the session was not recorded (client 0.9.4+ needed).")));
    }
    const sectors = S.fuelPerSector(laps, clean);
    if (sectors) out.appendChild(note(t("Fuel per sector (median): S1 {0} · S2 {1} · S3 {2} L.", sectors.map((v) => f(v, 3)))));
    const maps = S.byEngineMap(laps, clean);
    if (maps.length > 1) {
      out.appendChild(table([t("Engine map"), t("Laps"), t("Fuel / lap"), t("Median lap")], maps.map((m) => ({ cells: [String(m.map), String(m.laps), f(m.per_lap, 2, "L"), lapTime(m.lap_time)] }))));
      out.appendChild(note(t("By the engine map used most of each lap. Other things changed too (traffic, tyres), so this is a comparison, not a measured effect of the map alone.")));
    }
    // V1.0.3: the tank over the session (and, dashed, where it goes at the median consumption until
    // the session ends), and the consumption per lap as bars with the median as a line
    const { regions, markers } = sessionDecor(data);
    const tank = laps.map((l, i) => [i + 1, l.fuel_end_l]);
    if (tank.some((p) => fin(p[1]))) {
      const lastX = laps.length;
      const ahead = fin(fuel.laps_remaining) && fin(perLap) && fin(s.fuel_end_l)
        ? Array.from({ length: Math.min(Math.ceil(fuel.laps_remaining), 200) + 1 }, (_, k) => [lastX + k, Math.max(s.fuel_end_l - perLap * k, 0)])
        : [];
      out.appendChild(chart([
        { kind: "line", color: "#D8A83E", label: t("Fuel in the tank"), format: (v) => f(v, 1, "L"), points: tank },
        ...(ahead.length ? [{ kind: "line", color: "#D8A83E", dashed: true, width: 1.5, label: t("at the median consumption"), format: (v) => f(v, 1, "L"), points: ahead }] : []),
      ], { group: ahead.length ? undefined : group, legend: ahead.length > 0, integerX: true, regions, markers, height: 180, yZero: true,
        tooltipTitle: (x) => (x <= lastX ? lapTitle(data)(x) : t("{0} laps ahead", [x - lastX])),
        hlines: fin(margin.value) && fin(perLap) ? [{ y: perLap * margin.value, label: t("margin"), color: "#E0472B" }] : [],
        xLabel: t("lap in session"), yLabel: t("fuel (L)"), title: t("Fuel in the tank") }));
    }
    const used = laps.map((l, i) => [i + 1, l.fuel_used_l, { color: clean.has(l.id) ? stintColor(l.stint) : GREY, faded: !clean.has(l.id) }]);
    if (used.some((p) => fin(p[1]))) {
      out.appendChild(chart([
        { kind: "bars", color: "#D8A83E", label: t("Fuel / lap"), format: (v) => f(v, 2, "L"), points: used },
      ], { group, integerX: true, regions, markers, height: 150, yZero: true, tooltipTitle: lapTitle(data),
        hlines: fin(perLap) ? [{ y: perLap, label: t("median {0}", [f(perLap, 2, "L")]), color: TEXT_LINE }] : [],
        xLabel: t("lap in session"), yLabel: t("fuel / lap (L)"), title: t("Fuel per lap") }));
    }
  };
  marginInput.addEventListener("change", () => { const v = Number(marginInput.value); if (v >= 0 && v <= 10) { margin.value = v; draw(); } });
  draw();
  return card(t("Fuel and energy"), [el("label", { class: "stint-toolbar" }, [t("Safety margin"), " ", marginInput, " ", t("laps")]), out], { id: "stint-fuel" });
}

export function stateSection({ data, clean, group }) {
  const laps = data.laps;
  const corr = S.stateCorrelations(laps, clean);
  const labelOf = (key) => ({ tyre_temp_avg: t("Tyre temperature (avg)"), tyre_pressure_avg: t("Tyre pressure (avg)"), wear_left_avg: t("Tread left (avg)"), fuel_start_l: t("Fuel at lap start"), track_temp: t("Track temperature") })[key] || key;
  const strength = (r) => (Math.abs(r) >= 0.7 ? t("strong") : Math.abs(r) >= 0.4 ? t("moderate") : t("weak"));
  const rows = corr.map((c) => ({ cells: [labelOf(c.key), c.ok ? `${c.r >= 0 ? "+" : "−"}${Math.abs(c.r).toFixed(2)} (${strength(c.r)})` : (c.n >= S.MIN_CORRELATION_LAPS ? t("did not change") : t("needs {0} laps", [S.MIN_CORRELATION_LAPS])), String(c.n)] }));
  const damaged = laps.filter((l) => l.damage && (l.damage.dent_max > 0 || l.damage.overheating || l.damage.detached));
  const parts = [
    table([t("Measured value"), { text: t("Goes with lap time (r)"), title: t("Pearson correlation over the clean laps: + = higher value, slower lap.") }, t("Laps")], rows),
    note(t("A correlation, not a cause: two things can move together because of a third (traffic, track evolution).")),
  ];
  const temps = laps.map((l, i) => [i + 1, l.tyre_temp_avg]);
  if (temps.some((p) => fin(p[1]))) {
    parts.push(chart([
      { kind: "line", color: "#E0472B", points: temps, label: t("Tyre temperature (avg)"), format: (v) => `${v.toFixed(1)} °C` },
      { kind: "line", color: "#5FC9C2", points: laps.map((l, i) => [i + 1, l.track_temp]), label: t("Track temperature"), format: (v) => `${v.toFixed(1)} °C`, dashed: true },
    ], { group, legend: true, integerX: true, height: 160, tooltipTitle: lapTitle(data), xLabel: t("lap in session"), yLabel: "°C", title: t("Tyre temperature per lap") }));
  }
  if (damaged.length) {
    parts.push(table([t("Lap"), t("Damage (0–2)"), t("Overheating"), t("Part detached")], damaged.map((l) => ({ cells: [String(l.lap_number), String(l.damage.dent_max), l.damage.overheating ? t("yes") : t("no"), l.damage.detached ? t("yes") : t("no")] }))));
  } else if (data.summary.has_context) {
    parts.push(note(t("No damage reported by LMU in this session.")));
  }
  return card(t("Vehicle state"), parts, { id: "stint-state" });
}

export function trafficSection({ data, clean, group }) {
  const tr = S.trafficSummary(data.laps, clean);
  if (!tr.measured) return card(t("Traffic"), note(t("Not recorded for these laps: the gaps to other cars come with client 0.9.4.")), { id: "stint-traffic" });
  const places = data.laps.map((l, i) => [i + 1, fin(l.place) ? l.place : null]);
  const placeChart = places.filter((p) => fin(p[1])).length >= 2 && new Set(places.map((p) => p[1]).filter(fin)).size > 1
    ? chart([{ kind: "line", color: "#4C9BE8", label: t("Position"), format: (v) => `P${v}`, points: places },
      { kind: "dots", color: "#E0472B", label: t("Traffic lap"), format: (v) => `P${v}`, radius: 4,
        points: data.laps.map((l, i) => (l.flags.includes("traffic") || l.flags.includes("blue_flag") ? [i + 1, l.place, { label: l.flags.includes("blue_flag") ? t("blue flag") : t("traffic") }] : null)).filter((p) => p && fin(p[1])) }],
    { group, legend: true, integerX: true, invertY: true, height: 160, tooltipTitle: lapTitle(data), yFormat: (v) => (Number.isInteger(v) ? `P${v}` : ""), xLabel: t("lap in session"), yLabel: t("Position"), title: t("Position over the session") })
    : null;
  return card(t("Traffic"), [
    placeChart,
    el("div", { class: "sess__stats" }, [
      stat(t("Laps in traffic"), String(tr.traffic_laps), t("At least 10 % of the lap less than 1 s behind another car."), measuredTag()),
      stat(t("Their median vs clean"), signed(tr.difference, 2), null),
      stat(t("Blue flag laps"), String(tr.blue_flag_laps), null, measuredTag()),
      stat(t("Places gained / lost"), `+${tr.places_gained} / −${tr.places_lost}`, null, measuredTag()),
    ]),
    note(t("The difference is measured; that the traffic caused all of it is not proven.")),
  ], { id: "stint-traffic" });
}

export function pitSection({ data }) {
  if (!data.pit_stops.length) {
    return card(t("Pit stops"), note(data.summary.has_pit_data ? t("No pit stop in this session.") : t("No pit lane data for these laps (client older than 0.9.4).")), { id: "stint-pits" });
  }
  const rows = data.pit_stops.map((p) => ({ cells: [
    String(p.lap_number), p.kind === "stop" ? t("stop") : t("drive-through"), f(p.lane_s, 1, "s"), f(p.stationary_s, 1, "s"),
    fin(p.fuel_added_l) ? f(p.fuel_added_l, 1, "L") : dash,
    p.tyres_changed === true ? t("yes") : p.tyres_changed === false ? t("no") : dash,
    p.compound_before || p.compound_after ? `${p.compound_before || "?"} → ${p.compound_after || "?"}` : dash,
    fin(p.loss_s) ? el("span", {}, [signed(p.loss_s, 1, "s"), " ", estimateTag()]) : el("span", { class: "subtitle", title: t("Needs at least 3 clean laps in the session.") }, dash),
  ] }));
  return card(t("Pit stops"), [
    table([t("In-lap"), t("Kind"), t("Pit lane"), t("Standing"), t("Fuel added"), t("New tyres"), t("Compound"), { text: t("Time lost"), title: t("In-lap + out-lap minus as many median clean laps.") }], rows),
    note(t("Pit lane time, standing time, fuel and tyres are measured. The time lost is an estimate: in- and out-lap minus as many median clean laps (it includes the slower out-lap on cold tyres).")),
  ], { id: "stint-pits" });
}
