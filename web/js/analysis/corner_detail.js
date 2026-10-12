// analysis/corner_detail.js — V0.9.4 windows of the LapTime Analyzer:
//   * "line"           the racing-line comparison (analysis/linemap.js)
//   * "corner_detail"  one corner in detail: where the time goes (braking / entry / apex /
//                      exit / straight, adding up exactly to the corner's delta), every measured
//                      value of both laps side by side, the lines' distance at the apex, and the
//                      same corner on the driver's earlier laps (GET /corner-history)
//   * "sectors"        the session's sector times, the fastest DRIVEN sector of the session and
//                      of all your laps here, and this lap's potential per sector
// Plus the shared corner selection (which corner the windows show) and the corner-history
// matching used by the coach.

import { el, formatLapTime } from "../format.js";
import { createLineMap } from "./linemap.js";
import { formatDelta, formatMetres, LAP_COLORS } from "./model.js";

import { t, tp } from "../i18n.js";

const noop = () => {};
const num = (v) => v !== null && v !== undefined && Number.isFinite(v);
const tone = (v) => (v > 0 ? "analysis-diff--loss" : v < 0 ? "analysis-diff--gain" : "");
export const HISTORY_MATCH_M = 40;   // a stored corner is "the same corner" within this distance of the apex

export const PHASE_LABELS = () => ({ braking: t("Braking"), entry: t("Turn-in"), apex: t("Apex"), exit: t("Exit"), straight: t("Straight after") });

// The corner the windows show — a tiny store the page creates once.
export function createSelection(initial = null) {
  let value = initial;
  const listeners = new Set();
  return {
    get: () => value,
    set(next) {
      if (next === value) return;
      value = next;
      listeners.forEach((fn) => fn(value));
    },
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}

// A lazily loaded value shared by several windows (one request, however many windows ask).
export function createLazy(loader) {
  let promise = null;
  return { get: () => (promise || (promise = loader().catch((err) => { promise = null; throw err; }))) };
}

// The same corner on earlier laps: stored per lap as [apex, brake, min, throttle, exit]
// (server/lap_insights.py). Matched by apex distance. -> [{lap, brake, min, throttle, exit}]
export function historyFor(corner, history) {
  if (!history || !Array.isArray(history.laps)) return [];
  const out = [];
  for (const lap of history.laps) {
    let best = null;
    for (const row of lap.corners || []) {
      if (!Array.isArray(row) || !num(row[0])) continue;
      const d = Math.abs(row[0] - corner.apex);
      if (d <= HISTORY_MATCH_M && (!best || d < best.d)) best = { d, row };
    }
    if (best) out.push({ lap, brake: best.row[1], min: best.row[2], throttle: best.row[3], exit: best.row[4] });
  }
  return out;
}

// Plain sentences comparing Lap 1 with the driver's earlier laps in this corner. Only counts —
// "in 4 of your last 5 laps you braked later than here" — never a cause.
export function historySentences(corner, rows) {
  const a = corner.a || {};
  const out = [];
  const count = (key, better) => {
    const usable = rows.filter((r) => num(r[key]) && num(a[key]));
    if (usable.length < 3) return null;
    return { n: usable.filter((r) => better(r[key], a[key])).length, of: usable.length };
  };
  const later = count("brake", (h, now) => h > now + 3);
  if (later && later.n) out.push(t("In {0} of your last {1} laps you braked later than here.", [later.n, later.of]));
  const faster = count("min", (h, now) => h > now + 2);
  if (faster && faster.n) out.push(t("In {0} of your last {1} laps your minimum speed was higher.", [faster.n, faster.of]));
  const exit = count("exit", (h, now) => h > now + 3);
  if (exit && exit.n) out.push(t("In {0} of your last {1} laps you left the corner faster.", [exit.n, exit.of]));
  return out;
}

// ------------------------------------------------------------------ line window

export function renderLine(widget, ctx) {
  const map = createLineMap(ctx.mapState || ctx.state, {
    fetchLine: ctx.fetchLine,
    selection: ctx.selection,
    viewState: ctx.state,
  });
  return { element: el("div", { class: "card" }, [el("h2", {}, t("Racing lines")), map.element]), destroy: () => map.destroy() };
}

// ------------------------------------------------------------------ corner detail

const ROWS = () => [
  // key, label, format, better ("lower" | "higher" | null), estimated
  ["brake", t("Braking point"), (v) => formatMetres(v), "higher", false],
  ["brake_speed", t("Speed at braking"), (v) => `${v.toFixed(1)} km/h`, null, false],
  ["brake_len", t("Braking distance"), (v) => `${v.toFixed(0)} m`, "lower", false],
  ["brake_max", t("Maximum brake"), (v) => `${v.toFixed(0)} %`, null, false],
  ["brake_pressure_max", t("Maximum brake pressure"), (v) => `${v.toFixed(0)}`, null, false],
  ["decel_peak", t("Peak deceleration"), (v) => `${v.toFixed(2)} G`, null, false],
  ["turn_in", t("Turn-in"), (v) => formatMetres(v), null, true],
  ["brake_at_turn_in", t("Brake at turn-in"), (v) => `${v.toFixed(0)} %`, null, true],
  ["trail_m", t("Trail braking"), (v) => `${v.toFixed(0)} m`, null, true],
  ["coast_m", t("Coasting (no pedal)"), (v) => `${v.toFixed(0)} m`, "lower", false],
  ["min", t("Minimum speed"), (v) => `${v.toFixed(1)} km/h`, "higher", false],
  ["apex", t("Apex (slowest point)"), (v) => formatMetres(v), null, false],
  ["throttle_on", t("Back on throttle"), (v) => formatMetres(v), "lower", false],
  ["throttle_50", t("50 % throttle"), (v) => formatMetres(v), "lower", false],
  ["throttle", t("Full throttle"), (v) => formatMetres(v), "lower", false],
  ["exit", t("Exit speed"), (v) => `${v.toFixed(1)} km/h`, "higher", false],
  ["accel_exit", t("Acceleration after the apex"), (v) => `${v.toFixed(2)} G`, "higher", false],
];

function diffCell(key, a, b, better) {
  if (!num(a) || !num(b)) return el("td", {}, "—");
  const d = a - b;
  if (Math.abs(d) < 1e-9) return el("td", {}, "±0");
  const metres = ["brake", "turn_in", "apex", "throttle_on", "throttle_50", "throttle", "brake_len", "trail_m", "coast_m"].includes(key);
  const text = metres ? `${d > 0 ? "+" : "−"}${Math.abs(d).toFixed(0)} m` : `${d > 0 ? "+" : "−"}${Math.abs(d).toFixed(key.startsWith("decel") || key === "accel_exit" ? 2 : 1)}`;
  let cls = "";
  if (better === "higher") cls = d > 0 ? "analysis-diff--gain" : "analysis-diff--loss";
  if (better === "lower") cls = d < 0 ? "analysis-diff--gain" : "analysis-diff--loss";
  return el("td", { class: cls }, text);
}

export function renderCornerDetail(widget, ctx) {
  const base = ctx.baseData || ctx.data;
  const title = el("h2", {}, t("Corner in detail"));
  const body = el("div", { class: "cdetail__body" });
  const element = el("div", { class: "card cdetail" }, [title, body]);
  const corners = base.corners || [];
  if (!base.laps.b) {
    body.appendChild(el("div", { class: "notice" }, t("Choose a second lap to compare against.")));
    return { element, destroy: noop };
  }
  if (!corners.length) {
    body.appendChild(el("div", { class: "notice" }, t("No corners found on the common stretch of these laps.")));
    return { element, destroy: noop };
  }
  const select = el("select", { class: "cdetail__select", "aria-label": t("Corner") }, corners.map((c) => el("option", { value: String(c.n) }, `${t("Corner {0}", [c.n])} · ${formatMetres(c.apex)} · ${formatDelta(c.delta)} s`)));
  select.addEventListener("change", () => ctx.selection && ctx.selection.set(Number(select.value)));
  title.appendChild(select);

  let alive = true;
  function render() {
    body.innerHTML = "";
    const n = (ctx.selection && ctx.selection.get()) || (corners.reduce((w, c) => (c.delta > (w ? w.delta : -Infinity) ? c : w), null) || corners[0]).n;
    const c = corners.find((x) => x.n === n) || corners[0];
    select.value = String(c.n);

    // where the time goes
    const phases = c.phases || [];
    if (phases.length) {
      const labels = PHASE_LABELS();
      const worst = phases.reduce((w, p) => (p.delta > (w ? w.delta : 0) ? p : w), null);
      body.appendChild(el("div", { class: "cdetail__phases" }, phases.map((p) => el("div", {
        class: `cdetail__phase ${tone(p.delta)}${p === worst ? " cdetail__phase--worst" : ""}`, "data-phase": p.phase,
        title: `${formatMetres(p.from)} – ${formatMetres(p.to)}`,
      }, [el("div", { class: "cdetail__phase-name" }, labels[p.phase] || p.phase), el("div", { class: "cdetail__phase-delta" }, `${formatDelta(p.delta)} s`)]))));
      body.appendChild(el("div", { class: "subtitle" }, t("Corner {0}: {1} s in total (Lap 1 − Lap 2), split at points common to both laps — the phases add up exactly.", [c.n, formatDelta(c.delta)])));
    }

    // every measured value
    const rows = ROWS().filter(([key]) => num(c.a[key]) || num(c.b[key]));
    body.appendChild(el("div", { class: "cdetail__wrap" }, el("table", { class: "cdetail__table" }, [
      el("thead", {}, el("tr", {}, [el("th", {}, ""), el("th", { style: `color:${LAP_COLORS.a};` }, t("Lap 1")), el("th", { style: `color:${LAP_COLORS.b};` }, t("Lap 2")), el("th", {}, t("Lap 1 vs 2"))])),
      el("tbody", {}, rows.map(([key, label, fmt, better, estimated]) => el("tr", { "data-metric": key }, [
        el("td", {}, [label, estimated ? el("span", { class: "stint-tag stint-tag--estimate", title: t("Estimated from the steering trace") }, t("estimate")) : null]),
        el("td", {}, num(c.a[key]) ? fmt(c.a[key]) : "—"),
        el("td", {}, num(c.b[key]) ? fmt(c.b[key]) : "—"),
        diffCell(key, c.a[key], c.b[key], better),
      ]))),
    ])));

    // the lines at the apex
    const map = base.map || {};
    if (map.offset_b) {
      const i = Math.round((c.apex - base.grid.start) / base.grid.step);
      const off = map.offset_b[i];
      if (num(off)) {
        body.appendChild(el("div", { class: "subtitle cdetail__line" }, Math.abs(off) < 0.1
          ? t("At the apex both laps are on the same line.")
          : off > 0 ? t("At the apex Lap 2 is {0} m further inside than Lap 1.", [off.toFixed(1)]) : t("At the apex Lap 2 is {0} m further outside than Lap 1.", [(-off).toFixed(1)])));
      }
    }

    // earlier laps
    const historySlot = el("div", { class: "cdetail__history" }, el("div", { class: "subtitle" }, t("Loading your earlier laps …")));
    body.appendChild(historySlot);
    if (ctx.history) {
      ctx.history.get().then((history) => {
        if (!alive) return;
        historySlot.innerHTML = "";
        const matched = historyFor(c, history);
        if (!matched.length) {
          historySlot.appendChild(el("div", { class: "subtitle" }, t("No earlier valid laps of this driver with this car here to compare with.")));
          return;
        }
        const sentences = historySentences(c, matched);
        historySlot.appendChild(el("strong", {}, tp("This corner on {0} earlier lap", "This corner on {0} earlier laps", matched.length)));
        historySlot.appendChild(el("table", { class: "cdetail__table cdetail__table--history" }, [
          el("thead", {}, el("tr", {}, [t("Lap"), t("Braking point"), t("Minimum speed"), t("Full throttle"), t("Exit speed")].map((h) => el("th", {}, h)))),
          el("tbody", {}, [
            el("tr", { class: "cdetail__now" }, [t("Lap 1 (this)"), num(c.a.brake) ? formatMetres(c.a.brake) : "—", num(c.a.min) ? `${c.a.min.toFixed(1)}` : "—", num(c.a.throttle) ? formatMetres(c.a.throttle) : "—", num(c.a.exit) ? `${c.a.exit.toFixed(1)}` : "—"].map((v) => el("td", {}, v))),
            ...matched.map((r) => el("tr", {}, [
              el("td", {}, el("a", { href: `#/analysis?a=${r.lap.id}`, title: formatLapTime(r.lap.lap_time) }, t("lap #{0}", [r.lap.lap_number]))),
              el("td", {}, num(r.brake) ? formatMetres(r.brake) : "—"), el("td", {}, num(r.min) ? r.min.toFixed(1) : "—"),
              el("td", {}, num(r.throttle) ? formatMetres(r.throttle) : "—"), el("td", {}, num(r.exit) ? r.exit.toFixed(1) : "—"),
            ])),
          ]),
        ]));
        for (const s of sentences) historySlot.appendChild(el("div", { class: "cdetail__history-line" }, s));
      }).catch(() => {
        if (!alive) return;
        historySlot.innerHTML = "";
        historySlot.appendChild(el("div", { class: "subtitle" }, t("Earlier laps could not be loaded.")));
      });
    } else {
      historySlot.remove();
    }
  }
  const off = ctx.selection ? ctx.selection.subscribe(render) : noop;
  render();
  return { element, destroy: () => { alive = false; off(); } };
}

// ------------------------------------------------------------------ sectors

export function renderSectors(widget, ctx) {
  const element = el("div", { class: "card lsectors" }, [el("h2", {}, t("Sectors of the session"))]);
  const body = el("div", {}, el("div", { class: "subtitle" }, t("Loading …")));
  element.appendChild(body);
  let alive = true;
  if (!ctx.lapSectors) return { element, destroy: noop };
  ctx.lapSectors.get().then((res) => {
    if (!alive) return;
    body.innerHTML = "";
    const best = res.session_best;
    const isBest = (i, lapId) => best && best[i] && best[i].lap_id === lapId;
    const rows = (res.laps || []).map((lap) => el("tr", { class: lap.id === res.lap_id ? "lsectors__this" : (lap.is_valid ? "" : "lsectors__invalid") }, [
      el("td", {}, el("a", { href: `#/analysis?a=${lap.id}` }, t("lap #{0}", [lap.lap_number]))),
      el("td", {}, formatLapTime(lap.lap_time)),
      ...[0, 1, 2].map((i) => el("td", { class: isBest(i, lap.id) ? "lsectors__best" : "" }, Array.isArray(lap.sector_times) && lap.sector_times[i] > 0 ? lap.sector_times[i].toFixed(3) : "—")),
    ]));
    body.appendChild(el("div", { class: "cdetail__wrap" }, el("table", { class: "cdetail__table" }, [
      el("thead", {}, el("tr", {}, [t("Lap"), t("Time"), "S1", "S2", "S3"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows),
    ])));
    const line = (label, ideal, potential) => el("div", { class: "lsectors__line" }, [
      el("strong", {}, label), ` ${ideal ? formatLapTime(ideal) : "—"}`,
      potential ? el("span", { class: "subtitle" }, ` · ${t("this lap per sector")}: ${potential.map((p, i) => `S${i + 1} ${formatDelta(p)}`).join(" · ")}`) : null,
    ]);
    body.appendChild(line(t("Best sectors of the session (calculated):"), res.session_ideal, res.potential_session));
    body.appendChild(line(t("Your best sectors here with this car (calculated):"), res.personal_ideal, res.potential_personal));
    body.appendChild(el("div", { class: "subtitle" }, t("The sum of best sectors is calculated from sectors of different laps — never driven as one lap. Green: the session's fastest sector.")));
  }).catch((err) => {
    if (!alive) return;
    body.innerHTML = "";
    body.appendChild(el("div", { class: "notice notice--error" }, err.message || t("Could not load the sectors.")));
  });
  return { element, destroy: () => { alive = false; } };
}
