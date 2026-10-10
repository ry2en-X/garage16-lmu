// V0.9.1 language support: t() / tp() lookups and fallbacks, the language store, the
// first-visit popup, the menu switch and the static-markup translation (jsdom).
import { JSDOM } from "jsdom";
import assert from "node:assert/strict";

const dom = new JSDOM(`<!doctype html><html lang="en"><body>
  <nav><a id="a1" data-i18n="Leaderboard">Leaderboard</a><a id="a2" data-i18n="Analysis">Analysis</a>
  <button id="b1" aria-label="Toggle menu" data-i18n-aria-label="Toggle menu">☰</button>
  <span id="s1" data-i18n="not signed in">not signed in</span><span id="s2" data-i18n="No such text in the dictionary">No such text in the dictionary</span></nav>
  <div id="slot"></div></body></html>`, { url: "http://localhost/", pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.localStorage = dom.window.localStorage;
const setNavigator = (language, languages) => {
  Object.defineProperty(dom.window.navigator, "language", { value: language, configurable: true });
  Object.defineProperty(dom.window.navigator, "languages", { value: languages || [language], configurable: true });
  Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
};
setNavigator("en-US");

const i18n = await import("../../web/js/i18n.js");
const { DE } = await import("../../web/js/i18n/de.js");
const { t, tp, getLanguage, setLanguage, hasChosenLanguage, suggestedLanguage, locale, hooks } = i18n;
let reloads = 0;
hooks.reload = () => { reloads += 1; };

// ---------------------------------------------------------------- defaults: English, nothing stored
assert.equal(getLanguage(), "en");
assert.equal(hasChosenLanguage(), false);
assert.equal(locale(), "en-GB");
assert.equal(t("Choose Lap 1"), "Choose Lap 1", "English is the source text, returned unchanged");
assert.equal(t("Welcome back, {0}.", ["Mika"]), "Welcome back, Mika.");
assert.equal(t("Hello {who}, {n} laps", { who: "Nito", n: 3 }), "Hello Nito, 3 laps", "named placeholders");
assert.equal(t("{0} and {1}", ["a"]), "a and {1}", "a missing value leaves its placeholder visible instead of printing 'undefined'");
assert.equal(t("a {0} b", [0]), "a 0 b", "0 is a value");
assert.equal(t("a {0} b", [null]), "a {0} b");
assert.equal(t("plain text", undefined), "plain text");
assert.equal(tp("{0} lap", "{0} laps", 1), "1 lap");
assert.equal(tp("{0} lap", "{0} laps", 0), "0 laps");
assert.equal(tp("{0} lap", "{0} laps", 7), "7 laps");
assert.equal(tp("{0} / {1} team best held", "{0} / {1} team bests held", 3, [1, 3]), "1 / 3 team bests held", "explicit params");

// ---------------------------------------------------------------- German
assert.equal(setLanguage("de"), true);
assert.equal(getLanguage(), "de");
assert.equal(hasChosenLanguage(), true);
assert.equal(localStorage.getItem("garage16_language"), "de", "the choice is kept in this browser");
assert.equal(document.documentElement.lang, "de");
assert.equal(locale(), "de-DE");
assert.equal(t("Choose Lap 1"), "Runde 1 wählen");
assert.equal(t("Welcome back, {0}.", ["Mika"]), "Willkommen zurück, Mika.", "placeholders are filled in the German text");
assert.equal(tp("{0} lap", "{0} laps", 1), "1 Runde");
assert.equal(tp("{0} lap", "{0} laps", 5), "5 Runden");
assert.equal(tp("{0} / {1} team best held", "{0} / {1} team bests held", 3, [1, 3]), "1 / 3 Team-Bestzeiten gehalten");
assert.equal(t("This text has no German entry"), "This text has no German entry", "a missing translation falls back to English, never to a key or blank");
assert.equal(t("This {0} has none", ["text"]), "This text has none");
assert.equal(t("constructor"), "constructor", "dictionary lookup ignores inherited object properties");
assert.equal(t("__proto__"), "__proto__");
assert.equal(t("toString"), "toString");
assert.equal(setLanguage("fr"), false, "an unknown language is refused");
assert.equal(setLanguage(undefined), false);
assert.equal(getLanguage(), "de", "...and changes nothing");
assert.equal(localStorage.getItem("garage16_language"), "de");

// every German entry is a complete text: not empty, same placeholders as its English key
const placeholders = (s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(",");
for (const [en, de] of Object.entries(DE)) {
  assert.ok(typeof de === "string" && de.trim() !== "" || en.trim() === "", `empty German text for ${JSON.stringify(en)}`);
  assert.equal(placeholders(de), placeholders(en), `placeholders differ for ${JSON.stringify(en)}`);
}

// ---------------------------------------------------------------- static markup
i18n.applyStaticTranslations();
assert.equal(document.getElementById("a1").textContent, "Leaderboard");
assert.equal(document.getElementById("a2").textContent, "Analyse");
assert.equal(document.getElementById("b1").getAttribute("aria-label"), "Menü umschalten");
assert.equal(document.getElementById("s1").textContent, "nicht angemeldet");
assert.equal(document.getElementById("s2").textContent, "No such text in the dictionary", "unknown static text stays English");
setLanguage("en");
i18n.applyStaticTranslations();
assert.equal(document.getElementById("a2").textContent, "Analysis");
assert.equal(document.getElementById("s1").textContent, "not signed in");
assert.equal(document.documentElement.lang, "en");

// ---------------------------------------------------------------- the first-visit popup
localStorage.clear();
setLanguage("en"); localStorage.clear();                // "English by default", but nothing chosen yet
assert.equal(hasChosenLanguage(), false);
reloads = 0;
const popup = i18n.ensureLanguageChosen(document.getElementById("slot"));
assert.ok(popup, "asks on the very first visit");
assert.equal(document.querySelectorAll("[data-language-popup]").length, 1);
const buttons = [...popup.querySelectorAll("button")];
assert.deepEqual(buttons.map((b) => b.textContent), ["English", "Deutsch"]);
assert.deepEqual(buttons.map((b) => b.getAttribute("lang")), ["en", "de"], "each label is announced in its own language");
assert.ok(popup.querySelector('[role="dialog"][aria-modal="true"]'), "an accessible modal dialog");
assert.ok(/Choose your language/.test(popup.textContent) && /Sprache wählen/.test(popup.textContent), "the question is asked in both languages");
assert.equal(document.activeElement, buttons[0], "an English browser: English is focused");
assert.ok(buttons[0].classList.contains("lang-popup__button--suggested") && !buttons[1].classList.contains("lang-popup__button--suggested"));
popup.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
assert.ok(document.querySelector("[data-language-popup]"), "no way to skip it: one tap on either language is the way through");
popup.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true }));
assert.equal(document.activeElement, buttons[1], "Tab stays inside the dialog");
popup.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true }));
assert.equal(document.activeElement, buttons[0]);
assert.equal(reloads, 0, "nothing happens until a language is chosen");
buttons[1].click();
assert.equal(localStorage.getItem("garage16_language"), "de");
assert.equal(getLanguage(), "de");
assert.equal(document.querySelector("[data-language-popup]"), null, "the popup is gone");
assert.equal(reloads, 1, "the page reloads so no stale English is left on screen");
assert.equal(i18n.ensureLanguageChosen(document.getElementById("slot")), null, "never asks again once chosen");
assert.equal(document.querySelector("[data-language-popup]"), null);

// the browser's language is pre-selected
localStorage.clear(); setLanguage("en"); localStorage.clear();
setNavigator("de-AT");
assert.equal(suggestedLanguage(), "de");
let shown = i18n.showLanguagePopup(document.getElementById("slot"));
assert.equal(document.activeElement.getAttribute("data-lang"), "de", "a German browser: Deutsch is focused");
assert.ok(shown.querySelector('[data-lang="de"]').classList.contains("lang-popup__button--suggested"));
shown.remove();
setNavigator("fr-FR", ["fr-FR", "de-CH", "en"]);
assert.equal(suggestedLanguage(), "de", "any German in the preferred list counts");
setNavigator("fr-FR", ["fr-FR"]);
assert.equal(suggestedLanguage(), "en", "no German: English");
setNavigator("en-US");

// storage that refuses writes: the choice still applies for this session
localStorage.clear(); setLanguage("en"); localStorage.clear();
const realSet = dom.window.Storage.prototype.setItem;
const realGet = dom.window.Storage.prototype.getItem;
dom.window.Storage.prototype.setItem = () => { throw new Error("quota"); };
assert.equal(setLanguage("de"), true);
assert.equal(getLanguage(), "de", "kept in memory");
dom.window.Storage.prototype.setItem = realSet;
dom.window.Storage.prototype.getItem = () => { throw new Error("denied"); };
assert.equal(hasChosenLanguage(), false, "unreadable storage is 'not chosen', not a crash");
dom.window.Storage.prototype.getItem = realGet;
localStorage.clear();
setLanguage("en"); localStorage.clear();

// ---------------------------------------------------------------- the menu switch
reloads = 0;
const sw = i18n.createLanguageSwitch();
document.getElementById("slot").appendChild(sw);
const [en, de] = [...sw.querySelectorAll("button")];
assert.deepEqual([en.textContent, de.textContent], ["EN", "DE"]);
assert.equal(en.getAttribute("aria-pressed"), "true");
assert.equal(de.getAttribute("aria-pressed"), "false");
assert.ok(en.classList.contains("active") && !de.classList.contains("active"));
assert.equal(sw.getAttribute("role"), "group");
en.click();
assert.equal(reloads, 0, "clicking the language that is already active does nothing");
de.click();
assert.equal(reloads, 1);
assert.equal(localStorage.getItem("garage16_language"), "de");
const sw2 = i18n.createLanguageSwitch();
assert.equal(sw2.getAttribute("aria-label"), "Sprache", "the switch itself is translated");
assert.equal(sw2.querySelector('[data-lang="de"]').getAttribute("aria-pressed"), "true");
sw2.querySelector('[data-lang="en"]').click();
assert.equal(localStorage.getItem("garage16_language"), "en");
assert.equal(reloads, 2);

console.log("I18N-OK");
