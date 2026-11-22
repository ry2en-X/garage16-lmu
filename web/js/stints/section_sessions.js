// stints/section_sessions.js — this session against another one (V1.0.3): one of your other
// sessions on the same track and car, or a team mate's (a driver change in an endurance race, the
// same race of a team mate). Both sessions are filtered with the same rules (the filter bar), the
// lap times are drawn over "lap in stint" per stint pair or over the whole session, and the
// figures that matter for a race are set side by side.
//
// Opened with /stints?lap=<id>&compare=<lap id of the other session> the other session is chosen
// at once, so a comparison can be shared as a link.

import { api } from "../core/api.js";
import { el, formatLapTime } from "../core/format.js";
import { t, tp } from "../core/i18n.js";
import * as S from "./stats.js";
import { card, chart, dash, dateOf, f, fin, lapTime, note, signed, table } from "./ui.js";

const A_COLOR = "#E0472B";
const B_COLOR = "#4C9BE8";

// The figures of one session under the current filter.
export function sessionFigures(data, state) {
  const clean = S.cleanIds(data.laps, { exclude: state.exclude, outlierPct: state.outlierPct });
  const laps = data.laps.filter((l) => clean.has(l.id));
  const times = laps.map((l) => l.lap_time);
  const p = S.paceSummary(data.laps, clean, { window: state.window });
  const degs = data.stints.map((st) => S.degradation(data.laps, clean, st.number)).filter((d) => d.ok);
  return {
    clean,
    laps: data.laps.length,
    cleanLaps: laps.length,
    best: times.length ? Math.min(...times) : null,
    median: S.median(times),
    spread: p && p.consistency ? p.consistency.sigma : null,
    bestWindow: p && p.best_window ? p.best_window.average : null,
    degradation: degs.length ? S.median(degs.map((d) => d.slope)) : null,
    fuel: S.median(laps.map((l) => l.fuel_used_l)),
    energy: S.median(laps.map((l) => l.energy_used_pct)),
    wear: S.median(laps.map((l) => l.wear_used_avg)),
    tyreTemp: S.median(laps.map((l) => l.tyre_temp_avg)),
    stints: data.stints.length,
    pitStops: data.pit_stops.length,
  };
}

// Lap times of a session, x = lap in the session (all laps; filtered ones as null so the line breaks).
const sessionLine = (data, clean) => data.laps.map((l, i) => [i + 1, clean.has(l.id) ? l.lap_time : null]);

export function sessionsSection({ data, state, compareLap }) {
  const out = el("div");
  const pickDriver = el("select", { class: "input", "aria-label": t("Driver") });
  const pickSession = el("select", { class: "input", "aria-label": t("Session") });
  const own = { other: null };   // the other session, once loaded

  const draw = () => {
    out.innerHTML = "";
    const other = own.other;
    if (!other) {
      out.appendChild(note(t("Choose a session to compare with: your own on this track and car, or a team mate's.")));
      return;
    }
    const fa = sessionFigures(data, state);
    const fb = sessionFigures(other, state);
    const nameA = t("This session");
    const nameB = `${other.driver.own ? t("You") : other.driver.name} · ${dateOf(other.started)}`;
    const series = [
      { kind: "line", color: A_COLOR, label: nameA, format: (v) => formatLapTime(v), points: sessionLine(data, fa.clean) },
      { kind: "line", color: B_COLOR, label: nameB, format: (v) => formatLapTime(v), points: sessionLine(other, fb.clean) },
    ];
    const medians = [fa.median, fb.median];
    out.appendChild(chart(series, {
      legend: true, integerX: true, xLabel: t("lap in session"), yLabel: t("lap time"), title: t("Sessions compared"),
      yFormat: (v) => formatLapTime(v), tooltipTitle: (x) => t("Lap {0} of the session", [x]),
      hlines: medians.map((m, k) => (fin(m) ? { y: m, color: k ? B_COLOR : A_COLOR, label: t("median {0}", [lapTime(m)]) } : null)).filter(Boolean),
    }));
    const row = (label, a, b, fmt, lowerIsBetter = true) => {
      const diff = fin(a) && fin(b) ? b - a : null;
      const better = fin(diff) && diff !== 0 ? ((diff < 0) === lowerIsBetter ? "b" : "a") : null;
      return { cells: [label, el("span", { class: better === "a" ? "cmp-better" : null }, fmt(a)), el("span", { class: better === "b" ? "cmp-better" : null }, fmt(b)),
        fin(diff) ? signed(diff, 3, "") : dash] };
    };
    out.appendChild(table(["", nameA, nameB, t("Difference")], [
      { cells: [t("Laps (clean)"), `${fa.laps} (${fa.cleanLaps})`, `${fb.laps} (${fb.cleanLaps})`, dash] },
      row(t("Best (clean)"), fa.best, fb.best, lapTime),
      row(t("Median"), fa.median, fb.median, lapTime),
      row(t("Best {0} in a row", [state.window]), fa.bestWindow, fb.bestWindow, lapTime),
      row(t("Consistency (trend removed)"), fa.spread, fb.spread, (v) => (fin(v) ? `± ${v.toFixed(3)} s` : dash)),
      row(t("Change / lap"), fa.degradation, fb.degradation, (v) => signed(v, 3)),
      row(t("Fuel / lap"), fa.fuel, fb.fuel, (v) => f(v, 2, "L")),
      row(t("Energy / lap"), fa.energy, fb.energy, (v) => f(v, 2, "%")),
      row(t("Tyre wear / lap"), fa.wear, fb.wear, (v) => f(v, 3, "%")),
      row(t("Tyre temperature (avg)"), fa.tyreTemp, fb.tyreTemp, (v) => f(v, 1, "°C"), false),
      { cells: [t("Stints / pit stops"), `${fa.stints} / ${fa.pitStops}`, `${fb.stints} / ${fb.pitStops}`, dash] },
    ]));
    out.appendChild(note(t("Both sessions with the same filter. Difference = the other session minus this one; the better value is marked. Tyre temperature is not better or worse by itself — it is only set side by side.")));
  };

  const loadSessions = async () => {
    pickSession.innerHTML = "";
    pickSession.appendChild(el("option", { value: "" }, t("Loading …")));
    try {
      const res = await api.stintSessions({ driverId: Number(pickDriver.value) || undefined, trackName: data.track_name, carModel: data.car_model || undefined, limit: 30 });
      pickSession.innerHTML = "";
      pickSession.appendChild(el("option", { value: "" }, t("— choose a session —")));
      const thisIds = new Set(data.laps.map((l) => l.id));
      for (const s of res.sessions) {
        if (s.laps.some((l) => thisIds.has(l.id))) continue;   // this session itself
        pickSession.appendChild(el("option", { value: s.key_lap_id }, `${dateOf(s.started)} · ${tp("{0} lap", "{0} laps", s.laps.length)}${s.consistency && fin(s.consistency.best) ? ` · ${lapTime(s.consistency.best)}` : ""}`));
      }
      if (pickSession.options.length === 1) pickSession.options[0].textContent = t("No other session on this track and car.");
      return res;
    } catch (err) {
      pickSession.innerHTML = "";
      pickSession.appendChild(el("option", { value: "" }, err.message));
      return null;
    }
  };
  const loadOther = async (lapId) => {
    own.other = null;
    if (!lapId) { draw(); return; }
    out.innerHTML = "";
    out.appendChild(note(t("Loading …")));
    try {
      own.other = await api.stintSession(lapId);
    } catch (err) {
      out.innerHTML = "";
      out.appendChild(el("div", { class: "notice notice--error" }, err.message));
      return;
    }
    draw();
  };
  pickDriver.addEventListener("change", () => { own.other = null; draw(); loadSessions(); });
  pickSession.addEventListener("change", () => loadOther(Number(pickSession.value) || null));

  // the drivers you may look at come with the session list
  api.stintSessions({ trackName: data.track_name, carModel: data.car_model || undefined, limit: 1 }).then((res) => {
    for (const d of res.drivers || []) pickDriver.appendChild(el("option", { value: d.id, ...(d.own ? { selected: "selected" } : {}) }, d.own ? t("{0} (you)", [d.name]) : d.name));
    return loadSessions();
  }).then(() => {
    if (compareLap) {
      pickSession.value = String(compareLap);
      return loadOther(compareLap);
    }
    draw();
    return null;
  }).catch((err) => { out.innerHTML = ""; out.appendChild(el("div", { class: "notice notice--error" }, err.message)); });

  return card(t("Compare with another session"), [el("div", { class: "stint-toolbar" }, [pickDriver, " ", pickSession]), out], { id: "stint-sessions" });
}
