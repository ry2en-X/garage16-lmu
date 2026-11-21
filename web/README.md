# Garage16 — web app

The static web app of Garage16: leaderboards, dashboard, teams, LapTime Analyzer, Stint Analyzer,
circuit library, account, setup guide, legal pages and the admin area. Plain HTML/CSS and native ES
modules — no npm at runtime. Since V1.0.2 every page has a readable address
(`/leaderboard/fuji-speedway/hypercar`, `/driver/moritz-lachnit`, …); old `#/…` links are forwarded.

Two ways it is served (both keep `config.js` at `window.GARAGE16_API_BASE_URL = ""`):

- **Cloudflare Pages** (garage16.de, V1.0.2): `python -m scripts.release.build_web` writes
  `build/pages/` (assets under `/v/<content hash>/`, `_headers`, `_routes.json`); the GitHub workflow
  `.github/workflows/pages.yml` publishes it after a green CI on `main`. `functions/_middleware.js`
  passes `/api/*` (and the old API paths) to the NAS and fills title/preview per page.
- **Caddy on the NAS** (`docker/Caddyfile`): this folder as it is; unknown paths get `index.html` with
  the page's title filled in by the server (`/api/v1/public/page`).

## Running it locally

```
uvicorn server.main:app --reload          # API on :8000 (from the project root)
python -m scripts.dev.serve_web           # the pages on :5173, every page address → index.html, /api → :8000
```

## How it is built

- `js/main.js` — entry; starts the router.
- `js/core/routes.js` — the one table of pages: addresses (`/analysis/:a#/vs/:b`), title, sign-in
  needed, menu entry; `legacyHashToPath()` translates old `#/…` links.
- `js/core/router.js` — History-API router: intercepts link clicks, loads a page module on first
  use (dynamic import), sets the title (" – Garage16"), shows the 404 page, opens the terms dialog
  (`js/core/terms_gate.js`) when needed. `setAddress()` keeps the address in step with what a page shows.
- `js/core/link.js` — builds every internal address (`link.leaderboard(track, key)`, `link.driver(d)`,
  …); pages never assemble paths by hand. `js/core/copy_link.js` — the "Copy link" button.
- `js/core/api.js` — the API client (everything under `/api/v1`; cookie session or, for old
  token-only accounts, a bearer token); error answers become readable messages.
- `js/core/state.js` — the signed-in driver's name/id in `localStorage` (never a password; the web
  session itself is an HttpOnly cookie).
- `js/core/format.js` — `el()`, the only way pages build DOM: text always goes in as text nodes, never
  as HTML, so names and other user input cannot inject markup.
- `js/core/i18n.js` + `js/core/i18n/de.js` — every visible text is `t("English")`; the German texts
  live in `de.js` (checked by `scripts/dev/i18n_check.py` and `tests/web/test_i18n.py`).
- `js/pages/*` — one module per page; `js/analysis/*` and `js/stints/*` — the two analyzers (line
  map, tile editor and stint charts are loaded only when needed); `js/legal/*` — the legal texts
  (German binding, English translation).
- `fonts/` — self-hosted fonts (SIL Open Font License) — nothing is loaded from other hosts, which is
  what allows a strict Content-Security-Policy (`script-src 'self'`, no inline scripts).

## Sign-in (V1.0.1)

- **New driver:** name, email, password, minimum age, terms → `POST /api/v1/accounts/signup` answers
  "check your email" (the same for every address). After confirming the email the driver signs in
  (`POST /api/v1/accounts/login`, HttpOnly cookie) and connects the desktop client with the same email and
  password.
- **Account page:** driver name (unique), email & password, Discord link, connected PCs (each PC has
  its own token and can be disconnected), web sessions, token/secret rotation for old token-only
  setups, data export and account deletion.
- **Advanced:** pasting an old desktop-client token still restores a session (accounts from before
  V1.0 without a password).

## Pages

| Address | Auth | Main API calls (under `/api/v1`) |
|---|---|---|
| `/` | public | `/public/activity` |
| `/leaderboard`, `/leaderboard/<track>/<class or car>` | public | `/public/leaderboards/class/…`, `/public/leaderboards/car/…`, `/public/catalog/…` |
| `/circuits` | public | `/public/circuits` |
| `/dashboard` | required | `/accounts/me`, `/laps`, `/teams/mine` |
| `/analysis`, `/analysis/<a>/vs/<b>`, `/analysis/<track>/<car>/vs/class-best` | required | `/analysis/compare`, `/analysis/line`, `/analysis/track-surface`, … |
| `/stints`, `/stints/<track>/<date>`, `/stints/lap/<id>` | required | `/stints/…` |
| `/teams`, `/team/<name>` | required | `/teams/…`, `/invitations/…`, `/public/teams/by-slug/…` |
| `/driver/<name>` | public | `/public/drivers/by-slug/…` |
| `/account` | — | `/accounts/…` |
| `/download`, `/setup`, `/impressum`, `/datenschutz`, `/terms`, `/licenses` | public | `/health`, `/public/legal` |
| `/verify-email`, `/reset-password` | link from the email | `/accounts/verify-email/confirm`, `/accounts/password-reset/confirm` |
| `/admin` | admin token | `/admin/…` |

Tests: `tests/js/*.test.mjs` render the real pages in jsdom against mocked API answers
(`tests/web/test_js_suites.py` runs them).
