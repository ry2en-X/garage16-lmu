// pages/setup.js — the step-by-step guide from "no account" to "first lap on the leaderboard"
// (V1.0). Public. The sign-up form sends new drivers here (#/setup?welcome=1), the download page
// links here, and the desktop client's connect window opens it ("How do I set this up?").

import { api } from "../api.js";
import { store } from "../state.js";
import { el } from "../format.js";
import { t } from "../i18n.js";

const safeLink = (url) => (typeof url === "string" && /^https:\/\/[^\s]+$/i.test(url) ? url : null);

function step(number, title, children, { done = false } = {}) {
  return el("section", { class: `card setup-step${done ? " setup-step--done" : ""}`, "data-step": String(number) }, [
    el("h2", {}, [el("span", { class: "setup-step__num" }, done ? "✓" : String(number)), title]),
    ...[].concat(children),
  ]);
}

const p = (text) => el("p", {}, text);
// the address this page was loaded from IS the server's address (web app and API share it)
const serverAddress = () => (typeof window !== "undefined" && window.location && /^https?:/.test(window.location.origin) ? window.location.origin : "https://garage16.de");
const list = (items) => el("ol", { class: "setup-list" }, items.map((i) => el("li", {}, i)));

export async function renderSetup(container) {
  const query = new URLSearchParams(window.location.hash.split("?")[1] || "");
  const signedIn = store.isSignedIn();

  container.appendChild(el("h1", {}, t("Set up Garage16")));
  container.appendChild(el("p", { class: "subtitle" },
    t("From zero to your first lap on the leaderboard in about five minutes. You need Windows 10 or 11 and Le Mans Ultimate.")));
  if (query.get("welcome") === "1") {
    container.appendChild(el("div", { class: "notice notice--success" },
      t("Your account is ready. We have sent you an email to confirm your address — please click the link in it within 48 hours (check the spam folder too).")));
  }

  let health = {};
  try {
    health = await api.health();
  } catch {
    // the guide works without it; the download step then points at the download page
  }
  const links = {
    standard: safeLink(health.client_download_url),
    analysis: safeLink(health.client_app_download_url),
    all: safeLink(health.client_release_page_url),
  };

  container.appendChild(step(1, t("Create your account"), signedIn ? [
    p(t("Done — you are signed in.")),
  ] : [
    p(t("On the Account page, under \"New driver\": choose a driver name, enter your email and a password, confirm your age and the terms.")),
    el("a", { class: "btn", href: "#/account" }, t("Create account")),
  ], { done: signedIn }));

  const downloadButtons = el("div", { class: "inline-form setup-downloads" }, [
    links.standard
      ? el("a", { class: "btn", href: links.standard, rel: "noopener", "data-download": "standard" }, t("Download Client"))
      : el("a", { class: "btn", href: "#/download" }, t("Go to the download page")),
    links.analysis
      ? el("a", { class: "btn btn--ghost", href: links.analysis, rel: "noopener", "data-download": "analysis" }, t("Download Client + analysis app"))
      : null,
  ]);
  container.appendChild(step(2, t("Download the desktop client"), [
    p(t("The client records your laps while you drive and uploads them. Choose one of the two:")),
    el("ul", {}, [
      el("li", {}, [el("strong", {}, t("Client")), " — ", t("the analysis opens in your browser. The smallest download.")]),
      el("li", {}, [el("strong", {}, t("Client + analysis app")), " — ", t("the analysis also opens in its own window, without a browser.")]),
    ]),
    downloadButtons,
    links.all ? el("p", { class: "subtitle" }, [
      t("Download does not start? "),
      el("a", { href: links.all, target: "_blank", rel: "noopener" }, t("All downloads on GitHub")),
      t(" (Garage16-Client-Setup.exe or Garage16-Client-Analysis-Setup.exe)."),
    ]) : null,
  ]));

  container.appendChild(step(3, t("Install"), [
    list([
      t("Double-click the downloaded Garage16-Client-Setup.exe. No administrator rights are needed."),
      t("Windows shows \"Windows protected your PC\"? That is normal for new programs that are not digitally signed: click \"More info\", then \"Run anyway\"."),
      t("Click through the installer. Tick \"Start Garage16 automatically when I log in to Windows\" if you like — then you never have to think about it again."),
    ]),
  ]));

  container.appendChild(step(4, t("Connect the client to your account"), [
    list([
      t("Start Garage16 (start menu or desktop). The first time, the window \"Connect your account\" opens."),
      t("The server address should already be filled in: {0} — otherwise enter exactly this.", [serverAddress()]),
      t("Enter the email and password of your Garage16 account and click \"Connect\"."),
    ]),
    el("div", { class: "notice" },
      t("One PC at a time: connecting Garage16 on a second PC disconnects the first one. It then shows \"Reconnect account…\" — just sign in there again when you switch back.")),
  ]));

  container.appendChild(step(5, t("Start Le Mans Ultimate and drive"), [
    list([
      t("Start Le Mans Ultimate as usual — the order does not matter, Garage16 waits for it."),
      t("The Garage16 window shows \"Connected to LMU shared memory\" as soon as you are on track."),
      t("Drive. Every completed lap is uploaded automatically: green = valid, red = invalid (for example track limits)."),
      t("No internet for a moment? Your laps are kept on your PC and uploaded later."),
    ]),
  ]));

  container.appendChild(step(6, t("See your times"), [
    el("div", { class: "inline-form" }, [
      el("a", { class: "btn btn--ghost", href: "#/dashboard" }, t("Dashboard")),
      el("a", { class: "btn btn--ghost", href: "#/leaderboard" }, t("Leaderboard")),
      el("a", { class: "btn btn--ghost", href: "#/analysis" }, t("LapTime Analyzer")),
      el("a", { class: "btn btn--ghost", href: "#/teams" }, t("Teams")),
    ]),
  ]));

  container.appendChild(el("h2", {}, t("If something does not work")));
  const problems = [
    [t("\"Invalid email or password\" in the client"), t("Use exactly the email and password you signed up with on the website. Forgot the password? Account → Sign in → \"Forgot password?\".")],
    [t("\"Couldn't reach that server\""), t("Check your internet connection and that the server address is {0}.", [serverAddress()])],
    [t("The window stays at \"Waiting for LMU to start...\""), t("Le Mans Ultimate is not running yet, or its shared memory is switched off in the game settings.")],
    [t("No confirmation email"), t("Look in the spam folder. On the Account page you can send it again.")],
    [t("A lap is red / invalid"), t("Garage16 checks every lap (track limits, data quality). Only valid laps count in the leaderboards; the reason is shown next to the lap.")],
  ];
  container.appendChild(el("div", { class: "card setup-faq" }, problems.map(([q, a]) =>
    el("div", { class: "card-row setup-faq__row" }, [el("strong", {}, q), el("span", {}, a)]))));
}
