# V0.9.9 — Arbeitsprotokoll

Jeder Schritt mit Befehl und tatsächlichem Ergebnis. Umgebung für alle Läufe (falls nicht anders
angegeben): Cloud-Container, Linux 6.18 x86_64, 2 vCPU Intel Xeon 2,8 GHz, 7 GiB RAM, Python 3.12.3,
Node 22.22.0, PostgreSQL 16.15 (lokal, Port 5433), Caddy für die Proxy-Tests. **Nicht** die
NAS-Hardware — was dort zu messen ist, steht in `docs/V0_9_9_NAS_ANLEITUNG.md`.

## 1. Ausgangsstand V0.9.8 (vor jeder Änderung)

```
unzip Garage16_Lmu_V0_9_8.zip            # sha256 85b58384926b1d0742cf782f4326e28e45d21f6e5b53fa44283e810a4ba02f83
cd "Garage16 - LMU"
python -m scripts.release_check --report-dir <tmp>/base098_report
```

Ergebnis (2026-10-20, 452 s): **PASSED** — 1218 bestanden, 0 fehlgeschlagen, 0 Fehler,
3 übersprungen (nur die Opt-in-PyInstaller-Builds, erlaubt). ruff, bandit, pip-audit bestanden,
Coverage-Untergrenzen eingehalten. Code-Hash des getesteten Baums
`db1cf7050cc97cb2b4f7f1b386fe8b41d09e3290b9e563dab7499aeff9a7a46d`.

**Bestehende Fehler vor V0.9.9: keine im Basislauf.** Ein in V0.9.8 bereits vorhandener, aber nur
zufällig auftretender Testfehler wurde während der Arbeit gefunden — Abschnitt 3.

Der Arbeitsbaum war vor Beginn identisch mit dem Archiv (`diff -rq`, ohne Caches).

## 2. Fehler, die durch V0.9.9 entstanden, und ihre Behebung

Alle während der Umsetzung, keiner im ausgelieferten Stand.

| # | Fehler | Ursache | Behebung |
|---|---|---|---|
| 1 | 82 Testfehler nach der Regeländerung „fehlende Daten sind nicht sauber“ | Test-Helfer erzeugten Mini-Runden mit 50 Zeilen (= 0,6 Bilder/s → jetzt `low_frame_rate`), ohne die Signale eines 0.9.4-Clients, und `synthetic_lap` sprang sofort von 75 auf 38 m/s (≈ 19 G → `implausible_acceleration`) | Helfer erzeugen 20 Bilder/s und alle Signale (`tests/helpers/lapfile.py`), `synthetic_lap` mit 150-m-Geschwindigkeitsrampe; Erwartungen `pending` → `held`, Sektorzeiten jetzt INFO — jede Anpassung ist eine gewollte Verhaltensänderung laut Auftrag (REVIEW hält zurück) |
| 2 | Gefälschte Audit-Zeile mit unbekannter Schlüssel-ID galt als „Kette ok“ | Zeilen mit unbekanntem Schlüssel wurden übersprungen statt gezählt | unbekannter Schlüssel ⇒ Kette nicht ok; `consistency_check` meldet `audit_unverifiable` getrennt |
| 3 | PostgreSQL: zwei Integritätstests rot | PG vergibt gelöschte IDs nicht neu; `DROP TRIGGER` hat dort eine andere Syntax | Test nutzt die ID der gefälschten Zeile; Trigger-DDL je Dialekt |
| 4 | Gate: „Baum hat sich während des Laufs geändert“ | `.ruff_cache` entstand im gestagten Baum | `ruff --no-cache`, Caches in `EXCLUDE_DIRS` |
| 5 | Generator der Referenzrunden erzeugte Artefakte (Kategorie-Interpolation, Knicke, Krümmungssprünge) | Interpolation kategorialer Werte | nächster Abschnitt statt Interpolation, Kosinus-Buckel, 30-m-Glättung |
| 6 | Web: `el()` mit Zahl als Kind | jsdom-Fehler bei Lap-ID als Zahl | `String()` |
| 7 | Coverage `server/lap_check.py` fiel auf 89,0 % (Untergrenze 90) | neue Absturzzweige (Prüfung stürzt ab ⇒ REVIEW statt VALID) ungetestet | 6 Tests, die jeden Schritt absichtlich abstürzen lassen (`tests/test_v099_decision.py`); jetzt 95 % |
| 8 | Mutationstest: 39 überlebende Mutanten in `server/lap_decision.py` | Tests prüften Ergebnisse, nicht exakte Werte und Grenzen | `tests/test_v099_decision_units.py` (19 Tests mit von Hand gerechneten Werten und exakten Grenzen); 8 Mutanten als gleichwertig begründet (Abschnitt 6) |
| 9 | Mutationstest meldete 2 „Überlebende“ in `_crossing`, die in V0.9.8 als gleichwertig galten | die Liste ist nach Zeilennummer geschlüsselt; `check_limits` hat die Zeilen um 24 verschoben | Schlüssel aktualisiert und neuer Test `tests/test_v099_mutation_keys.py`, der veraltete Schlüssel sofort meldet |

Außerdem (nicht durch V0.9.9-Code verursacht):

| # | Befund | Behebung |
|---|---|---|
| 10 | pip-audit im Gate am 21.10.: `certifi 2026.7.22` hat seit dem 19.10. eine bekannte Schwachstelle (PYSEC-2026-4320) — beim V0.9.8-Basislauf am 20.10. noch nicht gemeldet | Pin in `constraints.txt` auf `certifi==2026.10.19` (Fix-Version), pip-audit danach: keine bekannten Schwachstellen. Betrifft auch das laufende V0.9.8 auf dem NAS — das Update auf V0.9.9 behebt es mit. |
| 11 | erster Gate-Probelauf brach beim Einsammeln der Tests ab | Reste eines älteren `--zip`-Laufs (`release/stage/…`) lagen im Arbeitsbaum; gelöscht |

## 3. Vorbestehender Zufallsfehler (V0.9.8) — gefunden, Ursache bewiesen, behoben

Im vollen Coverage-Lauf schlug **einmal**
`tests/test_catalog_venues.py::test_searching_fuji_returns_the_whole_track_with_every_variant` fehl
(„1 == 2“: die Runde „Fuji Speedway - Classic“ fehlte). Drei Wiederholungen waren grün.

Ursache: der Test-Helfer `_upload` in `tests/test_catalog.py` (und `tests/test_team_dashboard.py`)
machte zwei Runden unterscheidbar mit `salt = hash((strecke, auto, modell)) % 1000`. Pythons `hash()`
für Zeichenketten ist pro Prozess zufällig (`PYTHONHASHSEED`); für jedes Rundenpaar eines Tests
besteht pro Start eine Chance von etwa 1 : 1000, dass beide dasselbe Salz bekommen ⇒ byte-gleiche Dateien ⇒ der Server erkennt korrekt ein Duplikat
(gleicher Fahrer) bzw. in V0.9.9 eine fremde Datei (anderer Fahrer, REVIEW).

Beweis: Suche nach einem Startwert mit Kollision ⇒ `PYTHONHASHSEED=695`. Damit:

```
cd base098/"Garage16 - LMU"                       # unverändertes V0.9.8
PYTHONHASHSEED=695 python -m pytest -q tests/test_catalog_venues.py   →  1 failed, 26 passed
```

Der Fehler steckt also schon in V0.9.8 (kein Produktfehler, ein Test-Helfer-Fehler). Behebung: ein
Zähler pro Prozess statt `hash()` — jede Hochladung bekommt garantiert eigene Bytes. Danach mit
`PYTHONHASHSEED` 695, 0 und 1: 87 bestanden (alle Tests, die den Helfer nutzen).

## 4. Referenzmessung der Tracklimit-Erkennung (synthetisch)

```
python -m scripts.trackcheck_eval            # schreibt nichts, vergleicht mit tests/reference/trackcheck_metrics.json
```

Wahrheit aus der Geometrie (alle vier Räder ≥ 0,25 s jenseits der weißen Linie), nicht aus dem
Algorithmus. „Erkannt“ = REVIEW oder INVALID. Nicht manipulierte Runden:

| Satz | n | TP | FP | TN | FN | Präzision | Recall | FPR | FNR |
|---|---|---|---|---|---|---|---|---|---|
| kuratiert | 38 | 10 | 0 | 26 | 2 | 100 % | 83,3 % | 0 % | 16,7 % |
| Raster | 480 | 180 | 0 | 240 | 60 | 100 % | 75,0 % | 0 % | 25,0 % |

Nur automatische Ungültigkeit (INVALID): Präzision 100 %, Recall 33,3 % / 25,0 % — absichtlich
(nur LMUs eigenes Urteil macht ungültig, alles andere ist REVIEW). Alle FN sind dieselbe Art: alle
vier Räder auf dem breiten Kerb bzw. wenige cm hinter der Linie, LMU meldet nichts. **Echte Runden
sind nicht gemessen** — das geht nur mit dir (NAS-Anleitung Teil D).

Weitere Tests: 52 kuratierte Fälle mit dokumentierter Erwartung (`tests/helpers/trackcheck_cases.py`),
exakte Grenzwerte, Hypothesis-Eigenschaften (Rauschen, entfernte Signale, Umordnung; feste
Seeds, `derandomize`), alle in `tests/test_v099_trackcheck_reference.py`.

## 5. Manipulationstests

14 manipulierte bzw. unzureichende Aufzeichnungen (`tests/helpers/trackcheck.py` / `trackcheck_cases.py`:
LMU-Flags entfernt, alle Tracklimit-Signale entfernt, Flag-Werte, die LMU nie schreibt, gefälschte
Flags und Oberflächen, Positionssprung 300 m, Zeitachse um 7 % gestaucht, Geschwindigkeitsspitze,
vertauschte Blöcke, vor der Linie abgeschnitten, eingefügte Wiederholung, Ausflug mit eingefrorenen
Werten überklebt, nur 8 Bilder/s, 1,5 s Lücke, konsistente Komplettfälschung): **13 nicht als VALID durchgelassen**.
Durchgelassen: eine in *jedem* Signal konsistente Fälschung — als Grenze dokumentiert
(QUALITY.md „Limits of the detection“).

Server-seitig (`tests/test_v099_integrity.py`, `tests/test_v099_decision.py`): Datei nach dem
Speichern verändert / gekürzt / getauscht / gelöscht, Hash/Größe/Rundenzeit in der DB geändert,
MAC entfernt, Audit-Zeile gelöscht / geändert / gefälscht (auch mit unbekanntem Schlüssel),
Symlink im Speicher, Symlink auf die Temp-Datei, Pfad-Traversal, Dekompressionsbombe, exakte
Dekodiergrenzen, gleicher Upload zweimal, fremde Datei, abgebrochener Prozess mitten im Speichern
(echter Kindprozess, `SIGKILL`). Alle mit dem erwarteten Ergebnis (Quarantäne, REVIEW, INVALID,
eindeutiger Fehlercode), siehe die Testnamen.

## 6. API-Vergleich V0.9.8 → V0.9.9

Maschineller Vergleich von `tests/reference/openapi.json` beider Stände:

* entfernte Endpunkte: **keine**; entfernte oder umbenannte Felder: **keine**; neue Pflichtfelder: **keine**
* neu: `GET /admin/audit`, `GET /admin/audit/verify`, `GET /admin/integrity`, `POST /admin/laps/{lap_id}/integrity`
* `AccountExport`: zusätzlich `lap_files`, `audit_events` (optional)
* `GET /admin/review?status=`: erlaubt zusätzlich `held`, `quarantined` (Standard unverändert `pending`)
* Upload-Antwort unverändert `{status, lap_id, duplicate}` — Desktop-Client 0.9.4 bleibt kompatibel.

Inhaltlich neu (kein Strukturbruch): `review_status` kann jetzt `held` oder `quarantined` sein;
Analyse-Zugriffe auf eine veränderte Datei antworten 422 `INTEGRITY_FAILED` (fehlende Datei wie bisher 404).

## 7. Mutationstests

```
python -m scripts.mutation_check            # alle 12 Ziele, gestagte Kopie, 300 s Zeitlimit je Mutant
```

Verlauf während V0.9.9:

| Lauf | Stand | Ergebnis |
|---|---|---|
| 1 (`lap_decision`, `decision_status`, …) | nach den ersten V0.9.9-Tests | 163 von 202 getötet — 39 Überlebende in `lap_decision` (Rundung, Grenzen, Offsets) |
| 2 (`lap_decision`, `audit_chain`, `decision_status`) | + `tests/test_v099_decision_units.py`, 8 gleichwertige begründet | 142 von 145 getötet — 3 Überlebende (Beschleunigung exakt an der Grenze, Rundung `by_lap_dist_m`, Standard `tail_samples`) |
| 3 (alle Ziele, 20.10.) | + 3 präzisere Tests, **noch vor** zwei Testergänzungen und der Korrektur der `_crossing`-Schlüssel | 340 von 342 getötet — die 2 „Überlebenden“ waren die als gleichwertig geführten `_crossing`-Mutanten mit veralteter Zeilennummer |

**Korrektur (V0.9.9.1-Audit, Abschnitt 11.3):** Lauf 3 lief nicht auf exakt dem Stand des ersten
V0.9.9-Archivs (spätere Tests und die korrigierten `_crossing`-Schlüssel fehlten ihm), und die Aussage „27 gleichwertige Mutanten“ war falsch gezählt: 27 Schlüssel
übersprangen **31** Mutanten, weil vier Schlüssel je zwei Mutanten mit gleicher Beschreibung trafen.
Die nachgeholten Läufe auf dem Archiv-Code und auf dem Endstand stehen in Abschnitt 11.3; sie
ersetzen Lauf 3 als Nachweis.

## 8. Coverage

Im Release-Gate (ganzer Lauf, Zeilen / Zweige, Untergrenze): gesamt 89,4 / 79,2 (88 / 77);
`server/integrity.py` 99,0 / 98,0, `server/lap_decision.py` 97,8 / 93,8, `server/audit.py`
97,2 / 93,8, `server/official.py` und `server/request_context.py` 100 / 100, `server/lap_check.py`
96,9 / 92,9, `server/upload_log.py` 99,1 / 92,9.

Verlauf: `server/lap_check.py` war zwischendurch auf 89,0 % unter seine Untergrenze 90 gefallen
(neue Absturzzweige) → 6 Tests, die jeden Schritt absichtlich abstürzen lassen. Der Gate-Probelauf
vom 21.10. meldete `server/upload_log.py` 94,6 % < 96 (neue Fehlerbehandlung beim Audit-Aufräumen)
→ Test für beide Fehlerpfade. `server/integrity.py` hatte 84 % Zweige → 10 Tests für Schlüsselquellen,
Lesefehler, verschwindende Dateien, MAC ohne Größe, vollen Zwischenspeicher. Untergrenzen neu für
die fünf neuen Module, erhöht für `integrity.py` (98 / 97) und `lap_check.py` (96 / 92); keine gesenkt.

## 9. Performance V0.9.8 ↔ V0.9.9 (gleiche Maschine, gleiche Daten)

Methode: `scripts/bench_compare.py` (in beiden Bäumen identisch) mit
`--laps 40 --threads 4 --repeat 5`, frische Datenbank und frischer Speicherordner je Lauf, erzeugte
Runden (≈ 14 km, 60-Hz-Abfragen von 50-Hz-Bildern, alle 0.9.4-Spalten, Sensorrauschen):
normale Runde 8,0 MB / 12 600 Zeilen, lange Runde (9 min) 19,9 MB / 32 400 Zeilen. 40 Uploads
nacheinander, dann 40 mit 4 Threads. Container mit 2 vCPU.

Jede Kombination wurde **zweimal** gemessen: am 20.10. abends (Maschine spürbar belastet: sha256 nur
≈ 380 MB/s) und am 21.10. morgens ohne Last (≈ 1 000–1 500 MB/s). Die Tabelle zeigt den ruhigen
Lauf; der erste Lauf zeigte dieselben Richtungen bei größerer Streuung (Rohdaten aller acht Läufe:
`docs/bench_v099/`, Dateiname `<version>_<datenbank>[2].json`).

| Messung (Lauf 2, ruhig) | SQLite 0.9.8 | SQLite 0.9.9 | PostgreSQL 0.9.8 | PostgreSQL 0.9.9 |
|---|---|---|---|---|
| sha256 (MB/s) | 1 490 | 1 520 | 1 009 | 1 414 |
| Lap-Check normale Runde (Median ms) | 45,7 | 43,3 | 39,3 | 57,2 |
| Lap-Check lange Runde (ms) | 82,4 | 87,0 | 74,8 | 96,0 |
| Speicher-Spitze Check (MB, normal / lang) | 8,7 / 19,2 | 8,8 / 19,2 | 8,7 / 19,2 | 8,7 / 19,2 |
| Upload nacheinander (Runden/s) | 17,5 | 15,9 | 16,7 | 15,1 |
| Upload p50 / p95 (ms) | 51,6 / 82,3 | 56,7 / 69,4 | 56,6 / 70,9 | 61,4 / 84,6 |
| Zeit in der Schreibsperre p50 / p95 (ms) | 36,6 / 57,0 | 41,2 / 49,6 | 41,5 / 51,7 | 46,2 / 60,0 |
| SQL-Anweisungen je Upload | 14 | 18 | 12 | 17 |
| 4 parallele Uploader (Runden/s, Fehler) | 14,4, 0 | 14,2, 0 | 14,0, 0 | 14,7, 0 |
| Analyse erster Zugriff (ms) | 6,2 | 12,2 | 7,1 | 14,0 |
| Analyse wiederholter Zugriff (ms) | 6,1 | 5,8 | 7,0 | 6,9 |
| DB-Zuwachs je Runde (Bytes, Lauf 1) | 8 346 | 10 598 | 7 066 Laps + 2 970 Rest | 10 138 Laps + 2 970 Rest + 2 458 Audit |

Bewertung:
* **Erster Analyse-Zugriff +6–7 ms**: die sha256-Prüfung der 8-MB-Datei (≈ 5–6 ms) plus MAC —
  gewollt. Wiederholte Zugriffe kommen 10 Minuten aus dem Zwischenspeicher (nur `stat` + MAC):
  gleich schnell wie V0.9.8.
* **Upload ≈ +5 ms im Median (≈ +10 %)**, Schreibsperre ≈ +5 ms: 4–5 zusätzliche SQL-Anweisungen
  (Prüfung auf fremde Datei, Audit-Zeile mit Kettenkopf, MAC). p95 streut in beide Richtungen.
  Mit 4 parallelen Uploadern kein Unterschied (die Schreibsperre serialisiert ohnehin).
* Lap-Check: SQLite gleich, PostgreSQL in Lauf 2 +18–21 ms, in Lauf 1 −27 ms bzw. +10 ms — die
  Entscheidungsschicht kostet laut Einzelmessung wenige ms; der Rest ist Streuung.
* Speicher: unverändert (die neue Prüfung arbeitet auf den schon geladenen Spalten).
* Datenbank: ≈ +3 KB je Runde (Lap-Spalten, Entscheidungsprotokoll) + ≈ 2,5 KB Audit
  (365 Tage). 1 000 Runden/Jahr ≈ 5 MB — für Backups unerheblich; die Telemetrie-Dateien
  (8–20 MB je Runde) bleiben gleich groß.

Budget (begründet aus den Messungen, für den NAS mit Faktor 3 für die langsamere CPU): Upload p95
≤ 500 ms, erster Analyse-Zugriff ≤ 300 ms für eine 20-MB-Datei, Lap-Check ≤ 600 ms für die lange
Runde. Die echten NAS-Werte misst Teil A/E der NAS-Anleitung.

## 10. Abschluss-Gate

```
export GARAGE16_TEST_PG_ADMIN_URL=postgresql://postgres@127.0.0.1:5433/postgres   # lokaler Test-Superuser
python -m scripts.release_check --report-dir <tmp>/gate99_dry                   # Probelauf, gleicher Code
bash scripts/test_against_postgres.sh postgresql://garage16_test:…@127.0.0.1:5433/garage16_test
python -m scripts.release_check --zip Garage16_Lmu_V0_9_9.zip --zip-prefix "Garage16 - LMU"
python -m scripts.release_check --verify-archive Garage16_Lmu_V0_9_9.zip
```

| Lauf | Ergebnis |
|---|---|
| Gate-Probelauf 1 (21.10.) | abgebrochen: Reste eines alten `--zip`-Laufs im Baum; pip-audit: certifi (Abschnitt 2, Nr. 10/11) |
| Gate-Probelauf 2 | 1450 bestanden; 1 Coverage-Untergrenze verfehlt (`upload_log.py`) → Test ergänzt |
| Gate-Probelauf 3 (Endstand des Codes) | **PASSED** — 1452 bestanden, 0 fehlgeschlagen, 3 Opt-in übersprungen, ruff/bandit/pip-audit/Coverage bestanden, 411 s |
| PostgreSQL 16 (`test_against_postgres.sh`) | Migrationskette hin–zurück–hin bestanden; 1430 bestanden, 0 fehlgeschlagen, 25 übersprungen (21 Drills ohne Superuser-URL — laufen im Gate —, 3 Opt-in, 1 SQLite-spezifisch) |
| Gate mit `--zip`, Lauf 1 | PASSED — 1452 bestanden, Coverage identisch mit Probelauf 3. Beim Prüfen des Archivinhalts fiel auf, dass QUALITY.md und dieser Bericht `test-results.xml`/`coverage.json` als Archivinhalt nannten (nur `TEST_REPORT.md` und `environment.json` sind drin) → Text korrigiert, Archiv verworfen |
| Gate mit `--zip`, Lauf 2 (ausgeliefertes Archiv) | Ergebnis im Archiv: `release/TEST_REPORT.md` |
| `--verify-archive` auf dem fertigen Archiv | in der Übergabe-Nachricht (kann nicht im Archiv selbst stehen) |


## 11. V0.9.9.1-Audit vor dem NAS-Update (21.10.)

Auftrag: drei gezielte Punkte, keine neuen Features, keine gelockerten Tests. Ausgangspunkt war das
ausgelieferte Archiv (sha256 `0194958845ba5b3fce5e76a5b0600a7af9341fbe010d24a4413e8a59ab6a97f9`):
entpackt und mit dem Arbeitsbaum verglichen (`diff -rq`, ohne Caches) — identisch.

### 11.1 Integritäts-Cache: `st_ctime_ns` im Fingerabdruck — **PASS**

Befund: der Fingerabdruck war (Größe, mtime, Inode, Gerät). Eine Datei, die an Ort und Stelle (gleiche
Inode) mit gleicher Größe verändert und deren mtime mit `utime()` zurückgesetzt wird, sah für den
Cache unverändert aus — der Analyse-Zugriff antwortete bis zu 10 Minuten „ok“ (die volle Prüfung und
`integrity_check` fanden es immer). Der alte Test hielt das sogar als „dokumentierte Grenze“ fest.

Änderung: `server/integrity.py` `_fingerprint` = (Größe, mtime, **ctime**, Inode, Gerät). Die ctime
setzt der Kernel bei jedem Schreiben und jedem `utime()` neu; ein Programm kann sie nicht zurücksetzen.

Regressionstest `tests/test_v099_integrity.py::test_a_change_that_keeps_size_and_resets_mtime_is_found_despite_a_cached_entry`
(ersetzt den alten Grenz-Test, strenger): geprüft → Cache-Eintrag vorhanden → Byte geändert, gleiche
Inode und Größe, mtime zurückgesetzt → Vorbedingungen geprüft (Größe/mtime/Inode/Gerät gleich, ctime
anders) → Zugriff über den Cache meldet `hash_mismatch` und Quarantäne.

| Lauf | Ergebnis |
|---|---|
| Test mit altem Fingerabdruck (ctime testweise entfernt) | 1 failed — „found through the cache: 'ok' == 'hash_mismatch'“ |
| Test mit neuem Fingerabdruck | passed |
| `tests/test_v099_integrity.py` + `test_v099_integrity_units.py` | 42 passed |

### 11.2 Restore vergleicht den Audit-Anker vor dem Umschalten — **PASS**

Befund: `scripts/restore.sh` prüfte die Audit-**Kette** (`consistency_check --deep`), verglich aber den
`audit_head` des Manifests nie. Eine gelöschte **letzte** Audit-Zeile lässt die Kette intakt
aussehen; eine mit dem Schlüssel neu signierte letzte Zeile verifiziert. Der Abschlussbericht
behauptete die Kopf-Prüfung zu Unrecht („Kettenkopf … Restore-Prüfung“) — korrigiert.

Zweiter Befund dabei: `backup.sh` und `backup-loop.sh` lasen den Kopf mit einer eigenen Abfrage
**nach** `pg_dump`. Admin-Entscheidungen und Quarantänen schreiben Audit-Zeilen ohne die
Upload-Schreibsperre — eine solche Zeile zwischen Dump und Abfrage hätte im Manifest gestanden,
aber nicht im Dump, und der neue Vergleich hätte ein einwandfreies Backup abgelehnt. Jetzt wird der
Kopf aus dem Dump selbst gelesen (höchste ID in den `audit_events`-Daten; mit GNU awk und busybox
awk geprüft).

Änderungen: `server/audit.py` `parse_head_spec` / `compare_head`; `scripts/consistency_check.py
--audit-head`; `scripts/restore.sh` liest `audit_head` streng (Form `v<n>:<kid>:<hex>`, weil der
Wert an den Prüfbefehl geht) und übergibt ihn an Schritt 4 — nach Migration und `--deep`-Prüfung,
vor Schritt 5 (Stopp) und 6 (Umschalten). Regeln für ältere Backups (docs/RUNBOOKS.md 6): ohne
`audit_head` (V0.9.8) nicht verglichen, mit Hinweis; `null` → keine Audit-Zeile erlaubt; unlesbar →
Abbruch in Schritt 1.

| Test | Ergebnis |
|---|---|
| Drill: letzte Audit-Zeile im Dump **gelöscht**, sha256/Größe im Manifest angepasst | passed — Abbruch in Schritt 4 („rows at the end are missing“), Live-Stand unverändert, Staging-DB entfernt, kein `compose stop` |
| Drill: letzte Zeile **neu geschrieben, mit gültigem MAC** (Angreifer mit Schlüssel) | passed — Abbruch in Schritt 4 („row N differs from the backup“), sonst wie oben |
| Drill: dieselben Dumps ohne `audit_head` | passed — Restore läuft durch: Beweis, dass die Kette allein sie nicht erkennt |
| Drill: Backup ohne `audit_head` (V0.9.8-Format) | passed — Restore wie bisher, Hinweis „not compared“ |
| Drill: `audit_head: null` bei vorhandenen Zeilen / `"x"` / Shell-Einschleusung im MAC | passed — Abbruch (Schritt 4 bzw. 1), keine Ausführung |
| beide Abbruch-Drills mit abgeschaltetem Vergleich | 2 failed — die Tests erkennen das Fehlen der Prüfung |
| `tests/test_v0991_audit_anchor.py` (Regeln, CLI, gelöschte / mit Schlüssel neu geschriebene / angehängte Zeile, `none`, `absent`) | 7 passed |
| `tests/test_v098_backup_restore.py` gesamt (21 bisherige + 7 neue Drills, echter PostgreSQL) | 28 passed |

### 11.3 Mutationstest-Nachweis und Klärung der Zählung — **PASS**

Klärung: `mutants()` erzeugt auf diesem Code **371** Varianten. Die 27 `EQUIVALENT`-Schlüssel waren
nach Beschreibungstext geschlüsselt; vier Texte kamen je zweimal vor (zwei gleiche Änderungen auf
einer Zeile, z. B. beide `- 1` in `np.clip(x - 1, 0, len(anchors) - 1)`), also übersprangen 27
Schlüssel **31** Mutanten. Auf dem Code des Archivs: 371 − 31 = **340 getestet**. Die frühere Angabe
„342 Mutanten, 340 getötet, 2 Überlebende“ stammt aus Lauf 3 vom 20.10., bei dem zwei `_crossing`-
Schlüssel veraltet waren (2 Mutanten weniger übersprungen: 371 − 29 = 342) — sie war nicht auf dem
Archiv-Code gemessen.

Schwerer wiegt: drei der doppelt übersprungenen Mutanten waren **nicht** gleichwertig (der Schlüssel
und seine Begründung meinten jeweils die andere Stelle): `distance_axis` Zeile 100 `searchsorted(...)
- 1 → + 1` und `- 1 → - 2`, Zeile 116 untere `clip`-Grenze `0.0 → 1.0`. Einzeln ausgeführt: alle drei
**getötet** — es gab also keinen unentdeckten Überlebenden, nur eine falsche Zählung. Behebung:
gleiche Beschreibungen werden durchnummeriert („… #2“), die Schlüssel zeigen auf die richtige Stelle,
`check_limits` Zeile 160 bekommt den zweiten (ebenfalls nur die Meldung betreffenden) Schlüssel, und
`tests/test_v099_mutation_keys.py` verlangt eindeutige Beschreibungen und je Schlüssel genau einen
Mutanten.

| Lauf (alle 12 Ziele) | Code | erzeugt | gleichwertig übersprungen | getestet | getötet | überlebt |
|---|---|---|---|---|---|---|
| A | exakt das erste V0.9.9-Archiv (entpackt) | 371 | 31 (27 Schlüssel) | 340 | **340** | 0 |
| B | Endstand V0.9.9.1 (Code-Hash beim Start und Ende gleich) | 371 | 28 (28 Schlüssel, je 1) | 343 | **343** | 0 |

Je Ziel in Lauf B (getötet): client_ip 10, records 11, distance_axis 71, crossings 49, write_lock 10,
rate_limit 10, lap_decision 115, decision_status 17, integrity 30, official 3, upload_limits 4,
audit_chain 13. Vollständige Logs: `docs/mutation_v0991/v099_zip_code.txt`,
`docs/mutation_v0991/v0991_final_code.txt`. Zwischen Lauf B und dem Archiv wurden nur Dokumente und
`tests/coverage_floors.json` geändert, kein Code (Abschnitt 11.4).

### 11.4 Gate, Archiv, Verifikation

| Lauf | Ergebnis |
|---|---|
| Gate-Probelauf auf dem V0.9.9.1-Code | **PASSED** — 1467 bestanden (1452 + 15 neue), 0 fehlgeschlagen, 3 Opt-in übersprungen; ruff, bandit, pip-audit, Coverage bestanden (gesamt 89,4 / 79,3; `server/audit.py` 97,8 / 95,7 → Untergrenze auf 97 / 95 erhöht) |
| PostgreSQL 16 (`test_against_postgres.sh`) | Migrationskette hin–zurück–hin bestanden; 1438 bestanden, 0 fehlgeschlagen, 32 übersprungen (28 Drills ohne Superuser-URL — laufen im Gate —, 3 Opt-in, 1 SQLite-spezifisch) |
| Gate mit `--zip`, Lauf 1 | PASSED, 1467 bestanden — beim Prüfen des Inhalts fehlten die beiden Mutationslogs (das Gate schließt `*.log` aus) → in `.txt` umbenannt, Archiv verworfen |
| Gate mit `--zip`, Lauf 2 (ausgeliefertes Archiv) | Ergebnis im Archiv: `release/TEST_REPORT.md` |
| `--verify-archive` auf genau diesem Archiv | in der Übergabe-Nachricht (kann nicht im Archiv selbst stehen) |

## 12. V0.9.9.2 — Fehler beim ersten echten NAS-Update (21.10.)

Beim Update V0.9.5 → V0.9.9 auf der NAS: Build in Ordnung, Migrationen gelaufen, aber
`garage16-server-1` blieb „unhealthy“, `reverse_proxy` startete nicht („dependency failed to start“).
Log: jede Gesundheitsprüfung `GET /health/ready` → **400**; Abfrage im Container:
`{"detail":"HTTPS required. Access this server via its https:// URL."}`.

Ursache: `docker-compose.yml` setzt `LMU_GARAGE_REQUIRE_HTTPS: "1"`, die Gesundheitsprüfung fragt
`http://localhost:8000/health/ready` ohne `X-Forwarded-Proto`, und die HTTPS-Middleware
(`server/main.py` `require_https`) nahm nur `/health` aus. Seit V0.9.8 vorhanden (damals wurde die
Prüfung auf `/health/ready` umgestellt). Kein Test hatte HTTPS-Zwang und Gesundheitsprüfung zusammen
geprüft; die Container-Drills starten den Server ohne HTTPS-Zwang.

Behebung: `/health/live` und `/health/ready` sind ausgenommen wie `/health` (sie geben keine
Details preis). Regressionstest `tests/test_v0992_healthcheck_https.py`: alle drei Pfade antworten
über HTTP bei HTTPS-Zwang, alles andere weiter 400, und jeder Pfad, den die Compose-Prüfung abfragt,
muss in der Ausnahmeliste stehen. Ohne die Korrektur: 3 failed; mit: 5 passed.

Auf der NAS sofort mit derselben einzeiligen Änderung (`sed`) behoben, damit der Betrieb nicht auf
das neue Archiv warten muss; das Archiv enthält dieselbe Zeile (plus Kommentar).

