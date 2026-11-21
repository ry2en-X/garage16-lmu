// config.js — where THIS copy of the website finds the API. Loaded as a plain (non-module) script
// before js/main.js, so window.GARAGE16_API_BASE_URL is set by the time js/core/api.js reads it.
//
// Leave it "" (the default): the API is on the same address as the pages — under /api/v1. That is
// true for both ways of serving the website:
//
//   1. Cloudflare Pages: the Pages function
//      (web/functions/_middleware.js) passes /api/* on to the NAS.
//   2. The NAS itself: Caddy serves these files and the API on one address (docker/Caddyfile).
//
// Only for local development with the pages on another port than the API, set the API's address,
// e.g. "http://localhost:8000" (scripts/dev/serve_web.py does that for you). Do not commit that.
window.GARAGE16_API_BASE_URL = "";
