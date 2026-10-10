// i18n.js — English and German for the whole web app (V0.9.1).
//
// How it works, on purpose the simplest thing that stays checkable:
//   * The ENGLISH text in the code is the key: t("Choose Lap 1"). With English selected t()
//     returns it unchanged, so the code reads as before and everything that looks for
//     English text (tests, docs) keeps working.
//   * With German selected t() looks the same string up in i18n/de.js; a string without a
//     German entry is shown in English (never a key, never blank). tests/test_i18n.py fails
//     if any t("...") in the code has no German entry or an entry is no longer used, so
//     "forgot to translate" cannot ship silently.
//   * Variables: t("Welcome back, {0}.", [name]). Plurals: tp("{0} lap", "{0} laps", n).
//   * The language is chosen once by a popup on the first visit and can be switched at any time
//     in the menu; it is kept in this browser (localStorage). Switching reloads the page: the
//     pages build their DOM from text once, so a reload is the one way no stale English is left.
//   * Not translated, deliberately: text that comes from the SERVER (error messages in the
//     server's `detail`), driver / track / car names, lap times (m:ss.mmm in both languages),
//     and the channel names, units and groups the server defines for the analysis.
//
// Works in plain Node too (tests): no window or storage is needed, English is the default.

import { DE } from "./i18n/de.js";

export const STORAGE_KEY = "garage16_language";
export const LANGUAGES = [["en", "English"], ["de", "Deutsch"]];
const CODES = LANGUAGES.map(([code]) => code);

// Tests replace this (jsdom cannot reload a page).
export const hooks = { reload: () => window.location.reload() };

function readStored() {
  try {
    const value = typeof localStorage !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
    return CODES.includes(value) ? value : null;
  } catch {
    return null;
  }
}

let language = readStored() || "en";

export const getLanguage = () => language;
export const hasChosenLanguage = () => readStored() !== null;
export const locale = () => (language === "de" ? "de-DE" : "en-GB");

// The language a first-time visitor probably wants: German browsers get German pre-selected.
export function suggestedLanguage() {
  const preferred = typeof navigator !== "undefined" ? [navigator.language, ...(navigator.languages || [])] : [];
  return preferred.some((l) => typeof l === "string" && l.toLowerCase().startsWith("de")) ? "de" : "en";
}

export function setLanguage(next) {
  if (!CODES.includes(next)) return false;
  language = next;
  try {
    if (typeof localStorage !== "undefined") localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // no storage: the choice holds until the page is closed
  }
  if (typeof document !== "undefined") document.documentElement.lang = next;
  return true;
}

function fill(text, params) {
  if (params === undefined || params === null) return text;
  return text.replace(/\{(\w+)\}/g, (match, name) => {
    const value = Array.isArray(params) ? params[Number(name)] : params[name];
    return value === undefined || value === null ? match : String(value);
  });
}

const has = (table, key) => Object.prototype.hasOwnProperty.call(table, key);

// t("English text") / t("Hello {0}", [name]) / t("Hello {who}", { who: name })
export function t(text, params) {
  const translated = language === "de" && has(DE, text) ? DE[text] : text;
  return fill(translated, params);
}

// tp("{0} lap", "{0} laps", n) — English and German both use "one" for exactly 1.
export function tp(one, other, n, params = [n]) {
  return t(n === 1 ? one : other, params);
}

// Static markup (index.html): data-i18n="Text" and data-i18n-aria-label="Text".
export function applyStaticTranslations(root = document) {
  root.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = t(node.getAttribute("data-i18n"));
  });
  root.querySelectorAll("[data-i18n-aria-label]").forEach((node) => {
    node.setAttribute("aria-label", t(node.getAttribute("data-i18n-aria-label")));
  });
  if (typeof document !== "undefined") document.documentElement.lang = language;
}

function choose(next) {
  setLanguage(next);
  hooks.reload();
}

// The menu switch: "EN | DE", the current one marked. Returns the element.
export function createLanguageSwitch() {
  const wrap = document.createElement("div");
  wrap.className = "lang-switch";
  wrap.setAttribute("role", "group");
  wrap.setAttribute("aria-label", t("Language"));
  for (const [code, name] of LANGUAGES) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `lang-switch__button${code === language ? " active" : ""}`;
    button.setAttribute("data-lang", code);
    button.setAttribute("lang", code);
    button.setAttribute("aria-pressed", code === language ? "true" : "false");
    button.setAttribute("title", name);
    button.textContent = code.toUpperCase();
    button.addEventListener("click", () => {
      if (code !== language) choose(code);
    });
    wrap.appendChild(button);
  }
  return wrap;
}

// First visit: ask. Shown in BOTH languages (it is the one screen we cannot translate
// before knowing). The browser's language is pre-selected; there is no "close" — one tap
// on either language is the way through, so the app never starts half in one language.
export function showLanguagePopup(parent = document.body) {
  const suggested = suggestedLanguage();
  const overlay = document.createElement("div");
  overlay.className = "lang-overlay";
  overlay.setAttribute("data-language-popup", "1");
  const box = document.createElement("div");
  box.className = "lang-popup";
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  box.setAttribute("aria-labelledby", "langPopupTitle");
  const title = document.createElement("h2");
  title.id = "langPopupTitle";
  title.textContent = "Choose your language · Sprache wählen";
  const buttons = document.createElement("div");
  buttons.className = "lang-popup__buttons";
  let preselected = null;
  for (const [code, name] of LANGUAGES) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `btn lang-popup__button${code === suggested ? " lang-popup__button--suggested" : ""}`;
    button.setAttribute("data-lang", code);
    button.setAttribute("lang", code);
    button.textContent = name;
    button.addEventListener("click", () => {
      overlay.remove();
      choose(code);
    });
    if (code === suggested) preselected = button;
    buttons.appendChild(button);
  }
  const note = document.createElement("p");
  note.className = "lang-popup__note";
  note.textContent = "You can change this at any time in the menu · Das lässt sich jederzeit im Menü ändern";
  box.append(title, buttons, note);
  overlay.appendChild(box);
  // keep keyboard focus inside the two buttons
  overlay.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const all = [...buttons.querySelectorAll("button")];
    const index = all.indexOf(document.activeElement);
    const next = event.shiftKey ? (index <= 0 ? all.length - 1 : index - 1) : (index + 1) % all.length;
    event.preventDefault();
    all[next].focus();
  });
  parent.appendChild(overlay);
  if (preselected) preselected.focus();
  return overlay;
}

export function ensureLanguageChosen(parent) {
  if (hasChosenLanguage()) return null;
  return showLanguagePopup(parent);
}
