// pages/sessions.js — "My sessions" (V0.9.3): stints, consistency and the theoretical best.
//
// Everything here comes from the database only (per-lap figures stored at upload, see
// server/lap_insights.py) — no lap file is opened, so the page is fast even with hundreds of
// laps:
//   GET /telemetry/analysis/sessions          consecutive laps grouped into sessions
//   GET /telemetry/analysis/theoretical-best  best time over every 50 m stretch, summed
//
// Numbers and their meaning are spelled out on the page: "spread" is the standard deviation
// of the CLEAN laps (laps slower than 107 % of the session's median are left out as
// traffic/mistakes), "trend" is the slope of the clean lap times over the stint (+ = getting
// slower per lap, e.g. tyres or fuel), and the theoretical best is the sum of really driven
// stretches of your valid laps.

import { api } from "../api.js";
import { store } from "../state.js";
import { el, formatDate, formatLapTime } from "../format.js";
import { renderSignInPrompt } from "./auth.js";
import { t, tp } from "../i18n.js";

const fmtSigned = (v, digits = 3) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : "±"}${Math.abs(v).toFixed(digits)}`);
const dateOf = (unix) => (unix ? formatDate(new Date(unix * 1000).toISOString()) : "—");

// Pure: the bars of a session's lap-time chart (heights relative to the clean range).
export function lapBars(laps, consistency) {
  const valid = laps.filter((l) => l.is_valid && l.lap_time > 0);
  if (!valid.length) return laps.map((l) => ({ ...l, height: 0 }));
  const best = Math.min(...valid.map((l) => l.lap_time));
  const cap = consistency && consistency.median ? consistency.median * 1.07 : Math.max(...valid.map((l) => l.lap_time));
  const span = Math.max(cap - best, 0.5);
  return laps.map((l) => {
    if (!(l.lap_time > 0)) return { ...l, height: 0 };
    const over = l.lap_time > cap;
    const height = over ? 1 : 0.15 + 0.85 * Math.min((l.lap_time - best) / span, 1);
    return { ...l, height, best: l.is_valid && l.lap_time === best, outlier: over };
  });
}

function stat(label, value, title) {
  return el("div", { class: "sess__stat", title: title || null }, [el("div", { class: "sess__stat-label" }, label), el("div", { class: "sess__stat-value" }, value)]);
}

function lapChart(session) {
  const bars = lapBars(session.laps, session.consistency);
  return el("div", { class: "sess__bars", role: "list" }, bars.map((b) => {
    const classes = ["sess__bar"];
    if (!b.is_valid) classes.push("sess__bar--invalid");
    if (b.best) classes.push("sess__bar--best");
    if (b.outlier) classes.push("sess__bar--outlier");
    return el("a", {
      class: classes.join(" "), role: "listitem", href: `#/analysis?a=${b.id}`,
      title: `${t("Lap {0}", [b.lap_number])}: ${formatLapTime(b.lap_time)}${b.is_valid ? "" : ` (${t("invalid")})`}`,
      style: `height:${Math.round(8 + 72 * b.height)}px;`,
    });
  }));
}

const CONDITION_TEXT = () => ({
  dry: t("dry"), mixed: t("partly wet"), wet: t("wet"), unknown: t("conditions unknown"), all: t("all conditions mixed"),
});

// V0.9.3.1: which laps the figure is made of — never mixes track conditions silently
export function conditionsNote(c) {
  if (!c) return null;
  const names = CONDITION_TEXT();
  const lines = [t("From your {0} laps.", [names[c.selected] || c.selected])];
  const others = Object.entries(c.groups || {}).filter(([k]) => k !== c.selected);
  if (c.selected !== "all" && others.length) {
    lines.push(t("Not included: {0}.", [others.map(([k, g]) => `${tp("{0} lap", "{0} laps", g.laps)} ${names[k] || k}`).join(", ")]));
  }
  if (c.mixed_on_request) lines.push(t("Laps of different track conditions are mixed here on request."));
  if (c.selected === "unknown") lines.push(t("These laps were recorded before the client reported the track conditions."));
  if (c.varying) {
    const temp = c.track_temp_c ? t("track {0}–{1} °C", [c.track_temp_c[0], c.track_temp_c[1]]) : "";
    const grip = (c.grip_levels || []).length > 1 ? t("grip levels {0}", [c.grip_levels.join("/")]) : "";
    lines.push(t("Conditions varied between these laps ({0}).", [[grip, temp].filter(Boolean).join(", ")]));
  }
  return el("div", { class: "subtitle sess__conditions" }, lines.join(" "));
}

function idealBlock(trackName, carModel) {
  const slot = el("div", { class: "sess__ideal" });
  if (!carModel) {
    // V0.9.3.1: the theoretical best needs ONE car model — these laps have none
    return el("div", { class: "subtitle" }, t("No theoretical best: these laps have no car model (recorded by a very old client)."));
  }
  const button = el("button", { type: "button", class: "btn btn--ghost sess__ideal-btn" }, t("Theoretical best (calculated)"));
  button.addEventListener("click", async () => {
    button.disabled = true;
    slot.innerHTML = "";
    slot.appendChild(el("div", { class: "notice" }, t("Calculating …")));
    try {
      const res = await api.analysisTheoreticalBest({ trackName, carModel });
      slot.innerHTML = "";
      if (!res.found || !res.result) {
        slot.appendChild(el("div", { class: "notice" }, t("No valid lap on this layout with this car yet.")));
        const note = conditionsNote(res.conditions);
        if (note && Object.keys((res.conditions && res.conditions.groups) || {}).length) slot.appendChild(note);
        return;
      }
      const r = res.result;
      const parts = [];
      if (r.time) {
        parts.push(el("div", { class: "sess__ideal-main" }, [
          el("span", { class: "lap-time" }, formatLapTime(r.time)),
          el("span", { class: "analysis-diff--gain" }, ` ${fmtSigned(-r.gain)} s`),
          el("span", { class: "subtitle" }, ` ${t("vs. your best {0}", [formatLapTime(r.reference_lap_time)])}`),
        ]));
        parts.push(el("div", { class: "subtitle" }, t("Sum of your fastest time over each of the {0} stretches of {1} m, from {2} valid laps.", [r.segments, r.step, r.laps_used])));
        parts.push(el("div", { class: "subtitle" }, t("A calculated figure: stretches of different laps, never driven as one lap.")));
        const contributors = Object.entries(r.from_laps || {}).slice(0, 5);
        if (contributors.length) {
          parts.push(el("div", { class: "sess__contrib" }, [
            el("strong", {}, t("Best stretches come from: ")),
            ...contributors.flatMap(([lapId, count], i) => [
              i ? " · " : "",
              el("a", { href: `#/analysis?a=${r.reference_lap_id}&b=${lapId}` }, t("lap #{0}", [lapId])),
              ` (${tp("{0} stretch", "{0} stretches", count)})`,
            ]),
          ]));
        }
      }
      if (r.sector_ideal) {
        parts.push(el("div", { class: "subtitle" }, t("By sectors only (best S1 + S2 + S3): {0} ({1} s).", [formatLapTime(r.sector_ideal.time), fmtSigned(-r.sector_ideal.gain)])));
      }
      if (!r.time) parts.push(el("div", { class: "subtitle" }, t("The stretch-by-stretch best needs laps uploaded with server 0.9.3 or newer.")));
      const note = conditionsNote(res.conditions);
      if (note) parts.push(note);
      if (res.laps_without_profile) parts.push(el("div", { class: "subtitle" }, tp("{0} older lap is not included (no time profile).", "{0} older laps are not included (no time profile).", res.laps_without_profile)));
      parts.forEach((p) => slot.appendChild(p));
    } catch (err) {
      slot.innerHTML = "";
      slot.appendChild(el("div", { class: "notice notice--error" }, err.message));
    } finally {
      button.disabled = false;
    }
  });
  return el("div", {}, [button, slot]);
}

export function sessionCard(session) {
  const c = session.consistency || {};
  const trend = c.trend_per_lap;
  const stats = [
    stat(t("Laps"), `${session.laps.length}`),
    stat(t("Best"), c.best ? formatLapTime(c.best) : "—"),
    stat(t("Median"), c.median ? formatLapTime(c.median) : "—"),
    stat(t("Spread"), c.stdev !== undefined ? `± ${c.stdev.toFixed(3)} s` : "—", t("Standard deviation of the clean laps (without laps slower than 107 % of the median).")),
    stat(t("Within 0.5 s of best"), c.within_half_second !== undefined ? `${c.within_half_second} / ${c.clean_laps}` : "—"),
    stat(t("Trend"), trend !== undefined ? t("{0} s/lap", [fmtSigned(trend)]) : "—", t("Slope of the clean lap times over the session: + = getting slower per lap (tyres, fuel), − = getting faster.")),
    stat(t("Fuel / lap"), session.fuel_per_lap_l !== null ? `${session.fuel_per_lap_l.toFixed(2)} L` : "—"),
    stat(t("Energy / lap"), session.energy_per_lap_pct !== null ? `${session.energy_per_lap_pct.toFixed(2)} %` : "—"),
    stat(t("Tyre wear / lap"), session.wear_per_lap_pct !== null ? `${session.wear_per_lap_pct.toFixed(2)} %` : "—"),
  ];
  return el("div", { class: "card sess" }, [
    el("div", { class: "sess__head" }, [
      el("h2", {}, `${session.track_name} · ${session.car_model || "—"}`),
      el("span", { class: "subtitle" }, `${dateOf(session.started)} – ${dateOf(session.ended)}`),
    ]),
    el("div", { class: "sess__stats" }, stats),
    lapChart(session),
    el("div", { class: "subtitle sess__legend" }, t("Each bar is a lap (click to analyse it): taller = slower; grey = invalid; marked = best; full height = more than 107 % of the median.")),
    idealBlock(session.track_name, session.car_model),
  ]);
}

export async function renderSessions(container) {
  container.appendChild(el("h1", {}, t("My sessions")));
  container.appendChild(el("p", { class: "subtitle" }, t("Your stints with consistency, lap-time trend, fuel and tyre use — and your theoretical best lap.")));
  if (!store.isSignedIn()) {
    renderSignInPrompt(container, t("Sign in to see your sessions."));
    return;
  }
  const status = el("div", { class: "notice" }, t("Loading …"));
  container.appendChild(status);
  let data;
  try {
    data = await api.analysisSessions({ limit: 20 });
  } catch (err) {
    status.className = "notice notice--error";
    status.textContent = err.message;
    return;
  }
  status.remove();
  if (!data.sessions.length) {
    container.appendChild(el("div", { class: "empty-state" }, t("No laps uploaded yet — drive a few laps with the Garage16 client.")));
    return;
  }
  for (const session of data.sessions) container.appendChild(sessionCard(session));
  if (data.total > data.sessions.length) {
    container.appendChild(el("div", { class: "subtitle" }, t("Showing your {0} most recent sessions of {1}.", [data.sessions.length, data.total])));
  }
}
