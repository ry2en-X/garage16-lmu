// pages/admin.js — Admin UI (V0.7.2 §15). Separate credential domain
// from driver auth: gated by the shared operator admin token (see
// admin_store.js and server/routers/admin.py), never a driver session.
// Deliberately small — "kein riesiges CMS": three tabs (Reports, Drivers,
// System), each a thin view over existing/newly-added admin endpoints.

import { api, ApiError } from "../api.js";
import { adminStore } from "../admin_store.js";
import { el, formatLapTime, formatDate } from "../format.js";

export async function renderAdmin(container) {
  let token = adminStore.getToken();

  container.appendChild(el("h1", {}, "Admin"));

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
      el("div", { class: "notice notice--error" }, err instanceof ApiError ? err.message : "Could not verify the admin token.")
    );
    renderTokenGate(container);
    return;
  }

  container.appendChild(el("p", { class: "subtitle" }, "Reports, driver moderation, and system status."));

  const signOutBtn = el("button", { class: "btn btn--ghost", style: "float:right;" }, "Forget admin token");
  signOutBtn.addEventListener("click", () => {
    adminStore.clear();
    window.location.hash = "#/admin";
    window.location.reload();
  });
  container.appendChild(signOutBtn);

  const tabs = ["Reports", "Drivers", "System"];
  let active = "Reports";
  const tabBar = el("div", { class: "inline-form" }, tabs.map((t) => el("button", { class: "btn btn--ghost", "data-tab": t }, t)));
  const body = el("div", { class: "section" });
  container.appendChild(tabBar);
  container.appendChild(body);

  async function showTab(name) {
    active = name;
    tabBar.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    body.innerHTML = "";
    if (name === "Reports") body.appendChild(await renderReportsTab(token));
    else if (name === "Drivers") body.appendChild(await renderDriversTab(token));
    else body.appendChild(await renderSystemTab());
  }

  tabBar.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  await showTab(active);
}

function renderTokenGate(container) {
  const card = el("div", { class: "card" });
  card.appendChild(el("h2", {}, "Enter admin token"));
  card.appendChild(
    el("p", { class: "subtitle" }, "The shared operator token (LMU_GARAGE_ADMIN_TOKEN) — kept only for this browser tab, not saved permanently.")
  );
  const field = el("div", { class: "field" }, [
    el("label", { for: "adminToken" }, "Admin token"),
    el("input", { id: "adminToken", type: "password" }),
  ]);
  const btn = el("button", { class: "btn" }, "Continue");
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
      list.appendChild(el("div", { class: "empty-state" }, "No unresolved reports."));
      return;
    }
    reports.forEach((r) => {
      const card = el("div", { class: "card" });
      card.appendChild(
        el("div", { class: "card-row" }, [
          el("div", {}, [
            el("strong", {}, `${r.lap.track_name} — ${r.lap.car_name}`),
            el("div", { class: "subtitle", style: "margin:2px 0 0;" }, `Driver: ${r.lap.driver_name} — ${formatLapTime(r.lap.lap_time)} — ${r.lap.is_valid ? "currently valid" : "already invalid"}`),
          ]),
          el("span", { class: "tag" }, formatDate(r.created_at)),
        ])
      );
      card.appendChild(el("div", { class: "subtitle" }, `Reported by ${r.reported_by_display_name}: "${r.reason}"`));

      const actions = el("div", { class: "inline-form", style: "margin-top:8px;" });
      const invalidateBtn = el("button", { class: "btn" }, "Invalidate lap");
      const dismissBtn = el("button", { class: "btn btn--ghost" }, "Dismiss report");
      const actionOut = el("div");

      invalidateBtn.addEventListener("click", async () => {
        const reason = window.prompt("Reason for invalidating this lap:", r.reason);
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
    el("label", { for: "adminDriverSearch" }, "Search by display name"),
    el("input", { id: "adminDriverSearch", type: "text", placeholder: "leave blank to list everyone" }),
  ]);
  const searchBtn = el("button", { class: "btn" }, "Search");
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
      list.appendChild(el("div", { class: "empty-state" }, "No matching drivers."));
      return;
    }
    const card = el("div", { class: "card" });
    drivers.forEach((d) => {
      const actionBtn = el("button", { class: "btn btn--ghost" }, d.is_locked ? "Unlock" : "Lock");
      actionBtn.addEventListener("click", async () => {
        actionBtn.disabled = true;
        try {
          if (d.is_locked) {
            await api.adminUnlockDriver(token, d.driver_id);
          } else {
            const reason = window.prompt(`Reason for locking ${d.display_name}:`);
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
            el("div", { class: "subtitle", style: "margin:2px 0 0;" }, `${d.total_laps} laps${d.email ? ` — ${d.email}` : ""}${d.locked_reason ? ` — locked: ${d.locked_reason}` : ""}`),
          ]),
          el("span", { class: `tag ${d.is_locked ? "tag--invalid" : "tag--valid"}` }, d.is_locked ? "locked" : "active"),
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
    card.appendChild(el("div", { class: "card-row" }, [el("span", {}, "Status"), el("span", { class: `tag ${health.status === "ok" ? "tag--valid" : "tag--invalid"}` }, health.status)]));
    card.appendChild(el("div", { class: "card-row" }, [el("span", {}, "Version"), el("span", {}, health.version)]));
    card.appendChild(el("div", { class: "card-row" }, [el("span", {}, "Min client version"), el("span", {}, health.min_client_version)]));
    card.appendChild(el("div", { class: "card-row" }, [el("span", {}, "Database"), el("span", { class: `tag ${health.db_ok ? "tag--valid" : "tag--invalid"}` }, health.db_ok ? "ok" : "degraded")]));
    if (health.db_error) {
      card.appendChild(el("div", { class: "notice notice--error" }, health.db_error));
    }
  } catch (err) {
    card.appendChild(el("div", { class: "notice notice--error" }, err.message));
  }
  return card;
}
