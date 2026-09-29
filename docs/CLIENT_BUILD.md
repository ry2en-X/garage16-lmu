# Client-Paket bauen und verteilen (für den Admin)

So entsteht die App, die deine Freunde ohne Python und ohne Terminal
installieren: `Garage16-Client-Setup.exe`. Gebaut wird **einmal von dir**,
danach reicht es, die Datei zu verteilen.

> **Ehrlicher Stand (V0.8.6):** Alles hier ist geschrieben und statisch
> getestet. Ein **Linux**-Build der gleichen Konfiguration wurde real
> gebaut und ausgeführt (Version, eingebackene Server-Adresse, HTTPS-
> Serverumzug). Ein **Windows-.exe** und der **Installer** wurden noch nie
> gebaut — PyInstaller kann nur für das Betriebssystem bauen, auf dem es
> läuft. Der erste echte Lauf passiert bei dir. Bau ihn und probiere die
> Installation an einem Windows-PC aus, bevor du ihn verteilst (Checkliste
> unten).

## Variante A — GitHub baut es für dich (kein Windows-PC nötig)

1. Projekt in ein GitHub-Repository legen (privat reicht).
2. Repository → Settings → Secrets and variables → Actions → **Variables**
   → neue Variable `GARAGE16_SERVER_URL` = die Adresse deines Servers
   (z. B. `https://garage16.deinedomain.de`). Sie wird ins Paket
   eingebacken: Freunde tippen sie nie ein.
3. Actions → **build-client** → *Run workflow* (oder einen Tag wie
   `client-v0.8.6` pushen).
4. Nach ein paar Minuten: unter dem Lauf die Artefakte
   `Garage16-Client-Setup.exe` und `Garage16-Portable.zip` herunterladen.

## Variante B — auf einem Windows-PC selbst bauen

Voraussetzungen (nur auf dem Build-PC): Python 3.11/3.12 und
[Inno Setup 6](https://jrsoftware.org/isinfo.php).

```
.\packaging\build_windows.ps1 -ServerUrl "https://garage16.deinedomain.de"
```

Ergebnis:

| Datei | Wofür |
|---|---|
| `dist-installer\Garage16-Client-Setup.exe` | der Installer für Freunde |
| `dist-installer\Garage16-Portable.zip` | Ausweichlösung ohne Installer: entpacken, `Garage16.exe` starten |
| `dist\Garage16\` | die fertige App als Ordner |

Ohne Inno Setup entsteht trotzdem die Portable-Zip; das Skript sagt es
klar. Ohne `-ServerUrl` fragt die App beim ersten Start nach der Adresse.

## Verteilen

Die Setup-Datei irgendwo hinlegen, wo deine Freunde sie laden können
(GitHub-Release, geteilter Link von Google Drive/Nextcloud/NAS) und den
Link weitergeben. Trag denselben Link in `.env` ein, damit sich veraltete
Clients selbst dorthin schicken:

```
LMU_GARAGE_CLIENT_DOWNLOAD_URL=https://…/Garage16-Client-Setup.exe
```

Danach den Server neu starten (`docker compose up -d server`).

## Update-Ablauf

1. Version in `client/uploader/uploader.py` (`CLIENT_VERSION`) erhöhen —
   der Installer übernimmt sie automatisch (`packaging/sync_version.py`).
2. Neu bauen (Variante A oder B) und die neue Setup-Datei verteilen.
3. **Optional erzwingen:** `LMU_GARAGE_MIN_CLIENT_VERSION` in `.env` auf
   die neue Version setzen. Clients darunter zeigen dann ein Banner mit
   Download-Button; ihre bereits aufgezeichneten Runden bleiben erhalten
   und laden nach dem Update hoch.
4. Freunde starten die neue `Garage16-Client-Setup.exe` einfach erneut —
   sie aktualisiert an Ort und Stelle (gleiche App-ID). Anmeldung und
   Runden liegen außerhalb des Installationsordners und bleiben unberührt,
   auch bei einer Deinstallation.

## Serverumzug später

Nichts neu zu bauen: laufende Clients folgen einem angekündigten Umzug
selbst (README, "Moving the server later"). Nur wenn ein Client zu alt für
den neuen Server ist, braucht er das Update wie oben.

## Bekannte Eigenheiten — vorher lesen

- **Windows SmartScreen warnt** bei einer frisch gebauten, unsignierten
  `.exe` ("Der Computer wurde durch Windows geschützt"). Freunde müssen
  *Weitere Informationen → Trotzdem ausführen* wählen; das steht im
  Freunde-Leitfaden. Die Warnung verschwindet nur mit einem
  Code-Signing-Zertifikat (kostenpflichtig; für Open-Source z. B. über
  SignPath.io kostenlos möglich). Auch Antivirus-Fehlalarme bei neuen
  `.exe`-Dateien sind üblich.
- **Größe:** der entpackte Ordner ist rund 320 MB (pandas/pyarrow für das
  Runden-Format). Der Installer komprimiert deutlich kleiner.
- **Nur Windows-Nutzer mit LMU** brauchen den Client; der
  Shared-Memory-Reader läuft nur unter Windows.

## Checkliste vor dem Verteilen (auf einem echten Windows-PC)

- [ ] Installer läuft durch, ohne nach Administrator-Rechten zu fragen
- [ ] Nach dem Start: **Fenster** "Connect Garage16 to your account",
      Server-Adresse ist bereits vorausgefüllt (wenn eingebacken)
- [ ] Mit Auth-Token/Client-Secret verbinden → Hauptfenster,
      "Waiting for LMU to start..."
- [ ] LMU starten → "Connected to LMU shared memory."
- [ ] Eine Runde fahren → erscheint im Aktivitätsfenster und im Web-Leaderboard
- [ ] Zweites Mal `Garage16-Client-Setup.exe` ausführen → Update in Place,
      Anmeldung bleibt, kein neues Einrichtungsfenster
- [ ] Deinstallieren → Anmeldung/Runden bleiben (`%USERPROFILE%\.lmu_garage`,
      `%LOCALAPPDATA%\Garage16`)
- [ ] `Garage16.exe --version` in einer Eingabeaufforderung zeigt Version und Server
      (die App hat kein eigenes Konsolenfenster und dockt dafür an das
      Terminal an, aus dem du sie startest — dieser Weg ist unter Linux
      nicht testbar; funktioniert er nicht, ist es kein Blocker für Freunde,
      nur für diese Diagnosehilfe)
