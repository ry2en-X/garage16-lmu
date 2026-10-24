// legal/en.js — legal notice, privacy policy, terms of use, licences (V1.0), English.
// Same structure and content as legal/de.js (see there). The German version is the binding one;
// this is a translation for convenience (said on the page).

export const DATE = "24 October 2026";

export const LABELS = {
  title: "Legal",
  intro: "Garage16 is a private, non-commercial hobby project. This is who runs it, what happens to your data and which rules apply.",
  tabs: { imprint: "Legal notice", privacy: "Privacy", terms: "Terms of use", licenses: "Licences" },
  operatorMissing: "The operator's details have not been entered on this server yet (LMU_GARAGE_OPERATOR_NAME / _ADDRESS / _EMAIL in .env).",
  operatorError: "The operator's details could not be loaded right now. Please try again later.",
  email: "Email",
  asOf: "As of",
  binding: "Translation for convenience — the German version is the binding one.",
};

export const IMPRINT = [
  { h: "Information pursuant to § 5 of the German Digital Services Act (DDG)", blocks: [{ operator: true }] },
  {
    h: "What this is",
    blocks: ["Garage16 is a privately run, non-commercial hobby project for drivers of the racing simulation Le Mans Ultimate. There is no advertising, nothing to pay for and no intention to make a profit."],
  },
  {
    h: "Content from users",
    blocks: ["Driver names, team names, lap times and telemetry come from the users themselves and are not checked beforehand. If you come across unlawful content, please email the address above — we remove it promptly once we know about it."],
  },
  {
    h: "Links to other sites",
    blocks: ["Only their operators are responsible for the content of linked external sites (e.g. GitHub, Discord). No infringements were apparent when they were linked; we remove such links once we learn of any."],
  },
  {
    h: "Consumer dispute resolution",
    blocks: ["We are neither obliged nor willing to take part in dispute resolution proceedings before a consumer arbitration board."],
  },
  {
    h: "Not an official product",
    blocks: ["Garage16 is not an official product of Studio 397, Motorsport Games or the rights holders of Le Mans Ultimate and is not affiliated with them. All trademarks, car and track names belong to their respective owners."],
  },
];

export const PRIVACY = [
  {
    h: "1. Who is responsible?",
    blocks: [
      "The controller of your personal data at Garage16 within the meaning of the General Data Protection Regulation (GDPR) is:",
      { operator: true },
      "For any question about privacy or to exercise your rights, an informal email to this address is enough. No data protection officer has been appointed (not required by law).",
    ],
  },
  {
    h: "2. The essentials",
    blocks: [
      {
        ul: [
          "We only store what Garage16 needs to work: your account, your laps and their telemetry.",
          "No advertising, no tracking, no analytics services, no passing on — let alone selling — of data.",
          "Only one technically necessary sign-in cookie is set. Fonts and all scripts come from our own server.",
          "Your driver name and your valid lap times are publicly visible in the leaderboards — that is the core of Garage16.",
          "You can download all your data and delete your account with all its laps yourself at any time (Account → Data & Privacy).",
        ],
      },
    ],
  },
  {
    h: "3. Where the data is",
    blocks: ["The Garage16 server (database, telemetry files, backups) runs on a privately operated storage device (NAS) in Germany. No external hosting provider has access to the database. The connection between your browser or the desktop client and the server is always encrypted with HTTPS."],
  },
  {
    h: "4. Which data we process and why",
    blocks: [
      {
        table: {
          head: ["Data", "Purpose", "Legal basis", "Kept for"],
          rows: [
            ["Account: driver name, email address, password (only as an Argon2 hash, never in plain text), email confirmation status, creation date, acceptance of these terms (time, version)",
              "create the account and sign in, reset the password, leaderboards, proof of acceptance",
              "Art. 6(1)(b) GDPR (contract of use); proof of acceptance: (c) and (f)", "until you delete your account"],
            ["Desktop client credentials (token only as a hash, client secret encrypted)", "upload and sign laps securely", "Art. 6(1)(b) GDPR", "until deleted or renewed"],
            ["Web sessions: session id (as a hash), expiry, browser identifier (user agent)", "stay signed in; you see and end your sessions under Account → Sessions", "Art. 6(1)(b) GDPR", "until expiry (30 days), sign-out or deletion"],
            ["Laps: track, car, class, lap and sector times, validity with reason, temperatures, statistics, client version", "leaderboards, records, analysis, validity check", "Art. 6(1)(b) GDPR", "until you delete your account"],
            ["Telemetry of every lap (inputs such as throttle, brake, steering; speed; position on track; car data such as tyres, suspension, temperatures) as the unchanged original file with checksum and server signature",
              "LapTime Analyzer, Stint Analyzer, lap comparison, verifiable records", "Art. 6(1)(b) GDPR", "like the lap"],
            ["Teams: memberships, roles, invitations; reports about laps; record events", "team features, moderation, record announcements", "Art. 6(1)(b) GDPR", "until you leave or delete your account"],
            ["Discord user id (only if you link Discord yourself)", "mention you in record announcements, bot commands like /pb", "Art. 6(1)(b) GDPR", "until you unlink or delete your account"],
            ["Upload log: time, outcome, reason, client version, file size, duration (no IP address, no content)", "troubleshooting, stable operation", "Art. 6(1)(f) GDPR (legitimate interest in secure operation)", "30 days, then deleted automatically"],
            ["Audit log: time, event, lap id, checksum and check result — no names, emails or IP addresses", "traceable check and moderation decisions, detecting tampering", "Art. 6(1)(f) GDPR (fair, verifiable leaderboards)", "365 days"],
            ["IP address", "abuse protection (limiting requests per time window); access log of the web server (IP address, time, requested address, status code, browser identifier)",
              "Art. 6(1)(f) GDPR (security, defence against attacks)", "rate limiting: only in memory for the length of the window (at most 1 hour); access log: 7 days"],
            ["Backups (all data above except logs)", "recovery after a defect or error", "Art. 6(1)(f) GDPR", "14 days"],
          ],
        },
      },
      "You are not obliged to provide any data. Without an email address, password and driver name no account can be created, though, and without telemetry no lap can be scored.",
    ],
  },
  {
    h: "5. Who can see what",
    blocks: [
      {
        ul: [
          "Public, without signing in: leaderboards (driver name, lap time, track, car, date), your public driver profile (number of laps, tracks, records, teams), recent activity and the circuit library.",
          "Signed-in users: additionally the telemetry of valid laps, so that drivers can compare themselves with others in the LapTime Analyzer.",
          "Team members: the team page with members and team bests.",
          "Only you (and the administrators): your email address, invalid or held laps, sessions, upload log.",
          "Administrators: whatever operation and moderation require — but never your password.",
        ],
      },
      "Publishing your valid lap times is the purpose of Garage16. If you do not want that, do not upload laps, or delete your account. If only a single lap should disappear from the leaderboards, email us.",
    ],
  },
  {
    h: "6. Automatic lap check",
    blocks: ["Every uploaded lap is checked automatically for plausibility (track limits, data quality, the game's own verdict, among others). The result — valid, invalid or held for review — is shown with its reason next to the lap. It only decides whether a lap counts in the leaderboards and has no legal or similarly significant effect within the meaning of Art. 22 GDPR. An administrator looks at held laps; you can contest a decision by email at any time."],
  },
  {
    h: "7. The desktop client",
    blocks: ["While you drive, the Windows client reads the telemetry from Le Mans Ultimate's shared memory — nothing else on your PC. It keeps finished laps in your user folder (%LOCALAPPDATA%\\Garage16) until they are uploaded and writes a log for troubleshooting there. These files only leave your PC as a lap upload; the log only if you send it to somebody yourself. On start the client asks the server for the current version."],
  },
  {
    h: "8. Cookies and browser storage",
    blocks: [
      "We only use technically necessary storage, which needs no consent (§ 25(2) no. 2 TDDDG):",
      {
        table: {
          head: ["Name", "Type", "Purpose", "Duration"],
          rows: [
            ["garage16_session", "Cookie (HttpOnly, Secure)", "keeps you signed in", "30 days or until sign-out"],
            ["lmu_garage_session", "Local storage", "shows your name in the menu (no credentials)", "until sign-out"],
            ["garage16_language", "Local storage", "chosen language", "until you delete it"],
            ["garage16_analysis_layout", "Local storage", "your arrangement in the LapTime Analyzer", "until you delete it"],
            ["lmu_garage_admin_token", "Session storage", "administrators only: admin access in this tab", "until the tab is closed"],
          ],
        },
      },
      "There are no tracking, advertising or analytics cookies and no embedded content from other providers (fonts are loaded from our own server).",
    ],
  },
  {
    h: "9. Service providers and recipients",
    blocks: [
      "We only pass data to the following services, and only as far as the respective purpose requires:",
      {
        table: {
          head: ["Service", "What for", "Which data", "Legal basis"],
          rows: [
            ["Cloudflare, Inc., 101 Townsend St., San Francisco, CA 94107, USA", "makes the server reachable from the internet (Cloudflare Tunnel) without opening the home network; protects against attacks. The encrypted traffic passes through Cloudflare.",
              "all requests to the server, incl. IP address and browser identifier", "Art. 6(1)(f) GDPR (secure reachability)"],
            ["ALL-INKL.COM – Neue Medien Münnich, Hauptstraße 68, 02742 Friedersdorf, Germany", "domain garage16.de and web space for the website",
              "when the website is visited: IP address, time, page, browser identifier (the provider's server logs)", "Art. 6(1)(f) GDPR"],
            ["GitHub, Inc., 88 Colin P. Kelly Jr. St., San Francisco, CA 94107, USA", "hosting the desktop client's installers",
              "on download: IP address and browser identifier (Garage16 itself learns nothing of it)", "Art. 6(1)(f) GDPR"],
            ["Google Ireland Limited, Gordon House, Barrow Street, Dublin 4, Ireland (Gmail)", "sending the emails that confirm your address and reset your password",
              "your email address and the content of these emails", "Art. 6(1)(b) GDPR"],
            ["Discord Inc., 444 De Haro Street, Suite 200, San Francisco, CA 94107, USA", "record announcements in Discord channels where the Garage16 bot is set up; bot commands",
              "driver name, track, car, lap time, record type; your Discord id only if you linked it", "Art. 6(1)(b) and (f) GDPR"],
          ],
        },
      },
      "With Cloudflare, GitHub, Google and Discord, data may be transferred to the USA. Such transfers rely on the European Commission's adequacy decision on the EU-US Data Privacy Framework of 10 July 2023 where the provider is certified under it, and otherwise on the European Commission's standard contractual clauses used by these providers.",
      "Messages already posted in Discord stay with Discord and are not removed automatically when you delete your Garage16 account — a team owner can switch team announcements off.",
    ],
  },
  {
    h: "10. Deleting and downloading",
    blocks: ["Under Account → Data & Privacy you download everything the database holds about you as a JSON file, and delete your account. Deletion immediately and irrevocably removes your account, all laps with their telemetry files, records, reports, team memberships, invitations, sessions and your upload log. In the audit log the reference to you is removed. Limits: backups still contain the data for up to 14 days; server logs disappear with their rotation; Discord messages see above."],
  },
  {
    h: "11. Your rights",
    blocks: [
      "You have the right of access (Art. 15 GDPR), rectification (Art. 16), erasure (Art. 17), restriction of processing (Art. 18), data portability (Art. 20) and to object to processing based on legitimate interests (Art. 21). Much of it you can do yourself in your account; for everything else an email to the controller is enough.",
      "You can also lodge a complaint with a data protection supervisory authority (Art. 77 GDPR), in particular in the country or German state where you live or where the controller is based.",
    ],
  },
  {
    h: "12. Age",
    blocks: ["Garage16 may only be used from the age of {min_age}. When you sign up you confirm that you are at least {min_age} years old. If we learn that an account belongs to someone younger, we delete it."],
  },
  {
    h: "13. Security",
    blocks: ["Passwords are only stored as Argon2 hashes, client credentials only hashed or encrypted, every connection is encrypted with HTTPS, stored laps are signed, and access to the server is restricted to the operator. Nobody can guarantee absolute security, though."],
  },
  {
    h: "14. Changes",
    blocks: ["When Garage16 or the services it uses change, we update this policy. The version published here applies. As of {date}."],
  },
];

export const TERMS = [
  {
    h: "1. What this is about",
    blocks: ["These terms govern the use of Garage16 (website, server and desktop client) between you and the operator named in the legal notice. Garage16 is a free, privately run hobby project: it records your laps in Le Mans Ultimate, analyses them and shows leaderboards."],
  },
  {
    h: "2. Account",
    blocks: [
      {
        ul: [
          "You must be at least {min_age} years old.",
          "One account per person. Your details must be correct, your email address in particular.",
          "Keep your password and the client's credentials secret. If you suspect somebody knows them, change the password or renew the credentials in your account.",
          "By signing up you accept these terms and confirm that you have read the privacy policy.",
        ],
      },
    ],
  },
  {
    h: "3. Driver name and content",
    blocks: [
      "Your driver name and team names must not be insulting, discriminatory, sexually offensive or otherwise unlawful, and must not impersonate another person, team or brand. We may change such names or lock the account.",
      "You grant the operator the simple, free right to store and show your driver name, your lap times and the telemetry of your laps within Garage16 — in leaderboards, profiles, team pages, in other signed-in users' analysis and in record announcements on Discord. The right ends when you delete your account (exceptions: backups for up to 14 days, Discord messages already posted).",
    ],
  },
  {
    h: "4. Fair play",
    blocks: [
      "Leaderboards are only worth something if everybody drives honestly. Therefore it is not allowed to:",
      {
        ul: [
          "upload manipulated, invented or other people's laps and telemetry, or modify the client or its data,",
          "use cheats, helper programs or game bugs to obtain lap times,",
          "keep several accounts, share accounts or drive for others,",
          "attack or overload the server, bypass security mechanisms or query it automatically (except with the official client),",
          "harass other users.",
        ],
      },
      "Laps are checked automatically (see the privacy policy, section 6). Administrators can declare laps invalid or hold them, and lock or delete accounts that break these rules. There is no entitlement to a particular verdict; please email any objection.",
    ],
  },
  {
    h: "5. Availability",
    blocks: ["Garage16 runs on private hardware over a private internet connection. There is no entitlement to the service being reachable at all times, to particular features being kept or to it being run permanently. Maintenance, outages and changes are possible at any time. Should Garage16 be discontinued, we will announce it beforehand where possible so that you can download your data. Laps the client could not upload stay on your PC and are uploaded later."],
  },
  {
    h: "6. Liability",
    blocks: ["Garage16 is provided free of charge. The operator is therefore only liable for intent and gross negligence. Liability for injury to life, body or health, under the German Product Liability Act and for fraudulently concealed defects remains unaffected. The desktop client is provided as is; it only reads data from the game and does not modify it."],
  },
  {
    h: "7. Not an official product",
    blocks: ["Garage16 is not an official product of Studio 397, Motorsport Games or the rights holders of Le Mans Ultimate and is neither endorsed nor reviewed by them. All trademarks, car and track names belong to their respective owners and are only used to name things."],
  },
  {
    h: "8. End of use",
    blocks: ["You can delete your account yourself at any time (Account → Data & Privacy). The operator can lock or delete accounts that break these terms and can otherwise end the use with reasonable notice."],
  },
  {
    h: "9. Changes to these terms",
    blocks: ["We may change these terms when Garage16 evolves or the law requires it. We inform you of material changes beforehand by email or clearly visible on the website. If you do not agree, you can delete your account at any time."],
  },
  {
    h: "10. Law",
    blocks: ["German law applies. Mandatory consumer protection rules of the country you live in remain unaffected. Should a provision be invalid, the others remain valid. As of {date}."],
  },
];

export const LICENSES = [
  {
    h: "Garage16",
    blocks: [
      "Garage16's source code is published on GitHub, together with the complete third-party notices (THIRD_PARTY_NOTICES.md).",
      { links: [["Garage16 on GitHub", "https://github.com/ry2en-X/garage16-lmu"], ["THIRD_PARTY_NOTICES.md", "https://github.com/ry2en-X/garage16-lmu/blob/HEAD/THIRD_PARTY_NOTICES.md"]] },
    ],
  },
  {
    h: "Fonts",
    blocks: [
      "Oswald (Copyright 2016 The Oswald Project Authors) and IBM Plex Mono (Copyright 2017 IBM Corp.), both under the SIL Open Font License 1.1, unmodified and served from our own server.",
      { links: [["Licence text Oswald", "fonts/OFL-Oswald.txt"], ["Licence text IBM Plex Mono", "fonts/OFL-IBM-Plex-Mono.txt"]] },
    ],
  },
  {
    h: "Inspiration and facts",
    blocks: [
      {
        ul: [
          "ApexDash (MIT, Copyright 2026 ApexDash Contributors) — model for the lap analysis' feature set. No code was taken.",
          "LMU Pitwall (MIT, Copyright 2025 Swizzjack), pyLMUSharedMemory (MIT, Copyright 2021 Tony Whitley, 2025 Xiang) and goLMUSharedMemory (MIT) — layout of Le Mans Ultimate's shared memory (facts only).",
          "TinyPedal (GPL-3.0) — read only; individual facts about the LMU interface, no code.",
        ],
      },
    ],
  },
  {
    h: "Only in “Client + analysis app”",
    blocks: ["pywebview (BSD-3-Clause), pythonnet and clr_loader (MIT), bottle and proxy_tools (MIT), Microsoft Edge WebView2 SDK (Microsoft licence, redistributable). The licence texts are inside the installed packages."],
  },
];
