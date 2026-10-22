// analysis/layout.js — the user's analysis layout: which tiles, WHERE on the grid, how big,
// which channel, which colours. Pure data + a thin storage wrapper; no DOM, so every rule is
// testable in plain Node.
//
// V0.9.5: a real 2-D grid. Every tile has a cell position and a size in cells
// (x, y, w, h on a 12-column grid with fixed-height rows) instead of "the n-th window,
// small / medium / large". Tiles are placed where the user puts them — gaps are allowed —
// and can never overlap: whatever lands on a tile pushes it down (see pushDown()).
//
// Stored in the browser (localStorage) — a UI preference, not telemetry, and never anything
// but the JSON below. Shape (version 2):
//
//   { "version": 2,
//     "layout": [ { "type": "chart", "channel": "speed", "x": 0, "y": 5, "w": 12, "h": 4 }, ... ],
//     "colors": { "lapA": "#e0472b", "lapB": "#4fb3e8", "delta": "#ecedee" } }
//
// A version-1 layout (V0.9.1 – V0.9.4: an ordered list with small/medium/large) is migrated
// once, flowed onto the grid in its old order (MIGRATIONS[1]) — nobody loses their view.
//
// Rules this module guarantees:
//   * whatever is in storage — missing, corrupt, hand-edited, from the future —
//     sanitizeLayout() turns it into a VALID layout or falls back to the default;
//     it never throws and never lets a bad value reach the views
//   * every tile lies inside the 12 columns, is at least its type's minimum size and at most
//     MAX_H rows high, and no two tiles overlap
//   * colours: only #rrggbb is accepted, anything else falls back per entry
//   * known tile types only, no duplicates of single-instance tiles, one tile per channel
//     and kind, at least one tile
//   * versioned: MIGRATIONS[n] upgrades version n to n+1; an unknown (newer) version is
//     ignored rather than misread

import { DEFAULT_COLORS, isHexColor } from "./model.js";

export const LAYOUT_VERSION = 2;
export const STORAGE_KEY = "garage16_analysis_layout";
export const LEGACY_CHANNEL_KEY = "garage16_analysis_channels"; // V0.9.0's channel selection
export const COLS = 12;
export const MAX_H = 20;          // rows
export const MAX_Y = 400;         // rows — a hand-edited y of 1e9 must not create a mile of empty grid
export const ROW_PX = 40;         // height of one grid row (css px) — grid.js and the renderers use it
export const GAP_PX = 12;         // gap between rows / columns (css px)
export const MAX_WIDGETS = 30;
export const MAX_REQUEST_CHANNELS = 24; // the server's limit per request

// The old size names. Still derived for every tile (renderers that only need a rough idea of
// their size read widget.size) and understood when a version-1 layout is migrated.
export const SIZES = ["small", "medium", "large"];
const LEGACY_WIDTH = { small: 4, medium: 6, large: 12 };
export const sizeName = (w) => (w <= 4 ? "small" : w <= 8 ? "medium" : "large");

export const SINGLE_TYPES = ["summary", "delta", "corners", "coaching", "trackmap", "line", "corner_detail", "sectors", "events", "cursor_table", "physics", "tyres", "damper", "health"];
export const CHANNEL_TYPES = ["chart", "histogram"];
export const TYPES = [...SINGLE_TYPES, ...CHANNEL_TYPES];
export const TRACKMAP_MODES = ["delta", "none", "speed", "throttle", "brake", "gear", "rpm", "lat_g", "long_g"];

const CHANNEL_KEY = /^[a-z0-9_]{1,48}$/;

// Per tile type: the smallest size it is still readable at (min), the ready-made sizes the
// catalog offers (presets, [w, h] in cells) and the size a tile gets when none is given.
// Every preset is >= min; any size between min and COLS x MAX_H is allowed (a drawn box,
// the resize corner).
export const TILE_SPECS = {
  summary:       { min: [6, 5], def: [12, 8],  presets: [[12, 6], [12, 8], [8, 9], [6, 12]] },
  delta:         { min: [4, 4], def: [12, 6],  presets: [[12, 5], [12, 6], [12, 8], [8, 6], [6, 6]] },
  corners:       { min: [6, 4], def: [12, 8],  presets: [[12, 6], [12, 8], [12, 12], [8, 9], [6, 9]] },
  coaching:      { min: [4, 4], def: [6, 9],   presets: [[4, 9], [6, 9], [6, 12], [12, 7]] },
  trackmap:      { min: [3, 5], def: [6, 9],   presets: [[4, 7], [6, 9], [8, 10], [12, 12]] },
  line:          { min: [5, 7], def: [12, 12], presets: [[6, 10], [8, 12], [12, 12], [12, 16]] },
  corner_detail: { min: [6, 6], def: [12, 15], presets: [[6, 16], [8, 15], [12, 10], [12, 15]] },
  sectors:       { min: [6, 4], def: [12, 7],  presets: [[6, 7], [12, 6], [12, 9]] },
  events:        { min: [4, 4], def: [6, 9],   presets: [[4, 7], [6, 9], [12, 5]] },
  cursor_table:  { min: [3, 4], def: [6, 9],   presets: [[4, 9], [6, 9], [6, 12], [12, 8]] },
  physics:       { min: [6, 6], def: [12, 9],  presets: [[6, 10], [8, 9], [12, 9]] },
  tyres:         { min: [6, 6], def: [12, 9],  presets: [[6, 10], [12, 9]] },
  damper:        { min: [6, 5], def: [12, 7],  presets: [[6, 9], [12, 7]] },
  health:        { min: [4, 6], def: [12, 9],  presets: [[6, 12], [12, 9], [12, 12]] },   // V0.9.7
  chart:         { min: [3, 3], def: [12, 4],  presets: [[12, 3], [12, 4], [12, 6], [8, 4], [6, 4], [6, 3], [4, 3]] },
  histogram:     { min: [3, 4], def: [6, 5],   presets: [[4, 5], [6, 5], [8, 5], [12, 5]] },
};

export const tileSpec = (type) => TILE_SPECS[type] || { min: [3, 3], def: [12, 4], presets: [[12, 4]] };

// Does a w x h box suit this tile type?
export function fitsSize(type, w, h) {
  const { min } = tileSpec(type);
  return Number.isInteger(w) && Number.isInteger(h) && w >= min[0] && h >= min[1] && w <= COLS && h <= MAX_H;
}

// The Garage16 standard layout. The same for everyone, always available. The same tiles in the
// same order as V0.9.4's standard, now with explicit cells (two medium tiles side by side).
const STANDARD = [
  { type: "summary", x: 0, y: 0, w: 12, h: 8 },
  { type: "delta", x: 0, y: 8, w: 12, h: 6 },
  { type: "corners", x: 0, y: 14, w: 12, h: 8 },
  { type: "chart", channel: "speed", x: 0, y: 22, w: 12, h: 4 },
  { type: "chart", channel: "throttle", x: 0, y: 26, w: 12, h: 3 },
  { type: "chart", channel: "brake", x: 0, y: 29, w: 12, h: 3 },
  { type: "trackmap", mode: "delta", x: 0, y: 32, w: 6, h: 9 },
  { type: "cursor_table", x: 6, y: 32, w: 6, h: 9 },
  { type: "physics", x: 0, y: 41, w: 12, h: 9 },
  { type: "coaching", x: 0, y: 50, w: 6, h: 9 },
  { type: "events", x: 6, y: 50, w: 6, h: 9 },
  { type: "line", x: 0, y: 59, w: 12, h: 12 },
  { type: "corner_detail", x: 0, y: 71, w: 12, h: 15 },
];

// Reading order: top to bottom, then left to right. The DOM order (and so the order on a
// phone, where the tiles are stacked) follows it.
const byReadingOrder = (p, q) => p.y - q.y || p.x - q.x;

function finish(widgets) {
  return widgets
    .slice()
    .sort(byReadingOrder)
    .map((w, i) => ({ ...w, size: sizeName(w.w), id: `w${i + 1}` }));
}

export function defaultLayout() {
  return {
    version: LAYOUT_VERSION,
    widgets: finish(STANDARD.map((w) => ({ ...w }))),
    colors: { a: DEFAULT_COLORS.a, b: DEFAULT_COLORS.b, delta: DEFAULT_COLORS.delta },
  };
}

// ---------------------------------------------------------------- geometry

export const overlaps = (p, q) => p.x < q.x + q.w && q.x < p.x + p.w && p.y < q.y + q.h && q.y < p.y + p.h;

// The rows the layout occupies (the grid shows at least this many).
export const layoutRows = (layout) => layout.widgets.reduce((rows, w) => Math.max(rows, w.y + w.h), 0);

// Clamps a box into the grid and to its type's limits.
export function clampBox(type, box) {
  const { min, def } = tileSpec(type);
  const int = (v, fallback) => (Number.isFinite(v) ? Math.round(v) : fallback);
  const w = Math.min(Math.max(int(box.w, def[0]), min[0]), COLS);
  const h = Math.min(Math.max(int(box.h, def[1]), min[1]), MAX_H);
  const x = Math.min(Math.max(int(box.x, 0), 0), COLS - w);
  const y = Math.min(Math.max(int(box.y, 0), 0), MAX_Y);
  return { x, y, w, h };
}

// `fixed` stays where it is; every other tile that would overlap it (or a tile pushed before
// it) moves straight down until it is free. Tiles that are not in the way do not move, gaps
// stay — the user decides where things are. Returns new objects; terminates because a tile
// only ever moves down and below everything there is free space.
export function pushDown(widgets, fixedIds) {
  const fixed = widgets.filter((w) => fixedIds.includes(w.id)).map((w) => ({ ...w }));
  const rest = widgets.filter((w) => !fixedIds.includes(w.id)).map((w) => ({ ...w })).sort(byReadingOrder);
  const placed = [...fixed];
  for (const tile of rest) {
    let hit = placed.find((p) => overlaps(p, tile));
    while (hit) {
      tile.y = hit.y + hit.h;
      hit = placed.find((p) => overlaps(p, tile));
    }
    placed.push(tile);
  }
  return placed;
}

// The first free spot (reading order) for a w x h tile, scanning from row `fromY`.
export function findFreeSpot(widgets, w, h, fromY = 0) {
  const width = Math.min(Math.max(w, 1), COLS);
  for (let y = Math.max(fromY, 0); y <= MAX_Y; y++) {
    for (let x = 0; x + width <= COLS; x++) {
      const box = { x, y, w: width, h };
      if (!widgets.some((o) => overlaps(o, box))) return { x, y };
    }
  }
  return { x: 0, y: widgets.reduce((rows, o) => Math.max(rows, o.y + o.h), 0) };
}

// Is the box free (no tile except `ignoreId` in it)?
export function isFree(layout, box, ignoreId = null) {
  return !layout.widgets.some((w) => w.id !== ignoreId && overlaps(w, box));
}

// ---------------------------------------------------------------- sanitising

function cleanColors(raw) {
  const out = {};
  const source = raw && typeof raw === "object" ? raw : {};
  // stored names are lapA / lapB / delta
  const pick = { a: source.lapA ?? source.a, b: source.lapB ?? source.b, delta: source.delta };
  for (const key of ["a", "b", "delta"]) out[key] = isHexColor(pick[key]) ? pick[key].toLowerCase() : DEFAULT_COLORS[key];
  return out;
}

function cleanWidgetColors(raw) {
  if (!raw || typeof raw !== "object") return undefined;
  const out = {};
  if (isHexColor(raw.a)) out.a = raw.a.toLowerCase();
  if (isHexColor(raw.b)) out.b = raw.b.toLowerCase();
  return Object.keys(out).length ? out : undefined;
}

// Any value -> a valid layout. `validChannels`: optional Set of catalog keys; tiles with a
// channel outside it are dropped (a channel that no longer exists).
export function sanitizeLayout(raw, { validChannels } = {}) {
  try {
    const migrated = migrate(raw);
    if (!migrated || !Array.isArray(migrated.layout)) return defaultLayout();
    const seen = new Set();
    const accepted = [];
    const entries = migrated.layout.filter((item) => item && typeof item === "object");
    for (const item of entries) {
      if (accepted.length >= MAX_WIDGETS) break;
      if (!TYPES.includes(item.type)) continue;
      const widget = { type: item.type };
      if (CHANNEL_TYPES.includes(item.type)) {
        if (typeof item.channel !== "string" || !CHANNEL_KEY.test(item.channel)) continue;
        if (validChannels && !validChannels.has(item.channel)) continue;
        if (seen.has(`${item.type}:${item.channel}`)) continue; // one tile per channel and kind
        seen.add(`${item.type}:${item.channel}`);
        widget.channel = item.channel;
        const colors = item.type === "chart" ? cleanWidgetColors(item.colors) : undefined;
        if (colors) widget.colors = colors;
      } else {
        if (seen.has(item.type)) continue;
        seen.add(item.type);
        if (item.type === "trackmap") widget.mode = TRACKMAP_MODES.includes(item.mode) ? item.mode : "delta";
      }
      Object.assign(widget, clampBox(item.type, item));
      accepted.push(widget);
    }
    if (!accepted.length) return defaultLayout();
    // Overlaps (hand-edited storage): the earlier tile in reading order keeps its cells,
    // later ones move down — the same rule as a drop.
    const ordered = accepted.sort(byReadingOrder).map((w, i) => ({ ...w, id: `t${i}` }));
    const resolved = [];
    for (const tile of ordered) {
      let hit = resolved.find((p) => overlaps(p, tile));
      while (hit) {
        tile.y = hit.y + hit.h;
        hit = resolved.find((p) => overlaps(p, tile));
      }
      resolved.push(tile);
    }
    return { version: LAYOUT_VERSION, widgets: finish(resolved), colors: cleanColors(migrated.colors) };
  } catch {
    return defaultLayout();
  }
}

// ---------------------------------------------------------------- versions

// Version 1 -> 2: the old ordered list flowed onto the grid the way the old CSS grid showed it
// — left to right, a new row when the next window does not fit, each row as high as its
// tallest window. Widths: small 4, medium 6, large 12 columns; heights: the type's default.
function flowV1(old) {
  const items = Array.isArray(old.layout) ? old.layout : null;
  if (!items) return { ...old, layout: null };
  const ordered = items
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => item && typeof item === "object")
    .sort((p, q) => {
      const pp = Number.isFinite(p.item.position) ? p.item.position : p.index;
      const pq = Number.isFinite(q.item.position) ? q.item.position : q.index;
      return pp - pq || p.index - q.index;
    });
  let x = 0;
  let y = 0;
  let rowHeight = 0;
  const layout = [];
  for (const { item } of ordered) {
    const spec = tileSpec(item.type);
    const w = Math.max(LEGACY_WIDTH[item.size] || spec.def[0], spec.min[0]);
    const h = spec.def[1];
    if (x + w > COLS) {
      y += rowHeight;
      x = 0;
      rowHeight = 0;
    }
    const { position, size, ...rest } = item; // eslint-disable-line no-unused-vars
    layout.push({ ...rest, x, y, w, h });
    x += w;
    rowHeight = Math.max(rowHeight, h);
  }
  return { ...old, layout };
}

// MIGRATIONS[n](stored) returns the stored object upgraded from version n to n+1.
export const MIGRATIONS = { 1: flowV1 };

export function migrate(raw, migrations = MIGRATIONS, target = LAYOUT_VERSION) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  let current = raw;
  let version = Number.isInteger(current.version) ? current.version : null;
  if (version === null || version < 1) return null;
  if (version > target) return null; // written by a newer Garage16: do not guess
  while (version < target) {
    const step = migrations[version];
    if (typeof step !== "function") return null;
    current = step(current);
    version += 1;
    if (!current || typeof current !== "object") return null;
    current = { ...current, version };
  }
  return current;
}

// ---------------------------------------------------------------- storage

// What goes to storage (the shape above, in reading order).
export function toStored(layout) {
  return {
    version: LAYOUT_VERSION,
    layout: layout.widgets.map((w) => {
      const out = { type: w.type, x: w.x, y: w.y, w: w.w, h: w.h };
      if (w.channel) out.channel = w.channel;
      if (w.mode) out.mode = w.mode;
      if (w.colors) out.colors = { ...w.colors };
      return out;
    }),
    colors: { lapA: layout.colors.a, lapB: layout.colors.b, delta: layout.colors.delta },
  };
}

function legacyLayout(storage, validChannels) {
  try {
    const stored = JSON.parse(storage.getItem(LEGACY_CHANNEL_KEY) || "null");
    if (!Array.isArray(stored)) return null;
    const keys = stored.filter((k) => typeof k === "string" && (!validChannels || validChannels.has(k))).slice(0, MAX_REQUEST_CHANNELS);
    if (!keys.length) return null;
    const list = [{ type: "summary", size: "large" }, { type: "delta", size: "large" }, { type: "corners", size: "large" }];
    for (const channel of keys) list.push({ type: "chart", channel, size: "large" });
    list.push({ type: "trackmap", size: "medium", mode: "delta" }, { type: "cursor_table", size: "medium" }, { type: "physics", size: "large" });
    return { version: 1, layout: list };
  } catch {
    return null;
  }
}

// -> { layout, source: "stored" | "legacy" | "default", storageOk }
export function loadLayout(storage, { validChannels } = {}) {
  let raw = null;
  let storageOk = true;
  try {
    const text = storage.getItem(STORAGE_KEY);
    raw = text ? JSON.parse(text) : null;
  } catch (err) {
    // JSON.parse failure = corrupt value (use the default); a throwing getItem = no storage at all
    storageOk = !(err instanceof Error && err.name !== "SyntaxError");
    raw = null;
  }
  if (raw) {
    const migrated = migrate(raw);
    if (migrated) return { layout: sanitizeLayout(raw, { validChannels }), source: "stored", storageOk };
  }
  const legacy = legacyLayout(storage, validChannels);
  if (legacy) return { layout: sanitizeLayout(legacy, { validChannels }), source: "legacy", storageOk };
  return { layout: defaultLayout(), source: "default", storageOk };
}

export function saveLayout(storage, layout) {
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(toStored(layout)));
    return true;
  } catch {
    return false;
  }
}

// Removes ONLY the user's layout (and the old channel selection it replaced).
export function clearStoredLayout(storage) {
  try {
    storage.removeItem(STORAGE_KEY);
    storage.removeItem(LEGACY_CHANNEL_KEY);
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------- edits (all return a NEW layout)

const withWidgets = (layout, widgets) => ({ ...layout, widgets: finish(widgets) });

const edit = (layout, id, patch) => {
  const index = layout.widgets.findIndex((w) => w.id === id);
  if (index < 0) return layout;
  const widgets = layout.widgets.slice();
  widgets[index] = { ...widgets[index], ...patch };
  return withWidgets(layout, widgets);
};

// Puts a tile at a new box; what is in the way moves down. The tile keeps its identity
// (channel, colours, mode).
function reposition(layout, id, box) {
  const tile = layout.widgets.find((w) => w.id === id);
  if (!tile) return layout;
  const next = clampBox(tile.type, { x: box.x ?? tile.x, y: box.y ?? tile.y, w: box.w ?? tile.w, h: box.h ?? tile.h });
  if (next.x === tile.x && next.y === tile.y && next.w === tile.w && next.h === tile.h) return layout;
  const widgets = layout.widgets.map((w) => (w.id === id ? { ...w, ...next } : w));
  return withWidgets(layout, pushDown(widgets, [id]));
}

// Moves a tile to cell (x, y).
export function placeWidget(layout, id, x, y) {
  return reposition(layout, id, { x, y });
}

// Moves a tile by whole cells (keyboard: arrow keys on the handle).
export function shiftWidget(layout, id, dx, dy) {
  const tile = layout.widgets.find((w) => w.id === id);
  if (!tile) return layout;
  // Going up into another tile would push THAT tile down below this one — i.e. the two swap
  // places, which is what "move up" means; going down past a tile does the same the other way.
  if (dy < 0) {
    const above = layout.widgets
      .filter((w) => w.id !== id && w.y + w.h <= tile.y && w.x < tile.x + tile.w && tile.x < w.x + w.w)
      .sort((p, q) => (q.y + q.h) - (p.y + p.h))[0];
    if (above && above.y + above.h === tile.y) return placeWidget(layout, id, tile.x, above.y);
  }
  if (dy > 0) {
    const below = layout.widgets
      .filter((w) => w.id !== id && w.y >= tile.y + tile.h && w.x < tile.x + tile.w && tile.x < w.x + w.w)
      .sort((p, q) => p.y - q.y)[0];
    if (below && below.y === tile.y + tile.h) {
      // the tile below moves up into our place, we go below it
      const swapped = layout.widgets.map((w) => {
        if (w.id === below.id) return { ...w, y: tile.y };
        if (w.id === id) return { ...w, y: tile.y + below.h };
        return w;
      });
      return withWidgets(layout, pushDown(swapped, [below.id, id]));
    }
  }
  return placeWidget(layout, id, tile.x + dx, tile.y + dy);
}

// Sets a tile's size in cells (clamped to its minimum and the grid); what is in the way moves down.
export function resizeWidget(layout, id, w, h) {
  return reposition(layout, id, { w, h });
}

// Back-compat for the old size names (small / medium / large = 4 / 6 / 12 columns).
export function setSize(layout, id, size) {
  if (!SIZES.includes(size)) return layout;
  const tile = layout.widgets.find((w) => w.id === id);
  return tile ? resizeWidget(layout, id, LEGACY_WIDTH[size], tile.h) : layout;
}

// Every tile moves up as far as it can (keeps columns and reading order): closes the gaps.
export function compactLayout(layout) {
  const placed = [];
  for (const tile of layout.widgets.slice().sort(byReadingOrder)) {
    const moved = { ...tile };
    while (moved.y > 0 && !placed.some((p) => overlaps(p, { ...moved, y: moved.y - 1 }))) moved.y -= 1;
    placed.push(moved);
  }
  return withWidgets(layout, placed);
}

export function setChannel(layout, id, channel, validChannels) {
  const widget = layout.widgets.find((w) => w.id === id);
  if (!widget || !CHANNEL_TYPES.includes(widget.type) || typeof channel !== "string" || !CHANNEL_KEY.test(channel)) return layout;
  if (validChannels && !validChannels.has(channel)) return layout;
  if (layout.widgets.some((w) => w.id !== id && w.type === widget.type && w.channel === channel)) return layout; // already shown
  return edit(layout, id, { channel });
}

export function setTrackmapMode(layout, id, mode) {
  const widget = layout.widgets.find((w) => w.id === id);
  return widget && widget.type === "trackmap" && TRACKMAP_MODES.includes(mode) ? edit(layout, id, { mode }) : layout;
}

export function setWidgetColors(layout, id, colors) {
  const widget = layout.widgets.find((w) => w.id === id);
  if (!widget || widget.type !== "chart") return layout;
  const cleaned = cleanWidgetColors(colors);
  const next = { ...widget };
  if (cleaned) next.colors = cleaned;
  else delete next.colors;
  return withWidgets(layout, layout.widgets.map((w) => (w === widget ? next : w)));
}

export function setColors(layout, colors) {
  return { ...layout, colors: cleanColors({ lapA: colors.a ?? layout.colors.a, lapB: colors.b ?? layout.colors.b, delta: colors.delta ?? layout.colors.delta }) };
}

export function removeWidget(layout, id) {
  const widgets = layout.widgets.filter((w) => w.id !== id);
  return widgets.length === layout.widgets.length || !widgets.length ? layout : withWidgets(layout, widgets);
}

// Can this tile be added at all (single tiles once, one tile per channel and kind)?
export function canAdd(layout, spec, validChannels) {
  if (layout.widgets.length >= MAX_WIDGETS || !spec || !TYPES.includes(spec.type)) return false;
  if (CHANNEL_TYPES.includes(spec.type)) {
    if (typeof spec.channel !== "string" || !CHANNEL_KEY.test(spec.channel)) return false;
    if (validChannels && !validChannels.has(spec.channel)) return false;
    return !layout.widgets.some((w) => w.type === spec.type && w.channel === spec.channel);
  }
  return !layout.widgets.some((w) => w.type === spec.type);
}

// spec: { type, channel?, mode?, x?, y?, w?, h? }. Without a position the tile goes to the
// first free spot that fits its size; with one, it goes exactly there and what is in the way
// moves down. Refused (layout returned unchanged): single tiles twice, a channel already shown
// in that kind, unknown types / channels, more than MAX_WIDGETS.
export function addWidget(layout, spec, validChannels) {
  if (!canAdd(layout, spec, validChannels)) return layout;
  const { def } = tileSpec(spec.type);
  const legacyW = SIZES.includes(spec.size) ? LEGACY_WIDTH[spec.size] : undefined;
  const size = clampBox(spec.type, { x: 0, y: 0, w: spec.w ?? legacyW ?? def[0], h: spec.h ?? def[1] });
  const hasPosition = Number.isFinite(spec.x) && Number.isFinite(spec.y);
  const spot = hasPosition ? { x: spec.x, y: spec.y } : findFreeSpot(layout.widgets, size.w, size.h);
  const box = clampBox(spec.type, { ...size, ...spot });
  const widget = { type: spec.type, ...box, id: "__new" };
  if (CHANNEL_TYPES.includes(spec.type)) widget.channel = spec.channel;
  if (spec.type === "trackmap") widget.mode = TRACKMAP_MODES.includes(spec.mode) ? spec.mode : "delta";
  const widgets = pushDown([...layout.widgets, widget], ["__new"]);
  return withWidgets(layout, widgets);
}

// ---------------------------------------------------------------- what the layout needs

// Catalog channels the charts show — what has to be requested from the server.
// Trackmap colour channels are fetched on demand when that mode is chosen; histogram,
// damper and tyre tiles load their own data (distribution / on demand), so they add
// nothing here.
export function requiredChannels(layout) {
  const keys = [];
  for (const w of layout.widgets) {
    if (w.type === "chart" && !keys.includes(w.channel)) keys.push(w.channel);
    if (w.type === "trackmap" && w.mode && w.mode !== "delta" && w.mode !== "none" && !keys.includes(w.mode)) keys.push(w.mode);
  }
  return keys.slice(0, MAX_REQUEST_CHANNELS);
}

export function layoutsEqual(x, y) {
  return JSON.stringify(toStored(x)) === JSON.stringify(toStored(y));
}

// ---------------------------------------------------------------- the store the page uses

// Holds the current layout, persists every change, and applies the colours.
// `storage` is localStorage (or anything with get/set/removeItem); `onColors` is
// called with the colour set whenever the layout is (re)loaded or changed.
export function createLayoutStore(storage, { validChannels, onColors } = {}) {
  const loaded = loadLayout(storage, { validChannels });
  let layout = loaded.layout;
  let persisted = loaded.storageOk;
  const announce = () => onColors && onColors(layout.colors);
  announce();
  // (A migrated version-1 layout stays as it is in storage until the first change saves it in
  // the new shape — loading never writes.)
  return {
    source: loaded.source,
    get: () => layout,
    // false when the browser refused to store it (the view still changes, for this session)
    isPersisted: () => persisted,
    set(next) {
      layout = sanitizeLayout(toStored(next), { validChannels });
      persisted = saveLayout(storage, layout);
      announce();
      return layout;
    },
    // Back to the Garage16 standard layout; removes the stored layout, nothing else.
    reset() {
      layout = defaultLayout();
      persisted = clearStoredLayout(storage);
      announce();
      return layout;
    },
  };
}
