// core/copy_link.js — the "Copy link" button of the leaderboard, the lap analysis and the
// driver profile: puts the full address of what is shown on the clipboard, ready for Discord.

import { el } from "./format.js";
import { t } from "./i18n.js";
import { link } from "./link.js";

async function writeClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return true;
  }
  // http:// or an old browser: a hidden text field and the copy command
  const area = el("textarea", { style: "position:fixed;left:-9999px;top:0;", readonly: "readonly" });
  area.value = text;
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  area.remove();
  return ok;
}

/** A button that copies `path()` (a path from core/link.js, or the current address) as a full URL. */
export function copyLinkButton(path = () => window.location.pathname + window.location.search) {
  const button = el("button", { class: "btn btn--ghost btn--sm copy-link", type: "button", title: t("Copy the address of this page") }, t("Copy link"));
  let timer = null;
  button.addEventListener("click", async () => {
    const url = link.absolute(path());
    let ok = false;
    try {
      ok = await writeClipboard(url);
    } catch {
      ok = false;
    }
    button.textContent = ok ? t("Link copied ✓") : url;
    clearTimeout(timer);
    timer = setTimeout(() => { button.textContent = t("Copy link"); }, ok ? 2000 : 8000);
  });
  return button;
}
