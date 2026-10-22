# Runbooks — was tun, wenn … (V0.9.9)

Für jedes Szenario: woran man es erkennt, wie man die Ursache findet, was sicher hilft. Befehle
laufen im Projektordner auf dem Server (NAS: `/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_9/Garage16 - LMU`).

Befehle, die im Server-Container mit der echten Datenbank laufen müssen, brauchen die Datenbank-URL
(`docker compose exec` erbt sie nicht). Im Folgenden steht dafür `RUN_IN_SERVER`:

```
RUN_IN_SERVER() { sudo docker compose exec -T server sh -c "LMU_GARAGE_DB_URL=\"postgresql+psycopg://\${POSTGRES_USER}:\${POSTGRES_PASSWORD}@\${POSTGRES_HOST}:\${POSTGRES_PORT}/\${POSTGRES_DB}\" $*"; }
```

## Erst prüfen: läuft alles?

```
sudo docker compose --profile tunnel ps                     # alle "healthy"?
curl -s https://garage16.de/health/ready                    # 200 + "ready"; sonst steht in "checks", was fehlt
```
`/health/live` = der Prozess antwortet · `/health/ready` = Datenbank und Telemetrie-Speicher
nutzbar (503 sonst) · `warnings` darin: `MAINTENANCE` (Backup läuft), `DISK_LOW`.

## 1. Uploads kommen nicht an

**Erkennen:** Fahrer melden fehlende Runden; Admin → Betrieb zeigt steigende „Abgelehnt“ / „Serverfehler“.

**Ursache finden:**
1. Admin → Betrieb: „Warum Uploads fehlschlugen“ und „Letzte Fehlschläge“ (mit Client-Version).
2. Steht eine Request-ID im Client-Log des Fahrers oder in der Liste: oben ins Suchfeld → alle
   Versuche dieser Anfrage. Im Server-Log: `sudo docker compose logs server | grep <request-id>`.
3. Typische Gründe:
   * `426 UPDATE_REQUIRED` — Client zu alt (`LMU_GARAGE_MIN_CLIENT_VERSION`). Fahrer aktualisiert; die
     Runden bleiben im Client und gehen danach hoch.
   * `503 MAINTENANCE` — ein Backup läuft; der Client wiederholt von selbst. Dauerhaft? → Punkt 7.
   * `401 Signature verification failed` — Client-Geheimnis passt nicht (nach Schlüsselwechsel ohne
     Punkt 8, oder Konto zurückgesetzt) → Fahrer: Client einmal mit `--reconfigure` starten und neu anmelden (holt ein neues Geheimnis).
   * `413` — Datei zu groß (`LMU_GARAGE_MAX_UPLOAD_BYTES`).
   * `500` — Serverfehler: die Request-ID im Log zeigt den Stacktrace.

**Sicher:** nichts löschen; der Client hält jede nicht angenommene Runde.

## 2. Eine Runde lässt sich nicht analysieren / zeigt Unsinn

1. LapTime Analyzer → Kachel „Datenqualität“ (oder `GET /telemetry/analysis/laps/<id>/health`):
   Lücken, Frames pro Sekunde, Zeit springt zurück (Neustart in der Datei), Tempo vs. Distanz.
2. Datei selbst prüfen: `RUN_IN_SERVER python -m scripts.consistency_check --deep` (unlesbar,
   Prüfsumme verändert).
3. Ist die Aufzeichnung schuld („unzuverlässig“), liegt es nicht an der Analyse — die Runde neu
   fahren. Sonst: Request-ID / Lap-ID und Screenshot an den Entwickler.

## 3. Datenbank nicht erreichbar

**Erkennen:** `/health/ready` → `"database": "unreachable"` (503), Server „unhealthy“.

```
sudo docker compose logs --tail 100 db
sudo docker compose exec db pg_isready -U garage16
df -h /volume1                      # voll? → Punkt 4
```
**Sicher:** `sudo docker compose restart db`, danach `restart server`. Niemals `down -v` (löscht die
Volumes). Startet die Datenbank nicht mehr: letztes Backup → `docs/ROLLBACK.md`.

## 4. Festplatte voll / fast voll

**Erkennen:** `/health/ready` → Warnung `DISK_LOW` (unter `LMU_GARAGE_DISK_WARN_FREE_MB`, Standard
2048 MB); Uploads scheitern mit 500.

```
df -h
sudo docker system df
sudo docker compose exec server du -sh /data/telemetry
```
**Sicher, in dieser Reihenfolge:** alte Backups auf eine andere Platte verschieben (Aufbewahrung:
`BACKUP_RETENTION_DAYS`) · Rückfallpunkte alter Restores freigeben (`./scripts/restore.sh --cleanup <stamp>`)
· verwaiste Telemetrie: `RUN_IN_SERVER python -m scripts.consistency_check --delete-orphans` (zeigt
zuerst die Liste, fragt dann; nur Dateien älter als 1 h ohne Runde) · `sudo docker image prune`.

## 5. Migration beim Update schlägt fehl

**Erkennen:** Server startet nicht, Log: `alembic` Fehler.

```
sudo docker compose logs server | grep -A20 -i alembic
RUN_IN_SERVER alembic current
```
**Sicher:** neuen Stack stoppen, alten Ordner wieder starten (jede Version hat in der ADMIN_GUIDE
ihren Rückweg; hat die neue Version eine Migration, steht dort der `alembic downgrade`-Befehl, der
VORHER laufen muss). War die Migration halb durch: Backup von vor dem Update → `docs/ROLLBACK.md`.

## 6. Wiederherstellen aus einem Backup

`docs/ROLLBACK.md`, Abschnitt 3 — `./scripts/restore.sh <backup-ordner>`; danach
`./scripts/restore.sh --cleanup <stamp>`, wenn alles passt. Bei Exit-Code 3: „Restore manuell
zurückdrehen“ dort.

**Audit-Anker (V0.9.9.1).** Schritt 4 vergleicht die neueste Audit-Zeile der fertig migrierten und
geprüften Staging-Datenbank mit `audit_head` im Manifest — vor jedem Umschalten. Regeln:

| Manifest | Prüfung | Weicht ab → |
|---|---|---|
| `"audit_head": {"id": N, "mac": "…"}` (Backups ab V0.9.9) | neueste Zeile muss genau N mit genau diesem MAC sein | Abbruch, Live-System unberührt, Staging gelöscht |
| `"audit_head": null` | die Staging-Datenbank darf keine Audit-Zeile haben | Abbruch |
| kein `audit_head` (Backups von V0.9.8) | nicht verglichen (die Kette selbst prüft `--deep` weiter) — Hinweis im Log | — |
| `audit_head` nicht lesbar | nie als „kein Anker“ gewertet | Abbruch schon in Schritt 1 |

Meldet der Restore „audit head: DIFFERS from the backup“: das Backup **nicht** einspielen. Fehlende
letzte Zeilen oder eine veränderte letzte Zeile bedeuten ein verändertes Backup (die Prüfsumme im
selben Manifest wurde mit angepasst). Ein anderes Backup nehmen und vorgehen wie in Punkt 17.
Den Anker zusätzlich außerhalb des NAS notieren (`integrity_check --audit` zeigt ihn) — nur eine
Kopie, die ein Angreifer nicht mit ändern kann, schützt gegen ein komplett neu geschriebenes Backup.

## 7. Uploads bleiben auf „Wartung“ (503) hängen

Ein Backup wurde hart beendet und hat seine Wartungsmarkierung nicht entfernt.

```
sudo docker run --rm -v garage16_telemetry_data:/data alpine ls -la /data/.garage16-maintenance
sudo docker ps -a | grep garage16_backup_lock      # ein übrig gebliebener Lock-Helfer?
```
**Sicher:** `sudo docker rm -f <helfer>`; dann `sudo docker run --rm -v garage16_telemetry_data:/data alpine rm -f /data/.garage16-maintenance`.
Von selbst: der Helfer gibt die Sperre nach `GARAGE16_BACKUP_LOCK_MAX_S` (Standard 1800 s) auf, der
Server ignoriert eine Markierung, die älter als 2 Stunden ist.

## 8. Verschlüsselungsschlüssel wechseln (`LMU_GARAGE_SECRET_KEY`)

Ohne dass sich jemand neu anmelden muss:
1. Neuen Schlüssel erzeugen: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
2. In `.env`: `LMU_GARAGE_SECRET_KEY=<neu>,<alt>` (neu zuerst) → `sudo docker compose up -d server`
3. `RUN_IN_SERVER python -m scripts.rotate_secret_key` (Vorschau), dann `... --apply`
4. In `.env`: `LMU_GARAGE_SECRET_KEY=<neu>` → `sudo docker compose up -d server`

Meldet Schritt 3 „unreadable“: dieser Fahrer startet seinen Client einmal mit `--reconfigure` und meldet sich neu an.

## 9. Ein Fahrer beschwert sich über „Rate limit exceeded“

Die Grenzen zählen pro echter Client-Adresse (`LMU_GARAGE_TRUSTED_PROXIES`, `server/client_ip.py`)
und pro Fahrer. Teilen sich viele Fahrer eine Adresse (LAN-Party), die passende Grenze in `.env`
anheben (`LMU_GARAGE_RATELIMIT_<BUCKET>_MAX`, Liste im README) und `up -d server`. Hinter dem Tunnel
zählt die Adresse, die Cloudflare gesehen hat — nicht die des Tunnels.

## 10. Daten eines Fahrers löschen (DSGVO)

Selbst: Konto → Daten & Datenschutz → Konto löschen. Admin: Admin → Fahrer → Löschen (gehört er einem
Team mit anderen Mitgliedern, mit „force“ die Besitzerrolle übertragen). Was dabei alles verschwindet
und was in Backups noch bis zu 14 Tage liegt: `docs/DATA_PROTECTION.md`.

## 11. Eine Runde wurde zurückgestellt („Quarantäne“, Hash-Abweichung)

**Erkennen:** Admin → Integrität listet die Runde mit Grund (Datei fehlt / Größe verändert / Inhalt
verändert / Signatur passt nicht) und der Request-ID der Anfrage, die es bemerkt hat. Der Fahrer
sieht „zurückgestellt“ in seiner Rundenliste. Die Runde ist aus Bestenlisten und Rekorden heraus.

**Ursache finden:**
1. `RUN_IN_SERVER python -m scripts.integrity_check --lap <id>` — prüft genau diese Runde.
2. Audit: Admin → Integrität → Audit-Protokoll (oder `GET /admin/audit?lap_id=<id>`): wann
   angenommen (sha256, Größe), wann zurückgestellt, von welcher Anfrage.
3. Server-Log zur Request-ID: `sudo docker compose logs server | grep <request-id>`.
4. Betrifft es viele Runden auf einmal (alle „Datei fehlt“)? Dann ist eher das Volume falsch
   eingebunden oder ein Restore unvollständig — Punkt 3/6, NICHT einzeln freigeben.

**Sicher:**
* „Signatur passt nicht“ (`mac_invalid`): jemand hat Hash, Größe oder **Rundenzeit in der Datenbank**
  geändert, oder es wurde ein fremdes/manipuliertes Backup eingespielt. Nicht freigeben — die
  Runde aus einem Backup von VOR der Änderung wiederherstellen (`docs/ROLLBACK.md`) oder invalidieren.
* „Inhalt/Größe verändert“, „Datei fehlt“: die Datei aus dem letzten guten Backup zurückspielen
  (Punkt 12), dann Admin → Integrität → „Freigeben, wenn intakt“. Die Freigabe klappt **nur**, wenn
  die Datei wieder exakt passt.

## 12. Eine einzelne Runden-Datei aus dem Backup zurückholen

```
B=/volumeUSB2/usbshare/garage16-backups/garage16_<zeitstempel>      # das letzte Backup VOR dem Schaden
RUN_IN_SERVER python -c "from server.database import SessionLocal; from server.models import Lap; print(SessionLocal().get(Lap, <id>).telemetry_path)"
#   -> /data/telemetry/driver_5/spa/porsche_963/<sha256>.parquet   (der Teil nach /data/telemetry/ ist REL)
REL="driver_5/spa/porsche_963/<sha256>.parquet"
sudo docker run --rm -v garage16_telemetry_data:/data -v "$B":/backup:ro alpine \
    sh -c "tar xzf /backup/telemetry.tar.gz -C /data ./$REL && chmod 440 /data/$REL && sha256sum /data/$REL"
```
Die ausgegebene Prüfsumme muss dem Dateinamen entsprechen. Dann Admin → Integrität → „Freigeben,
wenn intakt“ (oder `RUN_IN_SERVER python -m scripts.integrity_check --apply --release-verified`).
Ist die Datei in keinem Backup: die Runde bleibt zurückgestellt; der Fahrer kann sie nicht neu
hochladen (sein Client hat sie nach 14 Tagen gelöscht) — invalidieren oder so lassen.

## 13. Nach abgebrochenen Datei-Operationen (Stromausfall, Container hart beendet)

Ein Upload legt die Datei ab und schreibt erst danach in einer Transaktion die Runde; bricht der
Prozess dazwischen ab, bleibt höchstens eine Datei ohne Runde übrig — nie eine Runde ohne Datei
(getestet: `tests/test_v099_integrity.py`, Prozess wird mitten im Speichern beendet).
```
RUN_IN_SERVER python -m scripts.consistency_check           # "files no lap points at" / "temp files"
RUN_IN_SERVER python -m scripts.consistency_check --delete-orphans   # nur Dateien älter als 1 h
RUN_IN_SERVER python -m scripts.integrity_check              # jede Runde gegen ihre Datei
```
Der Client des Fahrers hält die Runde und sendet sie erneut; sie wird dann normal gespeichert.
Bleiben Uploads auf „Wartung“ hängen: Punkt 7.

## 14. Einen verdächtigen Upload prüfen

Admin → Track-Limit-Prüfung zeigt jede zurückgehaltene Runde („wartet — zählt nicht“) mit ihren
Befunden: was, wo in der Runde, aus welchem Signal.
* **Analysieren** öffnen und die Stelle ansehen (Tempo, Position, Linie, Datenqualität-Kachel).
* „Datei von einem anderen Fahrer hochgeladen“ (`telemetry_reused`): fast immer eine kopierte Runde.
  Im Audit (`kind lap.accepted`, gleiche sha256) steht, wer sie zuerst hatte.
* Datenqualität (Lücke, Frames/s, Sprung, wiederholte Samples): Aufzeichnungsproblem ODER
  Manipulation — bei einem Fahrer, dem das öfter passiert, mit ihm reden (Client-Log, PC-Last).
* Fehlende Signale bei Client 0.9.4: die Datei wurde verändert (der Client schreibt sie immer).
* „Runde zählt“ = offiziell, Rekorde werden jetzt vergeben. „Runde ungültig“ mit Begründung.
Jede Entscheidung steht im Audit-Protokoll.

## 15. Sicherer Rückweg nach einem fehlerhaften Update

1. Backup des aktuellen Zustands (`./scripts/backup.sh …`), auch wenn er fehlerhaft ist.
2. Hat das neue Release eine Migration (V0.9.9: 0017), **zuerst** im NEUEN Ordner
   `RUN_IN_SERVER alembic downgrade <vorige Revision>` (steht in der ADMIN_GUIDE beim Release).
3. Neuen Stack stoppen (`down`, nie `down -v`), alten Ordner starten, `/health/ready` prüfen.
4. Ist die Datenbank selbst beschädigt: Restore aus dem letzten Backup VOR dem Update
   (`docs/ROLLBACK.md`, Abschnitt 3) — der Restore prüft dabei jede Datei und jede Signatur und
   bricht bei einem Widerspruch ab, ohne etwas zu verändern.

## 16. Integritäts-Schlüssel wechseln oder verloren

**Wechseln** (z. B. jährlich, oder wenn er jemandem bekannt wurde):
1. Neuen erzeugen: `python3 -c "import secrets; print(secrets.token_hex(32))"`
2. `.env`: `LMU_GARAGE_INTEGRITY_KEY=<neu>,<alt>` → `sudo docker compose up -d server`
3. `RUN_IN_SERVER python -m scripts.integrity_check --apply --resign` (signiert jede intakte Runde neu)
4. Den alten Schlüssel **erst entfernen, wenn das Audit-Protokoll keine damit signierten Zeilen mehr
   hat** (Aufbewahrung 365 Tage) — sonst meldet die Kettenprüfung diese Zeilen als „unbekannter
   Schlüssel“ (nicht von einer Fälschung zu unterscheiden). Den alten Schlüssel offline aufheben.

**Verloren:** Runden bleiben gespeichert und zählen weiter (ihre Dateien passen ja noch zu ihren
Prüfsummen), aber die Signaturen können Hash und Rundenzeit nicht mehr bestätigen. Neuen Schlüssel
setzen, Dateien gegen ein unabhängiges Backup prüfen, dann
`RUN_IN_SERVER python -m scripts.integrity_check --apply --resign --accept-unknown-key`.
Wurde `LMU_GARAGE_INTEGRITY_KEY` nie gesetzt, hängt die Signatur an `LMU_GARAGE_SECRET_KEY` —
dessen Wechsel (Punkt 8) erst mit „neu,alt“ machen, sonst gilt dasselbe.

## 17. Die Audit-Kette ist gebrochen

`RUN_IN_SERVER python -m scripts.integrity_check --audit` nennt die erste Zeile, ab der die Kette
nicht mehr stimmt („link“: eine Zeile davor wurde gelöscht/eingefügt; „content“: diese Zeile wurde
verändert; „key“: mit unbekanntem Schlüssel signiert). Den Kettenkopf aus dem Manifest eines älteren
Backups vergleichen (`audit_head`): stimmt er mit der Zeile gleicher ID heute überein, ist alles bis
dahin unverändert. Es ist ein Sicherheitsvorfall: Zugriff auf Server/Datenbank prüfen, Passwörter
und Schlüssel wechseln, den Zustand sichern (Backup), nichts „reparieren“.
