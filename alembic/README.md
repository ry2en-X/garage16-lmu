# Migrations (Alembic)

Setup (once):
```
pip install -r server/requirements.txt   # now includes alembic
```

## Which command do I run?

- **Fresh dev DB (no `lmu_garage_server.db` yet, or you just deleted it):**
  Nothing to do — `server/main.py` still calls `Base.metadata.create_all()`
  on startup, which creates every table from scratch. Alembic isn't
  required for this case, but running `alembic stamp head` afterwards is
  harmless and keeps its bookkeeping table in sync.

- **Existing dev DB that already has all current tables/columns**
  (e.g. you bootstrapped it via `create_all()` before Alembic existed):
  ```
  alembic stamp head
  ```
  This just records "this DB is at revision `0001`" — it does not touch
  any tables.

- **Existing DB that's missing a table/column added to `server/models.py`
  after this was written:**
  1. Make your model change in `server/models.py`.
  2. `alembic revision --autogenerate -m "describe the change"`
  3. Check the generated file under `alembic/versions/` — autogenerate is
     usually right but not infallible (renames, some constraint changes).
  4. `alembic upgrade head`

## Notes

- `alembic/env.py` reads the DB URL from `server.config.settings`, i.e.
  the same `LMU_GARAGE_DB_URL` env var (or the sqlite default) the app
  itself uses — there's no separate URL to keep in sync.
- `alembic/versions/0001_initial_baseline.py` is a baseline: it defines
  all six tables exactly as they existed when Alembic was introduced.
  `alembic/versions/0002_driver_token_revocation.py` is the first real
  incremental migration on top of it (adds `drivers.token_revoked_at` for
  token revocation) — a template for how future model changes get their
  own migration.
- Back up before running anything against real data:
  ```
  Copy-Item lmu_garage_server.db lmu_garage_server.backup.db
  ```
