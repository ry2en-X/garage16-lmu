// pages/download.js — the desktop client in its two editions, side by side (V0.9.2).
//
//   Client                  records and uploads laps; the analysis opens in the browser
//   Client + analysis app   the same, plus the analysis in its own window
//
// The links come from the server (GET /health: client_download_url and
// client_app_download_url, set by the admin in .env — see docs/ADMIN_GUIDE.md), so the
// page never points at a file that is not there. A missing link shows as "not available
// yet" instead of a broken button. Public page, no sign-in needed.

import { api } from "../api.js";
import { el } from "../format.js";
import { t } from "../i18n.js";
import { inDesktopApp } from "../app_bridge.js";

const safeLink = (url) => (typeof url === "string" && /^https?:\/\/[^\s]+$/i.test(url) ? url : null);

function editionCard({ edition, title, tag, points, url, buttonText }) {
  const button = url
    ? el("a", { class: "btn", href: url, rel: "noopener", "data-download": edition }, buttonText)
    : el("button", { class: "btn", disabled: "disabled", "data-download": edition }, t("Not available yet"));
  return el("div", { class: `card download-card${edition === "analysis" ? " download-card--app" : ""}`, "data-edition": edition }, [
    el("div", { class: "download-card__head" }, [
      el("h2", {}, title),
      tag ? el("span", { class: "download-card__tag" }, tag) : null,
    ]),
    el("ul", { class: "download-card__points" }, points.map((p) => el("li", {}, p))),
    el("div", { class: "download-card__action" }, [
      button,
      url ? null : el("div", { class: "subtitle", style: "margin:8px 0 0;" }, t("Ask whoever runs your Garage16 server.")),
    ]),
  ]);
}

export async function renderDownload(container) {
  container.appendChild(el("h1", {}, t("Download Garage16")));
  container.appendChild(el("p", { class: "subtitle" },
    t("The desktop client records your laps in Le Mans Ultimate and uploads them to Garage16. For Windows 10 and 11. Choose how you want to look at your analysis:")));

  let links = { standard: null, analysis: null, all: null };
  try {
    const health = await api.health();
    links = {
      standard: safeLink(health.client_download_url),
      analysis: safeLink(health.client_app_download_url),
      all: safeLink(health.client_release_page_url),   // V1.0: every file of the newest release
    };
  } catch (err) {
    container.appendChild(el("div", { class: "notice notice--error" }, err.message));
  }

  container.appendChild(el("div", { class: "download-grid" }, [
    editionCard({
      edition: "standard",
      title: t("Client"),
      tag: null,
      points: [
        t("Records and uploads your laps automatically"),
        t("The analysis opens in your browser"),
        t("Smallest download"),
      ],
      url: links.standard,
      buttonText: t("Download Client"),
    }),
    editionCard({
      edition: "analysis",
      title: t("Client + analysis app"),
      tag: t("analysis without a browser"),
      points: [
        t("Everything the client does"),
        t("The analysis in its own window — no browser needed"),
        t("Always in sync with the server: the same laps, comparisons and layout options as the website"),
        t("Needs the Microsoft Edge WebView2 Runtime — already part of Windows 11 and updated Windows 10"),
      ],
      url: links.analysis,
      buttonText: t("Download Client + analysis app"),
    }),
  ]));

  // V1.0: what to do once the file is there — the setup guide; shown more prominently after a click
  const next = el("div", { class: "notice download-next" }, [
    t("After downloading: "),
    el("a", { href: "#/setup" }, t("follow the setup guide")),
    t(" — install, sign in with your Garage16 email and password, start Le Mans Ultimate, drive."),
  ]);
  container.querySelectorAll("a[data-download]").forEach((a) => a.addEventListener("click", () => {
    next.classList.add("notice--success");
    next.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
  }));
  container.appendChild(next);

  container.appendChild(el("div", { class: "card download-notes" }, [
    links.all ? el("p", {}, [
      t("Download does not start? "),
      el("a", { href: links.all, target: "_blank", rel: "noopener" }, t("All downloads on GitHub")),
      t(" — also the portable ZIP versions without an installer."),
    ]) : null,
    el("p", {}, t("Changing your mind later is easy: install the other download over the existing one. Your login and your recorded laps stay.")),
    el("p", {}, t("With the analysis app you can still choose the browser at any time: a tick box in the Garage16 window.")),
    inDesktopApp() ? el("p", { class: "subtitle" }, t("You are looking at this page in the Garage16 analysis app.")) : null,
  ]));
}
