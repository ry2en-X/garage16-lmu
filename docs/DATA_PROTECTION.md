# Datenschutz — welche Daten Garage16 speichert, wozu, wie lange (V1.0)

Bestandsaufnahme für den Betreiber. **Ab V1.0 steht die fertige Datenschutzerklärung auf der
Website** (Rechtliches → Datenschutz, Text in `web/js/legal/de.js` und `web/js/legal/en.js`); sie
beruht auf dieser Tabelle. Wer hier etwas ändert, ändert dort mit (und `TERMS_VERSION` in
`server/routers/legal.py`). Keine Rechtsberatung.

## Was gespeichert wird

| Daten | Wo | Zweck | Wer sieht es | Aufbewahrung |
|---|---|---|---|---|
| Anzeigename, Konto-ID, Anlagedatum | DB `drivers` | Leaderboards, Teams, Profil | öffentlich (Name), Admin | bis zur Löschung |
| E-Mail (optional), Bestätigungsstatus | DB `drivers` | Web-Login, Passwort-Reset | nur der Fahrer, Admin | bis zur Löschung |
| Passwort | DB `drivers.password_hash` (Argon2, nie im Klartext) | Web-Login, ab V1.0 auch Anmeldung des Clients | niemand | bis zur Löschung |
| V1.0: Zustimmung zu Nutzungsbedingungen/Datenschutz (Zeitpunkt, Fassung) | DB `drivers.terms_accepted_at`, `terms_version` | Nachweis der Zustimmung bei der Registrierung | Admin; der Fahrer im Export | bis zur Löschung |
| Desktop-Token (Hash), Client-Geheimnis (verschlüsselt, `LMU_GARAGE_SECRET_KEY`) | DB `drivers` | Upload-Authentifizierung und -Signatur | niemand | bis zur Löschung / Erneuerung |
| Discord-Nutzer-ID (optional) | DB `drivers` | Rekord-Ankündigungen mit Erwähnung | Admin | bis Trennung / Löschung |
| Web-Sitzungen (Token-Hash, Ablauf, Browser-Kennung) | DB `sessions` | angemeldet bleiben, Sitzungen beenden | der Fahrer (Konto → Sitzungen) | bis Ablauf / Abmeldung / Löschung |
| Runden: Strecke, Auto, Zeiten, Sektoren, Gültigkeit mit Begründung, Kennzahlen, Temperaturen, Client-Version | DB `laps` | Leaderboards, Analyse, Prüfung | gültige Runden: angemeldete Nutzer; ungültige: nur der Fahrer, Admin | bis zur Löschung |
| Telemetrie (eine Datei pro Runde: Eingaben, Tempo, Position auf der Strecke, Fahrzeugdaten) — **die empfangene Originaldatei**, unverändert und schreibgeschützt (0440) | Volume `telemetry_data` | Analyse, Nachprüfbarkeit | wie die Runde (eine zurückgestellte Runde: nur der Fahrer und Admins) | wie die Runde |
| V0.9.9: Fingerabdruck der Originaldatei: SHA-256, Größe, Request-ID des Uploads, Server-Signatur (MAC) | DB `laps` | Manipulation an gespeicherten Runden erkennen | Admin; der Fahrer im Export | wie die Runde |
| V0.9.9: Audit-Protokoll: Zeit, Ereignis (angenommen, zurückgestellt, freigegeben, Admin-Entscheidung, neu bewertet, Fahrer gelöscht), Runden-ID, Fahrer-ID, Request-ID, Details (Prüfsumme, Größe, Befund-Codes, Prüfversionen; **keine** Namen, E-Mails, Tokens, IPs, Telemetrie-Inhalte) | DB `audit_events` | Nachvollziehbarkeit von Integritäts- und Prüfentscheidungen | Admin; der Fahrer im Export (seine Ereignisse) | **365 Tage** (`LMU_GARAGE_AUDIT_RETENTION_DAYS`, mind. 30) |
| Rekord-Ereignisse | DB `record_events` | Feeds, Discord | angemeldete Nutzer | bis zur Löschung des Fahrers |
| Teams, Mitgliedschaften, Einladungen | DB | Teamfunktionen | Teammitglieder | bis Austritt / Löschung |
| Meldungen zu Runden | DB `lap_reports` | Moderation | Admin | bis Löschung des Fahrers |
| Upload-Protokoll: Zeit, Request-ID, Fahrer-ID, Runden-ID, Ergebnis, Grund, Client-Version, Größe, Dauer (keine IP, kein Inhalt) | DB `upload_events` | Fehlersuche, Betriebsübersicht | Admin | **30 Tage**, dann automatisch gelöscht |
| Server-Log: Zeit, Methode, Pfad, Status, Dauer, Request-ID; bei Uploads Fahrer- und Runden-ID | Docker-Log | Fehlersuche | Betreiber | nach Docker-Log-Rotation |
| IP-Adressen | **nur im Arbeitsspeicher** (Rate-Limits, höchstens das jeweilige Zeitfenster) und im Zugriffsprotokoll des Reverse Proxy (`/data/access.log` im Caddy-Volume) | Missbrauchsschutz | Betreiber | Fenster der Grenze; Zugriffsprotokoll **7 Tage** (V1.0: `roll_keep_for 168h`) |
| Backups (alles oben, außer Logs) | Backup-Ordner | Wiederherstellung | Betreiber | **14 Tage** (`BACKUP_RETENTION_DAYS`) |
| Lokal beim Fahrer: noch nicht hochgeladene Runden, Konfiguration, Client-Log | sein PC | Upload-Warteschlange | der Fahrer | bis hochgeladen / vom Fahrer gelöscht |

## Auskunft und Export

Konto → Daten & Datenschutz → „Meine Daten exportieren“ (`GET /accounts/me/export`): Konto, alle
Runden mit Metadaten, Teams, Sitzungen, Rekorde, Upload-Protokoll, **seit V0.9.9 der Fingerabdruck
jeder Originaldatei (`lap_files`: SHA-256, Größe, Upload-Zeit, Request-ID, Prüfstatus) und die
Audit-Ereignisse zu seinen Runden (`audit_events`, ohne die Signaturen)** — maschinenlesbar (JSON). Die Telemetriedateien selbst lassen
sich je Runde im LapTime Analyzer ansehen.

## Löschung

Konto → Konto löschen (oder Admin → Fahrer → Löschen) entfernt in einer Transaktion: Konto, alle
Runden und ihre Telemetriedateien, Rekord-Ereignisse, Meldungen (eigene und zu eigenen Runden),
Teammitgliedschaften, Einladungen, Web-Sitzungen, E-Mail- und Passwort-Tokens, Discord-Verknüpfungscodes,
das **Upload-Protokoll** des Fahrers (V0.9.8). **V0.9.9:** Im Audit-Protokoll wird die Fahrer-ID
der Ereignisse entfernt (die Ereignisse selbst bleiben als Verlauf der — jetzt gelöschten — Runden,
ohne Personenbezug außer der Runden-ID, und laufen mit der Aufbewahrung aus); die Löschung selbst
wird nur mit der Anzahl der Runden protokolliert. Die Fahrer-ID gehört absichtlich nicht zur
Hash-Kette, damit das ohne Kettenbruch geht. Besitzt er ein Team mit anderen Mitgliedern, wird erst
die Besitzerrolle übertragen. Getestet: `tests/test_driver_deletion.py` (eine Wächter-Prüfung schlägt
fehl, sobald eine neue Tabelle auf Fahrer verweist, ohne in der Löschung berücksichtigt zu sein),
`tests/test_v097_operations.py`.

**Grenzen, die in die Datenschutzerklärung gehören:**
* **Backups** enthalten gelöschte Daten noch bis zu 14 Tage (bis das Backup selbst gelöscht wird).
  Wird ein Backup von vor der Löschung wiederhergestellt, ist der Fahrer wieder da — nach einem
  Restore die Löschungen der letzten 14 Tage erneut ausführen.
* **Discord-Nachrichten**, die der Bot schon gepostet hat, liegen bei Discord.
* **Docker-Logs** verschwinden mit ihrer Rotation, nicht mit der Löschung.

## Verantwortung beim Betrieb

* `LMU_GARAGE_SECRET_KEY` schützt die Client-Geheimnisse; Wechsel: `docs/RUNBOOKS.md` Punkt 8.
* V0.9.9: `LMU_GARAGE_INTEGRITY_KEY` signiert die Fingerabdrücke der Runden und das Audit-Protokoll
  (ein eigener Schlüssel für einen eigenen Zweck; nicht gesetzt: aus `LMU_GARAGE_SECRET_KEY`
  abgeleitet). Er steht nur in `.env` (bzw. als Docker-Secret), nie im Repository, in Logs oder in
  Backups. Wechsel und Verlust: `docs/RUNBOOKS.md` Punkt 16. Eine Signatur beweist nur, dass der Server
  den Datensatz so gespeichert hat — nicht, dass die Spieldaten echt sind.
* Backups liegen unverschlüsselt — auf einer Platte, auf die nur der Betreiber Zugriff hat, ablegen.
* Öffentlich (ohne Anmeldung) sind nur: Leaderboards, Streckenbibliothek, Kanalliste, öffentliche
  Fahrerprofile, Health-Checks (`tests/test_v097_permissions.py` prüft das für jeden Endpunkt).
* V1.0: Name, Anschrift und E-Mail des Betreibers für Impressum und Datenschutzerklärung stehen in
  `.env` (`LMU_GARAGE_OPERATOR_*`) und werden nur über `GET /legal` an die Seite „Rechtliches“
  gegeben (mit `X-Robots-Tag: noindex`) — nie im Repository.
