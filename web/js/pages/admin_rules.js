// pages/admin_rules.js — the admin's race rule sets (V0.9.4) for the Stint Analyzer's strategy
// calculator: race length, minimum stops, maximum stint, tyre and refuelling rules, pit lane
// figures. Stored on the server (race_rule_sets, routers/admin.py); every driver can pick them
// in the calculator. Only what the admin enters is used — nothing is filled in by guessing.

import { api } from "../api.js";
import { el } from "../format.js";
import { t } from "../i18n.js";

const FIELDS = () => [
  ["race_laps", t("Race length (laps)"), 1],
  ["race_minutes", t("or race length (minutes)"), 1],
  ["min_pit_stops", t("Minimum pit stops"), 1],
  ["max_stint_laps", t("Max. stint (laps)"), 1],
  ["max_stint_minutes", t("Max. stint (minutes)"), 1],
  ["tyre_sets", t("Tyre sets available"), 1],
  ["refuel_l_per_s", t("Refuelling (L/s)"), 0.1],
  ["tyre_change_s", t("Tyre change (s)"), 0.5],
  ["min_stop_s", t("Minimum stop (s)"), 0.5],
  ["pit_lane_loss_s", t("Pit lane loss (s)"), 0.5],
  ["fuel_margin_laps", t("Fuel margin (laps)"), 0.5],
];

// Pure: the form's values -> the request body (empty fields are left out, numbers as numbers).
export function ruleSetBody(values) {
  const rules = {};
  for (const [key] of FIELDS()) {
    const raw = values[key];
    if (raw === "" || raw === null || raw === undefined) continue;
    const n = Number(raw);
    if (Number.isFinite(n)) rules[key] = ["race_laps", "min_pit_stops", "max_stint_laps", "tyre_sets"].includes(key) ? Math.round(n) : n;
  }
  rules.mandatory_tyre_change = !!values.mandatory_tyre_change;
  rules.refuelling = values.refuelling !== false;
  if (values.notes) rules.notes = String(values.notes).slice(0, 1000);
  const text = (v) => (v && String(v).trim() ? String(v).trim() : null);
  return { name: String(values.name || "").trim(), description: text(values.description), track_name: text(values.track_name), car_class: text(values.car_class), rules };
}

export async function renderRulesTab(token) {
  const wrap = el("div");
  wrap.appendChild(el("p", { class: "subtitle", style: "margin-top:16px;" }, t("Race rules for the strategy calculator in the Stint Analyzer. Drivers pick a rule set there; their measured lap time, consumption and pit loss are combined with it. Leave a value empty if the race does not prescribe it.")));
  const list = el("div");
  const formSlot = el("div");
  wrap.appendChild(list);
  wrap.appendChild(formSlot);

  async function refresh() {
    list.innerHTML = "";
    let rows;
    try {
      rows = await api.adminRuleSets(token);
    } catch (err) {
      list.appendChild(el("div", { class: "notice notice--error" }, err.message));
      return;
    }
    if (!rows.length) list.appendChild(el("div", { class: "empty-state" }, t("No rule set yet.")));
    for (const rs of rows) {
      const edit = el("button", { class: "btn btn--ghost", type: "button" }, t("Edit"));
      edit.addEventListener("click", () => showForm(rs));
      const del = el("button", { class: "btn btn--ghost", type: "button" }, t("Delete"));
      del.addEventListener("click", async () => {
        if (typeof window.confirm === "function" && !window.confirm(t("Delete the rule set \"{0}\"?", [rs.name]))) return;
        try {
          await api.adminDeleteRuleSet(token, rs.id);
          refresh();
        } catch (err) {
          list.appendChild(el("div", { class: "notice notice--error" }, err.message));
        }
      });
      const r = rs.rules || {};
      const summary = [
        r.race_laps ? t("{0} laps", [r.race_laps]) : null,
        r.race_minutes ? t("{0} min", [r.race_minutes]) : null,
        t("min. {0} stops", [r.min_pit_stops || 0]),
        r.mandatory_tyre_change ? t("tyre change mandatory") : null,
        r.refuelling === false ? t("no refuelling") : null,
      ].filter(Boolean).join(" · ");
      list.appendChild(el("div", { class: "card card-row", "data-rule-set": rs.name }, [
        el("div", {}, [el("strong", {}, rs.name), el("div", { class: "subtitle" }, `${rs.track_name || t("all tracks")} · ${summary}`)]),
        el("div", { class: "inline-form" }, [edit, del]),
      ]));
    }
    const add = el("button", { class: "btn", type: "button" }, t("New rule set"));
    add.addEventListener("click", () => showForm(null));
    list.appendChild(el("div", { class: "inline-form section" }, add));
  }

  function showForm(existing) {
    formSlot.innerHTML = "";
    const r = (existing && existing.rules) || {};
    const input = (name, label, value, attrs = {}) => {
      const field = el("input", { class: "input", name, value: value === undefined || value === null ? "" : String(value), ...attrs });
      return [el("label", { class: "stint-form__field" }, [el("span", {}, label), field]), field];
    };
    const [nameLabel, name] = input("name", t("Name"), existing && existing.name, { maxlength: "80", required: "required" });
    const [descLabel, desc] = input("description", t("Description"), existing && existing.description, { maxlength: "500" });
    const [trackLabel, track] = input("track_name", t("Track (empty = all)"), existing && existing.track_name, { maxlength: "200" });
    const [classLabel, carClass] = input("car_class", t("Class (optional)"), existing && existing.car_class, { maxlength: "64" });
    const numbers = FIELDS().map(([key, label, step]) => input(key, label, r[key], { type: "number", min: "0", step: String(step) }));
    const tyres = el("input", { type: "checkbox", ...(r.mandatory_tyre_change ? { checked: "checked" } : {}) });
    const refuel = el("input", { type: "checkbox", ...(r.refuelling === false ? {} : { checked: "checked" }) });
    const status = el("div");
    const save = el("button", { class: "btn", type: "submit" }, existing ? t("Save") : t("Create"));
    const form = el("form", { class: "card" }, [
      el("h3", {}, existing ? t("Edit rule set") : t("New rule set")),
      el("div", { class: "stint-form" }, [nameLabel, descLabel, trackLabel, classLabel, ...numbers.map(([label]) => label),
        el("label", { class: "stint-form__check" }, [tyres, ` ${t("Tyre change mandatory")}`]),
        el("label", { class: "stint-form__check" }, [refuel, ` ${t("Refuelling allowed")}`])]),
      el("div", { class: "inline-form" }, [save]),
      status,
    ]);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const values = { name: name.value, description: desc.value, track_name: track.value, car_class: carClass.value, mandatory_tyre_change: tyres.checked, refuelling: refuel.checked };
      for (const [, field] of numbers) values[field.name] = field.value;
      status.innerHTML = "";
      try {
        await api.adminSaveRuleSet(token, ruleSetBody(values), existing ? existing.id : null);
        formSlot.innerHTML = "";
        refresh();
      } catch (err) {
        status.appendChild(el("div", { class: "notice notice--error" }, err.message));
      }
    });
    formSlot.appendChild(form);
  }

  await refresh();
  return wrap;
}
