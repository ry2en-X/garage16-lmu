// core/routes.js — every page of the website in one table: its address, its title, the
// module that draws it, whether it needs a signed-in driver, and the menu entry it belongs to.
//
// Addresses are readable paths (/leaderboard/fuji-speedway/hypercar, /driver/moritz-lachnit); the
// links themselves are built by core/link.js. A route's `query` turns the address into the
// parameters its page reads (routeQuery() in core/router.js) — names instead of slugs, ids of
// drivers and teams — so the pages never parse the address themselves.
//
// server/api/v1/pages.py knows the same pages (titles for link previews; tests compare the two).

import { api } from "./api.js";
import { CLASS_BEST, REFERENCE_SLUGS, slugify } from "./link.js";
import { t } from "./i18n.js";

const mod = (path, name) => () => import(path).then((m) => m[name]);

// --- turning slugs back into names (the catalog answers are cached by the browser and Cloudflare)

let trackNames = null;
async function trackForSlug(slug) {
  if (!slug) return null;
  try {
    trackNames = trackNames || (await api.catalogTracks());
  } catch {
    return null;
  }
  return trackNames.find((name) => slugify(name) === slug) || null;
}

async function classOrModelForSlug(track, slug) {
  if (!slug) return {};
  try {
    const [classes, cars] = await Promise.all([api.catalogClassOptions(track || undefined), track ? api.catalogCars(track) : []]);
    const cls = classes.find((c) => slugify(c.name) === slug);
    if (cls) return { cls: cls.name };
    const car = cars.find((c) => slugify(c) === slug);
    return car ? { car } : {};
  } catch {
    return {};
  }
}

const AGAINST = Object.fromEntries(Object.entries(REFERENCE_SLUGS).map(([key, slug]) => [slug, key]));

async function quickQuery({ track, model, against }, search) {
  const q = new URLSearchParams(search);
  const trackName = await trackForSlug(track);
  if (trackName) q.set("track", trackName);
  if (model && trackName) {
    const found = await classOrModelForSlug(trackName, model);
    if (found.car) q.set("model", found.car);
  }
  if (against === CLASS_BEST) {
    q.set("quick", "wr");
    q.set("scope", "class");
  } else {
    q.set("quick", AGAINST[against] || "wr");
  }
  return q;
}

async function stintsByDay({ track, date }, search) {
  const q = new URLSearchParams(search);
  const m = /^(\d{4}-\d{2}-\d{2})(?:-(\d+))?$/.exec(date || "");
  const trackName = await trackForSlug(track);
  if (!m || !trackName) return q;
  try {
    const data = await api.stintSessions({ trackName, limit: 50, driverId: q.get("driver") || undefined });
    const sameDay = (data.sessions || []).filter((s) => String(s.started || "").slice(0, 10) === m[1]).reverse();
    const session = sameDay[(Number(m[2]) || 1) - 1];
    if (session) q.set("lap", String(session.key_lap_id));
  } catch {
    /* the page shows the session list */
  }
  return q;
}

// pattern segments: "name" literal, ":x" any segment, ":x#" digits only
export const ROUTES = [
  { name: "landing", paths: ["/"], load: mod("../pages/landing.js", "renderLanding"), title: null },
  { name: "dashboard", paths: ["/dashboard"], load: mod("../pages/dashboard.js", "renderDashboard"), title: () => t("Dashboard"), auth: true },
  { name: "teams", paths: ["/teams"], load: mod("../pages/teams.js", "renderTeams"), title: () => t("Teams"), auth: true },
  {
    name: "team", paths: ["/team/:slug"], load: mod("../pages/team_detail.js", "renderTeamDetail"), title: () => t("Team"), auth: true, nav: "teams",
    resolve: async ({ slug }) => {
      const found = await api.teamBySlug(slug);
      return { arg: String(found.team_id), canonical: found.renamed ? `/team/${found.slug}` : null };
    },
  },
  {
    name: "driver", paths: ["/driver/:slug"], load: mod("../pages/driver.js", "renderDriverProfile"), title: () => t("Driver"),
    resolve: async ({ slug }) => {
      const found = await api.driverBySlug(slug);
      return { arg: String(found.driver_id), canonical: found.renamed ? `/driver/${found.slug}` : null };
    },
  },
  {
    name: "analysis", load: mod("../pages/analysis.js", "renderAnalysis"), title: () => t("LapTime Analyzer"), auth: true,
    paths: ["/analysis", "/analysis/:a#", "/analysis/:a#/vs/:b", "/analysis/vs/:against", "/analysis/:track/vs/:against",
      "/analysis/:track/:model/vs/:against"],
    query: async (p, search) => {
      if (p.a) {
        const q = new URLSearchParams(search);
        q.set("a", p.a);
        if (/^\d+$/.test(p.b || "")) q.set("b", p.b);
        else if (p.b && AGAINST[p.b]) q.set("ref", AGAINST[p.b]);
        return q;
      }
      if (p.against) return quickQuery(p, search);
      return new URLSearchParams(search);
    },
  },
  {
    name: "stints", load: mod("../pages/stints.js", "renderStints"), title: () => t("Stint Analyzer"), auth: true,
    paths: ["/stints", "/stints/lap/:lap#", "/stints/:track/:date"],
    query: async (p, search) => {
      if (p.lap) {
        const q = new URLSearchParams(search);
        q.set("lap", p.lap);
        return q;
      }
      return p.track ? stintsByDay(p, search) : new URLSearchParams(search);
    },
  },
  {
    name: "leaderboard", load: mod("../pages/leaderboard.js", "renderLeaderboard"), title: () => t("Leaderboard"),
    paths: ["/leaderboard", "/leaderboard/:track", "/leaderboard/:track/:key"],
    query: async (p, search) => {
      const q = new URLSearchParams(search);
      const track = await trackForSlug(p.track);
      if (track) q.set("track", track);
      if (track && p.key) {
        const found = await classOrModelForSlug(track, p.key);
        if (found.cls) q.set("class", found.cls);
        if (found.car) q.set("car", found.car);
      }
      return q;
    },
  },
  { name: "circuits", paths: ["/circuits"], load: mod("../pages/circuits.js", "renderCircuits"), title: () => t("Circuits") },
  { name: "account", paths: ["/account"], load: mod("../pages/account.js", "renderAccount"), title: () => t("Account") },
  { name: "download", paths: ["/download"], load: mod("../pages/download.js", "renderDownload"), title: () => t("Download") },
  { name: "setup", paths: ["/setup"], load: mod("../pages/setup.js", "renderSetup"), title: () => t("Setup guide") },
  { name: "admin", paths: ["/admin"], load: mod("../pages/admin.js", "renderAdmin"), title: () => t("Admin") },
  { name: "imprint", paths: ["/impressum"], load: mod("../pages/legal.js", "renderLegal"), title: () => t("Legal notice"), nav: "legal", tab: "imprint" },
  { name: "privacy", paths: ["/datenschutz"], load: mod("../pages/legal.js", "renderLegal"), title: () => t("Privacy policy"), nav: "legal", tab: "privacy" },
  { name: "terms", paths: ["/terms"], load: mod("../pages/legal.js", "renderLegal"), title: () => t("Terms of use"), nav: "legal", tab: "terms" },
  { name: "licenses", paths: ["/licenses"], load: mod("../pages/legal.js", "renderLegal"), title: () => t("Licences"), nav: "legal", tab: "licenses" },
  { name: "reset-password", paths: ["/reset-password"], load: mod("../pages/reset_password.js", "renderResetPassword"), title: () => t("Reset password"), tokenArg: true },
  { name: "verify-email", paths: ["/verify-email"], load: mod("../pages/verify_email.js", "renderVerifyEmail"), title: () => t("Confirm email"), tokenArg: true },
];

// The parameters an address gives without asking the server (ids in the path) — for a page drawn
// without the router (routeQuery() before the first render). Slugs stay unresolved here.
export function syncQuery(route, params, search) {
  const q = new URLSearchParams(search);
  if (route.name === "analysis" && params.a) {
    q.set("a", params.a);
    if (/^\d+$/.test(params.b || "")) q.set("b", params.b);
    else if (params.b && AGAINST[params.b]) q.set("ref", AGAINST[params.b]);
  }
  if (route.name === "stints" && params.lap) q.set("lap", params.lap);
  return q;
}

// addresses that moved: the old name opens the new page (the address bar is corrected)
export const MOVED = { "/legal": "/impressum", "/sessions": "/stints" };

function matchPattern(pattern, path) {
  const want = pattern.split("/").filter(Boolean);
  const got = path.split("/").filter(Boolean);
  if (want.length !== got.length) return null;
  const params = {};
  for (let i = 0; i < want.length; i += 1) {
    const w = want[i];
    let g;
    try {
      g = decodeURIComponent(got[i]);
    } catch {
      return null;
    }
    if (w.startsWith(":")) {
      const digits = w.endsWith("#");
      if (digits && !/^\d+$/.test(g)) return null;
      params[w.slice(1).replace(/#$/, "")] = g;
    } else if (w !== g) {
      return null;
    }
  }
  return params;
}

// { route, params } for a path, or null (the "page not found" page)
export function matchRoute(path) {
  const clean = "/" + path.split("/").filter(Boolean).join("/");
  for (const route of ROUTES) {
    for (const pattern of route.paths) {
      const params = matchPattern(pattern, clean);
      if (params) return { route, params };
    }
  }
  return null;
}

// ---------------------------------------------------------------------------- the old #/ links

// "#/leaderboard?track=Fuji%20Speedway&class=Hypercar" -> "/leaderboard/fuji-speedway/hypercar".
// Bookmarks, Discord posts and the desktop clients 1.0.1 and older still use these.
export function legacyHashToPath(hash) {
  const raw = String(hash || "").replace(/^#\/?/, "");
  const [pathPart, queryPart = ""] = raw.split("?");
  const q = new URLSearchParams(queryPart);
  const path = pathPart.replace(/\/+$/, "");
  const keep = (names) => {
    const out = new URLSearchParams();
    for (const n of names) if (q.get(n)) out.set(n, q.get(n));
    const s = out.toString();
    return s ? `?${s}` : "";
  };
  const int = (v) => (/^\d+$/.test(v || "") ? v : null);
  if (!path) return "/";
  let m;
  if ((m = /^driver\/(\d+)$/.exec(path))) return `/driver/${m[1]}`;
  if ((m = /^team\/(\d+)$/.exec(path))) return `/team/${m[1]}`;
  if (path === "leaderboard") {
    const track = q.get("track");
    const key = q.get("class") || q.get("car");
    if (!track) return "/leaderboard";
    return key ? `/leaderboard/${slugify(track)}/${slugify(key)}` : `/leaderboard/${slugify(track)}`;
  }
  if (path === "analysis") {
    const a = int(q.get("a")) || int(q.get("lap"));
    const b = int(q.get("b")) || int(q.get("ref"));   // the spec of V1.0.2: ?lap=123&ref=456
    const extra = keep(["c", "d", "note"]);
    if (a && b) return `/analysis/${a}/vs/${b}${extra}`;
    if (a && q.get("ref") && REFERENCE_SLUGS[q.get("ref")]) return `/analysis/${a}/vs/${REFERENCE_SLUGS[q.get("ref")]}${extra}`;
    if (a) return `/analysis/${a}${extra}`;
    if (b) return `/analysis?b=${b}`;     // only Lap 2 chosen: the page asks for Lap 1
    const quick = q.get("quick");
    if (quick) {
      const against = quick === "wr" && q.get("scope") === "class" ? CLASS_BEST : (REFERENCE_SLUGS[quick] || "garage16-best");
      const where = [q.get("track") && slugify(q.get("track")), q.get("track") && q.get("model") && slugify(q.get("model"))].filter(Boolean);
      return `/analysis/${[...where, "vs", against].join("/")}${keep(["note"])}`;
    }
    return "/analysis";
  }
  if (path === "stints" || path === "sessions") {
    const lap = int(q.get("lap"));
    return lap ? `/stints/lap/${lap}${keep(["compare", "driver"])}` : `/stints${keep(["driver"])}`;
  }
  if (path === "legal") {
    return { privacy: "/datenschutz", terms: "/terms", licenses: "/licenses" }[q.get("tab")] || "/impressum";
  }
  if (path === "reset-password" || path === "verify-email") return `/${path}${keep(["token"])}`;
  if (path === "setup") return `/setup${keep(["welcome"])}`;
  return `/${path}`;
}
