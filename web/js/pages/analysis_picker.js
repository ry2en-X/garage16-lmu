// pages/analysis_picker.js — choose a lap to analyse or compare against.
//
// Three ways, as tabs:
//   - Leaderboard: any row of the class or exact-car leaderboard at this track,
//     e.g. the number one (leaderboard rows carry the id of the ranked lap)
//   - My laps:     my own laps (valid and invalid) at this track
//   - Driver:      type a driver's exact name, then pick one of their valid laps
//
// `context` says what the lap has to match: { track, carClass, carModel }. Without a
// track (picking the very first lap) only "My laps" is offered, across all tracks.

import { api } from "../api.js";
import { store } from "../state.js";
import { el, formatDate, formatLapTime } from "../format.js";
import { isBasicLap } from "../analysis/model.js";

import { t, tp } from "../i18n.js";
// Marks laps from clients older than 0.9.0 (fewer channels) next to the lap time.
function basicTag(item) {
  return isBasicLap(item)
    ? el("span", { class: "tag tag--basic", title: t("Recorded by a client older than 0.9.0 — only the basic channels") }, t("basic data"))
    : null;
}

// V0.9.6: the car MODEL (the livery name is in the tooltip); the fastest valid lap of the list
// is marked; a click anywhere on the row selects it (the button stays, for keyboards).
function lapRow(lap, { onPick, excludeId, bestId }) {
  const disabled = excludeId != null && lap.id === excludeId;
  const row = el("tr", { class: `picker__row${disabled ? " picker__row--off" : ""}${lap.id === bestId ? " picker__row--best" : ""}` }, [
    el("td", { title: lap.car_name || "" }, lap.car_model || lap.car_name || ""),
    el("td", {}, `#${lap.lap_number}`),
    el("td", {}, [el("span", { class: "lap-time" }, formatLapTime(lap.lap_time)), lap.id === bestId ? el("span", { class: "tag tag--gold picker__best" }, t("best")) : null]),
    el("td", {}, [el("span", { class: `tag ${lap.is_valid ? "tag--valid" : "tag--invalid"}` }, lap.is_valid ? t("valid") : t("invalid")), " ", basicTag(lap)]),
    el("td", { class: "picker__when" }, formatDate(lap.uploaded_at)),
    el("td", {}, el("button", { class: "btn btn--ghost", disabled: disabled ? "disabled" : null, onclick: (event) => { event.stopPropagation(); onPick(lap.id); } }, disabled ? t("Already chosen") : t("Select"))),
  ]);
  if (!disabled) row.addEventListener("click", () => onPick(lap.id));
  return row;
}

function oneTable(laps, options) {
  const valid = laps.filter((l) => l.is_valid && l.lap_time > 0);
  const best = valid.length ? valid.reduce((a, b) => (b.lap_time < a.lap_time ? b : a)) : null;
  const head = [t("Car"), t("Lap"), t("Time"), "", t("Set"), ""];
  return el("table", { class: "timing-table picker__table" }, [
    el("thead", {}, el("tr", {}, head.map((h, i) => el("th", { class: i === 4 ? "picker__when" : null }, h)))),
    el("tbody", {}, laps.map((lap) => lapRow(lap, { ...options, bestId: best ? best.id : null }))),
  ]);
}

// The "basic data" explanation, only when such a lap is in the list.
function basicLegend(laps) {
  if (!laps.some((lap) => isBasicLap(lap))) return null;
  return el("div", { class: "subtitle", style: "margin:0 0 12px;" }, [
    el("span", { class: "tag tag--basic" }, t("basic data")),
    t(" = recorded with a client older than 0.9.0: it can still be compared, but only on the basic channels (no suspension, tyre forces, G-forces or TC/ABS)."),
  ]);
}

// V0.9.6: across all tracks the laps are grouped per track (the most recent track open), so the
// first choice is "which track", not a scroll through one long table.
function lapTable(laps, options) {
  if (!laps.length) return el("div", { class: "empty-state" }, t("No laps found here."));
  if (!options.showTrack) return el("div", {}, [basicLegend(laps), oneTable(laps, options)]);
  const groups = new Map();
  for (const lap of laps) {
    if (!groups.has(lap.track_name)) groups.set(lap.track_name, []);
    groups.get(lap.track_name).push(lap);
  }
  const nodes = [...groups].map(([track, list], i) => {
    const best = list.filter((l) => l.is_valid && l.lap_time > 0).reduce((a, b) => (!a || b.lap_time < a.lap_time ? b : a), null);
    const group = el("details", { class: "picker__group", "data-track": track }, [
      el("summary", {}, [
        el("strong", {}, track),
        el("span", { class: "subtitle picker__group-meta" }, `${tp("{0} lap", "{0} laps", list.length, [list.length])}${best ? ` · ${t("best")} ${formatLapTime(best.lap_time)}` : ""}`),
      ]),
      oneTable(list, options),
    ]);
    if (i === 0) group.open = true;
    return group;
  });
  return el("div", {}, [basicLegend(laps), ...nodes]);
}

export function renderLapPicker(container, { title, context = {}, excludeId, onPick }) {
  const session = store.getSession() || {};
  const box = el("div", { class: "card picker" });
  box.appendChild(el("h2", {}, title));
  const tabBar = el("div", { class: "inline-form picker__tabs" });
  const body = el("div", { class: "picker__body" });
  box.appendChild(tabBar);
  box.appendChild(body);
  container.appendChild(box);

  const hasTrack = !!context.track;
  const tabs = [];
  if (hasTrack) tabs.push(["leaderboard", t("Leaderboard")]);
  tabs.push(["mine", t("My laps")]);
  if (hasTrack) tabs.push(["driver", t("Another driver")]);

  const buttons = {};
  for (const [id, label] of tabs) {
    buttons[id] = el("button", { class: "btn btn--ghost", onclick: () => show(id) }, label);
    tabBar.appendChild(buttons[id]);
  }
  if (tabs.length === 1) tabBar.style.display = "none";   // V0.9.6: one tab is no choice

  const notice = (message, error = false) => el("div", { class: `notice${error ? " notice--error" : ""}` }, message);

  async function showLeaderboard() {
    const controls = el("div", { class: "inline-form section" });
    const modeSelect = el("select", { id: "pickMode" }, [
      el("option", { value: "class" }, t("Whole class")),
      context.carModel ? el("option", { value: "car" }, t("Exact car — {0}", [context.carModel])) : null,
    ]);
    const classSelect = el("select", { id: "pickClass" });
    controls.appendChild(el("div", { class: "field" }, [el("label", { for: "pickMode" }, t("Ranking")), modeSelect]));
    const classField = el("div", { class: "field" }, [el("label", { for: "pickClass" }, t("Class")), classSelect]);
    controls.appendChild(classField);
    body.appendChild(controls);
    const list = el("div");
    body.appendChild(list);

    let options = [];
    try {
      options = await api.catalogClassOptions(context.track);
    } catch (err) {
      list.appendChild(notice(err.message, true));
      return;
    }
    for (const o of options) {
      classSelect.appendChild(el("option", { value: o.name }, `${o.name} · ${tp("{0} lap", "{0} laps", o.lap_count)}`));
    }
    const preferred = options.find((o) => o.name === context.carClass && o.lap_count > 0) || options.find((o) => o.lap_count > 0);
    if (preferred) classSelect.value = preferred.name;
    // With an exact car known, that board is the more useful default.
    if (context.carModel) modeSelect.value = "car";

    async function load() {
      classField.style.display = modeSelect.value === "class" ? "" : "none";
      list.innerHTML = "";
      list.appendChild(notice(t("Loading…")));
      try {
        const entries = modeSelect.value === "car"
          ? await api.carLeaderboard(context.track, context.carModel, 30)
          : await api.classLeaderboard(context.track, classSelect.value, 30);
        list.innerHTML = "";
        if (!entries.length) {
          list.appendChild(el("div", { class: "empty-state" }, t("No valid laps on this leaderboard yet.")));
          return;
        }
        const leader = entries[0].lap_time;
        const legend = basicLegend(entries);
        if (legend) list.appendChild(legend);
        list.appendChild(
          el("table", { class: "timing-table" }, [
            el("thead", {}, el("tr", {}, ["#", t("Driver"), t("Lap"), t("Gap"), ""].map((h) => el("th", {}, h)))),
            el("tbody", {}, entries.map((entry, i) => {
              const gap = entry.lap_time - leader;
              const unusable = entry.lap_id == null || entry.lap_id === excludeId;
              return el("tr", {}, [
                el("td", {}, el("span", { class: `pos-badge ${i === 0 ? "p1" : ""}` }, String(i + 1))),
                el("td", {}, entry.driver_name),
                el("td", {}, [el("span", { class: "lap-time" }, formatLapTime(entry.lap_time)), " ", basicTag(entry)]),
                el("td", {}, gap > 0 ? `+${gap.toFixed(3)}` : "—"),
                el("td", {}, el("button", {
                  class: "btn btn--ghost", disabled: unusable ? "disabled" : null,
                  onclick: () => onPick(entry.lap_id),
                }, entry.lap_id === excludeId ? t("Already chosen") : t("Select"))),
              ]);
            })),
          ])
        );
      } catch (err) {
        list.innerHTML = "";
        list.appendChild(notice(err.message, true));
      }
    }
    modeSelect.addEventListener("change", load);
    classSelect.addEventListener("change", load);
    await load();
  }

  async function showMine() {
    body.appendChild(notice(t("Loading…")));
    try {
      let laps;
      if (hasTrack && session.driverId != null) {
        laps = (await api.analysisDriverLaps(session.driverId, { trackName: context.track, limit: 100 })).laps;
      } else {
        laps = await api.myLaps({ limit: 200 });
      }
      body.innerHTML = "";
      body.appendChild(lapTable(laps, { showTrack: !hasTrack, onPick, excludeId }));
    } catch (err) {
      body.innerHTML = "";
      body.appendChild(notice(err.message, true));
    }
  }

  async function showDriver() {
    const input = el("input", { id: "pickDriverName", type: "text", placeholder: t("Exact display name"), autocomplete: "off" });
    const go = el("button", { class: "btn" }, t("Find laps"));
    body.appendChild(el("div", { class: "inline-form section" }, [
      el("div", { class: "field" }, [el("label", { for: "pickDriverName" }, t("Driver name")), input]),
      go,
    ]));
    const results = el("div");
    body.appendChild(results);

    async function run() {
      const name = input.value.trim();
      if (!name) return;
      results.innerHTML = "";
      results.appendChild(notice(t("Looking up…")));
      try {
        const matches = await api.lookupDriverByName(name);
        results.innerHTML = "";
        if (!matches.length) {
          results.appendChild(notice(t("No driver called \"{0}\" (the name has to match exactly, capitalisation aside).", [name])));
          return;
        }
        for (const match of matches) {
          const data = await api.analysisDriverLaps(match.driver_id, { trackName: context.track, limit: 50 });
          results.appendChild(el("h2", {}, data.driver_name));
          results.appendChild(lapTable(data.laps, { showTrack: false, onPick, excludeId }));
        }
      } catch (err) {
        results.innerHTML = "";
        results.appendChild(notice(err.message, true));
      }
    }
    go.addEventListener("click", run);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") run();
    });
  }

  function show(id) {
    for (const [tabId] of tabs) buttons[tabId].classList.toggle("active", tabId === id);
    body.innerHTML = "";
    if (id === "leaderboard") return showLeaderboard();
    if (id === "driver") return showDriver();
    return showMine();
  }

  show(tabs[0][0]);
  return box;
}
