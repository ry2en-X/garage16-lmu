// web/functions/_middleware.js — the Cloudflare Pages function of garage16.de.
//
// Cloudflare Pages serves the website's files from its own network; the NAS answers only the API.
// This function sits in front of every request that is not a plain file (see _routes.json written
// by scripts/release/build_web.py) and does three things:
//
//   1. API requests (/api/*, /health, and the old API paths of desktop clients 1.0.x unless a
//      browser asks for a page) are passed on to the NAS (GARAGE16_API_ORIGIN, the Cloudflare
//      Tunnel's address), with the visitor's address for the rate limits (GARAGE16_EDGE_KEY,
//      server/core/client_ip.py) — same address for the browser, so cookies and sign-in work as before.
//   2. Page addresses (/leaderboard/fuji-speedway/hypercar, /driver/moritz-lachnit, …) get
//      index.html with the page's own title and link preview (Discord, WhatsApp, …), asked from
//      GET /api/v1/public/page-meta (cached by Cloudflare for a minute), and 404 for unknown pages.
//   3. Everything else is the file as Pages has it.
//
// Settings of the Pages project (Settings -> Environment variables), docs/DEPLOYMENT.md:
//   GARAGE16_API_ORIGIN  https://api.garage16.de    where the NAS answers (Cloudflare Tunnel)
//   GARAGE16_EDGE_KEY    the same secret as GARAGE16_EDGE_KEY in the NAS's .env

// keep equal to server/api/legacy.py is_legacy_api_request (tests/api/test_api_paths.py checks it)
const LEGACY_API_PREFIXES = ["/accounts/", "/telemetry/", "/drivers/", "/invitations/", "/admin/", "/teams/",
  "/leaderboard/class/", "/leaderboard/car/", "/leaderboard/catalog/"];
const LEGACY_API_EXACT = ["/leaderboard/recent"];

const HOP_BY_HOP = ["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
  "transfer-encoding", "upgrade", "host"];

function wantsPage(request) {
  return (request.headers.get("Accept") || "").includes("text/html");
}

export function isApiRequest(pathname, request) {
  if (pathname.startsWith("/api/") || pathname === "/health" || pathname.startsWith("/health/")) return true;
  if (LEGACY_API_PREFIXES.some((p) => pathname.startsWith(p)) || LEGACY_API_EXACT.includes(pathname)) return true;
  if (pathname === "/teams") return !["GET", "HEAD"].includes(request.method);   // POST /teams: the old "create a team"
  if (pathname === "/legal") return !wantsPage(request);                          // the old legal data
  return false;
}

async function toApi(request, env, url) {
  const origin = (env.GARAGE16_API_ORIGIN || "").replace(/\/+$/, "");
  if (!origin) return new Response(JSON.stringify({ detail: "GARAGE16_API_ORIGIN is not set in the Pages project." }), { status: 502, headers: { "Content-Type": "application/json" } });
  const headers = new Headers(request.headers);
  for (const name of HOP_BY_HOP) headers.delete(name);
  headers.set("X-Forwarded-Host", url.host);
  headers.set("X-Forwarded-Proto", "https");
  const visitor = request.headers.get("CF-Connecting-IP");
  if (visitor && env.GARAGE16_EDGE_KEY) {
    headers.set("X-Garage16-Client-IP", visitor);
    headers.set("X-Garage16-Edge-Key", env.GARAGE16_EDGE_KEY);
  }
  const target = origin + url.pathname + url.search;
  const init = { method: request.method, headers, redirect: "manual" };
  if (!["GET", "HEAD"].includes(request.method)) init.body = request.body;
  return fetch(target, init);
}

async function pageMeta(env, url) {
  const origin = (env.GARAGE16_API_ORIGIN || "").replace(/\/+$/, "");
  if (!origin) return null;
  try {
    const res = await fetch(`${origin}/api/v1/public/page-meta?path=${encodeURIComponent(url.pathname)}`, {
      headers: { Accept: "application/json" },
      cf: { cacheTtl: 60, cacheEverything: true },
    });
    return res.ok ? await res.json() : null;
  } catch {
    return null;   // the NAS is not reachable: the page still works, with the general title
  }
}

class SetAttribute {
  constructor(attr, value) { this.attr = attr; this.value = value; }
  element(el) { el.setAttribute(this.attr, this.value); }
}

class SetText {
  constructor(value) { this.value = value; }
  element(el) { el.setInnerContent(this.value); }
}

async function withMeta(response, env, url) {
  const meta = await pageMeta(env, url);
  const headers = new Headers(response.headers);
  headers.set("Cache-Control", "no-cache");
  headers.delete("Content-Length");
  const plain = new Response(response.body, { status: meta && meta.status === 404 ? 404 : 200, headers });
  if (!meta) return plain;
  const absolute = `${url.origin}${meta.canonical || url.pathname}`;
  return new HTMLRewriter()
    .on("title", new SetText(meta.title))
    .on('meta[name="description"]', new SetAttribute("content", meta.description))
    .on('meta[property="og:title"]', new SetAttribute("content", meta.title))
    .on('meta[property="og:description"]', new SetAttribute("content", meta.description))
    .on('meta[property="og:url"]', new SetAttribute("content", absolute))
    .on('meta[property="og:image"]', new SetAttribute("content", `${url.origin}/icon-192.png`))
    .on('link[rel="canonical"]', new SetAttribute("href", absolute))
    .transform(plain);
}

export async function onRequest(context) {
  const { request, env, next } = context;
  const url = new URL(request.url);
  if (isApiRequest(url.pathname, request)) return toApi(request, env, url);
  const response = await next();
  const type = response.headers.get("Content-Type") || "";
  // a page address: Pages answers it with index.html (single-page app) — fill in its title
  if (request.method === "GET" && type.includes("text/html") && !/\.[a-z0-9]+$/i.test(url.pathname)) {
    return withMeta(response, env, url);
  }
  return response;
}
