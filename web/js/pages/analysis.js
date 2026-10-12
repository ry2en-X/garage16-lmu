// pages/analysis.js — lap analysis: one lap, or two laps compared (V0.9.0), as a set of
// configurable windows (V0.9.1).
//
//   #/analysis?a=<lap id>&b=<lap id>
//   #/analysis?a=<lap id>&ref=wr|team_best|my_best      resolve Lap 2 on the server
//   #/analysis?quick=wr|team_best|my_best[&track=..]    "You vs ..." (see server /quick)
//   #/analysis?a=..&b=..&c=..&d=..                      V0.9.4: Lap 3 / Lap 4 drawn over the pair
//
// Lap 1 (a) is usually your own lap; Lap 2 (b) can be ANY valid lap — e.g. the
// leaderboard's number one. Both are drawn over the same stretch of track with the
// time difference underneath. ONE shared cursor (analysis/model.js createViewState)
// ties every window together: hover or click any chart or the track map and every
// other window shows the values at that spot — without a single request to the server.
//
// What the user sees is the layout in analysis/layout.js: the Garage16 standard layout
// at first, optionally their own ("Ansicht anpassen"), kept in this browser only.
// The windows themselves live in analysis/widgets.js and are listed in
// analysis/modules.js; this file wires data, layout, editor and toolbar together.

import { api } from "../api.js";
import { store } from "../state.js";
import { el, formatLapTime } from "../format.js";
import { renderSignInPrompt } from "./auth.js";
import { renderLapPicker } from "./analysis_picker.js";
import { openCompareDialog, referenceMessage } from "./analysis_compare.js";
import { createEditorPanel } from "../analysis/editor.js";
import { renderGrid } from "../analysis/grid.js";
import { chooseMaxPoints } from "../analysis/insights.js";
import { createLayoutStore, requiredChannels, setTrackmapMode } from "../analysis/layout.js";
import { createEventsStore } from "../analysis/events.js";
import { applyColors, basicDataNotice, createViewState, distanceAt, EXTRA_SIDES, indexForDistance, lapColor, lapName } from "../analysis/model.js";
import { createLazy, createSelection } from "../analysis/corner_detail.js";
import { baseAdapter, covers, isUsable, planRefinement, REFINE } from "../analysis/refine.js";
import { notice } from "../analysis/widgets.js";
import { T } from "../analysis/strings.js";

import { t } from "../i18n.js";
const GRID_STEP_M = 2;
const QUICK_MODES = ["wr", "team_best", "my_best"];

export function parseAnalysisQuery(hash = window.location.hash) {
  const query = (hash.replace(/^#\/?/, "").split("?")[1]) || "";
  const params = new URLSearchParams(query);
  const id = (key) => {
    const value = Number(params.get(key));
    return Number.isInteger(value) && value > 0 ? value : null;
  };
  return { a: id("a"), b: id("b") };
}

// The V0.9.1 addition to the URL, parsed separately so parseAnalysisQuery keeps its shape.
// Every value is checked against a whitelist or a length limit: the URL is never trusted.
export function parseAnalysisExtras(hash = window.location.hash) {
  const query = (hash.replace(/^#\/?/, "").split("?")[1]) || "";
  const params = new URLSearchParams(query);
  const pick = (key) => (QUICK_MODES.includes(params.get(key)) ? params.get(key) : null);
  const text = (key) => {
    const value = params.get(key);
    return value && value.length <= 200 ? value : null;
  };
  const note = params.get("note");
  const lapId = (key) => {
    const value = Number(params.get(key));
    return Number.isInteger(value) && value > 0 ? value : null;
  };
  return {
    // V0.9.4: up to two more laps (ids), drawn over Lap 1 / Lap 2
    laps: [lapId("c"), lapId("d")].filter((id, i, all) => id && all.indexOf(id) === i),
    quick: pick("quick"),
    ref: pick("ref"),
    track: text("track"),
    model: text("model"),
    note: note && Object.prototype.hasOwnProperty.call(T.note, note) ? note : null,
  };
}

function hashFor(a, b, extra = {}) {
  const params = new URLSearchParams();
  if (a) params.set("a", String(a));
  if (b) params.set("b", String(b));
  for (const [key, value] of Object.entries(extra)) if (value) params.set(key, value);
  return `#/analysis?${params.toString()}`;
}

function go(a, b, extra) {
  window.location.hash = hashFor(a, b, extra);
}

// For automatic redirects (quick / ref): replace the history entry, so "back" does not
// land on the redirecting URL again and bounce forward.
function redirect(a, b, extra) {
  const target = hashFor(a, b, extra);
  if (typeof window.location.replace === "function") window.location.replace(target);
  else window.location.hash = target;
}

// ----------------------------------------------------------------------------

export async function renderAnalysis(container) {
  container.classList.add("panel--wide");
  if (!store.isSignedIn()) {
    renderSignInPrompt(container, t("Sign in to analyse laps."));
    return;
  }
  container.appendChild(el("h1", {}, t("LapTime Analyzer")));
  container.appendChild(el("p", { class: "subtitle" }, t("Compare two laps channel by channel — against anyone's valid lap, e.g. the leaderboard's number one.")));

  const { a, b } = parseAnalysisQuery();
  const extras = parseAnalysisExtras();

  // --- "You vs ...": the server pairs my lap with the right reference
  if (!a && !b && extras.quick) {
    await runQuick(container, extras.quick, { track: extras.track, carModel: extras.model });
    return;
  }

  // --- Lap 1 given plus a reference kind in the URL
  if (a && !b && extras.ref) {
    await runReference(container, a, extras.ref);
    return;
  }

  // --- Nothing chosen yet: quick starts, then pick Lap 1
  if (!a && !b) {
    container.appendChild(quickStartCard());
    renderLapPicker(container, { title: t("Choose Lap 1"), context: {}, onPick: (id) => go(id, null) });
    return;
  }

  // --- Only Lap 2 given: pick my own Lap 1 at that track
  if (!a && b) {
    let info;
    try {
      info = await api.analysisLap(b);
    } catch (err) {
      container.appendChild(notice(err.message, true));
      return;
    }
    const lap = info.lap;
    container.appendChild(notice(t("Lap 2: {0} — {1}, {2} ({3}). Now choose Lap 1 to compare it against.", [lap.driver_name, lap.track_name, formatLapTime(lap.lap_time), lap.car_model || lap.car_name])));
    renderLapPicker(container, {
      title: t("Choose Lap 1"),
      context: { track: lap.track_name, carClass: lap.car_class, carModel: lap.car_model },
      excludeId: b,
      onPick: (id) => go(id, b),
    });
    return;
  }

  let catalog;
  try {
    catalog = await api.analysisChannels();
  } catch (err) {
    container.appendChild(notice(err.message, true));
    return;
  }
  const validChannels = new Set(catalog.channels.map((c) => c.key));
  const layoutStore = createLayoutStore(window.localStorage, { validChannels, onColors: applyColors });
  const maxPoints = chooseMaxPoints(container.clientWidth);
  const wanted = requiredChannels(layoutStore.get());

  const status = notice(t("Loading lap data…"));
  container.appendChild(status);
  let data;
  try {
    const extra = extras.laps.filter((id) => id !== a && id !== b);
    data = await api.analysisCompare({ lapA: a, lapB: b || undefined, channels: wanted, step: GRID_STEP_M, maxPoints, extra });
  } catch (err) {
    status.className = "notice notice--error";
    status.textContent = err.message;
    // Let the user get out of a dead end (e.g. Lap 2 vanished): choose again.
    const actions = el("div", { class: "inline-form section" }, [
      el("button", { class: "btn btn--ghost", onclick: () => go(null, null) }, t("Start over")),
      b ? el("button", { class: "btn btn--ghost", onclick: () => go(a, null) }, t("Analyse Lap 1 alone")) : null,
    ]);
    container.appendChild(actions);
    return;
  }
  status.remove();
  const extraIds = (data.extra_sides || []).map((side) => data.laps[side] && data.laps[side].id).filter(Boolean);
  renderResult(container, data, catalog, { a, b, extra: extraIds }, { layoutStore, maxPoints, note: extras.note });
}

// ----------------------------------------------------------------------------
// "You vs ..." and "compare with <reference>" entry points

// The sentence for a quick pairing that found nothing — with the track it looked at, because
// without a track the server uses the track of the driver's most recent lap, and "you already
// are the fastest" means nothing unless it says where.
function quickMessage(mode, res) {
  const message = res.reason === "already_best" ? T.alreadyBest[mode] : referenceMessage(mode, res.reason);
  return res.lap_a && res.lap_a.track_name ? t("At {0}: {1}", [res.lap_a.track_name, message]) : message;
}

function quickStartCard() {
  const status = el("div", { class: "quick__status", role: "status" });
  const actions = el("div", { class: "inline-form quick__actions" });
  const buttons = QUICK_MODES.map((mode) => el("button", {
    type: "button", class: "btn quick__button", "data-quick": mode,
    onclick: async () => {
      status.textContent = T.quickRunning;
      status.className = "quick__status";
      actions.innerHTML = "";
      try {
        const res = await api.analysisQuick({ mode });
        if (res.found) return redirect(res.lap_a.id, res.lap_b.id, res.already_best ? { note: `already_best_${mode}` } : {});
        status.className = "quick__status quick__status--error";
        status.textContent = quickMessage(mode, res);
        // a way on: look at that lap alone (then "Compare lap" offers the other references)
        if (res.lap_a) actions.appendChild(el("button", { type: "button", class: "btn btn--ghost quick__alone", onclick: () => go(res.lap_a.id, null) }, T.onlyLap1));
      } catch {
        status.className = "quick__status quick__status--error";
        status.textContent = T.genericError;
      }
    },
  }, T.quick[mode]));
  return el("div", { class: "card quick" }, [
    el("h2", {}, T.quickTitle), el("div", { class: "inline-form" }, buttons), status, actions,
    // V0.9.3: stints, consistency and the theoretical best live on their own page
    el("p", { class: "subtitle", style: "margin:10px 0 0;" }, el("a", { href: "#/stints" }, t("Stint Analyzer: sessions, stints, consistency & theoretical best →"))),
  ]);
}

async function runQuick(container, mode, { track, carModel }) {
  const status = notice(T.quickRunning);
  container.appendChild(status);
  try {
    const res = await api.analysisQuick({ mode, track, carModel });
    if (res.found) {
      redirect(res.lap_a.id, res.lap_b.id, res.already_best ? { note: `already_best_${mode}` } : {});
      return;
    }
    status.className = "notice notice--error";
    status.textContent = quickMessage(mode, res);
    const actions = [el("button", { class: "btn btn--ghost", onclick: () => go(null, null) }, T.back)];
    if (res.lap_a) actions.unshift(el("button", { class: "btn", onclick: () => go(res.lap_a.id, null) }, T.onlyLap1));
    container.appendChild(el("div", { class: "inline-form section" }, actions));
  } catch {
    status.className = "notice notice--error";
    status.textContent = T.genericError;
    container.appendChild(el("div", { class: "inline-form section" }, el("button", { class: "btn btn--ghost", onclick: () => go(null, null) }, T.back)));
  }
}

async function runReference(container, lapId, kind) {
  const status = notice(T.quickRunning);
  container.appendChild(status);
  try {
    const res = await api.analysisReference({ kind, lapId });
    if (res.found && res.lap) {
      redirect(lapId, res.lap.id, res.already_best ? { note: `already_best_${kind}` } : {});
      return;
    }
    status.className = "notice notice--error";
    status.textContent = res.reason === "already_best" ? T.alreadyBest[kind] : referenceMessage(kind, res.reason);
  } catch {
    status.className = "notice notice--error";
    status.textContent = T.genericError;
  }
  container.appendChild(el("div", { class: "inline-form section" }, el("button", { class: "btn btn--ghost", onclick: () => go(lapId, null) }, T.onlyLap1)));
}

// ----------------------------------------------------------------------------

function renderResult(container, baseData, catalog, ids, env) {
  const { layoutStore, maxPoints } = env;
  // `baseData` is the whole-lap overview and never changes identity; `data` is what the
  // charts draw right now — the overview, or (zoomed in far) a finer stretch of it.
  let data = baseData;
  let detail = null; // { start, end, step } of the refined stretch in use
  const hasB = !!baseData.laps.b;
  const extraIds = ids.extra || [];
  // V0.9.4: the extra laps stay when Lap 1 / Lap 2 change (c=, d= in the URL)
  const keep = (list = extraIds) => Object.fromEntries(list.slice(0, 2).map((id, i) => [EXTRA_SIDES[i], String(id)]));
  const state = createViewState(baseData);
  const selection = createSelection();
  const history = createLazy(() => api.analysisCornerHistory(ids.a));
  const lapSectors = createLazy(() => api.analysisLapSectors(ids.a));
  // V0.9.3: track-limits findings + lock-ups/wheelspin of both laps, loaded once (small)
  const events = createEventsStore(() => api.analysisEvents({ lapA: ids.a, lapB: ids.b || undefined }));
  container._analysis = { state, layoutStore, events }; // read by tests/js/*.test.mjs

  // A view of the shared state whose subscriptions can be released when the windows are
  // rebuilt (layout change) — otherwise every rebuild would leave listeners behind.
  let subscriptions = [];
  const scopedState = () => {
    const scoped = {
      ...state,
      subscribe(fn) {
        const unsubscribe = state.subscribe(fn);
        subscriptions.push(unsubscribe);
        return unsubscribe;
      },
    };
    // `data` stays live: after a zoom-refinement swap the windows read the new grid
    Object.defineProperty(scoped, "data", { get: () => state.data, enumerable: true });
    return scoped;
  };
  const releaseSubscriptions = () => {
    subscriptions.forEach((unsubscribe) => unsubscribe());
    subscriptions = [];
  };

  // --- notices that belong to the laps, not to a window
  const noticeSlot = el("div", { class: "analysis-notices" });
  const renderNotices = () => {
    noticeSlot.innerHTML = "";
    if (env.note && T.note[env.note]) noticeSlot.appendChild(notice(T.note[env.note]));
    const basicNotice = basicDataNotice(baseData);
    if (basicNotice) {
      const box = notice(basicNotice);
      box.classList.add("notice--basic");
      box.setAttribute("data-notice", "basic-data");
      noticeSlot.appendChild(box);
    }
    if (baseData.unavailable.length) {
      const labels = baseData.unavailable.map((k) => (baseData.channels[k] ? baseData.channels[k].label : k));
      noticeSlot.appendChild(notice(
        t("Not available for every lap (recorded before V0.9 or by an older client): {0}. The lap that has it is still drawn.", [labels.join(", ")])
      ));
    }
  };
  container.appendChild(noticeSlot);
  renderNotices();

  // --- toolbar + pickers
  const pickerSlot = el("div");
  const showPicker = (slot) => {
    pickerSlot.innerHTML = "";
    const other = slot === "a" ? ids.b : ids.a;   // (an extra lap: Lap 1 is excluded)
    renderLapPicker(pickerSlot, {
      title: slot === "a" ? t("Choose Lap 1") : slot === "b" ? t("Choose Lap 2 (compare against)") : t("Choose a lap to draw over the comparison"),
      context: { track: baseData.laps.a.track_name, carClass: baseData.laps.a.car_class, carModel: baseData.laps.a.car_model },
      excludeId: other || undefined,
      onPick: (id) => {
        if (slot === "a") go(id, ids.b, keep());
        else if (slot === "b") go(ids.a, id, keep());
        else go(ids.a, ids.b, keep([...extraIds, id]));   // V0.9.4: one more lap over the pair
      },
    });
    pickerSlot.scrollIntoView && pickerSlot.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };
  const customizeButton = el("button", { type: "button", class: "btn btn--ghost analysis-customize", "aria-pressed": "false", onclick: () => setEditing(!editing) }, T.customize);
  container.appendChild(el("div", { class: "inline-form section analysis-toolbar" }, [
    el("button", { type: "button", class: "btn analysis-compare-open", onclick: () => openCompareDialog({
      lapA: baseData.laps.a,
      onCompare: (lapB, note) => go(ids.a, lapB, note ? { note } : {}),
    }) }, T.compareButton),
    el("button", { class: "btn btn--ghost", onclick: () => showPicker("a") }, t("Change Lap 1")),
    el("button", { class: "btn btn--ghost", onclick: () => showPicker("b") }, hasB ? t("Change Lap 2") : t("Compare with…")),
    hasB ? el("button", { class: "btn btn--ghost", onclick: () => go(ids.b, ids.a, keep()) }, t("Swap laps")) : null,
    hasB ? el("button", { class: "btn btn--ghost", onclick: () => go(ids.a, null) }, t("Remove Lap 2")) : null,
    hasB && extraIds.length < 2 ? el("button", { class: "btn btn--ghost analysis-add-lap", onclick: () => showPicker("extra") }, t("+ Lap 3/4 over it")) : null,
    customizeButton,
  ]));
  // V0.9.4: the extra laps, each with its colour, line style and a way to remove it
  if (extraIds.length) {
    container.appendChild(el("div", { class: "analysis-extra-laps" }, (baseData.extra_sides || []).map((side, i) => {
      const lap = baseData.laps[side];
      return el("span", { class: `analysis-extra-lap analysis-extra-lap--${side}`, style: `border-color:${lapColor(side)};` }, [
        el("span", { style: `color:${lapColor(side)};` }, `${lapName(side)}: `),
        `${lap.driver_name} · ${formatLapTime(lap.lap_time)} · ${t("lap #{0}", [lap.lap_number])}`,
        el("button", { type: "button", class: "analysis-extra-lap__remove", title: t("Remove this lap"), "aria-label": t("Remove this lap"),
          onclick: () => go(ids.a, ids.b, keep(extraIds.filter((_, k) => k !== i))) }, "×"),
      ]);
    })));
  }
  container.appendChild(pickerSlot);
  if (!hasB) showPicker("b");

  // --- loading channels that a layout change needs (the only requests besides the first load
  //     and the zoom refinement below)
  const statusSlot = el("div", { class: "analysis-status", role: "status" });
  const sameGrid = (x, y) => x.start === y.start && x.step === y.step && x.count === y.count;
  const mergeChannels = (target, extra) => {
    Object.assign(target.channels, extra.channels);
    for (const key of extra.unavailable || []) if (!target.unavailable.includes(key)) target.unavailable.push(key);
  };
  async function ensureChannels(keys) {
    const missing = keys.filter((k) => !baseData.channels[k]).slice(0, 24);
    if (!missing.length) return;
    const extra = await api.analysisCompare({ lapA: ids.a, lapB: ids.b || undefined, channels: missing, step: GRID_STEP_M, maxPoints, extra: extraIds });
    if (!sameGrid(extra.grid, baseData.grid)) throw new Error(t("The laps changed while loading — reload the page."));
    // While zoomed in, the charts draw the refined stretch: it needs the channel too.
    // (Done without rebuilding anything, so the window that asked for it stays put.)
    let detailExtra = null;
    if (detail && data !== baseData) {
      detailExtra = await api.analysisCompare({
        lapA: ids.a, lapB: ids.b || undefined, channels: missing, step: detail.step, start: detail.start, end: detail.end, maxPoints: REFINE.maxPoints, extra: extraIds,
      });
      if (!sameGrid(detailExtra.grid, data.grid)) throw new Error(t("The view changed while loading — try again."));
    }
    mergeChannels(baseData, extra);
    if (detailExtra) mergeChannels(data, detailExtra);
    renderNotices();
  }

  // --- windows
  const gridSlot = el("div", { class: "analysis-grid-slot" });
  const editorSlot = el("div");
  let editing = false;
  let grid = null;

  const windowContext = () => {
    const scoped = scopedState();
    return {
      state: scoped,
      data,
      baseData,
      mapState: baseAdapter(scoped, baseData),
      catalog,
      ensureChannels,
      events,
      // V0.9.4: the corner the windows show, the driver's earlier laps, the session's sectors,
      // and the racing lines of a stretch in high resolution
      selection,
      history,
      lapSectors,
      fetchLine: (opts) => api.analysisLine({ lapA: ids.a, lapB: ids.b || undefined, extra: extraIds, ...opts }),
      fetchDistribution: (opts) => api.analysisDistribution({ lapA: ids.a, lapB: ids.b || undefined, ...opts }),
      onTrackmapMode: (mode) => {
        const map = layoutStore.get().widgets.find((w) => w.type === "trackmap");
        if (map) layoutStore.set(setTrackmapMode(layoutStore.get(), map.id, mode));
      },
      // `index` is an index of the overview grid. Take the user THERE: if that spot is outside
      // what is on screen (zoomed in elsewhere), move the view first — a pinned cursor
      // nobody can see is useless — then pin by distance on whichever grid is now current.
      pinAtDistance: (metres) => state.pinCursor(indexForDistance(state.data.grid, metres)),
      pinAt: (index) => {
        const metres = distanceAt(baseData.grid, index);
        const [lo, hi] = state.getView();
        if (metres < lo || metres > hi) state.setView(metres - (hi - lo) / 2, metres + (hi - lo) / 2);
        state.pinCursor(indexForDistance(state.data.grid, metres));
      },
    };
  };

  function rebuild() {
    if (grid) grid.destroy();
    releaseSubscriptions();
    grid = renderGrid(gridSlot, layoutStore.get(), windowContext(), { catalog, editing, onLayoutChange: applyLayout });
    editorSlot.innerHTML = "";
    if (editing) {
      editorSlot.appendChild(createEditorPanel({
        layout: layoutStore.get(), catalog, persisted: layoutStore.isPersisted(),
        onChange: applyLayout, onReset: resetLayout, onDone: () => setEditing(false),
      }).element);
    }
    customizeButton.setAttribute("aria-pressed", editing ? "true" : "false");
    customizeButton.textContent = editing ? T.customizeDone : T.customize;
  }

  async function applyLayout(next) {
    layoutStore.set(next);
    await loadThenRebuild();
  }

  async function loadThenRebuild(message) {
    statusSlot.innerHTML = "";
    const missing = requiredChannels(layoutStore.get()).filter((k) => !baseData.channels[k]);
    if (missing.length) {
      statusSlot.appendChild(notice(T.loadingChannels));
      try {
        await ensureChannels(missing);
        statusSlot.innerHTML = "";
      } catch {
        statusSlot.innerHTML = "";
        statusSlot.appendChild(notice(T.channelsFailed, true));
      }
    }
    if (message) statusSlot.appendChild(notice(message));
    rebuild();
  }

  function resetLayout() {
    layoutStore.reset();
    loadThenRebuild(T.resetDone);
  }

  function setEditing(on) {
    editing = on;
    rebuild();
  }

  container.appendChild(statusSlot);
  container.appendChild(editorSlot);
  container.appendChild(gridSlot);
  container.appendChild(el("p", { class: "subtitle" },
    t("Hover a chart to read exact values · click to pin the position (click again to release) · mouse wheel zooms · drag pans · double-click resets · ← → step along the lap. Touch: drag sideways to scrub, tap to place the cursor, hold to pin, pinch to zoom, double-tap resets.")));
  loadThenRebuild();

  // --- zoom refinement (V0.9.1): finer data for the stretch the user zoomed into
  // Finer (or the overview's) data of the same laps. Nothing is rebuilt: state.replaceData()
  // announces "data", the charts take the new values in place (same canvas, same listeners, a
  // drag in progress continues), the values table reads them, and the windows about the whole
  // lap (summary, track map, physics) do not change at all. Before this, every swap rebuilt every
  // window (~60 ms on the main thread) and cut off a drag.
  function swapData(next, nextDetail) {
    data = next;
    detail = nextDetail;
    state.replaceData(next);
  }

  let refineTimer = null;
  let refineSeq = 0;
  let refineBlockedUntil = 0;     // set after a 429: the server asked us to slow down
  const refineCache = new Map();  // "start|end|step|channels" -> response (insertion order = age)
  const asStretch = (r) => ({ start: r.grid.start, end: r.grid.start + r.grid.step * (r.grid.count - 1), step: r.grid.step });
  async function refine() {
    refineTimer = null;
    if (!container.isConnected) return;
    const plan = planRefinement({ view: state.getView(), full: state.full, baseStep: baseData.grid.step, current: detail });
    if (plan.action === "base") {
      refineSeq += 1; // an answer that is still on its way is no longer wanted
      if (data !== baseData) swapData(baseData, null);
      return;
    }
    if (plan.action !== "refine") return;
    const channels = Object.keys(baseData.channels).slice(0, 24);
    const key = `${plan.start}|${plan.end}|${plan.step}|${channels.join(",")}`;
    const seq = ++refineSeq;
    let response = refineCache.get(key);
    if (response) {
      refineCache.delete(key);
      refineCache.set(key, response); // most recently used
    } else {
      if (Date.now() < refineBlockedUntil) return;
      try {
        response = await api.analysisCompare({
          lapA: ids.a, lapB: ids.b || undefined, channels, step: plan.step, start: plan.start, end: plan.end, maxPoints: REFINE.maxPoints, extra: extraIds,
        });
      } catch (err) {
        // The overview stays: the charts are less detailed, not broken. After "too many
        // requests" stop asking for a while instead of retrying on every gesture.
        if (err && err.status === 429) refineBlockedUntil = Date.now() + REFINE.backoffMs;
        return;
      }
      if (isUsable(response, { ...plan, extraSides: baseData.extra_sides || [] })) {
        refineCache.set(key, response);
        while (refineCache.size > REFINE.cacheEntries) refineCache.delete(refineCache.keys().next().value);
      }
    }
    // Two independent guards against an answer that arrives too late (superseded by a newer
    // zoom/pan, or no longer covering what is on screen) — either alone is enough for most
    // cases, together they leave no gap.
    if (seq !== refineSeq || !container.isConnected) return;
    plan.extraSides = baseData.extra_sides || [];
    if (!isUsable(response, plan) || !covers(asStretch(response), state.getView())) return;
    swapData({ ...response, map: baseData.map }, asStretch(response));
  }
  state.subscribe((what) => {
    if (what !== "view") return;
    // Panning out of the refined stretch: show the overview AT ONCE, never a half-empty chart.
    if (detail && !covers(detail, state.getView())) {
      refineSeq += 1;
      swapData(baseData, null); // cheap now, and the drag goes on
    }
    if (refineTimer !== null) clearTimeout(refineTimer);
    refineTimer = setTimeout(refine, REFINE.debounceMs);
  });

  // --- keyboard: ← → step along the lap, Esc releases a pinned position
  const onKey = (event) => {
    if (!container.isConnected) {
      window.removeEventListener("keydown", onKey);
      return;
    }
    const tag = event.target && event.target.tagName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      const step = (event.shiftKey ? 10 : 1) * (event.key === "ArrowRight" ? 1 : -1);
      state.setCursor(state.getCursor() + step);
    } else if (event.key === "Escape") {
      state.unpin();
    }
  };
  window.addEventListener("keydown", onKey);
}
