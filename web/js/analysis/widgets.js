// analysis/widgets.js — the renderers of the analysis windows (V0.9.1). Each one
// takes (widget, ctx) and returns { element, destroy() }; they only READ the shared
// view state (cursor, zoom) and the already-loaded data — none of them calls the
// server while the cursor moves.
//
// ctx: {
//   state            scoped view state (see page: subscriptions are released on re-render)
//   data             the data the charts draw: the /compare response of the whole lap, or —
//                    while zoomed in far — of just the visible stretch at a finer step
//   baseData         always the whole-lap overview. Everything that is about the WHOLE lap
//                    (summary, sector table, biggest gain/loss) reads this, never `data`
//   mapState         view of `state` whose data is baseData (cursor translated by distance)
//   catalog          GET /telemetry/analysis/channels
//   ensureChannels   (keys) => Promise — loads missing channels, merges them into data
//   onTrackmapMode   (mode) => void   — remembered in the layout
//   pinAt            (baseIndex) => void — moves + pins the shared cursor
// }

import { el, formatLapTime } from "../format.js";
import { createChart } from "./charts.js";
import { createPhysicsView } from "./physics.js";
import { createTrackMap } from "./trackmap.js";
import { deltaFacts, sectorTable } from "./insights.js";
import {
  basicTagText, buildCursorRows, distanceAt, formatChannelValue, formatDelta, formatMetres, LAP_COLORS, PHYSICS_CHANNELS,
} from "./model.js";
import { T } from "./strings.js";

import { t } from "../i18n.js";
const noop = () => {};
const CHART_HEIGHT = { small: 80, medium: 110, large: 140 };

export const notice = (message, error = false) => el("div", { class: `notice${error ? " notice--error" : ""}` }, message);

// ---------------------------------------------------------------- summary

function lapCard(side, lap, recording) {
  const color = LAP_COLORS[side];
  const basic = basicTagText(recording);
  return el("div", { class: "card analysis-lap", style: `border-top:3px solid ${color};` }, [
    el("div", { class: "analysis-lap__tag", style: `color:${color};` }, side === "a" ? t("LAP 1") : t("LAP 2")),
    basic ? el("div", { class: "tag tag--basic", title: t("Recorded by a client older than 0.9.0 — fewer channels") }, basic) : null,
    el("div", { class: "analysis-lap__who" }, [el("a", { href: `#/driver/${lap.driver_id}` }, lap.driver_name)]),
    el("div", { class: "subtitle", style: "margin:0 0 8px;" }, `${lap.car_model || lap.car_name} · ${t("lap #{0}", [lap.lap_number])}${lap.is_valid ? "" : ` · ${t("invalid")}`}`),
    el("div", { class: "lap-time analysis-lap__time" }, formatLapTime(lap.lap_time)),
    el("div", { class: "analysis-lap__sectors" }, (lap.sector_times || []).map((s, i) => el("span", {}, `S${i + 1} ${s > 0 ? s.toFixed(3) : "—"}`))),
  ]);
}

const tone = (v) => (v > 0 ? "analysis-diff--loss" : v < 0 ? "analysis-diff--gain" : "");

function sectorBlock(data) {
  const table = sectorTable(data);
  if (!table) return el("div", { class: "subtitle sector-table__none" }, T.sectorsUnknown);
  // "best" / "worst" are relative (the sector that lost least / most); "biggest gain" and
  // "biggest loss" are only said when the sector really gained (negative) / lost (positive),
  // and only then is the row tinted green / red.
  const badge = (row) => {
    if (row.best) {
      const gained = row.diff < 0;
      return el("span", { class: `sector-row__badge${gained ? " sector-row__badge--gain" : ""}` }, gained ? `${T.sectorBest} · ${T.sectorBiggestGain}` : T.sectorBest);
    }
    if (row.worst) {
      const lost = row.diff > 0;
      return el("span", { class: `sector-row__badge${lost ? " sector-row__badge--loss" : ""}` }, lost ? `${T.sectorWorst} · ${T.sectorBiggestLoss}` : T.sectorWorst);
    }
    return null;
  };
  const rows = table.rows.map((row) => el("div", {
    class: `sector-row ${tone(row.diff)}${row.best && row.diff < 0 ? " sector-row--best" : ""}${row.worst && row.diff > 0 ? " sector-row--worst" : ""}`,
    "data-sector": row.label, "data-rank": row.best ? "best" : row.worst ? "worst" : "",
  }, [
    el("span", { class: "sector-row__value" }, `${row.label} ${formatDelta(row.diff)} s`),
    badge(row),
  ]));
  rows.push(el("div", { class: `sector-row sector-row--total ${tone(table.total)}` }, el("span", { class: "sector-row__value" }, `${T.sectorTotal} ${formatDelta(table.total)} s`)));
  return el("div", { class: "sector-table", "data-source": table.source }, [
    ...rows,
    el("div", { class: "subtitle sector-table__caption", style: "margin:6px 0 0;" }, T.sectorCaption),
  ]);
}

function differenceCard(data) {
  const total = data.laps.a.lap_time - data.laps.b.lap_time;
  return el("div", { class: "card analysis-lap" }, [
    el("div", { class: "analysis-lap__tag" }, t("LAP 1 vs LAP 2")),
    el("div", { class: `lap-time analysis-lap__time ${tone(total)}` }, `${formatDelta(total)} s`),
    el("div", { class: "subtitle", style: "margin:0 0 8px;" }, total > 0 ? t("Lap 1 is slower") : total < 0 ? t("Lap 1 is faster") : t("Equal")),
    el("div", { class: "sector-title" }, T.sectorsTitle),
    sectorBlock(data),
  ]);
}

export function renderSummary(widget, ctx) {
  const data = ctx.baseData || ctx.data;
  const hasB = !!data.laps.b;
  const element = el("div", { class: "analysis-summary" }, [
    lapCard("a", data.laps.a, data.recording && data.recording.a),
    hasB ? lapCard("b", data.laps.b, data.recording && data.recording.b) : null,
    hasB ? differenceCard(data) : null,
  ]);
  return { element, destroy: noop };
}

// ---------------------------------------------------------------- delta (+ gain/loss facts)

function factChip(label, signed, at, tag, onPin) {
  const chip = el("button", { type: "button", class: `delta-fact delta-fact--${tag}`, "data-fact": tag, title: T.moveCursorHere }, t("{0}: {1} s @ {2}", [label, signed, formatMetres(at)]));
  chip.addEventListener("click", onPin);
  return chip;
}

export function renderDelta(widget, ctx) {
  const { data, state } = ctx;
  if (!data.delta) return { element: notice(T.deltaNeedsSecond), destroy: noop };
  const base = ctx.baseData || data;
  const chart = createChart(state, {
    key: "delta", title: t("Delta  (Lap 1 − Lap 2)"), unit: "s", kind: "delta", height: CHART_HEIGHT[widget.size] || 130, showXAxis: true,
    series: [{ values: data.delta, color: LAP_COLORS.delta, name: "delta" }],
    rangeValues: [base.delta],
    sectors: data.sectors.a,
    format: (v) => (v === null || v === undefined ? "—" : `${formatDelta(v)} s`),
  });
  // finer / coarser data of the same laps (zoom refinement): new values, same chart
  const offData = state.subscribe((what) => {
    if (what === "data" && state.data.delta) chart.setData(state.data, [state.data.delta]);
  });
  const facts = deltaFacts(ctx.baseData || data);   // biggest gain/loss of the WHOLE lap, even while zoomed in
  const chips = [];
  if (facts) {
    const g = facts.gain;
    const l = facts.loss;
    // Lap 1 minus Lap 2: a gain is NEGATIVE (green), a loss POSITIVE (red), net is the lap's real difference.
    chips.push(g ? factChip(T.biggestGain, formatDelta(-g.seconds), g.at, "gain", () => ctx.pinAt(g.index)) : el("span", { class: "delta-fact delta-fact--none", "data-fact": "gain" }, T.noGain));
    chips.push(l ? factChip(T.biggestLoss, formatDelta(l.seconds), l.at, "loss", () => ctx.pinAt(l.index)) : el("span", { class: "delta-fact delta-fact--none", "data-fact": "loss" }, T.noLoss));
    const net = facts.net;
    chips.push(el("span", { class: `delta-fact delta-fact--net ${tone(net)}`, "data-fact": "net" }, `${T.netLabel}: ${formatDelta(net)} s`));
  }
  const element = el("div", { class: "delta-widget" }, [
    chart.element,
    facts ? el("div", { class: "delta-facts" }, chips) : null,
    facts ? el("div", { class: "subtitle delta-facts__caption", style: "margin:4px 0 0;" }, T.deltaCaption) : null,
  ]);
  return { element, destroy: () => { offData(); chart.destroy(); } };
}

// ---------------------------------------------------------------- corners (V0.9.1)

// Lap 1 brakes `diff` metres later (> 0) or earlier (< 0) than Lap 2.
function brakingText(a, b) {
  if (a === null || a === undefined || b === null || b === undefined) return "—";
  const diff = Math.round(a - b);
  if (diff === 0) return T.brakeSame;
  return diff > 0 ? t("{0} m later", [diff]) : t("{0} m earlier", [-diff]);
}
const speedPair = (a, b) => `${a === null || a === undefined ? "—" : a.toFixed(1)} / ${b === null || b === undefined ? "—" : b.toFixed(1)}`;

export function renderCorners(widget, ctx) {
  const base = ctx.baseData || ctx.data;
  if (!base.laps.b) return { element: el("div", { class: "card" }, [el("h2", {}, T.cornersTitle), notice(T.cornersNeedSecond)]), destroy: noop };
  const corners = base.corners || [];
  if (!corners.length) return { element: el("div", { class: "card" }, [el("h2", {}, T.cornersTitle), notice(T.noCorners)]), destroy: noop };
  let most = null;
  let least = null;
  for (const c of corners) {
    if (c.delta > 0 && (!most || c.delta > most.delta)) most = c;
    if (c.delta < 0 && (!least || c.delta < least.delta)) least = c;
  }
  const head = el("tr", {}, [
    el("th", {}, "#"), el("th", {}, T.cornerAt), el("th", {}, "Δ"), el("th", {}, T.cornerBraking),
    el("th", {}, `${T.cornerMinSpeed} (km/h)`), el("th", {}, `${T.cornerExitSpeed} (km/h)`),
  ]);
  const rows = corners.map((c) => {
    const row = el("tr", {
      class: `corner-row${c === most ? " corner-row--loss" : ""}${c === least ? " corner-row--gain" : ""}`,
      "data-corner": String(c.n), tabindex: "0", title: T.cornerZoom,
    }, [
      el("td", {}, String(c.n)),
      el("td", {}, formatMetres(c.apex)),
      el("td", { class: tone(c.delta) }, `${formatDelta(c.delta)} s`),
      el("td", {}, brakingText(c.a.brake, c.b.brake)),
      el("td", {}, speedPair(c.a.min, c.b.min)),
      el("td", {}, speedPair(c.a.exit, c.b.exit)),
    ]);
    const open = () => {
      // the braking zone, the apex and the way out — then the finer data arrives on its own
      const first = Math.min(...[c.a.brake, c.b.brake, c.entry ?? c.start].filter((v) => v !== null && v !== undefined));
      const last = Math.max(...[c.a.throttle, c.b.throttle, c.apex + 150].filter((v) => v !== null && v !== undefined));
      ctx.state.setView(first - 60, Math.min(last + 60, c.end));
      ctx.pinAtDistance(c.apex);
    };
    row.addEventListener("click", open);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
    return row;
  });
  const total = corners.reduce((sum, c) => sum + c.delta, 0);
  rows.push(el("tr", { class: "corner-row corner-row--total" }, [
    el("td", { colspan: "2" }, T.sectorTotal), el("td", { class: tone(total) }, `${formatDelta(total)} s`), el("td", { colspan: "3" }, ""),
  ]));
  const element = el("div", { class: "card corners" }, [
    el("h2", {}, T.cornersTitle),
    el("div", { class: "corners__wrap" }, el("table", { class: "corners__table" }, [el("thead", {}, head), el("tbody", {}, rows)])),
    el("div", { class: "subtitle corners__caption", style: "margin:8px 0 0;" }, T.cornersCaption),
  ]);
  return { element, destroy: noop };
}

// ---------------------------------------------------------------- one channel

export function renderChart(widget, ctx) {
  const { data, state } = ctx;
  const entry = data.channels[widget.channel];
  if (!entry) {
    const label = (ctx.catalog.channels.find((c) => c.key === widget.channel) || {}).label || widget.channel;
    return { element: notice(`${label}: ${T.channelsFailed}`, true), destroy: noop };
  }
  const hasB = !!data.laps.b;
  if (!entry.a && !entry.b) {
    return { element: notice(`${entry.label}: ${hasB ? T.noDataInEither : T.noDataInLap}`), destroy: noop };
  }
  const colors = widget.colors || {};
  const series = [{ values: entry.a, spikes: entry.a_spikes || null, color: colors.a || LAP_COLORS.a, name: t("Lap 1"), side: "a" }];
  if (hasB) series.push({ values: entry.b, spikes: entry.b_spikes || null, color: colors.b || LAP_COLORS.b, name: t("Lap 2"), side: "b" });
  const baseEntry = (ctx.baseData && ctx.baseData.channels[widget.channel]) || entry;
  const chart = createChart(state, {
    key: widget.channel, title: entry.label, unit: entry.unit, kind: "line", series,
    rangeValues: hasB ? [baseEntry.a, baseEntry.b] : [baseEntry.a],
    range: entry.fixed_range || undefined, discrete: entry.discrete,
    height: Math.max((CHART_HEIGHT[widget.size] || 110) - (entry.discrete ? 30 : 0), 60), showXAxis: true,
    sectors: data.sectors.a,
    format: (v) => formatChannelValue(widget.channel, v, entry),
  });
  const offData = state.subscribe((what) => {
    if (what !== "data") return;
    const next = state.data.channels[widget.channel];
    if (next) chart.setData(state.data, hasB ? [next.a, next.b] : [next.a], hasB ? [next.a_spikes, next.b_spikes] : [next.a_spikes]);
  });
  return { element: chart.element, destroy: () => { offData(); chart.destroy(); } };
}

// ---------------------------------------------------------------- track map

export function renderTrackmap(widget, ctx) {
  const map = createTrackMap(ctx.mapState || ctx.state, {
    mode: widget.mode,
    ensureChannels: ctx.ensureChannels,
    onModeChange: ctx.onTrackmapMode,
  });
  return { element: el("div", { class: "card" }, [el("h2", {}, t("Track")), map.element]), destroy: () => map.destroy() };
}

// ---------------------------------------------------------------- values at the cursor

// formatLapTime() shows "—" for 0 (it is made for lap times, where 0 means "none"); at the
// cursor, 0 is the start of the lap and should read as a time.
const cursorTime = (seconds) => (seconds === 0 ? "0:00.000" : formatLapTime(seconds));

export function renderCursorTable(widget, ctx) {
  const { state } = ctx;
  const data = ctx.data; // for the layout of the table; the VALUES are read from state.data below
  const keys = Object.keys(data.channels);
  const hasB = !!data.laps.b;
  const head = el("tr", {}, [
    el("th", {}, t("At cursor")),
    el("th", { style: `color:${LAP_COLORS.a};` }, t("Lap 1")),
    hasB ? el("th", { style: `color:${LAP_COLORS.b};` }, t("Lap 2")) : null,
    hasB ? el("th", {}, "Δ") : null,
  ]);
  const meta = {
    distance: el("td", { colspan: hasB ? "3" : "1" }, ""),
    timeA: el("td", {}, ""), timeB: el("td", {}, ""), delta: el("td", { colspan: "2" }, ""),
  };
  const bodyRows = [
    el("tr", { class: "cursor-meta" }, [el("td", {}, t("Distance")), meta.distance]),
    el("tr", { class: "cursor-meta" }, hasB
      ? [el("td", {}, t("Time")), meta.timeA, meta.timeB, el("td", {}, "")]
      : [el("td", {}, t("Time")), meta.timeA]),
  ];
  if (hasB) bodyRows.push(el("tr", { class: "cursor-meta" }, [el("td", {}, t("Delta")), meta.delta]));

  const cells = {};
  let lastGroup = null;
  for (const key of keys) {
    const entry = data.channels[key];
    if (entry.group !== lastGroup) {
      bodyRows.push(el("tr", { class: "cursor-group" }, el("td", { colspan: hasB ? "4" : "2" }, entry.group)));
      lastGroup = entry.group;
    }
    cells[key] = { a: el("td", {}, ""), b: el("td", {}, ""), d: el("td", { class: "cursor-diff" }, "") };
    bodyRows.push(el("tr", {}, [
      el("td", {}, [entry.label, entry.unit ? el("small", {}, ` ${entry.unit}`) : null]),
      cells[key].a, hasB ? cells[key].b : null, hasB ? cells[key].d : null,
    ]));
  }
  const table = el("table", { class: "cursor-table" }, [el("thead", {}, head), el("tbody", {}, bodyRows)]);

  function update() {
    const current = state.data; // overview or refined stretch — whichever the cursor index refers to
    const i = state.getCursor();
    meta.distance.textContent = `${formatMetres(distanceAt(current.grid, i))}${state.isPinned() ? `  📌 ${t("pinned (click again to release)")}` : ""}`;
    meta.timeA.textContent = cursorTime(current.time.a[i]);
    if (hasB) {
      meta.timeB.textContent = cursorTime(current.time.b[i]);
      meta.delta.textContent = `${formatDelta(current.delta[i])} s`;
    }
    for (const row of buildCursorRows(current, i, keys)) {
      const c = cells[row.key];
      c.a.textContent = row.aText;
      if (hasB) {
        c.b.textContent = row.bText;
        c.d.textContent = row.diff === null ? "—" : (row.diff > 0 ? "+" : row.diff < 0 ? "−" : "") + formatChannelValue(row.key, Math.abs(row.diff), { unit: "", discrete: row.discrete });
      }
    }
  }
  const unsubscribe = state.subscribe((what) => {
    if (what === "cursor" || what === "data") update();
  });
  update();
  return { element: el("div", { class: "card" }, [el("h2", {}, t("Values at the cursor")), el("div", { class: "cursor-wrap" }, table)]), destroy: unsubscribe };
}

// ---------------------------------------------------------------- physics (loaded when opened)

export function renderPhysics(widget, ctx) {
  const body = el("div", { class: "analysis-physics__body" });
  const details = el("details", { class: "analysis-physics" }, [el("summary", {}, t("Car physics — suspension, tyres, G-G at the cursor")), body]);
  let loaded = false;
  let view = null;
  details.addEventListener("toggle", async () => {
    if (!details.open || loaded) return;
    loaded = true;
    body.appendChild(notice(t("Loading suspension and tyre data…")));
    try {
      await ctx.ensureChannels(PHYSICS_CHANNELS);
      body.innerHTML = "";
      view = createPhysicsView(ctx.mapState || ctx.state);
      body.appendChild(view.element);
    } catch (err) {
      loaded = false; // allow a retry by closing and reopening
      body.innerHTML = "";
      body.appendChild(notice(err.message, true));
    }
  });
  return { element: el("div", { class: "section" }, details), destroy: () => view && view.destroy() };
}
