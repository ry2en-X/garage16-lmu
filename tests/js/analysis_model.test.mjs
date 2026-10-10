// Plain-Node test of the analysis view's logic (web/js/analysis/model.js) — no DOM needed.
import assert from "node:assert/strict";
import {
  basicDataNotice, basicTagText, BASIC_DATA_MISSING, FULL_DATA_CLIENT, isBasicLap,
  buildCursorRows, clamp, createViewState, distanceAt, divergingColor, endDistance, fitView, formatChannelValue,
  formatDelta, formatValue, indexForDistance, nearestIndex, niceTicks, normalizeForColor, physicsSnapshot,
  rampColor, sectorDeltas, valueRange, WHEELS,
} from "../../web/js/analysis/model.js";

const grid = { start: 2, step: 2, count: 101 }; // 2 m ... 202 m

// ---- grid
assert.equal(distanceAt(grid, 0), 2);
assert.equal(distanceAt(grid, 100), 202);
assert.equal(endDistance(grid), 202);
assert.equal(indexForDistance(grid, 2), 0);
assert.equal(indexForDistance(grid, 101), 50);
assert.equal(indexForDistance(grid, -50), 0);       // clamped
assert.equal(indexForDistance(grid, 9999), 100);

// ---- ticks / ranges
assert.deepEqual(niceTicks(0, 100, 5), [0, 20, 40, 60, 80, 100]);
assert.deepEqual(niceTicks(0, 1, 5), [0, 0.2, 0.4, 0.6, 0.8, 1]);
assert.deepEqual(niceTicks(5, 5), [5]);
assert.ok(niceTicks(-0.3, 1.1, 4).every((t) => t >= -0.3 && t <= 1.1));
assert.deepEqual(valueRange([[1, 3, null], [2, 9]], null).map((v) => Math.round(v * 100) / 100), [0.52, 9.48]);
assert.deepEqual(valueRange([[1, 2]], [0, 100]), [0, 100]);
assert.deepEqual(valueRange([null, [null]], null), [0, 1]);
const flat = valueRange([[5, 5, 5]], null);
assert.ok(flat[0] < 5 && flat[1] > 5);

// ---- formatting
assert.equal(formatValue(null), "—");
assert.equal(formatValue(NaN, "km/h"), "—");
assert.equal(formatValue(123.456, "km/h"), "123.5 km/h");
assert.equal(formatValue(1234.56, "N"), "1235 N");
assert.equal(formatValue(0.456, "G"), "0.46 G");
assert.equal(formatValue(3.4, "", true), "3");
assert.equal(formatChannelValue("gear", -1, { unit: "", discrete: true }), "R");
assert.equal(formatChannelValue("gear", 0, { unit: "", discrete: true }), "N");
assert.equal(formatChannelValue("gear", 5, { unit: "", discrete: true }), "5");
assert.equal(formatChannelValue("gear", null, { unit: "", discrete: true }), "—");
assert.equal(formatDelta(0.1234), "+0.123");
assert.equal(formatDelta(-0.0456), "−0.046");
assert.equal(formatDelta(0), "±0.000");
assert.equal(formatDelta(null), "—");

// ---- cursor rows
const data = {
  grid,
  delta: Array.from({ length: 101 }, (_, i) => i * 0.01),         // A loses 1.0 s over the lap
  sectors: { a: [70, 140], b: [70, 140] },
  channels: {
    speed: { label: "Speed", unit: "km/h", group: "Speed & inputs", discrete: false,
      a: Array.from({ length: 101 }, (_, i) => 100 + i), b: Array.from({ length: 101 }, (_, i) => 90 + i) },
    gear: { label: "Gear", unit: "", group: "Speed & inputs", discrete: true,
      a: Array.from({ length: 101 }, () => 4), b: Array.from({ length: 101 }, () => 3) },
    abs_level: { label: "ABS", unit: "level", group: "Driver aids", discrete: true,
      a: null, b: Array.from({ length: 101 }, () => 6) },
  },
};
const rows = buildCursorRows(data, 10, ["speed", "gear", "abs_level", "missing"]);
assert.equal(rows.length, 3);                                    // unknown key skipped
assert.equal(rows[0].a, 110);
assert.equal(rows[0].b, 100);
assert.equal(rows[0].diff, 10);
assert.equal(rows[1].aText, "4");
assert.equal(rows[2].a, null);                                   // lap A lacks the channel: null, not 0
assert.equal(rows[2].diff, null);
assert.equal(rows[2].aText, "—");
assert.equal(rows[2].bText, "6 level");

// ---- sector deltas (positive = A lost time)
const sd = sectorDeltas(data);
assert.ok(Math.abs(sd[0] - 0.34) < 1e-9);        // distance 70 -> index 34 -> 0.34 s
assert.ok(Math.abs(sd[1] - 0.35) < 1e-9);        // index 69 -> 0.69 s ; 0.69 - 0.34
assert.ok(Math.abs(sd[2] - 0.31) < 1e-9);        // end 1.00 - 0.69
assert.ok(Math.abs(sd[0] + sd[1] + sd[2] - 1.0) < 1e-9);
assert.equal(sectorDeltas({ ...data, delta: null }), null);
assert.equal(sectorDeltas({ ...data, sectors: { a: [] } }), null);

// ---- colours
assert.equal(rampColor(0), "rgb(44,92,210)");
assert.equal(rampColor(1), "rgb(224,71,43)");
assert.equal(rampColor(-5), rampColor(0));
assert.equal(rampColor(9), rampColor(1));
assert.equal(rampColor(0.5), "rgb(76,167,113)");
assert.notEqual(divergingColor(-1), divergingColor(1));
assert.equal(divergingColor(0), "rgb(140,145,153)");
const norm = normalizeForColor([0, 50, 100, null], [0, 100]);
assert.deepEqual(norm, [0, 0.5, 1, null]);
const spike = normalizeForColor([...Array.from({ length: 200 }, (_, i) => i), 100000], null);
assert.ok(spike[100] > 0.3 && spike[100] < 0.7);                 // one spike does not flatten the scale
assert.deepEqual(normalizeForColor([null, NaN], null), [null, null]);
assert.deepEqual(normalizeForColor([5, 5, 5], null), [0.5, 0.5, 0.5]);

// ---- map geometry
const xs = [0, 100, 100, 0];
const ys = [0, 0, 50, 50];
const fit = fitView(xs, ys, 300, 200, 10);
assert.ok(Math.abs(fit.scale - 2.8) < 1e-9);                      // limited by width: (300-20)/100
assert.ok(fit.toY(0) > fit.toY(50));                              // Y points up on screen
assert.ok(fit.toX(100) - fit.toX(0) - 280 < 1e-9);
assert.equal(fitView([null], [null], 100, 100), null);
assert.equal(nearestIndex(xs, ys, 99, 1), 1);
assert.equal(nearestIndex(xs, ys, 1, 49), 3);
assert.equal(nearestIndex(xs, ys, 500, 500, 10), -1);             // too far away
assert.equal(nearestIndex([null, 5], [null, 5], 0, 0), 1);        // nulls skipped

// ---- physics snapshot
const pdata = {
  grid,
  channels: {
    lat_g: { a: Array(101).fill(1.2), b: null },
    susp_travel_fl: { a: Array(101).fill(41.5), b: Array(101).fill(39) },
    tire_load_fl: { a: Array(101).fill(5000), b: null },
  },
};
const snapA = physicsSnapshot(pdata, 3, "a");
const snapB = physicsSnapshot(pdata, 3, "b");
assert.equal(snapA.latG, 1.2);
assert.equal(snapB.latG, null);
assert.equal(snapA.wheels.fl.travel, 41.5);
assert.equal(snapB.wheels.fl.travel, 39);
assert.equal(snapB.wheels.fl.load, null);
assert.equal(snapA.wheels.rr.travel, null);                       // channel absent -> null, never 0
assert.deepEqual(Object.keys(snapA.wheels), WHEELS);

// ---- shared view state
const state = createViewState(data);
const seen = [];
state.subscribe((what) => seen.push(what));
assert.equal(state.getCursor(), 0);
state.hoverCursor(10);
assert.equal(state.getCursor(), 10);
state.hoverCursor(10);                                            // no change -> no event
assert.deepEqual(seen, ["cursor"]);
state.pinCursor(20);
assert.equal(state.getCursor(), 20);
assert.equal(state.isPinned(), true);
state.hoverCursor(50);                                            // ignored while pinned
assert.equal(state.getCursor(), 20);
state.setCursor(30);                                              // explicit set (arrow keys) still works
assert.equal(state.getCursor(), 30);
state.pinCursor(30);                                              // same spot again -> release
assert.equal(state.isPinned(), false);
state.hoverCursor(60);
assert.equal(state.getCursor(), 60);
state.setCursor(9999);
assert.equal(state.getCursor(), 100);                             // clamped

assert.deepEqual(state.getView(), [2, 202]);
state.zoom(0.5, 102);                                             // zoom in 2x around the middle
let [lo, hi] = state.getView();
assert.ok(Math.abs(hi - lo - 100) < 1e-9 && lo < 102 && hi > 102);
state.pan(-1000);                                                 // cannot leave the lap
assert.equal(state.getView()[0], 2);
state.pan(1000);
assert.equal(state.getView()[1], 202);
state.zoom(0.0001, 100);                                          // cannot zoom below the minimum span
[lo, hi] = state.getView();
assert.ok(hi - lo >= 25 - 1e-9);
state.resetView();
assert.deepEqual(state.getView(), [2, 202]);
state.zoom(5, 100);                                               // zooming out past the lap stays at the lap
assert.deepEqual(state.getView(), [2, 202]);
assert.equal(clamp(5, 0, 3), 3);

// ---- laps from older clients (< 0.9.0)
assert.equal(FULL_DATA_CLIENT, "0.9.0");
assert.equal(basicTagText(null), null);
assert.equal(basicTagText({ level: "full", client_version: "0.9.0" }), null);
assert.equal(basicTagText({ level: "basic", client_version: "0.8.6" }), "Basic data · client 0.8.6");
assert.equal(basicTagText({ level: "basic", client_version: null }), "Basic data · older client");
assert.equal(isBasicLap({ data_level: "basic" }), true);
assert.equal(isBasicLap({ data_level: "full" }), false);
assert.equal(isBasicLap({}), false);
assert.equal(isBasicLap(null), false);

const laps = { a: { driver_name: "Me" }, b: { driver_name: "Rival" } };
const rec = (level, v, n) => ({ level, client_version: v, channels_available: n, channels_total: 96 });
assert.equal(basicDataNotice({ laps, recording: { a: rec("full", "0.9.0", 96), b: rec("full", "0.9.0", 96) } }), null);
assert.equal(basicDataNotice({ laps }), null, "a server without the field says nothing");
const onlyA = basicDataNotice({ laps, recording: { a: rec("basic", "0.8.6", 31), b: rec("full", "0.9.0", 96) } });
assert.ok(onlyA.includes("Lap 1 (Me)") && onlyA.includes("client 0.8.6") && onlyA.includes("31 of 96"));
assert.ok(!onlyA.includes("Lap 2 (Rival)"));
assert.ok(onlyA.includes("client 0.9.0 or newer") && onlyA.includes(BASIC_DATA_MISSING));
assert.ok(onlyA.includes("update the client and drive the lap again"));
const both = basicDataNotice({ laps, recording: { a: rec("basic", "0.8.6", 31), b: rec("basic", null, 31) } });
assert.ok(both.includes("Lap 1 (Me)") && both.includes("Lap 2 (Rival)") && both.includes("an older client"));
const single = basicDataNotice({ laps: { a: { driver_name: "Me" }, b: null }, recording: { a: rec("basic", "0.8.6", 31), b: null } });
assert.ok(single.includes("Lap 1 (Me)") && !single.includes("Lap 2"));

console.log("ANALYSIS-MODEL-OK");
