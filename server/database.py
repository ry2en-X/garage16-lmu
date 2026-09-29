from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
_pool_kwargs = (
    {}
    if settings.database_url.startswith("sqlite")
    # SQLite (a single file, no real connection pool) ignores these; they
    # matter for PostgreSQL under real concurrent load. See config.py's
    # db_pool_size/db_max_overflow docstring for what pool_pre_ping buys.
    else {
        "pool_pre_ping": True,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
    }
)
engine = create_engine(settings.database_url, connect_args=_connect_args, **_pool_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

if engine.dialect.name == "sqlite":
    # Fixes a real race condition in records.py: two concurrent uploads
    # could each read "what's the current best lap time" before either
    # commits, and both decide (independently, both "correctly" given what
    # they saw) that their lap is a new record — producing two RecordEvents
    # where only one lap is truly the winner.
    #
    # pysqlite's default isolation_level ("") only issues an implicit BEGIN
    # right before the first write of a transaction (SELECTs don't start
    # one) — using SQLite's default DEFERRED mode, which only takes a lock
    # once a write is attempted. That leaves exactly the window above open:
    # both transactions can do their read-then-decide step under a shared
    # (read) lock before either escalates to a write lock.
    #
    # This is SQLAlchemy's own documented recipe for that quirk (see
    # "Serializable isolation" in the SQLAlchemy pysqlite dialect docs):
    # disable pysqlite's own implicit transaction handling, and instead
    # open every transaction with BEGIN IMMEDIATE — which takes SQLite's
    # write lock immediately. A second transaction's BEGIN IMMEDIATE then
    # simply waits for the first to commit before it can proceed, so its
    # own read-then-decide step only ever sees fully-committed data.
    #
    # Trade-off: this serializes ALL transactions on this connection pool,
    # including plain reads (e.g. GET /leaderboard) — for this project's
    # actual traffic (a handful of drivers uploading laps occasionally),
    # that cost is negligible. If this ever needs real read concurrency,
    # the fix is moving off SQLite (a single-writer database) entirely,
    # not tuning this further.
    @event.listens_for(engine, "connect")
    def _sqlite_disable_pysqlite_autobegin(dbapi_connection, _connection_record):
        dbapi_connection.isolation_level = None
        # V0.8-NAS fix for a real, previously-unexplained flake in
        # test_duplicate_identical_upload_race_keeps_exactly_one_lap:
        # without a busy_timeout, a second connection's BEGIN IMMEDIATE
        # that can't acquire the write lock immediately raises "database
        # is locked" right away instead of waiting — SQLite's default is
        # to NOT retry at all. 5s is generous relative to how long one of
        # this project's transactions actually holds the write lock
        # (milliseconds), so this only ever matters for two requests that
        # land within that window of each other, exactly the concurrency
        # scenario the test above is built to exercise.
        dbapi_connection.execute("PRAGMA busy_timeout = 5000")

    @event.listens_for(engine, "begin")
    def _sqlite_begin_immediate(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def acquire_record_lock(db, track_name: str, car_name: str) -> None:
    """Serialize record evaluation for a given track/car combination.

    On SQLite this is a no-op — BEGIN IMMEDIATE (configured above) already
    serializes the entire transaction, so there's nothing additional to
    lock. On PostgreSQL (or any other backend that allows real concurrent
    writes), this takes a transaction-scoped advisory lock keyed on a hash
    of (track_name, car_name), so two uploads for the same combo wait for
    each other in the read-compare-write sequence instead of racing.

    Advisory locks are lightweight (no row-level locks, no deadlock risk
    with unrelated tables) and automatically released at transaction end
    (commit or rollback) — exactly the semantics we need.
    """
    if engine.dialect.name == "sqlite":
        return  # BEGIN IMMEDIATE handles this already
    # pg_advisory_xact_lock takes a bigint — hash the (track, car) pair
    # into a stable 63-bit integer (PostgreSQL bigint is signed 64-bit,
    # so mask to 63 bits to avoid negative-value edge cases).
    import hashlib
    key = hashlib.sha256(f"{track_name}\0{car_name}".encode()).digest()
    lock_id = int.from_bytes(key[:8], "big") & 0x7FFFFFFFFFFFFFFF
    db.execute(sa.text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
