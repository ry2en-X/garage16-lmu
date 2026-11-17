# Open Source und Code-Wiederverwendung — V0.9.3

Prüfung nach dem Zusatzauftrag „Open-Source-Recherche, wiederverwendbare Codebausteine und
Lizenzkonformität“. Alle Repositories wurden am 10.10.2026 tatsächlich geklont und ihre
Lizenzdatei gelesen; Stand jeweils als Commit angegeben.

**Ergebnis in einem Satz:** Es wurde **kein fremder Code übernommen und keine Abhängigkeit
hinzugefügt**. Übernommen wurden nur dokumentierte **Fakten** über LMUs Schnittstelle (aus einem
MIT-Projekt, siehe `THIRD_PARTY_NOTICES.md`). Alle Algorithmen sind eigene Implementierungen
allgemein bekannter Verfahren und durch Garage16-eigene Tests abgesichert.

## Untersuchte Projekte

| Projekt | Commit | Lizenz (gelesen) | Ergebnis |
|---|---|---|---|
| [uPlot](https://github.com/leeoniya/uPlot) | `a116ae6` | MIT, © 2022 Leon Sorokin | **nicht übernommen** — siehe unten |
| [Chart.js](https://github.com/chartjs/Chart.js) | `7169e65` | MIT, © 2014-2024 Chart.js Contributors | **nicht übernommen** |
| [SortableJS](https://github.com/SortableJS/Sortable) | `48b626b` | MIT, © 2019 Sortable contributors | **nicht übernommen** |
| [Apache Arrow](https://github.com/apache/arrow) | — | Apache-2.0 | **bereits im Einsatz** (pyarrow, seit V0.9.0); nichts Neues nötig |
| [pyLMUSharedMemory](https://github.com/s-victor/pyLMUSharedMemory) | `6cef58e` | MIT, © 2021 Tony Whitley, © 2025 Xiang | **Fakten übernommen** (Feldnamen/-typen, Enums), kein Code |
| [TinyPedal](https://github.com/s-victor/TinyPedal) | aktueller Stand | **GPL-3.0** | nur gelesen, um zu sehen, *welche* LMU-Felder es für Rad-Schlupf nutzt; kein Code übernommen (Copyleft ohne Kompatibilitätsprüfung ausgeschlossen) |
| [oasis4/LMU-telemetry](https://github.com/oasis4/LMU-telemetry) | `2c81e36` | **nur nichtkommerziell** („Commercial use … strictly prohibited“) | **ausgeschlossen** — nichts übernommen. Die öffentlichen Test-Fixtures (LMUs eigene `.duckdb`-Aufzeichnungen) wurden nur gelesen, um eine Annahme zu prüfen (s. u.) |
| [cfcata03/LMU-Dash](https://github.com/cfcata03/LMU-Dash) | `095d194` | MIT, © 2026 Alexandru Calin | nur gesichtet (nutzt dieselben rF2-Felder `mPathLateral`, `mTrackEdge`, `mCountLapFlag`, `mVehFilename`); kein Baustein, der besser wäre als der vorhandene |
| [alelosbrigia/LMU-telemetry](https://github.com/alelosbrigia/LMU-telemetry) | `fcbb605` | **nur nichtkommerziell** | **ausgeschlossen** — nichts übernommen |

## Entscheidungen im Einzelnen

**uPlot (Zeitreihen).** Gute Bibliothek, MIT. Garage16 hat seit V0.9.0 eine eigene Canvas-Engine
(`web/js/analysis/charts.js`) mit genau den Fähigkeiten, die uPlot bieten würde: geteilter Cursor
über alle Diagramme, Zoom/Pan, Touch-Gesten, Server-seitiges Downsampling plus Spike-Erhalt
(V0.9.1). Ein Wechsel wäre ein Neubau aller Fenster und brächte ~29 KB (gzip) mehr, ohne
messbaren Vorteil. Verworfen: *bestehende Garage16-Implementierung ist geeigneter.*

**Chart.js (Histogramme).** Für die vier Dämpfer-Histogramme und das Histogramm-Fenster reicht
ein Balkendiagramm; das ist in `web/js/analysis/distribution.js` in ~80 Zeilen Canvas-Code
umgesetzt und getestet, im selben Stil und mit denselben Farben wie die übrigen Fenster.
Chart.js ist eine allgemeine Diagrammbibliothek mit eigenem Rendering-, Konfigurations- und
Plugin-System; als zweite Diagramm-Engine neben der vorhandenen brächte sie Ladezeit und
Wartung für eine Darstellung, die wir nicht brauchen (die Bundle-Größe wurde nicht gemessen,
weil das Repository keinen fertigen Build enthält). Verworfen: *Abhängigkeit ohne messbaren
Nutzen.*

**SortableJS (Layout-Editor).** Der Editor (V0.9.1) verschiebt Fenster bereits per Pointer-Events
(Maus, Stift, Touch) am Griff, mit Tastatur (↑/↓) und Knöpfen, und ist durch DOM-Tests
abgesichert. SortableJS (45 KB minifiziert) würde dieselbe Funktion ersetzen. Verworfen:
*Abhängigkeit ohne klaren Nutzen.*

**Apache Arrow.** Die Pipeline nutzt Parquet + Zstandard über pyarrow bereits. V0.9.3 nutzt das
weiter: Der Upload liest für alle Prüfungen nur die ~60 benötigten Spalten der ~190 in einem
Durchgang (`server/validation.py` `validate_and_read`). Keine neue Speicherarchitektur.

## Verfahren — eigene Implementierungen allgemein bekannter Methoden

| Verfahren | Wo | Woher die Methode | Test |
|---|---|---|---|
| Dämpfergeschwindigkeit = zeitliche Ableitung des Federwegs (zentrale Differenzen, 3-Sample-Glättung) | `server/telemetry_channels.py` `_damper_velocity` | Definition | `test_v093_insights.py` (Korrelation mit der analytischen Ableitung > 0,9, beide Vorzeichen) |
| Vorzeichen „Druckstufe = +“ aus dem Bremsnicken (vorne minus hinten wächst beim Bremsen) | `calibrate_orientation` | Fahrphysik (Lastverlagerung), wie die übrigen Selbstkalibrierungen seit V0.9.0 | ebd. |
| Dämpfer-Histogramm mit Low/High-Speed-Grenze (25 mm/s) und Druck-/Zugstufe, zeitgewichtet | `telemetry_analysis.distribution` | übliche Darstellung der Dämpferanalyse | Summe = 100 %, Anteile konsistent |
| Längsschlupf = (ω·r − v)/v, Rollradius pro Runde als Median von v/ω im freien Rollen | `_slip_ratio` | Lehrbuch-Definition | blockiertes Rad wird an der gesetzten Stelle mit −50 % gefunden |
| Ereignis-Erkennung über Läufe mit Mindestdauer und Zusammenführen kurzer Lücken | `track_limits._runs/_merge`, `_event_runs` | Standard-Signalverarbeitung | Einheitstests aller Regeln |
| Robuste Konstanz: Median, Ausreißer > 107 % des Medians, Standardabweichung, MAD, lineare Regression für den Trend | `lap_insights.consistency` | Statistik; „107 %“ ist die bekannte Motorsport-Grenze | `test_consistency_uses_clean_laps…` |
| Theoretische Bestzeit aus festen 50-m-Abschnitten der Spiel-Rundendistanz; letzter Abschnitt mit der offiziellen Rundenzeit geschlossen | `lap_insights.time_profile/theoretical_best` | Mini-Sektor-Prinzip | Summe der Abschnitte = Rundenzeit exakt; Ideal = Summe echter Bestabschnitte |

## Geprüfte Annahme mit fremden Daten (nur gelesen)

Frage: Lässt sich das Fahrzeugmodell aus dem Fahrzeugnamen (`vehicle_name`) ableiten? Die
öffentlichen Test-Fixtures von oasis4/LMU-telemetry sind LMUs **eigene** Telemetrie-Aufzeichnungen.
Ihre Metadaten zeigen Namen wie „Team WRT 2025 #46:LM“ oder „Proton Competition #99:AL“ — **ohne
Modell**, mit Team, Saison, Nummer und einem Event-Kürzel. Daraus folgt: Der Name taugt nicht;
Garage16 nutzt LMUs eigenes Modellfeld (`mVehicleModel`) und lernt Aliase aus dem Spiel. Aus
diesem Projekt wurde nichts übernommen; die Beobachtung steht hier nur als Begründung.

Das Kürzel (`:LM`, `:WEC`, `:ELMS25`) kann auf eine Event-Spezifikation hinweisen. Ob LMU solche
Varianten im Modellnamen unterscheidet, zeigt `scripts/car_identity_probe.py` mit echten Daten.

## Offene Lizenzfragen

Keine. Garage16 enthält weiterhin nur eigenen Code plus die bereits in V0.9.2 dokumentierten
Pakete der Analyse-App-Edition.

## Nachtrag V0.9.3.1

Für das Korrekturrelease wurde **kein Code und keine Bibliothek übernommen**. Gelesen (nicht
kopiert) wurden:

| Quelle | Lizenz | Was geprüft wurde | Ergebnis |
|---|---|---|---|
| LMU-Guide „Understanding track limits“ | öffentliche Herstellerdokumentation | wie LMU Track-Limits erkennt, bewertet und Runden ungültig macht | Grundlage für die Stufen LMU-Urteil / Regel / Messung und das 6-s-Nachlauffenster |
| [TinyPedal](https://github.com/s-victor/TinyPedal), `tinypedal/adapter/lmu_reader.py`, Revision 4e2de40 | **GPL-3.0** | Umrechnung Schritte → Punkte, Lesart von `mLastLapTime`, Korrektur gekürzter Modellnamen | nur Fakten verwendet (Formel, Konvention, drei Fahrzeugnamen), kein Code; alle drei als „unbestätigt“ markiert |

Ein eigener Track-Limit-Probe (`scripts/track_limits_probe.py`) wurde neu geschrieben, statt ein
vorhandenes Werkzeug einzubinden: Er braucht nur den Shared-Memory-Leser, den Garage16 schon hat.

