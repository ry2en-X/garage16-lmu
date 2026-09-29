# Client aus dem Quellcode (nur für Entwickler)

> **Freunde brauchen das hier nicht.** Sie installieren die fertige App —
> siehe [`FRIENDS_GUIDE.md`](FRIENDS_GUIDE.md). Wie diese App gebaut wird:
> [`CLIENT_BUILD.md`](CLIENT_BUILD.md).
>
> Dieses Dokument beschreibt den Weg über Python, `pip` und ein Terminal —
> gedacht für Entwicklung, Tests und den Fall, dass jemand den Client
> bewusst aus dem Quellcode betreiben will.

## Voraussetzungen

- Windows 10/11 (der LMU-Shared-Memory-Reader braucht Windows)
- Python 3.11 oder 3.12 (von [python.org](https://python.org) — bei der
  Installation "Add python.exe to PATH" anhaken)
- Le Mans Ultimate mit aktiviertem Shared-Memory-Output

## Installation und Start

```
pip install -r client/requirements.txt
py -m client.main
```

Beim ersten Start öffnet sich ein **Fenster** ("Connect Garage16 to your
account") und fragt nach Server-Adresse, Auth-Token und Client-Secret —
kein Terminal-Assistent mehr (seit V0.8.6). Beides bekommst du auf der
Account-Seite der Web-App; das **Client-Secret wird nur einmal
angezeigt**.

Entwickler-Optionen:

| Aufruf | Wirkung |
|---|---|
| `py -m client.main --version` | Version, konfigurierter Server, Datenordner, Logdatei (nichts Geheimes — auch als Support-Ausgabe brauchbar) |
| `py -m client.main --reconfigure` | Einrichtungsfenster erneut öffnen (Friends nutzen den Button "Reconnect account…") |
| `py -m client.main --follow-migration` | Serverumzug **von Hand** folgen — nötig nur für einen alten Server ohne HTTPS; ansonsten folgt die laufende App automatisch (siehe README, "Moving the server later") |

Ohne Fenster (Skripte/CI) geht Konfiguration weiterhin über die
Umgebungsvariablen `LMU_GARAGE_API_URL`, `LMU_GARAGE_AUTH_TOKEN`,
`LMU_GARAGE_CLIENT_SECRET`; der Datenordner lässt sich mit
`LMU_GARAGE_DATA_DIR` festlegen.

## Wo liegt was

| Was | Wo |
|---|---|
| Gespeicherte Anmeldung + Server-Adresse | `%USERPROFILE%\.lmu_garage\client_config.json` |
| Aufgezeichnete Runden | `%LOCALAPPDATA%\Garage16\laps` (oder ein vorhandenes `./lmu_garage_data`) |
| Logdatei | neben dem Rundenordner: `garage16-client.log` |

## Aktualisieren

Quellcode durch die neue Version ersetzen und `pip install -r
client/requirements.txt` erneut ausführen. Konfiguration und Runden
bleiben erhalten. Ist der Client älter als das Server-Minimum
(`min_client_version`), zeigt das Fenster einen Hinweis; aufgezeichnete
Runden bleiben liegen und werden nach dem Update hochgeladen.
