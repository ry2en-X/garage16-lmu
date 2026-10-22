// pages/leaderboard.js — public, no auth. Two views (V0.6.3):
//   - Class: GET /leaderboard/class/{track}/{car_class} — main leaderboard,
//     every car within a class (e.g. all Hypercars) competes together.
//   - Car: GET /leaderboard/car/{track}/{car_model} — sub-leaderboard,
//     one exact car model, ignoring team/livery/car number.
//
// V0.8.9: the track picker is a searchable venue list with all layouts (see below).
//
// V0.9.6: nothing to click before you see a table — the busiest track and its busiest class are
// chosen and shown right away, every change of track / layout / class / car reloads the table at
// once (no "Search" button), the choice is kept in the address (#/leaderboard?track=..&class=..)
// so a link opens the same table, and the table shows each driver's gap to P1.
//
// V0.6.7: track/class/car are now dropdowns fed by the catalog endpoints
// (real values discovered from actual uploads — see
// server/routers/leaderboard.py) instead of free-text fields where a
// typo or the wrong internal name (LMU reports the Hypercar class as "Hyper";
// since V0.8.9 the server stores the readable name "Hypercar") silently returned an empty board.

import { api } from "../api.js";
import { el, formatLapTime, formatDate } from "../format.js";

import { t, tp } from "../i18n.js";
export async function renderLeaderboard(container) {
  container.appendChild(el("h1", {}, t("Leaderboard")));
  container.appendChild(el("p", { class: "subtitle" }, t("Each driver's best valid lap, fastest first.")));

  let mode = "class"; // "class" | "car"

  const modeToggle = el("div", { class: "inline-form" }, [
    el("button", { class: "btn btn--ghost", id: "modeClassBtn" }, t("By class")),
    el("button", { class: "btn btn--ghost", id: "modeCarBtn" }, t("By exact car")),
  ]);
  container.appendChild(modeToggle);

  // V0.8.9: search-first track picker. Every track is listed (also ones nobody
  // has driven yet), grouped by venue with ALL its layouts, and every class is
  // offered — nothing has to be entered by an admin; new tracks/layouts/classes
  // appear by themselves once LMU sends them (see server/catalog.py).
  const form = el("div", { class: "inline-form section" });

  const trackSearch = el("input", { id: "trackSearch", type: "search", placeholder: t("Search a track, e.g. Fuji, Le Mans, Spa…"), autocomplete: "off" });
  const searchField = el("div", { class: "field" }, [el("label", { for: "trackSearch" }, t("Find a track")), trackSearch]);

  const venueSelect = el("select", { id: "venueSelect" }, [el("option", { value: "" }, t("Loading tracks…"))]);
  const venueField = el("div", { class: "field" }, [el("label", { for: "venueSelect" }, t("Track")), venueSelect]);

  const layoutSelect = el("select", { id: "layoutSelect" });
  const layoutField = el("div", { class: "field", style: "display:none;" }, [el("label", { for: "layoutSelect" }, t("Layout")), layoutSelect]);

  const classSelect = el("select", { id: "classSelect" }, [el("option", { value: "" }, t("Pick a track first"))]);
  const classField = el("div", { class: "field" }, [el("label", { for: "classSelect" }, t("Class")), classSelect]);

  const carSelect = el("select", { id: "carSelect" }, [el("option", { value: "" }, t("Pick a track first"))]);
  const carField = el("div", { class: "field", style: "display:none;" }, [el("label", { for: "carSelect" }, t("Car")), carSelect]);

  [searchField, venueField, layoutField, classField, carField].forEach((node) => form.appendChild(node));
  container.appendChild(form);

  const hintBox = el("div");
  container.appendChild(hintBox);
  const resultsBox = el("div");
  container.appendChild(resultsBox);

  let venues = [];
  let currentTrack = ""; // the exact track string LMU sent for the chosen layout
  // what the address asks for (a shared link), used once
  const wanted = (() => {
    const q = new URLSearchParams((window.location.hash.split("?")[1]) || "");
    return { track: q.get("track"), cls: q.get("class"), car: q.get("car") };
  })();
  let firstLoad = true;

  function lapsLabel(n) {
    return n ? tp("{0} lap", "{0} laps", n) : t("no laps yet");
  }

  function fillSelect(select, values, placeholder) {
    select.innerHTML = "";
    if (!values.length) {
      select.appendChild(el("option", { value: "" }, placeholder));
      select.disabled = true;
      return;
    }
    select.disabled = false;
    select.appendChild(el("option", { value: "" }, t("Choose…")));
    values.forEach((v) => select.appendChild(el("option", { value: v }, v)));
  }

  function fillClassOptions(options) {
    classSelect.innerHTML = "";
    classSelect.disabled = false;
    classSelect.appendChild(el("option", { value: "" }, t("Choose…")));
    options.forEach((o) => classSelect.appendChild(el("option", { value: o.name }, `${o.name} · ${lapsLabel(o.lap_count)}`)));
    // V0.9.6: the class asked for in the address, else the one with the most laps
    const busiest = [...options].filter((o) => o.lap_count > 0).sort((a, b) => b.lap_count - a.lap_count)[0];
    const fromUrl = firstLoad && wanted.cls && options.some((o) => o.name === wanted.cls) ? wanted.cls : null;
    if (fromUrl || busiest) classSelect.value = fromUrl || busiest.name;
  }

  async function loadVenues(q) {
    const previous = venueSelect.value;
    try {
      venues = await api.catalogVenues(q);
    } catch (err) {
      venues = [];
      venueSelect.innerHTML = "";
      venueSelect.appendChild(el("option", { value: "" }, t("Couldn't load tracks")));
      return;
    }
    venueSelect.innerHTML = "";
    if (!venues.length) {
      venueSelect.appendChild(el("option", { value: "" }, q ? t("No track matches \"{0}\"", [q]) : t("No tracks yet")));
      venueSelect.disabled = true;
      await onVenueChange();
      return;
    }
    venueSelect.disabled = false;
    venueSelect.appendChild(el("option", { value: "" }, t("Choose a track…")));
    venues.forEach((v) => venueSelect.appendChild(el("option", { value: v.venue }, `${v.venue} · ${lapsLabel(v.lap_count)}`)));
    const urlVenue = firstLoad && wanted.track ? venues.find((v) => v.layouts.some((l) => l.track_name === wanted.track)) : null;
    if (previous && venues.some((v) => v.venue === previous)) {
      venueSelect.value = previous;
    } else if (q && venues.length === 1) {
      venueSelect.value = venues[0].venue; // one match: no need to make the user click it
    } else if (!q && urlVenue) {
      venueSelect.value = urlVenue.venue;          // V0.9.6: a shared link
    } else if (!q && !previous) {
      // V0.9.6: start with the busiest track instead of an empty page
      const busiest = [...venues].filter((v) => v.lap_count > 0).sort((a, b) => b.lap_count - a.lap_count)[0];
      if (busiest) venueSelect.value = busiest.venue;
    }
    await onVenueChange();
  }

  async function onVenueChange() {
    hintBox.innerHTML = "";
    const venue = venues.find((v) => v.venue === venueSelect.value);
    layoutField.style.display = "none";
    currentTrack = "";
    if (!venue) {
      fillSelect(classSelect, [], t("Pick a track first"));
      fillSelect(carSelect, [], t("Pick a track first"));
      return;
    }
    if (venue.layouts.length === 0) {
      // A known track nobody has driven yet — still selectable, with every class.
      hintBox.appendChild(el("div", { class: "notice" }, t("No laps recorded at {0} yet — be the first!", [venue.venue])));
      try {
        fillClassOptions(await api.catalogClassOptions());
      } catch {
        fillSelect(classSelect, [], t("Couldn't load classes"));
      }
      fillSelect(carSelect, [], t("No cars yet"));
      return;
    }
    if (venue.layouts.length > 1) {
      layoutSelect.innerHTML = "";
      const busiest = [...venue.layouts].sort((a, b) => b.lap_count - a.lap_count)[0];
      venue.layouts.forEach((l) =>
        layoutSelect.appendChild(el("option", { value: l.track_name }, `${l.track_name} · ${lapsLabel(l.lap_count)}`))
      );
      layoutSelect.value = busiest.track_name; // the most-driven variant first
      if (firstLoad && wanted.track && venue.layouts.some((l) => l.track_name === wanted.track)) layoutSelect.value = wanted.track;
      layoutField.style.display = "";
    }
    currentTrack = venue.layouts.length > 1 ? layoutSelect.value : venue.layouts[0].track_name;
    await onTrackResolved();
  }

  async function onTrackResolved() {
    try {
      const [classOptions, cars] = await Promise.all([api.catalogClassOptions(currentTrack), api.catalogCars(currentTrack)]);
      fillClassOptions(classOptions);
      fillSelect(carSelect, cars, t("No cars at this track yet"));
      if (cars.length) carSelect.value = firstLoad && wanted.car && cars.includes(wanted.car) ? wanted.car : cars[0];
      if (firstLoad && wanted.car && cars.includes(wanted.car)) setMode("car");
    } catch (err) {
      fillSelect(classSelect, [], t("Couldn't load classes"));
      fillSelect(carSelect, [], t("Couldn't load cars"));
    }
    firstLoad = false;
    await runSearch();
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
  classSelect.addEventListener("change", () => runSearch());
  carSelect.addEventListener("change", () => runSearch());

  function setMode(next) {
    mode = next;
    classField.style.display = mode === "class" ? "" : "none";
    carField.style.display = mode === "car" ? "" : "none";
    modeToggle.querySelector("#modeClassBtn").classList.toggle("active", mode === "class");
    modeToggle.querySelector("#modeCarBtn").classList.toggle("active", mode === "car");
  }
  setMode("class");

  modeToggle.querySelector("#modeClassBtn").addEventListener("click", () => { setMode("class"); runSearch(); });
  modeToggle.querySelector("#modeCarBtn").addEventListener("click", () => { setMode("car"); runSearch(); });

  async function runSearch() {
    const track = currentTrack;
    const key = mode === "class" ? classSelect.value : carSelect.value;
    if (!track || !key) {
      resultsBox.innerHTML = "";
      resultsBox.appendChild(
        el("div", { class: "empty-state" }, mode === "class" ? t("Pick a track and class (or an exact car) to see the standings.") : t("Pick both a track and a car to search."))
      );
      return;
    }
    const seq = ++searchSeq;
    resultsBox.setAttribute("aria-busy", "true");
    try {
      const entries = mode === "class"
        ? await api.classLeaderboard(track, key, 20)
        : await api.carLeaderboard(track, key, 20);
      if (seq !== searchSeq) return;   // a newer choice is already loading
      resultsBox.innerHTML = "";
      resultsBox.appendChild(el("h2", { class: "board-title" }, `${track} · ${key}`));
      resultsBox.appendChild(renderTable(entries));
      // V0.9.6: keep the choice in the address (a link opens this table); no re-render
      const params = new URLSearchParams({ track, [mode === "class" ? "class" : "car"]: key });
      if (window.history && window.history.replaceState) window.history.replaceState(null, "", `#/leaderboard?${params.toString()}`);
    } catch (err) {
      if (seq !== searchSeq) return;
      resultsBox.innerHTML = "";
      resultsBox.appendChild(el("div", { class: "notice notice--error" }, err.message));
    } finally {
      if (seq === searchSeq) resultsBox.removeAttribute("aria-busy");
    }
  }
  let searchSeq = 0;

  resultsBox.appendChild(
    el("div", { class: "empty-state" }, t("Pick a track and class (or an exact car) to see the standings."))
  );

  await loadVenues("");
}

function renderTable(entries) {
  if (!entries.length) {
    return el("div", { class: "empty-state" }, t("No valid laps recorded here yet."));
  }

  const leader = entries[0].lap_time;
  const rows = entries.map((entry, i) => {
    const pos = i + 1;
    return el("tr", {}, [
      el("td", {}, el("span", { class: `pos-badge ${pos === 1 ? "p1" : ""}` }, String(pos))),
      el("td", {}, el("a", { href: `#/driver/${entry.driver_id}` }, entry.driver_name)),
      el("td", {}, el("span", { class: "lap-time" }, formatLapTime(entry.lap_time))),
      // V0.9.6: the gap to P1, and the sectors in seconds (not "0:29.425")
      el("td", { class: "lap-gap" }, pos === 1 ? "" : `+${(entry.lap_time - leader).toFixed(3)}`),
      el(
        "td",
        { class: "sector-cells" },
        (entry.sector_times || []).map((s) => (s > 0 ? s.toFixed(3) : "—")).join(" · ")
      ),
      el(
        "td",
        {},
        el("span", { class: `tag ${entry.is_valid ? "tag--valid" : "tag--invalid"}` }, entry.is_valid ? t("valid") : t("invalid"))
      ),
      el("td", {}, formatDate(entry.uploaded_at)),
      // V0.9.1: open this exact lap in the analysis as Lap 1, then "Runde vergleichen" picks what to compare it with.
      el("td", {}, entry.lap_id != null ? el("a", { href: `#/analysis?a=${entry.lap_id}` }, t("Analyse")) : ""),
    ]);
  });

  const table = el("table", { class: "timing-table" }, [
    el("thead", {}, el("tr", {}, ["#", t("Driver"), t("Lap"), t("Gap"), t("Sectors"), "", t("Set"), ""].map((h) => el("th", {}, h)))),
    el("tbody", {}, rows),
  ]);

  return table;
}
