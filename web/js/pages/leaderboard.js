// pages/leaderboard.js — public, no auth. Two views (V0.6.3):
//   - Class: GET /leaderboard/class/{track}/{car_class} — main leaderboard,
//     every car within a class (e.g. all Hypercars) competes together.
//   - Car: GET /leaderboard/car/{track}/{car_model} — sub-leaderboard,
//     one exact car model, ignoring team/livery/car number.
//
// V0.8.9: the track picker is a searchable venue list with all layouts (see below).
//
// V0.6.7: track/class/car are now dropdowns fed by the catalog endpoints
// (real values discovered from actual uploads — see
// server/routers/leaderboard.py) instead of free-text fields where a
// typo or the wrong internal name (LMU reports the Hypercar class as "Hyper";
// since V0.8.9 the server stores the readable name "Hypercar") silently returned an empty board.

import { api } from "../api.js";
import { el, formatLapTime, formatDate } from "../format.js";

export async function renderLeaderboard(container) {
  container.appendChild(el("h1", {}, "Leaderboard"));
  container.appendChild(el("p", { class: "subtitle" }, "Each driver's best valid lap, fastest first."));

  let mode = "class"; // "class" | "car"

  const modeToggle = el("div", { class: "inline-form" }, [
    el("button", { class: "btn btn--ghost", id: "modeClassBtn" }, "By class"),
    el("button", { class: "btn btn--ghost", id: "modeCarBtn" }, "By exact car"),
  ]);
  container.appendChild(modeToggle);

  // V0.8.9: search-first track picker. Every track is listed (also ones nobody
  // has driven yet), grouped by venue with ALL its layouts, and every class is
  // offered — nothing has to be entered by an admin; new tracks/layouts/classes
  // appear by themselves once LMU sends them (see server/catalog.py).
  const form = el("div", { class: "inline-form section" });

  const trackSearch = el("input", { id: "trackSearch", type: "search", placeholder: "Search a track, e.g. Fuji, Le Mans, Spa…", autocomplete: "off" });
  const searchField = el("div", { class: "field" }, [el("label", { for: "trackSearch" }, "Find a track"), trackSearch]);

  const venueSelect = el("select", { id: "venueSelect" }, [el("option", { value: "" }, "Loading tracks…")]);
  const venueField = el("div", { class: "field" }, [el("label", { for: "venueSelect" }, "Track"), venueSelect]);

  const layoutSelect = el("select", { id: "layoutSelect" });
  const layoutField = el("div", { class: "field", style: "display:none;" }, [el("label", { for: "layoutSelect" }, "Layout"), layoutSelect]);

  const classSelect = el("select", { id: "classSelect" }, [el("option", { value: "" }, "Pick a track first")]);
  const classField = el("div", { class: "field" }, [el("label", { for: "classSelect" }, "Class"), classSelect]);

  const carSelect = el("select", { id: "carSelect" }, [el("option", { value: "" }, "Pick a track first")]);
  const carField = el("div", { class: "field", style: "display:none;" }, [el("label", { for: "carSelect" }, "Car"), carSelect]);

  const searchBtn = el("button", { class: "btn" }, "Search");
  [searchField, venueField, layoutField, classField, carField, searchBtn].forEach((node) => form.appendChild(node));
  container.appendChild(form);

  const hintBox = el("div");
  container.appendChild(hintBox);
  const resultsBox = el("div");
  container.appendChild(resultsBox);

  let venues = [];
  let currentTrack = ""; // the exact track string LMU sent for the chosen layout

  function lapsLabel(n) {
    return n ? `${n} lap${n === 1 ? "" : "s"}` : "no laps yet";
  }

  function fillSelect(select, values, placeholder) {
    select.innerHTML = "";
    if (!values.length) {
      select.appendChild(el("option", { value: "" }, placeholder));
      select.disabled = true;
      return;
    }
    select.disabled = false;
    select.appendChild(el("option", { value: "" }, "Choose…"));
    values.forEach((v) => select.appendChild(el("option", { value: v }, v)));
  }

  function fillClassOptions(options) {
    classSelect.innerHTML = "";
    classSelect.disabled = false;
    classSelect.appendChild(el("option", { value: "" }, "Choose…"));
    options.forEach((o) => classSelect.appendChild(el("option", { value: o.name }, `${o.name} · ${lapsLabel(o.lap_count)}`)));
  }

  async function loadVenues(q) {
    const previous = venueSelect.value;
    try {
      venues = await api.catalogVenues(q);
    } catch (err) {
      venues = [];
      venueSelect.innerHTML = "";
      venueSelect.appendChild(el("option", { value: "" }, "Couldn't load tracks"));
      return;
    }
    venueSelect.innerHTML = "";
    if (!venues.length) {
      venueSelect.appendChild(el("option", { value: "" }, q ? `No track matches "${q}"` : "No tracks yet"));
      venueSelect.disabled = true;
      await onVenueChange();
      return;
    }
    venueSelect.disabled = false;
    venueSelect.appendChild(el("option", { value: "" }, "Choose a track…"));
    venues.forEach((v) => venueSelect.appendChild(el("option", { value: v.venue }, `${v.venue} · ${lapsLabel(v.lap_count)}`)));
    if (previous && venues.some((v) => v.venue === previous)) {
      venueSelect.value = previous;
    } else if (q && venues.length === 1) {
      venueSelect.value = venues[0].venue; // one match: no need to make the user click it
    }
    await onVenueChange();
  }

  async function onVenueChange() {
    hintBox.innerHTML = "";
    const venue = venues.find((v) => v.venue === venueSelect.value);
    layoutField.style.display = "none";
    currentTrack = "";
    if (!venue) {
      fillSelect(classSelect, [], "Pick a track first");
      fillSelect(carSelect, [], "Pick a track first");
      return;
    }
    if (venue.layouts.length === 0) {
      // A known track nobody has driven yet — still selectable, with every class.
      hintBox.appendChild(el("div", { class: "notice" }, `No laps recorded at ${venue.venue} yet — be the first!`));
      try {
        fillClassOptions(await api.catalogClassOptions());
      } catch {
        fillSelect(classSelect, [], "Couldn't load classes");
      }
      fillSelect(carSelect, [], "No cars yet");
      return;
    }
    if (venue.layouts.length > 1) {
      layoutSelect.innerHTML = "";
      const busiest = [...venue.layouts].sort((a, b) => b.lap_count - a.lap_count)[0];
      venue.layouts.forEach((l) =>
        layoutSelect.appendChild(el("option", { value: l.track_name }, `${l.track_name} · ${lapsLabel(l.lap_count)}`))
      );
      layoutSelect.value = busiest.track_name; // the most-driven variant first
      layoutField.style.display = "";
    }
    currentTrack = venue.layouts.length > 1 ? layoutSelect.value : venue.layouts[0].track_name;
    await onTrackResolved();
  }

  async function onTrackResolved() {
    try {
      const [classOptions, cars] = await Promise.all([api.catalogClassOptions(currentTrack), api.catalogCars(currentTrack)]);
      fillClassOptions(classOptions);
      fillSelect(carSelect, cars, "No cars at this track yet");
    } catch (err) {
      fillSelect(classSelect, [], "Couldn't load classes");
      fillSelect(carSelect, [], "Couldn't load cars");
    }
  }

  let searchTimer = null;
  trackSearch.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadVenues(trackSearch.value.trim()), 250);
  });
  venueSelect.addEventListener("change", onVenueChange);
  layoutSelect.addEventListener("change", async () => {
    currentTrack = layoutSelect.value;
    await onTrackResolved();
  });

  function setMode(next) {
    mode = next;
    classField.style.display = mode === "class" ? "" : "none";
    carField.style.display = mode === "car" ? "" : "none";
    modeToggle.querySelector("#modeClassBtn").classList.toggle("active", mode === "class");
    modeToggle.querySelector("#modeCarBtn").classList.toggle("active", mode === "car");
  }
  setMode("class");

  modeToggle.querySelector("#modeClassBtn").addEventListener("click", () => setMode("class"));
  modeToggle.querySelector("#modeCarBtn").addEventListener("click", () => setMode("car"));

  async function runSearch() {
    const track = currentTrack;
    const key = mode === "class" ? classSelect.value : carSelect.value;
    if (!track || !key) {
      resultsBox.innerHTML = "";
      resultsBox.appendChild(
        el("div", { class: "notice" }, `Pick both a track and a ${mode === "class" ? "class" : "car"} to search.`)
      );
      return;
    }
    searchBtn.disabled = true;
    resultsBox.innerHTML = "";
    try {
      const entries = mode === "class"
        ? await api.classLeaderboard(track, key, 20)
        : await api.carLeaderboard(track, key, 20);
      resultsBox.appendChild(renderTable(entries));
    } catch (err) {
      resultsBox.appendChild(el("div", { class: "notice notice--error" }, err.message));
    } finally {
      searchBtn.disabled = false;
    }
  }

  searchBtn.addEventListener("click", runSearch);

  resultsBox.appendChild(
    el("div", { class: "empty-state" }, "Pick a track and class (or an exact car) to see the standings.")
  );

  await loadVenues("");
}

function renderTable(entries) {
  if (!entries.length) {
    return el("div", { class: "empty-state" }, "No valid laps recorded here yet.");
  }

  const rows = entries.map((entry, i) => {
    const pos = i + 1;
    return el("tr", {}, [
      el("td", {}, el("span", { class: `pos-badge ${pos === 1 ? "p1" : ""}` }, String(pos))),
      el("td", {}, el("a", { href: `#/driver/${entry.driver_id}` }, entry.driver_name)),
      el("td", {}, el("span", { class: "lap-time" }, formatLapTime(entry.lap_time))),
      el(
        "td",
        {},
        (entry.sector_times || []).map((s) => formatLapTime(s)).join(" / ")
      ),
      el(
        "td",
        {},
        el("span", { class: `tag ${entry.is_valid ? "tag--valid" : "tag--invalid"}` }, entry.is_valid ? "valid" : "invalid")
      ),
      el("td", {}, formatDate(entry.uploaded_at)),
    ]);
  });

  const table = el("table", { class: "timing-table" }, [
    el("thead", {}, el("tr", {}, ["#", "Driver", "Lap", "Sectors", "", "Set"].map((h) => el("th", {}, h)))),
    el("tbody", {}, rows),
  ]);

  return table;
}
