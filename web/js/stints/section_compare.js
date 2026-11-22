// stints/section_compare.js — stints compared, the sector heat map, team mates, all laps.
// (V1.0.3: split out of pages/stints.js.)

import { api } from "../core/api.js";
import { el, formatLapTime } from "../core/format.js";
import { t } from "../core/i18n.js";
import { link } from "../core/link.js";
import * as S from "./stats.js";
import { FLAG_LABELS, card, chart, dash, f, fin, lapTime, legend, note, signed, table } from "./ui.js";

export function compareSection({ data, clean }) {
  if (data.stints.length < 2) return null;
  const pick = (idx) => {
    const sel = el("select", { class: "input" }, data.stints.map((s) => el("option", { value: s.number, ...(s.number === idx ? { selected: "selected" } : {}) }, t("Stint {0}", [s.number]))));
    return sel;
  };
  const a = pick(1);
  const b = pick(data.stints.length);
  const out = el("div");
  const draw = () => {
    out.innerHTML = "";
    const na = Number(a.value);
    const nb = Number(b.value);
    const series = [na, nb].map((n, k) => ({
      kind: "line", color: k ? "#4C9BE8" : "#E0472B", label: t("Stint {0}", [n]), format: (v) => formatLapTime(v),
      points: data.laps.filter((l) => l.stint === n).map((l, i) => [i + 1, clean.has(l.id) ? l.lap_time : null]),
    }));
    // the difference lap by lap (B − A), where both have a clean lap
    const diff = series[0].points.map(([x, ya]) => { const q = series[1].points.find((p) => p[0] === x); return [x, fin(ya) && q && fin(q[1]) ? q[1] - ya : null]; });
    series.push({ kind: "bars", color: "#8B9199", label: t("Stint {0} − Stint {1}", [nb, na]), format: (v) => signed(v, 3), points: diff, legend: false });
    out.appendChild(chart(series.slice(0, 2), { legend: true, integerX: true, yFormat: (v) => formatLapTime(v), xLabel: t("lap in stint"), yLabel: t("lap time"), title: t("Stints compared"), tooltipTitle: (x) => t("Lap {0} of the stint", [x]) }));
    if (diff.some((p) => fin(p[1]))) {
      out.appendChild(chart([series[2]], { integerX: true, height: 130, yZero: true, yFormat: (v) => signed(v, 1, ""), xLabel: t("lap in stint"), yLabel: t("Stint {0} − Stint {1}", [nb, na]), title: t("Difference per lap"), tooltipTitle: (x) => t("Lap {0} of the stint", [x]) }));
    }
    const rowFor = (label, fn) => ({ cells: [label, ...[na, nb].map((n) => fn(n))] });
    const of = (n) => data.laps.filter((l) => l.stint === n && clean.has(l.id));
    const stint = (n) => data.stints.find((s) => s.number === n);
    out.appendChild(table(["", t("Stint {0}", [na]), t("Stint {0}", [nb])], [
      rowFor(t("Compound"), (n) => stint(n).compound || dash),
      rowFor(t("Clean laps"), (n) => String(of(n).length)),
      rowFor(t("Best"), (n) => lapTime(of(n).length ? Math.min(...of(n).map((l) => l.lap_time)) : null)),
      rowFor(t("Median"), (n) => lapTime(S.median(of(n).map((l) => l.lap_time)))),
      rowFor(t("Spread"), (n) => f(S.stdev(of(n).map((l) => l.lap_time)), 3, "s")),
      rowFor(t("Change / lap"), (n) => { const d = S.degradation(data.laps, clean, n); return d.ok ? signed(d.slope, 3) : dash; }),
      rowFor(t("Fuel / lap"), (n) => f(S.median(of(n).map((l) => l.fuel_used_l)), 2, "L")),
      rowFor(t("Tyre wear / lap"), (n) => f(S.median(of(n).map((l) => l.wear_used_avg)), 3, "%")),
      rowFor(t("Tyre temperature (avg)"), (n) => f(S.median(of(n).map((l) => l.tyre_temp_avg)), 1, "°C")),
    ]));
  };
  a.addEventListener("change", draw);
  b.addEventListener("change", draw);
  draw();
  return card(t("Compare stints"), [el("div", { class: "stint-toolbar" }, [a, " ", t("vs"), " ", b]), out], { id: "stint-compare" });
}

// Sector times of every lap as a heat map (V1.0.3): green = close to the best sector of the
// session's clean laps, red = far off. Shows at a glance which sector costs the time, and when.
export function sectorsSection({ data, clean }) {
  const laps = data.laps.filter((l) => l.sector_times);
  if (laps.length < 2) return null;
  const cleanLaps = laps.filter((l) => clean.has(l.id));
  if (!cleanLaps.length) return null;
  const best = [0, 1, 2].map((k) => Math.min(...cleanLaps.map((l) => l.sector_times[k])));
  const medianLoss = [0, 1, 2].map((k) => S.median(cleanLaps.map((l) => l.sector_times[k] - best[k])));
  // the colour scale: 0 = best, 1 = twice the typical (median) loss of that sector or more
  const shade = (k, v) => {
    const loss = v - best[k];
    const scale = Math.max(medianLoss[k] * 2, 0.05);
    const share = Math.min(Math.max(loss / scale, 0), 1);
    const hue = 130 - 130 * share;   // green → yellow → red
    return `hsla(${hue.toFixed(0)}, 60%, 45%, ${(0.18 + 0.5 * share).toFixed(2)})`;
  };
  const rows = laps.map((l) => ({ cls: clean.has(l.id) ? null : "stint-row--filtered", cells: [
    el("a", { href: link.analysis({ a: l.id }) }, String(l.lap_number)),
    String(l.stint || dash),
    ...l.sector_times.map((v, k) => el("span", { class: "heat", style: `background:${shade(k, v)}`, title: `${t("best")} ${f(best[k], 3)} · ${signed(v - best[k], 3)}` }, [f(v, 3), el("small", {}, ` ${signed(v - best[k], 2, "")}`)])),
    lapTime(l.lap_time),
  ] }));
  const where = [0, 1, 2].map((k) => [k, medianLoss[k]]).filter(([, v]) => fin(v)).sort((a, b) => b[1] - a[1])[0];
  return card(t("Sectors"), [
    where ? el("p", {}, t("The most time to find: sector {0} — the clean laps lose {1} s there on the median against your best sector.", [String(where[0] + 1), where[1].toFixed(3)])) : null,
    el("div", { class: "table-scroll" }, table([t("Lap"), t("Stint"), "S1", "S2", "S3", t("Time")], rows, "stint-heat")),
    note(t("Colour: green = at your best sector of the session (clean laps), red = twice the usual loss or more.")),
  ], { id: "stint-sectors" });
}

export function driversSection({ data }) {
  const box = el("div", {}, note(t("Loading …")));
  api.stintDrivers({ trackName: data.track_name, carModel: data.car_model }).then((res) => {
    box.innerHTML = "";
    if (!res.drivers.length) { box.appendChild(note(t("No valid laps on this track yet."))); return; }
    if (res.drivers.length < 2) box.appendChild(note(t("Join a team to compare yourself with your team mates here.")));
    box.appendChild(table([t("Driver"), t("Valid laps"), t("Best"), t("Median (clean)"), t("Spread"), t("Within 0.5 s of best"), t("Fuel / lap"), t("Energy / lap"), t("Tyre wear / lap")],
      res.drivers.map((d) => {
        const c = d.consistency || {};
        return { cls: d.own ? "stint-row--own" : null, cells: [d.own ? t("{0} (you)", [d.name]) : d.name, String(c.laps || 0), lapTime(c.best), lapTime(c.median),
          fin(c.stdev) ? `± ${c.stdev.toFixed(3)} s` : dash, fin(c.within_half_second) ? `${c.within_half_second} / ${c.clean_laps}` : dash,
          f(d.fuel_per_lap_l, 2, "L"), f(d.energy_per_lap_pct, 2, "%"), f(d.wear_per_lap_pct, 3, "%")] };
      })));
    box.appendChild(note(t("All valid laps on this track{0}, every session together; laps slower than 107 % of a driver's median are not in the median and spread.", [data.car_model ? ` ${t("with this car")}` : ""])));
  }).catch((err) => { box.innerHTML = ""; box.appendChild(el("div", { class: "notice notice--error" }, err.message)); });
  return card(t("You and your team mates"), box, { id: "stint-drivers" });
}

// V1.0.3: every lap of the session as a CSV file (for a spreadsheet). Plain numbers with a dot as
// the decimal separator and seconds for times, so a spreadsheet reads them as numbers; the
// separator is a semicolon, which German Excel expects (and LibreOffice/Sheets ask for).
export function lapsCsv(data, clean) {
  const labels = FLAG_LABELS();
  const num = (v, d) => (fin(v) ? v.toFixed(d) : "");
  const quote = (v) => (/[";\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  const head = ["lap", "stint", "tyre_set", "tyre_age", "lap_time_s", "s1_s", "s2_s", "s3_s", "clean", "flags", "fuel_start_l", "fuel_used_l",
    "fuel_end_l", "energy_used_pct", "tread_left_avg_pct", "tyre_temp_avg_c", "tyre_pressure_avg_kpa", "track_temp_c", "position", "compound"];
  const rows = data.laps.map((l) => [
    String(l.lap_number ?? ""), String(l.stint ?? ""), String(l.tyre_set ?? ""), String(l.tyre_age ?? ""), num(l.lap_time, 3),
    ...(l.sector_times || [null, null, null]).map((v) => num(v, 3)), clean.has(l.id) ? "1" : "0", l.flags.map((x) => labels[x] || x).join(", "),
    num(l.fuel_start_l, 2), num(l.fuel_used_l, 3), num(l.fuel_end_l, 2), num(l.energy_used_pct, 2), num(l.wear_left_avg, 1),
    num(l.tyre_temp_avg, 1), num(l.tyre_pressure_avg, 1), num(l.track_temp, 1), l.place ? String(l.place) : "", l.compound || "",
  ]);
  return [head, ...rows].map((r) => r.map(quote).join(";")).join("\r\n") + "\r\n";
}

function csvButton(data, clean) {
  const button = el("button", { type: "button", class: "btn btn--ghost btn--sm" }, t("Download as CSV"));
  button.addEventListener("click", () => {
    // the byte order mark tells Excel the file is UTF-8 (track names, "Medium → Hard")
    const blob = new Blob(["\ufeff", lapsCsv(data, clean)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const day = data.started ? new Date(data.started * 1000).toISOString().slice(0, 10) : "session";
    const name = `garage16-${String(data.track_name || "track").toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${day}.csv`;
    const a = el("a", { href: url, download: name });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  return button;
}

export function lapsSection({ data, clean }) {
  const labels = FLAG_LABELS();
  return card(t("All laps"), [el("div", { class: "stint-toolbar" }, csvButton(data, clean)), table([t("Lap"), t("Stint"), t("Time"), "S1", "S2", "S3", t("Tyre age"), t("Fuel used"), t("Fuel left"), t("Tread left"), t("Tyre temp."), t("Position"), t("Notes")],
    data.laps.map((l) => ({ cls: clean.has(l.id) ? null : "stint-row--filtered", cells: [
      el("a", { href: link.analysis({ a: l.id }), title: t("Open in the LapTime Analyzer") }, String(l.lap_number)),
      String(l.stint || dash), lapTime(l.lap_time),
      ...(l.sector_times || [null, null, null]).map((v) => f(v, 3)),
      String(l.tyre_age || dash), f(l.fuel_used_l, 2), f(l.fuel_end_l, 1), f(l.wear_left_avg, 1), f(l.tyre_temp_avg, 0),
      l.place ? String(l.place) : dash,
      el("span", { title: (l.invalid_reason || []).join(" · ") || null }, [
        l.flags.map((x) => labels[x] || x).join(", "),
        l.invalid_reason && l.invalid_reason.length ? el("div", { class: "stint-reason" }, l.invalid_reason.join(" · ")) : null,
      ]),
    ] }))), note(t("Greyed: not counted as clean with the current filter. Under an invalid lap: why the server did not count it."))], { id: "stint-laps" });
}
