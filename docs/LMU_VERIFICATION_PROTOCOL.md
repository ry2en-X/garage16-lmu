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

## (h) V0.9 — Fahrhilfen (TC, ABS, Motor-Map, Brake Migration) lesbar?

**Frage:** Liefert LMU die Fahrhilfen-Werte wirklich im Shared Memory (ab LMU 1.3),
und ändern sie sich, wenn du sie im Auto verstellst? (Quellen widersprechen sich:
zwei Projekte lesen sie, ein drittes meldet, die Live-Werte seien nicht lesbar.)

**Session:** Ein Auto mit einstellbarem TC und ABS fahren. Während einer Runde TC
um 2–3 Stufen verstellen, ABS einmal, Brake Migration einmal. Danach die Runde in
**Analysis** öffnen und die Kanäle „Traction control", „ABS", „Brake migration"
einblenden (Kanalauswahl).

**Erwartung:** Die Linien springen genau dort, wo du verstellt hast; `TC intervening`
/ `ABS intervening` schlagen beim Beschleunigen bzw. harten Bremsen aus.

**Diagnose:** Alle Fahrhilfen-Kanäle bleiben dauerhaft 0 → das Feld-Layout stimmt für
deine LMU-Version nicht (`tests/test_lmu_structs.py` zeigt, welche Offsets erwartet
werden) oder LMU liefert sie nicht; mit `scripts/dump_shared_memory.py` die rohen
Bytes ab Offset 745 der Telemetrie-Struktur ansehen. Nicht raten — erst das Ergebnis
hier eintragen.

---

## (i) V0.9 — Vorzeichen: Lenkung, Quer-G, Giergeschwindigkeit, Schlupfwinkel

**Frage:** Sind „Rechtskurve = positiv" für Lenkung, Quer-G, Gierrate und Schlupfwinkel
richtig? Das System entscheidet das pro Runde selbst aus den Reifenlasten; hier wird
geprüft, dass es das richtig tut.

**Session:** Eine Runde mit klar erkennbaren Rechts- und Linkskurven fahren (z. B.
Road Atlanta). In **Analysis** Lenkung, Quer-G, Gierrate und „Slip angle FL" ansehen.

**Erwartung:** In Rechtskurven sind alle vier Linien positiv, in Linkskurven negativ.
Die Antwort des Servers (`orientation.a.decided_from_data`) hat für `steering`,
`lat_g`, `long_g`, `yaw`, `slip` jeweils `true`.

**Diagnose:** Ein Wert `false` bedeutet „zu wenig Signal, Standardrichtung benutzt" —
dann die Runde mit mehr Kurven wiederholen. Ein `true` mit falschem Vorzeichen in der
Grafik wäre ein echter Fehler: Runde und Antwort (`/telemetry/analysis/compare`) sichern.

---

## (j) V0.9 — Streckenkarte nicht gespiegelt

**Frage:** Stimmt die Streckenkarte mit der echten Strecke überein (nicht gespiegelt)?

**Session:** Eine Runde auf einer Strecke fahren, deren Form du kennst.

**Erwartung:** Die Karte (Analysis oder Circuits) zeigt die Strecke wie in der
Streckenkarte des Spiels bzw. wie du sie kennst; Rechtskurven laufen im Uhrzeigersinn.
Notiere den Wert `map.mirrored` aus der Server-Antwort (es ist `true`, wenn LMUs
Welt-Koordinaten gegenüber der Standardannahme gespiegelt sind — das ist dann *kein*
Fehler, die Seite korrigiert es).

**Diagnose:** Karte erkennbar gespiegelt UND `map.a_decided` ist `false` → die
Runde hatte zu wenig Kurven für die automatische Erkennung; mit einer anderen Runde
wiederholen.

---

## (k) V0.9 — Einheiten und Radreihenfolge

**Frage:** Stimmen Einheiten und die Reihenfolge der Räder (VL, VR, HL, HR)?

**Session:** Nach einigen schnellen Runden: in **Analysis** „Brake disc temperature",
„Tyre temperature", „Tyre pressure", „Suspension travel", „Tyre load" für alle vier Räder
einblenden.

**Erwartung:** Bremsscheiben grob 200–900 °C (vorn meist heißer als hinten), Reifen
60–110 °C, Reifendruck um 130–220 kPa, Federweg im Bereich einiger Zentimeter (mm).
In einer **Linkskurve** sind die Lasten der **rechten** Räder (FR, RR) höher, in einer
Rechtskurve die der linken (FL, RL).

**Diagnose:** Bremsscheiben um 20–60 °C → LMU meldet jetzt Celsius statt Kelvin (die
Anzeige erkennt das, bitte trotzdem hier vermerken). Lastverteilung seitenverkehrt →
Radreihenfolge im Struct prüfen, **bevor** irgendetwas anderes geändert wird.

---

## (l) V0.9 — Kein Loch am Start/Ziel, Dateigröße

**Frage:** Geht durch das Speichern im Hintergrund nichts verloren, und wie groß wird
eine Runde wirklich?

**Session:** 10 Runden am Stück fahren (ohne Pause).

**Erwartung:** In den Metadaten (`*.json` neben jeder Runde im Client-Datenordner) ist
`sample_count` für alle Runden nahe `lap_time × 60`; in **Analysis** gibt es am
Start/Ziel keinen Sprung oder Aussetzer in den Linien. Notiere die Dateigröße der
`*.parquet`-Dateien (Soll: grob 4–10 MB bei 3–4 Minuten Rundenzeit) und die Größe auf
dem Server (`du -sh /data/telemetry`).

**Diagnose:** `sample_count` deutlich kleiner, vor allem bei der ersten Runde nach einer
anderen → Abtastlücke; Debug-Log (`LMU_GARAGE_DEBUG=1`) mitschneiden. Dateien viel größer
als erwartet → die Zahl hier eintragen, dann entscheiden, ob die Zusatzkanäle seltener
(z. B. jedes zweite Sample) aufgezeichnet werden sollen.

---

## (m) V0.9.3 — Track-Limits: was LMU selbst meldet

**Frage:** Erkennt der Server eine Runde mit Track-Limit-Verstoß zuverlässig — über LMUs
eigene Signale (`lap_invalidated`, Track-Limit-Punkte, `count_lap_flag`) statt nur zufällig?

**Session (Client 0.9.3, Server 0.9.3):**
1. Eine saubere Runde.
2. Eine Runde, in der du an **einer** bekannten Stelle mit allen vier Rädern über die weiße
   Linie fährst (dort, wo LMU üblicherweise eine Warnung gibt).
3. Eine Runde, in der du eine Schikane deutlich abkürzt.

**Erwartung:** In **Analyse → Track limits & events** (bzw. „Track-Limits & Ereignisse“):
Runde 1 „Track limits respected“; Runde 2 und 3 „invalid“ mit mindestens einem Befund
*LMU invalidated the lap* oder *Track-limits points from LMU* — und der Klick auf den Befund
setzt den Cursor an die richtige Stelle der Strecke. Unter **Dashboard** zeigt der Tooltip
von „ungültig“ den Grund.

**Diagnose:** Runde 2/3 gültig → im Event-Fenster nachsehen, welche Signale die Runde hat
(`signals` in `/telemetry/analysis/events`); fehlt `lap_invalidated`/`track_limits_steps`,
stimmt etwas mit dem Struct-Offset nicht → `dump_shared_memory.py` mitlaufen lassen. Eine
saubere Runde ungültig → den Befund notieren (welcher Code, welche Stelle).

---

## (n) V0.9.3 — Untergrund pro Rad

**Frage:** Liefert `surface_type` pro Rad, was dokumentiert ist (0 trocken, 1 nass, 2 Gras,
3 Erde, 4 Kies, 5 Randstein)?

**Session:** In einer Runde bewusst über einen Randstein fahren und an einer harmlosen Stelle
mit allen vier Rädern ins Gras.

**Erwartung:** Kanäle „Wheels on a kerb“ und „Wheels off track“ (Gruppe *Track limits*) zeigen
an genau diesen Stellen 1–4; die Runde mit Gras ist „invalid“ mit *All four wheels off the
track*. Randsteine allein machen keine Runde ungültig.

**Diagnose:** Kanäle immer 0 oder ständig ≥ 2 → der Server meldet *Signal ignored as
implausible: surface_type* und ignoriert das Signal (keine falschen Streichungen); bitte
melden, dann wird der Offset geprüft.

---

## (o) V0.9.3 — Abstand zum Streckenrand

**Frage:** Passen `path_lateral` und `track_edge` zur Strecke?

**Erwartung:** Kanal „Distance to the track edge“ ist auf der Strecke positiv (einige Meter),
neben der Strecke negativ; „Track edge“ selbst liegt grob bei 5–8 m. Ein Ausflug von
mehreren Metern über den Rand landet als *Beyond the track edge* in der **Track-Limit-Prüfung**
im Admin-Bereich (nicht automatisch ungültig).

**Diagnose:** Werte unplausibel (Rand < 2 m oder Hunderte Meter) → der Server ignoriert das
Signal; notieren.

---

## (p) V0.9.3 — Fahrzeugmodell und Fahrerfeld

**Frage:** Liefert LMU in `mVehicleModel` den Modellnamen, und unterscheidet er Varianten
(Evo, Le-Mans-Ausführung, neues Baujahr mit anderer Aerodynamik)?

**Session:** Eine Session mit vollem KI-Feld laden, dann im Projektordner auf dem PC:
`py -m scripts.car_identity_probe --csv autos.csv`

**Erwartung:** Jede Zeile hat einen Modellnamen; alle Lackierungen/Nummern eines Modells
ergeben dieselbe „Garage16 identity“; Varianten mit anderer Technik ergeben eine andere.
`autos.csv` an den Entwickler schicken.

**Diagnose:** Modellname leer → der Server nutzt den Dateinamen (Modell + Saison) und lernt,
sobald ein Client mit Modellnamen hochlädt. Zwei technisch verschiedene Varianten mit gleichem
Modellnamen → dafür ist `SPEC_RULES` in `server/car_identity.py` da (nur mit diesem Beleg).

---

## (q) V0.9.3 — Dämpfer und Radschlupf

**Frage:** Sind Dämpfergeschwindigkeit und Schlupf plausibel?

**Session:** Eine Runde mit hartem Anbremsen; einmal (ABS aus oder niedrig) absichtlich ein
Vorderrad blockieren.

**Erwartung:** Fenster **Damper histograms**: beide Seiten (Druck- und Zugstufe) belegt, der
größte Teil innerhalb ±100 mm/s, Hinterachse und Vorderachse unterscheiden sich. Kanal „Wheel
slip FL“ auf Geraden nahe 0 %, beim Blockieren deutlich negativ; **Track limits & events**
listet *FL locks under braking* an der Stelle.

**Diagnose:** Druck- und Zugstufe vertauscht → `orientation.susp` in der Antwort von
`/telemetry/analysis/compare` notieren (der Server entscheidet das Vorzeichen aus dem
Bremsnicken). Schlupf dauerhaft weit weg von 0 → Radius-Schätzung prüfen.

---

## V0.9.3.1 — was die Track-Limit-Prüfung noch mit echten Daten braucht

**Hintergrund.** Ein externes Code-Audit und eine Recherche (LMU-Guide „Understanding track
limits“, LMU-Wiki „Rules“, das quelloffene Werkzeug TinyPedal) haben ergeben:
- LMU erkennt Track-Limits an der **durchschnittlichen Streckenbreite** („zwei Räder innerhalb
  der weißen Linien oder vier Räder auf den Kerbs“), nicht am Untergrund.
- Ein Verstoß wird erst **2–5 s nach dem Wiedereinfädeln** bewertet, die Punkte richten sich nach
  dem **Zeitgewinn**. Außerhalb von Rennen macht ein Verstoß, der Verwarnungspunkte bringt, die
  Runde ungültig; nach dem letzten großen Bremspunkt auch die nächste.
- TinyPedal rechnet Punkte = `mTrackLimitsSteps / mTrackLimitsStepsPerPoint` — ein Schritt ist
  also nur ein Bruchteil eines Punkts. TinyPedal liest außerdem `mLastLapTime <= 0` als
  „letzte Runde ungültig“ und korrigiert drei auf 29 Zeichen gekürzte Modellnamen.
Das ist **plausibel, aber nicht offiziell dokumentiert**. V0.9.3.1 behandelt deshalb alles, was
nicht LMUs eigenes Flag ist, als Messung (nur Prüfung). Die folgenden Punkte entscheiden, welche
Messung später Runden streichen darf (`LMU_GARAGE_TRACK_LIMITS_INVALIDATING`).

Werkzeug für (r)–(u), auf dem PC im Projektordner, während du fährst:
`py -m scripts.track_limits_probe --csv tracklimits.csv` — schreibt jedes Frame und gibt jede
Änderung eines LMU-Signals mit „x s nach der Ziellinie“ aus. Notiere parallel, was du getan hast.

## (r) V0.9.3.1 — Schritte, Punkte und Rundenungültigkeit

**Frage:** Wie viele Schritte bringt ein Verstoß, und wann setzt LMU dabei `mLapInvalidated`?

**Session:** Training, Track-Limit-Profil **Default**. Pro Runde genau eine Sache:
1. saubere Runde; 2. zwei Räder knapp über der Linie ohne Zeitgewinn; 3. Schikane deutlich
abkürzen (Zeitgewinn); 4. Ausflug ins Kiesbett mit Zeitverlust; 5. in der **letzten Kurve vor
Start/Ziel** abkürzen. Dasselbe einmal im **Rennen** und einmal mit Profil **Strict**.

**Erwartung / was wir lesen:** `track_limits_steps` steigt bei 2.–5. unterschiedlich stark;
`steps_per_point` ist konstant; `lap_invalidated` springt nur, wenn LMU die Runde auf dem
Bildschirm als ungültig zeigt. Bei 5. kommt die Änderung **nach** der Ziellinie (Ausgabe „+x s
after the line“) — das bestätigt das Nachlauffenster (6 s) des Clients.

**Diagnose:** Steigt `lap_invalidated` bei jeder Schrittzunahme mit → Schritte brauchen keine
eigene Regel. Steigen Schritte ohne Ungültigkeit → die Prüfung bleibt richtig bei „nur Prüfung“.

## (s) V0.9.3.1 — `mLastLapTime` bei ungültigen Runden

**Frage:** Schreibt LMU für eine ungültige Runde `mLastLapTime <= 0`, und wann — sofort an der
Linie oder erst Sekunden später?

**Session:** dieselben Runden wie (r). In der Ausgabe auf `last_lap_time a -> b` achten.

**Diagnose:** Kommt `<= 0` erst nach der Linie → der Client-Befund
`game_last_lap_time_withdrawn` trifft genau das; dann kann er auf „ungültig“ hochgestuft werden.
Bleibt die Zeit positiv, obwohl LMU die Runde als ungültig zeigt → die TinyPedal-Annahme gilt
für diese LMU-Version nicht.

## (t) V0.9.3.1 — gekürzte Modellnamen und Varianten

**Frage:** Welche Modellnamen kürzt LMU (30-Byte-Feld), und unterscheiden sich Varianten (z. B.
Peugeot 9X8 2023/2024, Evo-Versionen, GTE/LMGT3) im Namen?

**Session:** Session mit vollem KI-Feld aller Klassen: `py -m scripts.car_identity_probe --csv autos.csv`.

**Erwartung:** Spalte `name_cut` ist nur bei langen Namen `True`; der Abschnitt „Please check“
listet, was von Hand zu klären ist. Mit dieser CSV wird `MODEL_NAME_FIXES` (Kürzungen) bzw.
`SPEC_RULES` (Varianten gleichen Namens) in `server/car_identity.py` ergänzt — nur mit Beleg.

## (u) V0.9.3.1 — Untergrund: Kerbs, Astroturf, Auslaufzonen, Nässe, Rad in der Luft

**Frage:** Was meldet `mSurfaceType` (0 trocken, 1 nass, 2 Gras, 3 Erde, 4 Kies, 5 Kerb,
6 Spezial) auf Kerbs, Astroturf, asphaltierten Auslaufzonen, im Regen und wenn ein Rad über
einen hohen Kerb in der Luft ist?

**Session:** Eine Strecke mit Astroturf/asphaltierten Auslaufzonen; je eine Fahrt über einen
flachen und einen hohen Kerb; eine Runde im Regen.

**Diagnose:** Asphalt-Auslauf = 0 → „vier Räder neben der Strecke“ sieht solche Abkürzungen
nicht (bleibt Messung). Regen ohne Wert 1 → die Einteilung trocken/nass der theoretischen
Bestzeit kann Regenrunden nicht erkennen (dann wird sie auf ein anderes Signal umgestellt).

## (v) V0.9.3.1 — Nachlauffenster im Alltag

**Frage:** Kommt jede Runde trotz 6 s Nachlauf an — auch wenn du direkt nach der Ziellinie in
die Box fährst, ins Menü gehst oder LMU beendest?

**Erwartung:** Im Client-Log erscheint jede Runde (spätestens 30 s nach der Linie, beim Beenden
sofort). Wurde das Fenster abgebrochen, steht in der Datenbank bei der Runde
`check_inputs.late.complete = false` (nur zur Diagnose; die Runde wird normal bewertet).

## (w) V0.9.4 — Stint-Daten: Boxenstopps, Reifenmischung, Abstände, Flaggen

**Hintergrund.** Der Stint Analyzer liest pro Runde, was LMU am Rundenende meldet
(`client/telemetry/parser.py` `lap_context`), und pro Sample die Boxengasse, die Position, die
Flagge und die Zeitabstände (`client/telemetry/channels.py` `STINT_CHANNELS`). Namen, Typen und
Reihenfolge der Felder stammen aus LMUs `SharedMemoryInterface`-Header (wie bei pyLMUSharedMemory,
MIT, nur das Layout übernommen). Was die Werte **im Alltag bedeuten**, ist nicht offiziell
dokumentiert. Bis zur Bestätigung gilt: Stints werden in erster Linie an **gemessener**
Boxengasse + Stillstand, gemessenem Nachtanken (Tankinhalt steigt) und gemessenem
Reifenwechsel (Profil steigt) geteilt; der Boxenstopp-Zähler ist nur ein zusätzlicher Grund.

Werkzeug: `py -m scripts.track_limits_probe --csv stint.csv` (zeigt jetzt auch
`num_pitstops`, `place`, `flag`, `compound_front/rear` als „Nummer:Name“, die Zeitabstände,
Tankinhalt und -größe, den größten Schaden und die Restzeit der Session).

**Fragen und Erwartung:**
1. **`mNumPitstops`:** Steigt beim Einfahren in die Box, beim Stillstand oder beim Verlassen?
   Zählt eine Durchfahrtsstrafe mit? *Erwartung:* steigt einmal pro Stopp.
2. **Reifenmischung:** Welche `mFrontTireCompoundIndex`/`…Name` gehören zu welcher Mischung im
   Boxenmenü — und ändern sie sich beim Wechsel sofort oder erst beim Losfahren?
   *Erwartung:* Name wie im Menü (z. B. „Medium“), Wechsel spätestens beim Verlassen der Box.
3. **Zeitabstände** `mTimeGapCarAhead` / `mTimeGapCarBehind`: Abstand in Sekunden zum Auto
   davor/dahinter **auf der Strecke** (auch ein überrundetes Auto)? Was steht im Training ohne
   Auto in der Nähe (0, sehr groß, negativ)? *Erwartung:* Sekunden; 0 oder negativ = kein Auto
   (der Server wertet nur Werte > 0).
4. **`mFlag`:** Nur 0 (grün) und 6 (blau), wie der Header sagt?
5. **`mDentSeverity`:** 0/1/2 pro Zone; steigt nach einem Kontakt und sinkt nach der Reparatur?
6. **`mSessionTimeRemaining`** im Rennen und `mMaxLaps` bei Zeitrennen (0?).

**Vorgehen:** Ein kurzes Rennen gegen KI mit einem Boxenstopp (tanken + Reifen wechseln, Mischung
ändern), eine Runde im Windschatten eines KI-Autos, einmal überrunden lassen. Notiere die Uhrzeit
von Box-Einfahrt, Stillstand und Ausfahrt. Danach im Stint Analyzer prüfen: Stint-Grenze an der
richtigen Runde, „getankt“ und „neue Reifen“ wie gefahren, Verkehrsrunde markiert.

---

## Nach Abschluss aller Punkte

Trage die Ergebnisse hier oder in einem Issue nach: für jeden Punkt (a)–(w)
„bestätigt wie erwartet" / „Abweichung gefunden: ..." mit einem Verweis auf
die zugehörige CSV/Logdatei. Nur bestätigte Abweichungen rechtfertigen
Codeänderungen — nicht auf Verdacht ändern.
