# Garage16 — web app

The static web app of Garage16: leaderboards, dashboard, teams, LapTime Analyzer, Stint Analyzer,
circuit library, account, setup guide, legal pages and the admin area. Plain HTML/CSS and native ES
modules — no build step, no npm at runtime. In production Caddy serves this folder and the API on
the same address (`docker/Caddyfile`), so `config.js` keeps `window.LMU_GARAGE_API_BASE_URL = ""`.

## Running it locally

```
uvicorn server.main:app --reload          # API on :8000 (from the project root)
cd web && python3 -m http.server 5173     # the pages on :5173
```

With `python3 -m http.server` the API is on another port: change the one line in `config.js` to
`window.LMU_GARAGE_API_BASE_URL = "http://localhost:8000";` (the API's development CORS default
allows `http://localhost:5173`). Do not commit that change — production must keep `""`.

## How it is built

- `js/router.js` — hash router; every page module is loaded when first opened (dynamic import),
  sets the page title, and opens the terms dialog (`js/terms_gate.js`) when the signed-in driver has
  not accepted the current terms.
- `js/api.js` — the API client (cookie session or, for old token-only accounts, a bearer token);
  error answers (`detail` as text, list or `{code, message}`) become readable messages.
- `js/state.js` — the signed-in driver's name/id in `localStorage` (never a password; the web session
  itself is an HttpOnly cookie).
- `js/format.js` — `el()`, the only way pages build DOM: text always goes in as text nodes, never as
  HTML, so names and other user input cannot inject markup.
- `js/i18n.js` + `js/i18n/de.js` — every visible text is `t("English")`; the German texts live in
  `de.js` (checked by `scripts/i18n_check.py` and `tests/test_i18n.py`).
- `js/pages/*` — one module per page; `js/analysis/*` and `js/stints/*` — the two analyzers;
  `js/legal/*` — the legal texts (German binding, English translation).
- `fonts/` — self-hosted fonts (SIL Open Font License) — nothing is loaded from other hosts, which is
  what lets Caddy send a strict Content-Security-Policy (`script-src 'self'`, no inline scripts).

## Sign-in (V1.0.1)

- **New driver:** name, email, password, minimum age, terms → `POST /accounts/signup` answers
  "check your email" (the same for every address). After confirming the email the driver signs in
  (`POST /accounts/login`, HttpOnly cookie) and connects the desktop client with the same email and
  password.
- **Account page:** driver name (unique), email & password, Discord link, connected PCs (each PC has
  its own token and can be disconnected), web sessions, token/secret rotation for old token-only
  setups, data export and account deletion.
- **Advanced:** pasting an old desktop-client token still restores a session (accounts from before
  V1.0 without a password).

## Pages

| Route | Auth | Main API calls |
|---|---|---|
| `#/` | public | `/leaderboard/recent` |
| `#/leaderboard` | public | `/leaderboard/class/…`, `/leaderboard/car/…`, catalog |
| `#/circuits` | public | `/telemetry/analysis/circuits` |
| `#/dashboard` | required | `/accounts/me`, `/telemetry/laps`, `/teams/mine` |
| `#/analysis` | required | `/telemetry/analysis/compare`, `/line`, `/track-surface`, … |
| `#/stints` | required | `/telemetry/analysis/stints/…` |
| `#/teams`, `#/team/<id>` | required | `/teams/…`, `/invitations/…` |
| `#/driver/<id>` | public | `/drivers/<id>/public` |
| `#/account` | — | `/accounts/…` |
| `#/download`, `#/setup`, `#/legal` | public | `/health`, `/legal` |
| `#/verify-email`, `#/reset-password` | link from the email | `/accounts/verify-email/confirm`, `/accounts/password-reset/confirm` |
| `#/admin` | admin token | `/admin/…` |

Tests: `tests/js/*.test.mjs` render the real pages in jsdom against mocked API answers
(`tests/test_web_analysis.py` runs them).
