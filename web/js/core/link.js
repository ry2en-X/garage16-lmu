// core/link.js — every link of the website is built here, so the readable addresses
// exist in one place only:
//
//   link.leaderboard("Fuji Speedway", "Hypercar")   /leaderboard/fuji-speedway/hypercar
//   link.leaderboard("Fuji Speedway", "Porsche 963") /leaderboard/fuji-speedway/porsche-963
//   link.driver(2, "Moritz Lachnit")                 /driver/moritz-lachnit
//   link.team(5, "HyperMotion Esports")              /team/hypermotion-esports
//   link.analysis({ a: 123, b: 456 })                /analysis/123/vs/456
//   link.analysis({ quick: "wr", scope: "class", track: "Fuji Speedway", model: "Porsche 963" })
//                                                    /analysis/fuji-speedway/porsche-963/vs/class-best
//   link.stints({ track: "Fuji Speedway", date: "2026-11-14" })  /stints/fuji-speedway/2026-11-14
//   link.legal("privacy")                            /datenschutz
//
// core/routes.js reads the same addresses back. slugify() is the browser twin of
// server/core/slugs.py (tests check both give the same answer).

export function slugify(text) {
  const folded = String(text ?? "").toLocaleLowerCase("en").normalize("NFKD").replace(/ß/g, "ss");
  let out = "";
  let dash = false;
  for (const ch of folded) {
    if (/\p{Mn}/u.test(ch)) continue;                // combining accent of the letter before
    if (/[\p{L}\p{N}]/u.test(ch)) {
      out += ch;
      dash = false;
    } else if (!dash && out) {
      out += "-";
      dash = true;
    }
  }
  return out.replace(/^-+|-+$/g, "").slice(0, 80).replace(/-+$/, "");
}

// a name with no letter or digit has no slug: its link uses the number
const ref = (id, name) => slugify(name) || String(id);

// the address's names for the lap a comparison goes against (?quick= / ?ref=)
export const REFERENCE_SLUGS = { wr: "garage16-best", team_best: "team-best", my_best: "my-best" };
export const CLASS_BEST = "class-best";
export const LEGAL_PATHS = { imprint: "/impressum", privacy: "/datenschutz", terms: "/terms", licenses: "/licenses" };

function withQuery(path, query) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query || {})) {
    if (value !== undefined && value !== null && value !== "" && value !== false) params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export const link = {
  home: () => "/",
  page: (name, query) => withQuery(`/${name}`, query),

  leaderboard(track, key) {
    if (!track) return "/leaderboard";
    return key ? `/leaderboard/${slugify(track)}/${slugify(key)}` : `/leaderboard/${slugify(track)}`;
  },

  driver: (id, name) => `/driver/${ref(id, name)}`,
  team: (id, name) => `/team/${ref(id, name)}`,

  // { a, b, c, d, quick, ref, track, model, scope, note } — the analysis page's own parameters
  analysis(p = {}) {
    const extra = { c: p.c, d: p.d, note: p.note };
    if (p.a) {
      if (p.b) return withQuery(`/analysis/${p.a}/vs/${p.b}`, extra);
      if (p.ref) return withQuery(`/analysis/${p.a}/vs/${REFERENCE_SLUGS[p.ref] || p.ref}`, extra);
      return withQuery(`/analysis/${p.a}`, extra);
    }
    if (p.b) return withQuery("/analysis", { b: p.b, ...extra });   // only Lap 2 chosen: pick Lap 1
    if (p.quick) {
      const against = p.scope === "class" && p.quick === "wr" ? CLASS_BEST : (REFERENCE_SLUGS[p.quick] || p.quick);
      const where = [p.track && slugify(p.track), p.track && p.model && slugify(p.model)].filter(Boolean);
      return withQuery(`/analysis/${[...where, "vs", against].join("/")}`, { note: p.note });
    }
    return "/analysis";
  },

  // { lap } one session by a lap of it, { track, date[, n] } a session by place and day, { driver } a
  // team mate's sessions, { compare } a lap compared in the session
  stints(p = {}) {
    const extra = { driver: p.driver, compare: p.compare };
    if (p.lap) return withQuery(`/stints/lap/${p.lap}`, extra);
    if (p.track && p.date) return withQuery(`/stints/${slugify(p.track)}/${p.date}${p.n > 1 ? `-${p.n}` : ""}`, extra);
    return withQuery("/stints", extra);
  },

  legal: (tab) => LEGAL_PATHS[tab] || LEGAL_PATHS.imprint,

  // the full address of a link, for "Copy link"
  absolute: (path) => new URL(path, window.location.origin).toString(),
};
