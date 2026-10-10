// analysis/layout.js — the user's analysis layout: which windows, in which order,
// how big, which channel, which colours (V0.9.1). Pure data + a thin storage
// wrapper; no DOM, so every rule is testable in plain Node.
//
// Stored in the browser (localStorage) — a UI preference, not telemetry, and never
// anything but the JSON below. Shape (version 1):
//
//   { "version": 1,
//     "layout": [ { "type": "chart", "channel": "speed", "size": "large", "position": 0 }, ... ],
//     "colors": { "lapA": "#e0472b", "lapB": "#4fb3e8", "delta": "#ecedee" } }
//
// Rules this module guarantees:
//   * whatever is in storage — missing, corrupt, hand-edited, from the future —
//     sanitizeLayout() turns it into a VALID layout or falls back to the default;
//     it never throws and never lets a bad value reach the views
//   * colours: only #rrggbb is accepted, anything else falls back per entry to the
//     built-in colour, so the defaults cannot be broken by a stored configuration
//   * a layout is always renderable: known window types, known sizes, no duplicates
//     of single-instance windows, at least one window
//   * versioned: MIGRATIONS[n] upgrades version n to n+1; an unknown (newer)
//     version is ignored rather than misread

import { DEFAULT_COLORS, isHexColor } from "./model.js";

export const LAYOUT_VERSION = 1;
export const STORAGE_KEY = "garage16_analysis_layout";
export const LEGACY_CHANNEL_KEY = "garage16_analysis_channels"; // V0.9.0's channel selection
export const SIZES = ["small", "medium", "large"];
export const MAX_WIDGETS = 30;
export const MAX_REQUEST_CHANNELS = 24; // the server's limit per request

// Window types. "chart" may appear many times (one channel each); the others once.
export const SINGLE_TYPES = ["summary", "delta", "corners", "trackmap", "cursor_table", "physics"];
export const TYPES = [...SINGLE_TYPES, "chart"];
export const TRACKMAP_MODES = ["delta", "none", "speed", "throttle", "brake", "gear", "rpm", "lat_g", "long_g"];

const CHANNEL_KEY = /^[a-z0-9_]{1,48}$/;

// The Garage16 standard layout. The same for everyone, always available.
const STANDARD = [
  { type: "summary", size: "large" },
  { type: "delta", size: "large" },
  { type: "corners", size: "large" },
  { type: "chart", channel: "speed", size: "large" },
  { type: "chart", channel: "throttle", size: "large" },
  { type: "chart", channel: "brake", size: "large" },
  { type: "trackmap", size: "medium", mode: "delta" },
  { type: "cursor_table", size: "medium" },
  { type: "physics", size: "large" },
];

function renumber(widgets) {
  return widgets.map((w, i) => ({ ...w, id: `w${i + 1}` }));
}

export function defaultLayout() {
  return {
    version: LAYOUT_VERSION,
    widgets: renumber(STANDARD.map((w) => ({ ...w }))),
    colors: { a: DEFAULT_COLORS.a, b: DEFAULT_COLORS.b, delta: DEFAULT_COLORS.delta },
  };
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

// Any value -> a valid layout. `validChannels`: optional Set of catalog keys; charts
// with a channel outside it are dropped (a channel that no longer exists).
export function sanitizeLayout(raw, { validChannels } = {}) {
  try {
    const migrated = migrate(raw);
    if (!migrated || !Array.isArray(migrated.layout)) return defaultLayout();
    const seen = new Set();
    const widgets = [];
    const entries = migrated.layout
      .map((item, index) => ({ item, index }))
      .filter(({ item }) => item && typeof item === "object")
      .sort((x, y) => {
        const px = Number.isFinite(x.item.position) ? x.item.position : x.index;
        const py = Number.isFinite(y.item.position) ? y.item.position : y.index;
        return px - py || x.index - y.index;
      });
    for (const { item } of entries) {
      if (widgets.length >= MAX_WIDGETS) break;
      if (!TYPES.includes(item.type)) continue;
      const size = SIZES.includes(item.size) ? item.size : defaultSize(item.type);
      const widget = { type: item.type, size };
      if (item.type === "chart") {
        if (typeof item.channel !== "string" || !CHANNEL_KEY.test(item.channel)) continue;
        if (validChannels && !validChannels.has(item.channel)) continue;
        if (seen.has(`chart:${item.channel}`)) continue; // one window per channel
        seen.add(`chart:${item.channel}`);
        const colors = cleanWidgetColors(item.colors);
        widget.channel = item.channel;
        if (colors) widget.colors = colors;
      } else {
        if (seen.has(item.type)) continue;
        seen.add(item.type);
        if (item.type === "trackmap") widget.mode = TRACKMAP_MODES.includes(item.mode) ? item.mode : "delta";
      }
      widgets.push(widget);
    }
    if (!widgets.length) return defaultLayout();
    return { version: LAYOUT_VERSION, widgets: renumber(widgets), colors: cleanColors(migrated.colors) };
  } catch {
    return defaultLayout();
  }
}

function defaultSize(type) {
  const found = STANDARD.find((w) => w.type === type);
  return found ? found.size : "large";
}

// ---------------------------------------------------------------- versions

// MIGRATIONS[n](stored) returns the stored object upgraded from version n to n+1.
// (Empty until a version 2 exists; the mechanism is tested with a fake step.)
export const MIGRATIONS = {};

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

// What goes to storage (the spec's shape, with explicit positions).
export function toStored(layout) {
  return {
    version: LAYOUT_VERSION,
    layout: layout.widgets.map((w, position) => {
      const out = { type: w.type, size: w.size, position };
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
    const widgets = [{ type: "summary", size: "large" }, { type: "delta", size: "large" }, { type: "corners", size: "large" }];
    for (const channel of keys) widgets.push({ type: "chart", channel, size: "large" });
    widgets.push({ type: "trackmap", size: "medium", mode: "delta" }, { type: "cursor_table", size: "medium" }, { type: "physics", size: "large" });
    return { version: LAYOUT_VERSION, widgets: renumber(widgets), colors: cleanColors(null) };
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
  if (legacy) return { layout: sanitizeLayout(toStored(legacy), { validChannels }), source: "legacy", storageOk };
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

const withWidgets = (layout, widgets) => ({ ...layout, widgets: renumber(widgets) });

export function moveWidget(layout, from, to) {
  const n = layout.widgets.length;
  if (!Number.isInteger(from) || !Number.isInteger(to) || from < 0 || from >= n) return layout;
  const target = Math.min(Math.max(to, 0), n - 1);
  if (from === target) return layout;
  const widgets = layout.widgets.slice();
  const [moved] = widgets.splice(from, 1);
  widgets.splice(target, 0, moved);
  return withWidgets(layout, widgets);
}

export function nudgeWidget(layout, id, direction) {
  const index = layout.widgets.findIndex((w) => w.id === id);
  return index < 0 ? layout : moveWidget(layout, index, index + (direction < 0 ? -1 : 1));
}

const edit = (layout, id, patch) => {
  const index = layout.widgets.findIndex((w) => w.id === id);
  if (index < 0) return layout;
  const widgets = layout.widgets.slice();
  widgets[index] = { ...widgets[index], ...patch };
  return withWidgets(layout, widgets);
};

export function setSize(layout, id, size) {
  return SIZES.includes(size) ? edit(layout, id, { size }) : layout;
}

export function setChannel(layout, id, channel, validChannels) {
  const widget = layout.widgets.find((w) => w.id === id);
  if (!widget || widget.type !== "chart" || typeof channel !== "string" || !CHANNEL_KEY.test(channel)) return layout;
  if (validChannels && !validChannels.has(channel)) return layout;
  if (layout.widgets.some((w) => w.id !== id && w.type === "chart" && w.channel === channel)) return layout; // already shown
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
  const widgets = layout.widgets.slice();
  widgets[layout.widgets.indexOf(widget)] = next;
  return withWidgets(layout, widgets);
}

export function setColors(layout, colors) {
  return { ...layout, colors: cleanColors({ lapA: colors.a ?? layout.colors.a, lapB: colors.b ?? layout.colors.b, delta: colors.delta ?? layout.colors.delta }) };
}

export function removeWidget(layout, id) {
  const widgets = layout.widgets.filter((w) => w.id !== id);
  return widgets.length === layout.widgets.length || !widgets.length ? layout : withWidgets(layout, widgets);
}

// spec: { type, channel?, size?, mode? }. Single-instance windows and a channel
// that is already shown are refused (the layout is returned unchanged).
export function addWidget(layout, spec, validChannels) {
  if (layout.widgets.length >= MAX_WIDGETS || !spec || !TYPES.includes(spec.type)) return layout;
  const widget = { type: spec.type, size: SIZES.includes(spec.size) ? spec.size : defaultSize(spec.type) };
  if (spec.type === "chart") {
    if (typeof spec.channel !== "string" || !CHANNEL_KEY.test(spec.channel)) return layout;
    if (validChannels && !validChannels.has(spec.channel)) return layout;
    if (layout.widgets.some((w) => w.type === "chart" && w.channel === spec.channel)) return layout;
    widget.channel = spec.channel;
  } else {
    if (layout.widgets.some((w) => w.type === spec.type)) return layout;
    if (spec.type === "trackmap") widget.mode = TRACKMAP_MODES.includes(spec.mode) ? spec.mode : "delta";
  }
  return withWidgets(layout, [...layout.widgets, widget]);
}

// ---------------------------------------------------------------- what the layout needs

// Catalog channels the charts show — what has to be requested from the server.
// Trackmap colour channels are fetched on demand when that mode is chosen.
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
