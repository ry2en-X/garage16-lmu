# V0.9.9 — Bestandsaufnahme vor der Umsetzung (Stand V0.9.8)

Grundlage: das ausgelieferte Archiv `Garage16_Lmu_V0_9_8.zip`
(sha256 `85b58384926b1d0742cf782f4326e28e45d21f6e5b53fa44283e810a4ba02f83`), sauber entpackt und
unverändert getestet (Ergebnis: `docs/V0_9_9_PROTOKOLL.md`, Abschnitt 1). Gelesen: CHANGELOG,
`docs/ARCHITECTURE.md`, `docs/QUALITY.md`, `docs/DATA_PROTECTION.md`, `docs/RUNBOOKS.md`,
`scripts/release_check.py`, `scripts/mutation_check.py`, die unten genannten Module.

## 1. Datenfluss vom Upload bis zur Bestzeit (V0.9.8)

```
Client 0.9.4 (client/telemetry/recorder.py, lap_verdict.py, uploader.py)
  │ parquet-Datei (~190 Spalten) + Umschlag (Metadaten, client_version, sha256) + HMAC(Client-Geheimnis)
  ▼
routers/telemetry.py upload_lap
  1  Umschlag ist JSON-Objekt · Client-Version ≥ LMU_GARAGE_MIN_CLIENT_VERSION (426)
  2  HMAC über den Umschlag (401)            ← beweist: Absender kennt das Geheimnis, NICHT: Daten echt
  3  Metadaten-Schema LapMetadata (extra=forbid, Wertebereiche, StrictBool) (400)
  4  Datei → .upload-<uuid>.tmp im Zielordner, sha256 + Größe beim Empfang, Limit 50 MiB (413)
  5  sha256 == Umschlag.telemetry_hash (400) · gleicher Fahrer + gleicher Hash → Duplikat (200)
  6  rename → <sha256>.parquet (atomar, gleicher Ordner)       ─┐ unter der geteilten
  7  lap_check.check_lap: EIN Lesen der Datei                    │ Schreibsperre
       validation.validate_and_read  → Plausibilität (ValidationResult)
       track_limits.assess           → Verdict valid/review/invalid + Findings
       lap_insights.compute          → Kennzahlen
  8  Lap-Zeile: is_valid = Plausibilität ∧ Verdict≠invalid; review_status="pending" bei review
  9  link_neighbours (späte Tracklimit-Meldungen → Vorrunde) · Fahrzeug-Identität lernen
 10  Advisory-Lock (Strecke, Modell) → records.evaluate_and_record (PR/WR/TEAM_BEST nur is_valid)
 11  COMMIT; bei Fehler Rollback + Datei löschen, falls keine Zeile auf sie zeigt  ─┘
 12  upload_events: Ergebnis mit Request-ID (eigene Session)
Bestenlisten/Teams/Profile: live aus laps WHERE is_valid (routers/leaderboard.py, teams.py, drivers.py,
leaderboard_ranking.py, analysis_reference.py)
```

## 2. Tracklimit-Erkennung heute (`server/track_limits.py`, Version 2)

| Kanal (Spalte) | Typ | Herkunft | Wer berechnet |
|---|---|---|---|
| `lap_invalidated` | float 0/1 je Sample | LMU `mLapInvalidated` (Client ≥ 0.9.0) | Spiel |
| `count_lap_flag` | float 0/1/2 | LMU `mCountLapFlag` (≥ 0.9.3) | Spiel |
| `track_limits_steps` | float 0…255 | LMU `mTrackLimitsSteps` (≥ 0.9.0) | Spiel |
| `num_penalties` | float | LMU (≥ 0.9.3) | Spiel |
| `surface_type_fl/fr/rl/rr` | float 0–6 | LMU `mSurfaceType` je Rad (≥ 0.9.0) | Spiel |
| `path_lateral`, `track_edge` | float m | LMU, relativ zu LMUs **ungefährem** Mittelpfad (≥ 0.9.3) | Spiel |
| `t`, `lap_dist`, `speed` | float | Client-Zeit, LMU-Rundendistanz (5 Hz), Tempo | Spiel/Client |
| gefahrene Distanz ∫v dt vs. übliche Distanz der Strecke | m | — | Garage16 |
| Client-Urteil, Gründe, Spielzusammenfassung, spätes Fenster | Metadaten | Client ≥ 0.9.3.1 | Client |

Regeln (Tabelle `RULES`): Stufe `lmu` (Spielurteil, kann ungültig machen), `rule` (feste
Garage16-Regeln: Boxengasse, keine Zeit), `heuristic` (eigene Messungen → nur `review`, außer der
Betreiber stuft sie hoch). Grenzwerte: `EDGE_MARGIN_M` 2,0 m, `EDGE_MIN_SECONDS` 0,3 s,
`OFF_MIN_SAMPLES` 3 / 0,05 s, `SHORT_DISTANCE` 1,5 % und ≥ 40 m, `MAX_OFF_SHARE` 30 %,
`SETTLE_SECONDS` 1 s, spätes Fenster 1 s / 6 s. **Keine Streckengeometrie** außer LMUs eigenem
`track_edge`; keine erfundenen Grenzen.

## 3. Gefundene Schwachstellen und Risiken

| # | Befund | Datei | Risiko |
|---|---|---|---|
| T1 | `not_verifiable` (Datei ohne jedes Tracklimit-Signal) ist nur INFO → Status **valid** | track_limits.py | Entfernte Signale = gültige Runde. Verstößt gegen „fehlendes Flag ist kein Beweis“. |
| T2 | `signal_unreliable` ist INFO, das Signal wird ignoriert → bei manipulierten/kaputten LMU-Flags **valid** | track_limits.py | Widersprüchliche oder verfälschte Spielflags machen eine Runde gültig. |
| T3 | Keine Erwartung je Client-Version: ein 0.9.4-Client, dessen Datei `lap_invalidated` fehlt, wird mit den restlichen Signalen beurteilt | track_limits.py / lap_check.py | Ein einzelnes Signal entfernen erhöht unbemerkt die „Sicherheit“. |
| T4 | `review` zählt vorläufig in Bestenlisten und erzeugt sofort PR/WR/TEAM_BEST-Ereignisse (Discord) | records.py, leaderboard*.py | Widerspricht „REVIEW darf keine offizielle Bestzeit erzeugen“. |
| T5 | Keine Prüfung der Bewegungsplausibilität: Positionssprung nach vorn (Teleport/Abkürzung < 15 %) und unmögliche Beschleunigung fallen nur über die 15-%-Integralprüfung auf | validation.py | Abkürzen um wenige hundert Meter bleibt unbemerkt (nur `short_distance`, wenn ≥ 3 Vergleichsrunden existieren). |
| T6 | Zeitlücken/Auflösung fließen nicht in das Urteil ein (nur Diagnose `telemetry_health`) | telemetry_health.py | Eine Lücke in der kritischen Kurve verdeckt einen Verstoß → trotzdem valid. |
| T7 | Dieselbe Datei kann von einem **anderen** Fahrer hochgeladen werden (Idempotenz nur je Fahrer) | routers/telemetry.py | Fremde Runde als eigene Bestzeit (Replay). |
| T8 | Absturz der Tracklimit-Prüfung → Status `valid` (wenn der Client „gültig“ sagt) | lap_check.py | Absturz = Freigabe. |
| I1 | Hash wird beim Upload berechnet und gespeichert, aber **bei späteren Zugriffen nie geprüft** (nur `consistency_check --deep`) | routers/analysis.py `_telemetry_file` | Ausgetauschte/veränderte Datei wird analysiert, Runde bleibt in der Bestenliste. |
| I2 | Keine Quarantäne, kein Prüfstatus für Integritätsfehler | — | Kein sicherer Zustand bei Hash-Abweichung. |
| I3 | Dateigröße, Request-ID der Annahme nicht an der Runde gespeichert | models.py | Größenänderung nicht billig erkennbar; Upload ↔ Runde nur über das 30-Tage-Protokoll. |
| I4 | DB-Hash und Datei konsistent austauschbar (wer DB und Volume schreiben kann, oder ein manipuliertes Backup — Backups liegen unverschlüsselt auf USB) | — | Nicht erkennbar. |
| I5 | Keine Begrenzung der **entpackten** Größe: eine kleine, hoch komprimierte Parquet-Datei kann beim Lesen viel Speicher belegen | validation.py, telemetry_analysis.py | Speicher-Erschöpfung (Dekompressionsbombe). |
| I6 | Temp-Datei mit `open("wb")` (folgt Symlinks), Zielordner wird nicht auf „liegt im Speicher“ geprüft, Dateien bleiben schreibbar (0644) | routers/telemetry.py, storage.py | Symlink im Volume lenkt Schreibzugriffe um; versehentliches Überschreiben. |
| A1 | Kein Audit-Protokoll für Integritäts-, Validierungs- und Admin-Ereignisse; Upload-Protokoll nur 30 Tage, ohne Schutz gegen Änderung | upload_log.py | Nachträgliche Änderungen nicht nachvollziehbar. |
| A2 | Admin-Entscheidungen (Review, Invalidieren) nur im Lap-JSON, ohne Verlauf | routers/admin.py | Nicht nachvollziehbar, wer wann was geändert hat. |

Bereits vorhanden und **erweitert statt ersetzt**: HMAC-Signatur des Umschlags, Schema mit
`extra=forbid`, NaN/Inf-Ablehnung in Pflichtspalten, Hash beim Empfang, inhaltsadressierte Dateinamen
(nie Client-Dateinamen), Größenlimit beim Streamen, Pfadprüfung beim Lesen, Schreibsperre für
Backups, Backup-Manifest mit Prüfsummen, Restore mit sha256-Prüfung jeder Datei, Upload-Protokoll mit
Request-ID, Konsistenzprüfung `--deep`, Review-Warteschlange, `revalidate_laps` (Trockenlauf).

## 4. Geplante Änderungen (Kurzfassung)

1. **Entscheidungsschicht** `server/lap_decision.py` (neu, klein): fasst Plausibilität, Tracklimit-Urteil,
   Signalabdeckung je Client-Version, Bewegungs- und Zeitachsen-Plausibilität, Wiederverwendung fremder
   Dateien und Integrität zu VALID / INVALID / REVIEW zusammen; Validator-Version, Quellen, Gründe,
   Stellen, Datenqualität, Evidenzstärke (ausdrücklich **keine** Wahrscheinlichkeit). Behebt T1–T3, T5–T8.
2. **REVIEW hält zurück** (T4): neuer Prüfstatus `held` — die Runde ist gespeichert und analysierbar,
   aber nicht in Bestenlisten/Rekorden, bis ein Admin sie freigibt; Rekord-Ereignisse entstehen erst bei
   Freigabe. Bestehende `pending`-Runden behalten ihr bisheriges Verhalten (keine stille Änderung).
   Eine Abfrage-Regel `official_lap()` für alle Bestenlisten.
3. **Integrität**: Größe, Request-ID und ein Server-MAC (eigener Schlüssel) je Runde; Prüfung bei
   Analyse-Zugriff (zwischengespeichert), Audit, Neubewertung, Restore-Validierung; Quarantäne (`quarantined`)
   mit Admin-Diagnose und Freigabe; `scripts/integrity_check.py`. Behebt I1–I4.
4. **Upload-Härtung**: Parquet-Kennung, Grenzen für Zeilen/Spalten/entpackte Größe (auch beim Lesen),
   `O_EXCL|O_NOFOLLOW`, Zielordner muss im Speicher liegen, Dateien schreibgeschützt (I5, I6).
5. **Audit-Protokoll** `audit_events` mit verketteter HMAC-Kette, DB-Trigger gegen Änderungen, Prüfskript,
   Kopf-Prüfsumme im Backup-Manifest (A1, A2).
6. Migration 0017 (additiv, rückrollbar). Keine Änderung bestehender API-Felder; neue Felder nur additiv.

## 5. Konsistenz über Runden, Stints und Sessions (Befund V0.9.8, unverändert gültig)

| Fall | Behandlung | Bewertung |
|---|---|---|
| Spätes Tracklimit-Signal nach der Ziellinie | `link_neighbours`: die Vorrunde bekommt einen `late_track_limits`-Befund (REVIEW), egal in welcher Reihenfolge beide Runden eintreffen | konsistent; V0.9.9: eine bereits gespeicherte Runde behält ihren Rekord vorläufig („pending“), nur die gerade hochgeladene wird zurückgehalten |
| Stints | der Stint Analyzer liest nur gültige, sichtbare Runden; ungültige sind „nicht sauber“ (`server/stints.py`) | konsistent (Test: unvollständige letzte Runde) |
| Überlappende Runden eines Fahrers | nicht geprüft — die Session-Uhr beginnt bei einem Neustart von vorn, ein Abgleich gäbe Fehlalarme | Grenze, dokumentiert (QUALITY.md) |
| Nachträglich neu bewertete Runden | `revalidate_laps` mit gespeicherten Eingaben, gleiche Funktion wie beim Upload; ändert nur mit `--apply`, Rekord-Wirkung im Trockenlauf sichtbar | V0.9.9: Integrität vorher geprüft, jede Änderung im Audit |
| Dieselbe Datei erneut | gleicher Fahrer: Duplikat (idempotent); anderer Fahrer: V0.9.9 REVIEW (`telemetry_reused`) | geschlossen |

## 6. Härtung von Server, Speicher und Datenbank (was geprüft und was getan wurde)

| Punkt | Befund | V0.9.9 |
|---|---|---|
| Dateirechte | Container läuft als `garage` (gosu), Dateien 0644 | Original nach dem Speichern 0440, Temp-Datei 0600 |
| Prozess-Trennung | ein Prozess für Web und Upload, schreibt nur ins Telemetrie-Volume | unverändert (eine zweite Rolle brächte für einen Freundeskreis-Server Komplexität ohne konkreten Nutzen) — dokumentiert |
| Datenbankrechte | eine Rolle (Eigentümer) für alles | Trigger verhindert Änderungen an Audit-Zeilen; getrennte Rollen als Empfehlung dokumentiert (nur sinnvoll, wenn mehrere Personen DB-Zugang haben) |
| Symlinks / Races | Temp-Datei folgte Symlinks; Zielordner ungeprüft | `O_EXCL|O_NOFOLLOW`, Zielordner muss im Speicher liegen, gespeicherter Pfad darf kein Symlink sein |
| Atomare Dateioperationen | rename im selben Ordner, Datei-Löschung nur, wenn keine Runde darauf zeigt | unverändert gut; abgebrochener Prozess getestet |
| Backup/Restore | konsistent, Manifest, Restore mit Prüfsummen (V0.9.8) | zusätzlich Größe, MAC und Audit-Kette bei der Restore-Prüfung; Kettenkopf im Manifest |
| Zugriff auf Uploads/Exporte | Telemetrie-Dateien werden nie roh ausgeliefert; Export nur Metadaten | Export ergänzt um Prüfsummen; zurückgestellte Runden nur für Eigentümer/Admin sichtbar |
| Löschen/Austauschen/Überschreiben | ohne Nachweis möglich | jede Änderung nach dem Speichern wird erkannt (Hash/MAC) und führt zur Quarantäne |

## 7. Wie das Ergebnis eines Uploads unterschieden wird

| Art | Antwort | Beispiel |
|---|---|---|
| technischer Fehler | 5xx / 503 mit Code (`MAINTENANCE`, `STORAGE_UNAVAILABLE`), Request-ID | Backup läuft, Volume falsch eingebunden |
| abgelehnte Anfrage | 4xx (400/401/413/426), unverändert | Signatur falsch, Datei zu groß, Client zu alt |
| ungültige Datei | 200, Runde gespeichert als INVALID mit Grund (feste Präfixe, `validation.py`) | nicht lesbar, Zeit läuft rückwärts, abgeschnitten, Dekodier-Grenzen |
| verdächtiger Datensatz | 200, Runde gespeichert, REVIEW / `held`, Befund-Codes | fehlende Signale, Sprung, fremde Datei |

Die Antwort-Struktur des Uploads bleibt `{status, lap_id, duplicate}` (Client 0.9.4 unverändert).
