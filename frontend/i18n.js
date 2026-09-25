// Interface language. The source strings are Italian; i18n-en.js maps each of them to English.
// t("Scade {0}", date) returns the string in the current language with the placeholders filled.
import EN from "./i18n-en.js";

const KEY = "lang";

function initial() {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "it" || saved === "en") return saved;
  } catch { /* storage unavailable */ }
  return (navigator.language || "").toLowerCase().startsWith("it") ? "it" : "en";
}

export const lang = initial();
export const locale = lang === "it" ? "it-IT" : "en-GB";
document.documentElement.lang = lang;

// Strings asked in English that have no translation yet (checked by the tests of the UI)
const missing = new Set();
window.__i18nMissing = missing;

export function t(text, ...args) {
  let out = text;
  if (lang === "en" && typeof text === "string") {
    const tr = EN[text];
    if (tr === undefined) missing.add(text);
    else out = tr;
  }
  return args.length ? String(out).replace(/\{(\d+)\}/g, (m, i) => (args[+i] ?? "")) : out;
}

/** Saves the choice and reloads: every view, label and number format follows the new language. */
export function setLang(next) {
  try { localStorage.setItem(KEY, next); } catch { /* the choice lasts for this page only */ }
  window.location.reload();
}
