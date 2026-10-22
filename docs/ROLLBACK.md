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

3. **Falls Daten beschädigt/inkonsistent sind: aus einem Backup wiederherstellen** (V0.9.8).

   Ein Backup ist seit V0.9.8 **ein Ordner** `garage16_<Zeitstempel>/` mit `db.sql.gz`,
   `telemetry.tar.gz` und `manifest.json` (beide Wege — `scripts/backup.sh` und
   `--profile backup` — legen genau das an; ein Ordner mit `.partial` am Ende ist ein abgebrochener
   Lauf und kein Backup). Datenbank und Dateien stammen aus demselben Moment: während des Backups
   werden neue Uploads kurz mit „Wartung“ (HTTP 503) abgewiesen und vom Client später wiederholt.

   ```
   ./scripts/restore.sh /pfad/zu/garage16_20261019_030000
   ```

   Ablauf — und was bei einem Fehler in jedem Schritt passiert:

   | Schritt | Was | Bei Fehler |
   |---|---|---|
   | 1 Prüfen | Manifest, SHA-256 und Größen, gzip, Dump vollständig, keine `../`-Pfade, Schema bekannt | nichts angefasst |
   | 2–3 Bereitstellen | Dump in eine NEUE Datenbank `<db>_restore_<stamp>`, Dateien in ein NEUES Volume | beides wieder entfernt |
   | 4 Prüfen der Kopie | `alembic upgrade head` + `consistency_check --deep` auf der Kopie | beides entfernt, Produktion unberührt |
   | 5 Stoppen | server + discord_bot | wieder gestartet |
   | 6 Datenbank tauschen | EINE Transaktion: live → `<db>_pre_restore_<stamp>`, Kopie → live | unverändert |
   | 7 Dateien tauschen | aktuelle Dateien nach `.pre_restore_<stamp>/` im Volume, Kopie hinein | Dateien und Datenbank zurück |
   | 8–9 Starten, prüfen | Health + Konsistenz des laufenden Servers | alles zurück, alter Stand läuft |

   Ergebnis ist **immer** entweder das geprüfte Backup oder der Stand davor. Scheitert sogar das
   Zurückdrehen, bricht das Skript mit Exit-Code 3 und „MANUAL ATTENTION NEEDED“ ab und nennt, welche
   Datenbank / welcher Ordner welchen Stand hält — nie stillschweigend.

   Der alte Stand bleibt als Rückfallpunkt liegen:
   ```
   ./scripts/restore.sh --rollback 20261019_141500    # zurück zum Stand vor diesem Restore
   ./scripts/restore.sh --cleanup  20261019_141500    # Rückfallpunkt löschen, wenn alles passt
   ```
   (den Zeitstempel nennt das Skript am Ende.)

   **Backups von vor V0.9.8** (zwei lose Dateien, kein Manifest) gehen mit
   `./scripts/restore.sh --allow-unverified <db.sql.gz> <telemetry.tar.gz>` — dieselbe sichere
   Abfolge, nur ohne Prüfsummen-Vergleich; beide Dateien müssen vom selben Lauf stammen.

   **Getestet:** `tests/test_v098_backup_restore.py` führt die ausgelieferten Skripte gegen ein
   echtes PostgreSQL und echte Alpine-Umgebungen aus — Uploads während des Backups, abgebrochene und
   gekillte Backups, ein Fehler in jedem einzelnen Restore-Schritt, ein scheiterndes Zurückdrehen,
   beschädigte / fremde / manipulierte Backups.

   ### Restore manuell zurückdrehen

   Nur nötig, wenn das Skript mit Exit-Code 3 abbricht. Es nennt die Namen; der Normalfall:
   ```
   docker compose stop server discord_bot
   # Dateien: alles außer dem Rückfallordner weg, dessen Inhalt zurück
   docker run --rm -v garage16_telemetry_data:/live alpine sh -c \
     'cd /live && find . -mindepth 1 -maxdepth 1 ! -name ".pre_restore_<stamp>" -exec rm -rf {} + && mv .pre_restore_<stamp>/* .pre_restore_<stamp>/.[!.]* . 2>/dev/null; rmdir .pre_restore_<stamp>'
   # Datenbank: in einer Transaktion zurückbenennen
   docker compose exec -T db psql -U garage16 -d postgres -1 \
     -c 'ALTER DATABASE garage16 RENAME TO garage16_failed_<stamp>;' \
     -c 'ALTER DATABASE garage16_pre_restore_<stamp> RENAME TO garage16;'
   docker compose up -d server discord_bot
   ```
   Danach `python -m scripts.consistency_check` im Server-Container (Befehl: ADMIN_GUIDE).

## Nach jedem Rollback

- `curl https://garage16.de/health` prüfen (Variante B) bzw. den
  über den Cloudflare-Tunnel erreichbaren Hostnamen (Variante A) — auf
  `db_ok: true` und die erwartete `version` achten.
- Discord-Bot-Logs prüfen (`docker compose logs discord_bot`) — ein
  Schema-Mismatch zwischen Bot und DB zeigt sich meist als wiederholte
  Exceptions im Poll-Loop. `docker compose ps` sollte alle vier Dienste
  als `healthy` zeigen, inklusive des Bots (Heartbeat-Datei, siehe
  README.md "Monitoring") — `unhealthy` dort ist ein zusätzliches, vom
  reinen Log-Grep unabhängiges Signal.
- **Nach einem Restore (V0.9.7): Datenbank und Telemetrie-Volume gegeneinander prüfen** —
  `python -m scripts.consistency_check --deep` im Server-Container (genauer Befehl:
  `docs/ADMIN_GUIDE.md`, „Neu in V0.9.7“). Runden ohne Datei oder Dateien mit anderer Prüfsumme
  heißen: DB- und Telemetrie-Backup stammen nicht vom selben Zeitpunkt.
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

