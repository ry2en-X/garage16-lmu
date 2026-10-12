// pages/admin.js — Admin UI (V0.7.2 §15). Separate credential domain
// from driver auth: gated by the shared operator admin token (see
// admin_store.js and server/routers/admin.py), never a driver session.
// Deliberately small — "kein riesiges CMS": three tabs (Reports, Drivers,
// System), each a thin view over existing/newly-added admin endpoints.

import { api, ApiError } from "../api.js";
import { adminStore } from "../admin_store.js";
import { el, formatLapTime, formatDate } from "../format.js";

import { t, tp } from "../i18n.js";
import { allFindings, findingText, ruleTitle, tierText } from "../findings.js";
import { renderRulesTab } from "./admin_rules.js";
export async function renderAdmin(container) {
  let token = adminStore.getToken();

  container.appendChild(el("h1", {}, t("Admin")));

  if (!token) {
    renderTokenGate(container);
    return;
  }

  // Verify the token actually works before building the rest of the UI —
  // a stale/wrong token should fail fast and clearly, not surface as a
  // confusing 403 buried inside the first tab's data load.
  try {
    await api.adminListReports(token, true);
  } catch (err) {
    adminStore.clear();
    container.appendChild(
      el("div", { class: "notice notice--error" }, err instanceof ApiError ? err.message : t("Could not verify the admin token."))
    );
    renderTokenGate(container);
    return;
  }

  container.appendChild(el("p", { class: "subtitle" }, t("Reports, track-limits review, driver moderation, car models, race rules and system status.")));

  const signOutBtn = el("button", { class: "btn btn--ghost", style: "float:right;" }, t("Forget admin token"));
  signOutBtn.addEventListener("click", () => {
    adminStore.clear();
    window.location.hash = "#/admin";
    window.location.reload();
  });
  container.appendChild(signOutBtn);

  // V0.9.3: "Review" (laps the track-limits check left to a human) and "Cars" (what the
  // server learned about car models)
  // V0.9.4: "Race rules" — rule sets for the Stint Analyzer's strategy calculator
  const tabs = ["Reports", "Review", "Drivers", "Cars", "Rules", "System"];
  const tabLabels = { Reports: t("Reports"), Review: t("Track-limits review"), Drivers: t("Drivers"), Cars: t("Car models"), Rules: t("Race rules"), System: t("System") };
  let active = "Reports";
  const tabBar = el("div", { class: "inline-form" }, tabs.map((name) => el("button", { class: "btn btn--ghost", "data-tab": name }, tabLabels[name])));
  const body = el("div", { class: "section" });
  container.appendChild(tabBar);
  container.appendChild(body);

  async function showTab(name) {
    active = name;
    tabBar.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    body.innerHTML = "";
    if (name === "Reports") body.appendChild(await renderReportsTab(token));
    else if (name === "Review") body.appendChild(await renderReviewTab(token));
    else if (name === "Drivers") body.appendChild(await renderDriversTab(token));
    else if (name === "Cars") body.appendChild(await renderCarsTab(token));
    else if (name === "Rules") body.appendChild(await renderRulesTab(token));
    else body.appendChild(await renderSystemTab());
  }

  tabBar.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  await showTab(active);
}

function renderTokenGate(container) {
  const card = el("div", { class: "card" });
  card.appendChild(el("h2", {}, t("Enter admin token")));
  card.appendChild(
    el("p", { class: "subtitle" }, t("The shared operator token (LMU_GARAGE_ADMIN_TOKEN) — kept only for this browser tab, not saved permanently."))
  );
  const field = el("div", { class: "field" }, [
    el("label", { for: "adminToken" }, t("Admin token")),
    el("input", { id: "adminToken", type: "password" }),
  ]);
  const btn = el("button", { class: "btn" }, t("Continue"));
  const out = el("div");
  btn.addEventListener("click", () => {
    const value = field.querySelector("input").value.trim();
    if (!value) return;
    adminStore.setToken(value);
    window.location.hash = "#/admin";
    window.location.reload();
  });
  card.appendChild(field);
  card.appendChild(btn);
  card.appendChild(out);
  container.appendChild(card);
}

async function renderReportsTab(token) {
  const wrap = el("div");
  const list = el("div");
  wrap.appendChild(list);

  async function refresh() {
    list.innerHTML = "";
    let reports;
    try {
      reports = await api.adminListReports(token, true);
    } catch (err) {
      list.appendChild(el("div", { class: "notice notice--error" }, err.message));
      return;
    }
    if (!reports.length) {
      list.appendChild(el("div", { class: "empty-state" }, t("No unresolved reports.")));
      return;
    }
    reports.forEach((r) => {
      const card = el("div", { class: "card" });
      card.appendChild(
        el("div", { class: "card-row" }, [
          el("div", {}, [
            el("strong", {}, `${r.lap.track_name} — ${r.lap.car_name}`),
            el("div", { class: "subtitle", style: "margin:2px 0 0;" }, t("Driver: {0} — {1} — {2}", [r.lap.driver_name, formatLapTime(r.lap.lap_time), r.lap.is_valid ? t("currently valid") : t("already invalid")])),
          ]),
          el("span", { class: "tag" }, formatDate(r.created_at)),
        ])
      );
      card.appendChild(el("div", { class: "subtitle" }, t("Reported by {0}: \"{1}\"", [r.reported_by_display_name, r.reason])));

      const actions = el("div", { class: "inline-form", style: "margin-top:8px;" });
      const invalidateBtn = el("button", { class: "btn" }, t("Invalidate lap"));
      const dismissBtn = el("button", { class: "btn btn--ghost" }, t("Dismiss report"));
      const actionOut = el("div");

      invalidateBtn.addEventListener("click", async () => {
        const reason = window.prompt(t("Reason for invalidating this lap:"), r.reason);
        if (!reason) return;
        invalidateBtn.disabled = true;
        try {
          await api.adminInvalidateLap(token, r.lap.id, reason);
          await api.adminResolveReport(token, r.id, "invalidated");
          await refresh();
        } catch (err) {
          actionOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
          invalidateBtn.disabled = false;
        }
      });
      dismissBtn.addEventListener("click", async () => {
        dismissBtn.disabled = true;
        try {
          await api.adminResolveReport(token, r.id, "dismissed");
          await refresh();
        } catch (err) {
          actionOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
          dismissBtn.disabled = false;
        }
      });
      actions.appendChild(invalidateBtn);
      actions.appendChild(dismissBtn);
      card.appendChild(actions);
      card.appendChild(actionOut);
      list.appendChild(card);
    });
  }

  await refresh();
  return wrap;
}

async function renderDriversTab(token) {
  const wrap = el("div");
  const searchField = el("div", { class: "field" }, [
    el("label", { for: "adminDriverSearch" }, t("Search by display name")),
    el("input", { id: "adminDriverSearch", type: "text", placeholder: t("leave blank to list everyone") }),
  ]);
  const searchBtn = el("button", { class: "btn" }, t("Search"));
  wrap.appendChild(el("div", { class: "inline-form" }, [searchField, searchBtn]));

  const list = el("div", { style: "margin-top:16px;" });
  wrap.appendChild(list);

  async function refresh() {
    list.innerHTML = "";
    let drivers;
    try {
      drivers = await api.adminListDrivers(token, searchField.querySelector("input").value.trim() || undefined);
    } catch (err) {
      list.appendChild(el("div", { class: "notice notice--error" }, err.message));
      return;
    }
    if (!drivers.length) {
      list.appendChild(el("div", { class: "empty-state" }, t("No matching drivers.")));
      return;
    }
    const card = el("div", { class: "card" });
    drivers.forEach((d) => {
      const actionBtn = el("button", { class: "btn btn--ghost" }, d.is_locked ? t("Unlock") : t("Lock"));
      actionBtn.addEventListener("click", async () => {
        actionBtn.disabled = true;
        try {
          if (d.is_locked) {
            await api.adminUnlockDriver(token, d.driver_id);
          } else {
            const reason = window.prompt(t("Reason for locking {0}:", [d.display_name]));
            if (!reason) {
              actionBtn.disabled = false;
              return;
            }
            await api.adminLockDriver(token, d.driver_id, reason);
          }
          await refresh();
        } catch (err) {
          list.appendChild(el("div", { class: "notice notice--error" }, err.message));
          actionBtn.disabled = false;
        }
      });
      card.appendChild(
        el("div", { class: "card-row" }, [
          el("div", {}, [
            el("a", { href: `#/driver/${d.driver_id}` }, [el("strong", {}, d.display_name)]),
            el("div", { class: "subtitle", style: "margin:2px 0 0;" }, `${tp("{0} lap", "{0} laps", d.total_laps)}${d.email ? ` — ${d.email}` : ""}${d.locked_reason ? ` — ${t("locked: {0}", [d.locked_reason])}` : ""}`),
          ]),
          el("span", { class: `tag ${d.is_locked ? "tag--invalid" : "tag--valid"}` }, d.is_locked ? t("locked") : t("active")),
          actionBtn,
        ])
      );
    });
    list.appendChild(card);
  }

  searchBtn.addEventListener("click", refresh);
  await refresh();
  return wrap;
}

async function renderSystemTab() {
  const card = el("div", { class: "card" });
  try {
    const health = await api.health();
    card.appendChild(el("div", { class: "card-row" }, [el("span", {}, t("Status")), el("span", { class: `tag ${health.status === "ok" ? "tag--valid" : "tag--invalid"}` }, health.status)]));
    card.appendChild(el("div", { class: "card-row" }, [el("span", {}, t("Version")), el("span", {}, health.version)]));
    card.appendChild(el("div", { class: "card-row" }, [el("span", {}, t("Min client version")), el("span", {}, health.min_client_version)]));
    card.appendChild(el("div", { class: "card-row" }, [el("span", {}, t("Database")), el("span", { class: `tag ${health.db_ok ? "tag--valid" : "tag--invalid"}` }, health.db_ok ? t("ok") : t("degraded"))]));
    if (health.db_error) {
      card.appendChild(el("div", { class: "notice notice--error" }, health.db_error));
    }
  } catch (err) {
    card.appendChild(el("div", { class: "notice notice--error" }, err.message));
  }
  return card;
}


// ---------------------------------------------------------------- V0.9.3: track-limits review

function findingLine(f) {
  const where = f.at !== undefined && f.at !== null ? ` @ ${Math.round(f.at)} m` : "";
  const tier = tierText(f.tier);
  return `${findingText(f)}${where} — ${tier ? `${tier}, ` : ""}${f.source}`;
}

// V0.9.3.1: which rule may invalidate, which only asks for a review (loaded on opening)
function rulesBlock(token) {
  const body = el("div", {}, el("div", { class: "notice" }, t("Loading …")));
  const details = el("details", { class: "card", "data-rules": "" }, [el("summary", {}, t("Track-limits rules on this server")), body]);
  let loaded = false;
  details.addEventListener("toggle", async () => {
    if (!details.open || loaded) return;
    loaded = true;
    try {
      const data = await api.adminTrackLimitsRules(token);
      const effect = { invalid: t("invalidates"), review: t("review"), info: t("shown only") };
      body.innerHTML = "";
      body.appendChild(el("table", { class: "timing-table" }, [
        el("thead", {}, el("tr", {}, [el("th", {}, t("Rule")), el("th", {}, t("Kind")), el("th", {}, t("Effect")), el("th", {}, t("Meaning confirmed"))])),
        el("tbody", {}, data.rules.map((r) => el("tr", { "data-code": r.code, title: r.note }, [
          el("td", {}, ruleTitle(r.code)), el("td", {}, tierText(r.tier)),
          el("td", {}, effect[r.effect] || r.effect), el("td", {}, r.confirmed ? t("yes") : t("not yet")),
        ]))),
      ]));
      body.appendChild(el("div", { class: "subtitle" }, data.invalidating_heuristics.length
        ? t("Measurements allowed to invalidate on this server: {0}", [data.invalidating_heuristics.join(", ")])
        : t("No measurement invalidates on its own (LMU_GARAGE_TRACK_LIMITS_INVALIDATING is empty).")));
    } catch (err) {
      body.innerHTML = "";
      body.appendChild(el("div", { class: "notice notice--error" }, err.message));
    }
  });
  return details;
}

async function renderReviewTab(token) {
  const wrap = el("div");
  wrap.appendChild(el("p", { class: "subtitle", style: "margin-top:16px;" }, t("Laps the automatic track-limits check could not decide on its own. They count until you decide. Every finding says what was seen, where on the lap and from which signal.")));
  wrap.appendChild(rulesBlock(token));
  const list = el("div");
  wrap.appendChild(list);

  async function refresh() {
    list.innerHTML = "";
    let laps;
    try {
      laps = await api.adminReviewQueue(token, "pending");
    } catch (err) {
      list.appendChild(el("div", { class: "notice notice--error" }, err.message));
      return;
    }
    if (!laps.length) {
      list.appendChild(el("div", { class: "empty-state" }, t("Nothing to review.")));
      return;
    }
    laps.forEach((lap) => {
      const findings = allFindings(lap.validation).filter((f) => f.severity !== "info");
      const out = el("div");
      const decide = async (decision) => {
        const note = window.prompt(decision === "invalidate" ? t("Reason for invalidating this lap:") : t("Note (optional):"), "");
        if (note === null) return;
        try {
          await api.adminReviewLap(token, lap.id, decision, note || undefined);
          await refresh();
        } catch (err) {
          out.appendChild(el("div", { class: "notice notice--error" }, err.message));
        }
      };
      list.appendChild(el("div", { class: "card", "data-review-lap": String(lap.id) }, [
        el("div", { class: "card-row" }, [
          el("div", {}, [
            el("strong", {}, `${lap.track_name} — ${lap.car_model || lap.car_name}`),
            el("div", { class: "subtitle", style: "margin:2px 0 0;" }, t("Driver: {0} — {1} — {2}", [lap.driver_name, formatLapTime(lap.lap_time), lap.is_valid ? t("currently valid") : t("already invalid")])),
          ]),
          el("span", { class: "tag" }, formatDate(lap.uploaded_at)),
        ]),
        el("ul", { class: "review__findings" }, findings.map((f) => el("li", { "data-code": f.code }, findingLine(f)))),
        el("div", { class: "inline-form", style: "margin-top:8px;" }, [
          el("a", { class: "btn btn--ghost", href: `#/analysis?a=${lap.id}` }, t("Analyse")),
          el("button", { class: "btn", onclick: () => decide("invalidate") }, t("Invalidate lap")),
          el("button", { class: "btn btn--ghost", onclick: () => decide("clear") }, t("Lap counts")),
        ]),
        out,
      ]));
    });
  }

  await refresh();
  return wrap;
}

// ---------------------------------------------------------------- V0.9.3: car models

async function renderCarsTab(token) {
  const wrap = el("div");
  wrap.appendChild(el("p", { class: "subtitle", style: "margin-top:16px;" }, t("Every livery, car number and season of a model counts as the same car. The server learns which LMU file names belong to which model from the game itself (every car of a session, reported by the 0.9.3 client). Nothing to enter by hand.")));
  let data;
  try {
    data = await api.adminCarIdentity(token);
  } catch (err) {
    wrap.appendChild(el("div", { class: "notice notice--error" }, err.message));
    return wrap;
  }
  const sourceText = { vehicle_model: t("LMU model name"), alias: t("learned alias"), veh_file: t("file name only"), null: t("unknown (very old client)") };
  wrap.appendChild(el("div", { class: "card" }, Object.entries(data.laps_by_source || {}).map(([source, count]) => el("div", { class: "card-row" }, [
    el("span", {}, sourceText[source] || source), el("span", {}, tp("{0} lap", "{0} laps", count)),
  ]))));
  // V0.9.3.1: identities real data must decide on — nothing is changed automatically
  const suspectText = {
    cut_name: t("LMU cut the model name (30-byte limit); the full name is not known yet"),
    several_models: t("laps from LMU files of different models — two variants with the same name?"),
    several_classes: t("laps in different classes"),
  };
  if ((data.suspects || []).length) {
    wrap.appendChild(el("div", { class: "card", "data-suspects": "" }, [
      el("h3", {}, t("To check with real data")),
      el("ul", { class: "review__findings" }, data.suspects.map((item) => el("li", { "data-identity": item.identity }, [
        el("strong", {}, item.identity), ` — ${item.reasons.map((r) => suspectText[r] || r).join("; ")} `,
        el("span", { class: "subtitle" }, `(${tp("{0} lap", "{0} laps", item.laps)}; ${item.veh_models.join(", ") || "—"}; ${item.classes.join(", ") || "—"})`),
      ]))),
      el("div", { class: "subtitle" }, t("Check with the car list of a full grid (py -m scripts.car_identity_probe on the PC) before relying on these groups.")),
    ]));
  }
  if (!data.aliases.length) {
    wrap.appendChild(el("div", { class: "empty-state" }, t("Nothing learned yet — it starts with the first lap uploaded by a 0.9.3 client.")));
    return wrap;
  }
  const aliasState = (a) => {
    if (a.conflict) return t("conflict with {0} — not used", [a.conflict_with]);
    if (a.used === false) return t("seen {0}× in grid lists only — used from 2×", [a.seen]);
    return t("seen {0}×", [a.seen]);
  };
  const rows = data.aliases.map((a) => el("tr", { class: a.conflict ? "alias--conflict" : "" }, [
    el("td", {}, a.key), el("td", {}, a.model), el("td", {}, aliasState(a)),
  ]));
  wrap.appendChild(el("div", { class: "card corners__wrap" }, el("table", { class: "timing-table" }, [
    el("thead", {}, el("tr", {}, [el("th", {}, t("LMU file name group")), el("th", {}, t("Model")), el("th", {}, t("Status"))])),
    el("tbody", {}, rows),
  ])));
  return wrap;
}
