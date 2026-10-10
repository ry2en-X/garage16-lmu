// analysis/modules.js — the registry of analysis windows (V0.9.1). One definition per
// window type; analysis.js and the layout editor read this instead of knowing the
// types themselves, so a new window is one entry here plus its renderer.
//
//   id, title, description, type, supportedChannels ("*" = any catalog channel,
//   [] = none), defaultSize, defaultPosition (index in the Garage16 standard layout,
//   or null when the window is not part of it), renderer, mobileSupport.

import { t } from "../i18n.js";
import { defaultLayout } from "./layout.js";
import { renderChart, renderCorners, renderCursorTable, renderDelta, renderPhysics, renderSummary, renderTrackmap } from "./widgets.js";

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
].map((m) => ({ ...m, defaultSize: sizeOf(m.type, "large"), defaultPosition: positionOf(m.type) }));

export const moduleForType = (type) => MODULES.find((m) => m.type === type) || null;
