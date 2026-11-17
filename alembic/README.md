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
- In Docker the entrypoint runs `alembic upgrade head` before the server starts — an update needs no
  manual step. A downgrade inside the container needs the database URL passed explicitly
  (`docker compose exec` does not inherit it); the exact commands are in `docs/ADMIN_GUIDE.md`
  (chapter 8) and `docs/ROLLBACK.md`.
- Revisions: `0001` baseline … `0015` stint analyzer (V0.9.4), `0016` upload log (V0.9.7), `0017`
  integrity + audit log (V0.9.9), `0018` terms acceptance (V1.0), `0019` unique driver names + one
  token per PC (V1.0.1). Every migration since 0013 is additive and has a tested downgrade
  (`tests/test_migrations.py`, `tests/test_v097_migrate_filled_db.py`).
- Back up before running anything against real data:
  ```
  Copy-Item lmu_garage_server.db lmu_garage_server.backup.db
  ```
