// Plain-Node tests (no DOM) of the V0.9.1 analysis logic (grid layout: V0.9.5): the layout model and its
// storage, colours, the delta / sector summary, touch gestures, the window registry.
import assert from "node:assert/strict";

import {
  addWidget, canAdd, clearStoredLayout, COLS, compactLayout, createLayoutStore, defaultLayout, findFreeSpot, fitsSize, LAYOUT_VERSION, layoutsEqual, loadLayout,
  MAX_H, MAX_WIDGETS, migrate, MIGRATIONS, placeWidget, pushDown, removeWidget, requiredChannels, resizeWidget, sanitizeLayout, saveLayout, setChannel, setColors,
  setSize, setTrackmapMode, setWidgetColors, shiftWidget, STORAGE_KEY, LEGACY_CHANNEL_KEY, TILE_SPECS, tileSpec, toStored, TYPES,
} from "../../web/js/analysis/layout.js";
import { applyColors, DEFAULT_COLORS, isHexColor, LAP_COLORS } from "../../web/js/analysis/model.js";
import { chooseMaxPoints, deltaFacts, factWindowMetres, sectorTable } from "../../web/js/analysis/insights.js";
import { createGestureTracker } from "../../web/js/analysis/touch.js";
import { MODULES, moduleForType } from "../../web/js/analysis/modules.js";
import { baseAdapter, covers, isUsable, planRefinement, REFINE } from "../../web/js/analysis/refine.js";
import { createViewState, distanceAt } from "../../web/js/analysis/model.js";
import { buildTileCatalog, KINDS, presetsFor, PURPOSES, purposeOfChannel, tileTree } from "../../web/js/analysis/tile_catalog.js";

const types = (layout) => layout.widgets.map((w) => (w.channel ? `${w.type}:${w.channel}` : w.type));
const valid = new Set(["speed", "throttle", "brake", "gear", "rpm", "lat_g", "long_g", "yaw_rate"]);

function fakeStorage(initial = {}) {
  const data = { ...initial };
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
    removeItem: (k) => { delete data[k]; },
  };
}

const overlap = (p, q) => p.x < q.x + q.w && q.x < p.x + p.w && p.y < q.y + q.h && q.y < p.y + p.h;

// a layout is "renderable": known types, unique ids, no duplicate singles/channels, not empty,
// every tile inside the grid and at least its minimum size, no two tiles overlapping,
// widgets in reading order
function assertRenderable(layout) {
  assert.equal(layout.version, LAYOUT_VERSION);
  assert.ok(layout.widgets.length >= 1 && layout.widgets.length <= MAX_WIDGETS);
  assert.equal(new Set(layout.widgets.map((w) => w.id)).size, layout.widgets.length, "ids unique");
  const singles = layout.widgets.filter((w) => w.type !== "chart" && w.type !== "histogram").map((w) => w.type);
  assert.equal(new Set(singles).size, singles.length, "single windows once");
  const channels = layout.widgets.filter((w) => w.type === "chart").map((w) => w.channel);
  assert.equal(new Set(channels).size, channels.length, "one window per channel");
  for (const w of layout.widgets) {
    assert.ok(TYPES.includes(w.type));
    assert.ok(["small", "medium", "large"].includes(w.size), "the rough size is derived");
    for (const k of ["x", "y", "w", "h"]) assert.ok(Number.isInteger(w[k]) && w[k] >= 0, `${w.type}.${k} = ${w[k]}`);
    assert.ok(w.x + w.w <= COLS, `${w.type} inside the 12 columns`);
    assert.ok(fitsSize(w.type, w.w, w.h), `${w.type} ${w.w}x${w.h} >= its minimum`);
    if (w.type === "chart") assert.match(w.channel, /^[a-z0-9_]{1,48}$/);
  }
  for (let i = 0; i < layout.widgets.length; i++) {
    for (let j = i + 1; j < layout.widgets.length; j++) assert.ok(!overlap(layout.widgets[i], layout.widgets[j]), `${types(layout)[i]} overlaps ${types(layout)[j]}`);
    if (i) {
      const p = layout.widgets[i - 1];
      const q = layout.widgets[i];
      assert.ok(p.y < q.y || (p.y === q.y && p.x <= q.x), "reading order");
    }
  }
  for (const key of ["a", "b", "delta"]) assert.ok(isHexColor(layout.colors[key]), `colour ${key}`);
}

// ---------------------------------------------------------------- the tile sizes
for (const [type, spec] of Object.entries(TILE_SPECS)) {
  assert.ok(TYPES.includes(type), type);
  assert.ok(fitsSize(type, ...spec.def), `${type}: the default size is allowed`);
  for (const preset of spec.presets) assert.ok(fitsSize(type, ...preset), `${type}: preset ${preset} >= minimum and inside the grid`);
}
assert.deepEqual(Object.keys(TILE_SPECS).sort(), [...TYPES].sort(), "every tile type has sizes");
assert.equal(fitsSize("chart", 2, 3), false, "below the minimum");
assert.equal(fitsSize("chart", 13, 3), false, "wider than the grid");
assert.equal(fitsSize("chart", 3, MAX_H + 1), false);
assert.equal(fitsSize("chart", 3.5, 3), false);

// ---------------------------------------------------------------- the standard layout
const standard = defaultLayout();
assertRenderable(standard);
// V0.9.3: coaching and the track-limits/events window appended (earlier windows keep their place)
assert.deepEqual(types(standard), ["summary", "delta", "corners", "chart:speed", "chart:throttle", "chart:brake", "trackmap", "cursor_table", "physics", "coaching", "events", "line", "corner_detail"]);
assert.equal(standard.widgets.find((w) => w.type === "trackmap").mode, "delta", "the track map defaults to Delta");
const mapTile = standard.widgets.find((w) => w.type === "trackmap");
const valuesTile = standard.widgets.find((w) => w.type === "cursor_table");
assert.deepEqual([mapTile.y, mapTile.x, valuesTile.y, valuesTile.x], [32, 0, 32, 6], "track map and values side by side");
assert.deepEqual(standard.colors, { a: DEFAULT_COLORS.a, b: DEFAULT_COLORS.b, delta: DEFAULT_COLORS.delta });
assert.notEqual(defaultLayout(), defaultLayout(), "every call is a fresh object (nobody can mutate the standard)");
assert.deepEqual(requiredChannels(standard), ["speed", "throttle", "brake"], "the standard layout asks the server for three channels");

// stored shape = the specified one
const stored = toStored(standard);
assert.equal(stored.version, 2);
assert.deepEqual(stored.layout[3], { type: "chart", x: 0, y: 22, w: 12, h: 4, channel: "speed" });
assert.deepEqual(Object.keys(stored.colors).sort(), ["delta", "lapA", "lapB"]);
assert.ok(layoutsEqual(sanitizeLayout(stored), standard), "stored -> sanitised round-trips");

// ---------------------------------------------------------------- whatever is stored, the layout is valid
const garbage = [
  undefined, null, 0, 1, "", "x", true, [], [1, 2], {}, { version: 1 }, { version: 1, layout: null }, { version: 1, layout: "x" },
  { version: 1, layout: [] }, { version: 1, layout: [null, 5, "a", []] }, { version: 1, layout: [{ type: "evil" }] },
  { version: 3, layout: [{ type: "chart", channel: "speed" }] }, { version: 0, layout: [{ type: "chart", channel: "speed" }] },
  { version: "1", layout: [{ type: "chart", channel: "speed" }] }, { version: -1 }, { version: 1.5, layout: [] },
  { version: 1, layout: [{ type: "chart" }] }, { version: 1, layout: [{ type: "chart", channel: "../../etc/passwd" }] },
  { version: 1, layout: [{ type: "chart", channel: "SPEED" }] }, { version: 1, layout: [{ type: "chart", channel: "x".repeat(100) }] },
  { version: 1, layout: [{ type: "chart", channel: "speed", size: "huge", position: "first" }] },
  { version: 1, layout: [{ type: "chart", channel: "speed" }], colors: "red" },
  { version: 1, layout: [{ type: "chart", channel: "speed" }], colors: { lapA: 5, lapB: null, delta: {} } },
  JSON.parse('{"version":1,"layout":[{"type":"__proto__","channel":"speed"}],"__proto__":{"x":1}}'),
  // version 2: cells that make no sense
  { version: 2, layout: [{ type: "chart", channel: "speed", x: -5, y: -1, w: 99, h: 0 }] },
  { version: 2, layout: [{ type: "chart", channel: "speed", x: "a", y: null, w: {}, h: [] }] },
  { version: 2, layout: [{ type: "chart", channel: "speed", x: 11, y: 1e12, w: 12, h: 1e9 }] },
  { version: 2, layout: [{ type: "chart", channel: "speed", x: NaN, y: Infinity, w: 3.7, h: 2.2 }] },
  { version: 2, layout: Array.from({ length: 10 }, (_, i) => ({ type: "chart", channel: ["speed", "throttle", "brake", "gear", "rpm", "lat_g", "long_g", "yaw_rate", "speed", "gear"][i], x: 0, y: 0, w: 12, h: 4 })) },
];
for (const input of garbage) {
  let layout;
  assert.doesNotThrow(() => { layout = sanitizeLayout(input, { validChannels: valid }); }, `sanitize(${JSON.stringify(input)})`);
  assertRenderable(layout);
}
// version 2: everything piled on one spot is laid out underneath each other, in stored order
const piled = sanitizeLayout(garbage.at(-1), { validChannels: valid });
assert.deepEqual(piled.widgets.map((w) => w.y), [0, 4, 8, 12, 16, 20, 24, 28], "overlapping tiles move down, duplicates dropped");
assert.equal(sanitizeLayout(garbage.at(-3), { validChannels: valid }).widgets[0].y <= 400, true, "a huge y is clamped");
const clamped = sanitizeLayout(garbage.at(-5), { validChannels: valid }).widgets[0];
assert.deepEqual([clamped.x, clamped.y, clamped.w, clamped.h], [0, 0, 12, 3], "out-of-range cells are clamped into the grid and to the minimum");

// version 1 (V0.9.1–V0.9.4) is migrated: bad entries dropped, the rest flowed onto the grid in its old order
const mixed = sanitizeLayout({
  version: 1,
  layout: [
    { type: "chart", channel: "speed", size: "small", position: 1 },
    { type: "bogus" }, { type: "chart", channel: "speed" }, { type: "chart", channel: "no_such_channel" },
    { type: "trackmap", mode: "weird", position: 0 }, { type: "trackmap" }, { type: "chart", channel: "rpm", size: "nope" },
  ],
}, { validChannels: valid });
assert.deepEqual(types(mixed), ["trackmap", "chart:speed", "chart:rpm"], "ordered by position; unknown type/channel and duplicates dropped");
assert.equal(mixed.widgets[0].mode, "delta", "an unknown map mode falls back");
assert.deepEqual([mixed.widgets[1].x, mixed.widgets[1].w, mixed.widgets[1].size], [6, 4, "small"], "small = 4 columns, next to the 6-wide map");
assert.equal(mixed.widgets[2].w, 12, "an unknown size falls back to the tile's default width");
const tooMany = sanitizeLayout({ version: 1, layout: Array.from({ length: 200 }, (_, i) => ({ type: "chart", channel: `ch_${i}` })) });
assert.ok(tooMany.widgets.length <= MAX_WIDGETS);

// The V0.9.4 standard layout as stored by V0.9.4 migrates to a grid that shows the same thing
const v094 = { version: 1, layout: [
  { type: "summary", size: "large", position: 0 }, { type: "delta", size: "large", position: 1 }, { type: "corners", size: "large", position: 2 },
  { type: "chart", channel: "speed", size: "large", position: 3 }, { type: "chart", channel: "throttle", size: "small", position: 4 },
  { type: "chart", channel: "brake", size: "small", position: 5 }, { type: "chart", channel: "gear", size: "small", position: 6 },
  { type: "trackmap", size: "medium", position: 7, mode: "speed" }, { type: "cursor_table", size: "medium", position: 8 },
], colors: { lapA: "#112233", lapB: "#445566", delta: "#778899" } };
const migratedV094 = sanitizeLayout(v094, { validChannels: valid });
assertRenderable(migratedV094);
assert.deepEqual(types(migratedV094), ["summary", "delta", "corners", "chart:speed", "chart:throttle", "chart:brake", "chart:gear", "trackmap", "cursor_table"], "same tiles, same order");
const row = migratedV094.widgets.filter((w) => ["throttle", "brake", "gear"].includes(w.channel));
assert.deepEqual(row.map((w) => [w.x, w.w]), [[0, 4], [4, 4], [8, 4]], "three small windows share a row, as they did");
assert.equal(new Set(row.map((w) => w.y)).size, 1);
assert.equal(migratedV094.widgets.find((w) => w.type === "trackmap").mode, "speed", "settings survive");
assert.deepEqual(migratedV094.colors, { a: "#112233", b: "#445566", delta: "#778899" }, "colours survive");

// ---------------------------------------------------------------- colours: the defaults cannot be broken
for (const bad of ["red", "#fff", "#GGGGGG", "#12345", "#1234567", 5, null, {}, [], "javascript:alert(1)", "#e0472b; background:url(x)"]) {
  assert.equal(isHexColor(bad), false, String(bad));
  const layout = sanitizeLayout({ version: 1, layout: [{ type: "chart", channel: "speed" }], colors: { lapA: bad, lapB: bad, delta: bad } });
  assert.deepEqual(layout.colors, { a: DEFAULT_COLORS.a, b: DEFAULT_COLORS.b, delta: DEFAULT_COLORS.delta }, `bad colour ${String(bad)} falls back`);
}
const partial = sanitizeLayout({ version: 1, layout: [{ type: "chart", channel: "speed" }], colors: { lapA: "#00FF00", lapB: "nope" } });
assert.deepEqual(partial.colors, { a: "#00ff00", b: DEFAULT_COLORS.b, delta: DEFAULT_COLORS.delta }, "per entry: the valid one is kept, the rest falls back");
applyColors({ a: "#112233", b: "garbage", delta: undefined });
assert.deepEqual({ ...LAP_COLORS }, { a: "#112233", b: DEFAULT_COLORS.b, delta: DEFAULT_COLORS.delta });
applyColors(null);
assert.deepEqual({ ...LAP_COLORS }, { ...DEFAULT_COLORS }, "applying nothing restores the defaults");
assert.throws(() => { DEFAULT_COLORS.a = "#000000"; }, TypeError, "the built-in colours are frozen");
const colored = sanitizeLayout({ version: 2, layout: [{ type: "chart", channel: "speed", x: 0, y: 0, w: 12, h: 4, colors: { a: "#ABCDEF", b: "bad" } }] });
assert.deepEqual(colored.widgets[0].colors, { a: "#abcdef" });

// ---------------------------------------------------------------- versions
const steps = { 1: (old) => ({ ...old, layout: old.layout.map((w) => ({ ...w, size: "small" })) }) };
const upgraded = migrate({ version: 1, layout: [{ type: "chart", channel: "speed", size: "large" }] }, steps, 2);
assert.equal(upgraded.version, 2);
assert.equal(upgraded.layout[0].size, "small", "a migration step runs");
assert.equal(migrate({ version: 1, layout: [] }, {}, 2), null, "a missing step is not guessed");
assert.equal(migrate({ version: 3, layout: [] }), null, "a layout from a newer Garage16 is ignored, not misread");
assert.equal(migrate({ layout: [] }), null);
assert.equal(migrate(null), null);
assert.equal(typeof MIGRATIONS[1], "function", "version 1 -> 2 exists");
const flowed = migrate({ version: 1, layout: [{ type: "chart", channel: "speed", size: "medium" }, { type: "chart", channel: "rpm", size: "medium" }, { type: "chart", channel: "gear", size: "medium" }] });
assert.deepEqual(flowed.layout.map((w) => [w.x, w.y, w.w]), [[0, 0, 6], [6, 0, 6], [0, 4, 6]], "two medium windows per row, like the old CSS grid");
assert.ok(flowed.layout.every((w) => !("size" in w) && !("position" in w)), "the old fields are gone");

// ---------------------------------------------------------------- storage
let s = fakeStorage();
let loaded = loadLayout(s, { validChannels: valid });
assert.equal(loaded.source, "default");
assert.ok(layoutsEqual(loaded.layout, standard), "a new user sees the standard layout, nothing to configure");

s = fakeStorage({ [STORAGE_KEY]: "{not json" });
loaded = loadLayout(s, { validChannels: valid });
assert.equal(loaded.source, "default");
assert.equal(loaded.storageOk, true, "a corrupt value is not a storage failure");

s = fakeStorage({ [STORAGE_KEY]: JSON.stringify({ version: 99, layout: [{ type: "chart", channel: "rpm" }] }) });
assert.equal(loadLayout(s, { validChannels: valid }).source, "default", "an unknown future version is ignored");

s = fakeStorage({ [STORAGE_KEY]: JSON.stringify(v094) });
loaded = loadLayout(s, { validChannels: valid });
assert.equal(loaded.source, "stored", "a V0.9.4 layout loads (migrated)");
assert.ok(layoutsEqual(loaded.layout, migratedV094));
assert.equal(JSON.parse(s.data[STORAGE_KEY]).version, 1, "loading never writes");

const custom = placeWidget(addWidget(standard, { type: "chart", channel: "rpm", w: 6, h: 3 }, valid), "w1", 0, 90);
assert.equal(saveLayout(s, custom), true);
loaded = loadLayout(s, { validChannels: valid });
assert.equal(loaded.source, "stored");
assert.ok(layoutsEqual(loaded.layout, custom), "what was saved is what loads");
assert.ok(!JSON.stringify(s.data).includes("lap_dist"), "only layout JSON is stored, never telemetry");

const throwing = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("quota"); }, removeItem() { throw new Error("denied"); } };
assert.equal(loadLayout(throwing, { validChannels: valid }).source, "default");
assert.equal(loadLayout(throwing, { validChannels: valid }).storageOk, false, "unavailable storage is reported");
assert.equal(saveLayout(throwing, standard), false);
assert.equal(clearStoredLayout(throwing), false);

// V0.9.0 users keep their channel selection
s = fakeStorage({ [LEGACY_CHANNEL_KEY]: JSON.stringify(["speed", "gear", "bogus_channel", "rpm"]) });
loaded = loadLayout(s, { validChannels: valid });
assert.equal(loaded.source, "legacy");
assert.deepEqual(types(loaded.layout), ["summary", "delta", "corners", "chart:speed", "chart:gear", "chart:rpm", "trackmap", "cursor_table", "physics"]);
assertRenderable(loaded.layout);
s = fakeStorage({ [LEGACY_CHANNEL_KEY]: "[]" });
assert.equal(loadLayout(s, { validChannels: valid }).source, "default");
s = fakeStorage({ [LEGACY_CHANNEL_KEY]: "garbage" });
assert.equal(loadLayout(s, { validChannels: valid }).source, "default");

// reset: removes the layout (and the old selection) and NOTHING else
s = fakeStorage({ [STORAGE_KEY]: JSON.stringify(toStored(custom)), [LEGACY_CHANNEL_KEY]: "[]", garage16_session_note: "keep", lmu_garage_session: "keep" });
const seen = [];
const store = createLayoutStore(s, { validChannels: valid, onColors: (c) => seen.push({ ...c }) });
assert.ok(layoutsEqual(store.get(), custom));
assert.equal(store.isPersisted(), true);
store.reset();
assert.ok(layoutsEqual(store.get(), standard));
assert.deepEqual(Object.keys(s.data).sort(), ["garage16_session_note", "lmu_garage_session"], "only the UI layout keys were removed");
assert.deepEqual(seen.at(-1), { a: DEFAULT_COLORS.a, b: DEFAULT_COLORS.b, delta: DEFAULT_COLORS.delta }, "reset also restores the colours");
store.set(setColors(store.get(), { a: "#00ff00" }));
assert.equal(JSON.parse(s.data[STORAGE_KEY]).colors.lapA, "#00ff00");
assert.equal(seen.at(-1).a, "#00ff00", "the store announces new colours");
const brokenStore = createLayoutStore(throwing, { validChannels: valid });
brokenStore.set(resizeWidget(brokenStore.get(), "w4", 6, 4));
assert.equal(brokenStore.isPersisted(), false);
assert.equal(brokenStore.get().widgets.find((w) => w.channel === "speed").w, 6, "the view still changes for this session");

// ---------------------------------------------------------------- geometry helpers
const box = (x, y, w, h, id) => ({ x, y, w, h, id, type: "chart", channel: id });
let pushed = pushDown([box(0, 0, 12, 4, "a"), box(0, 2, 6, 3, "b"), box(6, 4, 6, 3, "c"), box(0, 20, 12, 3, "d")], ["a"]);
const byId = (list, id) => list.find((w) => w.id === id);
assert.deepEqual([byId(pushed, "b").y, byId(pushed, "c").y, byId(pushed, "d").y], [4, 4, 20], "only what is in the way moves; the gap above d stays");
pushed = pushDown([box(0, 0, 12, 4, "a"), box(0, 3, 12, 4, "b"), box(0, 7, 12, 4, "c")], ["a"]);
assert.deepEqual(pushed.map((w) => w.y), [0, 4, 8], "a push chains down");
assert.deepEqual(findFreeSpot([box(0, 0, 6, 4, "a")], 6, 4), { x: 6, y: 0 }, "first free spot in reading order");
assert.deepEqual(findFreeSpot([box(0, 0, 12, 4, "a")], 4, 3), { x: 0, y: 4 });
assert.deepEqual(findFreeSpot([box(0, 0, 4, 4, "a"), box(8, 0, 4, 4, "b")], 6, 2), { x: 0, y: 4 }, "a 6-wide tile does not fit into a 4-wide hole");

// ---------------------------------------------------------------- edits
// A fixed layout to edit: w1 summary, w2 delta, w3 speed, w4 throttle, w5 brake, w6 map, w7 values, w8 physics.
const fixture = sanitizeLayout({ version: 1, layout: [
  { type: "summary", size: "large" }, { type: "delta", size: "large" }, { type: "chart", channel: "speed", size: "large" },
  { type: "chart", channel: "throttle", size: "large" }, { type: "chart", channel: "brake", size: "large" },
  { type: "trackmap", size: "medium", mode: "delta" }, { type: "cursor_table", size: "medium" }, { type: "physics", size: "large" },
] });
assertRenderable(fixture);
assert.deepEqual(fixture.widgets.map((w) => w.id), ["w1", "w2", "w3", "w4", "w5", "w6", "w7", "w8"]);
const fixtureCopy = JSON.parse(JSON.stringify(fixture));
let l = fixture;
const tile = (layout, key) => layout.widgets.find((w) => (w.channel || w.type) === key);

// place: exactly where asked, what is in the way moves down, gaps stay
l = placeWidget(fixture, "w3", 0, 60);
assertRenderable(l);
assert.deepEqual([tile(l, "speed").x, tile(l, "speed").y], [0, 60]);
assert.equal(tile(l, "throttle").y, tile(fixture, "throttle").y, "nothing moves up to fill the hole");
assert.equal(types(l).at(-1), "chart:speed", "reading order follows");
l = placeWidget(fixture, "w5", 0, 0);
assertRenderable(l);
assert.equal(tile(l, "brake").y, 0);
assert.ok(tile(l, "summary").y >= tile(l, "brake").h, "the tile that was there moved below");
assert.equal(placeWidget(fixture, "nope", 0, 0), fixture);
assert.equal(placeWidget(fixture, "w3", tile(fixture, "speed").x, tile(fixture, "speed").y), fixture, "same place: nothing changes");
l = placeWidget(fixture, "w6", 99, 3);
assert.equal(tile(l, "trackmap").x, COLS - tile(l, "trackmap").w, "clamped into the columns");

// shift (keyboard): up swaps with the tile directly above, down with the one below
l = shiftWidget(fixture, "w3", 0, -1);
assertRenderable(l);
assert.deepEqual(types(l).slice(1, 4), ["chart:speed", "delta", "chart:throttle"]);
l = shiftWidget(l, tile(l, "speed").id, 0, 1);
assert.ok(layoutsEqual(l, fixture), "down undoes up");
l = shiftWidget(fixture, "w6", 1, 0);
assertRenderable(l);
assert.equal(tile(l, "trackmap").x, 1, "→ moves one column");
assert.ok(tile(l, "cursor_table").y > tile(fixture, "cursor_table").y, "…the values window in the way moved down");
assert.equal(shiftWidget(fixture, "w1", -1, 0), fixture, "at the edge: nothing");

// resize: clamped to the minimum and the grid, pushes what is in the way
l = resizeWidget(fixture, "w3", 6, 3);
assert.deepEqual([tile(l, "speed").w, tile(l, "speed").h, tile(l, "speed").size], [6, 3, "medium"]);
l = resizeWidget(fixture, "w3", 1, 1);
assert.deepEqual([tile(l, "speed").w, tile(l, "speed").h], tileSpec("chart").min, "never below the minimum");
l = resizeWidget(fixture, "w6", 12, 12);
assertRenderable(l);
assert.equal(tile(l, "trackmap").w, 12);
assert.ok(tile(l, "cursor_table").y >= tile(l, "trackmap").y + 12, "the neighbour moved below");
assert.equal(resizeWidget(fixture, "nope", 6, 6), fixture);
l = setSize(fixture, "w3", "small");
assert.equal(tile(l, "speed").w, 4, "the old size names still work (small = 4 columns)");
assert.equal(setSize(fixture, "w3", "gigantic"), fixture);
assert.equal(setSize(fixture, "nope", "small"), fixture);

// compact: everything moves up as far as it goes
l = compactLayout(placeWidget(fixture, "w8", 0, 200));
assertRenderable(l);
assert.equal(tile(l, "physics").y, tile(fixture, "physics").y, "the far-away tile comes back up");

l = setChannel(fixture, "w3", "rpm", valid);
assert.equal(tile(l, "rpm").id, "w3");
assert.equal(setChannel(fixture, "w3", "throttle", valid), fixture, "a channel that is already shown is refused");
assert.equal(setChannel(fixture, "w3", "nonexistent", valid), fixture);
assert.equal(setChannel(fixture, "w3", "../x", valid), fixture);
assert.equal(setChannel(fixture, "w2", "rpm", valid), fixture, "only chart windows have a channel");

l = setWidgetColors(fixture, "w3", { a: "#ff0000", b: "bad" });
assert.deepEqual(tile(l, "speed").colors, { a: "#ff0000" });
assert.equal(tile(setWidgetColors(l, "w3", null), "speed").colors, undefined, "a window's colours can be cleared");
assert.equal(setWidgetColors(fixture, "w2", { a: "#ff0000" }), fixture);
assert.equal(tile(setTrackmapMode(fixture, "w6", "speed"), "trackmap").mode, "speed");
assert.equal(setTrackmapMode(fixture, "w6", "bogus"), fixture);
assert.deepEqual(requiredChannels(setTrackmapMode(fixture, "w6", "lat_g")), ["speed", "throttle", "brake", "lat_g"], "a map colour channel is requested too");

assert.equal(addWidget(fixture, { type: "summary" }, valid), fixture, "single windows are not duplicated");
assert.equal(addWidget(fixture, { type: "chart", channel: "speed" }, valid), fixture);
assert.equal(addWidget(fixture, { type: "chart", channel: "unknown" }, valid), fixture);
assert.equal(addWidget(fixture, { type: "nope" }, valid), fixture);
assert.equal(addWidget(fixture, { type: "chart" }, valid), fixture);
assert.equal(canAdd(fixture, { type: "chart", channel: "gear" }, valid), true);
assert.equal(canAdd(fixture, { type: "histogram", channel: "speed" }, valid), true, "a histogram of a channel that has a chart is fine");
l = addWidget(fixture, { type: "chart", channel: "gear", size: "small" }, valid);
assert.equal(tile(l, "gear").w, 4);
assertRenderable(l);
l = addWidget(fixture, { type: "chart", channel: "gear", w: 4, h: 3 }, valid);
assert.equal(tile(l, "gear").y, Math.max(...fixture.widgets.map((w) => w.y + w.h)), "no position: the first free spot (below, the grid is full)");
l = addWidget(resizeWidget(fixture, "w3", 8, 4), { type: "chart", channel: "gear", w: 4, h: 4 }, valid);
assert.deepEqual([tile(l, "gear").x, tile(l, "gear").y], [8, tile(l, "speed").y], "…or a hole that fits");
l = addWidget(fixture, { type: "chart", channel: "gear", x: 0, y: 0, w: 6, h: 3 }, valid);
assertRenderable(l);
assert.deepEqual([tile(l, "gear").x, tile(l, "gear").y], [0, 0], "with a position: exactly there");
assert.ok(tile(l, "summary").y >= 3, "…and what was there moved down");
let full = defaultLayout();
for (let i = 0; i < 40; i++) full = addWidget(full, { type: "chart", channel: `extra_${i}` });
assert.equal(full.widgets.length, MAX_WIDGETS);
assertRenderable(full);

l = removeWidget(fixture, "w3");
assert.equal(l.widgets.length, fixture.widgets.length - 1);
assertRenderable(l);
let one = { ...fixture, widgets: [fixture.widgets[0]] };
assert.equal(removeWidget(one, "w1"), one, "the last window cannot be removed");

l = setColors(fixture, { a: "#010203", delta: "bogus" });
assert.deepEqual(l.colors, { a: "#010203", b: DEFAULT_COLORS.b, delta: DEFAULT_COLORS.delta });
assert.ok(layoutsEqual(fixture, fixtureCopy), "edits never mutate the layout they started from");
assert.ok(layoutsEqual(standard, defaultLayout()), "...and nobody mutated the standard layout");

// many random edits: the layout always stays valid
let rnd = 7;
const random = (n) => { rnd = (rnd * 1103515245 + 12345) % 2147483648; return rnd % n; };
l = defaultLayout();
const keys = ["speed", "throttle", "brake", "gear", "rpm", "lat_g", "long_g", "yaw_rate"];
for (let i = 0; i < 400; i++) {
  const ids = l.widgets.map((w) => w.id);
  const id = ids[random(ids.length)];
  const op = random(6);
  if (op === 0) l = placeWidget(l, id, random(14) - 1, random(80));
  else if (op === 1) l = resizeWidget(l, id, random(14), random(22));
  else if (op === 2) l = shiftWidget(l, id, random(3) - 1, random(3) - 1);
  else if (op === 3) l = addWidget(l, { type: random(2) ? "chart" : "histogram", channel: keys[random(keys.length)], x: random(12), y: random(60), w: random(13), h: random(10) }, valid);
  else if (op === 4 && l.widgets.length > 6) l = removeWidget(l, id);
  else l = compactLayout(l);
  assertRenderable(l);
  assert.ok(layoutsEqual(sanitizeLayout(toStored(l), { validChannels: valid }), l), "store -> load is lossless");
}

// ---------------------------------------------------------------- the tile catalog (Wofür → Art → Kachel)
const channelCatalog = { channels: [
  { key: "speed", label: "Speed", unit: "km/h", group: "Speed & inputs" },
  { key: "rpm", label: "Engine speed", unit: "rpm", group: "Speed & inputs" },
  { key: "lat_g", label: "Lateral G", unit: "G", group: "Motion & G" },
  { key: "tire_load_fl", label: "Tyre load front left", unit: "N", group: "Tyres" },
  { key: "tire_temp_fl", label: "Tyre temperature front left", unit: "°C", group: "Tyres" },
  { key: "damper_vel_fl", label: "Damper velocity front left", unit: "mm/s", group: "Dampers" },
  { key: "downforce_front", label: "Downforce front", unit: "N", group: "Aero" },
  { key: "line_offset", label: "Line of Lap 2 vs Lap 1", unit: "m", group: "Racing line", pair: true },
  { key: "something_new", label: "Something new", unit: "", group: "A group nobody knows" },
] };
const tiles = buildTileCatalog(channelCatalog);
const tileByKey = (k) => tiles.find((x) => x.key === k);
assert.equal(new Set(tiles.map((x) => x.key)).size, tiles.length, "unique keys");
for (const type of TYPES.filter((x) => x !== "chart" && x !== "histogram")) assert.ok(tileByKey(`type:${type}`), `${type} is offered`);
assert.equal(tileByKey("chart:lat_g").purpose, "forces", "lateral G: forces acting on the car");
assert.equal(tileByKey("chart:tire_load_fl").purpose, "forces", "a tyre load is a force, even though the server groups it under tyres");
assert.equal(tileByKey("chart:downforce_front").purpose, "forces", "aero forces");
assert.equal(tileByKey("chart:tire_temp_fl").purpose, "tyres");
assert.equal(tileByKey("chart:rpm").purpose, "powertrain");
assert.equal(tileByKey("chart:damper_vel_fl").purpose, "chassis");
assert.equal(tileByKey("chart:line_offset").purpose, "corners");
assert.equal(tileByKey("histogram:line_offset"), undefined, "a lap-pair channel has no histogram");
assert.equal(tileByKey("chart:something_new").purpose, "other", "an unknown group still shows up");
assert.equal(purposeOfChannel({ key: "x", group: "Brakes" }), "tyres");
for (const x of tiles) {
  assert.ok(PURPOSES.some((p) => p.id === x.purpose), `${x.key}: known purpose`);
  assert.ok(KINDS.some((k) => k.id === x.kind), `${x.key}: known kind`);
  assert.ok(x.title && x.presets.length, x.key);
}
let tree = tileTree(tiles);
assert.deepEqual(tree.map((p) => p.id), PURPOSES.map((p) => p.id).filter((id) => tree.some((q) => q.id === id)), "purposes in their fixed order");
const forces = tree.find((p) => p.id === "forces");
assert.deepEqual(forces.kinds.map((k) => k.id), ["chart", "histogram", "diagram"], "kinds in their fixed order, empty ones left out");
assert.deepEqual(forces.kinds[0].tiles.map((x) => x.key), ["chart:lat_g", "chart:tire_load_fl", "chart:downforce_front"]);
tree = tileTree(tiles, { fit: { w: 4, h: 4 } });
const fitted = tree.flatMap((p) => p.kinds.flatMap((k) => k.tiles));
assert.ok(fitted.length > 0 && fitted.every((x) => fitsSize(x.type, 4, 4)), "a drawn box: only tiles that fit");
assert.ok(!fitted.some((x) => x.type === "corners" || x.type === "line"), "big tiles are not offered for a small box");
assert.equal(tileTree(tiles, { fit: { w: 2, h: 2 } }).length, 0, "nothing fits a 2 x 2 box");
tree = tileTree(tiles, { layout: standard, validChannels: new Set(channelCatalog.channels.map((c) => c.key)) });
const flat = tree.flatMap((p) => p.kinds.flatMap((k) => k.tiles));
assert.equal(flat.find((x) => x.key === "type:summary").taken, true, "already on the grid: marked");
assert.equal(flat.find((x) => x.key === "chart:speed").taken, true);
assert.equal(flat.find((x) => x.key === "histogram:speed").taken, false);
assert.deepEqual(tileTree(tiles, { query: "tyre load" }).flatMap((p) => p.kinds.flatMap((k) => k.tiles.map((x) => x.key))), ["chart:tire_load_fl", "histogram:tire_load_fl"], "search");
assert.deepEqual(presetsFor(tileByKey("chart:speed"), { w: 6, h: 4 }), [[6, 4], [6, 3], [4, 3]], "presets that fit a box");

// ---------------------------------------------------------------- delta facts
// "Biggest gain / loss" = the steepest change of the delta over a corner-sized stretch
// (factWindowMetres: 1/40 of the lap, 100..400 m), not over arbitrary lengths.
assert.equal(factWindowMetres(13600), 340, "Le Mans: 340 m");
assert.equal(factWindowMetres(4000), 100, "short tracks: never below 100 m");
assert.equal(factWindowMetres(30000), 400, "never above 400 m");
const grid = (n, step = 10) => ({ start: 0, step, count: n });
// The case the first definition got wrong (found in a real browser with a 14 km lap): a lap that
// loses time steadily everywhere, plus one corner losing 0.3 s and one corner gaining 0.2 s. The
// old "largest rise between any two points" answered "most of the lap, at its middle".
const steady = [];
let v = 0;
for (let i = 0; i < 1401; i++) { v += 0.0005; if (i >= 500 && i < 510) v += 0.03; if (i >= 900 && i < 910) v -= 0.02; steady.push(v); }
let facts = deltaFacts({ grid: grid(1401, 10), delta: steady });       // 14 km, window 350 m = 35 points
// the corner adds its time between the points at 4.99 and 5.09 km
assert.ok(facts.loss.from <= 4990 && facts.loss.to >= 5090 && facts.loss.to - facts.loss.from === 350, `the loss is the corner at 5.0–5.1 km, not the lap (${facts.loss.from}–${facts.loss.to})`);
assert.ok(Math.abs(facts.loss.seconds - (0.3 + 25 * 0.0005)) < 0.01, `0.3 s in the corner plus the steady loss around it (${facts.loss.seconds})`);
assert.ok(facts.gain.from <= 8990 && facts.gain.to >= 9090, `the gain is the corner at 9.0–9.1 km (${facts.gain.from}–${facts.gain.to})`);
assert.ok(Math.abs(facts.gain.seconds - (0.2 - 25 * 0.0005)) < 0.01, `(${facts.gain.seconds})`);
assert.equal(facts.loss.at, (facts.loss.from + facts.loss.to) / 2);
assert.equal(facts.loss.index, Math.round((facts.loss.from / 10 + facts.loss.to / 10) / 2));
assert.ok(Math.abs(facts.net - steady.at(-1)) < 1e-12, "net = the lap's real difference (Lap 1 − Lap 2), positive = Lap 1 behind");

facts = deltaFacts({ grid: grid(200, 10), delta: Array.from({ length: 200 }, (_, i) => i * 0.001) });
assert.equal(facts.gain, null, "never gained anything");
assert.ok(facts.loss && facts.net > 0, "only ever lost time: net is positive");
facts = deltaFacts({ grid: grid(200, 10), delta: Array.from({ length: 200 }, (_, i) => -i * 0.001) });
assert.equal(facts.loss, null);
assert.ok(facts.gain && facts.net < 0, "only ever gained time: net is negative (Lap 1 faster)");
facts = deltaFacts({ grid: grid(50, 10), delta: new Array(50).fill(0) });
assert.deepEqual([facts.gain, facts.loss, facts.net], [null, null, 0], "identical laps: nothing gained, nothing lost");
facts = deltaFacts({ grid: grid(5, 10), delta: [0, 0.1, 0.2, 0.25, 0.3] });
assert.ok(Math.abs(facts.loss.seconds - 0.3) < 1e-9, "a lap shorter than the window: the whole lap is the one stretch");
facts = deltaFacts({ grid: grid(30, 10), delta: Array.from({ length: 30 }, (_, i) => (i === 7 ? NaN : i === 8 ? null : i * 0.01)) });
assert.ok(facts.loss && Number.isFinite(facts.loss.seconds), "gaps in the curve are skipped, not turned into NaN");
assert.equal(facts.net, 0.29, "the last real value counts");
facts = deltaFacts({ grid: grid(6), delta: [0, 0.1, null, 0.2, NaN, null] });
assert.equal(facts.net, 0.2, "trailing gaps: the last finite value is the net");
assert.equal(deltaFacts({ grid: grid(3), delta: null }), null, "one lap: no delta");
assert.equal(deltaFacts({ grid: grid(3), delta: [null, null, null] }), null);
assert.equal(deltaFacts(null), null);
// V0.9.1 review: the server's facts (measured on a fixed 0.5 m grid) win over the chart's grid,
// so a phone and a desktop show the same numbers; the cursor index still comes from the chart grid
facts = deltaFacts({ grid: grid(1401, 10), delta: steady, delta_facts: { window: 350, gain: { seconds: 0.1875, from: 8992.5, to: 9342.5 }, loss: { seconds: 0.3125, from: 4990.5, to: 5340.5 }, net: 0.512 } });
assert.deepEqual([facts.loss.seconds, facts.loss.from, facts.loss.to, facts.loss.at], [0.3125, 4990.5, 5340.5, 5165.5]);
assert.equal(facts.loss.index, 517, "the grid index nearest the middle (5165.5 m on a 10 m grid)");
assert.equal(facts.gain.seconds, 0.1875);
assert.equal(facts.net, 0.512);
facts = deltaFacts({ grid: grid(50, 10), delta: new Array(50).fill(0), delta_facts: { window: 100, gain: null, loss: null, net: 0 } });
assert.deepEqual([facts.gain, facts.loss, facts.net], [null, null, 0]);

// ---------------------------------------------------------------- sectors
const lapsWith = (a, b) => ({ laps: { a: { sector_times: a }, b: { sector_times: b } } });
let table = sectorTable(lapsWith([30, 30.5, 29.5], [28, 29, 28]));
assert.equal(table.source, "times");
assert.deepEqual(table.rows.map((r) => r.diff), [2, 1.5, 1.5]);
assert.equal(table.total, 5);
assert.equal(table.rows[0].worst, true, "S1 lost the most");
table = sectorTable(lapsWith([28.1, 29.4, 27.7], [28.2, 29.3, 28.0]));
assert.deepEqual(table.rows.map((r) => [r.label, r.best, r.worst]), [["S1", false, false], ["S2", false, true], ["S3", true, false]]);
assert.ok(Math.abs(table.total - -0.3) < 1e-9);
table = sectorTable(lapsWith([30, 30, 30], [30, 30, 30]));
assert.deepEqual(table.rows.map((r) => [r.best, r.worst]), [[false, false], [false, false], [false, false]], "all equal: nothing is called best or worst");
const fromCurve = sectorTable({ laps: { a: { sector_times: [0, 0, 0] }, b: { sector_times: [30, 30, 30] } }, grid: grid(10), sectors: { a: [30, 60] }, delta: [0, 0.1, 0.2, 0.3, 0.2, 0.1, 0.0, 0.1, 0.2, 0.3] });
assert.equal(fromCurve.source, "curve", "without usable game sector times the delta curve at the boundaries is used");
assert.equal(sectorTable({ laps: { a: { sector_times: [0, 0, 0] }, b: { sector_times: [1, 1, 1] } }, grid: grid(5), delta: null, sectors: { a: [] } }), null);
assert.equal(sectorTable({ laps: { a: { sector_times: [1, 1, 1] }, b: null } }), null, "one lap: no comparison");

// ---------------------------------------------------------------- points per series
assert.equal(chooseMaxPoints(0), 1000);
assert.equal(chooseMaxPoints(NaN), 1000);
assert.equal(chooseMaxPoints(300), 1000);
assert.equal(chooseMaxPoints(900), 1800);
assert.equal(chooseMaxPoints(5000), 3000);

// ---------------------------------------------------------------- touch gestures
function fakeTimers() {
  const pending = new Map();
  let nextId = 1;
  return {
    set(fn) { const id = nextId++; pending.set(id, fn); return id; },
    clear(id) { pending.delete(id); },
    fireAll() { for (const [id, fn] of [...pending]) { pending.delete(id); fn(); } },
    get count() { return pending.size; },
  };
}
function recorder() {
  const log = [];
  return { log, handlers: { tap: (x, y) => log.push(["tap", x, y]), scrub: (x) => log.push(["scrub", x]), longPress: (x) => log.push(["hold", x]), pinch: (p) => log.push(["pinch", p.factor, p.centerX]), pan: (dx) => log.push(["pan", dx]) } };
}
let timers = fakeTimers();
let rec = recorder();
let clock = 0;
const now = () => clock;
let g = createGestureTracker(rec.handlers, { timers, now });
// tap: the cursor is placed when the finger LIFTS without having moved (V0.9.1 fix: placing it on
// touch-down made every scroll that starts on a chart jump the cursor first)
assert.deepEqual(g.start([{ x: 100, y: 50 }]), { prevent: false });
assert.deepEqual(rec.log, [], "nothing happens on touch-down");
assert.deepEqual(g.move([{ x: 103, y: 52 }]), { prevent: false }, "a tiny wobble is still a tap");
g.end([]);
assert.deepEqual(rec.log, [["tap", 100, 50]], "lifting the finger places the cursor");
// sideways drag: scrub, and no tap at the end
rec.log.length = 0;
clock += 1000;
g.start([{ x: 100, y: 50 }]);
assert.deepEqual(g.move([{ x: 160, y: 55 }]), { prevent: true }, "sideways drag scrubs and owns the gesture");
g.move([{ x: 180, y: 56 }]);
assert.deepEqual(rec.log, [["scrub", 160], ["scrub", 180]]);
assert.equal(timers.count, 0, "moving cancelled the hold timer");
g.end([]);
assert.equal(g.mode, "idle");
assert.equal(rec.log.filter((e) => e[0] === "tap").length, 0, "a scrub is not also a tap");

rec = recorder(); timers = fakeTimers(); g = createGestureTracker(rec.handlers, { timers, now });
g.start([{ x: 100, y: 50 }]);
assert.deepEqual(g.move([{ x: 104, y: 120 }]), { prevent: false }, "vertical drag: the page scrolls, we do not prevent it");
assert.equal(g.mode, "scroll");
assert.deepEqual(g.move([{ x: 130, y: 220 }]), { prevent: false });
g.end([]);
assert.deepEqual(rec.log, [], "a scroll neither scrubs nor moves the cursor");

rec = recorder(); timers = fakeTimers(); g = createGestureTracker(rec.handlers, { timers, now });
g.start([{ x: 100, y: 50 }]);
timers.fireAll();
assert.deepEqual(rec.log, [["hold", 100]], "holding pins");
assert.deepEqual(g.move([{ x: 120, y: 90 }]), { prevent: true }, "after the hold, dragging moves the cursor in any direction");
assert.deepEqual(rec.log.at(-1), ["scrub", 120]);
g.end([]);
assert.equal(rec.log.filter((e) => e[0] === "tap").length, 0, "a hold is not also a tap");

rec = recorder(); timers = fakeTimers(); g = createGestureTracker(rec.handlers, { timers, now });
g.start([{ x: 100, y: 50 }]);
g.end([]);
timers.fireAll();
assert.deepEqual(rec.log, [["tap", 100, 50]], "lifting before the timer: a tap, no pin");

// double tap resets the zoom; two taps far apart in time or space are two taps
const resets = [];
rec = recorder(); timers = fakeTimers(); clock = 5000;
g = createGestureTracker({ ...rec.handlers, doubleTap: () => resets.push(clock) }, { timers, now });
g.start([{ x: 100, y: 50 }]); g.end([]);
clock += 200;
g.start([{ x: 104, y: 52 }]); g.end([]);
assert.deepEqual(resets, [5200], "two quick taps at the same spot = double tap");
assert.deepEqual(rec.log, [["tap", 100, 50]], "the first tap placed the cursor, the second only reset");
clock += 200;
g.start([{ x: 100, y: 50 }]); g.end([]);
assert.equal(resets.length, 1, "a third tap starts over");
clock += 1000;
g.start([{ x: 100, y: 50 }]); g.end([]);
assert.equal(resets.length, 1, "too slow: two taps");
clock += 100;
g.start([{ x: 300, y: 50 }]); g.end([]);
assert.equal(resets.length, 1, "too far apart: two taps");
rec = recorder(); timers = fakeTimers();
g = createGestureTracker(rec.handlers, { timers, now });
g.start([{ x: 1, y: 1 }]); g.end([]); clock += 50; g.start([{ x: 1, y: 1 }]); g.end([]);
assert.equal(rec.log.filter((e) => e[0] === "tap").length, 2, "without a doubleTap handler a second tap is just a tap");

// the default timers must work where setTimeout insists on being called on the global object —
// as in every browser. (The first version stored setTimeout as a method and threw "Illegal
// invocation" on every touch on a real phone; Node never checks, so only a browser showed it.)
const realSetTimeout = globalThis.setTimeout;
const realClearTimeout = globalThis.clearTimeout;
globalThis.setTimeout = function strictSetTimeout(fn, ms) {
  if (this !== undefined && this !== globalThis) throw new TypeError("Illegal invocation");
  return realSetTimeout(fn, ms);
};
globalThis.clearTimeout = function strictClearTimeout(id) {
  if (this !== undefined && this !== globalThis) throw new TypeError("Illegal invocation");
  return realClearTimeout(id);
};
try {
  const holds = [];
  const strict = createGestureTracker({ tap() {}, scrub() {}, longPress: () => holds.push(1), pinch() {}, pan() {} }, { longPressMs: 5 });
  assert.doesNotThrow(() => strict.start([{ x: 1, y: 1 }]), "touch-down with the default timers");
  await new Promise((r) => realSetTimeout(r, 30));
  assert.deepEqual(holds, [1], "the hold timer really fired");
  assert.doesNotThrow(() => { strict.start([{ x: 1, y: 1 }]); strict.end([]); }, "cancelling the timer too");
} finally {
  globalThis.setTimeout = realSetTimeout;
  globalThis.clearTimeout = realClearTimeout;
}

rec = recorder(); timers = fakeTimers(); g = createGestureTracker(rec.handlers, { timers });
assert.deepEqual(g.start([{ x: 100, y: 0 }, { x: 200, y: 0 }]), { prevent: true });
assert.deepEqual(g.move([{ x: 80, y: 0 }, { x: 220, y: 0 }]), { prevent: true });
assert.equal(rec.log[0][0], "pinch");
assert.ok(Math.abs(rec.log[0][1] - 100 / 140) < 1e-9 && rec.log[0][1] < 1, "fingers apart = zoom in (factor < 1)");
assert.equal(rec.log[0][2], 150);
g.move([{ x: 100, y: 0 }, { x: 240, y: 0 }]);
assert.deepEqual(rec.log.at(-1), ["pan", 20], "moving both fingers pans by the movement of their centre (150 -> 170)");
g.end([{ x: 100, y: 0 }]);
assert.equal(g.mode, "scroll", "one finger left after a pinch does not scrub by accident");
assert.equal(rec.log.filter((e) => e[0] === "tap").length, 0, "a pinch never counts as a tap");

rec = recorder(); timers = fakeTimers(); g = createGestureTracker(rec.handlers, { timers, holdToScrub: true });
g.start([{ x: 100, y: 50 }]);
assert.deepEqual(g.move([{ x: 180, y: 52 }]), { prevent: false }, "track map: a plain swipe always scrolls");
assert.equal(rec.log.filter((e) => e[0] === "scrub").length, 0);
g.cancel();
assert.equal(g.mode, "idle");

// ---------------------------------------------------------------- the registry
assert.deepEqual(MODULES.map((m) => m.type).sort(), [...TYPES].sort(), "every window type has a module");
for (const m of MODULES) {
  for (const field of ["id", "title", "description", "type", "supportedChannels", "defaultSize", "renderer", "mobileSupport"]) assert.ok(m[field] !== undefined, `${m.id}.${field}`);
  assert.equal(typeof m.renderer, "function");
  assert.equal(moduleForType(m.type), m);
}
MODULES.forEach((m) => {
  const index = defaultLayout().widgets.findIndex((w) => w.type === m.type);
  assert.equal(m.defaultPosition, index < 0 ? null : index, `${m.id}: defaultPosition matches the standard layout`);
  assert.equal(m.defaultSize, (defaultLayout().widgets[index] || { size: "large" }).size);
  assert.deepEqual(m.min, tileSpec(m.type).min, `${m.id}: minimum size`);
});
assert.equal(moduleForType("chart").supportedChannels, "*");
assert.equal(moduleForType("nonsense"), null);

// ---------------------------------------------------------------- zoom refinement: when to ask for finer data
const FULL = [0, 13000];
const plan = (view, current = null, baseStep = 4.5) => planRefinement({ view, full: FULL, baseStep, current });
assert.deepEqual(plan([0, 13000]), { action: "none" }, "the whole lap: the overview is what is wanted");
assert.deepEqual(plan([2000, 9000]), { action: "none" }, "zoomed in less than 2x: no");
assert.deepEqual(plan([2000, 4800]), { action: "none" }, "~620 overview points are visible (>= 600): still readable, no request");
assert.deepEqual(plan([2000, 4500]), { action: "refine", start: 1375, end: 5125, step: 2.08 }, "~555 visible (< 600): a moderately finer grid, sized to the view");
assert.deepEqual(plan([1000, 1200]), { action: "refine", start: 950, end: 1250, step: 0.5 }, "200 m: finer data, view +/-25 %, the server's smallest step");
assert.deepEqual(plan([5000, 5600]), { action: "refine", start: 4850, end: 5750, step: 0.5 }, "600 m");
assert.deepEqual(plan([0, 300]), { action: "refine", start: 0, end: 375, step: 0.5 }, "the stretch is clamped to the lap");
assert.deepEqual(plan([12800, 13000]), { action: "refine", start: 12750, end: 13000, step: 0.5 });
const wide = plan([3000, 4300]);   // 1300 m at 4.5 m = 289 points visible
assert.equal(wide.action, "refine");
assert.ok(wide.step > 0.5 && wide.step <= 1.1 && (wide.end - wide.start) / wide.step <= 2000, `a sized request, not a flood (step ${wide.step})`);
assert.deepEqual(plan([1000, 1200], null, 0.6), { action: "none" }, "an overview that is already about as fine: nothing to gain, nothing requested");
assert.deepEqual(plan([1000, 1200], null, 0.7), { action: "none" }, "a step of 0.7 m cannot be made 30 % finer than the server's 0.5 m floor");
assert.deepEqual(plan([1000, 1200], null, 0.71), { action: "none" }, "the break-even is 0.5 / 0.7 = 0.714 m");
assert.equal(plan([1000, 1200], null, 0.72).action, "refine", "just above it, 0.5 m is more than 30 % finer");
for (const [view, baseStep] of [[[5, 5], 4.5], [[10, 5], 4.5], [[0, 100], 0], [[NaN, 1], 4.5]]) assert.deepEqual(plan(view, null, baseStep), { action: "none" }, `nonsense input ${view}/${baseStep}`);
assert.deepEqual(planRefinement({ view: [0, 100], full: [5, 5], baseStep: 4.5, current: null }), { action: "none" });

const refined = { start: 950, end: 1250, step: 0.5 };
assert.deepEqual(plan([1000, 1200], refined), { action: "keep" }, "the refined data already covers the view");
assert.deepEqual(plan([1100, 1150], refined), { action: "keep" }, "zooming further into it: still fine enough");
assert.deepEqual(plan([960, 1160], refined), { action: "keep" }, "a small pan inside the margin needs no request");
assert.equal(plan([900, 1100], refined).action, "refine", "panned out of the stretch: ask for the new one");
assert.deepEqual(plan([6000, 6200], refined), { action: "refine", start: 5950, end: 6250, step: 0.5 });
assert.deepEqual(plan([0, 13000], refined), { action: "base" }, "zoomed all the way out: back to the overview");
assert.deepEqual(plan([2000, 9000], refined), { action: "base" });
assert.equal(plan([1000, 1200], { start: 950, end: 1250, step: 4.5 }).action, "refine", "a stretch that is too coarse for the new zoom is replaced");
assert.deepEqual(plan([1000, 1200], { start: 950, end: 1250, step: 0.75 }), { action: "keep" }, "within 1.5x of what is wanted is good enough");
assert.deepEqual(covers(refined, [1000, 1200]), true);
assert.deepEqual(covers(refined, [900, 1200]), false);
assert.deepEqual(covers(null, [1, 2]), false);
assert.ok(REFINE.debounceMs >= 150, "the debounce waits for a zoom gesture to settle");

// a server answer is only swapped in if it is consistent with what was asked for
const answer = (over = {}) => {
  const n = 601;
  return { grid: { start: 950, step: 0.5, count: n }, time: { a: new Array(n).fill(1) }, delta: new Array(n).fill(0), laps: { a: {} }, channels: { speed: { a: new Array(n).fill(1), b: new Array(n).fill(2) } }, ...over };
};
const asked = { start: 950, end: 1250 };
assert.equal(isUsable(answer(), asked), true);
assert.equal(isUsable(answer({ delta: null }), asked), true, "one lap: no delta");
for (const [what, bad] of [
  ["no grid", answer({ grid: null })], ["zero step", answer({ grid: { start: 950, step: 0, count: 601 } })], ["tiny count", answer({ grid: { start: 950, step: 0.5, count: 1 } })],
  ["NaN start", answer({ grid: { start: NaN, step: 0.5, count: 601 } })], ["wrong stretch", answer({ grid: { start: 5000, step: 0.5, count: 601 } })],
  ["too short", answer({ grid: { start: 950, step: 0.5, count: 50 } })], ["time length", answer({ time: { a: [1, 2] } })], ["delta length", answer({ delta: [0, 1] })],
  ["channel length", answer({ channels: { speed: { a: [1, 2, 3], b: null } } })], ["no laps", answer({ laps: null })], ["null", null], ["string", "oops"],
]) assert.equal(isUsable(bad, asked), false, what);

// the shared state can swap its data under an unchanged view and cursor
const baseGrid = { start: 2, step: 12, count: 200 };
const baseData = { grid: baseGrid, channels: {}, laps: { a: {}, b: null } };
const fineGrid = { start: 950, step: 0.5, count: 601 };
const fineData = { grid: fineGrid, channels: {}, laps: { a: {}, b: null } };
const shared = createViewState(baseData);
shared.setView(1000, 1200);
shared.setCursor(83);                                        // 2 + 83*12 = 998 m
const seen2 = [];
shared.subscribe((what) => seen2.push(what));
shared.replaceData(fineData);
assert.equal(shared.data, fineData);
assert.deepEqual(shared.getView(), [1000, 1200], "what the user looks at does not move");
assert.equal(shared.getCursor(), 96, "the cursor keeps its distance (998 m = (998-950)/0.5 on the fine grid)");
assert.equal(distanceAt(fineGrid, shared.getCursor()), 998);
assert.deepEqual(shared.full, [2, 2 + 12 * 199], "the extent of the whole lap is not recomputed");
assert.deepEqual(seen2, ["data"]);
shared.setCursor(10_000);
assert.equal(shared.getCursor(), 600, "cursor limits follow the NEW grid");
shared.pinCursor(300);
shared.replaceData(baseData);
assert.equal(shared.isPinned(), true, "a pinned cursor stays pinned");
assert.equal(shared.getCursor(), Math.round((distanceAt(fineGrid, 300) - 2) / 12), "...at the same distance on the overview grid");
shared.setView(0, 1e9);
assert.deepEqual(shared.getView(), shared.full, "the view is still limited to the whole lap, not to the refined stretch");

// the track map keeps the overview; its cursor index is translated by distance
const st = createViewState(baseData);
st.replaceData(fineData);
const map = baseAdapter(st, baseData);
assert.equal(map.data, baseData);
st.setCursor(96);                                            // 998 m
assert.equal(map.getCursor(), 83, "998 m is index 83 of the overview");
map.setCursor(100);                                          // 1202 m
assert.equal(distanceAt(fineGrid, st.getCursor()), 1202 > 1250 ? 1250 : 1202, "setting through the map lands on the same distance");
map.hoverCursor(90);
assert.equal(distanceAt(fineGrid, st.getCursor()), 1082);
map.pinCursor(80);
assert.equal(st.isPinned(), true);
assert.equal(distanceAt(fineGrid, st.getCursor()), 962);
assert.equal(map.getCursor(), 80);
let notified = 0;
const off = map.subscribe(() => { notified += 1; });
st.setCursor(10);
assert.equal(notified, 1, "the adapter delegates subscriptions");
off();
const identity = baseAdapter(createViewState(baseData), baseData);
identity.setCursor(50);
assert.equal(identity.getCursor(), 50, "without a refined stretch the adapter changes nothing");

console.log("ANALYSIS-LAYOUT-OK");
