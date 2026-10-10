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

function lapRow(lap, { showTrack, onPick, excludeId }) {
  const disabled = excludeId != null && lap.id === excludeId;
  return el("tr", {}, [
    showTrack ? el("td", {}, lap.track_name) : null,
    el("td", {}, lap.car_name || lap.car_model || ""),
    el("td", {}, `#${lap.lap_number}`),
    el("td", {}, el("span", { class: "lap-time" }, formatLapTime(lap.lap_time))),
    el("td", {}, [el("span", { class: `tag ${lap.is_valid ? "tag--valid" : "tag--invalid"}` }, lap.is_valid ? t("valid") : t("invalid")), " ", basicTag(lap)]),
    el("td", {}, formatDate(lap.uploaded_at)),
    el("td", {}, el("button", { class: "btn btn--ghost", disabled: disabled ? "disabled" : null, onclick: () => onPick(lap.id) }, disabled ? t("Already chosen") : t("Select"))),
  ]);
}

function lapTable(laps, options) {
  if (!laps.length) return el("div", { class: "empty-state" }, t("No laps found here."));
  const head = [options.showTrack ? t("Track") : null, t("Car"), t("Lap"), t("Time"), "", t("Set"), ""].filter((h) => h !== null);
  return el("table", { class: "timing-table" }, [
    el("thead", {}, el("tr", {}, head.map((h) => el("th", {}, h)))),
    el("tbody", {}, laps.map((lap) => lapRow(lap, options))),
  ]);
}

export function renderLapPicker(container, { title, context = {}, excludeId, onPick }) {
  const session = store.getSession() || {};
  const box = el("div", { class: "card picker" });
  box.appendChild(el("h2", {}, title));
  const tabBar = el("div", { class: "inline-form picker__tabs" });
  const body = el("div", { class: "picker__body" });
  box.appendChild(tabBar);
  box.appendChild(el("div", { class: "subtitle", style: "margin:0 0 12px;" }, [
    el("span", { class: "tag tag--basic" }, t("basic data")),
    t(" = recorded with a client older than 0.9.0: it can still be compared, but only on the basic channels (no suspension, tyre forces, G-forces or TC/ABS)."),
  ]));
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
