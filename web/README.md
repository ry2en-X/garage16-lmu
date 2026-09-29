# LMU Garage — Web

A static, no-build-step web frontend for the LMU Garage backend
(`server/`): leaderboards, your own lap history, teams, and Discord
account linking.

No React/Vite/npm needed — it's plain HTML/CSS/JS using native ES
modules, so any static file server works.

## Running it locally (development)

```
cd web
python3 -m http.server 5173
```

Then open `http://localhost:5173`.

**Important:** `config.js` ships with `window.LMU_GARAGE_API_BASE_URL = ""`,
which means "the API lives on the same domain as this page" — correct for
the production setup (Caddy serves both, see `docker/Caddyfile`), but with
`python3 -m http.server` there is no API on port 5173, so every request
would fail. For local development, start the API on port 8000 and change
that one line in `web/config.js` to

```js
window.LMU_GARAGE_API_BASE_URL = "http://localhost:8000";
```

(the API's default CORS setting already allows `http://localhost:5173`).
Don't commit that change — production must keep `""`.

## Pointing it at a different backend

Set the same variable in `config.js` (it is loaded before the router
script, see `index.html`):

```js
window.LMU_GARAGE_API_BASE_URL = "https://your-backend-host";
```

## Backend CORS

The FastAPI backend already allows the development frontend origin
`http://localhost:5173`. If you deploy the frontend elsewhere, add that
origin to `allow_origins` in `server/main.py`.

## How sign-in works

There's no username/password login on the backend — `POST
/accounts/register` (see `accounts.py`) is a dev-only endpoint that mints
a fresh `auth_token` + `client_secret` for a new driver, shown once. This
app's **Account** page:

- **New driver**: registers and stores the `auth_token` in
  `localStorage` to authenticate future requests. The `client_secret` is
  shown once — that one's for configuring the desktop recorder client
  (`client/uploader.py`), not needed by this web app.
- **Already have a token**: paste back a previously-saved `auth_token`
  to restore a session on another device/browser.

Per the backend's own `DriverRegisterResponse.warning`, treat this as a
placeholder auth scheme — fine for internal testing, not for real users.

If the backend has `LMU_GARAGE_REGISTRATION_SECRET` set (see
`server/config.py`), the registration form's "Registration secret" field
must match it, or `/accounts/register` returns 403.

## Managing credentials (Account page → Security)

- **Rotate auth token / Rotate client secret** — issues a new credential
  and immediately invalidates the old one, without losing the driver
  identity. Use this routinely, or the moment you suspect a credential
  leaked but still have access. After rotating the client secret, run
  `python -m client.main --reconfigure` to save it to the desktop client.
- **Revoke token** — kills the current token outright, signing out every
  device using it (this browser, the desktop client, any Discord link).
  There's no recovery path afterwards (no password to prove ownership) —
  only use this for "this token is definitely compromised."

## Pages

| Route | Auth | Backed by |
|---|---|---|
| `#/leaderboard` | public | `GET /leaderboard/{track}/{car}` |
| `#/dashboard` | required | `GET /telemetry/laps` |
| `#/teams` | required | `POST /teams`, `POST /teams/join`, `GET /teams/mine` |
| `#/account` | — | `POST /accounts/register`, `POST /teams/discord-link-code` |

## File layout

```
web/
  index.html
  css/styles.css
  js/
    api.js        API client
    state.js      session (auth token) store, persisted to localStorage
    format.js     lap-time/date formatting + a tiny DOM helper
    router.js     hash-based router, nav + status bar
    pages/
      auth.js         registration / restore-session form
      account.js       account page (wraps auth.js when signed out)
      leaderboard.js
      dashboard.js
      teams.js
```
