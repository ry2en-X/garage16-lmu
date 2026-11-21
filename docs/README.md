# Dokumentation

Wähle deinen Weg. Sprachregel: Was Fahrer und der Betreiber lesen, ist **Deutsch**; die technische
Referenz für Entwickler ist **Englisch** (wie `README.md` und `CHANGELOG.md`).

## Ich fahre
1. Auf der Website ein Konto anlegen und die E-Mail bestätigen.
2. Den Client von der Seite **Download** (`/download`) installieren und mit E-Mail und Passwort
   verbinden.
3. Fahren — jede Runde landet von selbst im Leaderboard, in der **Rundenanalyse** und der
   **Stint-Analyse**.

Alles Weitere: [`FRIENDS_GUIDE.md`](FRIENDS_GUIDE.md) — Konto, Client, LMU, Analyse, häufige
Meldungen. Dieselbe Anleitung steht auf der Website unter **Einrichtung** (`/setup`).

## Ich betreibe die NAS
1. [`ADMIN_GUIDE.md`](ADMIN_GUIDE.md) — Installation, `.env`, **Kapitel 8: das aktuelle Update**,
   Discord, SMTP, Domain, Cloudflare Pages, Client-Release auf GitHub.
2. [`RUNBOOKS.md`](RUNBOOKS.md) — was tun, wenn etwas kaputt ist (Server down, Platte voll,
   Quarantäne, Restore, Tunnel-Adresse geändert, …).
3. [`ROLLBACK.md`](ROLLBACK.md) — Backup zurückspielen und zurück auf eine ältere Version.

Dazu: [`NAS_MESSUNGEN.md`](NAS_MESSUNGEN.md) (Messungen mit echten Runden und auf der
NAS-Hardware), [`DATA_PROTECTION.md`](DATA_PROTECTION.md) (welche Daten wo liegen, wie lange, wer
sie sieht) und die englische Referenz [`DEPLOYMENT.md`](DEPLOYMENT.md) (alle Einstellungen,
beide Zugangsvarianten, Backups, Umzug).

## Ich entwickle (English)
1. [`../README.md`](../README.md) — set up, run, the quick test run.
2. [`ARCHITECTURE.md`](ARCHITECTURE.md) — the code layout (server areas, `api/v1`, web `core/` and
   `pages/`), data flow, decisions (ADRs).
3. [`QUALITY.md`](QUALITY.md) — the tests (by topic), the release gate, coverage, mutation check,
   what is not proven yet.

Also: [`DEPLOYMENT.md`](DEPLOYMENT.md) (every server setting, Cloudflare Pages, running tests),
[`CLIENT_BUILD.md`](CLIENT_BUILD.md) (Windows installers), [`CLIENT_INSTALL.md`](CLIENT_INSTALL.md)
(the client from source), [`LMU_VERIFICATION_PROTOCOL.md`](LMU_VERIFICATION_PROTOCOL.md) (what was
verified against the real game), `trackcheck_labels_vorlage.csv` (labelling real laps,
`NAS_MESSUNGEN.md` Teil D). What changed in which release: [`../CHANGELOG.md`](../CHANGELOG.md).

## Archiv
[`archive/`](archive/README.md) — abgeschlossene Berichte und alte Anleitungen, unverändert
aufbewahrt: Update-Anleitungen V0.9–V1.0, Changelog bis V0.8, die V0.9.9-Berichte, Messrohdaten,
Mutationslogs, die frühen Änderungsnotizen V0.5.x und die alten Rechtstext-Vorlagen. Sie nennen
die Pfade und Namen ihrer Zeit (Skripte direkt unter `scripts/`, `LMU_GARAGE_*`-Namen, `#/…`-Links).
