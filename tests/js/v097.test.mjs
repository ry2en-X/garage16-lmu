// V0.9.7 — measured statistics with their uncertainty (stints), hints that respect the
// measuring accuracy, the corner ranking, and the data-quality texts.
import assert from "node:assert/strict";

const S = await import("../../web/js/stints/stats.js");

// ---------------------------------------------------------------- Student t, CIs
assert.ok(Math.abs(S.t95(10) - 2.228) < 0.005, String(S.t95(10)));
assert.ok(Math.abs(S.t95(3) - 3.182) < 0.03, String(S.t95(3)));
assert.ok(Math.abs(S.t95(1000) - 1.962) < 0.002);

// a deterministic pseudo-random noise
let seed = 7;
const noise = () => { seed = (seed * 16807) % 2147483647; return (seed / 2147483647 - 0.5) * 0.3; };

// degradation 0.05 s / lap + noise: the CI holds the true slope and excludes 0
const ages = Array.from({ length: 20 }, (_, i) => i + 1);
const times = ages.map((a) => 100 + 0.05 * a + noise());
const fit = S.theilSen(ages, times);
assert.ok(fit.ci && fit.ci[0] <= 0.05 && fit.ci[1] >= 0.05, JSON.stringify(fit.ci));
assert.ok(fit.ci[0] > 0, "a clear degradation is distinguishable from zero");

// tyres 0.04 s/lap, fuel 0.03 s/l, from two stints with different fuel at the same tyre age
const x1 = []; const x2 = []; const y = [];
for (const fuelStart of [90, 50]) for (let age = 1; age <= 12; age += 1) {
  const fuel = fuelStart - 3 * age;
  x1.push(age); x2.push(fuel); y.push(100 + 0.04 * age + 0.03 * fuel + noise() / 3);
}
const r = S.regress2(x1, x2, y);
assert.ok(r.ok, JSON.stringify(r));
assert.ok(r.ci1[0] <= 0.04 && r.ci1[1] >= 0.04, JSON.stringify(r.ci1));
assert.ok(r.ci2[0] <= 0.03 && r.ci2[1] >= 0.03, JSON.stringify(r.ci2));
assert.ok(Math.abs(r.b1 - 0.04) < 0.01 && Math.abs(r.b2 - 0.03) < 0.01);

// consistency without the trend: a perfectly even driver on degrading tyres is consistent
const even = ages.map((a) => 100 + 0.08 * a);
const p = S.detrendedSpread(even);
assert.ok(p.sigma < 1e-9 && Math.abs(p.trend - 0.08) < 1e-9);
assert.ok(S.stdev(even) > 0.4, "while the plain spread blames the tyres on the driver");

// ---------------------------------------------------------------- hints and measuring accuracy
const { cornerHints, pointUncertainty } = await import("../../web/js/analysis/coaching.js");
const corner = (aBrake, bBrake, res) => ({
  n: 1, start: 0, apex: 200, end: 400, delta: 0.1,
  a: { brake: aBrake, min: 100, exit: 150, throttle: 260, res: { brake: res, throttle: 1 } },
  b: { brake: bBrake, min: 100, exit: 150, throttle: 260, res: { brake: res, throttle: 1 } },
});
assert.equal(pointUncertainty(corner(100, 108, 1.4).a, corner(100, 108, 1.4).b, "brake"), 2.8);
assert.deepEqual(cornerHints(corner(100, 108, 1.4)).map((h) => h.kind), ["brake_later"], "8 m > 5 m and > 2.8 m");
assert.equal(cornerHints(corner(100, 108, 1.4))[0].uncertainty, 2.8);
assert.deepEqual(cornerHints(corner(100, 108, 5)).map((h) => h.kind), [], "8 m is within ±10 m (samples missing there): no hint");

// ---------------------------------------------------------------- the ranking
const { JSDOM } = await import("jsdom");
const dom = new JSDOM("<!doctype html><body></body>");
globalThis.window = dom.window; globalThis.document = dom.window.document;
const { rankCorners } = await import("../../web/js/analysis/widgets.js");
const ranked = rankCorners([{ n: 1, delta: 0.1 }, { n: 2, delta: -0.2 }, { n: 3, delta: 0.3 }]);
assert.deepEqual(ranked.map((r) => r.corner.n), [3, 1, 2]);
assert.ok(Math.abs(ranked[0].share - 0.75) < 1e-9 && ranked[2].share === 0);

// ---------------------------------------------------------------- data quality texts
const { healthLines, validityText } = await import("../../web/js/analysis/health.js");
const lines = healthLines({ checks: [
  { key: "gaps", status: "warn", values: { count: 1, longest_s: 0.6, at_m: 1500 } },
  { key: "resolution", status: "ok", values: { metres: 1.44 } },
  { key: "time", status: "bad", values: { restart: true, largest_back_s: 40 } },
] });
assert.equal(lines.length, 3);
assert.ok(lines[0].text.includes("0.60") && lines[0].text.includes("1.50 km"), lines[0].text);
assert.ok(lines[1].text.includes("±1.4 m"), lines[1].text);
assert.ok(lines[2].text.includes("restart"), lines[2].text);
assert.equal(validityText({ is_valid: false, reasons: ["track limits"] }), "Lap invalid: track limits");

console.log("V097-OK");
