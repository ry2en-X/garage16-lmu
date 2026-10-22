// analysis/tile_catalog.js — every tile the user can put on the grid, sorted the way the
// picker shows them (V0.9.5):
//
//   1. Wofür?   (purpose)  — e.g. "Forces acting on the car"
//   2. Art?     (kind)     — e.g. "Chart along the lap"
//   3. the tiles of that purpose and kind — e.g. "Lateral G", "Tyre load front left" …
//
// Pure data, no DOM: the picker (tile_picker.js) renders it, the tests check it. A channel's
// purpose follows from the group the SERVER puts it in (telemetry_channels.py) plus a few
// channels that measure a force but live in another group; anything unknown lands in
// "Other channels", so a channel added on the server shows up without a code change here.

import { t } from "../i18n.js";
import { canAdd, fitsSize, SINGLE_TYPES, tileSpec } from "./layout.js";
import { moduleForType } from "./modules.js";

export const PURPOSES = [
  { id: "time", label: t("Lap time & delta"), hint: t("Where the time is won and lost") },
  { id: "inputs", label: t("Driver inputs & speed"), hint: t("Throttle, brake, steering, gear, speed") },
  { id: "corners", label: t("Corners & racing line"), hint: t("Lines, braking points, corner phases") },
  { id: "forces", label: t("Forces acting on the car"), hint: t("G-forces, tyre forces, wheel loads, aero") },
  { id: "tyres", label: t("Tyres & brakes"), hint: t("Temperatures, pressures, slip, wear, brakes") },
  { id: "chassis", label: t("Suspension & dampers"), hint: t("Travel, ride height, heave, roll, dampers") },
  { id: "powertrain", label: t("Engine & energy"), hint: t("RPM, torque, fuel, battery, temperatures") },
  { id: "aids", label: t("Driver aids & settings"), hint: t("TC, ABS, maps, brake bias, anti-roll bars") },
  { id: "track", label: t("Track & track limits"), hint: t("Track map, kerbs, track limits, events") },
  { id: "values", label: t("All values"), hint: t("Every loaded channel at the cursor") },
  { id: "data", label: t("Data quality"), hint: t("Is the recording itself trustworthy?") },
  { id: "other", label: t("Other channels"), hint: t("Channels without a better place") },
];

export const KINDS = [
  { id: "chart", label: t("Chart along the lap"), hint: t("One channel of every lap over the distance") },
  { id: "histogram", label: t("Histogram"), hint: t("How the lap's time is spread over one channel") },
  { id: "map", label: t("Map"), hint: t("The track seen from above") },
  { id: "diagram", label: t("Special diagram"), hint: t("G-G diagram, per-wheel views") },
  { id: "table", label: t("Table & values"), hint: t("Numbers to read and compare") },
  { id: "analysis", label: t("Analysis & hints"), hint: t("Evaluated for you: corners, phases, hints") },
];

// Server group -> purpose.
const GROUP_PURPOSE = {
  "Speed & inputs": "inputs",
  "Motion & G": "forces",
  "Track limits": "track",
  "Driver aids": "aids",
  Suspension: "chassis",
  Dampers: "chassis",
  Tyres: "tyres",
  "Tyre temperatures": "tyres",
  Brakes: "tyres",
  Aero: "forces",
  "Powertrain & energy": "powertrain",
  "Racing line": "corners",
};
// Channels whose group says one thing but which measure a force (or are clearly about
// something else).
const CHANNEL_PURPOSE = [
  [/^tire_load_/, "forces"], [/^lat_force_/, "forces"], [/^long_force_/, "forces"], [/^susp_force_/, "chassis"],
  [/^rpm$/, "powertrain"], [/^front_wing_height$/, "chassis"], [/^delta_best$/, "time"], [/^line_offset$/, "corners"],
  [/^steering_torque$/, "inputs"],
];

export function purposeOfChannel(channel) {
  for (const [pattern, purpose] of CHANNEL_PURPOSE) if (pattern.test(channel.key)) return purpose;
  return GROUP_PURPOSE[channel.group] || "other";
}

// The single tiles: purpose and kind.
const SINGLE_PLACE = {
  summary: ["time", "table"],
  delta: ["time", "chart"],
  sectors: ["time", "table"],
  coaching: ["time", "analysis"],
  corners: ["corners", "analysis"],
  corner_detail: ["corners", "analysis"],
  line: ["corners", "map"],
  trackmap: ["track", "map"],
  events: ["track", "table"],
  cursor_table: ["values", "table"],
  physics: ["forces", "diagram"],
  tyres: ["tyres", "diagram"],
  damper: ["chassis", "histogram"],
  health: ["data", "table"],
};

// A stable key per tile: "type:<type>" for single tiles, "chart:<channel>", "histogram:<channel>".
export const tileKey = (spec) => (spec.channel ? `${spec.type}:${spec.channel}` : `type:${spec.type}`);

/**
 * Every tile that exists for this channel catalog:
 *   [{ key, type, channel?, purpose, kind, title, description, unit?, group?, min, def, presets }]
 * Charts for every channel, histograms for every channel that belongs to one lap (not the
 * lap-pair channel line_offset), and every single tile.
 */
export function buildTileCatalog(catalog) {
  const tiles = [];
  for (const type of SINGLE_TYPES) {
    const module = moduleForType(type);
    const [purpose, kind] = SINGLE_PLACE[type] || ["other", "analysis"];
    const spec = tileSpec(type);
    tiles.push({ key: `type:${type}`, type, purpose, kind, title: module ? module.title : type, description: module ? module.description : "", ...specOf(spec) });
  }
  for (const channel of (catalog && catalog.channels) || []) {
    const purpose = purposeOfChannel(channel);
    const label = channel.label || channel.key;
    tiles.push({
      key: `chart:${channel.key}`, type: "chart", channel: channel.key, purpose, kind: "chart", title: label, unit: channel.unit || "", group: channel.group,
      description: t("{0} of every lap along the track.", [label]), ...specOf(tileSpec("chart")),
    });
    if (!channel.pair) {
      tiles.push({
        key: `histogram:${channel.key}`, type: "histogram", channel: channel.key, purpose, kind: "histogram", title: label, unit: channel.unit || "", group: channel.group,
        description: t("How much of the lap's time was spent at each value of {0}.", [label]), ...specOf(tileSpec("histogram")),
      });
    }
  }
  return tiles;
}

const specOf = (spec) => ({ min: spec.min.slice(), def: spec.def.slice(), presets: spec.presets.map((p) => p.slice()) });

// The tree the picker shows: purposes -> kinds -> tiles; empty branches are left out.
// opts.fit = { w, h }: only tiles that suit a box of that size (a drawn rectangle).
// opts.layout (+ validChannels): tiles that are already on the grid are marked `taken`.
// opts.query: a text filter over title, unit, group and description.
export function tileTree(tiles, { fit = null, layout = null, validChannels = null, query = "" } = {}) {
  const needle = String(query || "").trim().toLowerCase();
  const visible = tiles.filter((tile) => {
    if (fit && !fitsSize(tile.type, fit.w, fit.h)) return false;
    if (!needle) return true;
    return [tile.title, tile.unit, tile.group, tile.description].some((s) => s && s.toLowerCase().includes(needle));
  });
  const tree = [];
  for (const purpose of PURPOSES) {
    const ofPurpose = visible.filter((tile) => tile.purpose === purpose.id);
    if (!ofPurpose.length) continue;
    const kinds = [];
    for (const kind of KINDS) {
      const ofKind = ofPurpose.filter((tile) => tile.kind === kind.id).map((tile) => ({
        ...tile,
        taken: layout ? !canAdd(layout, { type: tile.type, channel: tile.channel }, validChannels) : false,
      }));
      if (ofKind.length) kinds.push({ ...kind, tiles: ofKind });
    }
    tree.push({ ...purpose, count: ofPurpose.length, kinds });
  }
  return tree;
}

// The presets of a tile that are still possible in a box of `fit` (or all of them).
export function presetsFor(tile, fit = null) {
  return tile.presets.filter(([w, h]) => !fit || (w <= fit.w && h <= fit.h));
}
