// analysis/coaching.js — "Where to find time" (V0.9.3): plain, checkable driving hints.
//
// Every hint is a direct comparison of numbers the corner analysis already measured on the
// raw samples (server/telemetry_analysis.py corner_analysis): braking point, minimum speed,
// full-throttle point and exit speed of Lap 1 against Lap 2, plus the lock-ups measured from
// wheel slip. No model, no guessing, no AI: each sentence names the two values it is built
// from, so a driver can check it on the charts. Lap 2 is the reference (usually the faster lap).
//
// Thresholds — below them a difference is within normal lap-to-lap noise and not worth a hint:
//   braking point 5 m · minimum speed 2 km/h · full throttle 10 m · exit speed 3 km/h ·
//   a corner must cost at least 0.03 s to be listed.
//
// V0.9.4: hints are grouped by the PHASE of the corner they belong to (braking / turn-in / apex /
// exit / straight — server/telemetry_analysis.py corner_phases, whose time differences add up
// exactly to the corner's delta), each phase with its own measured time difference. "Brake
// later" is only said when Lap 2 — braking later — is NOT slower at the apex and on the exit:
// otherwise the later braking point is not what made Lap 2 faster. With the driver's earlier
// laps loaded (GET /corner-history), a line says how often they did better in this corner.

import { el } from "../format.js";
import { formatDelta, formatMetres, LAP_COLORS, WHEEL_LABEL } from "./model.js";
import { historyFor, historySentences, PHASE_LABELS } from "./corner_detail.js";

import { t } from "../i18n.js";

export const COACH = { minLoss: 0.03, brakeM: 5, minSpeedKmh: 2, throttleM: 10, exitKmh: 3, maxCorners: 5, maxStrengths: 3 };

const num = (v) => v !== null && v !== undefined && Number.isFinite(v);

// Pure: the hints for one corner, from Lap 1's (a) and Lap 2's (b) measurements.
export function cornerHints(corner, lockups = []) {
  const { a, b } = corner;
  const hints = [];
  if (num(a.brake) && num(b.brake)) {
    const diff = a.brake - b.brake; // > 0: Lap 1 brakes later
    // Lap 2 brakes later AND is not slower at the apex or on the exit for it
    const apexOk = !num(a.min) || !num(b.min) || b.min >= a.min - COACH.minSpeedKmh;
    const exitOk = !num(a.exit) || !num(b.exit) || b.exit >= a.exit - COACH.exitKmh;
    if (diff <= -COACH.brakeM && apexOk && exitOk) {
      hints.push({ kind: "brake_later", value: Math.round(-diff), text: t("Brake {0} m later — Lap 2 brakes at {1}, Lap 1 at {2}.", [Math.round(-diff), formatMetres(b.brake), formatMetres(a.brake)]) });
    } else if (diff >= COACH.brakeM && num(a.min) && num(b.min) && a.min < b.min - COACH.minSpeedKmh) {
      hints.push({ kind: "overbraking", value: Math.round(diff), text: t("Lap 1 brakes {0} m later but is {1} km/h slower at the apex — brake a little earlier or release the brake sooner.", [Math.round(diff), (b.min - a.min).toFixed(1)]) });
    }
  }
  if (num(a.min) && num(b.min) && a.min < b.min - COACH.minSpeedKmh && !hints.some((h) => h.kind === "overbraking")) {
    hints.push({ kind: "min_speed", value: b.min - a.min, text: t("Carry {0} km/h more minimum speed ({1} instead of {2} km/h).", [(b.min - a.min).toFixed(1), b.min.toFixed(1), a.min.toFixed(1)]) });
  }
  if (num(a.throttle) && num(b.throttle) && a.throttle - b.throttle >= COACH.throttleM) {
    hints.push({ kind: "throttle_earlier", value: Math.round(a.throttle - b.throttle), text: t("Back to full throttle {0} m earlier.", [Math.round(a.throttle - b.throttle)]) });
  } else if (num(b.throttle) && !num(a.throttle)) {
    hints.push({ kind: "throttle_missing", value: null, text: t("Lap 1 never reaches full throttle on the way out; Lap 2 does at {0}.", [formatMetres(b.throttle)]) });
  }
  if (num(a.exit) && num(b.exit) && a.exit < b.exit - COACH.exitKmh) {
    hints.push({ kind: "exit_speed", value: b.exit - a.exit, text: t("Exit {0} km/h faster ({1} instead of {2} km/h) — it pays off on the following straight.", [(b.exit - a.exit).toFixed(1), b.exit.toFixed(1), a.exit.toFixed(1)]) });
  }
  for (const lock of lockups) {
    hints.push({ kind: "lockup", value: lock.peak, text: t("{0} locks in the braking zone at {1} — less brake pressure or more rear bias.", [WHEEL_LABEL[lock.wheel] || lock.wheel, formatMetres(lock.at)]) });
  }
  return hints;
}

// Which phase of the corner a hint belongs to.
export const HINT_PHASE = {
  brake_later: "braking", overbraking: "braking", lockup: "braking",
  min_speed: "apex", throttle_earlier: "exit", throttle_missing: "exit", exit_speed: "exit",
};

// Pure (V0.9.4): the corner's phases with their time difference and the hints that belong to
// each — phases in the order driven; a phase without a hint is listed when it costs time.
export function hintsByPhase(corner, hints) {
  const phases = corner.phases && corner.phases.length ? corner.phases : null;
  if (!phases) return [{ phase: null, delta: corner.delta, hints }];
  return phases
    .map((p) => ({ phase: p.phase, delta: p.delta, from: p.from, to: p.to, hints: hints.filter((h) => HINT_PHASE[h.kind] === p.phase) }))
    .filter((g) => g.hints.length || g.delta >= COACH.minLoss / 2);
}

// Pure: the corners where Lap 1 loses most (with hints) and where it is strongest.
//   corners: /compare's "corners"; lockups: Lap 1's measured lock-ups [{at, wheel, peak}]
export function coachingPlan(corners, lockups = []) {
  const withLocks = (c) => lockups.filter((l) => l.at >= c.start && l.at <= c.end);
  const losses = corners
    .filter((c) => c.delta >= COACH.minLoss)
    .sort((x, y) => y.delta - x.delta)
    .slice(0, COACH.maxCorners)
    .map((c) => ({ corner: c, hints: cornerHints(c, withLocks(c)) }));
  const strengths = corners
    .filter((c) => c.delta <= -COACH.minLoss)
    .sort((x, y) => x.delta - y.delta)
    .slice(0, COACH.maxStrengths);
  const potential = corners.reduce((sum, c) => sum + (c.delta > 0 ? c.delta : 0), 0);
  return { losses, strengths, potential };
}

export function renderCoaching(widget, ctx) {
  const base = ctx.baseData || ctx.data;
  const title = el("h2", {}, t("Where to find time"));
  if (!base.laps.b) return { element: el("div", { class: "card coach" }, [title, el("div", { class: "notice" }, t("Choose a second lap to compare against — the hints compare Lap 1 with Lap 2."))]), destroy() {} };
  const corners = base.corners || [];
  if (!corners.length) return { element: el("div", { class: "card coach" }, [title, el("div", { class: "notice" }, t("No corners found on the common stretch of these laps."))]), destroy() {} };
  const body = el("div", { class: "coach__body" });
  const element = el("div", { class: "card coach" }, [title, body]);

  let history = null;
  if (ctx.history) ctx.history.get().then((h) => { history = h; render(); }).catch(() => {});

  const zoomTo = (c) => {
    if (ctx.selection) ctx.selection.set(c.n);
    const first = Math.min(...[c.a.brake, c.b.brake, c.entry ?? c.start].filter(num));
    const last = Math.max(...[c.a.throttle, c.b.throttle, c.apex + 150].filter(num));
    ctx.state.setView(first - 60, Math.min(last + 60, c.end));
    ctx.pinAtDistance(c.apex);
  };

  function render() {
    body.innerHTML = "";
    const lockups = ctx.events && ctx.events.get()
      ? (ctx.events.get().wheels && ctx.events.get().wheels.a || []).filter((e) => e.type === "lockup")
      : [];
    const plan = coachingPlan(corners, lockups);
    if (!plan.losses.length) {
      body.appendChild(el("div", { class: "subtitle" }, t("Lap 1 loses no corner by more than {0} s against Lap 2.", [COACH.minLoss.toFixed(2)])));
    } else {
      body.appendChild(el("div", { class: "subtitle coach__potential" }, t("Lap 1 loses {0} s in the corners where it is slower — the biggest ones first:", [formatDelta(plan.potential)])));
      body.appendChild(el("ol", { class: "coach__list" }, plan.losses.map(({ corner, hints }) => {
        const head = el("button", { type: "button", class: "coach__corner", "data-corner": String(corner.n), title: t("Zoom into this corner") }, [
          el("strong", {}, t("Corner {0}", [corner.n])),
          ` · ${formatMetres(corner.apex)} · `,
          el("span", { class: "analysis-diff--loss" }, `${formatDelta(corner.delta)} s`),
        ]);
        head.addEventListener("click", () => zoomTo(corner));
        const labels = PHASE_LABELS();
        const groups = hintsByPhase(corner, hints);
        const earlier = history ? historySentences(corner, historyFor(corner, history)) : [];
        return el("li", { class: "coach__item" }, [
          head,
          hints.length || groups.some((g) => g.phase)
            ? el("ul", { class: "coach__phases" }, groups.map((g) => el("li", { class: "coach__phase", "data-phase": g.phase || "" }, [
              g.phase ? el("span", { class: `coach__phase-head ${g.delta > 0 ? "analysis-diff--loss" : "analysis-diff--gain"}` }, `${labels[g.phase] || g.phase} ${formatDelta(g.delta)} s`) : null,
              g.hints.length
                ? el("ul", { class: "coach__hints" }, g.hints.map((h) => el("li", { "data-hint": h.kind }, h.text)))
                : (g.phase ? el("span", { class: "subtitle" }, ` — ${t("no single measured value differs clearly here")}`) : null),
            ])))
            : el("div", { class: "subtitle" }, t("The time is lost gradually (no single measured value differs clearly) — compare the speed traces.")),
          earlier.length ? el("div", { class: "coach__history" }, earlier.join(" ")) : null,
        ]);
      })));
    }
    if (plan.strengths.length) {
      body.appendChild(el("div", { class: "coach__strengths" }, [
        el("strong", { style: `color:${LAP_COLORS.a};` }, t("Strengths of Lap 1: ")),
        plan.strengths.map((c) => t("corner {0} ({1} s)", [c.n, formatDelta(c.delta)])).join(" · "),
      ]));
    }
    body.appendChild(el("div", { class: "subtitle coach__caption" }, t("Each hint compares values measured on both laps (braking point, minimum, full-throttle point, exit speed, wheel lock-ups). Lap 2 is the reference. The time per phase is measured; the phases add up to the corner's time.")));
  }
  const off = ctx.events ? ctx.events.subscribe(render) : () => {};
  render();
  return { element, destroy: off };
}
