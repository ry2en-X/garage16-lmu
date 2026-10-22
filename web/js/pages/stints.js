// pages/stints.js — the Stint Analyzer (V0.9.4): a dashboard of its own, separate from the
// LapTime Analyzer (#/analysis). One driving session at a time: pace and consistency, stints,
// tyres and degradation, fuel and energy, vehicle state, traffic, pit stops, a forecast, the
// strategy calculator, and comparisons (stints, team mates).
//
//   #/stints                      your sessions (or a team mate's: ?driver=ID)
//   #/stints?lap=ID               the session this lap belongs to
//   #/sessions                    the V0.9.3 address — opens the same page
//
// Data: server/routers/stints.py (from the database only — fast). The statistics run here
// (web/js/stints/stats.js) so every filter applies at once. Every estimate is labelled as one;
// a figure that needs more data than there is says so instead of guessing.

import { api } from "../api.js";
import { store } from "../state.js";
import { el, formatDate, formatLapTime } from "../format.js";
import { renderSignInPrompt } from "./auth.js";
import { sessionCard } from "./sessions.js";
import { t, tp } from "../i18n.js";
import * as S from "../stints/stats.js";
import { chart as svgChart, legend } from "../stints/chart.js";

// The SVG charts scale with the page; on a phone they are drawn for the narrow width, so the
// labels keep a readable size instead of being shrunk to a third.
const chartWidth = () => (typeof window !== "undefined" && window.innerWidth && window.innerWidth < 700 ? Math.max(300, window.innerWidth - 60) : 720);
const chart = (series, options = {}) => svgChart(series, { width: chartWidth(), ...options });

const COLORS = ["#E0472B", "#4C9BE8", "#4CA771", "#D8A83E", "#B276E0", "#E07AB0", "#5FC9C2", "#C9C25F"];
const GREY = "#5A6068";
const fin = (v) => typeof v === "number" && Number.isFinite(v);
const dash = "—";
const f = (v, d = 2, unit = "") => (fin(v) ? `${v.toFixed(d)}${unit ? ` ${unit}` : ""}` : dash);
const signed = (v, d = 3, unit = "s") => (fin(v) ? `${v > 0 ? "+" : v < 0 ? "−" : "±"}${Math.abs(v).toFixed(d)}${unit ? ` ${unit}` : ""}` : dash);
const lapTime = (v) => (fin(v) ? formatLapTime(v) : dash);
const dateOf = (unix) => (unix ? formatDate(new Date(unix * 1000).toISOString()) : dash);
const duration = (s) => (fin(s) ? `${Math.floor(s / 3600) ? `${Math.floor(s / 3600)} h ` : ""}${Math.floor((s % 3600) / 60)} min` : dash);

export const FLAG_LABELS = () => ({
  invalid: t("invalid"), outlap: t("out-lap"), inlap: t("in-lap"), pit_stop: t("pit stop"), traffic: t("traffic"),
  blue_flag: t("blue flag"), race_start: t("race start"), wet: t("wet"), damage: t("damage"),
  gap_before: t("laps missing before"), outlier: t("outlier"),
});
const FLAG_HELP = () => ({
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
const REASON_LABELS = () => ({
  session_start: t("session start"), pit: t("pit stop"), pit_counter: t("pit stop (LMU counter)"),
  tyres: t("new tyres (measured)"), refuel: t("refuelled (measured)"), compound: t("compound changed"),
});

function card(title, children, extra = {}) {
  return el("section", { class: `card stint-card ${extra.class || ""}`, id: extra.id || null }, [
    el("h2", { class: "stint-card__title" }, title), ...[].concat(children).filter(Boolean)]);
}
function note(text, cls = "") { return el("p", { class: `subtitle stint-note ${cls}` }, text); }
function estimateTag() { return el("span", { class: "stint-tag stint-tag--estimate", title: t("Calculated from measured values; not measured itself.") }, t("estimate")); }
function measuredTag() { return el("span", { class: "stint-tag stint-tag--measured" }, t("measured")); }
function stat(label, value, title, tag) {
  return el("div", { class: "sess__stat", title: title || null }, [
    el("div", { class: "sess__stat-label" }, [label, tag ? " " : null, tag || null]),
    el("div", { class: "sess__stat-value" }, value),
  ]);
}
function table(head, rows, cls = "") {
  return el("div", { class: "stint-table-wrap" }, el("table", { class: `table stint-table ${cls}` }, [
    el("thead", {}, el("tr", {}, head.map((h) => el("th", { title: (h && h.title) || null }, (h && h.text) || h)))),
    el("tbody", {}, rows.map((r) => el("tr", { class: r.cls || null }, r.cells.map((c) => el("td", {}, c))))),
  ]));
}

// ------------------------------------------------------------------ query

export function parseStintQuery(hash = window.location.hash) {
  const [, query = ""] = hash.split("?");
  const p = new URLSearchParams(query);
  const num = (k) => (/^\d+$/.test(p.get(k) || "") ? Number(p.get(k)) : null);
  return { lap: num("lap"), driver: num("driver"), compareLap: num("compare") };
}

// ------------------------------------------------------------------ page

export async function renderStints(container) {
  container.classList.add("panel--wide");
  container.appendChild(el("h1", {}, t("Stint Analyzer")));
  container.appendChild(el("p", { class: "subtitle" }, t("Whole sessions: pace over the stint, tyres, fuel and energy, pit stops, traffic, forecasts and strategy. For a single lap use the LapTime Analyzer.")));
  if (!store.isSignedIn()) {
    renderSignInPrompt(container, t("Sign in to see your sessions."));
    return;
  }
  const q = parseStintQuery();
  if (q.lap) await renderSession(container, q.lap, q.compareLap);
  else await renderSessionList(container, q.driver);
}

async function renderSessionList(container, driverId) {
  const status = el("div", { class: "notice" }, t("Loading …"));
  container.appendChild(status);
  let data;
  try {
    data = await api.stintSessions({ driverId, limit: 20 });
  } catch (err) {
    status.className = "notice notice--error";
    status.textContent = err.message;
    return;
  }
  status.remove();
  if ((data.drivers || []).length > 1) {
    const select = el("select", { class: "input", "aria-label": t("Driver") }, data.drivers.map((d) => el("option", {
      value: d.id, ...(d.id === data.driver_id ? { selected: "selected" } : {}),
    }, d.own ? t("{0} (you)", [d.name]) : d.name)));
    select.addEventListener("change", () => { window.location.hash = `#/stints?driver=${select.value}`; });
    container.appendChild(el("div", { class: "stint-toolbar" }, [el("label", {}, [t("Driver"), " ", select])]));
  }
  if (!data.own) container.appendChild(note(t("A team mate's sessions show their valid laps only.")));
  if (!data.sessions.length) {
    container.appendChild(el("div", { class: "empty-state" }, t("No laps uploaded yet — drive a few laps with the Garage16 client.")));
    return;
  }
  for (const session of data.sessions) {
    const c = sessionCard(session);
    const open = el("a", { class: "btn", href: `#/stints?lap=${session.key_lap_id}` }, t("Open in the Stint Analyzer"));
    c.insertBefore(el("div", { class: "stint-open" }, open), c.children[1] || null);
    container.appendChild(c);
  }
  if (data.total > data.sessions.length) {
    container.appendChild(note(t("Showing your {0} most recent sessions of {1}.", [data.sessions.length, data.total])));
  }
}

// ------------------------------------------------------------------ one session

const DEFAULT_STATE = () => ({ exclude: [...S.NOT_CLEAN_DEFAULT], outlierPct: S.DEFAULT_OUTLIER_PCT, window: 5, stint: null });

async function renderSession(container, lapId, compareLap) {
  const status = el("div", { class: "notice" }, t("Loading …"));
  container.appendChild(status);
  let data;
  try {
    data = await api.stintSession(lapId);
  } catch (err) {
    status.className = "notice notice--error";
    status.textContent = err.message;
    return;
  }
  status.remove();
  const back = el("a", { class: "btn btn--ghost", href: data.driver.own ? "#/stints" : `#/stints?driver=${data.driver.id}` }, t("← All sessions"));
  container.appendChild(el("div", { class: "stint-head" }, [
    back,
    el("div", {}, [
      el("h2", { class: "stint-head__title" }, `${data.track_name} · ${data.car_model || dash}`),
      el("div", { class: "subtitle" }, `${data.driver.name || ""} · ${dateOf(data.started)} – ${dateOf(data.ended)} · ${tp("{0} lap", "{0} laps", data.laps.length)}`),
    ]),
  ]));
  if (data.partial) container.appendChild(note(t("Another driver's session: only their valid laps are shown, so stints and pit stops may be incomplete."), "notice"));
  const s = data.summary || {};
  const missing = [];
  if (!s.has_pit_data) missing.push(t("pit lane"));
  if (!s.has_context) missing.push(t("pit stop counter, compound, damage"));
  if (!s.has_traffic) missing.push(t("gaps to other cars"));
  if (missing.length) {
    container.appendChild(note(t("Not recorded for these laps (client older than 0.9.4): {0}. Stints are then split where new tyres or fuel were measured.", [missing.join(", ")])));
  }

  const state = DEFAULT_STATE();
  const body = el("div", { class: "stint-body" });
  const filters = el("div", { class: "stint-filters card" });
  container.appendChild(filters);
  container.appendChild(body);
  const rerender = () => {
    filters.innerHTML = "";
    filterBar(filters, data, state, rerender);
    body.innerHTML = "";
    const clean = S.cleanIds(data.laps, { exclude: state.exclude, outlierPct: state.outlierPct });
    const ctx = { data, state, clean, rerender, compareLap };
    for (const section of SECTIONS) {
      try {
        const node = section(ctx);
        if (node) body.appendChild(node);
      } catch (err) {
        body.appendChild(el("div", { class: "notice notice--error" }, `${t("This section could not be shown")}: ${err.message}`));
      }
    }
  };
  rerender();
}

function filterBar(box, data, state, rerender) {
  const labels = FLAG_LABELS();
  const help = FLAG_HELP();
  const present = new Set(data.laps.flatMap((l) => l.flags));
  box.appendChild(el("strong", {}, t("Clean laps — leave out:")));
  const flags = el("div", { class: "stint-filters__flags" });
  for (const flag of S.SWITCHABLE_FLAGS) {
    const count = data.laps.filter((l) => l.flags.includes(flag)).length;
    const input = el("input", { type: "checkbox", ...(state.exclude.includes(flag) ? { checked: "checked" } : {}) });
    input.addEventListener("change", () => {
      state.exclude = input.checked ? [...state.exclude, flag] : state.exclude.filter((x) => x !== flag);
      rerender();
    });
    flags.appendChild(el("label", { class: `stint-filter${present.has(flag) ? "" : " stint-filter--none"}`, title: help[flag] || null }, [input, ` ${labels[flag]} (${count})`]));
  }
  const outlier = el("input", { type: "checkbox", ...(state.exclude.includes("outlier") ? { checked: "checked" } : {}) });
  outlier.addEventListener("change", () => {
    state.exclude = outlier.checked ? [...state.exclude, "outlier"] : state.exclude.filter((x) => x !== "outlier");
    rerender();
  });
  const pct = el("input", { type: "number", class: "input stint-filter__pct", min: "101", max: "130", step: "1", value: String(state.outlierPct), "aria-label": t("Outlier threshold in percent") });
  pct.addEventListener("change", () => {
    const v = Number(pct.value);
    if (v >= 101 && v <= 130) { state.outlierPct = v; rerender(); }
  });
  flags.appendChild(el("label", { class: "stint-filter", title: help.outlier }, [outlier, ` ${t("slower than")} `, pct, " % ", t("of the median")]));
  box.appendChild(flags);
  const win = el("select", { class: "input", "aria-label": t("Rolling average") }, [3, 5, 10].map((n) => el("option", { value: n, ...(n === state.window ? { selected: "selected" } : {}) }, tp("{0} lap", "{0} laps", n))));
  win.addEventListener("change", () => { state.window = Number(win.value); rerender(); });
  const reset = el("button", { type: "button", class: "btn btn--ghost" }, t("Reset filters"));
  reset.addEventListener("click", () => { Object.assign(state, DEFAULT_STATE()); rerender(); });
  box.appendChild(el("div", { class: "stint-filters__row" }, [el("label", {}, [t("Rolling average over"), " ", win]), reset]));
}

// ------------------------------------------------------------------ sections

function stintColor(n) { return COLORS[(n - 1) % COLORS.length]; }

// V0.9.7: " (0.021 … 0.068)" — the 95 % range of a slope
function ciText(ci, digits = 3) {
  return ci && fin(ci[0]) && fin(ci[1]) ? ` (${ci[0].toFixed(digits)} … ${ci[1].toFixed(digits)})` : "";
}

function paceSection({ data, state, clean }) {
  const laps = data.laps;
  const p = S.paceSummary(laps, clean, { window: state.window });
  const labels = FLAG_LABELS();
  const points = laps.map((l, i) => [i + 1, l.lap_time, {
    color: clean.has(l.id) ? stintColor(l.stint) : GREY, faded: !clean.has(l.id),
    title: `${t("Lap {0}", [l.lap_number])}: ${lapTime(l.lap_time)}${l.flags.length ? ` (${l.flags.map((x) => labels[x] || x).join(", ")})` : ""}`,
    id: l.id,
  }]);
  // rolling average over the last N CLEAN laps, drawn through the clean laps only (a filtered
  // lap neither breaks the line nor enters the average)
  const cleanIdx = laps.map((l, i) => (clean.has(l.id) ? i : -1)).filter((i) => i >= 0);
  const avgClean = S.rolling(cleanIdx.map((i) => laps[i].lap_time), state.window);
  const med = p ? p.median : null;
  const capped = p ? Math.min(Math.max(...laps.filter((l) => clean.has(l.id)).map((l) => l.lap_time)) + 1.5, (med || 0) * 1.08) : null;
  const svgChart = chart([
    { kind: "dots", points, onClick: (meta) => { if (meta) window.location.hash = `#/analysis?a=${meta.id}`; } },
    { kind: "line", color: "#ECEDEE", width: 1.5, points: cleanIdx.map((i, k) => [i + 1, avgClean[k]]), label: t("Rolling average") },
  ], {
    yFormat: (v) => formatLapTime(v), xLabel: t("lap in session"), yLabel: t("lap time"),
    yMin: p ? p.best - 0.5 : undefined, yMax: fin(capped) ? capped : undefined,
    markers: data.stints.slice(1).map((s) => ({ x: laps.findIndex((l) => l.id === s.lap_ids[0]) + 1 - 0.5, label: t("Stint {0}", [s.number]) })),
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
  const parts = [svgChart, legend([
    ...data.stints.map((st) => ({ color: stintColor(st.number), label: t("Stint {0}", [st.number]), dot: true })),
    { color: GREY, label: t("not clean (filtered)"), dot: true },
    { color: "#ECEDEE", label: t("rolling average ({0})", [state.window]) },
  ])];
  if (p) {
    parts.push(el("div", { class: "sess__stats" }, stats));
    if (p.thirds) parts.push(note(t("Median by thirds of the session: {0} → {1} → {2}.", p.thirds.map(lapTime))));
  } else parts.push(note(t("No clean lap with the current filter.")));
  parts.push(note(t("Click a point to open that lap in the LapTime Analyzer.")));
  return card(t("Pace and consistency"), parts, { id: "stint-pace" });
}

function stintsSection({ data, clean }) {
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

function tyresSection({ data, clean }) {
  const laps = data.laps;
  const series = data.stints.map((st) => ({
    kind: "dots", color: stintColor(st.number),
    points: laps.filter((l) => l.stint === st.number && clean.has(l.id)).map((l) => [l.tyre_age, l.lap_time, { title: `${t("Lap {0}", [l.lap_number])}: ${lapTime(l.lap_time)} · ${t("tyre age {0}", [l.tyre_age])}` }]),
  }));
  const lines = data.stints.map((st) => {
    const deg = S.degradation(laps, clean, st.number);
    if (!deg.ok) return null;
    const ages = laps.filter((l) => l.stint === st.number).map((l) => l.tyre_age);
    const lo = Math.min(...ages);
    const hi = Math.max(...ages);
    return { kind: "line", color: stintColor(st.number), dashed: true, width: 1.5, points: [[lo, deg.intercept + deg.slope * lo], [hi, deg.intercept + deg.slope * hi]] };
  }).filter(Boolean);
  const parts = [chart([...series, ...lines], { yFormat: (v) => formatLapTime(v), xLabel: t("tyre age (laps)"), yLabel: t("lap time"), title: t("Lap time over tyre age") })];
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

function fuelSection({ data, clean }) {
  const laps = data.laps;
  const s = data.summary;
  const perLap = S.median(laps.filter((l) => clean.has(l.id)).map((l) => l.fuel_used_l));
  const energyPerLap = S.median(laps.filter((l) => clean.has(l.id)).map((l) => l.energy_used_pct));
  const medianLap = S.median(laps.filter((l) => clean.has(l.id)).map((l) => l.lap_time));
  const margin = { value: 1 };
  const out = el("div");
  const marginInput = el("input", { type: "number", class: "input stint-input--short", min: "0", max: "10", step: "0.5", value: "1", "aria-label": t("Safety margin in laps") });
  const draw = () => {
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
    const fuelSeries = laps.map((l, i) => [i + 1, l.fuel_used_l, { color: clean.has(l.id) ? stintColor(l.stint) : GREY, faded: !clean.has(l.id), title: `${t("Lap {0}", [l.lap_number])}: ${f(l.fuel_used_l, 2, "L")}` }]);
    if (fuelSeries.some((p) => fin(p[1]))) out.appendChild(chart([{ kind: "dots", points: fuelSeries }], { height: 160, xLabel: t("lap in session"), yLabel: t("fuel / lap (L)"), title: t("Fuel per lap") }));
  };
  marginInput.addEventListener("change", () => { const v = Number(marginInput.value); if (v >= 0 && v <= 10) { margin.value = v; draw(); } });
  draw();
  return card(t("Fuel and energy"), [el("label", { class: "stint-toolbar" }, [t("Safety margin"), " ", marginInput, " ", t("laps")]), out], { id: "stint-fuel" });
}

function stateSection({ data, clean }) {
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
      { kind: "line", color: "#E0472B", points: temps, label: t("Tyre temperature (avg)") },
    ], { height: 150, xLabel: t("lap in session"), yLabel: "°C", title: t("Tyre temperature per lap") }));
  }
  if (damaged.length) {
    parts.push(table([t("Lap"), t("Damage (0–2)"), t("Overheating"), t("Part detached")], damaged.map((l) => ({ cells: [String(l.lap_number), String(l.damage.dent_max), l.damage.overheating ? t("yes") : t("no"), l.damage.detached ? t("yes") : t("no")] }))));
  } else if (data.summary.has_context) {
    parts.push(note(t("No damage reported by LMU in this session.")));
  }
  return card(t("Vehicle state"), parts, { id: "stint-state" });
}

function trafficSection({ data, clean }) {
  const tr = S.trafficSummary(data.laps, clean);
  if (!tr.measured) return card(t("Traffic"), note(t("Not recorded for these laps: the gaps to other cars come with client 0.9.4.")), { id: "stint-traffic" });
  return card(t("Traffic"), [
    el("div", { class: "sess__stats" }, [
      stat(t("Laps in traffic"), String(tr.traffic_laps), t("At least 10 % of the lap less than 1 s behind another car."), measuredTag()),
      stat(t("Their median vs clean"), signed(tr.difference, 2), null),
      stat(t("Blue flag laps"), String(tr.blue_flag_laps), null, measuredTag()),
      stat(t("Places gained / lost"), `+${tr.places_gained} / −${tr.places_lost}`, null, measuredTag()),
    ]),
    note(t("The difference is measured; that the traffic caused all of it is not proven.")),
  ], { id: "stint-traffic" });
}

function pitSection({ data }) {
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

function forecastSection({ data, clean }) {
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
    { kind: "band", color: "#D8A83E", band: [[maxAge, ahead[0].lap_time, ahead[0].lap_time], ...ahead.map((p) => [p.tyre_age, p.low, p.high])], label: t("forecast range") },
    { kind: "dots", points: actual },
    { kind: "line", color: "#D8A83E", dashed: true, points: [[maxAge, ahead[0].lap_time - fc.slope], ...ahead.map((p) => [p.tyre_age, p.lap_time])] },
  ], { yFormat: (v) => formatLapTime(v), xLabel: t("tyre age (laps)"), yLabel: t("lap time"), title: t("Forecast of the last stint") });
  return card(t("Forecast"), [
    el("p", {}, [estimateTag(), " ", t("From the {0} clean laps of stint {1}, if it goes on like this:", [fc.n, last.number])]),
    table([t("Laps ahead"), t("Tyre age"), t("Lap time"), t("Range")], ahead.map((p) => ({ cells: [`+${p.laps_ahead}`, String(p.tyre_age), lapTime(p.lap_time), `${lapTime(p.low)} – ${lapTime(p.high)}`] }))),
    svgChart,
    note(t("A straight line through the measured laps (Theil–Sen). It does not know about a tyre falling off a cliff, rain or traffic — the further ahead, the less reliable.")),
  ], { id: "stint-forecast" });
}

function strategySection({ data, clean }) {
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

function compareSection({ data, clean }) {
  if (data.stints.length < 2) return null;
  const pick = (idx) => {
    const sel = el("select", { class: "input" }, data.stints.map((s) => el("option", { value: s.number, ...(s.number === idx ? { selected: "selected" } : {}) }, t("Stint {0}", [s.number]))));
    return sel;
  };
  const a = pick(1);
  const b = pick(data.stints.length);
  const out = el("div");
  const draw = () => {
    out.innerHTML = "";
    const na = Number(a.value);
    const nb = Number(b.value);
    const series = [na, nb].map((n, k) => ({
      kind: "line", color: k ? "#4C9BE8" : "#E0472B", label: t("Stint {0}", [n]),
      points: data.laps.filter((l) => l.stint === n).map((l, i) => [i + 1, clean.has(l.id) ? l.lap_time : null]),
    }));
    out.appendChild(chart(series, { yFormat: (v) => formatLapTime(v), xLabel: t("lap in stint"), yLabel: t("lap time"), title: t("Stints compared") }));
    out.appendChild(legend(series.map((s) => ({ color: s.color, label: s.label }))));
    const rowFor = (label, fn) => ({ cells: [label, ...[na, nb].map((n) => fn(n))] });
    const of = (n) => data.laps.filter((l) => l.stint === n && clean.has(l.id));
    const stint = (n) => data.stints.find((s) => s.number === n);
    out.appendChild(table(["", t("Stint {0}", [na]), t("Stint {0}", [nb])], [
      rowFor(t("Compound"), (n) => stint(n).compound || dash),
      rowFor(t("Clean laps"), (n) => String(of(n).length)),
      rowFor(t("Best"), (n) => lapTime(of(n).length ? Math.min(...of(n).map((l) => l.lap_time)) : null)),
      rowFor(t("Median"), (n) => lapTime(S.median(of(n).map((l) => l.lap_time)))),
      rowFor(t("Spread"), (n) => f(S.stdev(of(n).map((l) => l.lap_time)), 3, "s")),
      rowFor(t("Change / lap"), (n) => { const d = S.degradation(data.laps, clean, n); return d.ok ? signed(d.slope, 3) : dash; }),
      rowFor(t("Fuel / lap"), (n) => f(S.median(of(n).map((l) => l.fuel_used_l)), 2, "L")),
      rowFor(t("Tyre wear / lap"), (n) => f(S.median(of(n).map((l) => l.wear_used_avg)), 3, "%")),
      rowFor(t("Tyre temperature (avg)"), (n) => f(S.median(of(n).map((l) => l.tyre_temp_avg)), 1, "°C")),
    ]));
  };
  a.addEventListener("change", draw);
  b.addEventListener("change", draw);
  draw();
  return card(t("Compare stints"), [el("div", { class: "stint-toolbar" }, [a, " ", t("vs"), " ", b]), out], { id: "stint-compare" });
}

function driversSection({ data }) {
  const box = el("div", {}, note(t("Loading …")));
  api.stintDrivers({ trackName: data.track_name, carModel: data.car_model }).then((res) => {
    box.innerHTML = "";
    if (!res.drivers.length) { box.appendChild(note(t("No valid laps on this track yet."))); return; }
    if (res.drivers.length < 2) box.appendChild(note(t("Join a team to compare yourself with your team mates here.")));
    box.appendChild(table([t("Driver"), t("Valid laps"), t("Best"), t("Median (clean)"), t("Spread"), t("Within 0.5 s of best"), t("Fuel / lap"), t("Energy / lap"), t("Tyre wear / lap")],
      res.drivers.map((d) => {
        const c = d.consistency || {};
        return { cls: d.own ? "stint-row--own" : null, cells: [d.own ? t("{0} (you)", [d.name]) : d.name, String(c.laps || 0), lapTime(c.best), lapTime(c.median),
          fin(c.stdev) ? `± ${c.stdev.toFixed(3)} s` : dash, fin(c.within_half_second) ? `${c.within_half_second} / ${c.clean_laps}` : dash,
          f(d.fuel_per_lap_l, 2, "L"), f(d.energy_per_lap_pct, 2, "%"), f(d.wear_per_lap_pct, 3, "%")] };
      })));
    box.appendChild(note(t("All valid laps on this track{0}, every session together; laps slower than 107 % of a driver's median are not in the median and spread.", [data.car_model ? ` ${t("with this car")}` : ""])));
  }).catch((err) => { box.innerHTML = ""; box.appendChild(el("div", { class: "notice notice--error" }, err.message)); });
  return card(t("You and your team mates"), box, { id: "stint-drivers" });
}

function lapsSection({ data, clean }) {
  const labels = FLAG_LABELS();
  return card(t("All laps"), [table([t("Lap"), t("Stint"), t("Time"), "S1", "S2", "S3", t("Tyre age"), t("Fuel used"), t("Fuel left"), t("Tread left"), t("Tyre temp."), t("Position"), t("Notes")],
    data.laps.map((l) => ({ cls: clean.has(l.id) ? null : "stint-row--filtered", cells: [
      el("a", { href: `#/analysis?a=${l.id}`, title: t("Open in the LapTime Analyzer") }, String(l.lap_number)),
      String(l.stint || dash), lapTime(l.lap_time),
      ...(l.sector_times || [null, null, null]).map((v) => f(v, 3)),
      String(l.tyre_age || dash), f(l.fuel_used_l, 2), f(l.fuel_end_l, 1), f(l.wear_left_avg, 1), f(l.tyre_temp_avg, 0),
      l.place ? String(l.place) : dash,
      el("span", { title: (l.invalid_reason || []).join(" · ") || null }, [
        l.flags.map((x) => labels[x] || x).join(", "),
        l.invalid_reason && l.invalid_reason.length ? el("div", { class: "stint-reason" }, l.invalid_reason.join(" · ")) : null,
      ]),
    ] }))), note(t("Greyed: not counted as clean with the current filter. Under an invalid lap: why the server did not count it."))], { id: "stint-laps" });
}

const SECTIONS = [paceSection, stintsSection, tyresSection, fuelSection, stateSection, trafficSection, pitSection, forecastSection, strategySection, compareSection, driversSection, lapsSection];
