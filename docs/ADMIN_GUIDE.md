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
