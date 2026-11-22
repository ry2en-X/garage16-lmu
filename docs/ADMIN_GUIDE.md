# Admin-Leitfaden (V1.0.2)

Kurz und praktisch — für den, der Garage16 auf der NAS betreibt, in der Reihenfolge, in der man es
braucht. Die technische Referenz zu jedem Punkt (alle Einstellungen, beide Zugangsvarianten,
Backups, Umzug) steht auf Englisch in `docs/DEPLOYMENT.md`; was im Störfall zu tun ist, in
`docs/RUNBOOKS.md`.

**Stand unserer Instanz:** NAS (Synology), Cloudflare **Quick Tunnel** (Adresse
`….trycloudflare.com`, siehe Kapitel 10 für den späteren Umzug auf `garage16.de`), PostgreSQL,
Gmail als Absender, tägliches Backup auf die USB-Platte (`/volumeUSB2/usbshare/garage16-backups`).
Projektordner je Version unter `/volume1/homes/Moritz/garage16/Garage16_Lmu_<Version>/Garage16 - LMU`.

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
   `docs/DEPLOYMENT.md` unter "Production deployment"; die eigene Domain
   für den Tunnel: Kapitel 10.
5. Starten:
   ```
   docker compose --profile tunnel up -d      # Cloudflare-Tunnel-Variante
   # oder
   docker compose up -d                        # direkter Reverse-Proxy
   ```
6. Prüfen (auf der NAS, unabhängig von der öffentlichen Adresse):
   ```
   sudo docker compose exec -T server python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/health/ready').read().decode())"
   ```
   muss `"status":"ready"` liefern. Von außen: `https://<deine Adresse>/health`.

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
| `cloudflared` | Nur bei Tunnel-Variante mit eigener Domain (`--profile tunnel`). Ein Quick Tunnel (`….trycloudflare.com`) läuft als eigener Container außerhalb des Stacks — siehe `docs/DEPLOYMENT.md` |
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
| `GARAGE16_SECRET_KEY` | Verschlüsselt Client-Secrets in der DB — einmal generieren, nie ändern (sonst werden bestehende Secrets unlesbar) |
| `GARAGE16_ADMIN_TOKEN` | Dein Zugang zur Admin-Oberfläche (`/admin`) |
| `GARAGE16_REGISTRATION_SECRET` | Verhindert, dass Fremde sich registrieren können, sobald die Instanz öffentlich erreichbar ist |
| `GARAGE16_OPEN_REGISTRATION=1` | V1.0: jeder darf sich auf der Website registrieren (statt des Kennworts; eins von beiden ist Pflicht) |
| `GARAGE16_OPERATOR_NAME` / `_ADDRESS` / `_EMAIL` | V1.0: Betreiber für Impressum und Datenschutzerklärung (Seite „Rechtliches“) |
| `GARAGE16_REQUIRE_EMAIL_VERIFICATION`, `GARAGE16_UNVERIFIED_ACCOUNT_DAYS`, `GARAGE16_NEW_DRIVER_REVIEW_LAPS`, `GARAGE16_MAX_DEVICES` | V1.0.1: Kontoregeln (E-Mail vor dem Verbinden bestätigen, unbestätigte Anmeldungen nach 7 Tagen löschen, Fair-Play-Prüfung neuer Fahrer, PCs pro Konto) — die Standards passen, nur bei Bedarf setzen |
| `POSTGRES_PASSWORD` | Datenbank-Passwort |
| `CADDY_SITE_ADDRESS` oder `CLOUDFLARE_TUNNEL_TOKEN` | Je nach gewählter Zugangsvariante (Abschnitt 1) |

Secrets generieren:
```
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # GARAGE16_SECRET_KEY
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
  anlegen, der `./scripts/ops/backup.sh /pfad/zu/backups` ausführt.

Beide sichern PostgreSQL **und** Telemetrie-Dateien zusammen (beide
müssen zueinander passen — ein Lap-Eintrag ohne zugehörige Datei ist ein
Datenintegritätsproblem).

Seit V0.9.8 ist jedes Backup ein Ordner `garage16_<Zeitstempel>/` (Dump, Telemetrie-Archiv,
`manifest.json` mit SHA-256), aus **demselben Moment**: während des Backups werden neue Uploads
kurz mit „Wartung“ abgewiesen (der Client wiederholt sie), laufende werden noch fertig. Der Ordner
erscheint erst, wenn beide Dateien geprüft sind.

Seit V1.0.2 sind die Backups **inkrementell**: die Datenbank wird jedes Mal ganz gesichert, von der
Telemetrie nur die Dateien, die noch nicht im gemeinsamen Ordner `telemetry-store/` (neben den
Backup-Ordnern) liegen; das Manifest listet, welche Dateien zu diesem Stand gehören. Dateien, die
kein Backup mehr braucht, räumt das Skript selbst weg. `telemetry-store/` nie von Hand löschen.

**Restore ist ein Release-Gate — nicht nur Backups erstellen, sondern
mindestens einmal echt wiederherstellen und prüfen:**
1. `./scripts/ops/restore.sh /pfad/zu/garage16_<Zeitstempel>` (Ablauf und Rückweg: `docs/ROLLBACK.md`) —
   der Stack läuft dabei weiter; das Skript stoppt und startet server/bot selbst
2. Prüfen: Login funktioniert, Leaderboard zeigt die erwarteten Daten,
   Teams sind vorhanden, eine Testrunde lässt sich hochladen
3. `./scripts/ops/restore.sh --cleanup <stamp>` gibt den Platz des Rückfallpunkts frei

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
echten E-Mails). **Seit V1.0.1 verbindet sich der Desktop-Client erst mit
bestätigter E-Mail** — ohne funktionierendes SMTP also
`GARAGE16_REQUIRE_EMAIL_VERIFICATION=0` setzen. Unsere Instanz verschickt
über Gmail (Kapitel 10, Teil B).

**Einen echten Anbieter einrichten** (jeder SMTP-fähige Anbieter
funktioniert; Beispiel mit einem kostenlosen Kontingent):
1. Bei einem SMTP-Anbieter (z. B. Brevo, Mailgun, oder ein Gmail-
   Konto mit "App-Passwort") Zugangsdaten besorgen.
2. In `.env`:
   ```
   GARAGE16_EMAIL_PROVIDER=smtp
   GARAGE16_SMTP_HOST=smtp.deinanbieter.example
   GARAGE16_SMTP_PORT=587
   GARAGE16_SMTP_USER=dein-benutzername
   GARAGE16_SMTP_PASSWORD=dein-passwort
   GARAGE16_SMTP_FROM=garage16@deine-domain.example
   GARAGE16_FRONTEND_URL=https://deine-domain.example
   ```
3. `docker compose up -d server` (Neustart, damit die neuen Variablen
   greifen).
4. **Echt testen**: in der Web-App "Passwort vergessen" mit deiner
   eigenen E-Mail-Adresse auslösen — die Mail muss tatsächlich ankommen.
   Kommt nichts an: `docker compose logs server | grep -i smtp` prüfen.

`GARAGE16_FRONTEND_URL` muss auf die echte, von außen erreichbare
Domain zeigen — sie steckt im Link, den die E-Mail enthält.

## 8. Updates

### 8.1 So läuft jedes Update

Jede Version kommt in einen **eigenen Ordner** neben dem alten — so bleibt der alte Stand bis zum
Schluss unangetastet und ist der Rückweg. Die Daten (Datenbank, Telemetrie) liegen in Docker-Volumes
und gehören keinem Ordner; der neue Stack übernimmt sie automatisch (gleicher Projektname
`garage16`).

1. **Backup** im alten Ordner (`scripts/ops/backup.sh`, bis V1.0.1 direkt scripts/backup.sh; Kapitel 5).
2. **Neuer Ordner**, ZIP hinein, entpacken, die `.env` aus dem alten Ordner kopieren.
3. **Alten Stack stoppen** — immer **ohne** `-v` (das `-v` löscht die Volumes!).
4. **Neuen Stack bauen und starten**: `sudo docker compose --profile tunnel up -d --build`.
   Migrationen laufen dabei von selbst.
5. **Prüfen**: `/health/ready` zeigt `"status":"ready"` und die neue Version; im Browser **Strg+F5**.
6. **Neues Backup** aus dem neuen Ordner und die **tägliche Backup-Aufgabe** im DSM-Aufgabenplaner
   auf den neuen Ordner umstellen.
7. Den alten Ordner erst löschen, wenn die neue Version ein paar Tage sauber läuft.

Auf dem PC: Git-Arbeitskopie aktualisieren und — wenn es einen neuen Client gibt — einen Tag
`client-vX.Y.Z` pushen (Kapitel 11). Zum Erzwingen eines Client-Updates
`GARAGE16_MIN_CLIENT_VERSION` erhöhen; zu alte Clients zeigen dann ein Banner mit Download-Knopf,
ihre Runden bleiben erhalten. Ab V1.0.1 zeigen ältere Clients ohnehin einen Hinweis „neue Version
verfügbar“ (freiwillig).

Abkürzungen für die Befehle unten (einmal pro SSH-Sitzung einfügen; `G16` ist der Basisordner auf
unserer NAS — auf einer anderen NAS anpassen):
```
G16=/volume1/homes/Moritz/garage16
BACKUPS=/volumeUSB2/usbshare/garage16-backups
```

### 8.2 Neu in V1.0.2 — lesbare Links, Website auf Cloudflare Pages, aufgeräumter Code

- **Lesbare Adressen:** statt `#/leaderboard?track=…` jetzt z. B. `garage16.de/leaderboard/fuji-speedway/hypercar`,
  `/driver/<name>`, `/team/<name>`, `/analysis/123/vs/456`, `/stints/fuji-speedway/2026-11-14`,
  `/impressum`, `/datenschutz`, `/terms`. **Alte Links (`#/…`) leiten von selbst weiter.**
  Nach dem Umbenennen eines Fahrers oder Teams führt der alte Link weiter zum neuen Namen.
- **Vorschau in Discord:** jede Seite hat einen eigenen Titel („Fuji Speedway · Hypercar – Garage16“);
  Bestenliste, Analyse und Fahrerprofil haben einen Knopf **„Link kopieren“**; unbekannte Adressen zeigen
  eine echte 404-Seite.
- **Menü auf Deutsch:** „Rundenanalyse“ und „Stint-Analyse“.
- **API unter `/api/v1`.** Die alten Pfade bleiben für Clients 1.0.0/1.0.1 erhalten, bis
  `GARAGE16_MIN_CLIENT_VERSION` angehoben wird.
- **Website auf Cloudflare Pages** (optional, empfohlen): die Seite selbst kommt dann von Cloudflare,
  die NAS beantwortet nur noch die API. Ohne Pages läuft alles wie bisher über den Tunnel.
- **Schneller:** Dateien mit Versionsnummer werden ein Jahr gecacht, öffentliche Bestenlisten 30–60 s;
  neuer Index für die Bestenliste; Streckenkarte, Kachel-Editor und Stint-Diagramme laden erst bei Bedarf.
- **Backups jetzt inkrementell:** die Datenbank wird jedes Mal ganz gesichert, von der Telemetrie nur
  noch neue Dateien (Ordner `telemetry-store/` neben den Backups). Alte Backups (Format 1) lassen sich
  weiterhin wiederherstellen und werden wie bisher nach und nach aussortiert.
- **Neue Namen der Einstellungen:** `LMU_GARAGE_*` heißt jetzt `GARAGE16_*`. **Die alte `.env` funktioniert
  unverändert** (alte Namen werden weiter gelesen); umbenennen kannst du in Ruhe.
- **Skripte liegen in Unterordnern:** `scripts/ops/` (Betrieb: backup, restore, Prüfungen),
  `scripts/dev/`, `scripts/release/`. **Die DSM-Backup-Aufgabe muss auf `scripts/ops/backup.sh` zeigen.**
- **Client 1.0.2:** spricht die neue API (fällt bei einem älteren Server von selbst auf die alten Pfade
  zurück); Einstellungen liegen in `%USERPROFILE%\.garage16` (wird beim ersten Start aus `.lmu_garage`
  übernommen).
- **Eine Migration (0020):** Spalten `drivers.slug`, `teams.slug`, Tabelle `slug_redirects`, zwei Indizes
  auf `laps`. Rein additiv.

**Update der NAS von V1.0.1 auf V1.0.2**

1. **Backup** (noch im alten Ordner):
   ```
   ALT="$G16/Garage16_Lmu_V1_0_1/Garage16 - LMU"
   cd "$ALT"
   sudo ./scripts/backup.sh "$BACKUPS"      # der alte Ordner (V1.0.1) hat das Skript noch direkt unter scripts/
   ```
2. **Neuen Ordner anlegen**, `Garage16_Lmu_V1_0_2.zip` per File Station hineinladen und dort entpacken,
   `.env` übernehmen:
   ```
   mkdir -p "$G16/Garage16_Lmu_V1_0_2"
   # … ZIP per File Station in diesen Ordner laden und dort entpacken …
   NEU="$G16/Garage16_Lmu_V1_0_2/Garage16 - LMU"
   ls "$NEU"
   sudo cp "$ALT/.env" "$NEU/.env"
   ```
3. **Alten Stack stoppen** (ohne `-v`), **neuen bauen und starten** — Migration 0020 läuft dabei:
   ```
   cd "$ALT" && sudo docker compose --profile tunnel down
   cd "$NEU" && sudo docker compose --profile tunnel up -d --build
   sudo docker compose --profile tunnel ps
   sudo docker compose exec -T server python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/api/v1/health/ready').read().decode())"
   ```
   Erwartet: `"status":"ready"`, `"version":"1.0.2"`. Die Healthchecks prüfen jetzt alle 30–60 s;
   `ps` kann deshalb in der ersten Minute noch „starting“ zeigen. Der `quicktunnel`-Container bleibt
   unangetastet.
4. **Im Browser prüfen** (Strg+F5): Bestenliste öffnen — die Adresse lautet z. B.
   `/leaderboard/fuji-speedway/hypercar`; „Link kopieren“ ausprobieren; einen alten Link mit `#/`
   öffnen — er springt auf die neue Adresse; `/gibtsnicht` zeigt die 404-Seite.
5. **Konsistenz prüfen** (ändert nichts):
   ```
   sudo docker compose exec -T server sh -c 'GARAGE16_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.ops.consistency_check'
   ```
6. **Backup mit dem neuen Ordner** (jetzt inkrementell) und Aufgabenplaner umstellen:
   ```
   sudo ./scripts/ops/backup.sh "$BACKUPS"
   ls "$BACKUPS"            # neu: Ordner telemetry-store/ neben den garage16_<Zeit>-Ordnern
   ```
   DSM → Systemsteuerung → Aufgabenplaner → die tägliche Garage16-Backup-Aufgabe → Befehl auf
   `cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V1_0_2/Garage16 - LMU" && ./scripts/ops/backup.sh /volumeUSB2/usbshare/garage16-backups`
   ändern. Den Ordner `telemetry-store/` **nie einzeln löschen** — alle neuen Backups verweisen darauf.
7. **Client 1.0.2 veröffentlichen** (auf dem PC, Kapitel 11): Tag `client-v1.0.2` pushen.
8. **Optional: Website auf Cloudflare Pages umziehen** — Schritt für Schritt in `docs/DEPLOYMENT.md`,
   Abschnitt „The website on Cloudflare Pages“. Kurz:
   1. Im Cloudflare-Dashboard dem bestehenden Tunnel einen zweiten öffentlichen Hostnamen
      `api.garage16.de` → `http://reverse_proxy:80` geben.
   2. Ein langes Zufallswort erzeugen (`openssl rand -hex 32`) und als `GARAGE16_EDGE_KEY=` in die `.env`
      eintragen, dann `sudo docker compose --profile tunnel up -d`.
   3. Workers & Pages → Pages-Projekt `garage16` anlegen, Umgebungsvariablen `GARAGE16_API_ORIGIN=https://api.garage16.de`
      und `GARAGE16_EDGE_KEY=<dasselbe Wort>` setzen, eigene Domain `garage16.de` zuweisen.
   4. In GitHub (Settings → Secrets and variables → Actions) die Secrets `CLOUDFLARE_API_TOKEN`,
      `CLOUDFLARE_ACCOUNT_ID` und die Variable `CLOUDFLARE_PAGES_PROJECT=garage16` anlegen. Ab dann
      veröffentlicht jeder Push auf `main` (nach grüner CI) die Website von selbst.
   5. Cloudflare-Regeln (kostenloser Plan): Cache-Regel für `/api/v1/public/` und die
      Rate-Limit-Regel aus `docs/DEPLOYMENT.md`, „Cloudflare rules“.
   6. Vorher die Datenschutzerklärung anpassen (Cloudflare liefert dann auch die Website aus) —
      siehe `docs/DATA_PROTECTION.md`, „Verantwortung beim Betrieb“, V1.0.2.

**Zurück auf V1.0.1:** erst die Migration zurücknehmen, dann im alten Ordner starten:
```
cd "$G16/Garage16_Lmu_V1_0_2/Garage16 - LMU"
sudo docker compose exec -T server sh -c 'GARAGE16_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" alembic downgrade 0019'
sudo docker compose --profile tunnel down
cd "$G16/Garage16_Lmu_V1_0_1/Garage16 - LMU"
sudo docker compose --profile tunnel up -d
```
(Der Downgrade löscht nur die Kurz-Namen und Weiterleitungen; lesbare Links funktionieren dann nicht
mehr, alles andere bleibt. Falls Pages schon eingerichtet ist: im Pages-Projekt die eigene Domain
wieder entfernen, damit `garage16.de` wieder über den Tunnel läuft. Inkrementelle Backups (Format 2)
kann V1.0.1 nicht wiederherstellen — vorher ein Backup mit V1.0.1 anlegen oder mit `restore.sh` aus
V1.0.2 zurückspielen.)

### 8.3 Neu in V1.0.1 — Konten für eine offene Instanz, mehrere PCs, aufgeräumte Doku

- **E-Mail zuerst bestätigen:** Neue Konten melden den Browser nicht mehr sofort an; der Desktop-Client
  verbindet sich erst, wenn die E-Mail bestätigt ist (`GARAGE16_REQUIRE_EMAIL_VERIFICATION`,
  Standard an). Nie bestätigte Anmeldungen ohne Runden und ohne Team werden nach 7 Tagen gelöscht
  (`GARAGE16_UNVERIFIED_ACCOUNT_DAYS`). **Beta-Fahrer, die ihre E-Mail nie bestätigt haben,
  müssen das beim nächsten Verbinden nachholen** (Konto → „Bestätigungs-E-Mail erneut senden“) —
  bereits verbundene Clients laden weiter hoch.
- **Nutzungsbedingungen:** Konten von vor V1.0 (und alle nach einer Änderung der Texte) werden nach
  dem Anmelden auf der Website einmal um Zustimmung gebeten; der Client 1.0.1 fragt beim Verbinden.
- **Mehrere PCs pro Konto:** Rig und Laptop bleiben gleichzeitig verbunden (bis 5,
  `GARAGE16_MAX_DEVICES`), sichtbar und trennbar unter Konto → Desktop-Client & Anmeldungen →
  Verbundene PCs. Bereits verbundene Clients behalten ihre Anmeldung.
- **Fair Play:** Fährt ein neuer Fahrer (weniger als 10 offizielle Runden,
  `GARAGE16_NEW_DRIVER_REVIEW_LAPS`) schneller als das Garage16 Best anderer, wartet die Runde auf
  dich (Admin → Track-Limit-Prüfung, mit Begründung). Siehe Kapitel 12.
- **Eindeutige Fahrernamen** (Groß-/Kleinschreibung, unsichtbare Zeichen und Breitschrift zählen
  nicht als Unterschied; „admin“, „garage16“ u. ä. sind reserviert). Schon doppelt vergebene Namen
  bleiben bestehen; der jüngere Fahrer muss beim nächsten Umbenennen einen freien Namen wählen.
- **Schutz der Konten:** gleiche Antwort bei der Registrierung, egal ob die Adresse schon ein Konto
  hat; Fehlversuche beim Anmelden werden zusätzlich pro E-Mail-Adresse gezählt; Mails an eine
  Adresse sind begrenzt. Die Webseite sendet jetzt Sicherheitsheader (CSP, kein Einbetten in fremde
  Seiten).
- **Client 1.0.1:** speichert die Anmeldung unter Windows verschlüsselt, schickt den Computernamen
  (für die PC-Liste), warnt vor `http://`, zeigt freiwillige Updates an.
- **Eine Migration (0019)**, rein additiv: Spalte `drivers.display_name_key` und Tabelle
  `device_tokens`. Neue `.env`-Einträge sind **nicht nötig** (alle haben sinnvolle Standards,
  siehe `.env.example`).
- Die Dokumentation ist aufgeräumt: alte Update-Abschnitte stehen in
  `docs/archive/UPDATES_V0_9_BIS_V1_0.md`, die technische Referenz in `docs/DEPLOYMENT.md`.

**Update der NAS von V1.0.0 auf V1.0.1**

1. **Backup** (noch im alten Ordner):
   ```
   ALT="$G16/Garage16_Lmu_V1_0_0/Garage16 - LMU"
   cd "$ALT"
   sudo ./scripts/backup.sh "$BACKUPS"
   ```
2. **Neuen Ordner anlegen**, `Garage16_Lmu_V1_0_1.zip` per File Station hineinladen und dort
   entpacken („Hierher extrahieren“), `.env` übernehmen:
   ```
   mkdir -p "$G16/Garage16_Lmu_V1_0_1"
   # … ZIP per File Station in diesen Ordner laden und dort entpacken …
   NEU="$G16/Garage16_Lmu_V1_0_1/Garage16 - LMU"
   ls "$NEU"
   sudo cp "$ALT/.env" "$NEU/.env"
   ```
3. **Alten Stack stoppen** (ohne `-v`), **neuen bauen und starten** — Migration 0019 läuft dabei:
   ```
   cd "$ALT" && sudo docker compose --profile tunnel down
   cd "$NEU" && sudo docker compose --profile tunnel up -d --build
   sudo docker compose --profile tunnel ps
   sudo docker compose exec -T server python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/health/ready').read().decode())"
   ```
   Erwartet: `"status":"ready"`, `"version":"1.0.1"`. Der Quick-Tunnel-Container (`quicktunnel`)
   gehört nicht zum Stack und läuft unverändert weiter — **nicht neu erstellen**, sonst ändert sich
   die Adresse (`docs/RUNBOOKS.md`, „Quick-Tunnel-Adresse hat sich geändert“).
4. **Im Browser prüfen** (Strg+F5): Menü → **Konto** → angemeldet erscheint ggf. der Dialog
   „Nutzungsbedingungen und Datenschutzerklärung“ (einmal akzeptieren); unter „Desktop-Client &
   Anmeldungen“ steht **Verbundene PCs**. Ein Blick in die Entwicklerwerkzeuge (F12 → Konsole) zeigt
   keine roten CSP-Fehler.
5. **Konsistenz prüfen** (ändert nichts):
   ```
   sudo docker compose exec -T server sh -c 'GARAGE16_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.ops.consistency_check'
   ```
6. **Doppelte Fahrernamen finden** (nur Anzeige — sie bleiben bestehen, siehe oben):
   ```
   sudo docker compose exec -T db psql -U garage16 -d garage16 -c "SELECT id, display_name, created_at FROM drivers WHERE display_name_key IS NULL ORDER BY id;"
   ```
   Leer = keine Doppel.
7. **Backup mit dem neuen Ordner** und Aufgabenplaner umstellen:
   ```
   sudo ./scripts/backup.sh "$BACKUPS"
   ```
   DSM → Systemsteuerung → Aufgabenplaner → die tägliche Garage16-Backup-Aufgabe → Pfad auf
   `Garage16_Lmu_V1_0_1` ändern.
8. **Client 1.0.1 veröffentlichen** (auf dem PC, Kapitel 11): Tag `client-v1.0.1` pushen. Die
   Download-Knöpfe zeigen danach von selbst auf das neue Release; laufende 1.0.0-Clients zeigen den
   Hinweis „Garage16 v1.0.1 is available“.

**Zurück auf V1.0.0:** erst die Migration zurücknehmen, dann im alten Ordner starten:
```
cd "$G16/Garage16_Lmu_V1_0_1/Garage16 - LMU"
sudo docker compose exec -T server sh -c 'GARAGE16_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" alembic downgrade 0018'
sudo docker compose --profile tunnel down
cd "$G16/Garage16_Lmu_V1_0_0/Garage16 - LMU"
sudo docker compose --profile tunnel up -d
```
(Der Downgrade löscht die Tabelle `device_tokens` und die Namensspalte. PCs, die mit Client 1.0.1
über E-Mail verbunden wurden, müssen sich danach neu verbinden; alles andere bleibt.)

### 8.4 Ältere Updates

Die Anleitungen für V0.9 bis V1.0 (mit den damaligen Rückwegen) stehen unverändert in
`docs/archive/UPDATES_V0_9_BIS_V1_0.md`.

## 9. Troubleshooting

| Symptom | Wahrscheinliche Ursache | Was tun |
|---|---|---|
| `/health` antwortet nicht | `server`-Container läuft nicht | `docker compose ps`, dann `docker compose logs server` |
| `/health` liefert `db_ok: false` | PostgreSQL nicht erreichbar | `docker compose logs db`; `docker compose restart db server` |
| Website und Clients erreichen den Server nicht mehr, Stack ist aber „healthy“ | Die Quick-Tunnel-Adresse hat sich geändert (Container neu erstellt) | `docs/RUNBOOKS.md`, „Quick-Tunnel-Adresse hat sich geändert“ |
| Registrierung schlägt fehl (403) | Ohne `GARAGE16_OPEN_REGISTRATION=1` verlangt das Formular das Registrierungs-Kennwort | Für alle öffnen: `GARAGE16_OPEN_REGISTRATION=1`; sonst `GARAGE16_REGISTRATION_SECRET` weitergeben |
| Registrierung: „That driver name is already taken“ | V1.0.1: Namen sind eindeutig (ohne Groß-/Kleinschreibung) | Anderen Namen wählen |
| Server startet nicht: „Neither GARAGE16_REGISTRATION_SECRET nor GARAGE16_OPEN_REGISTRATION=1 …“ | Keins von beiden in der `.env` | Eins von beiden setzen |
| Client: „Please confirm your email address first“ | V1.0.1: Der Client verbindet sich erst mit bestätigter E-Mail | Fahrer öffnet den Link aus der Mail (Spam-Ordner) oder sendet ihn unter Konto neu. Kommt keine Mail: SMTP prüfen (Kapitel 7). Für einen geschlossenen Kreis ohne SMTP: `GARAGE16_REQUIRE_EMAIL_VERIFICATION=0` |
| Client: „The terms of use and the privacy policy have changed“ | Die Bedingungen wurden noch nicht (neu) akzeptiert | Haken im Verbinden-Fenster setzen (Client 1.0.1) oder auf der Website anmelden und zustimmen |
| Client 1.0.0 meldet beim Verbinden „error (409)“ | Einer der beiden Fälle darüber; Client 1.0.0 kennt die Meldung noch nicht | Auf der Website anmelden: dort steht, was fehlt (E-Mail bestätigen / zustimmen). Oder Client 1.0.1 installieren |
| Runde eines neuen Fahrers zählt nicht, steht auf „wartet“ | V1.0.1 Fair Play: neue Fahrer, schneller als das Garage16 Best anderer | Kapitel 12 |
| Rechtliches zeigt „Betreiberangaben … noch nicht eingetragen“ | `GARAGE16_OPERATOR_*` fehlen in der `.env` | Eintragen, `docker compose --profile tunnel up -d server` |
| Discord-Bot postet nichts | Bot offline oder falscher Token | `docker compose logs discord_bot`; Token in `.env` prüfen |
| Keine Verifizierungs-/Reset-Mails | SMTP nicht konfiguriert oder falsch | Kapitel 7; `docker compose logs server \| grep -i smtp` |
| Nach Update: alte Fahrer können sich nicht einloggen | Unwahrscheinlich — Migrationen sind rückwärtskompatibel gebaut | `docker compose logs server` auf Migrationsfehler prüfen; im Zweifel `docs/ROLLBACK.md` |
| Freund meldet Banner „Garage16 rejected your login“ | Anmeldung dieses PCs gilt nicht mehr (unter Konto → Verbundene PCs getrennt, mehr als 5 PCs, oder Token erneuert) | Freund klickt „Reconnect account…“ und meldet sich mit E-Mail und Passwort an |
| Freund kann nicht installieren / Windows warnt | Unsignierte `.exe` → SmartScreen | „Weitere Informationen → Trotzdem ausführen“ (`docs/CLIENT_BUILD.md`, „Bekannte Eigenheiten“); Prüfsumme in `SHA256SUMS.txt` des Releases |
| Analyse meldet „The telemetry of this lap is no longer stored“ | Die Datei der Runde fehlt auf der Platte | Prüfen, ob das `telemetry_data`-Volume dasselbe ist wie vorher; `scripts.ops.telemetry_cleanup` (Dry-Run) listet Runden ohne Datei — sie werden nie automatisch gelöscht |
| Analyse: Federweg/Reifen/TC/ABS fehlen für eine Runde | Runde wurde mit einem Client < 0.9.0 aufgezeichnet | Kein Fehler — die Runde neu fahren |
| Speicherplatz läuft voll | Verwaiste Telemetrie-Dateien, alte Backups | Aufräum-Werkzeug im Server-Container (Dry-Run zuerst, Befehl in `README.md`, „Operations in one minute“); `docs/RUNBOOKS.md`, „Speicher voll“ |
| Ein Fahrer cheatet / meldet eine verdächtige Runde | — | Admin-Oberfläche (`/admin`) → Reports, Track-Limit-Prüfung, oder Fahrer sperren |

Für alles, was hier nicht auftaucht: `docker compose logs -f` (alle Dienste gleichzeitig) zeigt fast
immer die eigentliche Fehlermeldung.


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
GARAGE16_CORS_ORIGINS=https://garage16.de
GARAGE16_FRONTEND_URL=https://garage16.de
```
`CADDY_SITE_ADDRESS` bleibt unverändert (Standardwert `:80`).

**5. Webspace auf eine Unteradresse legen** (nur, wenn du ihn weiter nutzt)
1. Im All-Inkl-KAS eine Subdomain `download.garage16.de` anlegen und den
   Webspace-Inhalt dorthin legen.
2. In Cloudflare → DNS einen **A-Eintrag** `download` auf die in Schritt 1
   notierte IP, **„DNS only"** (graue Wolke — sonst kann All-Inkl kein
   Zertifikat ausstellen).
3. Optional in der `.env`, damit zu alte Clients den richtigen Download
   anbieten: `GARAGE16_CLIENT_DOWNLOAD_URL=https://download.garage16.de/Garage16-Client-Setup.exe`
4. Ab V0.9.2 gibt es zwei Downloads. Leg beide Setup-Dateien dorthin und trag auch
   den zweiten ein:
   `GARAGE16_CLIENT_APP_DOWNLOAD_URL=https://download.garage16.de/Garage16-Client-Analysis-Setup.exe`
   Beide erscheinen dann auf **garage16.de → Download** nebeneinander. Fehlt einer,
   steht dort „Noch nicht verfügbar“ statt eines kaputten Knopfs.

### Teil B: E-Mail über Gmail

Dieselbe Datei — ergänze zusätzlich:
```
GARAGE16_EMAIL_PROVIDER=smtp
GARAGE16_SMTP_HOST=smtp.gmail.com
GARAGE16_SMTP_PORT=587
GARAGE16_SMTP_USER=garage16.lmu@gmail.com
GARAGE16_SMTP_PASSWORD=<App-Passwort ohne Leerzeichen>
GARAGE16_SMTP_FROM=garage16.lmu@gmail.com
```
Das App-Passwort erstellst du so: im Google-Konto die Bestätigung in zwei
Schritten aktivieren, dann unter `myaccount.google.com/apppasswords` ein
App-Passwort anlegen.

### Teil C: Umstellen, testen, Clients mitnehmen

Die Reihenfolge ist Absicht: der **alte Quick Tunnel bleibt zuerst laufen**,
damit bestehende Clients den Umzug mitbekommen können.

1. **Neu starten** (im Projektordner auf der NAS):
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
   GARAGE16_MIGRATED_TO=https://garage16.de
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
   `GARAGE16_MIGRATED_TO` wieder aus der `.env` entfernen, dann
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

## 11. Client-Release auf GitHub

Die Download-Knöpfe der Website laden
`https://github.com/ry2en-X/garage16-lmu/releases/latest/download/…` — also immer die Installer des
**neuesten** Releases. Ein neuer Client ist deshalb nur ein Tag:

1. **Server-Adresse für den Client:** GitHub → Repository → Settings → Secrets and variables →
   Actions → Variables → `GARAGE16_SERVER_URL` = die Adresse, unter der der Server wirklich
   erreichbar ist. Solange `garage16.de` noch auf den Webspace zeigt: die aktuelle
   trycloudflare-Adresse. **Ändert sich die Quick-Tunnel-Adresse, hier mitändern** und einen neuen
   Client bauen (`docs/RUNBOOKS.md`). Ohne die Variable wird `https://garage16.de` eingebacken.
2. **Code hochladen** (Git-Arbeitskopie auf dem PC, `C:\Users\Moritz\Documents\Programmieren\garage16-lmu`).
   Erst **alles außer `.git` löschen**, dann den Inhalt von `Garage16 - LMU` aus der ZIP
   hineinkopieren — sonst bleiben Dateien liegen, die die neue Version verschoben oder gelöscht hat
   (V1.0.1 hat z. B. Doku nach `docs/archive/` verschoben). PowerShell, im Ordner der Arbeitskopie:
   ```
   Get-ChildItem -Force | Where-Object { $_.Name -ne ".git" } | Remove-Item -Recurse -Force
   # … jetzt den Inhalt von "Garage16 - LMU" aus der ZIP hierher kopieren …
   git status
   git add -A
   git commit -m "Garage16 V1.0.1"
   git push origin main
   ```
   Actions → **ci** muss grün werden (ca. 15–40 Minuten).
3. **Release bauen lassen:**
   ```
   git tag client-v1.0.1
   git push origin client-v1.0.1
   ```
   Actions → **build-client**: erst die komplette Testsuite, dann beide Windows-Pakete, dann das
   Release „Garage16 client 1.0.1“ mit zwei Installern, zwei ZIPs und `SHA256SUMS.txt`.
4. **Prüfen:** Releases → „Garage16 client 1.0.1“ ist **Latest**. In einem privaten Browserfenster
   `https://github.com/ry2en-X/garage16-lmu/releases/latest/download/Garage16-Client-Setup.exe`
   öffnen — der Download startet sofort. Dann auf der Website → Download beide Knöpfe testen.
5. **Schlägt Schritt 3 fehl:** Actions → den roten Lauf öffnen, der erste rote Schritt sagt warum.
   Nach einer Korrektur den Tag neu setzen:
   `git tag -d client-v1.0.1; git push origin :refs/tags/client-v1.0.1`, dann Schritt 3 wiederholen.

Nie eine `.env` hochladen: `git log --all --oneline -- .env` muss leer sein (`.gitignore` schließt
sie aus).

## 12. Review-Warteschlange und Fair Play

Admin (`/admin`, mit `GARAGE16_ADMIN_TOKEN`) → **Track-Limit-Prüfung** listet jede Runde, die
erst nach deiner Entscheidung zählt:

- **Track Limits / Datenqualität** (seit V0.9.9): die Prüfung konnte die Runde nicht sicher
  entscheiden — die Befunde stehen darunter, mit Ort auf der Runde.
- **Neuer Fahrer schneller als das Garage16 Best** (seit V1.0.1, `server/laps/fair_play.py`): Ein Fahrer
  mit weniger als `GARAGE16_NEW_DRIVER_REVIEW_LAPS` (Standard 10) offiziellen Runden fährt auf
  einer Strecke mit einem Auto schneller als die bisher schnellste Runde **anderer** Fahrer. Der
  Hinweis nennt die Zahl seiner bisherigen Runden und das bisherige Garage16 Best.

Was du tust: **Analysieren** öffnet die Runde im LapTime Analyzer — gegen das bisherige Garage16 Best
vergleichen (Linie, Bremspunkte, Tempo plausibel? Datenqualität-Kachel grün?). Dann **„Runde zählt“**
(erst jetzt gibt es PB/WR/Team-Bestzeit und die Discord-Meldung) oder **„Runde ungültig machen“** mit
Grund. Die Runde ist in der Zwischenzeit sichtbar und analysierbar, nur nicht in den Bestenlisten.
Die erste Runde überhaupt auf einer Strecke/einem Auto wird nie zurückgehalten, langsamere Runden
neuer Fahrer auch nicht. `GARAGE16_NEW_DRIVER_REVIEW_LAPS=0` schaltet die Regel ab.
