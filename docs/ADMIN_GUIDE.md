# Admin-Leitfaden (V0.8-NAS)

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
6. Prüfen: `curl https://deine-domain/health` sollte
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

**Restore ist ein Release-Gate — nicht nur Backups erstellen, sondern
mindestens einmal echt wiederherstellen und prüfen:**
1. Laufenden Stack stoppen: `docker compose down`
2. Restore-Schritte aus `docs/ROLLBACK.md` befolgen
3. Stack wieder starten: `docker compose up -d`
4. Prüfen: Login funktioniert, Leaderboard zeigt die erwarteten Daten,
   Teams sind vorhanden, eine Testrunde lässt sich hochladen

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
4. Prüfen: `curl https://deine-domain/health` — `version` sollte die
   neue Versionsnummer zeigen, `db_ok: true`.
5. **Neue Client-Version verteilen:** Paket neu bauen und die neue
   `Garage16-Client-Setup.exe` weitergeben (`docs/CLIENT_BUILD.md`).
   Freunde führen sie einfach erneut aus — sie aktualisiert an Ort und
   Stelle, Anmeldung und Runden bleiben. Zum Erzwingen
   `LMU_GARAGE_MIN_CLIENT_VERSION` in `.env` erhöhen und den Download-Link
   in `LMU_GARAGE_CLIENT_DOWNLOAD_URL` eintragen: zu alte Clients zeigen
   dann ein Banner mit Download-Knopf, gefahrene Runden bleiben erhalten.

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
| Speicherplatz läuft voll | Verwaiste Telemetrie-Dateien | Aufräum-Werkzeug im Server-Container (Dry-Run zuerst!) — genauer Befehl im README, Abschnitt "Telemetry storage cleanup" |
| Ein Fahrer cheatet / meldet eine verdächtige Runde | — | Admin-Oberfläche (`#/admin`) → Reports-Tab, oder Fahrer sperren im Drivers-Tab |

Für alles, was hier nicht auftaucht: `docker compose logs -f` (alle
Dienste gleichzeitig) zeigt fast immer die eigentliche Fehlermeldung.
