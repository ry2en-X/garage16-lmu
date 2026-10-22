// analysis/events.js — what happened where on the lap (V0.9.3): the server's track-limits
// findings (stored at upload, server/track_limits.py) and the measured wheel events
// (lock-ups, wheelspin) from GET /telemetry/analysis/events.
//
// One small store per opened comparison loads them ONCE; the events window, the track
// map (markers) and the coaching window read it. Moving the cursor never asks the server.

import { el } from "../format.js";
import { formatMetres, indexForDistance, LAP_COLORS, WHEEL_LABEL } from "./model.js";

import { t } from "../i18n.js";
import { findingText, tierText } from "../findings.js";

export { findingText };

export const SEVERITY_ORDER = { invalid: 0, review: 1, warning: 2, info: 3 };

function wheelText(e) {
  const wheel = WHEEL_LABEL[e.wheel] || e.wheel;
  return e.type === "lockup"
    ? t("{0} locks under braking ({1} %, {2} s)", [wheel, e.peak, e.seconds])
    : t("{0} wheelspin (+{1} %, {2} s)", [wheel, e.peak, e.seconds]);
}

// Pure: every event of both laps as one list, ordered along the lap.
//   -> [{ side, source: "track"|"wheel", code, severity, at, until, text, wheel }]
export function collectEvents(response) {
  const out = [];
  if (!response) return out;
  for (const side of ["a", "b"]) {
    const tl = response.track_limits && response.track_limits[side];
    for (const f of (tl && tl.findings) || []) {
      out.push({ side, source: "track", code: f.code, severity: f.severity, tier: f.tier || null, tierText: tierText(f.tier), at: f.at ?? null, until: f.until ?? null, text: findingText(f), detail: f.detail || {} });
    }
    for (const e of (response.wheels && response.wheels[side]) || []) {
      out.push({ side, source: "wheel", code: e.type, severity: "warning", at: e.at, until: e.until, text: wheelText(e), wheel: e.wheel, peak: e.peak });
    }
  }
  out.sort((x, y) => (x.at ?? -1) - (y.at ?? -1) || SEVERITY_ORDER[x.severity] - SEVERITY_ORDER[y.severity]);
  return out;
}

export function verdictOf(response, side) {
  const tl = response && response.track_limits && response.track_limits[side];
  if (!tl) return null;
  return { status: tl.status, review: tl.review_status, signals: tl.signals || [] };
}

// load: () => Promise of the /events response (the page binds it to the two laps)
export function createEventsStore(load) {
  let data = null;
  let error = null;
  const listeners = new Set();
  const ready = Promise.resolve()
    .then(load)
    .then((response) => { data = response; })
    .catch((err) => { error = err; })
    .finally(() => listeners.forEach((fn) => fn()));
  return {
    ready,
    get: () => data,
    error: () => error,
    events: () => collectEvents(data),
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}

const STATUS_TEXT = () => ({
  valid: t("Track limits respected"),
  review: t("Under review"),
  invalid: t("Track limits: invalid"),
});

function statusBadge(verdict) {
  if (!verdict || !verdict.status) return el("span", { class: "tl-badge tl-badge--none" }, t("Not checked (lap from before V0.9.3)"));
  let text = STATUS_TEXT()[verdict.status] || verdict.status;
  if (verdict.review === "cleared") text = t("Reviewed: counts");
  if (verdict.review === "held") text = t("Under review — not counted yet");
  if (verdict.review === "quarantined") text = t("Set aside: integrity check failed");
  if (verdict.review === "invalidated") text = t("Reviewed: invalid");
  return el("span", { class: `tl-badge tl-badge--${verdict.status}`, "data-status": verdict.status }, text);
}

export function renderEvents(widget, ctx) {
  const store = ctx.events;
  const body = el("div", { class: "events__body" }, el("div", { class: "notice" }, t("Loading …")));
  const element = el("div", { class: "card events" }, [el("h2", {}, t("Track limits & events")), body]);
  const base = ctx.baseData || ctx.data;

  function render() {
    body.innerHTML = "";
    if (store.error()) {
      body.appendChild(el("div", { class: "notice notice--error" }, store.error().message || t("Could not load the events.")));
      return;
    }
    const response = store.get();
    if (!response) {
      body.appendChild(el("div", { class: "notice" }, t("Loading …")));
      return;
    }
    const sides = base.laps.b ? ["a", "b"] : ["a"];
    body.appendChild(el("div", { class: "events__verdicts" }, sides.map((side) => el("div", { class: "events__verdict" }, [
      el("span", { class: "events__dot", style: `background:${LAP_COLORS[side]};` }),
      el("strong", {}, side === "a" ? t("Lap 1") : t("Lap 2")),
      " ",
      statusBadge(verdictOf(response, side)),
    ]))));
    const events = collectEvents(response);
    if (!events.length) {
      body.appendChild(el("div", { class: "subtitle" }, t("Nothing noteworthy: no track-limits findings, no lock-ups, no wheelspin.")));
      return;
    }
    const rows = events.map((e) => {
      const row = el("tr", { class: `event-row event-row--${e.severity}`, "data-code": e.code, "data-side": e.side, tabindex: e.at !== null ? "0" : null }, [
        el("td", {}, el("span", { class: "events__dot", style: `background:${LAP_COLORS[e.side]};` })),
        el("td", {}, e.at !== null ? formatMetres(e.at) : "—"),
        el("td", {}, e.text),
        el("td", { class: "event-row__sev", title: e.tierText || null }, e.tierText ? `${severityLabel(e.severity)} · ${e.tierText}` : severityLabel(e.severity)),
      ]);
      if (e.at !== null) {
        const go = () => ctx.pinAt(indexForDistance(base.grid, e.at));
        row.addEventListener("click", go);
        row.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            go();
          }
        });
      }
      return row;
    });
    body.appendChild(el("div", { class: "events__wrap" }, el("table", { class: "events__table" }, el("tbody", {}, rows))));
    body.appendChild(el("div", { class: "subtitle events__caption" }, t("Click an event to move the cursor there. Red = the lap does not count, orange = an admin looks at it, yellow = measured from the telemetry.")));
  }

  const off = store.subscribe(render);
  render();
  return { element, destroy: off };
}

function severityLabel(severity) {
  return { invalid: t("invalid"), review: t("review"), warning: t("note"), info: t("info") }[severity] || severity;
}
