// tracks.js — one name per track on every page (V1.0.1).
//
// LMU reports its own track strings ("Algarve International Circuit", "Autodromo Nazionale Monza");
// the leaderboard groups them into venues with readable names ("Portimão (Algarve)", "Monza" —
// server/catalog.py). Before V1.0.1 the dashboard, the driver profile, the activity feeds and the
// analysis showed the raw LMU string while the leaderboard showed the venue name, so the same track
// had two names. Now every page shows the venue name — as long as the venue has exactly one layout.
// A venue with several layouts (e.g. Silverstone GP and National) keeps the LMU string, because only
// that says which layout it was.
//
// The mapping comes from GET /leaderboard/catalog/venues, loaded once per page load. trackLabel()
// never waits and never fails: before the list has arrived (or if it can't be loaded) it returns
// the LMU string unchanged, so a page is never blocked or broken by it.

import { api } from "./api.js";

let labels = new Map();
let pending = null;

export function loadTrackNames() {
  if (!pending) {
    pending = Promise.resolve()
      .then(() => api.catalogVenues())
      .then((venues) => {
        const next = new Map();
        for (const venue of Array.isArray(venues) ? venues : []) {
          const layouts = Array.isArray(venue.layouts) ? venue.layouts : [];
          if (layouts.length === 1 && layouts[0].track_name) next.set(layouts[0].track_name, venue.venue);
        }
        labels = next;
      })
      .catch(() => {
        pending = null; // try again on the next page
      });
  }
  return pending;
}

export function trackLabel(raw) {
  if (!raw) return "";
  return labels.get(raw) || raw;
}

// for tests
export function _resetTrackNames() {
  labels = new Map();
  pending = null;
}
