// pages/legal.js — "Legal": legal notice, privacy policy, terms of use, licences (V1.0).
//
// Public, no sign-in. The texts live in ../legal/de.js and ../legal/en.js and are chosen as a
// whole by the page language (they do not go through t(): legal texts are translated as a
// document, not sentence by sentence). The operator's name, address and email come from the
// server (GET /legal, .env) and are only fetched and shown on the tabs that need them.
//
//   #/legal                 legal notice
//   #/legal?tab=privacy     privacy policy      (also: terms, licenses)

import { api } from "../api.js";
import { el } from "../format.js";
import { getLanguage } from "../i18n.js";
import * as DE from "../legal/de.js";
import * as EN from "../legal/en.js";

export const TABS = ["imprint", "privacy", "terms", "licenses"];

const texts = () => (getLanguage() === "de" ? DE : EN);

function currentTab() {
  const query = new URLSearchParams((window.location.hash.split("?")[1]) || "");
  const tab = query.get("tab");
  return TABS.includes(tab) ? tab : "imprint";
}

function fill(text, values) {
  return text.replace(/\{(\w+)\}/g, (m, name) => (values[name] === undefined ? m : String(values[name])));
}

function operatorBlock(info, labels) {
  if (info === null) return el("div", { class: "notice notice--error" }, labels.operatorError);
  if (!info.operator) return el("div", { class: "notice" }, labels.operatorMissing);
  const op = info.operator;
  return el("address", { class: "legal-operator" }, [
    el("strong", {}, op.name),
    ...op.address.map((line) => el("div", {}, line)),
    op.email ? el("div", { style: "margin-top:6px;" }, [`${labels.email}: `, el("a", { href: `mailto:${op.email}` }, op.email)]) : null,
  ]);
}

function renderBlock(block, ctx) {
  if (typeof block === "string") return el("p", {}, fill(block, ctx.values));
  if (block.operator) return operatorBlock(ctx.info, ctx.labels);
  if (block.ul) return el("ul", {}, block.ul.map((item) => el("li", {}, fill(item, ctx.values))));
  if (block.links) {
    return el("ul", { class: "legal-links" }, block.links.map(([label, href]) =>
      el("li", {}, el("a", { href, rel: "noopener", target: /^https?:/.test(href) ? "_blank" : null }, label))));
  }
  if (block.table) {
    return el("div", { class: "table-scroll" }, el("table", { class: "legal-table" }, [
      el("thead", {}, el("tr", {}, block.table.head.map((h) => el("th", {}, h)))),
      el("tbody", {}, block.table.rows.map((row) => el("tr", {}, row.map((cell) => el("td", {}, fill(cell, ctx.values)))))),
    ]));
  }
  return null;
}

export async function renderLegal(container) {
  const L = texts();
  const tab = currentTab();
  const sections = { imprint: L.IMPRINT, privacy: L.PRIVACY, terms: L.TERMS, licenses: L.LICENSES }[tab];

  container.appendChild(el("h1", {}, L.LABELS.title));
  container.appendChild(el("p", { class: "subtitle" }, L.LABELS.intro));
  container.appendChild(el("nav", { class: "inline-form legal-tabs", "aria-label": L.LABELS.title },
    TABS.map((name) => el("a", {
      href: name === "imprint" ? "#/legal" : `#/legal?tab=${name}`,
      class: `btn btn--ghost${name === tab ? " active" : ""}`,
      "data-tab": name,
      "aria-current": name === tab ? "page" : null,
    }, L.LABELS.tabs[name]))));

  // the operator's details only where the text needs them (legal notice, privacy policy)
  const needsOperator = sections.some((s) => s.blocks.some((b) => b && b.operator));
  let info = { operator: null, min_age: 16 };
  if (needsOperator || tab === "terms") {
    try {
      info = await api.legal();
    } catch {
      info = needsOperator ? null : info;
    }
  }
  const ctx = { info, labels: L.LABELS, values: { min_age: (info && info.min_age) || 16, date: L.DATE } };

  const article = el("article", { class: "card legal-text", "data-tab": tab });
  if (L.LABELS.binding) article.appendChild(el("p", { class: "subtitle" }, L.LABELS.binding));
  for (const section of sections) {
    article.appendChild(el("h2", {}, section.h));
    for (const block of section.blocks) {
      const node = renderBlock(block, ctx);
      if (node) article.appendChild(node);
    }
  }
  article.appendChild(el("p", { class: "subtitle", style: "margin-top:18px;" }, `${L.LABELS.asOf}: ${L.DATE}`));
  container.appendChild(article);
}
