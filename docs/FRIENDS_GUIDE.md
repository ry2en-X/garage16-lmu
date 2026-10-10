# Garage16 — Leitfaden für Fahrer

Kein Python, kein Terminal, keine Konfigurationsdateien. Der Ablauf:

**1. Installieren → 2. Starten → 3. Account verbinden → 4. LMU starten → 5. Fahren**

Wenn etwas nicht klappt: ganz unten steht eine Liste der Meldungen, die
du sehen kannst, und was du dann tust.

## 1. Installieren

Du bekommst von deinem Admin (dem, der Garage16 betreibt) drei Dinge:

- die Setup-Datei (oder einen Download-Link dazu — auf **garage16.de → Download**
  stehen beide nebeneinander, ab 0.9.2; oder auf GitHub unter **Releases → Latest**).
  Du wählst **eine** davon, lädst sie herunter und doppelklickst — ein Terminal
  brauchst du nie:
  - **Client** — `Garage16-Client-Setup.exe`: zeichnet deine Runden auf und lädt sie
    hoch. Die Analyse öffnet sich in deinem Browser.
  - **Client + Analysetool** — `Garage16-Client-Analysis-Setup.exe`: dasselbe, und die
    Analyse öffnet sich in einem **eigenen Fenster** — ganz ohne Browser, aber mit
    denselben Daten wie auf der Website (alles kommt immer vom Server).
  Umentscheiden geht jederzeit: einfach die andere Datei über die vorhandene
  installieren. Deine Anmeldung und deine Runden bleiben.
- die **Web-Adresse** von Garage16: **`https://garage16.de`**
- das **Registration secret** (ein Kennwort, das nur zum Anlegen deines
  Accounts nötig ist — ohne es lässt der Server keine Fremden herein)

Doppelklick auf die Setup-Datei, den Assistenten durchklicken. Du
brauchst dafür **keine Administrator-Rechte**. Wenn du magst, setz die
Haken für eine Desktop-Verknüpfung und "Garage16 automatisch starten,
wenn ich mich bei Windows anmelde".

> **Windows zeigt eine Warnung** ("Der Computer wurde durch Windows
> geschützt" / SmartScreen)? Das ist bei neuen, nicht digital signierten
> Programmen normal. Klick auf **Weitere Informationen → Trotzdem
> ausführen**. Wenn du der Quelle nicht traust, frag vorher deinen Admin.

Voraussetzungen: Windows 10/11 und Le Mans Ultimate.

## 2. Starten

Garage16 aus dem Startmenü (oder vom Desktop) öffnen. Beim allerersten
Start öffnet sich ein Fenster **"Connect Garage16 to your account"**.

## 3. Account verbinden

Zuerst brauchst du einen Account — der wird im **Browser** angelegt:

1. Die Web-Adresse von deinem Admin öffnen.
2. **"Get Garage16"** → im Kasten **"New driver"** deinen Fahrernamen
   eingeben und das **Registration secret** von deinem Admin ins Feld
   "Registration secret" einfügen → **Register**. (Meldet der Server
   "forbidden"/403, war das Secret falsch oder leer.)
3. **Wichtig:** Jetzt werden **Auth token** und **Client secret**
   angezeigt — und **nur dieses eine Mal**. Beide kopieren (sie sind lang;
   markieren und kopieren, nicht abtippen).
4. Danach unter *Account* → *Email & Password* eine E-Mail und ein
   Passwort setzen, damit du dich später normal einloggen und ein
   vergessenes Passwort zurücksetzen kannst. Die Bestätigungs-E-Mail
   musst du nicht sofort anklicken; ohne bestätigte Adresse funktioniert
   "Passwort vergessen" aber nicht zuverlässig.

Dann zurück ins Garage16-Fenster:

- **Server address:** ist meist schon ausgefüllt. Falls nicht: die
  Web-Adresse von deinem Admin.
- **Auth token** und **Client secret:** einfügen.
- **Connect** klicken.

Kann Garage16 den Server nicht erreichen, steht im Fenster "Couldn't reach
that server…". Adresse und Internetverbindung prüfen; ein zweiter Klick
speichert deine Eingaben trotzdem (praktisch, wenn der Server nur gerade
offline ist).

## 4. LMU starten

Le Mans Ultimate normal starten. Reihenfolge egal — Garage16 wartet
einfach, bis LMU läuft. Im Garage16-Fenster siehst du:

- **"Waiting for LMU to start..."** — normal, solange LMU nicht läuft
- **"Connected to LMU shared memory."** und **Connected** — es klappt

Läuft LMU und Garage16 bleibt trotzdem dauerhaft bei "Waiting…", prüf in
den LMU-Einstellungen, ob der Shared-Memory-Output aktiviert ist.

## 5. Fahren

Fahren. Jede abgeschlossene Runde wird automatisch hochgeladen; im
Fenster erscheint sie unter "Last lap" (grün = gültig, rot = ungültig)
und in der Aktivitätsliste. Du musst nichts bedienen — das Fenster kann
im Hintergrund bleiben.

Deine Zeiten siehst du im Browser: **Leaderboard** (Strecke, Klasse,
optional Auto wählen; jeder Fahrername ist anklickbar und öffnet ein
Fahrerprofil), dein **Garage16-Dashboard** (nach dem Einloggen) und die
**Teams**-Seite.

## Teams

- **Beitreten:** Einladungscode von einem Teammitglied → Teams-Seite →
  "Join a team". Oder ein Teammitglied lädt dich über deinen Fahrernamen
  ein — die Einladung erscheint oben auf der Teams-Seite mit
  "Accept"/"Decline".
- **Gründen:** Teams-Seite → "Create a team". Du wirst Owner.
- Im Team-Dashboard siehst du die Team-Bestzeiten, wer sie hält und wie
  weit du selbst davon entfernt bist.

## Discord verknüpfen (optional)

Wenn dein Admin einen Garage16-Discord-Bot betreibt, kannst du dein
Discord-Konto mit deinem Fahrerprofil verbinden — dann kennt der Bot dich
bei Befehlen wie `/pb` und `/teambest`:

1. Im Browser: **Account → Link Discord → "Generate link code"**.
2. Es erscheint ein Befehl wie `/link ABCD-EFGH` (Code gilt **einmal** und
   **10 Minuten**). Auf **Copy command** klicken.
3. In einem Kanal auf deinem Discord-Server, in dem der Garage16-Bot ist,
   den Befehl einfügen und absenden. Die Antwort des Bots sehen nur du.
4. Die Karte auf der Website springt von selbst auf **linked** — kein
   Neuladen nötig.

Den Code brauchst du nur, um **dein eigenes** Discord-Konto zu verbinden.
Nutze nie einen Code, den dir jemand anderes schickt — er würde dein
Discord-Konto mit **dessen** Fahrerprofil verbinden.

**Lösen oder wechseln:** Auf derselben Karte **Unlink Discord** klicken,
oder in Discord `/unlink` eingeben. Danach kannst du mit einem neuen Code
ein anderes Konto verbinden. Ein Code kann nie eine bestehende Verknüpfung
überschreiben, und zu viele falsche Codes sperren dich kurz (dann etwas
warten und einen neuen Code erzeugen).

## Updates und Serverumzug — was du davon merkst

- **Neue Garage16-Version:** Deinen Admin nach der neuen
  `Garage16-Client-Setup.exe` fragen und sie einfach **erneut ausführen**.
  Sie aktualisiert an Ort und Stelle; Anmeldung und aufgezeichnete Runden
  bleiben erhalten.
- **Der Admin zieht auf einen neuen Server um:** Du musst nichts tun.
  Garage16 wechselt selbst und schreibt in die Aktivitätsliste: *"Your
  Garage16 server has moved — switched to … automatically."* Account,
  Runden und Teams bleiben. Neu installieren oder neu anmelden musst du
  dafür nicht.

## Meldungen und was du tust

| Was im Fenster steht | Bedeutung | Was tun |
|---|---|---|
| **Gelbes Banner:** "Can't reach the Garage16 server right now — your laps are saved and will upload automatically once it's back." | Server offline oder deine Verbindung hakt | Nichts. Deine Runden sind sicher gespeichert und werden automatisch nachgeladen. Hält es lange an: Admin fragen |
| **Gelbes Banner:** "A new version of Garage16 … is required. … Your recorded laps are kept and will upload after you update." (evtl. mit Knopf **Open download page**) | Dein Garage16 ist zu alt für den Server | Knopf klicken bzw. neue Setup-Datei vom Admin holen und ausführen. Gefahrene Runden gehen nicht verloren |
| **Gelbes Banner:** "Garage16 rejected your login (it may have been reset or revoked). Click "Reconnect account…" …" | Auth token/Client secret gelten nicht mehr (z. B. zurückgesetzt) | **Reconnect account…** klicken, aktuelle Werte von der Account-Seite der Website eintragen |
| **Gelbes Banner:** "This server may have moved to …, but the connection isn't secure enough to switch automatically. Please ask whoever runs your Garage16 server for updated connection details." | Der Server zieht um, aber die alte Verbindung ist nicht per HTTPS gesichert — deshalb wechselt Garage16 aus Sicherheitsgründen nicht selbst | Admin nach der neuen Adresse fragen, dann **Reconnect account…** und die neue Server-Adresse eintragen |
| "Waiting for LMU to start..." (bleibt) | LMU läuft nicht, oder Shared Memory ist aus | LMU starten; Einstellung prüfen |
| Runde ist rot / "invalid" | Die Plausibilitätsprüfung hat die Runde beanstandet (z. B. Abkürzung) | Normal — nur gültige Runden zählen fürs Leaderboard |
| Keine Bestätigungs- oder Reset-E-Mail | Spam-Ordner, oder der Admin hat E-Mail noch nicht eingerichtet | Spam prüfen; "Resend verification email" auf der Account-Seite; sonst Admin fragen |
| Bot: "That code is invalid or expired…" | Code schon benutzt, abgelaufen (10 Minuten) oder ein neuerer Code hat ihn ersetzt | Auf der Website einen neuen Code erzeugen und den **neuesten** verwenden |
| Bot: "…is already linked to … Run `/unlink` first…" | Dieses Discord-Konto hängt schon an einem Fahrer | In Discord `/unlink`, dann mit einem neuen Code erneut `/link` |
| Bot: "Too many wrong codes…" | Mehrmals ein falscher Code | Ein paar Minuten warten, neuen Code erzeugen |
| Team-Einladung nicht mehr da | Einladungen laufen nach ein paar Tagen ab | Teammitglied bittet dich erneut ("Resend") |

**Support:** Wenn dein Admin dich um Hilfe bittet, kann er dich
`Garage16.exe --version` in einer Eingabeaufforderung ausführen lassen
(zeigt Version, Server und Logdatei — nichts Geheimes) und um die
Logdatei `garage16-client.log` bitten, die neben deinem Rundenordner
(`%LOCALAPPDATA%\Garage16`) liegt.


## Deinen Namen ändern

Der Name, der bei den Bestenlisten steht, ist der, den du bei der Registrierung
eingetippt hast. Er lässt sich jederzeit ändern: im Web **Account → "Driver
name"** → neuen Namen eintragen → **Save name**. Der neue Name erscheint sofort
überall (Leaderboard, Teams, Discord), auch bei deinen alten Runden.

## Strecke und Klasse im Leaderboard finden

Im **Leaderboard** einfach in das Feld "Find a track" tippen (z. B. `fuji`):
du bekommst die ganze Strecke mit allen Varianten. Alle Klassen (Hypercar,
LMP2 WEC, LMP2 ELMS, LMP3, GTE, GT3) stehen immer zur Auswahl; dahinter steht,
wie viele Runden es schon gibt. Neue Strecken, Varianten und Autos erscheinen
von selbst, sobald jemand sie fährt.

## Sprache: Englisch oder Deutsch (ab 0.9.1)

Beim **ersten Besuch** der Seite fragt ein kleines Fenster, in welcher Sprache du sie
benutzen möchtest: **English** oder **Deutsch**. Ein Tipp genügt; deine Browsersprache
ist schon vorgewählt. Später wechselst du jederzeit im Menü mit den Knöpfen **EN | DE**
(die Seite lädt dann einmal neu). Die Wahl wird nur in diesem Browser gemerkt.
Namen von Fahrern, Strecken und Fahrzeugen, Rundenzeiten und Meldungen, die direkt vom
Server kommen, bleiben dabei unverändert (Servermeldungen sind englisch).

## Runden analysieren und vergleichen (ab V0.9)

**Aus dem Garage16-Fenster heraus** (ab 0.9.2): **Open analysis** öffnet die Analyse,
**Analyse last lap** öffnet direkt die Runde, die gerade hochgeladen wurde.
- Mit dem **Client** öffnet sich dafür dein Browser (melde dich dort einmal auf
  garage16.de an).
- Mit **Client + Analysetool** öffnet sich ein eigenes Fenster **„Garage16 — Analysis“**.
  Du musst dich darin **nicht anmelden** — das Fenster nimmt die Anmeldung deines
  Clients. Es zeigt genau die Analyse der Website, mit allen Runden, Vergleichen und
  deinem Layout. Willst du lieber doch den Browser, setz im Garage16-Fenster den Haken
  **„Open the analysis in my browser instead“**.
- Das Analysefenster braucht die **Microsoft Edge WebView2 Runtime**. Windows 11 und ein
  aktuelles Windows 10 haben sie schon. Fehlt sie, öffnet sich die Analyse im Browser,
  und das Garage16-Fenster sagt dir, woran es liegt (die Runtime gibt es kostenlos bei
  Microsoft).
- In deinem Konto unter den Sitzungen steht das Fenster als **„Garage16 desktop app
  (analysis window)“** — dort kannst du es auch abmelden; beim nächsten Öffnen meldet
  es sich von selbst wieder an.

Mit dem Client (ab 0.9.0) zeichnet Garage16 viel mehr von jeder Runde auf —
unter anderem Federweg, Reifenlast, Schlupf und die Einstellungen von TC und ABS.
Im Web:

- **Analysis** → du siehst zwei Runden übereinander in einem Diagramm pro Wert
  (Runde 1 rot, Runde 2 blau) und darunter, wo du Zeit gewinnst oder verlierst.
  Fahr mit der Maus über ein Diagramm: überall erscheinen die genauen Werte an
  dieser Stelle der Strecke. Ein Klick hält die Stelle fest (nochmal klicken
  löst sie), das Mausrad zoomt, mit gedrückter Maustaste verschiebst du den
  Ausschnitt, ein Doppelklick setzt alles zurück. Dabei fragt die Seite nichts
  beim Server nach — es ist alles schon geladen. Nur wenn du weit hineinzoomst,
  holt sie für den sichtbaren Ausschnitt automatisch feinere Daten nach (ein
  kurzer Moment, danach sind die Kurven genauer).
- **Runde vergleichen** (ab 0.9.1): Öffne eine Runde (im Leaderboard, im Dashboard
  oder in der Streckenbibliothek auf **Analyse**) und klicke **Runde vergleichen**.
  Im Fenster **Vergleichen mit** wählst du:
  - **Garage16 Best** — die schnellste gültige Runde auf Garage16 für dieselbe
    Strecke und dasselbe Auto. (Das ist kein offizieller Weltrekord, sondern der
    Bestwert hier.)
  - **Team Best** — die schnellste gültige Runde deines Teams. Bist du in keinem
    Team oder hat dein Team keine Runde dort, steht da **„Kein Team Best verfügbar."**
  - **Meine Beste** — deine schnellste gültige Runde.
  - **Lap ID** — eine bestimmte Runde über ihre Nummer. Gibt es sie nicht,
    steht da **„Runde nicht gefunden."**; kann sie nicht angezeigt werden:
    **„Diese Runde kann nicht analysiert werden."**
  Fährst du schon die schnellste Runde, sagt das Fenster es dir und bietet die
  nächstschnellere an — dieselbe Runde wird nie zweimal geladen.
- **Schnellstart**: auf der Analysis-Startseite vergleichen die Knöpfe **Du vs. Garage16
  Best**, **Du vs. Team Best** und **Du vs. Meine Beste** deine letzte Strecke mit
  einem Klick.
- **Ansicht anpassen** (unauffällig neben den Knöpfen): Du siehst zuerst immer das
  Garage16-Standardlayout und musst nichts einstellen. Willst du es ändern, kannst du
  Fenster am **Griff (⠿)** ziehen oder mit ↑ ↓ verschieben, die Größe wählen (klein,
  mittel, groß), einem Fenster einen anderen Kanal geben, Fenster hinzufügen oder
  entfernen und Farben für Runde 1, Runde 2 und Delta wählen. Das wird **nur in
  diesem Browser** gespeichert. **Standardlayout wiederherstellen** bringt alles
  zurück — deine Runden und dein Konto sind davon nie betroffen. Ein Diagramm lässt
  sich beim normalen Ziehen nicht versehentlich verschieben: dafür gibt es den Griff.
- **Delta und Sektoren**: unter dem Delta-Diagramm stehen die Stelle, an der du am
  meisten Zeit gewinnst, und die, an der du am meisten verlierst (jeweils auf einem
  Stück von etwa Kurvenlänge; ein Klick springt dorthin), und das Ergebnis der Runde.
  In der Sektortabelle sind der beste und der schlechteste Sektor markiert.
  **Überall gilt dieselbe Regel: Runde 1 minus Runde 2.** Ein **Minus** heißt, Runde 1
  ist schneller (Zeit gewonnen, grün), ein **Plus**, Runde 1 ist langsamer (Zeit
  verloren, rot) — im Delta-Diagramm, in den Sektoren und beim Gesamtunterschied.
- **Kurven** (ab 0.9.1): eine Tabelle mit jeder Kurve der Runde — wo ihr Scheitel liegt,
  wie viel Zeit du dort gewonnen oder verloren hast, ob du früher oder später gebremst
  hast als Runde 2, dein tiefstes Tempo und dein Tempo am Ausgang. Die Kurve mit dem
  größten Verlust ist rot, die mit dem größten Gewinn grün hinterlegt. Ein Klick (oder
  Enter) zoomt genau auf Bremszone, Scheitel und Ausgang. Jede Kurve reicht bis zur
  nächsten Bremszone, die erste beginnt an der Linie — zusammen ergeben sie also genau
  den Unterschied der Rundenzeiten.
- **Dünne senkrechte Striche** in einem Diagramm sind kurze Ausschläge — ein Tipper auf
  die Bremse, ein Randstein —, die zwischen zwei Punkten der Übersicht liegen und sonst
  verschwinden würden. Zoomst du hinein, siehst du sie als normale Kurve.
- Auf dem **Handy/Tablet**: Tippen setzt den Cursor, seitliches Wischen über ein
  Diagramm führt ihn entlang der Runde, senkrechtes Wischen scrollt die Seite,
  langes Drücken hält die Stelle fest, zwei Finger zoomen, doppelt tippen setzt den
  Zoom zurück.
- Auf der **Streckenkarte** klickst du auf einen Punkt, und alle Diagramme springen
  dorthin. Sie lässt sich nach „Zeit gewonnen/verloren", Tempo, Gas, Bremse, Gang,
  Drehzahl oder G-Kraft einfärben.
- **Car physics** (aufklappen) zeigt die vier Räder und das G-G-Diagramm an der
  gewählten Stelle.
- **Circuits** ist die Streckenbibliothek: jede Strecke mit Umriss, Länge,
  schnellster Runde, deiner besten Runde dort und einem Knopf, der deine beste
  Runde mit der schnellsten vergleicht.

Runden, die mit einem **älteren Client** (vor 0.9.0) aufgezeichnet wurden, tragen das
Etikett **„Basisdaten"** (in Rundenlisten, im Leaderboard-Auswahlfenster und oben auf
der Analyse-Seite mit einer Erklärung). Sie zählen weiter fürs Leaderboard und lassen
sich vergleichen — aber nur auf den Grundwerten (Tempo, Gas, Bremse, Lenkung, Gang,
Drehzahl, Sprit, Reifendruck/-temperatur, Bremsentemperatur, Streckenkarte). Federweg,
Reifenkräfte, G-Kräfte und TC/ABS gibt es erst für Runden mit dem neuen Client: einfach
Client aktualisieren und die Runde noch einmal fahren. In einem Fenster, für das eine
Runde keine Daten hat, steht **„Keine Daten in dieser Runde"**. Jeder
angemeldete Fahrer kann deine **gültigen** Runden öffnen; ungültige siehst nur du.

### Speicherplatz auf deinem PC (ab 0.9.1)

Eine aufgezeichnete Runde ist mehrere Megabyte groß. Damit dein Laufwerk nicht
voll läuft, löscht der Client die **lokale Telemetrie-Datei einer Runde 14 Tage,
nachdem sie erfolgreich hochgeladen wurde** — die Runde selbst bleibt natürlich
auf Garage16 und lässt sich weiter analysieren. Noch nicht hochgeladene oder
abgelehnte Runden werden **nie** gelöscht. Möchtest du die Dateien länger oder
für immer behalten, sag dem Admin Bescheid: Er kann die Frist für dich ändern
(die technische Einstellung steht in der `README.md`).

Der Client 0.9.1 ist beim Fahren auch sparsamer: Er liest aus dem Speicher von LMU
nur noch die paar Kilobyte, die er braucht, statt 60-mal pro Sekunde den ganzen Block
zu kopieren, und eine Runde belegt beim Aufzeichnen rund ein Viertel des
Arbeitsspeichers von vorher. (Gemessen an nachgebauten Daten — mit laufendem Spiel
noch nicht nachgemessen.)
