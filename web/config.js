// config.js — sets the API base URL for THIS deployment of the web
// frontend. Loaded as a plain (non-module) script, before js/router.js,
// so window.LMU_GARAGE_API_BASE_URL is already set by the time
// js/api.js reads it — see that file for the fallback logic.
//
// Two deployment shapes:
//
//   1. Same domain as the API (the default docker-compose setup: Caddy
//      serves both these static files AND the API, via path-based
//      routing — see docker/Caddyfile). Leave this as "": requests go to
//      whatever domain the page itself was loaded from.
//
//   2. Separate domain (e.g. this web/ folder deployed to Cloudflare
//      Pages, with the API running elsewhere on the NAS/VPS). Set this
//      to the API's full public URL, e.g.:
//        window.LMU_GARAGE_API_BASE_URL = "https://api.your-domain.example";
//      The API's LMU_GARAGE_CORS_ORIGINS (.env) must then include this
//      Pages domain, or the browser will block the requests.
window.LMU_GARAGE_API_BASE_URL = "";
