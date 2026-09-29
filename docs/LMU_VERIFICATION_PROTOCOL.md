# LMU Shared-Memory-Verifikationsprotokoll

Diese Punkte können nicht ohne echtes LMU auf Windows verifiziert werden.
Für jeden: eine konkrete Session fahren, `scripts/dump_shared_memory.py`
mitlaufen lassen, und `LMU_GARAGE_DEBUG=1` setzen, wenn der echte Client
(`client/main.py`) läuft, um die Debug-Logzeilen aus `parser.py` zu sehen.

Werkzeug: `python -m scripts.dump_shared_memory --out session.csv`
schreibt jeden Frame als CSV-Zeile und druckt Anomalien (Lap-Sprünge)
sofort auf die Konsole.

---

## (a) Scoring-Sync: Rundenende und Werte aus verschiedenen Puffern

**Frage:** Hinkt `scoring.last_lap_time` dem `telemetry.lap_number`-Inkrement
hinterher? Wenn ja, um wie viele Frames/ms?

**Session:** 5 saubere Runden auf einer bekannten Strecke fahren, Rundenzeit
parallel mit einer Stoppuhr/In-Game-Anzeige vergleichen.

**Erwartung:** `scoring.last_lap_time` zum Zeitpunkt des `lap_number`-Sprungs
sollte der tatsächlichen Rundenzeit entsprechen (±50ms).

**Diagnose:**
- Mit `LMU_GARAGE_DEBUG=1`: Suche nach `EMIT:` Zeilen. Das Feld `diff=`
  zeigt die Differenz zwischen `scoring.last_lap_time` und der aus der
  Telemetrie-Pufferdauer berechneten Zeit. Sollte nahe 0 sein.
- Falls `diff` konstant um eine Rundenzeit versetzt ist (z.B. Runde N zeigt
  Zeit von Runde N-1): Scoring-Puffer ist dem Telemetrie-Puffer einen Frame
  hinterher. **Fix dann:** Rundenabschluss verzögern (einen Frame warten,
  bevor der Buffer emittiert wird) oder auf ein Bestätigungssignal aus dem
  Scoring-Puffer warten statt auf `telemetry.lap_number` allein.

**In CSV zu prüfen:** Spalten `lap_number`, `last_lap_time`, `elapsed_time`
um die Zeile herum, wo `lap_number` sich ändert.

---

## (b) Lap-Sprünge — bereits gefixt in V0.5.3, hier nur bestätigen

**Frage:** Treten reale Lap-Sprünge (Session-Restart, Return-to-Garage,
Replay) tatsächlich als `lap_number`-Sprung >1 oder rückwärts auf, wie der
V0.5.3-Fix annimmt?

**Session:** Eine Runde fahren, dann zur Box zurückkehren und Session neu
starten (P→Q→R Wechsel), dann weiterfahren.

**Erwartung:** Konsole zeigt `!! JUMP !!` beim Sessionwechsel; `parser.py`
sollte laut Log `Lap number jumped ... discarding buffer` ausgeben, NICHT
eine falsche Lap emittieren.

**Diagnose:** Grep im Client-Log nach `Lap number jumped`. Kommt es vor,
ohne dass eine falsche Lap gespeichert wurde → (b) bestätigt korrekt.

---

## (c) `count_lap_flag`-Semantik

**Frage:** Ist `count_lap_flag`: `0` = nicht zählen, `1` = zählen (aktuelle
Annahme in `parser.py`)? Oder folgt LMU der rF2-Konvention (`0`=weder Runde
noch Zeit, `1`=Runde ohne Zeit, `2`=beides zählt)?

**Session:**
1. Eine normale, gültige Runde fahren.
2. Eine Runde mit Track-Limits-Verstoß (frühzeitiges Verlassen der Strecke).
3. Eine Out-Lap direkt nach Boxenausfahrt.

**Erwartung:** Notiere `count_lap_flag` für jede der drei Situationen in
der CSV (Spalte `count_lap_flag`, Zeile beim Rundenende).

**Diagnose:** Falls Fall 2 (Track-Limits) einen anderen Wert als `0` zeigt,
obwohl die Runde ungültig sein sollte → `_derive_is_valid()` in `parser.py`
muss angepasst werden (aktuell: nur `== 0` wird abgelehnt).

---

## (d) `mmap`-Verhalten bei "LMU nicht gestartet"

**Frage:** Wirft `mmap.mmap(-1, size, tagname, ACCESS_READ)` zuverlässig
einen `OSError`, wenn die Shared-Memory-Region noch nicht existiert?

**Session:** `python -m scripts.dump_shared_memory` OHNE laufendes LMU
starten.

**Erwartung:** Konsole zeigt `LMU not running / no active session —
waiting 5s...` in einer Schleife, KEIN Absturz, KEIN stilles "Connected"
ohne echte Daten.

**Diagnose:** Falls "Connected" ohne laufendes LMU erscheint → `_open_map()`
in `reader.py` legt fälschlich eine neue Mapping an. **Fix dann:** Wechsel
auf `OpenFileMappingW` + `MapViewOfFile` via `pywin32` oder `ctypes.windll`,
die nie neu anlegen.

---

## (e) Silent Weather-Drop

**Frage:** Verwirft `validate_scoring_info()` echte Frames wegen
unplausibler Wetterwerte, auch wenn das Wetter nicht das Problem ist?

**Session:** Normale Session bei trockenem UND bei nassem Wetter fahren.

**Erwartung:** Client-GUI bleibt durchgehend auf "Connected", keine Lücken
im Log.

**Diagnose:** Falls das Client-GUI zwischenzeitlich stumm wird (kein neuer
Log-Eintrag, aber auch kein Fehler) → `validate_scoring_info()` verwirft
Frames. **Fix dann:** Wetterfelder bei Unplausibilität auf `None`/0 setzen
statt den ganzen Frame zu verwerfen, einmalig loggen.

---

## (f) Car-Identity / Encoding

**Frage:** Enthält `vehicle_name` Modell, Team, Startnummer oder Livery?
Gehen Sonderzeichen (Akzente) durch `errors="replace"` verloren oder werden
sie korrekt dekodiert?

**Session:** Auf einer Strecke mit Sonderzeichen im Namen fahren (z.B.
"Autódromo José Carlos Pace" falls in LMU vorhanden), und ein Auto mit
Sonderzeichen im Namen wählen falls vorhanden.

**Erwartung:** `track_name`/`vehicle_name` in der CSV zeigen die Zeichen
korrekt oder als `�`-Ersatzzeichen (nicht stillschweigend abgeschnitten).

**Diagnose:** Falls Namen für Leaderboard-Gruppierung zu granular sind
(gleiches Auto, aber unterschiedliche Startnummer = unterschiedlicher
Leaderboard-Eintrag) → auf `veh_filename` oder `vehicle_class` als
Gruppierungsschlüssel wechseln statt `vehicle_name`.

---

## (g) Staleness-Erkennung

**Frage:** Wächst der Sample-Puffer unkontrolliert, wenn das Spiel
pausiert/im Menü ist?

**Session:** Während einer laufenden Session pausieren (ESC-Menü) oder
zum Hauptmenü zurückkehren, 2 Minuten warten, dann fortsetzen.

**Erwartung:** Mit `LMU_GARAGE_DEBUG=1`: `STALE:`-Zeilen im Log während
der Pause.

**Diagnose:** Falls `STALE`-Zeilen erscheinen UND der Buffer trotzdem
weiterwächst (viele identische Samples) → Frames mit unverändertem
`elapsed_time` sollten verworfen werden, nicht in den Buffer aufgenommen.
Aktuell (V0.6.0) ist das nur geloggt, nicht behoben — das ist der
nächste Schritt, sobald diese Diagnose bestätigt, dass es real vorkommt.

---

## Nach Abschluss aller Punkte

Trage die Ergebnisse hier oder in einem Issue nach: für jeden Punkt (a)–(g)
„bestätigt wie erwartet" / „Abweichung gefunden: ..." mit einem Verweis auf
die zugehörige CSV/Logdatei. Nur bestätigte Abweichungen rechtfertigen
Codeänderungen — nicht auf Verdacht ändern.
