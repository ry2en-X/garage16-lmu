# Admin-Leitfaden (V0.9)

Kurz und praktisch — für den, der Garage16 auf der NAS betreibt. Für die
volle technische Tiefe zu jedem Punkt verweist dieser Leitfaden auf die
ausführlichen Abschnitte in `README.md`; hier steht nur, was man
tatsächlich tun muss, in der Reihenfolge, in der man es braucht.

## 1. NAS-Installation

1. Docker auf der NAS aktivieren (Synology: Paket-Zentrum → "Container
   Manager" installieren).
2. Projektordner auf die NAS kopieren (z. B. via File Station oder
   `git clone`).
3. `.env`-Datei anlegen: `cp .env.example .env`, dann die Werte darin
   ausfüllen (siehe Abschnitt 3 unten).
4. Zugangsvariante wählen — **Cloudflare Tunnel** (kein offener Router-
   Port nötig, empfohlen für eine Heim-NAS) oder **direkter Reverse
   Proxy** (braucht eine öffentliche IP, eher für einen VPS). Die
   komplette Schritt-für-Schritt-Anleitung für beide Varianten steht in
   `README.md` unter "Production deployment" — hier nicht dupliziert,
   da sie sich mit jedem Cloudflare-/DNS-Detail ändern kann.
5. Starten:
   ```
   docker compose --profile tunnel up -d      # Cloudflare-Tunnel-Variante
   # oder
   docker compose up -d                        # direkter Reverse-Proxy
   ```
6. Prüfen: `curl https://garage16.de/health` sollte
   `{"status":"ok","db_ok":true,...}` liefern.

In Synologys Container Manager (DSM 7.2+) funktioniert dasselbe
`docker-compose.yml` direkt als "Projekt" — Container Manager erkennt die
Datei automatisch.

## 2. Docker

Sechs Dienste, jeweils mit eigenem Neustart-Verhalten
(`restart: unless-stopped` — überlebt einen NAS-Neustart automatisch):

| Dienst | Aufgabe |
|---|---|
| `db` | PostgreSQL — die eigentlichen Daten |
| `server` | Die API (FastAPI) |
| `discord_bot` | Discord-Bot (optional, siehe Abschnitt 6) |
| `reverse_proxy` | Caddy — HTTPS, liefert das Web-Frontend aus |
| `cloudflared` | Nur bei Tunnel-Variante |
| `backup` | Optional, siehe Abschnitt 5 — nicht standardmäßig aktiv |

Nützliche Befehle:
```
docker compose ps                    # Status aller Dienste
docker compose logs -f server        # Live-Logs eines Diensts
docker compose restart server        # einen Dienst neu starten
docker compose down && docker compose up -d   # alles neu starten
```

Daten liegen in benannten Docker-Volumes (`db_data`, `telemetry_data`,
...) — die überleben `docker compose down` und einen NAS-Neustart. Nur
`docker compose down -v` (das `-v`!) löscht sie tatsächlich — im
Normalbetrieb nie verwenden.

## 3. `.env`

Die wichtigsten Variablen (vollständige Liste mit Erklärung: `.env.example`):

| Variable | Wofür |
|---|---|
| `LMU_GARAGE_SECRET_KEY` | Verschlüsselt Client-Secrets in der DB — einmal generieren, nie ändern (sonst werden bestehende Secrets unlesbar) |
| `LMU_GARAGE_ADMIN_TOKEN` | Dein Zugang zur Admin-Oberfläche (`#/admin`) |
| `LMU_GARAGE_REGISTRATION_SECRET` | Verhindert, dass Fremde sich registrieren können, sobald die Instanz öffentlich erreichbar ist |
| `POSTGRES_PASSWORD` | Datenbank-Passwort |
| `CADDY_SITE_ADDRESS` oder `CLOUDFLARE_TUNNEL_TOKEN` | Je nach gewählter Zugangsvariante (Abschnitt 1) |

Secrets generieren:
```
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # LMU_GARAGE_SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(32))"                                  # ADMIN_TOKEN, REGISTRATION_SECRET
```

## 4. PostgreSQL

Läuft als eigener Docker-Dienst (`db`), keine separate Installation
nötig. Daten liegen im `db_data`-Volume. Migrationen laufen automatisch
beim Start von `server` (`docker/entrypoint.sh`) — kein manueller Schritt
nötig, auch nicht bei einem Update (siehe Abschnitt 8).

Direkter Zugriff zum Nachschauen (selten nötig):
```
docker compose exec db psql -U garage16 -d garage16
```

## 5. Backup & Restore

**Backup läuft NICHT automatisch von selbst** — zwei gleichwertige
Wege, es einzurichten (einen davon wählen):

- **Containerisiert** (identisch auf NAS und VPS):
  ```
  docker compose --profile backup up -d
  ```
  Läuft dann dauerhaft im Hintergrund, Intervall über
  `BACKUP_INTERVAL_SECONDS` in `.env` einstellbar (Default: täglich).
- **NAS-nativ** (z. B. Synology Task Scheduler): einen geplanten Task
  anlegen, der `./scripts/backup.sh /pfad/zu/backups` ausführt.

Beide sichern PostgreSQL **und** Telemetrie-Dateien zusammen (beide
müssen zueinander passen — ein Lap-Eintrag ohne zugehörige Datei ist ein
Datenintegritätsproblem).

Seit V0.9.8 ist jedes Backup ein Ordner `garage16_<Zeitstempel>/` (Dump, Telemetrie-Archiv,
`manifest.json` mit SHA-256), aus **demselben Moment**: während des Backups werden neue Uploads
kurz mit „Wartung“ abgewiesen (der Client wiederholt sie), laufende werden noch fertig. Der Ordner
erscheint erst, wenn beide Dateien geprüft sind.

**Restore ist ein Release-Gate — nicht nur Backups erstellen, sondern
mindestens einmal echt wiederherstellen und prüfen:**
1. `./scripts/restore.sh /pfad/zu/garage16_<Zeitstempel>` (Ablauf und Rückweg: `docs/ROLLBACK.md`) —
   der Stack läuft dabei weiter; das Skript stoppt und startet server/bot selbst
2. Prüfen: Login funktioniert, Leaderboard zeigt die erwarteten Daten,
   Teams sind vorhanden, eine Testrunde lässt sich hochladen
3. `./scripts/restore.sh --cleanup <stamp>` gibt den Platz des Rückfallpunkts frei

Ohne diesen echten Durchlauf ist ein Backup nur eine unbewiesene Annahme.

## 6. Discord

1. [Discord Developer Portal](https://discord.com/developers/applications)
   → "New Application" → Namen vergeben.
2. Reiter "Bot" → "Add Bot" → unter "Privileged Gateway Intents" nichts
   Zusätzliches nötig (der Bot nutzt nur Slash-Commands).
3. "Reset Token" → Token kopieren → in `.env` als `DISCORD_BOT_TOKEN`
   eintragen.
4. Reiter "OAuth2" → "URL Generator" → Scopes: `bot`, `applications.commands`
   → Permissions: `Send Messages`, `Use Slash Commands` → generierten
   Link öffnen, Bot auf deinen Server einladen.
5. `docker compose up -d discord_bot` (oder den ganzen Stack neu starten).

Fahrer verknüpfen sich selbst: Web-App → Account → **Link Discord** →
"Generate link code" zeigt `/link ABCD-EFGH` (einmalig, 10 Minuten); das
geben sie in einem Kanal ein, in dem der Bot ist. Lösen geht auf derselben
Karte ("Unlink Discord") oder per `/unlink` im Discord. Ein neuer Code
entwertet den vorherigen, eine bestehende Verknüpfung wird nie
überschrieben, und wiederholt falsche Codes werden pro Discord-Konto
gedrosselt (5 Fehlversuche / 10 Minuten; der Zähler liegt im Speicher des
Bot-Prozesses, ein Neustart setzt ihn zurück). Details zu allen
Slash-Commands: `discord_bot/DISCORD_INTEGRATION.md`.

## 7. SMTP

Ohne SMTP-Konfiguration läuft Garage16 im `development`-Modus weiter —
Passwort-Reset- und Verifizierungs-E-Mails werden nur geloggt, nicht
verschickt (praktisch zum Testen, aber Freunde bekommen dann keine
echten E-Mails).

**Einen echten Anbieter einrichten** (jeder SMTP-fähige Anbieter
funktioniert; Beispiel mit einem kostenlosen Kontingent):
1. Bei einem SMTP-Anbieter (z. B. Brevo, Mailgun, oder ein Gmail-
   Konto mit "App-Passwort") Zugangsdaten besorgen.
2. In `.env`:
   ```
   LMU_GARAGE_EMAIL_PROVIDER=smtp
   LMU_GARAGE_SMTP_HOST=smtp.deinanbieter.example
   LMU_GARAGE_SMTP_PORT=587
   LMU_GARAGE_SMTP_USER=dein-benutzername
   LMU_GARAGE_SMTP_PASSWORD=dein-passwort
   LMU_GARAGE_SMTP_FROM=garage16@deine-domain.example
   LMU_GARAGE_FRONTEND_URL=https://deine-domain.example
   ```
3. `docker compose up -d server` (Neustart, damit die neuen Variablen
   greifen).
4. **Echt testen**: in der Web-App "Passwort vergessen" mit deiner
   eigenen E-Mail-Adresse auslösen — die Mail muss tatsächlich ankommen.
   Kommt nichts an: `docker compose logs server | grep -i smtp` prüfen.

`LMU_GARAGE_FRONTEND_URL` muss auf die echte, von außen erreichbare
Domain zeigen — sie steckt im Link, den die E-Mail enthält.

## 8. Updates

1. Neue Version besorgen (neue Codebasis/ZIP an die Stelle der alten).
2. `.env` bleibt unverändert erhalten (nicht überschreiben).
3. ```
   docker compose build
   docker compose up -d
   ```
   Migrationen laufen beim Start automatisch — kein manueller
   `alembic upgrade`-Schritt nötig.
4. Prüfen: `curl https://garage16.de/health` — `version` sollte die
   neue Versionsnummer zeigen, `db_ok: true`.
5. **Neue Client-Version verteilen:** Paket neu bauen und die neue
   `Garage16-Client-Setup.exe` weitergeben (`docs/CLIENT_BUILD.md`).
   Freunde führen sie einfach erneut aus — sie aktualisiert an Ort und
   Stelle, Anmeldung und Runden bleiben. Zum Erzwingen
   `LMU_GARAGE_MIN_CLIENT_VERSION` in `.env` erhöhen und den Download-Link
   in `LMU_GARAGE_CLIENT_DOWNLOAD_URL` eintragen: zu alte Clients zeigen
   dann ein Banner mit Download-Knopf, gefahrene Runden bleiben erhalten.

**Neu in V0.9 — worauf du beim Update achten musst:**
- Das Server-Image enthält neuen Code (Analyse-Endpunkte): baue es neu —
  `docker compose --profile tunnel up -d --build` (ohne `--profile tunnel`,
  falls du keinen Tunnel nutzt). Die Web-Oberfläche kommt direkt aus dem
  `web/`-Ordner; im Browser einmal **Strg+F5** drücken.
- Die neuen Analyse-Seiten laufen unter dem schon bestehenden Pfad
  `/telemetry/…` — an der `Caddyfile` ändert sich nichts.
- Nur Clients ab **0.9.0** zeichnen die neuen Kanäle auf (Federweg, Reifenkräfte,
  TC/ABS, …). **Ältere Clients bleiben kompatibel**: sie laden wie bisher hoch
  (Server und Validierung sind für sie unverändert), ihre Runden zählen fürs
  Leaderboard und lassen sich auf den Basiskanälen vergleichen. Die Seite
  kennzeichnet solche Runden mit **„Basisdaten"** (englisch „Basic data"; Rundenlisten, Leaderboard-
  Auswahl, Dashboard) und erklärt oben in der Analyse, was fehlt. Maßgeblich ist
  dabei immer die tatsächliche Datei, nicht die Versionsnummer.
- Die Adresse der Seite ist jetzt **`https://garage16.de`** — Einrichtung und
  Umzug der Clients: Kapitel 10.
- **Speicherbedarf der Telemetrie:** eine Runde braucht ab jetzt grob
  **4–10 MB** statt früher ~0,3 MB (Schätzung — miss nach den ersten echten
  Sessions: `docker compose exec server du -sh /data/telemetry`). 1000 Runden
  sind also ein paar Gigabyte; das Telemetrie-Volume steckt auch im Backup
  (Abschnitt 5), die USB-Platte muss entsprechend Platz haben.
- Die Analyse-Funktion liest pro Aufruf zwei Telemetrie-Dateien. Die
  Rate-Limit-Obergrenze `LMU_GARAGE_RATELIMIT_ANALYSIS_MAX` (Standard seit V0.9.1:
  900 pro Stunde und Fahrer/IP, vorher 300) musst du für einen Freundeskreis nicht
  anfassen.

**Neu in V0.9.9 — Tracklimits nachvollziehbar, Manipulationsschutz für gespeicherte Runden**

- **Klare Entscheidung je Runde: GÜLTIG / UNGÜLTIG / PRÜFEN.** Eine Runde, die nicht ausreichend
  geprüft werden kann (fehlende Signale, Lücken, unplausible Bewegung, Datei eines anderen Fahrers),
  ist nicht mehr automatisch gültig: Sie wird gespeichert und ist analysierbar, **zählt aber erst
  nach deiner Freigabe** (Admin → Track-Limit-Prüfung, „Runde zählt“). Erst dann gibt es PB/WR/
  Team-Bestzeit und die Discord-Meldung. Runden, die schon vor dem Update „in Prüfung“ waren, zählen
  weiter vorläufig — **keine bestehende Bestzeit ändert sich durch das Update.**
- **Gespeicherte Runden-Dateien sind geschützt:** Größe, SHA-256 und eine Server-Signatur (MAC) je
  Runde. Wird eine Datei nachträglich verändert, ausgetauscht oder gelöscht, wird die Runde beim
  nächsten Zugriff **zurückgestellt** (Quarantäne: raus aus Bestenlisten). Admin → Integrität zeigt
  sie; nach dem Zurückspielen der Datei „Freigeben, wenn intakt“.
- **Audit-Protokoll** (Admin → Integrität): jede Annahme, Quarantäne, Freigabe und Admin-Entscheidung,
  verkettet und gegen Änderung geschützt; der Kettenkopf steht in jedem Backup-Manifest.
- **Eine Datenbank-Migration (0017)**, rein additiv. Client bleibt 0.9.4.
- Neu: `scripts/integrity_check.py`, `scripts/trackcheck_eval.py`, `scripts/bench_compare.py`.
  Ausführliche Anleitung mit allem, was nur auf dem NAS geht: `docs/V0_9_9_NAS_ANLEITUNG.md`.

Update von V0.9.8:

1. **Backup** (noch mit dem alten Ordner):
   ```
   ALT="/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_8/Garage16 - LMU"
   cd "$ALT"
   sudo ./scripts/backup.sh /volumeUSB2/usbshare/garage16-backups
   ```
2. **Neuen Ordner anlegen**, `Garage16_Lmu_V0_9_9.zip` per File Station hineinladen und dort
   entpacken („Hierher extrahieren“), dann `.env` übernehmen:
   ```
   NEU="/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_9/Garage16 - LMU"
   mkdir -p /volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_9
   # … ZIP per File Station in diesen Ordner laden und dort entpacken …
   ls "$NEU"
   sudo cp "$ALT/.env" "$NEU/.env"
   ```
3. **Eigener Integritäts-Schlüssel (empfohlen, VOR dem ersten Start):**
   ```
   python3 -c "import secrets; print(secrets.token_hex(32))"
   sudo nano "$NEU/.env"        # Zeile anhängen:  LMU_GARAGE_INTEGRITY_KEY=<die Ausgabe>
   ```
   Den Schlüssel zusätzlich offline aufbewahren (Passwort-Manager). Ohne ihn können die Signaturen
   die gespeicherten Runden nicht mehr bestätigen. Lässt du die Zeile weg, wird er aus
   `LMU_GARAGE_SECRET_KEY` abgeleitet.
4. **Alten Stack stoppen** (ohne `-v`), **neuen bauen und starten** — die Migration 0017 läuft dabei:
   ```
   cd "$ALT" && sudo docker compose --profile tunnel down
   cd "$NEU" && sudo docker compose --profile tunnel up -d --build
   sudo docker compose --profile tunnel ps
   sudo docker compose exec -T server python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/health/ready').read().decode())"
   ```
   Die Ausgabe muss `"status":"ready"` und `"version":"0.9.9"` enthalten. Im Browser **Strg+F5**.
5. **Alle Runden-Dateien einmal prüfen und signieren:**
   ```
   sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.integrity_check'
   sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.integrity_check --apply --backfill'
   ```
   Der erste Befehl ändert nichts (Exit-Code 1 = etwas gefunden, steht in der Ausgabe). Der zweite
   signiert alle älteren Runden, deren Datei zu ihrer Prüfsumme passt, und stellt kaputte zurück.
6. **Trockenlauf der neuen Regeln über alle gespeicherten Runden** (ändert nichts):
   ```
   sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.revalidate_laps --hold' > ~/v099_trockenlauf.txt
   tail -40 ~/v099_trockenlauf.txt
   ```
   „valid -> review“ und „of which hold a current record“ zeigen, wie viele alte Runden die neuen
   Regeln prüfen lassen würden. **Nichts davon wird angewendet**, solange du nicht `--apply` angibst
   (siehe `docs/V0_9_9_NAS_ANLEITUNG.md`, Teil C).
7. **Ein Backup mit dem neuen Ordner** — im Manifest steht jetzt der Kettenkopf des Audit-Protokolls:
   ```
   sudo ./scripts/backup.sh /volumeUSB2/usbshare/garage16-backups
   cat /volumeUSB2/usbshare/garage16-backups/$(ls -t /volumeUSB2/usbshare/garage16-backups | head -1)/manifest.json
   ```

Zurück auf V0.9.8: **erst die Migration zurücknehmen** (V0.9.8 kennt Revision 0017 nicht und würde
sonst nicht starten), dann im alten Ordner starten:
```
cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_9/Garage16 - LMU"
sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" alembic downgrade 0016'
sudo docker compose --profile tunnel down
cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_8/Garage16 - LMU"
sudo docker compose --profile tunnel up -d
```
(Die Downgrade-Zeile löscht nur das Audit-Protokoll und die drei neuen Spalten (Größe, Request-ID,
Signatur); Runden, Dateien und Urteile bleiben. Runden mit dem Status „held“ oder „quarantined“
zeigt V0.9.8 als „in Prüfung“ an und zählt sie vorläufig mit — vor dem Rückweg in Admin →
Track-Limit-Prüfung entscheiden, wenn das nicht gewollt ist.)

**Neu in V0.9.8 — sicherer Betrieb: echte Client-Adressen, konsistente Backups, sichere Wiederherstellung**

- **Rate-Limits zählen die echte Adresse:** ein selbst geschriebener `X-Forwarded-For`-Header hilft
  niemandem mehr, die Grenzen zu umgehen. Nichts einzustellen — `docker-compose.yml` setzt
  `LMU_GARAGE_TRUSTED_PROXIES=private`, die `Caddyfile` vertraut nur dem Docker-Netz.
- **Backups aus einem Moment:** während des Dumps (Sekunden) bekommen neue Uploads „Wartung“ (503),
  der Client wiederholt sie. Jedes Backup ist jetzt **ein Ordner** `garage16_<Zeitstempel>/` mit
  `db.sql.gz`, `telemetry.tar.gz` und `manifest.json` (Prüfsummen). Der Synology-Task und das
  `backup`-Profil bleiben, wie sie sind.
- **Wiederherstellen:** `./scripts/restore.sh <backup-ordner>` — prüft, stellt in einer NEUEN
  Datenbank bereit, prüft dort, tauscht erst dann, und dreht bei jedem Fehler zurück
  (`docs/ROLLBACK.md`). Alte Backups (zwei lose Dateien): `--allow-unverified`.
- **Neue Doku:** `docs/RUNBOOKS.md` (was tun, wenn …), `docs/ARCHITECTURE.md`,
  `docs/DATA_PROTECTION.md`, `docs/QUALITY.md`.
- **Keine Datenbank-Migration**, kein neuer Client (bleibt 0.9.4), nichts neu zu berechnen.

Update von V0.9.7:

1. **Backup** (noch mit dem alten Skript):
   ```
   ALT="/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_7/Garage16 - LMU"
   cd "$ALT"
   sudo ./scripts/backup.sh /volumeUSB2/usbshare/garage16-backups
   ```
2. **Neuen Ordner anlegen**, `Garage16_Lmu_V0_9_8.zip` per File Station hineinladen und dort
   entpacken („Hierher extrahieren“), dann prüfen und `.env` übernehmen:
   ```
   mkdir -p /volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_8
   # … ZIP per File Station in diesen Ordner laden und dort entpacken …
   ls "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_8/Garage16 - LMU"
   sudo cp "$ALT/.env" "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_8/Garage16 - LMU/.env"
   ```
3. **Alten Stack stoppen** (ohne `-v`):
   ```
   cd "$ALT"
   sudo docker compose --profile tunnel down
   ```
4. **Neuen Stack bauen und starten** (Caddy liest dabei die neue `Caddyfile`):
   ```
   cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_8/Garage16 - LMU"
   sudo docker compose --profile tunnel up -d --build
   sudo docker compose --profile tunnel ps
   sudo docker compose exec -T server python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/health/ready').read().decode())"
   ```
   Die Ausgabe muss `"status":"ready"` und `"version":"0.9.8"` enthalten. Im Browser **Strg+F5**
   (Mac: Cmd+Shift+R).
5. **Ein Backup mit dem neuen Skript** — und kurz ansehen, dass ein Ordner mit `manifest.json`
   entsteht:
   ```
   sudo ./scripts/backup.sh /volumeUSB2/usbshare/garage16-backups
   ls /volumeUSB2/usbshare/garage16-backups/
   ```
   Nutzt du das `backup`-Profil: `sudo docker compose --profile backup up -d` (der Dienst bekommt
   das Telemetrie-Volume jetzt beschreibbar — für die Wartungs-Markierung).
6. **Optional — gespeicherte Kennzahlen auffrischen** (Version 5; betrifft nur Runden mit längeren
   Lücken im Tempo-Signal, die Analyse selbst rechnet ohnehin frisch):
   ```
   sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.consistency_check --recompute-figures'
   ```

Zurück auf V0.9.7: neuen Stack stoppen, im alten Ordner starten — nichts zurückzudrehen (keine
Migration). Backups im neuen Ordner-Format stellt die V0.9.7-`restore.sh` mit den zwei Dateien des
Ordners wieder her.
```
cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_8/Garage16 - LMU"
sudo docker compose --profile tunnel down
cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_7/Garage16 - LMU"
sudo docker compose --profile tunnel up -d
```

**Neu in V0.9.7 — nachprüfbar: Release-Prüfung, Referenzrunden, Datenqualität, Betrieb**

- **Release-Prüfung:** Jede Version wird mit `python -m scripts.release_check` in einer sauberen
  Umgebung mit festen Versionen (`constraints.txt`) geprüft; fehlt etwas, heißt das „nicht
  getestet“ (Exit-Code 6), nicht „grün“. Der Bericht liegt im ZIP unter `release/TEST_REPORT.md`.
  Die Docker-Images installieren jetzt genau diese Versionen.
- **Analyse gegen bekannte Sollwerte geprüft** (simulierte Referenzrunden). Dabei gefunden und
  behoben: wenige fehlende Tempo-Werte ließen alle Kurven verschwinden; ein Neustart mitten in einer
  Datei wurde als Runde analysiert (jetzt abgelehnt).
- **Neue Kachel „Datenqualität“** im LapTime Analyzer: Kanäle, Frames pro Sekunde, Lücken,
  Zeit/Distanz, Auflösung eines Bremspunkts u. a. — bevor man der Analyse misstraut. Fahrhinweise
  zeigen ihre Messgenauigkeit (±), Kurven lassen sich nach Zeitverlust sortieren, der Stint Analyzer
  zeigt 95-%-Bereiche und die Konstanz ohne Trend.
- **Admin → „Betrieb“:** jeder Upload-Versuch mit Ergebnis, Grund und Request-ID (dieselbe ID steht
  im Server-Log), Fehlerquote, Dauer; dazu der **Konsistenz-Check** Datenbank ↔ Telemetriedateien.
- **Eine Datenbank-Migration (0016, neue Tabelle `upload_events`)** — läuft beim Start von selbst,
  ändert keine bestehenden Daten. **Kein neuer Client** (bleibt 0.9.4), kein Neuberechnen nötig.

Update von V0.9.6:

1. **Backup:**
   ```
   ALT="/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_6/Garage16 - LMU"
   cd "$ALT"
   sudo ./scripts/backup.sh /volumeUSB2/usbshare/garage16-backups
   ```
2. **Neuen Ordner anlegen**, `Garage16_Lmu_V0_9_7.zip` per File Station hineinladen und dort
   entpacken („Hierher extrahieren“), dann prüfen und `.env` übernehmen:
   ```
   mkdir -p /volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_7
   # … ZIP per File Station in diesen Ordner laden und dort entpacken …
   ls "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_7/Garage16 - LMU"
   sudo cp "$ALT/.env" "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_7/Garage16 - LMU/.env"
   ```
3. **Alten Stack stoppen** (ohne `-v`):
   ```
   cd "$ALT"
   sudo docker compose --profile tunnel down
   ```
4. **Neuen Stack bauen und starten** (der Build lädt die festen Versionen aus `constraints.txt`,
   beim ersten Mal dauert das einige Minuten; die Migration 0016 läuft beim Start):
   ```
   cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_7/Garage16 - LMU"
   sudo docker compose --profile tunnel up -d --build
   sudo docker compose --profile tunnel ps
   sudo docker compose exec -T server python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/health').read().decode())"
   ```
   Die Ausgabe muss `"version":"0.9.7"` und `"db_ok":true` enthalten. Im Browser **Strg+F5**
   (Mac: Cmd+Shift+R).
5. **Konsistenz prüfen** (nur lesend, ändert nichts):
   ```
   sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.consistency_check'
   ```
   Zeilen mit `!!` brauchen dich (z. B. Runden ohne Datei). „Dateien ohne Runde“ und „Temporäre
   Dateien“ kannst du mit demselben Befehl plus `--delete-orphans` entfernen; `--deep` liest zusätzlich
   jede Datei und vergleicht ihre Prüfsumme. Dasselbe siehst du unter Admin → Betrieb.

Zurück auf V0.9.6: **erst die Migration zurücknehmen** (V0.9.6 kennt Revision 0016 nicht und würde
sonst nicht starten), dann im alten Ordner starten:
```
cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_7/Garage16 - LMU"
sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" alembic downgrade 0015'
sudo docker compose --profile tunnel down
cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_6/Garage16 - LMU"
sudo docker compose --profile tunnel up -d
```
(Die Downgrade-Zeile löscht nur die Upload-Protokoll-Tabelle; Runden und alles andere bleiben.)

**Neu in V0.9.6 — übersichtlicher, einfacher, schneller**

- **Menü in Gruppen** (Meine Garage · Analyse · Rennen; Konto und Download unten), überall der
  Name **Garage16**. Die **Schriften liefert Garage16 selbst aus** (`web/fonts`) — kein Abruf bei
  Google mehr (Datenschutz), funktioniert auch offline im Analysefenster der App.
- **Schneller:** Seiten werden erst geladen, wenn man sie öffnet (Startseite: 9 statt 50
  JS-Dateien, ~130 statt ~580 KB); Dashboard und Teamseite laden ihre Teile gleichzeitig; Caddy
  komprimiert mit zstd.
- **Übersichtlicher:** Dashboard mit Schnellzugriffen (letzte Runde analysieren, gegen Garage16
  Best, Stint Analyzer) und Rundenliste mit Strecken-Auswahl; Aktivitäten **eine Zeile pro Runde**
  (statt dieselbe Runde zwei-, dreimal); Leaderboard zeigt sofort eine Tabelle (meistgefahrene
  Strecke/Klasse), lädt bei jeder Änderung neu, merkt sich die Auswahl in der Adresse und zeigt den
  Abstand zu P1; Teamseite: Ergebnisse zuerst, Einstellungen eingeklappt; Kontoseite: Häufiges
  oben, Client-Zugangsdaten / Sitzungen / Datenexport eingeklappt; Rundenauswahl im LapTime
  Analyzer nach Strecke gruppiert, schnellste Runde markiert; Strecken ohne Runden als kurze Liste.
- **Keine Datenbank-Migration**, keine neue Abhängigkeit, **kein neuer Client** (bleibt 0.9.4),
  kein Neuberechnen der Runden nötig. Die API liefert in den Aktivitäten zusätzlich `lap_id` und
  `car_model` (nur ergänzt).

Update von V0.9.5:

1. **Backup:**
   ```
   ALT="/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_5/Garage16 - LMU"
   cd "$ALT"
   sudo ./scripts/backup.sh /volumeUSB2/usbshare/garage16-backups
   ```
2. **Neuen Ordner anlegen**, `Garage16_Lmu_V0_9_6.zip` per File Station hineinladen und dort
   entpacken („Hierher extrahieren“), dann prüfen und `.env` übernehmen:
   ```
   mkdir -p /volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_6
   # … ZIP per File Station in diesen Ordner laden und dort entpacken …
   ls "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_6/Garage16 - LMU"
   sudo cp "$ALT/.env" "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_6/Garage16 - LMU/.env"
   ```
3. **Alten Stack stoppen** (ohne `-v`):
   ```
   cd "$ALT"
   sudo docker compose --profile tunnel down
   ```
4. **Neuen Stack bauen und starten** (der Reverse Proxy liest dabei die neue `docker/Caddyfile`):
   ```
   cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_6/Garage16 - LMU"
   sudo docker compose --profile tunnel up -d --build
   sudo docker compose --profile tunnel ps
   sudo docker compose exec -T server python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/health').read().decode())"
   ```
   Die Ausgabe muss `"version":"0.9.6"` und `"db_ok":true` enthalten. Im Browser **Strg+F5**
   (Mac: Cmd+Shift+R).

Zurück auf V0.9.5: neuen Stack stoppen, im alten Ordner starten — nichts zurückzudrehen:
```
cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_6/Garage16 - LMU"
sudo docker compose --profile tunnel down
cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_5/Garage16 - LMU"
sudo docker compose --profile tunnel up -d
```

**Neu in V0.9.5 — LapTime Analyzer im Raster, alle Kanäle in voller Auflösung**

- **Ansicht anpassen ist jetzt ein Raster-Editor:** Kacheln liegen auf einem Raster aus 12
  Spalten; verschieben am Griff ⠿, Größe an der Ecke ◢, neue Kacheln links über
  **Wofür? › Art › Kachel** mit Vorschau und fertigen Größen — oder auf leeren Feldern einen
  Kasten aufziehen, dann gibt es nur passende Kacheln. Gespeichert wird weiter nur im Browser;
  ein Layout aus V0.9.1–V0.9.4 wird automatisch übernommen.
- **Genauere Auswertung aller Kanäle:** Die Rundendistanz (`lap_dist`) kommt bei LMU aus dem
  Scoring-Block, der in rFactor 2 nur etwa 5-mal pro Sekunde aktualisiert wird, die übrigen
  Kanäle aus der Telemetrie (~50/s). Bis V0.9.4 hat der Server jede Messung verworfen, bei der
  `lap_dist` noch stand — falls LMU es genauso macht, blieb nur jede zehnte Messung übrig
  (Bremspunkte, Linien, Delta, Dämpfer-Histogramme, Radschlupf). Jetzt bekommt jede Messung ihre
  Distanz aus der Geschwindigkeit, exakt angeglichen an jeden neuen `lap_dist`-Wert. Ob das bei
  dir zutrifft, zeigt jede Analyse-Antwort: `orientation.a.distance_axis` mit `anchors`
  (Messungen mit neuem `lap_dist`) und `kept` (verwendete Messungen). Liegt `anchors` deutlich
  unter `kept`, aktualisiert LMU die Distanz seltener als die Telemetrie.
- **Ideallinien auf der Strecke:** Die Karte zeigt jetzt die Strecke von oben — Asphalt, weiße
  Ränder, rot-weiße Kerbs — mit den Linien jeder Runde darauf und um jede Runde ein blasses Band
  in Fahrzeugbreite. Die Strecke wird aus LMUs Streckendaten (`path_lateral`, `track_edge`,
  Untergrund der Räder) der Runden auf der Karte und bis zu 6 weiteren gültigen Runden derselben
  Strecke rekonstruiert; je mehr Runden von verschiedenen Linien es gibt, desto vollständiger.
  Ein gestrichelter Rand ist geschätzt (dort ist nie jemand entlanggefahren). Runden von Clients
  vor 0.9.0 haben diese Daten nicht.
- Brems-, Einlenk- und Gaspunkte werden zwischen zwei Messungen interpoliert; die höchste
  Verzögerung ist der höchste 0,1-s-Mittelwert (ein Randstein in einem Frame zählt nicht mehr).
- **Keine Datenbank-Migration** (bleibt bei `0015`), **keine neue Abhängigkeit**, **kein neuer
  Client** — der Desktop-Client bleibt 0.9.4, das Analysefenster der App zeigt das neue Raster
  automatisch (es lädt die Seite vom Server).

Update von V0.9.4:

1. **Backup:**
   ```
   ALT="/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_4/Garage16 - LMU"
   cd "$ALT"
   sudo ./scripts/backup.sh /volumeUSB2/usbshare/garage16-backups
   ```
2. **Neuen Ordner anlegen**, ZIP per File Station hineinladen und dort entpacken, `.env` übernehmen:
   ```
   mkdir -p /volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_5
   # … Garage16_Lmu_V0_9_5.zip per File Station in diesen Ordner laden und dort entpacken …
   cp "$ALT/.env" "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_5/Garage16 - LMU/.env"
   ```
3. **Alten Stack stoppen** (ohne `-v`):
   ```
   cd "$ALT"
   sudo docker compose --profile tunnel down
   ```
4. **Neuen Stack bauen und starten:**
   ```
   cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_5/Garage16 - LMU"
   sudo docker compose --profile tunnel up -d --build
   curl -s https://<adresse-der-seite>/health
   ```
   `/health` muss `"version":"0.9.5"` und `"db_ok":true` zeigen (über die Adresse, unter der die
   Seite gerade erreichbar ist). Im Browser **Strg+F5**.
5. **Rundenwerte neu berechnen** (empfohlen — Kurven und Zeitprofil der gespeicherten Runden auf
   der neuen Distanzachse, Version 4). Erst der Probelauf, dann speichern; beides ändert keine
   Gültigkeit:
   ```
   sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.revalidate_laps'
   sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.revalidate_laps --apply'
   ```

Zurück auf V0.9.4: neuen Stack stoppen, im alten Ordner wieder starten — es gibt nichts
zurückzudrehen:
```
cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_5/Garage16 - LMU"
sudo docker compose --profile tunnel down
cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_4/Garage16 - LMU"
sudo docker compose --profile tunnel up -d
```
Der Analyse-Cache rechnet einmal neu (Version 6).

**Neu in V0.9.4 — zwei Dashboards: LapTime Analyzer und Stint Analyzer**

Die Analyse besteht jetzt aus **zwei getrennten Dashboards** (im Browser und im
Analyse-Fenster der App):

- **LapTime Analyzer** (`#/analysis`, vorher „Analyse“) — eine Runde gegen eine andere:
  neues Fenster **Ideallinien** (beide gefahrenen Linien gleichwertig auf einer zoombaren Karte,
  Zwei-Finger-Zoom, Fahrzeugbreite, Maßstab, Brems-/Einlenk-/Scheitel-/Vollgaspunkte je Runde,
  „Runde 2 1,4 m weiter innen“ am Cursor, beim Hineinzoomen Linien in 0,5-m-Auflösung),
  **Kurve im Detail** (Zeit je Phase Bremsen/Einlenken/Scheitel/Ausgang/Gerade — die Phasen
  ergeben genau den Zeitunterschied der Kurve —, jeder Messwert beider Runden, dieselbe Kurve in
  deinen früheren Runden), **Sektoren der Session**, Coach nach Phasen gegliedert, Runde 3/4
  zusätzlich darüberlegen (gestrichelt/gepunktet), Delta oder verstrichene Zeit, Kanal
  „Linie von Runde 2 gegenüber Runde 1“.
- **Stint Analyzer** (`#/stints`, die alte Adresse `#/sessions` führt dorthin) — ganze
  Sessions: Pace und Konstanz mit Filtern (In-/Out-Lap, Verkehr, blaue Flagge, Ausreißer …),
  Stints und Reifensätze, Reifenabbau, Kraftstoff/Energie mit Zielverbrauch und Reserve,
  Fahrzeugzustand, Verkehr, Boxenstopps mit geschätztem Zeitverlust, Prognose (erst ab 5
  sauberen Runden), **Strategierechner**, Stint-Vergleich, Vergleich mit Teamkollegen.
  Schätzungen sind als **Schätzung** markiert, Messwerte als **gemessen**.
- Im Desktop-Client heißen die Knöpfe jetzt **LapTime Analyzer**, **Stint Analyzer** und
  **Analyse last lap**.
- Admin-Bereich, neuer Reiter **Rennregeln**: Regelwerke für den Strategierechner
  (Renndauer, Mindeststopps, maximale Stintlänge, Reifenwechsel-Pflicht, Tanken,
  Boxengassen-Zeiten). Leer lassen, was ein Rennen nicht vorschreibt.

- **Rundenzeit, die LMU zu spät meldet:** LMU schreibt die Zeit der gerade beendeten Runde
  manchmal erst einige Frames, nachdem der Client die Runde abgeschlossen hat. Bis V0.9.3.1 ging
  die Runde dann mit der **Zeit der vorherigen Runde** (oder ohne Zeit) an den Server, und die
  Prüfung „telemetry duration does not match claimed lap_time“ hat eine Runde gestrichen, die
  LMU als gültig gewertet hat. Client 0.9.4 erkennt das (die Zeit passt nicht zur gemessenen
  Rundendauer oder ist exakt die der Vorrunde) und übernimmt LMUs Zeit, sobald LMU sie im
  6-s-Nachlauf meldet. Geschickt wird immer LMUs eigene Zeit, nie eine selbst gemessene.
- **Warum eine Runde nicht zählt**, steht jetzt auch im Stint Analyzer unter jeder ungültigen
  Runde (Tabelle „Alle Runden“), im LapTime Analyzer weiterhin im Fenster „Track-Limits &
  Ereignisse“.
- **Icon:** Client, Analysefenster, Installer und Website zeigen das Garage16-Icon (eine
  kantige „16“) — in der Windows-Taskleiste, im Explorer und als Browser-Tab-Symbol.

**Gültige LMU-Runden, die Garage16 als ungültig zeigt (V0.9.2 / V0.9.3):** Auf der NAS
nachsehen, mit welchem Grund der Server sie gestrichen hat (liest nur, ändert nichts):
```
cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_2/Garage16 - LMU"   # der laufende Ordner
sudo docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT id, track_name, lap_number, round(lap_time::numeric, 3) AS zeit, client_claimed_valid AS client_ok, invalid_reason FROM laps WHERE NOT is_valid ORDER BY id DESC LIMIT 30;"'
```
- `client_ok = f` und als Grund „the desktop client … rejected“: der Client hat die Runde
  verworfen (Boxengasse, keine Zeit, LMU-Flag).
- „telemetry duration (… s) does not match claimed lap_time (… s)“ oder „sector_times sum …“:
  sehr wahrscheinlich die zu spät gemeldete LMU-Rundenzeit (oben) — die Zeit dieser Runde ist
  dann auch falsch gespeichert (die der Vorrunde).
- „lap_dist has implausible backward jumps“ / „lap_dist (22m) does not match distance implied
  by the speed trace“ bei `client_ok = t`: LMU hat die Rundendistanz ein paar Frames VOR dem
  Rundenwechsel zurückgesetzt, und der Client 0.9.2 hat die ersten Meter der nächsten Runde
  an die Runde gehängt. Die Runde selbst ist vollständig und LMUs Zeit stimmt. Der Server 0.9.4
  liest solche Dateien richtig; Client 0.9.4 macht es gar nicht erst.
- „sector_times sum (… s) does not match lap_time“ bei `client_ok = f`: LMU hatte die
  Sektorzeiten noch nicht geschrieben. Client 0.9.4 wartet darauf. Bei 0.9.2-Runden kann der
  Server nicht ausschließen, dass der Client sie auch wegen der Boxengasse verworfen hat — sie
  bleiben ungültig.
- Ab V0.9.4 zeigt `scripts.revalidate_laps` (Schritt 5) im Probelauf, welche Runden nach
  den aktuellen Regeln zählen würden („invalid -> valid (restorable)“) — dazu gehören die
  „lap_dist“-Runden oben; `--apply --restore` lässt sie wieder zählen (sie stehen danach zur
  Kontrolle in der Track-Limit-Prüfung). Eine Runde mit falscher (veralteter) Zeit bleibt
  besser gestrichen — ihre echte Zeit ist nicht gespeichert.

Es gibt eine **Datenbank-Migration** (`0015`, nach `0014`): Sie legt die Spalte
`laps.lap_context` (Boxenstopp-Zähler, Reifenmischung, Schaden, Tankgröße, Position, Restzeit
am Rundenende — vom Client 0.9.4 gemeldet) und die Tabelle `race_rule_sets` an. **Rein
ergänzend — kein vorhandener Wert ändert sich, keine Rundengültigkeit ändert sich.**
Keine neue Abhängigkeit. Die Anleitung gilt für ein Update von V0.9.3.1 (für V0.9.3/V0.9.2
setze `ALT` entsprechend; die Migrationen `0013`/`0014` laufen dann zuerst mit).

1. **Backup** (aus dem laufenden Ordner, auf die USB-Platte):
   ```
   ALT="/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_3_1/Garage16 - LMU"
   cd "$ALT"
   sudo ./scripts/backup.sh /volumeUSB2/usbshare/garage16-backups
   ```
2. **Neuen Ordner anlegen**, ZIP per File Station hineinladen und dort entpacken, `.env`
   übernehmen:
   ```
   mkdir -p /volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_4
   # … Garage16_Lmu_V0_9_4.zip per File Station in diesen Ordner laden und dort entpacken …
   cp "$ALT/.env" "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_4/Garage16 - LMU/.env"
   ```
3. **Alten Stack stoppen** (ohne `-v` — die Volumes mit Datenbank und Telemetrie bleiben):
   ```
   cd "$ALT"
   sudo docker compose --profile tunnel down
   ```
4. **Neuen Stack bauen und starten** (Migration `0015` läuft von selbst):
   ```
   cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_4/Garage16 - LMU"
   sudo docker compose --profile tunnel up -d --build
   sudo docker compose logs server | grep -i -E "alembic|0015|error"
   curl -s https://garage16.de/health
   ```
   `/health` muss `"version":"0.9.4"` und `"db_ok":true` zeigen. Im Browser **Strg+F5**.
5. **Neue Rundenwerte für vorhandene Runden berechnen** (optional, empfohlen). Gespeicherte
   Runden bekommen damit die neuen Kennzahlen aus ihrer Telemetrie-Datei: Kurven für den
   Vergleich mit früheren Runden, Kraftstoff je Sektor, gemessenes Nachtanken und
   Reifenwechsel (damit der Stint Analyzer auch alte Sessions in Stints teilen kann).
   Erst der Probelauf (ändert nichts) — er sollte keine Gültigkeitsänderungen zeigen, weil die
   Track-Limit-Regeln gleich geblieben sind:
   ```
   sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.revalidate_laps'
   ```
   Dann speichern (ändert keine Gültigkeit):
   ```
   sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.revalidate_laps --apply'
   ```
   Was alte Runden nie hatten (Boxengasse, Abstände zu anderen Autos, Reifenmischung,
   Schaden), bleibt leer — das zeichnet erst der Client 0.9.4 auf; die Seite sagt das dazu.
6. **Rennregeln anlegen** (optional): Admin-Bereich → **Rennregeln** → **Neues Regelwerk**.
7. **Client 0.9.4 verteilen** (`docs/CLIENT_BUILD.md`). Er arbeitet auch mit einem Server
   0.9.3.1, 0.9.3 oder 0.9.2 (er lässt dann Stufe für Stufe die Felder weg, die der Server nicht
   kennt). Ältere Clients bleiben nutzbar; ihren Runden fehlen nur die neuen Stint-Daten.
8. **Mit echtem LMU bestätigen** — offener Punkt (w) im Prüfprotokoll
   (`docs/LMU_VERIFICATION_PROTOCOL.md`): wann der Boxenstopp-Zähler steigt, welche
   Mischungsnummer zu welcher Mischung gehört, was die Zeitabstände zeigen. Auf dem PC im
   Projektordner, während du einen Boxenstopp fährst:
   ```
   py -m scripts.track_limits_probe --csv stint.csv
   ```

Was sich im Betrieb ändert:
- **Zurück auf V0.9.3.1:** zuerst mit dem NEUEN Stack die Migration zurückdrehen (löscht die
  Spalte `laps.lap_context` und die Rennregeln), dann wechseln:
  ```
  cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_4/Garage16 - LMU"
  sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" alembic downgrade 0014'
  sudo docker compose --profile tunnel down
  cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_3_1/Garage16 - LMU"
  sudo docker compose --profile tunnel up -d
  ```
- Der Stint Analyzer liest nur aus der Datenbank (keine Telemetrie-Datei) und ist auch bei
  langen Sessions schnell. Die hochaufgelösten Linien beim Hineinzoomen lesen eine Datei pro
  Runde und zählen wie jede Analyse gegen `LMU_GARAGE_RATELIMIT_ANALYSIS_MAX`.
- Der Analyse-Cache rechnet einmal neu (Version 5) — die ersten Aufrufe nach dem Update sind
  minimal langsamer.

**Neu in V0.9.3.1 — Korrekturrelease: Track-Limits in Stufen, Runden nachträglich korrigierbar**

V0.9.3.1 korrigiert die Track-Limit-Prüfung von V0.9.3, bevor sie Ranglisten verfälscht.
Der Kern: Ein steigender Track-Limit-Zähler von LMU ist nur ein Bruchteil eines
Verwarnungspunkts (LMU wertet Verstöße nach Zeitgewinn, 2–5 s nach dem Wiedereinfädeln), und
vier Räder im Kies ohne Zeitgewinn sind für LMU kein Verstoß. V0.9.3 hat solche Runden trotzdem
gestrichen. Jetzt gilt:

- **LMU-Urteil** (LMU macht die Runde selbst ungültig / wertet die Zeit nicht) und
  **feste Garage16-Regeln** (Boxengasse, keine Rundenzeit, unplausible Sektorzeiten) machen
  eine Runde ungültig.
- **Messungen** (Track-Limit-Schritte, alle vier Räder neben der Strecke, jenseits des
  Streckenrands, kürzer als üblich, Strafe, Meldungen kurz nach der Ziellinie) streichen keine
  Runde mehr — die Runde zählt und landet unter **Track-Limit-Prüfung**. Erst wenn echte
  LMU-Aufzeichnungen eine Messung bestätigen, kannst du sie per `.env` scharf schalten
  (`LMU_GARAGE_TRACK_LIMITS_INVALIDATING`, siehe `.env.example`; Standard: leer).
- **Client 0.9.3.1 entscheidet nach denselben Regeln** und schickt seine Gründe mit. Er hält
  eine fertige Runde nach der Ziellinie 6 Sekunden zurück und notiert, was LMU in dieser Zeit
  noch meldet (verspätete Track-Limit-Schritte, eine zurückgezogene Rundenzeit). Unklare
  Fälle („gehört das noch zur alten oder schon zur neuen Runde?“) landen bei beiden Runden in
  der Prüfung.
- **Gespeicherte Runden lassen sich neu bewerten**, ohne neuen Upload — auch Runden, die
  V0.9.3 wegen der zu strengen Regel gestrichen hat.

Es gibt eine **Datenbank-Migration** (`0014`, nach `0013`): Sie legt `laps.check_inputs` an
(woran eine Runde gemessen wurde) und füllt sie für vorhandene Runden aus dem, was noch da ist.
**Keine Rundengültigkeit ändert sich durch die Migration.** Die Anleitung gilt für ein Update
von V0.9.2 **oder** V0.9.3 — setze unten `ALT` auf den Ordner, der gerade läuft.

1. **Backup** (aus dem laufenden Ordner, auf die USB-Platte):
   ```
   ALT="/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_2/Garage16 - LMU"   # oder …_V0_9_3/…
   cd "$ALT"
   sudo ./scripts/backup.sh /volumeUSB2/usbshare/garage16-backups
   ```
2. **Neuen Ordner anlegen**, ZIP per File Station hineinladen und dort entpacken, `.env`
   übernehmen:
   ```
   mkdir -p /volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_3_1
   # … ZIP per File Station in diesen Ordner laden und dort entpacken …
   cp "$ALT/.env" "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_3_1/Garage16 - LMU/.env"
   ```
3. **Alten Stack stoppen** (ohne `-v`):
   ```
   cd "$ALT"
   sudo docker compose --profile tunnel down
   ```
4. **Neuen Stack bauen und starten** (Migrationen `0013` — falls du von V0.9.2 kommst — und
   `0014` laufen von selbst):
   ```
   cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_3_1/Garage16 - LMU"
   sudo docker compose --profile tunnel up -d --build
   sudo docker compose logs server | grep -i -E "alembic|0013|0014|error"
   curl -s https://garage16.de/health
   ```
   `/health` muss `"version":"0.9.3.1"` und `"db_ok":true` zeigen. Im Browser **Strg+F5**.
5. **Runden neu bewerten — erst der Probelauf** (ändert nichts):
   ```
   sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.revalidate_laps'
   ```
   Der Bericht ordnet jede betroffene Runde einem Übergang zu: **valid -> invalid** (die
   Regeln würden eine zählende Runde streichen), **invalid -> valid (restorable)** (eine
   automatisch gestrichene Runde, die nach den neuen Regeln zählt — typisch: von V0.9.3 nur
   wegen Track-Limit-Schritten gestrichen), **kept** (von einem Admin oder einer Meldung
   gestrichen — wird nie angefasst), **valid -> review** (neu in der Prüfliste) und **admin
   decided** (von dir in der Prüfung entschieden — bleibt, wie du entschieden hast).
6. **Speichern** (Gültigkeit bleibt unverändert, neu auffällige Runden kommen in die Prüfung):
   ```
   sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.revalidate_laps --apply'
   ```
   Wenn die Liste „restorable“ stimmt, diese Runden wieder zählen lassen — sie stehen danach
   zur Kontrolle in der Track-Limit-Prüfung:
   ```
   sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.revalidate_laps --apply --restore'
   ```
   `--update-validity` (Streichungen anwenden) nur, wenn du die Liste „valid -> invalid“
   gelesen hast. Beides lässt sich kombinieren und jederzeit wiederholen.
7. **Fahrzeuge prüfen** (Probelauf, dann ggf. anwenden): LMU kürzt Modellnamen nach 29
   Zeichen (z. B. „Lamborghini Huracan LMGT3 Evo“ statt „…Evo2“). Bekannte Fälle setzt der
   Server jetzt auf den vollen Namen, unbekannte bekommen ein „…“ am Ende. Für bereits
   gespeicherte Runden:
   ```
   sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.car_identity_report'
   sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.car_identity_report --apply'
   ```
   Der Abschnitt „To check by hand“ (und im Admin-Bereich **Fahrzeugmodelle → Mit echten Daten
   zu prüfen**) listet Modelle, bei denen echte Daten entscheiden müssen: gekürzte Namen,
   Runden aus LMU-Dateien verschiedener Modelle unter einem Namen, Runden in verschiedenen
   Klassen. Automatisch geändert wird daran nichts.
8. **Client 0.9.3.1 verteilen** (`docs/CLIENT_BUILD.md`). Er arbeitet auch mit einem Server
   0.9.3 oder 0.9.2 (er lässt dann Stufe für Stufe die Felder weg, die der Server nicht kennt).
   Clients 0.9.3 bleiben nutzbar: Lehnt ein 0.9.3-Client eine Runde nur wegen
   Track-Limit-Schritten ab, macht der Server daraus eine Prüfung statt einer Streichung.
9. **Mit echtem LMU bestätigen** — die offenen Punkte stehen im Prüfprotokoll
   (`docs/LMU_VERIFICATION_PROTOCOL.md`, Abschnitt V0.9.3.1, Punkte (r)–(v)). Auf dem PC im
   Projektordner, während du fährst:
   ```
   py -m scripts.track_limits_probe --csv tracklimits.csv
   py -m scripts.car_identity_probe --csv autos.csv
   ```
   Schick mir beide CSV-Dateien und deine Notizen (welche Runde, welche Kurve, was LMU
   angezeigt hat). Erst danach entscheiden wir, ob eine Messung Runden streichen darf.

Was sich im Betrieb ändert:
- **Zurück auf V0.9.3:** zuerst mit dem NEUEN Stack die Migration zurückdrehen, dann wechseln
  (der V0.9.3-Server kennt `0014` nicht und startet sonst nicht):
  ```
  cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_3_1/Garage16 - LMU"
  sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" alembic downgrade 0013'
  sudo docker compose --profile tunnel down
  cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_3/Garage16 - LMU"
  sudo docker compose --profile tunnel up -d
  ```
  Zurück auf V0.9.2 genauso, nur mit `alembic downgrade 0012` und dem V0.9.2-Ordner. Oder das
  Backup aus Schritt 1 zurückspielen (`docs/ROLLBACK.md`).
- **Admin → Track-Limit-Prüfung:** jeder Befund zeigt seine Art (LMU-Urteil, Garage16-Regel,
  Messung) und, aufklappbar, die Regeltabelle dieses Servers mit Wirkung und ob die Bedeutung
  schon mit echten Daten bestätigt ist. Die Prüfliste wird länger als bei V0.9.3 — das ist
  gewollt: Was vorher still gestrichen wurde, siehst du jetzt.
- **Theoretische Bestzeit:** nur noch pro Fahrzeugmodell und getrennt nach Streckenzustand
  (trocken / teilweise nass / nass / unbekannt). Ausgeschlossene Runden werden gezählt und
  genannt, nie still weggelassen.

**Neu in V0.9.3 — worauf du beim Update achten musst:**

Diesmal gibt es eine **Datenbank-Migration** (`0013`): sie ordnet jede Runde ihrem
Fahrzeug**modell** zu und legt Felder für das Track-Limit-Urteil und die Rundenkennzahlen an.
Sie läuft beim Start automatisch, ändert keine Rundenzeit und keine Gültigkeit, und sie ist
umkehrbar (der rohe LMU-Wert bleibt in jeder Runde gespeichert). Trotzdem gilt: **vorher
sichern.** Die Befehle per SSH auf der NAS (Port 2234), Pfade wie bei dir:

1. **Backup** (aus dem laufenden V0.9.2-Ordner, auf die USB-Platte):
   ```
   cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_2/Garage16 - LMU"
   sudo ./scripts/backup.sh /volumeUSB2/usbshare/garage16-backups
   ```
   Am Ende muss ein neuer Ordner `garage16_<Datum>_<Zeit>` mit Datenbank-Dump und
   Telemetrie dort liegen.
2. **Neuen Ordner anlegen**, das ZIP per File Station hineinladen und dort entpacken
   (Rechtsklick → Extrahieren → „Hierher extrahieren“), dann die `.env` aus dem alten Ordner
   übernehmen:
   ```
   mkdir -p /volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_3
   # … ZIP per File Station in diesen Ordner laden und dort entpacken …
   cp "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_2/Garage16 - LMU/.env" \
      "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_3/Garage16 - LMU/.env"
   ```
3. **Alten Stack stoppen** (ohne `-v` — die Daten liegen in den benannten Volumes und
   bleiben):
   ```
   cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_2/Garage16 - LMU"
   sudo docker compose --profile tunnel down
   ```
4. **Neuen Stack bauen und starten** (die Migration läuft dabei von selbst):
   ```
   cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_3/Garage16 - LMU"
   sudo docker compose --profile tunnel up -d --build
   sudo docker compose logs server | grep -i -E "alembic|0013|error"
   curl -s https://garage16.de/health
   ```
   `/health` muss `"version":"0.9.3"` und `"db_ok":true` zeigen. Im Browser einmal **Strg+F5**.
5. **Fahrzeuge prüfen** (nur lesen, ändert nichts):
   ```
   sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.car_identity_report'
   ```
   Du siehst pro LMU-Dateiname, welchem Modell er jetzt zugeordnet ist. Direkt nach dem
   Update stehen ältere Runden als „Modell (Saison)“ da, z. B. „499P (2025)“ — sobald der
   erste Client 0.9.3 eine Runde hochlädt, meldet er alle Autos seiner Session mit LMUs
   eigenem Modellnamen, und der Server ordnet die alten Runden automatisch um.
6. **Alte Runden nachprüfen lassen** (Track-Limits + Kennzahlen für die theoretische
   Bestzeit). Erst der Probelauf — er ändert nichts und zeigt, welche Runden die neuen
   Regeln beanstanden würden:
   ```
   sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.revalidate_laps'
   ```
   Dann speichern (Gültigkeit bleibt unverändert; beanstandete Runden landen im Admin-Bereich
   unter **Track-Limit-Prüfung**):
   ```
   sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.revalidate_laps --apply'
   ```
   Nur wenn du die Liste gelesen hast und die Runden wirklich nicht zählen sollen, zusätzlich
   `--update-validity` anhängen.
7. **Client 0.9.3 verteilen** wie gewohnt (`docs/CLIENT_BUILD.md`). Er ist mit dem neuen Server
   UND mit einem alten Server kompatibel (lehnt ein 0.9.2-Server die neuen Felder ab,
   lädt der Client dieselbe Runde ohne sie hoch). Ältere Clients bleiben ebenfalls nutzbar —
   ihre Runden werden aber nur mit den Signalen geprüft, die sie aufzeichnen.
8. **Mit echtem LMU bestätigen** (einmal, auf dem PC im Projektordner
   `C:\Users\Moritz\Documents\Programmieren\Garage16_Lmu_V0_9_3\Garage16 - Lmu`), während LMU
   in einer Session mit vollem KI-Feld läuft:
   ```
   py -m scripts.car_identity_probe --csv autos.csv
   ```
   Die Liste zeigt jedes Auto so, wie LMU es meldet, und welches Modell Garage16 daraus macht.
   Schick mir `autos.csv` — daran sehe ich, ob LMU z. B. Evo- oder Le-Mans-Varianten im
   Modellnamen unterscheidet. Die Punkte (m)–(q) in `docs/LMU_VERIFICATION_PROTOCOL.md`
   beschreiben die Probefahrten für Track-Limits, Untergrund, Streckenrand, Modelle und Dämpfer.

Was sich im Betrieb ändert:
- **Zurück auf V0.9.2** geht auf zwei Wegen: das Backup aus Schritt 1 zurückspielen
  (`docs/ROLLBACK.md`), oder nur die Migration zurückdrehen. Wichtig ist die Reihenfolge — der
  V0.9.2-Server kennt die Migration 0013 nicht und startet nicht, solange die Datenbank auf 0013
  steht:
  ```
  cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_3/Garage16 - LMU"
  sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" alembic downgrade 0012'
  sudo docker compose --profile tunnel down
  cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_2/Garage16 - LMU"
  sudo docker compose --profile tunnel up -d
  ```
  Der Downgrade stellt die alten Fahrzeugnamen exakt wieder her; Track-Limit-Urteile und
  Kennzahlen entfallen (sie lassen sich nach einem erneuten Update jederzeit neu berechnen).
- **Admin-Bereich:** zwei neue Reiter. **Track-Limit-Prüfung** listet Runden, die die
  automatische Prüfung nicht allein entscheiden konnte (z. B. „jenseits des Streckenrands“ —
  LMU nennt diesen Wert selbst ungefähr). Sie zählen, bis du „Runde zählt“ oder „Runde
  ungültig“ wählst. **Fahrzeugmodelle** zeigt, was der Server über Modelle gelernt hat,
  inklusive Konflikten (wird nie zum Zusammenlegen benutzt).
- **Was eine Runde ungültig macht** (Stand V0.9.3 — **seit V0.9.3.1 geändert, siehe oben**:
  Track-Limit-Schritte und „alle vier Räder“ führen nur noch zur Prüfung): LMU hat die Runde
  ungültig gemacht, LMU wertet die Zeit nicht (`count_lap_flag` ≠ 2), LMU hat
  Track-Limit-Punkte vergeben, alle vier Räder auf Gras/Kies/Erde, oder der Client hat sie
  abgelehnt (Boxengasse). Ein Signal, das unplausibel
  aussieht, wird für diese Runde ignoriert statt sie zu streichen.
- **Speicher:** pro Runde kommen rund 1–4 KB Kennzahlen in der Datenbank dazu. Der Upload liest
  für alle Prüfungen die Telemetrie-Datei weiterhin nur einmal (gemessen an einer synthetischen
  4-Minuten-Runde: ~23 ms statt ~7 ms pro Upload). Nichts einzustellen.
- **Neue Seite „Meine Sessions"** und neue Analyse-Fenster: alles aus vorhandenen Daten,
  keine Einstellung nötig. Das Analyse-Rate-Limit (900/Stunde) reicht weiter.

**Neu in V0.9.2 — worauf du beim Update achten musst:**
- **Keine Datenbank-Migration.** Server neu bauen wie immer, im Browser einmal **Strg+F5**.
- **Zwei Client-Downloads:** „Client“ (`Garage16-Client-Setup.exe`, Analyse im Browser)
  und „Client + Analysetool“ (`Garage16-Client-Analysis-Setup.exe`, Analyse im eigenen
  Fenster). GitHub baut beide (Actions → build-client); ein Tag `client-v0.9.2` legt
  zusätzlich ein GitHub-Release mit allen vier Dateien an. **Das Repository ist privat** —
  Freunde können dort nichts herunterladen. Leg die beiden Setup-Dateien deshalb auf den
  Webspace und trag beide Links in die `.env` ein (Kapitel 10, Teil A, Schritt 5):
  `LMU_GARAGE_CLIENT_DOWNLOAD_URL=…` und neu `LMU_GARAGE_CLIENT_APP_DOWNLOAD_URL=…`.
  Danach `docker compose up -d server`. Die Website zeigt beide unter **Download**.
- **Das Analysefenster meldet sich selbst an:** Es holt sich mit den Zugangsdaten des
  Clients eine ganz normale Web-Sitzung (neu: `POST /accounts/app-session`, nur mit dem
  Client-Token möglich). Die Sitzung steht im Konto des Fahrers als „Garage16 desktop app
  (analysis window)“ und lässt sich dort beenden. Am Server ist nichts einzustellen; neues
  Rate-Limit `LMU_GARAGE_RATELIMIT_APPSESSION_MAX` (Standard 30 pro Stunde) musst du nicht
  anfassen.
- **Nichts Neues im Backup:** Das Fenster zeigt nur die Website an; es speichert keine
  Telemetrie, und am Server kommt nur die Sitzung dazu (wie bei jedem Browser-Login).
- Nicht mit echtem Windows bestätigt: der Windows-Build des Analysetools (der GitHub-Build
  prüft ihn mit `--analysis-selftest`), das Fenster auf WebView2 selbst und der Wechsel
  zwischen beiden Installern. Geprüft wurde das echte Fenster unter Linux (gleicher Code,
  andere Browser-Engine) gegen einen echten Server.

**Neu in V0.9.1 — worauf du beim Update achten musst:**
- **Keine Datenbank-Migration**, keine neue Abhängigkeit. Server-Image neu bauen wie
  oben (`docker compose --profile tunnel up -d --build`), im Browser einmal **Strg+F5**.
  Die `Caddyfile` hat eine kleine Ergänzung (die Web-Dateien werden mit
  `Cache-Control: no-cache` ausgeliefert, damit nach einem Update nie alte und neue
  JavaScript-Teile gemischt laufen) — Caddy-Container also mit neu starten
  (`docker compose up -d` tut das, wenn die Datei geändert ist).
- **Neue Analyse-Oberfläche** (Vergleichsfenster, anpassbares Layout, „Du vs. …“,
  Garage16 Best / Team Best / Meine Beste / Lap ID): Das Layout speichert jeder Fahrer
  nur in seinem eigenen Browser — **nichts davon liegt auf dem Server und nichts davon
  gehört ins Backup**. „Garage16 Best“ ist bewusst nicht „Weltrekord“ genannt: es ist
  die schnellste gültige Runde auf deiner Garage16-Instanz für dieselbe Strecke und
  dasselbe Auto.
- **Weniger Datenverkehr**: Der Server liest pro Analyse nur die gebrauchten Spalten
  der Telemetrie-Datei, tastet die Daten auf höchstens ~1000–3000 Punkte pro Kurve
  herunter und lässt Caddy die Antwort komprimieren. Die Rohdaten bleiben unverändert
  gespeichert. Gemessen an einer **synthetischen** Runde (nicht an echten LMU-Daten):
  eine Analyse einer ~14 km langen Runde sank von rund 990 KiB auf rund 120 KiB
  JSON. Echte Werte bekommst du erst nach den ersten echten Sessions.
- **Feinere Daten beim Hineinzoomen**: Zoomt ein Fahrer weit in eine Runde hinein, lädt
  die Seite für genau diesen Ausschnitt feinere Daten nach (eine Anfrage, sobald die
  Zoom-Bewegung zur Ruhe kommt; der Browser merkt sich schon geholte Ausschnitte und
  pausiert eine Minute, falls der Server „zu viele Anfragen“ meldet). Das ist der Grund,
  warum das Analyse-Limit von 300 auf 900 pro Stunde angehoben wurde. Bewegt man nur
  die Maus über die Diagramme, entstehen keine Anfragen.
- **Zwischenspeicher im Arbeitsspeicher**: höchstens 128 Einträge / 48 MiB / 15 Minuten
  pro Server-Prozess, geht bei jedem Neustart verloren und ist **keine** zweite Kopie
  der Telemetrie. Er prüft nie Berechtigungen für den Server — die Prüfung läuft vor
  jedem Zugriff. Dazu kommen bis zu **32 MiB** bereits gelesener Runden (nur die
  gebrauchten Spalten), damit Zoomen und erneutes Vergleichen derselben Runde die Datei
  nicht noch einmal entpacken muss. Zusammen also höchstens rund 80 MiB pro
  Server-Prozess. Du musst nichts einstellen.
- **Genauer**: Die Analyse hat ein neues Fenster **Kurven** (Zeit pro Kurve, Bremspunkt,
  Scheitel- und Ausgangstempo, gemessen an den Rohdaten; die Kurven ergeben zusammen genau
  den Rundenunterschied). Kurze Ausschläge (Bremstipper, Randsteine), die zwischen zwei
  Punkten der Übersicht lägen, werden als dünne senkrechte Striche gezeigt. „Größter
  Gewinn/Verlust“ rechnet der Server auf einem festen 0,5-m-Raster — auf Handy und PC
  gleich. Ein neues Feld in der Antwort, keine Einstellung; der Ergebnis-Zwischenspeicher
  ist versioniert (`ANALYSIS_CACHE_VERSION = 3`), alte Einträge werden nie verwendet.
- **Weniger Last beim Fahrer**: Der Client 0.9.1 kopiert pro Bild nur noch die drei
  kleinen Teile des LMU-Speichers, die er braucht, statt des ganzen Blocks (~317 KiB,
  60-mal pro Sekunde) — gemessen an einem nachgebauten Speicherblock rund 2 % → 0,07 %
  eines CPU-Kerns — und hält eine Runde beim Aufzeichnen mit ~26 statt ~98 MiB im
  Arbeitsspeicher. Im Browser kostet eine Cursorbewegung nur noch einen Bruchteil, und
  feinere Zoomdaten bauen die Fenster nicht mehr neu auf.
- **Eine Telemetrie-Datei pro Runde** bleibt die einzige dauerhafte Kopie auf dem
  Server (`<sha256>.parquet`). Der Upload prüft sie jetzt direkt aus der Datei und
  hinterlässt bei einem Fehler weder Zwischen- noch Waisen-Dateien. Eine Datei, die nur
  in einem erweiterten Kanal beschädigt ist, fällt beim Upload nicht mehr auf; in der
  Analyse steht dann „Telemetry file could not be read“ statt eines Absturzes.
- **Sprache der Weboberfläche:** Englisch ist Standard, Deutsch wählt jeder Fahrer beim
  ersten Besuch in einem Fenster oder später im Menü (EN | DE). Das wird nur im Browser
  des Fahrers gespeichert; auf dem Server ist nichts einzustellen und nichts zu sichern.
  Meldungen, die direkt vom Server kommen (z. B. Fehlertexte), bleiben englisch.
- **Vorzeichen in der Analyse:** überall „Runde 1 minus Runde 2“ — Minus = Runde 1
  schneller (grün), Plus = langsamer (rot).
- **Wichtig fürs Backup:** Der neue Client löscht die lokale Telemetrie-Datei einer
  Runde **14 Tage nach dem erfolgreichen Upload** (einstellbar, siehe
  `docs/FRIENDS_GUIDE.md`). Danach liegt die einzige Kopie bei dir — ein
  funktionierendes, getestetes Backup (Kapitel 5) ist damit keine Kür mehr. Noch nicht
  hochgeladene oder abgelehnte Runden werden nie gelöscht.
- Client-Version **0.9.1**: ältere Clients (0.8.x, 0.9.0) laden weiter hoch und ihre
  Runden lassen sich öffnen und vergleichen (Basisdaten wie gehabt).
- Nicht mit echten Daten bestätigt: Dateigröße und CPU/RAM echter Aufzeichnungen, das
  Lesen der drei Speicherteile bei laufendem Spiel (gleiche Strukturen wie bisher, aber nur
  mit einem nachgebauten Speicherblock getestet), die Kurvenerkennung an echten Runden,
  der Weg über den Cloudflare-Tunnel, Touch auf echten Handys (geprüft in einem
  echten Chromium mit nachgebildetem Touch, nicht auf iPhone/Safari), das
  Verhalten der Upload-Aufräumlogik unter echter PostgreSQL-Parallelität.
- **Datenschutz-Hinweis (schon vor V0.9):** Die Weboberfläche lädt ihre Schriften von
  Google Fonts; dabei geht die IP-Adresse jedes Besuchers an Google. Für eine in
  Deutschland betriebene Seite ist das ein bekanntes DSGVO-Thema. Abhilfe: die zwei
  Schriften (Oswald, IBM Plex Mono — beide frei lizenziert) selbst ausliefern.

**Serverumzug später (NAS → eigener Server/Domain):** siehe `README.md`,
Abschnitt "Moving the server later". Fahrer müssen nichts tun — laufende
Clients wechseln selbst (alle ~10 Minuten geprüft), **sofern alter und
neuer Server HTTPS nutzen**. Bei einer reinen HTTP-Verbindung wechselt der
Client aus Sicherheitsgründen nicht selbst; dann bekommen Freunde die
neue Adresse von dir und nutzen "Reconnect account…".

## 9. Troubleshooting

| Symptom | Wahrscheinliche Ursache | Was tun |
|---|---|---|
| `/health` antwortet nicht | `server`-Container läuft nicht | `docker compose ps`, dann `docker compose logs server` |
| `/health` liefert `db_ok: false` | PostgreSQL nicht erreichbar | `docker compose logs db`; Container neu gestartet? `docker compose restart db server` |
| Registrierung schlägt fehl (403) | Freund hat das "Registration secret" nicht oder falsch ins Feld beim Registrieren eingetragen | `LMU_GARAGE_REGISTRATION_SECRET` aus deiner `.env` an den Freund weitergeben (der Server verlangt es in Produktion — ohne den Wert startet er gar nicht) |
| Discord-Bot postet nichts | Bot offline oder falscher Token | `docker compose logs discord_bot`; Token in `.env` prüfen |
| Keine Verifizierungs-/Reset-Mails | SMTP nicht konfiguriert oder falsch | Abschnitt 7 oben; `docker compose logs server \| grep -i smtp` |
| Nach Update: alte Fahrer können sich nicht einloggen | Unwahrscheinlich — Migrationen sind rückwärtskompatibel gebaut | `docker compose logs server` auf Migrationsfehler prüfen; im Zweifel `docs/ROLLBACK.md` |
| Freund meldet Banner "Garage16 rejected your login" | Token/Secret zurückgesetzt oder widerrufen | Freund nutzt "Reconnect account…" mit den aktuellen Werten von der Account-Seite |
| Freund kann nicht installieren / Windows warnt | Unsignierte `.exe` → SmartScreen | "Weitere Informationen → Trotzdem ausführen" (siehe `docs/CLIENT_BUILD.md`, Abschnitt "Bekannte Eigenheiten") |
| Analyse meldet "The telemetry of this lap is no longer stored" | Die Datei der Runde fehlt auf der Platte (gelöscht, Volume gewechselt) | Prüfen, ob das `telemetry_data`-Volume dasselbe ist wie vorher; `python -m scripts.telemetry_cleanup` (Dry-Run) listet Runden ohne Datei auf — sie werden nie automatisch gelöscht |
| Analyse: Federweg/Reifen/TC/ABS fehlen für eine Runde | Runde wurde mit einem Client < 0.9.0 aufgezeichnet | Kein Fehler — der Fahrer muss die Runde mit dem neuen Client neu fahren |
| Speicherplatz läuft voll | Verwaiste Telemetrie-Dateien | Aufräum-Werkzeug im Server-Container (Dry-Run zuerst!) — genauer Befehl im README, Abschnitt "Telemetry storage cleanup" |
| Ein Fahrer cheatet / meldet eine verdächtige Runde | — | Admin-Oberfläche (`#/admin`) → Reports-Tab, oder Fahrer sperren im Drivers-Tab |

Für alles, was hier nicht auftaucht: `docker compose logs -f` (alle
Dienste gleichzeitig) zeigt fast immer die eigentliche Fehlermeldung.


## 10. Dashboard auf garage16.de und E-Mail über Gmail

Ziel: Web-Oberfläche **und** API laufen unter **`https://garage16.de`** (eine
Adresse für beides, deshalb bleibt `web/config.js` unverändert). Der Server
bleibt auf der NAS und ist über einen Cloudflare Tunnel erreichbar. Alles hier
ist Konfiguration — keine Code-Änderung und kein GitHub-Release nötig, und du
fasst die `.env` nur **einmal** an.

> **Wichtig vorab:** `garage16.de` zeigt bisher auf deinen All-Inkl-Webspace.
> Damit der Tunnel diese Adresse bekommt, muss sie stattdessen auf den Tunnel
> zeigen. Dein Webspace (Webseite/Download) zieht deshalb auf eine
> Unteradresse, z. B. `download.garage16.de` (Schritt 5). Deine **Mail-Einträge
> (MX, SPF, DKIM) bleiben unangetastet** — sie liegen neben dem Tunnel-Eintrag.

### Teil A: Domain garage16.de

**1. Domain zu Cloudflare holen**
1. Kostenloses Cloudflare-Konto anlegen, „Add a domain" → `garage16.de` →
   Free-Tarif.
2. Cloudflare liest die bestehenden DNS-Einträge ein. **Notiere dir die
   IP-Adresse des A-Eintrags für `@`** (das ist dein All-Inkl-Server, du
   brauchst sie in Schritt 5). Alle Mail-Einträge (MX, TXT) müssen
   übernommen sein; Mail-/Webspace-Einträge stehen auf „DNS only" (graue
   Wolke).
3. Im All-Inkl-KAS bei der Domain die Nameserver auf die zwei umstellen, die
   Cloudflare dir nennt. Das dauert Minuten bis wenige Stunden, bis Cloudflare
   „Active" meldet.

**2. Die alten Web-Einträge entfernen**
In Cloudflare → DNS → die **A-/AAAA-/CNAME-Einträge für `@` und `www`**
löschen (nur diese!). Solange sie existieren, lässt Cloudflare den Tunnel nicht
auf diese Adresse legen („record already exists"). **MX- und TXT-Einträge
bleiben.**

**3. Tunnel anlegen**
1. Cloudflare → Zero Trust → Networks → Tunnels → Create tunnel (Typ
   Cloudflared) und den **Token** kopieren. Cloudflare verlangt für Zero Trust
   manchmal eine hinterlegte Zahlungsmethode; im Free-Tarif kostet es trotzdem
   nichts.
2. **Public Hostname:** Subdomain **leer lassen**, Domain `garage16.de`, Path
   leer, Service-Typ **HTTP**, URL `reverse_proxy:80`.
3. *Optional, empfohlen:* `www.garage16.de` auf `garage16.de` umleiten, damit
   beide Schreibweisen funktionieren: einen zweiten Public Hostname `www` mit
   demselben Ziel anlegen und in Cloudflare unter Rules → Redirect Rules die
   Vorlage „Redirect from WWW to root" verwenden.

**4. Änderungen in der `.env` auf der NAS**
```
CLOUDFLARE_TUNNEL_TOKEN=<der Token>
LMU_GARAGE_CORS_ORIGINS=https://garage16.de
LMU_GARAGE_FRONTEND_URL=https://garage16.de
```
`CADDY_SITE_ADDRESS` bleibt unverändert (Standardwert `:80`).

**5. Webspace auf eine Unteradresse legen** (nur, wenn du ihn weiter nutzt)
1. Im All-Inkl-KAS eine Subdomain `download.garage16.de` anlegen und den
   Webspace-Inhalt dorthin legen.
2. In Cloudflare → DNS einen **A-Eintrag** `download` auf die in Schritt 1
   notierte IP, **„DNS only"** (graue Wolke — sonst kann All-Inkl kein
   Zertifikat ausstellen).
3. Optional in der `.env`, damit zu alte Clients den richtigen Download
   anbieten: `LMU_GARAGE_CLIENT_DOWNLOAD_URL=https://download.garage16.de/Garage16-Client-Setup.exe`
4. Ab V0.9.2 gibt es zwei Downloads. Leg beide Setup-Dateien dorthin und trag auch
   den zweiten ein:
   `LMU_GARAGE_CLIENT_APP_DOWNLOAD_URL=https://download.garage16.de/Garage16-Client-Analysis-Setup.exe`
   Beide erscheinen dann auf **garage16.de → Download** nebeneinander. Fehlt einer,
   steht dort „Noch nicht verfügbar“ statt eines kaputten Knopfs.

### Teil B: E-Mail über Gmail

Dieselbe Datei — ergänze zusätzlich:
```
LMU_GARAGE_EMAIL_PROVIDER=smtp
LMU_GARAGE_SMTP_HOST=smtp.gmail.com
LMU_GARAGE_SMTP_PORT=587
LMU_GARAGE_SMTP_USER=garage16.lmu@gmail.com
LMU_GARAGE_SMTP_PASSWORD=<App-Passwort ohne Leerzeichen>
LMU_GARAGE_SMTP_FROM=garage16.lmu@gmail.com
```
Das App-Passwort erstellst du so: im Google-Konto die Bestätigung in zwei
Schritten aktivieren, dann unter `myaccount.google.com/apppasswords` ein
App-Passwort anlegen.

### Teil C: Umstellen, testen, Clients mitnehmen

Die Reihenfolge ist Absicht: der **alte Quick Tunnel bleibt zuerst laufen**,
damit bestehende Clients den Umzug mitbekommen können.

1. **Neu starten** (im Projektordner auf der NAS; `--build`, weil V0.9 neuen
   Code mitbringt):
   ```
   sudo docker compose --profile tunnel up -d --build
   ```
   Den alten Quick Tunnel (separater Container, `sudo docker ps`) **noch nicht**
   stoppen.
2. **Testen.** `https://garage16.de/health` muss `"status":"ok"` und die neue
   Version liefern. Web-App öffnen, anmelden (Web-Logins gelten nur für die
   Adresse, auf der man sich angemeldet hat — einmal neu anmelden). Dann eine
   E-Mail-Adresse setzen: die Verifizierungsmail muss ankommen, und der Link darin
   muss auf `garage16.de` zeigen. Wenn nicht:
   `sudo docker compose logs server | grep -i smtp`.
3. **Clients umstellen — Variante 1, automatisch.** In der `.env` ergänzen:
   ```
   LMU_GARAGE_MIGRATED_TO=https://garage16.de
   ```
   und `sudo docker compose up -d server`. Der Server sagt dann über
   `/health`, wohin er umgezogen ist. Laufende Clients (ab 0.8.6) prüfen das alle
   ca. 10 Minuten und schalten **selbst** auf `https://garage16.de` um. Das
   klappt nur, solange der Client in diesem Zeitraum **läuft**, und nur, weil
   beide Adressen HTTPS nutzen. Ein Client, der schon auf `garage16.de` zeigt,
   tut dabei nichts.
4. **Variante 2, von Hand** (für alle, die nicht mitgekommen sind — z. B. weil der
   Client nicht lief): auf dem PC `py -m client.main --reconfigure` (bei der
   installierten Version der Button „Reconnect account…") und die Adresse
   `https://garage16.de` eintragen. Neue Installer bauen die Adresse selbst ein
   (siehe `docs/CLIENT_BUILD.md`).
5. **Aufräumen**, wenn alle umgestellt sind (Faustregel: ein paar Tage warten):
   den Quick Tunnel stoppen (`sudo docker rm -f <name>`) und die Zeile
   `LMU_GARAGE_MIGRATED_TO` wieder aus der `.env` entfernen, dann
   `sudo docker compose up -d server`.

### Gut zu wissen

- Die Quick-Tunnel-Adresse (`….trycloudflare.com`) ändert sich, sobald der
  Quick-Tunnel-Container neu erstellt wird. Hat ein Client nur diese alte Adresse
  und der Container wurde neu erstellt, erreicht er den Server nicht mehr — dann
  hilft nur Variante 2.
- Soll die App lieber unter einer Unteradresse laufen (z. B. `app.garage16.de`),
  damit `garage16.de` beim Webspace bleibt: im Tunnel die Subdomain `app`
  eintragen, die Adresse überall in der `.env` ersetzen, und Schritt 2 und 5
  entfallen.
- Gmail als Absender reicht für einen Freundeskreis völlig aus. Später kannst du
  auch eine Adresse wie `noreply@garage16.de` über den All-Inkl-SMTP nutzen; dafür
  änderst du nur Host, User, Passwort und From in der `.env`.
