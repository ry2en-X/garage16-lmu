// legal/de.js — Impressum, Datenschutzerklärung, Nutzungsbedingungen, Lizenzen (V1.0), deutsch.
//
// Beschreibt den Betrieb unter garage16.de: Server auf einem privaten NAS in Deutschland,
// Cloudflare Tunnel, Webspace bei ALL-INKL.COM, Downloads über GitHub, optional Discord,
// E-Mail-Versand über Gmail. Wer Garage16 anders betreibt, muss diese Texte anpassen.
// Name, Anschrift und E-Mail des Betreibers stehen NICHT hier, sondern in der .env des Servers
// (LMU_GARAGE_OPERATOR_*) und kommen über GET /legal — das Repository ist öffentlich.
//
// Aufbau: Abschnitte { h, blocks }. Ein Block ist ein Absatz (String), { ul: [...] },
// { table: { head, rows } } oder { operator: true } (Betreiberangaben, von der Seite eingesetzt).
// Platzhalter: {min_age}, {date}. Fachausdrücke dürfen hier auf Deutsch stehen: diese Datei
// läuft nicht durch t() — die Sprache wird als Ganzes gewählt (pages/legal.js).
// Bei inhaltlichen Änderungen: TERMS_VERSION in server/routers/legal.py und {date} anpassen.

export const DATE = "24. Oktober 2026";

export const LABELS = {
  title: "Rechtliches",
  intro: "Garage16 ist ein privates, nicht-kommerzielles Hobbyprojekt. Hier steht, wer dahintersteht, was mit deinen Daten passiert und welche Regeln gelten.",
  tabs: { imprint: "Impressum", privacy: "Datenschutz", terms: "Nutzungsbedingungen", licenses: "Lizenzen" },
  operatorMissing: "Die Betreiberangaben sind auf diesem Server noch nicht eingetragen (LMU_GARAGE_OPERATOR_NAME / _ADDRESS / _EMAIL in der .env).",
  operatorError: "Die Betreiberangaben konnten gerade nicht geladen werden. Bitte später erneut versuchen.",
  email: "E-Mail",
  asOf: "Stand",
};

export const IMPRINT = [
  {
    h: "Angaben gemäß § 5 Digitale-Dienste-Gesetz (DDG)",
    blocks: [{ operator: true }],
  },
  {
    h: "Art des Angebots",
    blocks: [
      "Garage16 ist ein privat betriebenes, nicht-kommerzielles Hobbyprojekt für Fahrer der Rennsimulation Le Mans Ultimate. Es gibt keine Werbung, keine kostenpflichtigen Leistungen und keine Gewinnerzielungsabsicht.",
    ],
  },
  {
    h: "Inhalte von Nutzern",
    blocks: [
      "Anzeigenamen, Teamnamen, Rundenzeiten und Telemetriedaten stammen von den jeweiligen Nutzern. Wir prüfen sie nicht vorab. Wenn dir ein rechtswidriger Inhalt auffällt, schreib uns bitte an die oben genannte E-Mail-Adresse — wir entfernen ihn nach Kenntnis umgehend.",
    ],
  },
  {
    h: "Links auf andere Seiten",
    blocks: [
      "Für die Inhalte verlinkter fremder Seiten (z. B. GitHub, Discord) sind ausschließlich deren Betreiber verantwortlich. Zum Zeitpunkt der Verlinkung waren keine Rechtsverstöße erkennbar; bei Bekanntwerden entfernen wir solche Links.",
    ],
  },
  {
    h: "Verbraucherstreitbeilegung",
    blocks: [
      "Wir sind nicht verpflichtet und nicht bereit, an Streitbeilegungsverfahren vor einer Verbraucherschlichtungsstelle teilzunehmen.",
    ],
  },
  {
    h: "Kein offizielles Produkt",
    blocks: [
      "Garage16 ist kein offizielles Produkt von Studio 397, Motorsport Games oder den Rechteinhabern von Le Mans Ultimate und steht mit ihnen in keiner Verbindung. Alle Marken, Fahrzeug- und Streckennamen gehören ihren jeweiligen Inhabern.",
    ],
  },
];

export const PRIVACY = [
  {
    h: "1. Wer ist verantwortlich?",
    blocks: [
      "Verantwortlich für die Verarbeitung deiner personenbezogenen Daten bei Garage16 im Sinne der Datenschutz-Grundverordnung (DSGVO) ist:",
      { operator: true },
      "Für alle Fragen zum Datenschutz und zur Ausübung deiner Rechte genügt eine formlose E-Mail an diese Adresse. Ein Datenschutzbeauftragter ist nicht benannt (keine gesetzliche Pflicht).",
    ],
  },
  {
    h: "2. Das Wichtigste in Kürze",
    blocks: [
      {
        ul: [
          "Wir speichern nur, was Garage16 zum Funktionieren braucht: dein Konto, deine Runden und deren Telemetrie.",
          "Keine Werbung, kein Tracking, keine Analyse-Dienste, keine Weitergabe oder gar Verkauf von Daten.",
          "Es wird nur ein technisch notwendiges Anmelde-Cookie gesetzt. Schriften und alle Skripte kommen vom eigenen Server.",
          "Dein Anzeigename und deine gültigen Rundenzeiten sind öffentlich in den Bestenlisten zu sehen — das ist der Kern von Garage16.",
          "Du kannst alle deine Daten jederzeit selbst herunterladen und dein Konto mit allen Runden selbst löschen (Konto → Daten & Datenschutz).",
        ],
      },
    ],
  },
  {
    h: "3. Wo die Daten liegen",
    blocks: [
      "Der Garage16-Server (Datenbank, Telemetriedateien, Sicherungen) läuft auf einem privat betriebenen Speichergerät (NAS) in Deutschland. Es gibt keinen externen Hosting-Anbieter, der Zugriff auf die Datenbank hat. Die Verbindung zwischen deinem Browser bzw. dem Desktop-Client und dem Server ist immer per HTTPS verschlüsselt.",
    ],
  },
  {
    h: "4. Welche Daten wir verarbeiten und warum",
    blocks: [
      {
        table: {
          head: ["Daten", "Zweck", "Rechtsgrundlage", "Speicherdauer"],
          rows: [
            ["Konto: Anzeigename, E-Mail-Adresse, Passwort (nur als Argon2-Hash, nie im Klartext), Bestätigungsstatus der E-Mail, Anlagedatum, Zustimmung zu diesen Bedingungen (Zeitpunkt, Fassung)",
              "Konto anlegen und anmelden, Passwort zurücksetzen, Bestenlisten, Nachweis der Zustimmung",
              "Art. 6 Abs. 1 lit. b DSGVO (Nutzungsvertrag); Zustimmungsnachweis: lit. c und f",
              "bis du dein Konto löschst"],
            ["Zugangsdaten des Desktop-Clients (Token nur als Hash, Client-Geheimnis verschlüsselt)",
              "Runden sicher hochladen und signieren",
              "Art. 6 Abs. 1 lit. b DSGVO",
              "bis zur Löschung oder Erneuerung"],
            ["Web-Sitzungen: Sitzungskennung (als Hash), Ablaufzeit, Browser-Kennung (User-Agent)",
              "angemeldet bleiben; du siehst und beendest deine Sitzungen unter Konto → Sitzungen",
              "Art. 6 Abs. 1 lit. b DSGVO",
              "bis Ablauf (30 Tage), Abmeldung oder Löschung"],
            ["Runden: Strecke, Fahrzeug, Klasse, Runden- und Sektorzeiten, Gültigkeit mit Begründung, Temperaturen, Kennzahlen, Client-Version",
              "Bestenlisten, Rekorde, Analyse, Prüfung auf Gültigkeit",
              "Art. 6 Abs. 1 lit. b DSGVO",
              "bis du dein Konto löschst"],
            ["Telemetrie jeder Runde (Eingaben wie Gas, Bremse, Lenkung; Geschwindigkeit; Position auf der Strecke; Fahrzeugdaten wie Reifen, Fahrwerk, Temperaturen) als unveränderte Originaldatei mit Prüfsumme und Server-Signatur",
              "LapTime Analyzer, Stint Analyzer, Rundenvergleich, Nachprüfbarkeit der Bestzeiten",
              "Art. 6 Abs. 1 lit. b DSGVO",
              "wie die Runde"],
            ["Teams: Mitgliedschaften, Rollen, Einladungen; Meldungen zu Runden; Rekord-Ereignisse",
              "Teamfunktionen, Moderation, Rekord-Ankündigungen",
              "Art. 6 Abs. 1 lit. b DSGVO",
              "bis Austritt bzw. Löschung des Kontos"],
            ["Discord-Nutzer-ID (nur wenn du Discord selbst verknüpfst)",
              "dich in Rekord-Ankündigungen erwähnen, Bot-Befehle wie /pb",
              "Art. 6 Abs. 1 lit. b DSGVO",
              "bis du die Verknüpfung löst oder das Konto löschst"],
            ["Upload-Protokoll: Zeit, Ergebnis, Grund, Client-Version, Dateigröße, Dauer (keine IP-Adresse, kein Inhalt)",
              "Fehlersuche, stabiler Betrieb",
              "Art. 6 Abs. 1 lit. f DSGVO (berechtigtes Interesse am sicheren Betrieb)",
              "30 Tage, dann automatisch gelöscht"],
            ["Prüfprotokoll (Audit): Zeit, Ereignis, Runden-ID, Prüfsumme und Prüfergebnis — keine Namen, E-Mails oder IP-Adressen",
              "Nachvollziehbarkeit von Prüf- und Moderationsentscheidungen, Erkennen von Manipulation",
              "Art. 6 Abs. 1 lit. f DSGVO (faire, überprüfbare Bestenlisten)",
              "365 Tage"],
            ["IP-Adresse",
              "Schutz vor Missbrauch (Begrenzung der Anfragen pro Zeitraum); Zugriffsprotokoll des Webservers (IP-Adresse, Zeit, aufgerufene Adresse, Statuscode, Browser-Kennung)",
              "Art. 6 Abs. 1 lit. f DSGVO (Sicherheit, Abwehr von Angriffen)",
              "Anfragebegrenzung: nur im Arbeitsspeicher für die Dauer des Zeitfensters (höchstens 1 Stunde); Zugriffsprotokoll: 7 Tage"],
            ["Sicherungskopien (alle Daten oben außer Protokollen)",
              "Wiederherstellung nach Defekt oder Fehler",
              "Art. 6 Abs. 1 lit. f DSGVO",
              "14 Tage"],
          ],
        },
      },
      "Eine Pflicht, uns Daten bereitzustellen, gibt es nicht. Ohne E-Mail-Adresse, Passwort und Anzeigenamen kann aber kein Konto angelegt werden, und ohne Telemetrie keine Runde gewertet werden.",
    ],
  },
  {
    h: "5. Wer was sehen kann",
    blocks: [
      {
        ul: [
          "Öffentlich, ohne Anmeldung: Bestenlisten (Anzeigename, Rundenzeit, Strecke, Fahrzeug, Datum), dein öffentliches Fahrerprofil (Anzahl Runden, Strecken, Rekorde, Teams), die letzten Aktivitäten und die Streckenbibliothek.",
          "Angemeldete Nutzer: zusätzlich die Telemetrie gültiger Runden, damit man sich im LapTime Analyzer mit anderen vergleichen kann.",
          "Teammitglieder: die Team-Seite mit Mitgliedern und Team-Bestzeiten.",
          "Nur du (und die Administratoren): deine E-Mail-Adresse, ungültige oder zurückgestellte Runden, Sitzungen, Upload-Protokoll.",
          "Administratoren: alles, was für Betrieb und Moderation nötig ist — aber nie dein Passwort.",
        ],
      },
      "Deine gültigen Rundenzeiten zu veröffentlichen ist der Zweck von Garage16. Wenn du das nicht möchtest, lade keine Runden hoch oder lösche dein Konto. Soll nur eine einzelne Runde aus den Bestenlisten verschwinden, schreib uns eine E-Mail.",
    ],
  },
  {
    h: "6. Automatische Prüfung der Runden",
    blocks: [
      "Jede hochgeladene Runde wird automatisch auf Plausibilität geprüft (u. a. Streckenbegrenzungen, Datenqualität, Urteil des Spiels). Das Ergebnis — gültig, ungültig oder zur Prüfung zurückgestellt — steht mit Begründung bei der Runde. Es entscheidet nur darüber, ob eine Runde in den Bestenlisten zählt, und hat keine rechtliche oder ähnlich erhebliche Wirkung im Sinne von Art. 22 DSGVO. Zurückgestellte Runden sieht sich ein Administrator an; du kannst eine Entscheidung jederzeit per E-Mail beanstanden.",
    ],
  },
  {
    h: "7. Der Desktop-Client",
    blocks: [
      "Der Windows-Client liest während des Fahrens die Telemetrie aus dem gemeinsamen Speicher (Shared Memory) von Le Mans Ultimate — sonst nichts von deinem PC. Fertige Runden speichert er in deinem Benutzerordner (%LOCALAPPDATA%\\Garage16), bis sie hochgeladen sind, und schreibt dort ein Protokoll zur Fehlersuche. Diese Dateien verlassen deinen PC nur als Runden-Upload; das Protokoll nur, wenn du es selbst jemandem schickst. Beim Start fragt der Client den Server nach der aktuellen Version.",
    ],
  },
  {
    h: "8. Cookies und Speicher im Browser",
    blocks: [
      "Wir setzen ausschließlich technisch notwendige Speicher ein, für die keine Einwilligung nötig ist (§ 25 Abs. 2 Nr. 2 TDDDG):",
      {
        table: {
          head: ["Name", "Art", "Zweck", "Dauer"],
          rows: [
            ["garage16_session", "Cookie (HttpOnly, Secure)", "hält dich angemeldet", "30 Tage oder bis zur Abmeldung"],
            ["lmu_garage_session", "Local Storage", "zeigt deinen Namen im Menü (keine Zugangsdaten)", "bis zur Abmeldung"],
            ["garage16_language", "Local Storage", "gewählte Sprache", "bis du sie löschst"],
            ["garage16_analysis_layout", "Local Storage", "deine Anordnung im LapTime Analyzer", "bis du sie löschst"],
            ["lmu_garage_admin_token", "Session Storage", "nur für Administratoren: Admin-Zugang in diesem Tab", "bis der Tab geschlossen wird"],
          ],
        },
      },
      "Es gibt keine Tracking-, Werbe- oder Analyse-Cookies und keine eingebundenen Inhalte fremder Anbieter (Schriftarten werden vom eigenen Server geladen).",
    ],
  },
  {
    h: "9. Dienstleister und Empfänger",
    blocks: [
      "Wir geben Daten nur an die folgenden Dienste weiter, und nur soweit es für den jeweiligen Zweck nötig ist:",
      {
        table: {
          head: ["Dienst", "Wofür", "Welche Daten", "Rechtsgrundlage"],
          rows: [
            ["Cloudflare, Inc., 101 Townsend St., San Francisco, CA 94107, USA",
              "macht den Server aus dem Internet erreichbar (Cloudflare Tunnel), ohne dass das Heimnetz geöffnet werden muss; schützt vor Angriffen. Der verschlüsselte Datenverkehr läuft über Cloudflare.",
              "alle Anfragen an den Server, inkl. IP-Adresse und Browser-Kennung",
              "Art. 6 Abs. 1 lit. f DSGVO (sichere Erreichbarkeit)"],
            ["ALL-INKL.COM – Neue Medien Münnich, Hauptstraße 68, 02742 Friedersdorf, Deutschland",
              "Domain garage16.de und Webspace für die Website",
              "beim Aufruf der Website: IP-Adresse, Zeit, aufgerufene Seite, Browser-Kennung (Server-Protokolle des Anbieters)",
              "Art. 6 Abs. 1 lit. f DSGVO"],
            ["GitHub, Inc., 88 Colin P. Kelly Jr. St., San Francisco, CA 94107, USA",
              "Bereitstellung der Installationsdateien des Desktop-Clients",
              "beim Download: IP-Adresse und Browser-Kennung (Garage16 selbst erfährt davon nichts)",
              "Art. 6 Abs. 1 lit. f DSGVO"],
            ["Google Ireland Limited, Gordon House, Barrow Street, Dublin 4, Irland (Gmail)",
              "Versand der E-Mails zur Bestätigung der Adresse und zum Zurücksetzen des Passworts",
              "deine E-Mail-Adresse und der Inhalt dieser E-Mails",
              "Art. 6 Abs. 1 lit. b DSGVO"],
            ["Discord Inc., 444 De Haro Street, Suite 200, San Francisco, CA 94107, USA",
              "Rekord-Ankündigungen in Discord-Kanälen, in denen der Garage16-Bot eingerichtet ist; Bot-Befehle",
              "Anzeigename, Strecke, Fahrzeug, Rundenzeit, Art des Rekords; deine Discord-ID nur, wenn du verknüpft hast",
              "Art. 6 Abs. 1 lit. b und f DSGVO"],
          ],
        },
      },
      "Bei Cloudflare, GitHub, Google und Discord kann eine Übermittlung in die USA stattfinden. Sie stützt sich auf den Angemessenheitsbeschluss der EU-Kommission zum EU-US Data Privacy Framework vom 10. Juli 2023, soweit der jeweilige Anbieter danach zertifiziert ist, und im Übrigen auf die Standardvertragsklauseln der EU-Kommission, die diese Anbieter verwenden.",
      "Bereits in Discord gepostete Nachrichten liegen bei Discord und werden durch die Löschung deines Garage16-Kontos nicht automatisch entfernt — Team-Ankündigungen kann ein Team-Owner abschalten.",
    ],
  },
  {
    h: "10. Löschen und Herunterladen",
    blocks: [
      "Unter Konto → Daten & Datenschutz lädst du alle Daten, die die Datenbank über dich enthält, als JSON-Datei herunter, und löschst dein Konto. Die Löschung entfernt sofort und unwiderruflich dein Konto, alle Runden mit ihren Telemetriedateien, Rekorde, Meldungen, Teammitgliedschaften, Einladungen, Sitzungen und dein Upload-Protokoll. Im Prüfprotokoll wird der Bezug zu dir entfernt. Grenzen: Sicherungskopien enthalten die Daten noch bis zu 14 Tage; Server-Protokolle verschwinden mit ihrer Rotation; Discord-Nachrichten siehe oben.",
    ],
  },
  {
    h: "11. Deine Rechte",
    blocks: [
      "Du hast das Recht auf Auskunft (Art. 15 DSGVO), Berichtigung (Art. 16), Löschung (Art. 17), Einschränkung der Verarbeitung (Art. 18), Datenübertragbarkeit (Art. 20) und Widerspruch gegen Verarbeitungen auf Grundlage berechtigter Interessen (Art. 21). Vieles davon kannst du direkt im Konto selbst erledigen; für alles andere genügt eine E-Mail an den Verantwortlichen.",
      "Außerdem kannst du dich bei einer Datenschutz-Aufsichtsbehörde beschweren (Art. 77 DSGVO), insbesondere in dem Land bzw. Bundesland, in dem du wohnst, oder in dem der Verantwortliche seinen Sitz hat.",
    ],
  },
  {
    h: "12. Alter",
    blocks: [
      "Garage16 ist erst ab {min_age} Jahren nutzbar. Bei der Registrierung bestätigst du, dass du mindestens {min_age} Jahre alt bist. Erfahren wir, dass ein Konto von einer jüngeren Person stammt, löschen wir es.",
    ],
  },
  {
    h: "13. Sicherheit",
    blocks: [
      "Passwörter werden nur als Argon2-Hash gespeichert, Zugangsdaten des Clients nur gehasht bzw. verschlüsselt, jede Verbindung ist per HTTPS verschlüsselt, gespeicherte Runden sind signiert, und der Zugang zum Server ist auf den Betreiber beschränkt. Eine absolute Sicherheit kann trotzdem niemand garantieren.",
    ],
  },
  {
    h: "14. Änderungen",
    blocks: [
      "Wenn sich Garage16 oder die genutzten Dienste ändern, passen wir diese Erklärung an. Es gilt die jeweils hier veröffentlichte Fassung. Stand: {date}.",
    ],
  },
];

export const TERMS = [
  {
    h: "1. Worum es geht",
    blocks: [
      "Diese Bedingungen regeln die Nutzung von Garage16 (Website, Server und Desktop-Client) zwischen dir und dem im Impressum genannten Betreiber. Garage16 ist ein kostenloses, privat betriebenes Hobbyprojekt: Es zeichnet deine Runden in Le Mans Ultimate auf, wertet sie aus und zeigt Bestenlisten.",
    ],
  },
  {
    h: "2. Konto",
    blocks: [
      {
        ul: [
          "Du musst mindestens {min_age} Jahre alt sein.",
          "Pro Person ein Konto. Deine Angaben müssen stimmen, insbesondere deine E-Mail-Adresse.",
          "Halte Passwort und Zugangsdaten des Clients geheim. Wenn du vermutest, dass jemand sie kennt, ändere das Passwort bzw. erneuere die Zugangsdaten im Konto.",
          "Mit der Registrierung akzeptierst du diese Bedingungen und bestätigst, die Datenschutzerklärung gelesen zu haben.",
        ],
      },
    ],
  },
  {
    h: "3. Anzeigename und Inhalte",
    blocks: [
      "Dein Anzeigename und deine Teamnamen dürfen nicht beleidigend, diskriminierend, sexuell anstößig oder sonst rechtswidrig sein und keine andere Person, kein Team und keine Marke vortäuschen. Solche Namen dürfen wir ändern oder das Konto sperren.",
      "Du räumst dem Betreiber das einfache, unentgeltliche Recht ein, deinen Anzeigenamen, deine Rundenzeiten und die Telemetrie deiner Runden innerhalb von Garage16 zu speichern und anzuzeigen — in Bestenlisten, Profilen, Team-Seiten, in der Analyse anderer angemeldeter Nutzer und in Rekord-Ankündigungen auf Discord. Das Recht endet mit der Löschung deines Kontos (Ausnahmen: Sicherungskopien bis 14 Tage, bereits gepostete Discord-Nachrichten).",
    ],
  },
  {
    h: "4. Fair Play",
    blocks: [
      "Bestenlisten sind nur etwas wert, wenn alle ehrlich fahren. Nicht erlaubt ist deshalb:",
      {
        ul: [
          "manipulierte, erfundene oder fremde Runden und Telemetriedaten hochzuladen oder den Client bzw. seine Daten zu verändern,",
          "Cheats, Hilfsprogramme oder Spielfehler zu nutzen, um sich Rundenzeiten zu verschaffen,",
          "mehrere Konten zu führen, Konten zu teilen oder für andere zu fahren,",
          "den Server anzugreifen, zu überlasten, Sicherheitsmechanismen zu umgehen oder ihn automatisiert abzufragen (Ausnahme: der offizielle Client),",
          "andere Nutzer zu belästigen.",
        ],
      },
      "Runden werden automatisch geprüft (siehe Datenschutzerklärung, Abschnitt 6). Administratoren können Runden für ungültig erklären oder zurückstellen und Konten bei Verstößen sperren oder löschen. Ein Anspruch auf eine bestimmte Wertung besteht nicht; Beanstandungen bitte per E-Mail.",
    ],
  },
  {
    h: "5. Verfügbarkeit",
    blocks: [
      "Garage16 läuft auf privater Hardware über einen privaten Internetanschluss. Es gibt keinen Anspruch darauf, dass der Dienst jederzeit erreichbar ist, dass bestimmte Funktionen erhalten bleiben oder dass er dauerhaft betrieben wird. Wartung, Ausfälle und Änderungen sind jederzeit möglich. Soll Garage16 eingestellt werden, kündigen wir das nach Möglichkeit vorher an, damit du deine Daten herunterladen kannst. Runden, die der Client nicht hochladen konnte, bleiben auf deinem PC und werden später nachgeladen.",
    ],
  },
  {
    h: "6. Haftung",
    blocks: [
      "Garage16 wird unentgeltlich bereitgestellt. Der Betreiber haftet deshalb nur für Vorsatz und grobe Fahrlässigkeit. Unberührt bleibt die Haftung für Schäden aus der Verletzung des Lebens, des Körpers oder der Gesundheit, nach dem Produkthaftungsgesetz und für arglistig verschwiegene Mängel. Der Desktop-Client wird so bereitgestellt, wie er ist; er liest nur Daten des Spiels und verändert es nicht.",
    ],
  },
  {
    h: "7. Kein offizielles Produkt",
    blocks: [
      "Garage16 ist kein offizielles Produkt von Studio 397, Motorsport Games oder den Rechteinhabern von Le Mans Ultimate und wird von ihnen weder unterstützt noch geprüft. Alle Marken, Fahrzeug- und Streckennamen gehören ihren jeweiligen Inhabern und werden nur zur Bezeichnung verwendet.",
    ],
  },
  {
    h: "8. Ende der Nutzung",
    blocks: [
      "Du kannst dein Konto jederzeit selbst löschen (Konto → Daten & Datenschutz). Der Betreiber kann Konten bei Verstößen gegen diese Bedingungen sperren oder löschen und die Nutzung im Übrigen mit angemessener Frist beenden.",
    ],
  },
  {
    h: "9. Änderungen dieser Bedingungen",
    blocks: [
      "Wir können diese Bedingungen ändern, wenn Garage16 sich weiterentwickelt oder die Rechtslage es erfordert. Über wesentliche Änderungen informieren wir dich vorab per E-Mail oder deutlich sichtbar auf der Website. Bist du nicht einverstanden, kannst du dein Konto jederzeit löschen.",
    ],
  },
  {
    h: "10. Recht",
    blocks: [
      "Es gilt deutsches Recht. Zwingende Verbraucherschutzvorschriften des Staates, in dem du wohnst, bleiben unberührt. Sollte eine Bestimmung unwirksam sein, bleiben die übrigen wirksam. Stand: {date}.",
    ],
  },
];

export const LICENSES = [
  {
    h: "Garage16",
    blocks: [
      "Der Quellcode von Garage16 ist auf GitHub veröffentlicht. Dort stehen auch die vollständigen Hinweise zu Drittanbietern (THIRD_PARTY_NOTICES.md).",
      { links: [["Garage16 auf GitHub", "https://github.com/ry2en-X/garage16-lmu"], ["THIRD_PARTY_NOTICES.md", "https://github.com/ry2en-X/garage16-lmu/blob/HEAD/THIRD_PARTY_NOTICES.md"]] },
    ],
  },
  {
    h: "Schriftarten",
    blocks: [
      "Oswald (Copyright 2016 The Oswald Project Authors) und IBM Plex Mono (Copyright 2017 IBM Corp.), beide unter der SIL Open Font License 1.1, unverändert und vom eigenen Server ausgeliefert.",
      { links: [["Lizenztext Oswald", "fonts/OFL-Oswald.txt"], ["Lizenztext IBM Plex Mono", "fonts/OFL-IBM-Plex-Mono.txt"]] },
    ],
  },
  {
    h: "Vorbilder und Fakten",
    blocks: [
      {
        ul: [
          "ApexDash (MIT, Copyright 2026 ApexDash Contributors) — Vorbild für den Funktionsumfang der Rundenanalyse. Es wurde kein Code übernommen.",
          "LMU Pitwall (MIT, Copyright 2025 Swizzjack), pyLMUSharedMemory (MIT, Copyright 2021 Tony Whitley, 2025 Xiang) und goLMUSharedMemory (MIT) — Aufbau des Shared Memory von Le Mans Ultimate (nur Fakten).",
          "TinyPedal (GPL-3.0) — nur gelesen; einzelne Fakten über die LMU-Schnittstelle, kein Code.",
        ],
      },
    ],
  },
  {
    h: "Nur im „Client + Analysetool“",
    blocks: [
      "pywebview (BSD-3-Clause), pythonnet und clr_loader (MIT), bottle und proxy_tools (MIT), Microsoft Edge WebView2 SDK (Microsoft-Lizenz, weiterverteilbar). Die Lizenztexte liegen in den installierten Paketen.",
    ],
  },
];
