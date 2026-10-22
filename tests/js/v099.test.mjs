// V0.9.9 — the texts of the decision layer's findings, held/quarantined in the verdict line, and the
// admin "Integrity" tab (quarantined laps, check again / release, audit log and chain check).
import { JSDOM } from "jsdom";
import assert from "node:assert/strict";

const dom = new JSDOM(`<!doctype html><body><div id="root"></div></body>`, { url: "http://localhost/#/admin", pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.localStorage = dom.window.localStorage;
globalThis.sessionStorage = dom.window.sessionStorage;
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
dom.window.LMU_GARAGE_API_BASE_URL = "";
dom.window.localStorage.setItem("garage16_language", "en");

const calls = [];
let quarantined = [{ lap_id: 7, driver_id: 2, driver_name: "Ann", track_name: "Spa", lap_time: 137.5,
  quarantine: { reason: "hash_mismatch", request_id: "req-1", previous_review_status: null } }];
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(url, "http://x");
  calls.push(`${init.method || "GET"} ${u.pathname}${u.search}`);
  const json = (body, status = 200) => ({ ok: status < 400, status, statusText: String(status), json: async () => body, text: async () => JSON.stringify(body) });
  if (u.pathname === "/admin/integrity") return json({ quarantined, laps: 12, unsigned: 3, audit_head: { id: 41, mac: "v1:abcd1234:ff" } });
  if (u.pathname === "/admin/audit") return json({ events: [{ id: 41, at: "2026-10-20T12:00:00", kind: "lap.quarantined", actor: "system", lap_id: 7, driver_id: 2, request_id: "req-1", detail: {} }], head: { id: 41, mac: "x" } });
  if (u.pathname === "/admin/audit/verify") return json({ ok: false, rows: 41, first_broken: 12, problem: "content (this row was changed)" });
  if (u.pathname === "/admin/laps/7/integrity") {
    const release = u.searchParams.get("release") === "true";
    if (release) quarantined = [];
    return json({ lap_id: 7, result: { status: "ok", mac: "ok" }, review_status: release ? null : "quarantined", released: release });
  }
  return json({ detail: "unexpected " + u.pathname }, 500);
};
const text = (node) => node.textContent.replace(/\s+/g, " ");
const tick = () => new Promise((r) => setTimeout(r, 0));

// ---------------------------------------------------------------- finding texts
const { findingText, ruleTitle, tierText } = await import("../../web/js/findings.js");
assert.match(findingText({ code: "signals_missing", detail: { missing: ["lap_invalidated"], empty: ["track_edge"] } }), /lap_invalidated, track_edge/);
assert.match(findingText({ code: "coverage_gap", detail: { seconds: 1.4 } }), /1\.4 s/);
assert.match(findingText({ code: "position_jump", detail: { advance_m: 312, by_speed_m: 12 } }), /312 m .*12 m/);
assert.match(findingText({ code: "frozen_samples", detail: { seconds: 1.5 } }), /1\.5 s of repeated samples/);
assert.match(findingText({ code: "not_verifiable" }), /cannot be checked/);
for (const code of ["signals_missing", "insufficient_evidence", "coverage_gap", "low_frame_rate", "position_jump",
  "speed_distance_mismatch", "implausible_acceleration", "frozen_samples", "telemetry_reused", "check_failed", "unsupported_inputs"]) {
  assert.notEqual(findingText({ code, detail: {} }), code, code);
  assert.notEqual(ruleTitle(code), code, code);
}
assert.match(tierText("quality"), /review only/);

// ---------------------------------------------------------------- the verdict line
const { validityText } = await import("../../web/js/analysis/health.js");
assert.match(validityText({ is_valid: true, review_status: "held" }), /counts after an admin review/);
assert.match(validityText({ is_valid: true, review_status: "quarantined" }), /integrity check/);
assert.equal(validityText({ is_valid: true, review_status: null }), "Lap valid");

// ---------------------------------------------------------------- the Integrity tab
const { renderIntegrityTab } = await import("../../web/js/pages/admin.js");
const tab = await renderIntegrityTab("tok");
document.getElementById("root").appendChild(tab);
const row = tab.querySelector('[data-quarantined="7"]');
assert.ok(row, "the quarantined lap is listed");
assert.match(text(row), /Spa/);
assert.match(text(row), /content changed/);
assert.match(text(row), /req-1/);
assert.match(text(tab), /12 laps stored, 3 of them not signed yet/);
assert.match(text(tab), /Chain head: row 41/);
assert.ok(tab.querySelector('[data-audit="lap.quarantined"]'), "the audit log is shown");

// check again: nothing released
row.querySelectorAll("button")[0].click();
await tick(); await tick();
assert.ok(calls.includes("POST /admin/laps/7/integrity"), calls.join("\n"));
assert.ok(tab.querySelector('[data-quarantined="7"]'));

// release: the lap disappears from the list
tab.querySelector('[data-quarantined="7"]').querySelectorAll("button")[1].click();
await tick(); await tick(); await tick();
assert.ok(calls.includes("POST /admin/laps/7/integrity?release=true"), calls.join("\n"));
assert.equal(tab.querySelector('[data-quarantined="7"]'), null);
assert.match(text(tab), /Nothing set aside/);

// the chain check reports where it broke
[...tab.querySelectorAll("button")].find((b) => /Verify the chain/.test(b.textContent)).click();
await tick(); await tick();
assert.match(text(tab), /Chain broken at row 12/);

console.log("V099-OK");
