// analysis/editor.js — the panel shown in edit mode ("Ansicht anpassen"): add a
// window, set the three main colours, restore the Garage16 standard layout (V0.9.1).
// It only edits the layout object (via onChange) — nothing here touches laps, accounts
// or telemetry; resetting removes the stored UI preference and nothing else.

import { el } from "../format.js";
import { addWidget, MAX_WIDGETS, SINGLE_TYPES } from "./layout.js";
import { DEFAULT_COLORS } from "./model.js";
import { moduleForType } from "./modules.js";
import { setColors } from "./layout.js";
import { T } from "./strings.js";

export function createEditorPanel({ layout, catalog, onChange, onReset, onDone, persisted }) {
  const valid = new Set(catalog.channels.map((c) => c.key));
  const shownCharts = new Set(layout.widgets.filter((w) => w.type === "chart").map((w) => w.channel));
  const shownSingles = new Set(layout.widgets.filter((w) => SINGLE_TYPES.includes(w.type)).map((w) => w.type));

  // --- add a window
  const add = el("select", { class: "editor__add", id: "editor_add", "aria-label": T.addWindow }, [el("option", { value: "" }, T.addChoose)]);
  for (const type of SINGLE_TYPES) {
    if (shownSingles.has(type)) continue;
    add.appendChild(el("option", { value: `type:${type}` }, moduleForType(type).title));
  }
  const groups = new Map();
  for (const channel of catalog.channels) {
    if (shownCharts.has(channel.key)) continue;
    if (!groups.has(channel.group)) groups.set(channel.group, []);
    groups.get(channel.group).push(channel);
  }
  for (const [group, channels] of groups) {
    add.appendChild(el("optgroup", { label: group }, channels.map((c) => el("option", { value: `chart:${c.key}` }, c.unit ? `${c.label} (${c.unit})` : c.label))));
  }
  add.disabled = layout.widgets.length >= MAX_WIDGETS;
  add.addEventListener("change", () => {
    const [kind, value] = add.value.split(":");
    if (!kind) return;
    onChange(kind === "chart" ? addWidget(layout, { type: "chart", channel: value }, valid) : addWidget(layout, { type: value }, valid));
  });

  // --- colours
  const swatch = (label, key) => {
    const input = el("input", { type: "color", value: layout.colors[key], id: `editor_color_${key}`, "aria-label": label });
    input.addEventListener("change", () => onChange(setColors(layout, { [key]: input.value })));
    return el("label", { class: "editor__color", for: `editor_color_${key}` }, [label, " ", input]);
  };
  const isDefaultColors = ["a", "b", "delta"].every((k) => layout.colors[k] === DEFAULT_COLORS[k].toLowerCase());
  const colorReset = el("button", { type: "button", class: "btn btn--ghost editor__colors-reset", disabled: isDefaultColors ? "disabled" : null, onclick: () => onChange(setColors(layout, { a: DEFAULT_COLORS.a, b: DEFAULT_COLORS.b, delta: DEFAULT_COLORS.delta })) }, T.colorReset);

  const reset = el("button", { type: "button", class: "btn btn--ghost editor__reset", onclick: () => {
    if (typeof window.confirm === "function" && !window.confirm(T.resetConfirm)) return;
    onReset();
  } }, T.resetLayout);
  const done = el("button", { type: "button", class: "btn editor__done", onclick: onDone }, T.customizeDone);

  const element = el("div", { class: "card editor", "data-editor": "1" }, [
    el("h2", {}, T.customize),
    el("p", { class: "subtitle", style: "margin:0 0 10px;" }, T.editHint),
    persisted ? null : el("div", { class: "notice notice--error editor__storage" }, T.storageUnavailable),
    el("div", { class: "inline-form editor__row" }, [el("label", { for: "editor_add" }, T.addWindow), add]),
    el("div", { class: "inline-form editor__row" }, [el("strong", {}, `${T.colors}: `), swatch(T.colorLap1, "a"), swatch(T.colorLap2, "b"), swatch(T.colorDelta, "delta"), colorReset]),
    el("div", { class: "inline-form editor__row" }, [reset, done]),
  ]);
  return { element };
}
