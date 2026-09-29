"""
alembic/env.py — wires Alembic up to the server's actual models and DB URL
instead of a separate, hand-maintained copy of either.

- The target metadata is server.database.Base.metadata, after importing
  server.models so every table is registered on it. That's what makes
  `alembic revision --autogenerate` able to diff "what's in models.py" vs
  "what's actually in the DB".
- The DB URL comes from server.config.settings.database_url — the same
  LMU_GARAGE_DB_URL env var (or sqlite default) the FastAPI app itself
  uses — so migrations always run against the DB the app is configured
  for, never a separately-configured one that could drift out of sync.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import the server's Base *and* models so all tables are registered on
# Base.metadata before Alembic looks at it.
from server.database import Base
from server import models  # noqa: F401  (registers tables on Base.metadata)
from server.config import settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override whatever [alembic] sqlalchemy.url might say (there isn't one in
# alembic.ini on purpose) with the server's real, environment-aware URL.
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL scripts without a live DB connection (`alembic upgrade
    head --sql`), using the URL above."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the real DB and apply migrations directly — the normal
    `alembic upgrade head` / `alembic stamp head` path."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
