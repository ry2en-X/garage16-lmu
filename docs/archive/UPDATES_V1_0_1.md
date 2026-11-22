# Update V1.0.0 → V1.0.1 (aus dem Admin-Leitfaden, Kapitel 8)

Unverändert aus `docs/ADMIN_GUIDE.md` übernommen, als V1.0.3 erschien. Die Pfade und Befehle gelten
für den damaligen Stand (Skripte direkt unter `scripts/`).

## Neu in V1.0.1 — Konten für eine offene Instanz, mehrere PCs, aufgeräumte Doku

- **E-Mail zuerst bestätigen:** Neue Konten melden den Browser nicht mehr sofort an; der Desktop-Client
  verbindet sich erst, wenn die E-Mail bestätigt ist (`GARAGE16_REQUIRE_EMAIL_VERIFICATION`,
  Standard an). Nie bestätigte Anmeldungen ohne Runden und ohne Team werden nach 7 Tagen gelöscht
  (`GARAGE16_UNVERIFIED_ACCOUNT_DAYS`). **Beta-Fahrer, die ihre E-Mail nie bestätigt haben,
  müssen das beim nächsten Verbinden nachholen** (Konto → „Bestätigungs-E-Mail erneut senden“) —
  bereits verbundene Clients laden weiter hoch.
- **Nutzungsbedingungen:** Konten von vor V1.0 (und alle nach einer Änderung der Texte) werden nach
  dem Anmelden auf der Website einmal um Zustimmung gebeten; der Client 1.0.1 fragt beim Verbinden.
- **Mehrere PCs pro Konto:** Rig und Laptop bleiben gleichzeitig verbunden (bis 5,
  `GARAGE16_MAX_DEVICES`), sichtbar und trennbar unter Konto → Desktop-Client & Anmeldungen →
  Verbundene PCs. Bereits verbundene Clients behalten ihre Anmeldung.
- **Fair Play:** Fährt ein neuer Fahrer (weniger als 10 offizielle Runden,
  `GARAGE16_NEW_DRIVER_REVIEW_LAPS`) schneller als das Garage16 Best anderer, wartet die Runde auf
  dich (Admin → Track-Limit-Prüfung, mit Begründung). Siehe Kapitel 12.
- **Eindeutige Fahrernamen** (Groß-/Kleinschreibung, unsichtbare Zeichen und Breitschrift zählen
  nicht als Unterschied; „admin“, „garage16“ u. ä. sind reserviert). Schon doppelt vergebene Namen
  bleiben bestehen; der jüngere Fahrer muss beim nächsten Umbenennen einen freien Namen wählen.
- **Schutz der Konten:** gleiche Antwort bei der Registrierung, egal ob die Adresse schon ein Konto
  hat; Fehlversuche beim Anmelden werden zusätzlich pro E-Mail-Adresse gezählt; Mails an eine
  Adresse sind begrenzt. Die Webseite sendet jetzt Sicherheitsheader (CSP, kein Einbetten in fremde
  Seiten).
- **Client 1.0.1:** speichert die Anmeldung unter Windows verschlüsselt, schickt den Computernamen
  (für die PC-Liste), warnt vor `http://`, zeigt freiwillige Updates an.
- **Eine Migration (0019)**, rein additiv: Spalte `drivers.display_name_key` und Tabelle
  `device_tokens`. Neue `.env`-Einträge sind **nicht nötig** (alle haben sinnvolle Standards,
  siehe `.env.example`).
- Die Dokumentation ist aufgeräumt: alte Update-Abschnitte stehen in
  `docs/archive/UPDATES_V0_9_BIS_V1_0.md`, die technische Referenz in `docs/DEPLOYMENT.md`.

**Update der NAS von V1.0.0 auf V1.0.1**

1. **Backup** (noch im alten Ordner):
   ```
   ALT="$G16/Garage16_Lmu_V1_0_0/Garage16 - LMU"
   cd "$ALT"
   sudo ./scripts/backup.sh "$BACKUPS"
   ```
2. **Neuen Ordner anlegen**, `Garage16_Lmu_V1_0_1.zip` per File Station hineinladen und dort
   entpacken („Hierher extrahieren“), `.env` übernehmen:
   ```
   mkdir -p "$G16/Garage16_Lmu_V1_0_1"
   # … ZIP per File Station in diesen Ordner laden und dort entpacken …
   NEU="$G16/Garage16_Lmu_V1_0_1/Garage16 - LMU"
   ls "$NEU"
   sudo cp "$ALT/.env" "$NEU/.env"
   ```
3. **Alten Stack stoppen** (ohne `-v`), **neuen bauen und starten** — Migration 0019 läuft dabei:
   ```
   cd "$ALT" && sudo docker compose --profile tunnel down
   cd "$NEU" && sudo docker compose --profile tunnel up -d --build
   sudo docker compose --profile tunnel ps
   sudo docker compose exec -T server python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/health/ready').read().decode())"
   ```
   Erwartet: `"status":"ready"`, `"version":"1.0.1"`. Der Quick-Tunnel-Container (`quicktunnel`)
   gehört nicht zum Stack und läuft unverändert weiter — **nicht neu erstellen**, sonst ändert sich
   die Adresse (`docs/RUNBOOKS.md`, „Quick-Tunnel-Adresse hat sich geändert“).
4. **Im Browser prüfen** (Strg+F5): Menü → **Konto** → angemeldet erscheint ggf. der Dialog
   „Nutzungsbedingungen und Datenschutzerklärung“ (einmal akzeptieren); unter „Desktop-Client &
   Anmeldungen“ steht **Verbundene PCs**. Ein Blick in die Entwicklerwerkzeuge (F12 → Konsole) zeigt
   keine roten CSP-Fehler.
5. **Konsistenz prüfen** (ändert nichts):
   ```
   sudo docker compose exec -T server sh -c 'GARAGE16_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" python -m scripts.ops.consistency_check'
   ```
6. **Doppelte Fahrernamen finden** (nur Anzeige — sie bleiben bestehen, siehe oben):
   ```
   sudo docker compose exec -T db psql -U garage16 -d garage16 -c "SELECT id, display_name, created_at FROM drivers WHERE display_name_key IS NULL ORDER BY id;"
   ```
   Leer = keine Doppel.
7. **Backup mit dem neuen Ordner** und Aufgabenplaner umstellen:
   ```
   sudo ./scripts/backup.sh "$BACKUPS"
   ```
   DSM → Systemsteuerung → Aufgabenplaner → die tägliche Garage16-Backup-Aufgabe → Pfad auf
   `Garage16_Lmu_V1_0_1` ändern.
8. **Client 1.0.1 veröffentlichen** (auf dem PC, Kapitel 11): Tag `client-v1.0.1` pushen. Die
   Download-Knöpfe zeigen danach von selbst auf das neue Release; laufende 1.0.0-Clients zeigen den
   Hinweis „Garage16 v1.0.1 is available“.

**Zurück auf V1.0.0:** erst die Migration zurücknehmen, dann im alten Ordner starten:
```
cd "$G16/Garage16_Lmu_V1_0_1/Garage16 - LMU"
sudo docker compose exec -T server sh -c 'GARAGE16_DB_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" alembic downgrade 0018'
sudo docker compose --profile tunnel down
cd "$G16/Garage16_Lmu_V1_0_0/Garage16 - LMU"
sudo docker compose --profile tunnel up -d
```
(Der Downgrade löscht die Tabelle `device_tokens` und die Namensspalte. PCs, die mit Client 1.0.1
über E-Mail verbunden wurden, müssen sich danach neu verbinden; alles andere bleibt.)
