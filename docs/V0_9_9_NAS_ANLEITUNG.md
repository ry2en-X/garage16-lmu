# V0.9.9 — was nur auf dem NAS (und in LMU) geht: genaue Anleitung

Alles, was ohne deinen NAS und ohne echte LMU-Runden prüfbar war, ist erledigt und im Protokoll
(`docs/V0_9_9_PROTOKOLL.md`). Diese Anleitung deckt den Rest ab. Jeder Teil sagt:
**was** du tust, **was** du sehen solltest, **was** du tust, wenn es anders aussieht, und **was** du
mir zurückschickst. Wenn ein Schritt scheitert: nicht improvisieren, sondern den Abschnitt
„Wenn es nicht klappt“ des Teils befolgen und die Ausgabe aufschreiben (Vorlage ganz unten).

Abkürzungen (einmal pro SSH-Sitzung einfügen):
```
ALT="/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_8/Garage16 - LMU"
NEU="/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_9/Garage16 - LMU"
BK=/volumeUSB2/usbshare/garage16-backups
RUN_IN_SERVER() { sudo docker compose exec -T server sh -c "LMU_GARAGE_DB_URL=\"postgresql+psycopg://\${POSTGRES_USER}:\${POSTGRES_PASSWORD}@\${POSTGRES_HOST}:\${POSTGRES_PORT}/\${POSTGRES_DB}\" $*"; }
```
`RUN_IN_SERVER` immer aus dem Ordner aufrufen, dessen Stack gerade läuft (`cd "$ALT"` oder `cd "$NEU"`).

---

## Teil A — Messungen VOR dem Update (V0.9.8, ca. 15 Minuten)

Damit der Vergleich V0.9.8 ↔ V0.9.9 auf derselben Hardware mit denselben Runden möglich ist.

1. ZIP entpacken wie in `docs/ADMIN_GUIDE.md` „Neu in V0.9.9“, Schritt 2 (der alte Stack läuft weiter).
   Prüfsumme des ZIPs auf dem Mac (Terminal, im Download-Ordner):
   ```
   shasum -a 256 Garage16_Lmu_V0_9_9.zip
   ```
   Muss exakt der Wert sein, den ich dir mit dem ZIP genannt habe. **Abweichung:** ZIP nicht
   verwenden, neu herunterladen; weicht es wieder ab, mir beide Werte schicken.
2. Lap-Check und Hash auf dem NAS mit V0.9.8 messen (liest nur):
   ```
   cd "$ALT"
   sudo docker compose cp "$NEU/scripts/bench_server.py" server:/tmp/bench_server.py
   RUN_IN_SERVER python /tmp/bench_server.py --root /app --laps 20 --repeat 3 > ~/bench_098.json
   tail -25 ~/bench_098.json
   ```
   Erwartet: JSON mit `"version": "0.9.8"`, `"laps": 20` (oder weniger, wenn du weniger Runden hast)
   und Werten unter `median`.
3. API-Antwortzeiten von außen (auf dem Mac, Terminal, im entpackten Ordner — Python 3 reicht).
   Zwei Runden-IDs derselben Strecke aus der Website (Analyse-URL `#/analysis?a=<ID>`) und dein
   Desktop-Token (auf dem Windows-PC in `C:\Users\<Name>\.lmu_garage\client_config.json`, Eintrag
   `auth_token`):
   ```
   python3 -m scripts.load_test https://garage16.de --token <TOKEN> --lap-a <ID1> --lap-b <ID2> --users 4 --seconds 30 > ~/load_098.txt
   tail -15 ~/load_098.txt
   ```
   Nur laufen lassen, wenn gerade niemand fährt.
4. Upload-Dauer der letzten 7 Tage: Admin → Betrieb → Zeitraum „letzte 7 Tage“ → Screenshot der
   Upload-Tabelle (Median / p95).

**Wenn es nicht klappt:** `docker compose cp` meldet „no such service“ → du bist nicht im Ordner des
laufenden Stacks (`cd "$ALT"`). `bench_server.py` meldet einen Python-Fehler → die komplette Ausgabe
kopieren, Teil A überspringen (Teil E misst nur V0.9.9) und mir die Ausgabe schicken.
`load_test` meldet `403`/`401` → Token falsch; `0` als Status → Seite nicht erreichbar.

**Zurückschicken:** `~/bench_098.json`, `~/load_098.txt`, Screenshot Betrieb.

---

## Teil B — Update durchführen

Genau wie `docs/ADMIN_GUIDE.md` „Neu in V0.9.9“, Schritte 1–5. Zusätzlich prüfen:

| Prüfung | Befehl | Erwartet |
|---|---|---|
| Migration angekommen | `cd "$NEU"; RUN_IN_SERVER alembic current` | `0017 (head)` |
| Version | Schritt 4 der ADMIN_GUIDE | `"version":"0.9.9"`, `"status":"ready"` |
| Audit-Tabelle samt Schutz | `sudo docker compose exec -T db psql -U garage16 -d garage16 -c "\dS+ audit_events" \| tail -5` | Zeile `audit_events_no_update BEFORE UPDATE` |
| Ein Upload funktioniert | eine Runde fahren (oder den Client eine wartende Runde senden lassen) | Admin → Integrität → Audit-Protokoll: `lap.accepted` |

**Wenn es nicht klappt:**
* Server startet nicht, Log zeigt `alembic`: `sudo docker compose logs server | grep -A20 -i alembic`
  → Ausgabe aufschreiben, dann **Rückweg** (ADMIN_GUIDE, „Zurück auf V0.9.8“; hat 0017 nicht
  geklappt, ist kein Downgrade nötig — `alembic current` zeigt dann `0016`).
* `/health/ready` gibt 503: `curl -s http://localhost:8000/health/ready` im Container zeigt unter
  `checks`, was fehlt (Datenbank / Speicher) → `docs/RUNBOOKS.md` Punkt 3/4.
* Uploads scheitern mit 500 `STORAGE_UNAVAILABLE`: ein Ordner im Telemetrie-Volume ist ein
  symbolischer Link (sollte nie vorkommen):
  `sudo docker run --rm -v garage16_telemetry_data:/data alpine find /data -type l` → Ausgabe schicken.

---

## Teil C — Gespeicherte Runden prüfen und die neuen Regeln testen (ändert nichts)

1. **Integrität aller Dateien** (Dauer ≈ Gesamtgröße ÷ 100–300 MB/s):
   ```
   cd "$NEU"
   RUN_IN_SERVER python -m scripts.integrity_check --json /tmp/integrity_before.json
   ```
   Erwartet: `ok (unsigned)` für alle Runden (sie sind vor V0.9.9 gespeichert) und
   `Audit chain: OK`. Exit-Code 1 + Zeilen `FAILED lap …` = gefunden: notieren (Runde, Grund).
2. **Signieren** (ändert nur die drei neuen Spalten und stellt kaputte Runden zurück):
   ```
   RUN_IN_SERVER python -m scripts.integrity_check --apply --backfill
   RUN_IN_SERVER python -m scripts.integrity_check
   ```
   Der zweite Lauf muss `ok (ok)` für alle intakten Runden zeigen.
   **Wenn Runden „missing“ sind:** zuerst `RUN_IN_SERVER python -m scripts.consistency_check` —
   fehlen viele, ist das Volume nicht richtig eingebunden (STOPP, mir schreiben); einzelne: Runbook 12.
3. **Trockenlauf der V0.9.9-Regeln über alle alten Runden:**
   ```
   RUN_IN_SERVER python -m scripts.revalidate_laps --hold --details 200 > ~/v099_trockenlauf.txt
   grep -A40 "^Summary" ~/v099_trockenlauf.txt
   ```
   Die Zusammenfassung zeigt je Übergang die Anzahl und die Gründe. Wichtig sind:
   * `valid -> review` — so viele alte Runden würden die neuen Regeln prüfen lassen, darunter
     `of which hold a current record` (Rekord-Halter).
   * die Gründe darunter (z. B. `coverage_gap`, `signals_missing`, `speed_distance_mismatch`).
   **Nichts anwenden.** Diese Zahlen entscheiden, ob eine Schwelle für echte Daten zu streng ist.
   Faustregel: mehr als 5 % der sauberen Runden mit demselben Grund → wahrscheinlich ist die
   Schwelle für LMU-Daten falsch, nicht die Runden.

**Zurückschicken:** die Summary aus `~/v099_trockenlauf.txt` und von 3–5 Runden je häufigem Grund
die Zeilen `[valid -> review] lap …` (mit Runden-ID), dazu je eine Analyse-Ansicht als Screenshot.

---

## Teil D — Gekennzeichnete echte Runden fahren (der Referenzdatensatz) — ca. 1–2 Stunden in LMU

Ziel: Precision/Recall auf ECHTEN Runden statt nur auf dem synthetischen Satz. Du fährst Runden,
von denen du sicher weißt, ob sie sauber sind, und notierst das **sofort** nach jeder Runde.

**Vorbereitung:** eine Strecke mit klaren weißen Linien und Randsteinen, die du gut kennst
(Vorschlag: Monza oder Spa), ein Auto, Practice/Hotlap, Client 0.9.4 läuft und lädt hoch. Auf
Papier oder Handy eine Tabelle: `Runde Nr. | Rundenzeit | sauber/Verstoß | Kurve | LMU hat „ungültig“ angezeigt? | Bemerkung`.

**Fahrprogramm** (Mindestanzahl in Klammern, mehr ist besser):
1. **Sauber, mit Abstand zur Linie** (10): alle vier Räder immer deutlich innerhalb der weißen Linie.
2. **Sauber, Randsteine voll genutzt** (10): innere Räder auf dem Kerb, aber nie alle vier über der Linie.
3. **Klarer Verstoß, LMU bestraft** (10): an EINER festen Kurve mit allen vier Rädern deutlich (ein
   Auto breit) über die Linie und dabei abkürzen/Zeit gewinnen, sodass LMU die Runde ungültig zeigt.
4. **Verstoß ohne Zeitgewinn** (10, wenn möglich): mit allen vier Rädern über die Linie, aber
   langsam/weit raus, sodass LMU die Runde NICHT ungültig macht (Zeit verloren). Notieren, ob LMU
   trotzdem „ungültig“ zeigt.
5. **Grenzfälle** (5–10): alle vier Räder knapp (ca. eine Handbreit) über der Linie, nur auf dem Kerb.

Danach auf der Website unter Dashboard → Meine Runden die Runden der Session suchen (Zeit und Uhrzeit
vergleichen) und die **Runden-ID** aus dem Link „Analysieren“ (`#/analysis?a=<ID>`) in die Tabelle
übertragen. Dann die Datei `labels.csv` anlegen (Vorlage: `docs/trackcheck_labels_vorlage.csv`,
Beispielzeilen löschen):
```
lap_id,label,note
1234,clean,"P1 sauber Abstand"
1235,clean,"P2 Kerbs"
1240,violation,"P3 Kurve 4, LMU ungültig"
1241,violation,"P4 Kurve 4 weit raus, LMU gültig"
1250,violation,"P5 knapp über Linie, LMU gültig"
```
`label` ist nur `clean` (kein Rad-Satz komplett über der Linie) oder `violation` (alle vier über der
Linie). Bist du dir bei einer Runde nicht sicher: **weglassen** (lieber weniger, aber sichere Labels).

**Auswerten** (ändert nichts an den Runden):
```
cd "$NEU"
sudo docker compose cp ~/labels.csv server:/tmp/labels.csv
RUN_IN_SERVER python -m scripts.trackcheck_eval --labels /tmp/labels.csv --json /tmp/real_eval.json --markdown /tmp/real_eval.md
sudo docker compose cp server:/tmp/real_eval.md ~/real_eval.md
sudo docker compose cp server:/tmp/real_eval.json ~/real_eval.json
cat ~/real_eval.md
```
Erwartet: zwei Tabellen (aktuelle Regeln / beim Upload gespeichertes Urteil) mit n, TP, FP, TN, FN
und den vier Raten, darunter die falsch eingestuften Runden mit Namen.

**Wenn es nicht klappt:**
* `Skipped: … no such lap` → Runden-ID falsch abgeschrieben.
* `file failed its integrity check` → Teil C, Schritt 2 nicht gelaufen.
* LMU zeigt bei Programm 4 immer „ungültig“: so notieren („LMU bestraft jedes Verlassen“) — das ist
  ein wichtiges Ergebnis, die Runden trotzdem als `violation` labeln.
* Der Client lädt eine Runde nicht hoch: die Datei `garage16-client.log` neben dem Datenordner des
  Clients aufheben (Client → „Log öffnen“, oder im Ordner `C:\Users\<Name>\.lmu_garage`).

**Zurückschicken:** `~/real_eval.md`, `~/real_eval.json`, `labels.csv` und deine Tabelle.
Ich passe danach — falls nötig — die Schwellen an und dokumentiere die echten Raten in
`docs/QUALITY.md`. Ohne diesen Teil bleiben die Raten „nur synthetisch gemessen“.

---

## Teil E — Messungen NACH dem Update (V0.9.9)

Wie Teil A, Schritte 2–4, nur im neuen Ordner und mit neuen Dateinamen:
```
cd "$NEU"
RUN_IN_SERVER python -m scripts.bench_server --root /app --laps 20 --repeat 3 > ~/bench_099.json
python3 -m scripts.load_test https://garage16.de --token <TOKEN> --lap-a <ID1> --lap-b <ID2> --users 4 --seconds 30 > ~/load_099.txt
```
(`load_test` auf dem Mac, mit denselben IDs wie in Teil A.) Nach einer Woche Betrieb: Screenshot
Admin → Betrieb „letzte 7 Tage“.

Zusätzlich die Dauer des ersten Backups mit V0.9.9 (Zeitstempel der ersten und letzten Logzeile von
`backup.sh`) und die Zeile `Laps checked: … (… s)` aus Teil C, Schritt 2 notieren.

**Zurückschicken:** `~/bench_099.json`, `~/load_099.txt`, Backup-Dauer, Integritäts-Dauer, Screenshot.

Bewertung (mache ich, oder du selbst): `check_ms` und `sha256_mb_s` V0.9.8 vs. V0.9.9 sowie p95 aus
`load_test`. Zusatzkosten von V0.9.9 pro Upload sind einmal Hash (schon vorher gemacht) + die
Entscheidungsschicht; beim ersten Öffnen einer Analyse einmal das Hashen der Datei.

---

## Teil F — Backup und Restore mit V0.9.9 einmal üben (optional, aber empfohlen)

1. Backup: `sudo ./scripts/backup.sh "$BK"`; im neuen `manifest.json` muss `"audit_head": {"id": …}` stehen.
2. Restore-Übung **auf einer Kopie** ist auf dem NAS aufwendig; der Restore wurde im Drill-Test
   automatisch geprüft (`tests/test_v098_backup_restore.py`, läuft bei jedem Release). Wenn du es
   trotzdem real üben willst: nur nach `docs/ROLLBACK.md` Abschnitt 3 und vorher ein frisches Backup.

---

## Optionale Härtung (deine Entscheidung)

* **Alte Clients sperren:** in `.env` `LMU_GARAGE_MIN_CLIENT_VERSION=0.9.4` und
  `sudo docker compose up -d server`. Dann muss jede Runde alle Tracklimit-Signale enthalten; ältere
  Clients bekommen „bitte aktualisieren“ (ihre Runden bleiben im Client).
* **Integritätsprüfung regelmäßig:** Synology Aufgabenplaner → Benutzerdefiniertes Skript, wöchentlich:
  ```
  cd "/volume1/homes/Moritz/garage16/Garage16_Lmu_V0_9_9/Garage16 - LMU" && sudo docker compose exec -T server sh -c 'LMU_GARAGE_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.integrity_check --apply'
  ```
  Mit „Ausführungsdetails per E-Mail senden“ bekommst du eine Mail, wenn etwas zurückgestellt wurde.

---

## Vorlage: Rückmeldung an mich

```
Teil: A / B / C / D / E / F
Schritt:
Befehl (kopiert):
Was ich erwartet habe:
Was passiert ist (Ausgabe kopiert, die letzten ~40 Zeilen):
Was ich danach gemacht habe:
Läuft der Server gerade? (/health/ready):
```
