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
account") und fragt nach Server-Adresse, **E-Mail und Passwort** deines
Garage16-Kontos (seit V1.0; die E-Mail muss bestätigt sein, seit V1.0.1).
Auth-Token und Client-Secret von Hand gehen weiter unter „Use an auth token
instead (advanced)“. Die Zugangsdaten landen in
`~/.garage16/client_config.json` (bis 1.0.1 `~/.lmu_garage`, wird einmal verschoben) — unter Windows verschlüsselt (V1.0.1,
`client/secret_store.py`). Gegen einen lokalen Entwicklungsserver:
`http://localhost:8000` eintragen; ohne SMTP steht der Bestätigungslink im
Server-Log, oder den Server mit `GARAGE16_REQUIRE_EMAIL_VERIFICATION=0` starten.

Entwickler-Optionen:

| Aufruf | Wirkung |
|---|---|
| `py -m client.main --version` | Version, konfigurierter Server, Datenordner, Logdatei (nichts Geheimes — auch als Support-Ausgabe brauchbar) |
| `py -m client.main --reconfigure` | Einrichtungsfenster erneut öffnen (Friends nutzen den Button "Reconnect account…") |
| `py -m client.main --follow-migration` | Serverumzug **von Hand** folgen — nötig nur für einen alten Server ohne HTTPS; ansonsten folgt die laufende App automatisch (siehe `docs/DEPLOYMENT.md`, "Moving the server later") |

Ohne Fenster (Skripte/CI) geht Konfiguration weiterhin über die
Umgebungsvariablen `GARAGE16_API_URL`, `GARAGE16_AUTH_TOKEN`,
`GARAGE16_CLIENT_SECRET`; der Datenordner lässt sich mit
`GARAGE16_DATA_DIR` festlegen.

## Wo liegt was

| Was | Wo |
|---|---|
| Gespeicherte Anmeldung + Server-Adresse | `%USERPROFILE%\.garage16\client_config.json` (bis 1.0.1 `.lmu_garage`) |
| Aufgezeichnete Runden | `%LOCALAPPDATA%\Garage16\laps` (oder ein vorhandenes `./lmu_garage_data`) |
| Logdatei | neben dem Rundenordner: `garage16-client.log` |

## Aktualisieren

Quellcode durch die neue Version ersetzen und `pip install -r
client/requirements.txt` erneut ausführen. Konfiguration und Runden
bleiben erhalten. Ist der Client älter als das Server-Minimum
(`min_client_version`), zeigt das Fenster einen Hinweis; aufgezeichnete
Runden bleiben liegen und werden nach dem Update hochgeladen.
