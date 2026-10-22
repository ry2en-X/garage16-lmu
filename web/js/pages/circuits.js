// pages/circuits.js — circuit library (V0.9.0): every track as a card, built entirely
// from uploads (plus the seeded venue list, see server/catalog.py) — nobody enters a
// track by hand. A card shows the layouts LMU has sent, lap/driver counts, the
// classes driven there and the fastest valid lap; the outline is drawn from that
// fastest lap's own position data, so a new track gets a map by itself.
//
// Outlines need a signed-in driver (they read a telemetry file); without one the
// cards still show everything else.

import { api } from "../api.js";
import { store } from "../state.js";
import { el, formatLapTime } from "../format.js";
import { cssVar, fitCanvas } from "../analysis/charts.js";
import { fitView, formatMetres } from "../analysis/model.js";

import { t, tp } from "../i18n.js";
const OUTLINE_HEIGHT = 170;

export function drawOutline(canvas, outline) {
  const fit = fitCanvas(canvas, OUTLINE_HEIGHT);
  if (!fit) return false;
  const { ctx, w, h } = fit;
  const view = fitView(outline.x, outline.y, w, h, 14);
  if (!view) return false;
  ctx.clearRect(0, 0, w, h);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  const path = () => {
    ctx.beginPath();
    let pen = false;
    for (let i = 0; i < outline.x.length; i++) {
      if (outline.x[i] === null || outline.y[i] === null) {
        pen = false;
        continue;
      }
      const x = view.toX(outline.x[i]);
      const y = view.toY(outline.y[i]);
      if (!pen) {
        ctx.moveTo(x, y);
        pen = true;
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.stroke();
  };
  ctx.strokeStyle = cssVar("--hairline", "#2C3136");
  ctx.lineWidth = 7;
  path();
  ctx.strokeStyle = cssVar("--text", "#ECEDEE");
  ctx.lineWidth = 2.2;
  path();
  const mark = (index, color) => {
    if (outline.x[index] === null || outline.x[index] === undefined) return;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(view.toX(outline.x[index]), view.toY(outline.y[index]), 4.5, 0, Math.PI * 2);
    ctx.fill();
  };
  mark(0, cssVar("--signal", "#E0472B"));
  const { start, step } = outline.grid;
  for (const distance of outline.sectors || []) {
    mark(Math.min(Math.max(Math.round((distance - start) / step), 0), outline.x.length - 1), cssVar("--gold", "#D8A83E"));
  }
  return true;
}

function layoutBlock(layout, observer) {
  const signedIn = store.isSignedIn();
  const canvas = el("canvas", { class: "circuit__canvas", "aria-label": t("Outline of {0}", [layout.track_name]) });
  const outlineBox = el("div", { class: "circuit__outline" }, canvas);
  const meta = el("div", { class: "circuit__meta" });
  const hasLaps = layout.lap_count > 0;

  // V0.9.1: "My Best" on this layout (loaded together with the outline, i.e. only when
  // the card is seen, and only for a signed-in driver — it is their own data).
  const mine = el("div", { class: "circuit__mine subtitle", style: "margin:4px 0 0;" });
  async function loadMine() {
    const session = store.getSession();
    if (!session || session.driverId == null) return;
    try {
      const res = await api.analysisDriverLaps(session.driverId, { trackName: layout.track_name, limit: 20 });
      const best = (res.laps || []).find((lap) => lap.is_valid);
      mine.textContent = "";
      if (best) {
        mine.appendChild(document.createTextNode(t("My Best ")));
        mine.appendChild(el("span", { class: "lap-time" }, formatLapTime(best.lap_time)));
        mine.appendChild(document.createTextNode(` · ${best.car_model || best.car_name || ""}`));
      } else {
        mine.textContent = t("You have no analysable lap on this layout yet.");
      }
    } catch {
      mine.textContent = "";
    }
  }

  let loaded = false;
  async function loadOutline() {
    if (loaded || !hasLaps || !signedIn) return;
    loaded = true;
    loadMine();
    try {
      const outline = await api.analysisOutline(layout.track_name);
      if (!drawOutline(canvas, outline)) throw new Error(t("Cannot draw here."));
      meta.prepend(el("span", { class: "tag" }, formatMetres(outline.length_m)));
    } catch (err) {
      loaded = false; // allow a retry when it scrolls into view again
      outlineBox.innerHTML = "";
      outlineBox.appendChild(el("div", { class: "subtitle", style: "margin:0;" }, err.status === 404 ? t("No outline yet.") : err.message));
    }
  }

  const children = [el("div", { class: "circuit__name" }, layout.track_name)];
  if (!hasLaps) {
    children.push(el("div", { class: "subtitle", style: "margin:0;" }, t("No laps yet — be the first!")));
  } else {
    if (signedIn) children.push(outlineBox);
    meta.appendChild(el("span", { class: "tag" }, tp("{0} lap", "{0} laps", layout.lap_count)));
    meta.appendChild(el("span", { class: "tag" }, tp("{0} driver", "{0} drivers", layout.driver_count)));
    for (const cls of layout.classes) meta.appendChild(el("span", { class: "tag" }, cls));
    children.push(meta);
    const f = layout.fastest;
    if (f) {
      children.push(el("div", { class: "circuit__fastest" }, [
        el("span", { class: "subtitle", style: "margin:0;" }, t("Fastest ")),
        el("span", { class: "lap-time" }, formatLapTime(f.lap_time)),
        el("span", { class: "subtitle", style: "margin:0;" }, ` ${f.driver_name} · ${f.car_model || ""}`),
      ]));
      if (signedIn) {
        children.push(mine);
        // V0.9.1: the server pairs my best valid lap here with the fastest lap for my car.
        children.push(el("a", { class: "btn btn--ghost circuit__action", href: `#/analysis?quick=wr&track=${encodeURIComponent(layout.track_name)}` }, t("Compare my lap with it")));
      }
    }
    if (!signedIn) children.push(el("div", { class: "subtitle", style: "margin:8px 0 0;" }, t("Sign in to see the outline and compare laps.")));
  }
  const block = el("div", { class: "circuit__layout" }, children);
  if (hasLaps && signedIn) {
    if (observer) observer.observe(block, loadOutline);
    else loadOutline();
  }
  return block;
}

export async function renderCircuits(container) {
  container.classList.add("panel--wide");
  container.appendChild(el("h1", {}, t("Circuits")));
  container.appendChild(el("p", { class: "subtitle" }, t("Every track with all its layouts. Cards and outlines are built from the laps drivers upload — nothing is entered by hand.")));

  const search = el("input", { id: "circuitSearch", type: "search", placeholder: t("Search a track, e.g. Fuji, Le Mans, Spa…"), autocomplete: "off" });
  container.appendChild(el("div", { class: "inline-form section" }, el("div", { class: "field" }, [el("label", { for: "circuitSearch" }, t("Find a track")), search])));
  const list = el("div", { class: "circuits" });
  container.appendChild(list);

  // Outlines load when their card scrolls into view (a long list is not 40 requests at once).
  const callbacks = new WeakMap();
  const io = typeof IntersectionObserver === "function"
    ? new IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const cb = callbacks.get(entry.target);
          if (cb) cb();
          io.unobserve(entry.target);
        }
      }, { rootMargin: "200px" })
    : null;
  const observer = io ? { observe: (node, cb) => { callbacks.set(node, cb); io.observe(node); } } : null;

  async function load(q) {
    list.innerHTML = "";
    list.appendChild(el("div", { class: "notice" }, t("Loading…")));
    let venues;
    try {
      venues = (await api.analysisCircuits(q || undefined)).venues;
    } catch (err) {
      list.innerHTML = "";
      list.appendChild(el("div", { class: "notice notice--error" }, err.message));
      return;
    }
    list.innerHTML = "";
    if (!venues.length) {
      list.appendChild(el("div", { class: "empty-state" }, q ? t("No track matches \"{0}\".", [q]) : t("No tracks yet.")));
      return;
    }
    // Tracks with laps as cards (busiest first); V0.9.6: the not-yet-driven ones as one compact
    // list underneath instead of a dozen empty cards.
    const driven = venues.filter((v) => v.layouts.length).sort((x, y) => y.lap_count - x.lap_count || x.venue.localeCompare(y.venue));
    const empty = venues.filter((v) => !v.layouts.length).sort((x, y) => x.venue.localeCompare(y.venue));
    for (const venue of driven) {
      list.appendChild(el("div", { class: "card circuit" }, [
        el("h2", {}, venue.venue),
        el("div", { class: "circuit__layouts" }, venue.layouts.map((layout) => layoutBlock(layout, observer))),
      ]));
    }
    if (empty.length) {
      list.appendChild(el("div", { class: "card circuit circuit--undriven" }, [
        el("h2", {}, t("Not driven yet")),
        el("p", { class: "subtitle", style: "margin:0 0 8px;" }, t("No laps yet — be the first!")),
        el("div", { class: "circuit__names" }, empty.map((v) => el("span", { class: "tag" }, v.venue))),
      ]));
    }
  }

  let timer = null;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => load(search.value.trim()), 250);
  });
  await load("");
}
