# Datenschutzerklärung — VORLAGE, KEINE RECHTSBERATUNG

**Dieses Dokument ist kein fertiger Rechtstext.** Es listet, welche Daten
Garage16 technisch tatsächlich verarbeitet (aus dem Code abgeleitet, Stand
V0.6.0), damit ein Anwalt/eine Anwältin eine echte, rechtsgültige
Datenschutzerklärung daraus erstellen kann. Vor Live-Betrieb mit echten
Nutzern MUSS das von einer fachkundigen Person geprüft werden — die
Anforderungen hängen u.a. davon ab, ob Nutzer aus der EU teilnehmen (dann
greift die DSGVO unabhängig vom Sitz des Betreibers), ob personenbezogene
Daten an Discord (USA) übertragen werden (Drittlandtransfer), und ob der
Dienst kommerziell oder als Hobbyprojekt betrieben wird.

## Was tatsächlich gespeichert wird (Stand Code, nicht Interpretation)

**Beim Registrieren (`POST /accounts/register`):**
- Anzeigename (frei wählbar, keine Verifikation)
- Auth-Token (gehasht, SHA-256)
- Client-Secret (Fernet-verschlüsselt at rest)
- Zeitstempel der Registrierung

**Bei jedem Upload (`POST /telemetry/upload`):**
- Rundenzeit, Sektorzeiten, Strecken-/Fahrzeugname
- Rohe Telemetriedaten (Geschwindigkeit, Gas, Bremse, Lenkung, Position,
  Reifendruck/-temperatur, etc.) als Parquet-Datei auf dem Server
- Client-Version, Upload-Zeitstempel
- **Keine IP-Adressen werden aktuell in der DB gespeichert** (nur
  optional in Access-Logs des Reverse-Proxys, falls aktiviert — siehe
  `docker/Caddyfile`)

**Bei Discord-Verknüpfung:**
- Discord-User-ID (numerisch, kein Name/Avatar)

**Bei Team-Mitgliedschaft:**
- Zuordnung Fahrer↔Team, Beitrittszeitpunkt

## Aufbewahrung / Löschung

- Selbstauskunft: `GET /accounts/me/export` (siehe unten)
- Löschung: `DELETE /accounts/me` — entfernt Fahrer, alle Runden, alle
  Telemetriedateien, Team-Mitgliedschaften, Discord-Verknüpfung
  unwiderruflich (siehe `server/routers/accounts.py`)
- Keine automatische Löschfrist implementiert — falls eine gesetzliche
  Aufbewahrungsgrenze gilt, muss das separat umgesetzt werden (aktuell:
  Daten bleiben, bis der Fahrer selbst löscht oder ein Admin es tut)

## Offene rechtliche Fragen an dich

1. **Zielgruppe/Jurisdiktion:** Nur Deutschland/EU, oder international?
   Bestimmt, ob DSGVO zwingend ist oder nur "best practice".
2. **Kommerziell oder Hobby?** Bestimmt Impressumspflicht (§5 TMG bzw.
   Digitale-Dienste-Gesetz) — auch ein Hobbyprojekt mit Spenden-Button
   kann impressumspflichtig sein.
3. **Minderjährige Nutzer erlaubt?** Falls ja: zusätzliche Anforderungen
   (Einwilligung der Erziehungsberechtigten je nach Alter/Land).
4. **Discord-Datenübertragung:** Ist eine Auftragsverarbeitungs-
   vereinbarung mit Discord vorhanden/nötig? (Meist über Discords eigene
   Data Processing Addendum abgedeckt, aber das muss geprüft werden.)
5. **Server-Standort:** Wo steht die Datenbank physisch? Relevant für
   Drittlandtransfer-Fragen.
6. **Werden Server-Logs (Access-Logs des Reverse-Proxys) dauerhaft
   gespeichert?** Falls ja, enthalten sie IP-Adressen = personenbezogene
   Daten mit eigener Löschfrist-Anforderung.

## Empfehlung

Diese Liste + den tatsächlichen Code (insbesondere `server/models.py` für
das vollständige Datenmodell) einem Anwalt für IT-Recht vorlegen, bevor
der Dienst öffentlich zugänglich gemacht wird.
