# Garage16 — LMU V0.5.3 Changes

## P0-Fixes (alle umgesetzt)

### P0-1: Upload-Vertrag gebrochen [BEHOBEN]
- **Dateien**: `client/uploader/uploader.py`
- **Problem**: `_build_payload()` sendete die gesamte Metadaten-JSON inkl. `"uploaded": false`. Der Server hat `extra="forbid"` auf `LapMetadata` — **jeder Upload des echten Clients wurde mit 400 abgewiesen**. Die Kernfunktion des Produkts ging nicht.
- **Fix**: `_build_payload()` filtert lokale Felder (`uploaded`) heraus bevor der Envelope gebaut wird.
- **Test**: `test_upload_contract.py` — Vertragstest Recorder → Envelope → `LapMetadata.model_validate()`.
- **Zusätzlich**: `client_version` von `"0.1.0"` auf `"0.5.3"` aktualisiert.

### P0-2: Upload-Härtung [BEHOBEN]
- **Datei**: `server/routers/telemetry.py`
- **Fixes**:
  - Envelope muss ein JSON-Objekt sein (nicht Array/String) — verhindert `AttributeError` → 500.
  - Signatur als Hex validiert bevor `compare_digest` — verhindert `TypeError` → 500.
  - Dateiendung immer `.parquet` erzwungen — nie client-kontrollierte Extension.
  - Validator-Exceptions werden als `is_valid=False` behandelt, nicht als 500.
  - `try/finally`-Cleanup für alle Exceptions nach `replace()`.

### P0-3: Client-Validity-Flags verworfen [BEHOBEN]
- **Datei**: `server/routers/telemetry.py`
- **Problem**: Server ignorierte `metadata.is_valid` komplett. Pit-Laps und Cut-Laps mit plausibler Telemetrie landeten in Leaderboards und als WR in Discord.
- **Fix**: `is_valid = server_valid AND client_valid`. Beide Seiten können ablehnen, keine kann allein überstimmen.

### P0-4a: PostgreSQL Record-Race [BEHOBEN]
- **Dateien**: `server/database.py`, `server/routers/telemetry.py`
- **Fix**: `acquire_record_lock()` mit `pg_advisory_xact_lock()` pro (track, car). SQLite: No-Op (BEGIN IMMEDIATE reicht).

### P0-4b: IntegrityError löscht Gewinner-Datei [BEHOBEN]
- **Datei**: `server/routers/telemetry.py`
- **Problem**: Bei parallelen Uploads identischer Bytes löschte der Verlierer `dest_path` — die Datei auf die der Gewinner zeigt → Datenverlust.
- **Fix**: `dest_path.unlink()` entfernt im IntegrityError-Zweig. Content-addressed Dateien gehören dem Gewinner.

### P0-5: Discord-Outbox [BEHOBEN]
- **Datei**: `discord_bot/bot.py`
- **Fixes**:
  - DB-Session wird geschlossen **bevor** Discord-Sends awaited werden. Vorher hielt die Session (und unter SQLite den Write-Lock) über jedes `await channel.send()`.
  - Embed-Titel auf 256 Zeichen gekürzt (Discord-Limit).
  - WR ohne `previous_best` ("first time set") wird nicht mehr gebroadcastet — verhindert WR-Spam durch erfundene Track/Car-Kombinationen.
  - `_stamp_announced()` als eigene Short-Lived-Session.
  - `/link` — Race Condition auf Link-Code mit `with_for_update()`.
  - `/link` — discord_user_id Collision mit Vorprüfung + IntegrityError-Handling.
  - `/leaderboard` — GROUP BY+JOIN Duplicate-Bug → ROW_NUMBER() (wie Server).

### P0-6: Fail-open Defaults [BEHOBEN]
- **Dateien**: `server/config.py`, `server/crypto.py`, `server/main.py`, `server/requirements.txt`
- **Fixes**:
  - Default invertiert: **nur** explizites `LMU_GARAGE_ENV=development` bekommt Dev-Key und offene Registrierung. Alles andere (unset, Tippfehler, "prod") → Hard-Fail.
  - Registration-Secret ist in Production **pflicht** — Startup bricht ab ohne.
  - `create_all()` nur in Development — Production muss Alembic nutzen.
  - CORS Origins konfigurierbar via `LMU_GARAGE_CORS_ORIGINS`.
  - `psycopg[binary]` in `requirements.txt` ergänzt.
  - Dev-DB-Dateien aus dem Paket entfernt.

### P0-7: Parser — Lap-Sprünge, Reset, Falsy-Zero [TEILWEISE BEHOBEN]
- **Dateien**: `client/telemetry/parser.py`, `client/main.py`
- **Fixes**:
  - **(b)** Nur `n → n+1` als gültiges Lap-Ende akzeptiert. Sprünge (Session-Restart 5→1, Crash) verwerfen den Buffer statt eine Bogus-Lap zu erzeugen.
  - **(b)** Parser wird bei Reconnect und Exception `reset()` — keine stale Lap-Buffer aus vorheriger Session.
  - **Falsy-Zero**: `self._lap_start_time or ...` → `is not None` — `elapsed_time=0.0` am Session-Start erzeugt nicht mehr `t=0` für alle Samples.
  - **Encoding**: `errors="ignore"` → `errors="replace"` — Akzente (Autódromo) gehen nicht mehr verloren.
- **Tests**: 3 neue Tests für Lap-Sprung, Rückwärts-Sprung und normale Runde nach Sprung.
- **Offen (benötigt LMU auf Windows)**: (a) Scoring-Telemetrie-Sync, (c) count_lap_flag Semantik, (d) mmap-Verhalten, (e) Silent Weather-Drop, (f) car identity, (g) Staleness.

## Weitere Fixes aus V0.5.3

- **`datetime.utcnow()`** → `datetime.now(timezone.utc)` in allen Dateien (Python 3.12+ Deprecation).
- **`create_team`**: Zwei separate Commits → ein `flush()` + ein `commit()` — Team und Membership atomar.
- **`list_my_laps`**: `offset`-Parameter für Pagination.
- **`/link_team`, `/link_global`**: `with_for_update()` für konsistente Channel-Registrierung.

## Teststand

52 Tests, alle grün:
- 8 ABI/Struct-Tests
- 12 Parser-Tests (inkl. 3 neue für Lap-Sprünge)
- 5 Recorder-Tests
- 3 Upload-Contract-Tests (NEU — hätte P0-1 gefunden)
- 3 Upload-Streaming-Tests
- 6 Uploader-Retry-Tests
- 15 Validation-Tests

## Was NICHT in V0.5.3 ist (→ P1/P2)

- ASGI-Middleware für Content-Length vor Auth (P0-2 Proxy-Empfehlung stattdessen)
- Rate-Limiting (Proxy-seitig empfohlen)
- Client-Queue Härtung (lap_time ≤ 0 nicht speichern, Poison-Lap Isolation)
- Transaktionsdauer (Validator vor DB-Transaktion, Endpoint als `def`)
- Validator-Verschärfung (Toleranzen, ∫speed·dt)
- Moderation (Lap invalidieren, Fahrer sperren)
- P0-7 Teile die LMU auf Windows benötigen
