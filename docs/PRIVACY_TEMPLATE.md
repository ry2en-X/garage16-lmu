# Datenschutzerklärung — ab V1.0 auf der Website

Diese frühere Vorlage ist ersetzt: Impressum, Datenschutzerklärung, Nutzungsbedingungen und Lizenzen
stehen seit V1.0 fertig auf der Seite **Rechtliches** (`#/legal`).

- Texte: `web/js/legal/de.js` (verbindlich) und `web/js/legal/en.js` (Übersetzung)
- Betreiberangaben (Name, Anschrift, E-Mail): `.env` → `LMU_GARAGE_OPERATOR_NAME`,
  `LMU_GARAGE_OPERATOR_ADDRESS` (Zeilen mit `|` getrennt), `LMU_GARAGE_OPERATOR_EMAIL`
- Welche Daten wo liegen: `docs/DATA_PROTECTION.md`
- Fassung der Bedingungen: `TERMS_VERSION` in `server/routers/legal.py` (wird bei jeder
  Registrierung gespeichert) — bei inhaltlichen Änderungen zusammen mit dem Datum in den Texten anpassen

Die Texte beschreiben den Betrieb unter garage16.de (privates NAS in Deutschland, Cloudflare Tunnel,
ALL-INKL-Webspace, GitHub-Downloads, Gmail, optional Discord). Wer Garage16 anders betreibt, muss
sie anpassen. Keine Rechtsberatung; bei Unsicherheit einen Generator (z. B. e-recht24) oder eine
Fachanwältin/einen Fachanwalt für IT-Recht gegenlesen lassen.
