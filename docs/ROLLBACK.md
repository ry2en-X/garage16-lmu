# Rollback-Plan

## Welche Variante läuft gerade?

Die Befehle unten nehmen Variante A (Cloudflare Tunnel, NAS) als Beispiel
an — `docker compose ...`. Läuft stattdessen Variante B (VPS, direkter
Reverse Proxy), immer die Override-Datei mit angeben:
```
docker compose -f docker-compose.yml -f docker-compose.vps.yml ...
```
Sonst identisch — beide Varianten teilen sich Images, Volumes und
Migrationslogik, der Unterschied ist nur die Port-Freigabe auf
`reverse_proxy` (siehe README.md).

## Wenn ein Deployment schiefgeht

1. **Sofort: vorheriges Image wieder hochfahren.**
   ```
   docker compose down server discord_bot
   docker compose up -d server discord_bot   # falls Image-Tag gepinnt ist, alten Tag in docker-compose.yml eintragen
   ```
   Setzt KEINE Migration zurück — nur den Code.

2. **Falls das Problem eine fehlerhafte Migration war:**
   ```
   docker compose exec server alembic downgrade -1
   ```
   Nur EINE Revision zurück, nicht mehr. Prüfe danach `alembic current`
   gegen das, was der zuvor laufende Server-Code erwartet.

   **Achtung:** Ein `downgrade` das Spalten löscht, die bereits Daten
   enthalten, verliert diese Daten unwiderruflich. Vor jedem Downgrade in
   Produktion: Backup laufen lassen (`./scripts/backup.sh` oder, falls
   der containerisierte Weg genutzt wird, einfach den nächsten
   `--profile backup`-Zyklus abwarten oder manuell antriggern — siehe
   README.md "Backups").

3. **Falls Daten beschädigt/inkonsistent sind: aus einem Backup wiederherstellen.**

   Mit dem Skript (empfohlen — es macht alles in der richtigen
   Reihenfolge und fragt vorher nach):
   ```
   ./scripts/restore.sh <db.sql.gz> <telemetry.tar.gz>
   ```
   Die beiden Dateien MÜSSEN aus demselben Backup-Lauf stammen (gleicher
   Zeitstempel) — sonst zeigen `Lap.telemetry_path`-Einträge ins Leere
   oder umgekehrt. Beide Backup-Arten funktionieren, man übergibt nur die
   zwei Dateien:
   - `scripts/backup.sh`: `<Backup-Ordner>/garage16_<Zeitstempel>/db.sql.gz`
     und `.../telemetry.tar.gz`
   - containerisiert (`--profile backup`):
     `garage16_db_<Zeitstempel>.sql.gz` und
     `garage16_telemetry_<Zeitstempel>.tar.gz` in `${BACKUP_HOST_PATH}`

   Das Skript: prüft beide Dateien, verlangt die Eingabe von `RESTORE`,
   stoppt `server` und `discord_bot`, legt eine Sicherheitskopie der
   AKTUELLEN Datenbank neben dein Backup (`pre_restore_<Zeit>.sql.gz`),
   löscht und erstellt die Datenbank neu, spielt den Dump ein, ersetzt die
   Telemetrie-Dateien und startet `server` + `discord_bot` wieder.

   **Warum nicht einfach `psql < dump`?** Weil das gegen die bestehende,
   gefüllte Datenbank scheitert (`relation "alembic_version" already
   exists`, gegen ein echtes PostgreSQL 16 nachgestellt), und weil
   `docker compose exec` einen LAUFENDEN db-Container braucht — ein
   vorheriges `docker compose down` macht den Befehl unmöglich. (Beides
   stand in früheren Versionen dieser Anleitung falsch.)

   Von Hand (nur falls das Skript nicht nutzbar ist) — die DB muss laufen,
   die Schreiber müssen aus sein:
   ```
   docker compose stop server discord_bot
   docker compose up -d db
   docker compose exec -T db psql -U garage16 -d postgres -v ON_ERROR_STOP=1 \
       -c "DROP DATABASE IF EXISTS garage16 WITH (FORCE);" \
       -c "CREATE DATABASE garage16 OWNER garage16;"
   gunzip -c /pfad/zum/backup/db.sql.gz | docker compose exec -T db psql -U garage16 -d garage16 -q -v ON_ERROR_STOP=1
   docker run --rm -v garage16_telemetry_data:/data -v /pfad/zum/backup:/backup:ro alpine \
       sh -c "find /data -mindepth 1 -delete && tar xzf /backup/telemetry.tar.gz -C /data"
   docker compose up -d server discord_bot
   ```

## Nach jedem Rollback

- `curl https://your-domain.example/health` prüfen (Variante B) bzw. den
  über den Cloudflare-Tunnel erreichbaren Hostnamen (Variante A) — auf
  `db_ok: true` und die erwartete `version` achten.
- Discord-Bot-Logs prüfen (`docker compose logs discord_bot`) — ein
  Schema-Mismatch zwischen Bot und DB zeigt sich meist als wiederholte
  Exceptions im Poll-Loop. `docker compose ps` sollte alle vier Dienste
  als `healthy` zeigen, inklusive des Bots (Heartbeat-Datei, siehe
  README.md "Monitoring") — `unhealthy` dort ist ein zusätzliches, vom
  reinen Log-Grep unabhängiges Signal.
- Betroffene Fahrer informieren, falls Uploads in der Zwischenzeit
  verloren gingen (Client-Queue behält lokale Kopien — `--reconfigure`
  ist NICHT nötig, der Uploader versucht es beim nächsten Zyklus erneut,
  außer die Lap wurde bereits als `rejected` markiert).

## Client-Kompatibilität nach einem Server-Rollback

Wenn der Server auf eine ältere Version zurückgerollt wird, aber Fahrer
bereits einen neueren Client haben: prüfe `LMU_GARAGE_MIN_CLIENT_VERSION`
auf dem zurückgerollten Server — falls der neue Client-Wert höher ist als
das, was der alte Server als `min_client_version` meldet, laufen Uploads
weiter normal (die Prüfung ist "mindestens", nicht "exakt"). Nur ein
Server-Rollback VOR eine Version, die ein Feld eingeführt hat, das neue
Clients senden, würde erneut den P0-1-artigen Fehler auslösen — daher:
niemals hinter die Version zurückrollen, die den Upload-Vertrag zuletzt
geändert hat, ohne das explizit zu prüfen.

## Rollback eines Umstiegs NAS → VPS

Läuft der Umstieg schief (Domain zeigt noch nicht richtig, Zertifikat
scheitert, o.ä.): das alte NAS-Deployment einfach weiterlaufen lassen
(nichts daran anfassen, bis der VPS-Umstieg bestätigt funktioniert) —
DNS-Umstellung ist der einzige Schritt, der wirklich "scharf" ist, und
lässt sich durch Zurückändern des DNS-Eintrags rückgängig machen, ohne
dass am NAS selbst etwas repariert werden müsste.

