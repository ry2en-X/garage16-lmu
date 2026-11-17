# Dokumentation

Sprachregel: Was Fahrer und der Betreiber der Instanz lesen, ist **Deutsch**; die technische
Referenz für Entwickler und fremde Betreiber ist **Englisch** (wie `README.md` und `CHANGELOG.md`).

## Für Fahrer
- [`FRIENDS_GUIDE.md`](FRIENDS_GUIDE.md) — Konto, Client installieren und verbinden, LMU, Analyse,
  häufige Meldungen. Dieselbe Anleitung steht auf der Website unter **Einrichtung** (`#/setup`).

## Für den Betreiber
- [`ADMIN_GUIDE.md`](ADMIN_GUIDE.md) — NAS-Installation, `.env`, Updates (Kapitel 8: das aktuelle
  Update), Discord, SMTP, Domain, Client-Release auf GitHub.
- [`RUNBOOKS.md`](RUNBOOKS.md) — was tun, wenn etwas kaputt ist (Server down, Platte voll,
  Quarantäne, Restore, Tunnel-Adresse geändert, …).
- [`ROLLBACK.md`](ROLLBACK.md) — Backup zurückspielen und zurück auf eine ältere Version.
- [`NAS_MESSUNGEN.md`](NAS_MESSUNGEN.md) — Messungen mit echten LMU-Runden und auf der NAS-Hardware.
- [`DATA_PROTECTION.md`](DATA_PROTECTION.md) — welche Daten wo liegen, wie lange, wer sie sieht.

## Technische Referenz (English)
- [`DEPLOYMENT.md`](DEPLOYMENT.md) — access variants, frontend placement, backups, cleanup, moving
  the server, monitoring, migrations, secrets, every server setting, credentials, tests.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — components, data flow, decisions (ADRs).
- [`QUALITY.md`](QUALITY.md) — release gate, metric definitions, coverage, what is not proven yet.
- [`CLIENT_BUILD.md`](CLIENT_BUILD.md) — building and publishing the Windows installers.
- [`CLIENT_INSTALL.md`](CLIENT_INSTALL.md) — running the client from source (developers).
- [`LMU_VERIFICATION_PROTOCOL.md`](LMU_VERIFICATION_PROTOCOL.md) — what was verified against the
  real game, item by item.
- `trackcheck_labels_vorlage.csv` — template for labelling real laps (`NAS_MESSUNGEN.md`, Teil D).

## Archiv
[`archive/`](archive/README.md) — abgeschlossene Berichte und alte Anleitungen, unverändert
aufbewahrt: Update-Anleitungen V0.9–V1.0, Changelog bis V0.8, die V0.9.9-Berichte, Messrohdaten,
Mutationslogs, die frühen Änderungsnotizen V0.5.x und die alten Rechtstext-Vorlagen (ersetzt durch
die Seite „Rechtliches“ der Web-App).
