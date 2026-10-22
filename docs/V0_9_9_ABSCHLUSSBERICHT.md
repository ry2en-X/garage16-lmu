# V0.9.9 — Abschlussbericht

Auftrag: „Garage16-LMU V0.9.9 – Tracklimit-Validierung, Manipulationsschutz und Release-Qualität“.
Gliederung nach Abschnitt 10 des Auftrags. Alle Zahlen stammen aus tatsächlich gelaufenen Befehlen;
Befehle, Rohergebnisse und behobene Fehler stehen in `docs/V0_9_9_PROTOKOLL.md`. Was nur auf dem NAS
und in LMU geprüft werden kann, steht in `docs/V0_9_9_NAS_ANLEITUNG.md` und ist unten als **offen**
markiert — nichts davon ist als bestanden ausgewiesen.

## 1. Geänderte und neue Dateien (gegenüber dem V0.9.8-Archiv)

**Neu (31 Einträge, davon 2 aus dem V0.9.9.1-Audit):**
`alembic/versions/0017_integrity_audit.py` ·
`server/lap_decision.py`, `server/integrity.py`, `server/audit.py`, `server/official.py`, `server/request_context.py` ·
`scripts/integrity_check.py`, `scripts/trackcheck_eval.py`, `scripts/bench_compare.py`, `scripts/bench_server.py` ·
`docs/V0_9_9_BESTANDSAUFNAHME.md`, `docs/V0_9_9_PROTOKOLL.md`, `docs/V0_9_9_NAS_ANLEITUNG.md`,
`docs/V0_9_9_ABSCHLUSSBERICHT.md`, `docs/trackcheck_labels_vorlage.csv`, `docs/bench_v099/` (8 Messdateien) ·
`tests/helpers/lapfile.py`, `tests/helpers/trackcheck.py`, `tests/helpers/trackcheck_cases.py`,
`tests/reference/trackcheck_metrics.json`, `tests/js/v099.test.mjs`,
`tests/test_v099_trackcheck_reference.py`, `tests/test_v099_decision.py`, `tests/test_v099_decision_units.py`,
`tests/test_v099_integrity.py`, `tests/test_v099_integrity_units.py`, `tests/test_v099_mutation_keys.py`,
`tests/test_v099_docs.py` · V0.9.9.1: `tests/test_v0991_audit_anchor.py`, `docs/mutation_v0991/` (2 Mutationslogs).

**Geändert (85, davon `scripts/restore.sh` neu im V0.9.9.1-Audit):**
- Server: `server/lap_check.py`, `track_limits.py`, `models.py`, `records.py`, `leaderboard_ranking.py`,
  `analysis_reference.py`, `consistency.py`, `driver_lifecycle.py`, `storage.py`, `telemetry_analysis.py`,
  `validation.py`, `upload_log.py`, `schemas.py`, `main.py`; Router `telemetry.py`, `analysis.py`,
  `admin.py`, `accounts.py`, `leaderboard.py`, `teams.py`, `drivers.py`, `stints.py`.
- Skripte: `release_check.py`, `mutation_check.py`, `revalidate_laps.py`, `consistency_check.py`,
  `test_requirements.py`, `backup.sh`, `restore.sh` (V0.9.9.1); `docker/backup-loop.sh`, `docker/Dockerfile.server`,
  `docker-compose.yml`, `.env.example`, `.github/workflows/ci.yml`, `constraints.txt`,
  `requirements-dev.txt`, `.gitignore`.
- Web: `web/js/findings.js`, `api.js`, `pages/admin.js`, `pages/dashboard.js`, `analysis/events.js`,
  `analysis/health.js`, `i18n/de.js`.
- Doku: `CHANGELOG.md`, `README.md`, `docs/QUALITY.md`, `ARCHITECTURE.md`, `DATA_PROTECTION.md`,
  `RUNBOOKS.md`, `ADMIN_GUIDE.md`.
- Tests (angepasst an gewollte Verhaltensänderungen oder realistischere Testdaten): 34 Dateien,
  u. a. `tests/helpers/synthetic_lap.py`, `tests/coverage_floors.json`, `tests/reference/openapi.json`,
  `tests/test_catalog.py`, `tests/test_team_dashboard.py` (Abschnitt 3 des Protokolls).

Der Desktop-Client ist unverändert (0.9.4).

## 2. Sicherheitsrelevante Änderungen

| Änderung | Warum |
|---|---|
| Fehlende, leere oder unlesbare Tracklimit-Signale, nur eine Signalfamilie, Lücken, zu wenige Bilder/s, Sprünge, unphysikalische Beschleunigung, eingefrorene Werte, eine fremde Datei, ein Absturz der Prüfung ⇒ **REVIEW** statt VALID | „fehlendes Flag ist kein Beweis“: vorher wurde eine Runde ohne Signale gültig, ein Absturz galt als Freigabe |
| REVIEW = `held`: gespeichert, analysierbar, **nicht** in Bestenlisten/Rekorden/Discord bis zur Admin-Freigabe; Freigabe wertet Rekorde unter der Rekordsperre aus | REVIEW darf keine offizielle Bestzeit erzeugen |
| Eine Abfrageregel für „offiziell“ (`server/official.py`) in allen Bestenlisten, Team- und Profilansichten | keine Ansicht kann die Regel vergessen |
| Alte `pending`-Runden behalten ihren Status und Rekord | keine stille Änderung bestehender Bestzeiten |
| Größe, Upload-Request-ID und Server-MAC je Runde; Original schreibgeschützt (0440) | nachträgliche Änderung der Datei **oder** der DB-Werte wird erkannt |
| Prüfung bei jedem Analyse-Zugriff (sha256, 10 min zwischengespeichert — Fingerabdruck Größe, mtime, ctime, Inode —, MAC); Fehler ⇒ Quarantäne + Audit, Antwort ohne Pfade (404 / 422 `INTEGRITY_FAILED`) | definierter sicherer Zustand, keine Offenlegung interner Pfade |
| Temp-Datei `O_EXCL|O_NOFOLLOW` (0600), Speicherordner muss im Speicher-Root liegen, gespeicherter Pfad darf kein Symlink sein | Symlink-Umlenkung, Traversal |
| Dekodiergrenzen vor dem Lesen (200 000 Zeilen, 1 000 Spalten, 512 MiB entpackt) bei Upload, Analyse, Datenqualität | Dekompressionsbombe |
| Audit-Log als HMAC-Kette mit Append-only-Trigger; Kettenkopf im Backup-Manifest (aus dem Dump gelesen); Restore prüft die Kette **und** (V0.9.9.1) den Kopf gegen das Manifest, vor dem Umschalten | nachvollziehbare, manipulationserkennbare Admin- und Integritätsereignisse; eine gelöschte oder neu signierte letzte Zeile wird erkannt |
| Eigener Integritätsschlüssel `LMU_GARAGE_INTEGRITY_KEY` (rotierbar), sonst aus dem Secret-Key abgeleitet | Trennung der Schlüssel |

## 3. Architektur der Tracklimit-Entscheidung — vorher / nachher

**Vorher (V0.9.8):** `validation.validate_and_read` (Plausibilität) → `track_limits.assess`
(valid/review/invalid) → `is_valid = plausibel ∧ ≠ invalid`; REVIEW zählte vorläufig und erzeugte
sofort Rekord-Ereignisse; fehlende Signale = INFO = gültig; Absturz = gültig.

**Nachher (V0.9.9):** Eingabe (Schema, `validation.py`, Dekodiergrenzen) → Zeitachse und Position
(`validation.py`, `lap_decision._quality/_motion/_frozen`) → Tracklimits (`track_limits.py`,
Regelversion 3) → Entscheidung (`lap_decision.assess`, Version 1: Signalabdeckung je Client-Version,
Evidenz = Anzahl unabhängiger Signalfamilien, ausdrücklich keine Wahrscheinlichkeit) → Protokoll
(`laps.validation.decision`: Status, Quellen, Gründe mit Ort, Datenqualität, Validator-Versionen,
Referenzdistanz) → offizielle Wertung nur bei VALID (`official.py`) → Audit. Jeder Schritt hat ein
eigenes Absturz-Netz, das REVIEW (bzw. INVALID bei Ablehnung durch den Client) ergibt.
Ausführlich: `docs/ARCHITECTURE.md` (Pipeline, ADR-10 bis ADR-12).

## 4. Grenzen der Manipulationserkennung

* Eine in **jedem** Signal konsistent gefälschte Datei ist von einer echten Runde nicht zu unterscheiden.
  Die Upload-Signatur beweist, wer hochlädt, nicht dass LMU die Daten erzeugt hat.
* Die Datei-Integrität gilt ab dem Speichern auf dem Server; ob sie vorher echt war, prüft sie nicht.
* Wer den Integritätsschlüssel **und** Schreibrechte auf DB und Volume hat, kann konsistent fälschen.
* Zeitachse um < 5 % gestaucht wird nicht erkannt; Abkürzen bei schweigendem LMU nur über Räder/Kante
  oder als kürzere Runde (ab 3 Vergleichsrunden).
* Alle vier Räder auf dem Kerb bzw. wenige cm hinter der Linie bei schweigendem LMU: nicht erkennbar
  (einzige Art der gemessenen False Negatives).
* Überlappende Runden eines Fahrers werden nicht abgeglichen.
* Die Audit-Kette erkennt Änderungen, verhindert aber nicht, dass jemand mit DB-Superuser-Rechten den
  Trigger entfernt und die ganze Kette mit dem Schlüssel neu schreibt.

## 5. Testbefehle und Ergebnisse

| Befehl | Ergebnis |
|---|---|
| `python -m scripts.release_check` (V0.9.8-Archiv, Ausgangsstand) | PASSED — 1218 bestanden, 0 fehlgeschlagen, 3 Opt-in übersprungen |
| `python -m scripts.release_check --zip Garage16_Lmu_V0_9_9.zip --zip-prefix "Garage16 - LMU"` (gestagte Kopie genau der ausgelieferten Dateien, Stand V0.9.9.1) | **PASSED** — 1467 bestanden, 0 fehlgeschlagen, 0 Fehler, 3 übersprungen (nur die Opt-in-PyInstaller-Builds); ruff, bandit, pip-audit, Coverage-Untergrenzen bestanden. Der Bericht liegt im Archiv: `release/TEST_REPORT.md` (Ergebnis, Zahlen, jede Coverage-Zeile), `release/environment.json` |
| `python -m scripts.release_check --verify-archive Garage16_Lmu_V0_9_9.zip` (frisch entpackte Kopie, sha256 und Baum-Hash geprüft, Gate erneut) | siehe Übergabe-Nachricht (läuft auf dem fertigen Archiv, kann nicht im Archiv stehen) |
| `scripts/test_against_postgres.sh` (PostgreSQL 16) | Migrationen upgrade head → downgrade base → upgrade head bestanden; 1438 bestanden, 0 fehlgeschlagen, 32 übersprungen (28 Backup/Restore-Drills, die einen PG-Superuser brauchen und im Gate laufen, 3 Opt-in-Builds, 1 SQLite-spezifischer Test) |
| `python -m scripts.mutation_check` (Endstand V0.9.9.1) | 371 erzeugt, 28 gleichwertig übersprungen, **343 getestet, 343 getötet** (Abschnitt 7) |
| `python -m scripts.mutation_check` (Code des ersten V0.9.9-Archivs) | 371 erzeugt, 31 übersprungen, 340 getestet, 340 getötet |
| `python -m scripts.trackcheck_eval` | Werte wie Abschnitt 8, identisch mit `tests/reference/trackcheck_metrics.json` |
| `scripts/bench_compare.py` × 8 Läufe | Abschnitt 9 |
| `PYTHONHASHSEED=695 pytest tests/test_catalog_venues.py` auf V0.9.8 | 1 fehlgeschlagen → beweist den vorbestehenden Zufallsfehler; nach der Korrektur mit Seeds 695/0/1 je 87 bestanden |

Der Gate-Lauf im strikten Modus wertet jeden übersprungenen Test als Fehler (Ausnahme nur die drei
dokumentierten Opt-in-Builds). Kein Test ist als bestanden ausgewiesen, der nicht gelaufen ist.

## 6. Line- und Branch-Coverage

Aus dem Release-Gate (ganzer Testlauf, Zeilen / Zweige, Untergrenze in Klammern):

| Modul | Zeilen | Zweige |
|---|---|---|
| gesamt | 89,4 (88) | 79,3 (77) |
| `server/integrity.py` | 99,0 (98) | 98,0 (97) |
| `server/lap_decision.py` | 97,8 (97) | 93,8 (93) |
| `server/audit.py` | 97,8 (97) | 95,7 (95) |
| `server/official.py` | 100 (100) | 100 (100) |
| `server/request_context.py` | 100 (100) | 100 (100) |
| `server/lap_check.py` | 96,9 (96) | 92,9 (92) |
| `server/records.py` | 100 (100) | 100 (100) |
| `server/track_limits.py` | 94,9 (94) | 84,9 (84) |
| `server/upload_log.py` | 99,1 (96) | 92,9 (92) |
| `server/routers/telemetry.py` | 94,1 (93) | 90,0 (90) |

Untergrenzen für die neuen Module gesetzt, für `integrity.py` und `lap_check.py` erhöht; keine
Untergrenze gesenkt. Alle Module des Gates: `release/TEST_REPORT.md` im Archiv.

## 7. Mutation Testing

Endlauf `python -m scripts.mutation_check` (alle 12 Ziele) auf dem Code, der im Archiv liegt:
**371 Mutanten erzeugt, 28 als gleichwertig übersprungen (je ein Schlüssel mit Begründung in
`scripts/mutation_check.py`), 343 getestet — 343 getötet, 0 überlebt.** Log: `docs/mutation_v0991/`.

Korrektur gegenüber dem ersten V0.9.9-Bericht („342 Mutanten, 340 getötet, 27 gleichwertig“): Diese
Zahlen stammten aus einem Lauf vor den letzten Testergänzungen; außerdem übersprangen 27 Schlüssel
31 Mutanten, weil vier Beschreibungen doppelt vorkamen. Drei dieser übersprungenen Mutanten waren
nicht gleichwertig — einzeln ausgeführt wurden alle drei getötet. Auf dem Code des ersten Archivs
ergab der nachgeholte Lauf 340 von 340 getötet. Seit V0.9.9.1 sind die Beschreibungen eindeutig und
ein Test verlangt genau einen Mutanten je Schlüssel. Verlauf und Tabellen: Protokoll 7 und 11.3.

## 8. Tracklimit-Referenztests

Synthetisch gekennzeichnete Runden (Wahrheit aus der Geometrie):

| Satz | n | TP | FP | TN | FN | Präzision | Recall | FPR | FNR |
|---|---|---|---|---|---|---|---|---|---|
| kuratiert | 38 | 10 | 0 | 26 | 2 | 100 % | 83,3 % | 0 % | 16,7 % |
| Raster | 480 | 180 | 0 | 240 | 60 | 100 % | 75,0 % | 0 % | 25,0 % |

Automatische Ungültigkeit allein: Präzision 100 %, Recall 33,3 % / 25,0 % (gewollt: nur LMUs Urteil
macht ungültig). 14 manipulierte Aufzeichnungen: 13 nicht als VALID durchgelassen. 52 kuratierte Fälle
mit dokumentierter Erwartung, exakte Grenzwerte, Hypothesis-Eigenschaften.
**Offen: echte, von dir gekennzeichnete LMU-Runden** (NAS-Anleitung Teil D).

## 9. Performance gegenüber V0.9.8

Gleiche Maschine (2 vCPU), gleiche erzeugte Daten, je zwei Läufe, SQLite und PostgreSQL (Protokoll
Abschnitt 9, Rohdaten `docs/bench_v099/`): erster Analyse-Zugriff +6–7 ms (sha256-Prüfung,
gewollt), wiederholt gleich; Upload ≈ +5 ms im Median (≈ +10 %, 4–5 zusätzliche SQL-Anweisungen),
parallel kein Unterschied, Speicher unverändert, DB ≈ +5,5 KB je Runde inkl. Audit.
**Offen: Messung auf dem NAS** (Teil A vor, Teil E nach dem Update).

## 10. Backup, Restore, Rückwärtskompatibilität

* **Backup/Restore**: die 21 Drill-Tests aus V0.9.8 (echte Backup-Container als Chroots, PostgreSQL,
  Restore mit Prüfsummen) laufen im Gate und bestehen. Restore lehnt eine Sicherung mit gebrochener
  Audit-Kette oder geänderten Hashes/Größen/Rundenzeiten ab (`consistency_check --deep`), aber nicht
  eine mit nur ausgemustertem Schlüssel. **V0.9.9.1:** 7 weitere Drills — der Kettenkopf der
  geprüften Staging-DB muss dem `audit_head` des Manifests entsprechen; eine gelöschte und eine mit
  gültigem MAC neu geschriebene letzte Audit-Zeile werden vor dem Umschalten abgelehnt; Backups ohne
  `audit_head` (V0.9.8) laufen wie bisher; `null` / unlesbare Werte werden nie als „kein Anker“
  gewertet. Die erste Fassung dieses Berichts nannte die Kopf-Prüfung schon, obwohl `restore.sh`
  sie nicht durchführte — das war falsch und ist jetzt umgesetzt und getestet.
* **Migration 0017**: additiv (drei nullable Spalten, eine Tabelle, Trigger), auf SQLite und
  PostgreSQL vorwärts, rückwärts bis `base` und wieder vorwärts getestet; Rückweg auf V0.9.8 mit
  `alembic downgrade 0016` (ADMIN_GUIDE).
* **Bestehende Daten**: alte Runden bleiben gültig und behalten ihre Rekorde; `pending` zählt weiter;
  Team-Bestzeiten unverändert (Tests der Rekord- und Team-Logik aus V0.9.8 laufen unverändert mit).
* **API**: nur additiv (Protokoll Abschnitt 6), Upload-Antwort unverändert, Desktop-Client 0.9.4
  ohne Änderung kompatibel (Client-Migrations- und Integrationstests bestehen).
* **Export und Löschung**: Export enthält zusätzlich Prüfsummen und Audit-Ereignisse; Fahrer-Löschung
  entfernt die Fahrer-ID aus dem Audit, ohne die Kette zu brechen (getestet).
* **Offen auf dem NAS**: Update mit echten Daten, Backfill der Signaturen, Restore-Übung (Teil B, C, F).

## 11. Bekannte Einschränkungen und Restrisiken

* Schwellen der Entscheidungsschicht sind physikalisch bzw. aus V0.9.7 begründet, aber **nicht an echten
  gekennzeichneten Runden** geprüft — erst Teil D zeigt die echte Fehlalarm-Rate. Bis dahin können
  echte Runden häufiger als nötig in REVIEW landen (Folge: Admin-Arbeit, keine falschen Bestzeiten).
* Nach dem Update sind alte Runden unsigniert, bis `integrity_check --apply --backfill` läuft (Teil B).
* Bestehende `pending`-Runden zählen weiter (bewusst, keine stille Änderung); `revalidate_laps --hold`
  kann sie auf Wunsch zurückhalten.
* Ein Ratenbegrenzer für mehrere Worker fehlt weiterhin (ein Worker, ADR-1).
* Kein externer Penetrationstest, kein Alarmkanal.
* Getrennte DB-Rollen für Audit-Schreiben sind dokumentiert, aber nicht eingerichtet.

## 12. Release-Gate-Status

**Automatisches Release-Gate: PASS** (Lauf auf exakt den archivierten Dateien, Bericht im Archiv).

Checkliste aus Abschnitt 9 des Auftrags:

| Punkt | Stand |
|---|---|
| Bestandsaufnahme | erfüllt (`docs/V0_9_9_BESTANDSAUFNAHME.md`) |
| alle vorhandenen Tests ausgeführt, dokumentiert | erfüllt (Protokoll 1, 10) |
| keine neu verursachten Testfehler | erfüllt (0 fehlgeschlagen; ein vorbestehender Zufallsfehler gefunden und behoben) |
| Referenzfälle gültig / ungültig / unklar | erfüllt (52 kuratierte + 480 Raster-Fälle) |
| FP/FN an gekennzeichneten Daten gemessen | **teilweise**: an synthetisch gekennzeichneten Runden erfüllt; **echte gekennzeichnete Runden offen** (Teil D) |
| fehlende/widersprüchliche Daten nicht still gültig | erfüllt |
| Originale und Analyseergebnisse getrennt | erfüllt |
| Hash-Verifikation erkennt nachträgliche Änderungen | erfüllt (ab Speicherung) |
| beschädigte/verdächtige Dateien nicht in offiziellen Bestzeiten | erfüllt |
| Pfadmanipulation, Archive, Ressourcenmissbrauch | erfüllt |
| Nebenläufigkeit und Abbruch | erfüllt |
| Backup, Restore, Export, Löschung | erfüllt (Container-Drill lokal; auf dem NAS offen, Teil F) |
| API und Bestzeitenlogik rückwärtskompatibel | erfüllt |
| Mutation Testing, Überlebende behoben/begründet | erfüllt |
| Lint, Security, Abhängigkeiten, Coverage | erfüllt |
| Doku und Changelog | erfüllt |
| Gate auf exakt den Archivdateien | erfüllt |
| Archiv reproduzierbar prüfbar, SHA-256 dokumentiert | erfüllt (`--verify-archive`, `.sha256`) |

Der einzige nicht vollständig erfüllte Punkt (echte gekennzeichnete Runden) ist kein Sicherheitsmangel
im Code, sondern eine fehlende Messung, die nur mit dir möglich ist; bis dahin wirkt die Erkennung
eher zu streng (REVIEW statt falscher Bestzeiten). Freigabe-Empfehlung: **ja**, mit Teil D als
nächstem Schritt.

## 13. Release-Archiv

`Garage16_Lmu_V0_9_9.zip`, neu gebaut nach dem V0.9.9.1-Audit (Serverversion bleibt 0.9.9). Die
SHA-256 steht in `Garage16_Lmu_V0_9_9.zip.sha256` neben dem Archiv und in der Übergabe-Nachricht (eine
Datei kann ihre eigene Prüfsumme nicht enthalten). Das erste Archiv
(`0194958845ba5b3fce5e76a5b0600a7af9341fbe010d24a4413e8a59ab6a97f9`) und das nach dem Audit
(`478950277a4dd114c448f5056457189dcb1286e82423d023aeb078f2e2a31197`) sind ersetzt (V0.9.9.2, Abschnitt 14).

## 14. V0.9.9.1-Audit (Nachtrag)

| Punkt | Ergebnis | Nachweis |
|---|---|---|
| 1. Integritäts-Cache mit `st_ctime_ns` | **PASS** | Regressionstest schlägt mit altem Fingerabdruck fehl, besteht mit neuem (Protokoll 11.1) |
| 2. Restore vergleicht `audit_head` vor dem Umschalten | **PASS** | 7 Drills + 7 Unit-Tests bestehen; die Abbruch-Drills schlagen ohne die Prüfung fehl (Protokoll 11.2) |
| 3. Mutationstest-Nachweis auf dem Archiv-Code, Zählung geklärt | **PASS** | 343 / 343 getötet auf dem Endstand, 340 / 340 auf dem ersten Archiv; 371 = 343 + 28 (Protokoll 11.3) |
| V0.9.9.2: Gesundheitsprüfung bei HTTPS-Zwang (beim echten NAS-Update gefunden) | **behoben** | `/health/ready` antwortete der Compose-Prüfung mit 400, der Server wurde nie „healthy“; Ausnahme ergänzt, Regressionstest 3 failed ohne / 5 passed mit Korrektur (Protokoll 12) |
