// format.js — small display helpers shared across pages.

import { locale, t } from "./i18n.js";

export function formatLapTime(seconds) {
  if (!seconds || seconds <= 0) return "—";
  const minutes = Math.floor(seconds / 60);
  const secs = seconds - minutes * 60;
  return `${minutes}:${secs.toFixed(3).padStart(6, "0")}`;
}

export function formatDate(isoString) {
  if (!isoString) return "—";
  const d = new Date(isoString);
  if (Number.isNaN(d.getTime())) return isoString;
  return d.toLocaleString(locale(), {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value !== undefined && value !== null) {
      node.setAttribute(key, value);
    }
  }
  for (const child of [].concat(children)) {
    if (child === undefined || child === null) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

// Values the server sends as plain words (team roles, invitation states) shown in the user's
// language. Anything unexpected is shown as it is.
const ROLE_LABELS = () => ({ owner: t("owner"), admin: t("admin"), member: t("member") });
const INVITE_LABELS = () => ({ pending: t("pending"), accepted: t("accepted"), declined: t("declined"), revoked: t("revoked"), expired: t("expired") });
export const roleLabel = (role) => ROLE_LABELS()[role] || role;
export const inviteStatusLabel = (status) => INVITE_LABELS()[status] || status;

// The server's record types (WR / TEAM_BEST / PR) as words. "WR" is the fastest valid lap on
// THIS Garage16 instance for that track and car, not an official world record (spec §51), so
// it is called "Garage16 Best" — the same name the analysis uses.
const RECORD_LABELS = () => ({ WR: t("Garage16 Best"), TEAM_BEST: t("Team Best"), PR: t("Personal best") });
export const recordTypeLabel = (type) => RECORD_LABELS()[type] || type;
