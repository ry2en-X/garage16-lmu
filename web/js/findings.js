// findings.js — readable text for the track-limits findings of server/track_limits.py
// (V0.9.3.1: one table for the analysis page and the admin review, with the rule's tier).
//
// A finding: { code, severity: invalid|review|info, tier: lmu|rule|heuristic|signal,
//              source, at, until, t, detail }.
// Findings linked from the next lap (a track-limits report LMU sent after the finish line)
// are stored separately (validation.linked) and listed with the others.

import { t } from "./i18n.js";

const ATTRIBUTION = () => ({
  this_lap: t("this lap"),
  previous_lap: t("previous lap"),
  ambiguous: t("unclear which lap"),
});

function points(d) {
  return d.points !== undefined && d.points !== null ? t(" = {0} warning points", [d.points]) : "";
}

export function findingText(f) {
  const d = f.detail || {};
  switch (f.code) {
    case "game_invalidated": return t("LMU invalidated the lap");
    case "game_time_not_counted": return t("LMU does not count the lap time");
    case "game_lap_not_counted": return t("LMU does not count the lap");
    case "game_last_lap_time_withdrawn": return t("LMU withdrew the lap time after the line ({0} → {1})", [d.lap_time ?? "?", d.became ?? "?"]);
    case "game_lap_time_changed": return t("LMU changed the lap time after the line ({0} → {1})", [d.lap_time ?? "?", d.became ?? "?"]);
    case "no_lap_time": return t("LMU reported no lap time");
    case "pit_lane": return t("Pit lane during the lap");
    case "sector_times_implausible": return t("Implausible sector times from LMU");
    case "track_limits_points": return t("Track-limits steps from LMU (+{0}{1}; {2})", [d.added ?? 1, points(d), ATTRIBUTION()[d.attribution] || ATTRIBUTION().this_lap]);
    case "track_limits_points_previous_lap": return t("Track-limits steps right after the line (+{0}) — counted for the previous lap", [d.added ?? 1]);
    case "late_track_limits": return t("Track-limits steps {0} s after the finish line (+{1}{2}; {3})", [d.seconds_after_line ?? 0, d.added ?? 1, points(d), ATTRIBUTION()[d.attribution] || ATTRIBUTION().ambiguous]);
    case "four_wheels_off": return t("All four wheels off the track ({0} s)", [d.seconds ?? "?"]);
    case "client_invalid": return t("Rejected by the client (pit lane or the game's flags)");
    case "client_invalid_unexplained": return t("Rejected by a 0.9.3 client — only track-limits steps explain it (a pit-lane visit cannot be ruled out)");
    case "edge_exceeded": return t("Beyond the track edge by {0} m (approximate)", [d.beyond_m ?? "?"]);
    case "short_distance": return t("Lap {0} m shorter than usual on this layout", [d.short_by ?? "?"]);
    case "penalty_added": return t("Penalty received from LMU");
    case "wheels_off": return t("{0} wheels off the track", [d.wheels ?? 2]);
    case "signal_unreliable": return t("Signal ignored as implausible: {0}", [f.source]);
    case "not_verifiable": return t("No track-limits data in this lap — it cannot be checked");
    // V0.9.9: the decision layer (server/lap_decision.py)
    case "signals_missing": return t("Signals this client version always records are missing: {0}", [[...(d.missing || []), ...(d.empty || []), ...(d.unreadable || [])].join(", ") || "?"]);
    case "insufficient_evidence": return t("Only one independent track-limits signal could be checked");
    case "coverage_gap": return t("No telemetry for {0} s", [d.seconds ?? "?"]);
    case "low_frame_rate": return t("Only {0} frames per second", [d.frame_rate_hz ?? "?"]);
    case "position_jump": return t("Lap distance jumps {0} m ahead (speed allows {1} m)", [d.advance_m ?? "?", d.by_speed_m ?? "?"]);
    case "speed_distance_mismatch": return t("Speed trace and lap distance disagree (ratio {0})", [d.ratio ?? "?"]);
    case "implausible_acceleration": return t("Implausible acceleration ({0} G)", [d.g ?? "?"]);
    case "frozen_samples": return t("{0} s of repeated samples while the time advanced", [d.seconds ?? "?"]);
    case "telemetry_reused": return t("This exact file was already uploaded by another driver");
    case "check_failed": return t("The check could not run on this file");
    case "unsupported_inputs": return t("Check inputs from a newer server version");
    default: return f.code;
  }
}

// A rule's name without the numbers of one finding (the admin's rule table).
export function ruleTitle(code) {
  return {
    game_invalidated: t("LMU invalidated the lap"),
    game_time_not_counted: t("LMU does not count the lap time"),
    game_lap_not_counted: t("LMU does not count the lap"),
    game_last_lap_time_withdrawn: t("LMU withdrew the lap time after the line"),
    game_lap_time_changed: t("LMU changed the lap time after the line"),
    no_lap_time: t("LMU reported no lap time"),
    pit_lane: t("Pit lane during the lap"),
    sector_times_implausible: t("Implausible sector times from LMU"),
    client_invalid: t("Rejected by the client (pit lane or the game's flags)"),
    client_invalid_unexplained: t("Rejected by a 0.9.3 client, only track-limits steps explain it"),
    track_limits_points: t("Track-limits steps from LMU"),
    late_track_limits: t("Track-limits steps right after the finish line"),
    track_limits_points_previous_lap: t("Track-limits steps counted for the previous lap"),
    four_wheels_off: t("All four wheels off the track"),
    edge_exceeded: t("Beyond the track edge (approximate)"),
    short_distance: t("Shorter than the usual lap on this layout"),
    penalty_added: t("Penalty received from LMU"),
    wheels_off: t("Wheels off the track"),
    signal_unreliable: t("Signal ignored as implausible"),
    not_verifiable: t("No track-limits data (not checkable)"),
    signals_missing: t("Expected signals missing"),
    insufficient_evidence: t("Too few independent signals"),
    coverage_gap: t("Gap in the telemetry"),
    low_frame_rate: t("Too few frames per second"),
    position_jump: t("Lap distance jumps ahead"),
    speed_distance_mismatch: t("Speed and lap distance disagree"),
    implausible_acceleration: t("Implausible acceleration"),
    frozen_samples: t("Repeated samples"),
    telemetry_reused: t("File uploaded by another driver"),
    check_failed: t("Check failed"),
    unsupported_inputs: t("Check inputs from a newer server"),
  }[code] || code;
}

export function tierText(tier) {
  return {
    lmu: t("LMU verdict"),
    rule: t("Garage16 rule"),
    heuristic: t("measurement (review only)"),
    signal: t("signal"),
    quality: t("data quality (review only)"),
  }[tier] || "";
}

// Every finding of a stored verdict, own and linked, in one list.
export function allFindings(validation) {
  if (!validation) return [];
  return [...(validation.findings || []), ...(validation.linked || [])];
}
