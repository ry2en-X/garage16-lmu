// pages/stints.js — the Stint Analyzer: a dashboard of its own, separate from the
// LapTime Analyzer (#/analysis). One driving session at a time: pace and consistency, stints,
// tyres and degradation, fuel and energy, vehicle state, traffic, pit stops, a forecast, the
// strategy calculator, and comparisons (stints, team mates).
//
//   #/stints                      your sessions (or a team mate's: ?driver=ID)
//   #/stints?lap=ID               the session this lap belongs to
//   #/sessions                    the V0.9.3 address — opens the same page
//
// Data: server/api/v1/stints.py (from the database only — fast). The statistics run here
// (web/js/stints/stats.js) so every filter applies at once. Every estimate is labelled as one;
// a figure that needs more data than there is says so instead of guessing.

import { api } from "../core/api.js";
import { store } from "../core/state.js";
import { el } from "../core/format.js";
import { renderSignInPrompt } from "./auth.js";
import { sessionCard } from "./sessions.js";
import { t, tp } from "../core/i18n.js";
import { loadTrackNames, trackLabel } from "../core/tracks.js";
import * as S from "../stints/stats.js";
import { link } from "../core/link.js";
import { navigate, routeQuery } from "../core/router.js";
import { FLAG_HELP, FLAG_LABELS, createGroup, dash, dateOf, loadCharts, note } from "../stints/ui.js";

// ------------------------------------------------------------------ query

// the page's parameters (core/routes.js turns /stints/lap/123 and /stints/<track>/<day> into ?lap=)
export function parseStintQuery(input) {
  const p = input === undefined ? routeQuery()
    : typeof input === "string" ? new URLSearchParams(input.split("?")[1] || "") : input;   // a string: an old #/ address
  const num = (k) => (/^\d+$/.test(p.get(k) || "") ? Number(p.get(k)) : null);
  return { lap: num("lap"), driver: num("driver"), compareLap: num("compare") };
}

// ------------------------------------------------------------------ page

export async function renderStints(container) {
  loadTrackNames(); // Readable track names (tracks.js) — not awaited, never blocks the page
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
    select.addEventListener("change", () => { navigate(link.stints({ driver: select.value })); });
    container.appendChild(el("div", { class: "stint-toolbar" }, [el("label", {}, [t("Driver"), " ", select])]));
  }
  if (!data.own) container.appendChild(note(t("A team mate's sessions show their valid laps only.")));
  if (!data.sessions.length) {
    container.appendChild(el("div", { class: "empty-state" }, t("No laps uploaded yet — drive a few laps with the Garage16 client.")));
    return;
  }
  // A session's address is its track and day (/stints/fuji-speedway/2026-11-14), with -2,
  // -3 … for a second or third session on the same track that day (counted from the morning)
  const sessionLink = (session) => {
    const day = String(session.started || "").slice(0, 10);
    if (!session.track_name || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return link.stints({ lap: session.key_lap_id });
    const sameDay = data.sessions.filter((x) => x.track_name === session.track_name && String(x.started || "").slice(0, 10) === day).reverse();
    return link.stints({ track: session.track_name, date: day, n: sameDay.indexOf(session) + 1, driver: data.own ? null : data.driver_id });
  };
  for (const session of data.sessions) {
    const c = sessionCard(session);
    const open = el("a", { class: "btn", href: sessionLink(session) }, t("Open in the Stint Analyzer"));
    c.insertBefore(el("div", { class: "stint-open" }, open), c.children[1] || null);
    container.appendChild(c);
  }
  if (data.total > data.sessions.length) {
    container.appendChild(note(t("Showing your {0} most recent sessions of {1}.", [data.sessions.length, data.total])));
  }
}

// ------------------------------------------------------------------ one session

const DEFAULT_STATE = () => ({ exclude: [...S.NOT_CLEAN_DEFAULT], outlierPct: S.DEFAULT_OUTLIER_PCT, window: 5, stint: null,
  paceMode: "time", wheelValue: "temp" });

async function renderSession(container, lapId, compareLap) {
  const status = el("div", { class: "notice" }, t("Loading …"));
  container.appendChild(status);
  let data;
  try {
    const [session] = await Promise.all([api.stintSession(lapId), loadCharts()]);
    data = session;
  } catch (err) {
    status.className = "notice notice--error";
    status.textContent = err.message;
    return;
  }
  status.remove();
  const back = el("a", { class: "btn btn--ghost", href: data.driver.own ? "/stints" : link.stints({ driver: data.driver.id }) }, t("← All sessions"));
  container.appendChild(el("div", { class: "stint-head" }, [
    back,
    el("div", {}, [
      el("h2", { class: "stint-head__title" }, `${trackLabel(data.track_name)} · ${data.car_model || dash}`),
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
  // One cursor and one zoom window for every chart over "lap in session" (V1.0.3); it outlives a
  // re-render, so changing a filter keeps the zoomed laps.
  const group = createGroup();
  const body = el("div", { class: "stint-body" });
  const filters = el("div", { class: "stint-filters card" });
  container.appendChild(filters);
  container.appendChild(body);
  const rerender = () => {
    filters.innerHTML = "";
    filterBar(filters, data, state, rerender);
    body.innerHTML = "";
    const clean = S.cleanIds(data.laps, { exclude: state.exclude, outlierPct: state.outlierPct });
    const ctx = { data, state, clean, rerender, compareLap, group };
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
  box.appendChild(note(t("Charts: point at a lap to see every value of it in all charts, drag across a range to zoom (double-click: back), click a legend entry to hide it."), "stint-hint"));
  box.appendChild(el("div", { class: "stint-filters__row" }, [el("label", {}, [t("Rolling average over"), " ", win]), reset]));
}

// ------------------------------------------------------------------ sections

import { compareSection, driversSection, lapsSection, sectorsSection } from "../stints/section_compare.js";
import { fuelSection, pitSection, stateSection, trafficSection } from "../stints/section_fuel.js";
import { paceSection } from "../stints/section_pace.js";
import { forecastSection, strategySection } from "../stints/section_plan.js";
import { stintsSection, tyresSection, wheelsSection } from "../stints/section_tyres.js";

const SECTIONS = [paceSection, stintsSection, tyresSection, wheelsSection, fuelSection, stateSection, trafficSection, pitSection, forecastSection, strategySection, compareSection, sectorsSection, driversSection, lapsSection];

