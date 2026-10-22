// analysis/health.js — "Data quality" (V0.9.7): how trustworthy each lap's recording is.
//
// The server measures it (GET /telemetry/analysis/laps/{id}/health, server/telemetry_health.py):
// channels, samples, gaps, time and distance only forward, how often LMU updated the lap
// distance, speed vs distance, recorded duration vs lap time, implausible values, and how finely
// a braking point can be placed at all. This file only turns those numbers into sentences — one
// per check, with its status — so a surprising result can first be checked against its input.
// The lap's validity is shown next to it: a valid lap can be a poor recording and vice versa.

import { el, formatLapTime } from "../format.js";
import { formatMetres, LAP_COLORS } from "./model.js";
import { t } from "../i18n.js";

const pct = (share, digits = 1) => (share * 100).toFixed(digits);
const num = (v) => typeof v === "number" && Number.isFinite(v);

export const STATUS_LABEL = () => ({ good: t("good"), limited: t("limited"), unreliable: t("unreliable") });

// Pure: one sentence per check. -> [{ key, status, text }]
export function healthLines(health) {
  if (!health || !Array.isArray(health.checks)) return [];
  return health.checks.map((check) => ({ key: check.key, status: check.status, text: checkText(check) }));
}

function checkText({ key, status, values: v = {} }) {
  switch (key) {
    case "readable":
      return t("The file cannot be read: {0}", [v.error || "?"]);
    case "channels": {
      const head = v.level === "full"
        ? t("{0} of {1} channels in the file", [v.available, v.total])
        : t("Basic recording (client older than 0.9.0): {0} of {1} channels", [v.available, v.total]);
      const blank = v.blank_count ? ` · ${t("{0} without a single value: {1}", [v.blank_count, (v.blank || []).join(", ")])}` : "";
      return head + blank;
    }
    case "nan":
      return status === "ok"
        ? t("No missing values in the main channels")
        : t("Missing values in up to {0} % of the rows ({1})", [pct(v.worst_share || 0, 2), Object.keys(v.by_channel || {}).join(", ")]);
    case "samples":
      return t("{0} frames per second over {1} s ({2} % repeated polls of the same frame, dropped)",
        [num(v.frame_rate_hz) ? v.frame_rate_hz.toFixed(0) : "?", num(v.duration_s) ? v.duration_s.toFixed(1) : "?", pct(v.duplicate_share || 0, 0)]);
    case "gaps":
      return status === "ok"
        ? t("No gaps in the recording")
        : t("{0} gap(s) without data, the longest {1} s at {2}", [v.count, (v.longest_s || 0).toFixed(2), num(v.at_m) ? formatMetres(v.at_m) : "?"]);
    case "time":
      if (v.restart) return t("Time jumps back by {0} s — a restart inside the file; this lap cannot be analysed", [(v.largest_back_s || 0).toFixed(1)]);
      return status === "ok" ? t("Time only runs forward") : t("Time steps back {0} times (by milliseconds)", [v.backward_steps]);
    case "distance":
      return status === "ok"
        ? t("Lap distance only runs forward")
        : t("Lap distance jumps back {0} times (up to {1} m)", [v.backward_jumps, Math.round(v.largest_back_m || 0)]);
    case "axis":
      return t("LMU updated the lap distance {0} times per second; the samples in between are placed by speed", [num(v.anchor_rate_hz) ? v.anchor_rate_hz.toFixed(1) : "?"]);
    case "speed_distance": {
      const off = num(v.ratio) ? Math.abs(v.ratio - 1) : null;
      return status === "ok"
        ? t("Speed and lap distance agree (within {0} %)", [off === null ? "?" : pct(off, 1)])
        : t("Speed and lap distance disagree by {0} % — distances and the delta are less certain", [off === null ? "?" : pct(off, 1)]);
    }
    case "jumps":
      return status === "ok"
        ? t("No implausible values")
        : t("Implausible values: {0} speed jumps, {1} speeds and {2} pedal values out of range", [v.speed_jumps, v.speed_out_of_range, v.pedal_out_of_range]);
    case "resolution":
      return t("Braking and throttle points are accurate to about ±{0} m (distance between two frames at speed)", [num(v.metres) ? v.metres.toFixed(1) : "?"]);
    case "duration":
      return t("Recorded {0} for a lap time of {1} (difference {2} s), first sample {3} after the line",
        [formatLapTime(v.recorded_s), formatLapTime(v.lap_time_s), num(v.difference_s) ? v.difference_s.toFixed(2) : "?", formatMetres(v.start_m || 0)]);
    default:
      return key;
  }
}

// Pure: the verdict line.
export function validityText(validation) {
  if (!validation) return "";
  if (validation.is_valid && validation.review_status === "held") return t("Lap stored — counts after an admin review");
  if (validation.is_valid && validation.review_status === "quarantined") return t("Lap set aside — its file failed the integrity check");
  if (validation.is_valid) return validation.review_status === "pending" ? t("Lap valid — waiting for an admin review") : t("Lap valid");
  const reasons = (validation.reasons || []).map((r) => (typeof r === "string" ? r : r && (r.text || r.code))).filter(Boolean);
  return reasons.length ? t("Lap invalid: {0}", [reasons.join("; ")]) : t("Lap invalid");
}

function lapColumn(side, label, health) {
  const labels = STATUS_LABEL();
  return el("div", { class: "health__lap", "data-side": side }, [
    el("div", { class: "health__head" }, [
      el("strong", { style: `color:${LAP_COLORS[side] || "inherit"};` }, label),
      " ",
      el("span", { class: `tag health__status health__status--${health.status}` }, `${t("Recording")}: ${labels[health.status] || health.status}`),
    ]),
    el("div", { class: "subtitle health__validity" }, validityText(health.validation)),
    el("ul", { class: "health__list" }, healthLines(health).map((line) => el("li", {
      class: `health__item health__item--${line.status}`, "data-check": line.key,
    }, [el("span", { class: "health__dot", "aria-label": line.status }), line.text]))),
  ]);
}

export function renderHealth(widget, ctx) {
  const element = el("div", { class: "card health" }, [el("h2", {}, t("Data quality"))]);
  const body = el("div", { class: "health__body" }, el("div", { class: "notice" }, t("Loading…")));
  element.appendChild(body);
  const sides = [["a", t("Lap 1")], ...(ctx.health && ctx.health.b ? [["b", t("Lap 2")]] : [])];
  if (!ctx.health) {
    body.innerHTML = "";
    body.appendChild(el("div", { class: "notice" }, t("Not available.")));
    return { element, destroy() {} };
  }
  let alive = true;
  Promise.all(sides.map(([side]) => ctx.health[side].get().catch((err) => ({ error: err.message }))))
    .then((reports) => {
      if (!alive) return;
      body.innerHTML = "";
      reports.forEach((report, i) => {
        const [side, label] = sides[i];
        body.appendChild(report.error
          ? el("div", { class: "notice notice--error" }, `${label}: ${report.error}`)
          : lapColumn(side, label, report));
      });
      body.appendChild(el("div", { class: "subtitle health__caption" }, t("Measured on the recorded file before any analysis. A 'limited' recording can still be compared — the numbers say where its limits are.")));
    });
  return { element, destroy() { alive = false; } };
}
