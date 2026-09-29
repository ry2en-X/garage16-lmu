# Garage16 — Leitfaden für Fahrer

Kein Python, kein Terminal, keine Konfigurationsdateien. Der Ablauf:

**1. Installieren → 2. Starten → 3. Account verbinden → 4. LMU starten → 5. Fahren**

Wenn etwas nicht klappt: ganz unten steht eine Liste der Meldungen, die
du sehen kannst, und was du dann tust.

## 1. Installieren

Du bekommst von deinem Admin (dem, der Garage16 betreibt) drei Dinge:

- die Datei **`Garage16-Client-Setup.exe`** (oder einen Download-Link dazu)
- die **Web-Adresse** von Garage16 (z. B. `https://garage16.deinefreunde.de`)
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
