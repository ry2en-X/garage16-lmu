// analysis/modules.js — the registry of analysis windows (V0.9.1). One definition per
// window type; analysis.js and the layout editor read this instead of knowing the
// types themselves, so a new window is one entry here plus its renderer.
//
//   id, title, description, type, supportedChannels, min / presets / defaultCells (grid cells, V0.9.5), ("*" = any catalog channel,
//   [] = none), defaultSize, defaultPosition (index in the Garage16 standard layout,
//   or null when the window is not part of it), renderer, mobileSupport.

import { t } from "../i18n.js";
import { defaultLayout, tileSpec } from "./layout.js";
import { renderChart, renderCorners, renderCursorTable, renderDelta, renderPhysics, renderSummary, renderTrackmap } from "./widgets.js";
import { renderCoaching } from "./coaching.js";
import { renderDamper, renderHistogram } from "./distribution.js";
import { renderEvents } from "./events.js";
import { renderTyres } from "./tyres.js";
import { renderCornerDetail, renderLine, renderSectors } from "./corner_detail.js";
import { renderHealth } from "./health.js";

const standard = defaultLayout().widgets;
const positionOf = (type) => {
  const index = standard.findIndex((w) => w.type === type);
  return index < 0 ? null : index;
};
const sizeOf = (type, fallback) => (standard.find((w) => w.type === type) || { size: fallback }).size;

export const MODULES = [
  { id: "summary", title: t("Lap summary & sectors"), description: t("Both laps, the time difference and the sector comparison."), type: "summary", supportedChannels: [], renderer: renderSummary, mobileSupport: true },
  { id: "delta", title: t("Delta"), description: t("Where Lap 1 gains and loses time, with the biggest gain and loss."), type: "delta", supportedChannels: [], renderer: renderDelta, mobileSupport: true },
  { id: "corners", title: t("Corners"), description: t("Every corner: time gained or lost, braking point, apex and exit speed of both laps — click one to zoom in."), type: "corners", supportedChannels: [], renderer: renderCorners, mobileSupport: true },
  { id: "chart", title: t("Channel chart"), description: t("One telemetry channel of both laps along the track (speed, throttle, brake, gear, RPM, G-forces, tyres, suspension …)."), type: "chart", supportedChannels: "*", renderer: renderChart, mobileSupport: true },
  { id: "trackmap", title: t("Track map"), description: t("The track, coloured by delta or a channel, with the shared cursor."), type: "trackmap", supportedChannels: ["speed", "throttle", "brake", "gear", "rpm", "lat_g", "long_g"], renderer: renderTrackmap, mobileSupport: true },
  { id: "cursor_table", title: t("Values at the cursor"), description: t("All loaded channels of both laps at the cursor position."), type: "cursor_table", supportedChannels: [], renderer: renderCursorTable, mobileSupport: true },
  { id: "physics", title: t("Suspension, tyres & G-G"), description: t("Suspension travel, wheel load, slip, temperatures and the G-G diagram at the cursor."), type: "physics", supportedChannels: [], renderer: renderPhysics, mobileSupport: true },
  // V0.9.3
  { id: "coaching", title: t("Where to find time"), description: t("The corners where Lap 1 loses most, with hints built from measured braking points, speeds and lock-ups."), type: "coaching", supportedChannels: [], renderer: renderCoaching, mobileSupport: true },
  { id: "events", title: t("Track limits & events"), description: t("The server's track-limits verdict with every finding, plus lock-ups and wheelspin — click one to jump there."), type: "events", supportedChannels: [], renderer: renderEvents, mobileSupport: true },
  { id: "tyres", title: t("Tyres & brakes"), description: t("Tread temperature inner / middle / outer, pressure, wear and brake temperature per wheel — lap averages and at the cursor."), type: "tyres", supportedChannels: [], renderer: renderTyres, mobileSupport: true },
  { id: "damper", title: t("Damper histograms"), description: t("Damper velocity distribution per wheel: bump / rebound, low- and high-speed."), type: "damper", supportedChannels: [], renderer: renderDamper, mobileSupport: true },
  { id: "histogram", title: t("Histogram"), description: t("How the lap's time is distributed over one channel (time per gear, tyre temperature, ride height …)."), type: "histogram", supportedChannels: "*", renderer: renderHistogram, mobileSupport: true },
  // V0.9.4
  { id: "line", title: t("Racing lines"), description: t("The driven lines of both laps on a zoomable map: braking, turn-in, apex and full-throttle points, the line distance at the cursor."), type: "line", supportedChannels: [], renderer: renderLine, mobileSupport: true },
  { id: "corner_detail", title: t("Corner in detail"), description: t("One corner: the time per phase (braking, turn-in, apex, exit), every measured value of both laps and the same corner on your earlier laps."), type: "corner_detail", supportedChannels: [], renderer: renderCornerDetail, mobileSupport: true },
  { id: "sectors", title: t("Sectors of the session"), description: t("Sector times of every lap of the session, the fastest driven sectors and this lap's potential."), type: "sectors", supportedChannels: [], renderer: renderSectors, mobileSupport: true },
  // V0.9.7
  { id: "health", title: t("Data quality"), description: t("How trustworthy each lap's recording is: channels, samples, gaps, distance axis, speed vs distance, resolution — next to the lap's validity."), type: "health", supportedChannels: [], renderer: renderHealth, mobileSupport: true },
].map((m) => ({
  ...m, defaultSize: sizeOf(m.type, "large"), defaultPosition: positionOf(m.type),
  // V0.9.5: sizes on the grid (cells) — the smallest readable, the ready-made ones, the default
  min: tileSpec(m.type).min, presets: tileSpec(m.type).presets, defaultCells: tileSpec(m.type).def,
}));

export const moduleForType = (type) => MODULES.find((m) => m.type === type) || null;
