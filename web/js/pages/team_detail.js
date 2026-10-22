// pages/team_detail.js — the Team Dashboard (V0.6.8): overview, member
// list, team-scoped leaderboard, team records, and an activity console.
// Reuses the same api.js/el() patterns as the rest of the app.

import { api } from "../api.js";
import { store } from "../state.js";
import { renderActivityList } from "../activity.js";
import { el, formatLapTime, formatDate, inviteStatusLabel, roleLabel } from "../format.js";
import { renderSignInPrompt } from "./auth.js";

import { t, tp } from "../i18n.js";
export async function renderTeamDetail(container, teamId) {
  if (!store.isSignedIn()) {
    renderSignInPrompt(container, t("Sign in to view this team."));
    return;
  }

  const myDriverId = store.getSession()?.driverId;
  let detail, members;
  try {
    [detail, members] = await Promise.all([api.teamDetail(teamId), api.teamMembers(teamId)]);
  } catch (err) {
    container.appendChild(el("div", { class: "notice notice--error" }, err.message));
    return;
  }

  const canManage = detail.my_role === "owner" || detail.my_role === "admin";
  const isOwner = detail.my_role === "owner";

  container.appendChild(el("a", { href: "#/teams", class: "subtitle" }, t("← All teams")));
  container.appendChild(el("h1", {}, detail.name));
  if (detail.description) container.appendChild(el("p", { class: "subtitle" }, detail.description));

  // V0.9.6 — one order for everyone: what the team achieved first (overview, bests, leaderboard,
  // activity), the people next, the settings last (folded away). The sections load in PARALLEL
  // into their places (before: one after the other).
  const section = (title, extra = null) => {
    if (title) container.appendChild(el("h2", {}, title));
    const slot = el("div", { class: "team-slot" }, el("div", { class: "subtitle" }, t("Loading…")));
    container.appendChild(slot);
    if (extra) container.appendChild(extra);
    return slot;
  };
  const fill = (slot, promise) => promise.then((node) => slot.replaceChildren(node)).catch((err) => slot.replaceChildren(el("div", { class: "notice notice--error" }, err.message)));

  const overviewSlot = section(null);
  const recordsSlot = section(t("Team bests"));
  const boardSlot = section(t("Team leaderboard"));
  const activitySlot = section(t("Recent activity"));

  // --- Members (+ invitations for owner/admin) ---
  container.appendChild(el("h2", {}, t("Members")));
  container.appendChild(renderMembersTable(teamId, members, detail.my_role, myDriverId, isOwner, () => refreshMembers()));
  const membersBox = { current: container.lastChild };
  async function refreshMembers() {
    const fresh = await api.teamMembers(teamId);
    const rebuilt = renderMembersTable(teamId, fresh, detail.my_role, myDriverId, isOwner, refreshMembers);
    membersBox.current.replaceWith(rebuilt);
    membersBox.current = rebuilt;
  }
  const invitesSlot = canManage ? section(t("Invitations")) : null;

  // --- Settings (owner/admin only), folded: rarely needed, and holds "Delete team"
  if (canManage) {
    const settings = el("details", { class: "fold fold--single" }, [el("summary", {}, t("Team settings")), renderSettingsCard(teamId, detail, isOwner)]);
    container.appendChild(settings);
  }

  await Promise.all([
    fill(overviewSlot, renderTeamStatsCard(teamId, detail)),
    fill(recordsSlot, renderTeamRecords(teamId)),
    fill(boardSlot, renderTeamLeaderboard(teamId)),
    fill(activitySlot, renderActivity(teamId)),
    invitesSlot ? fill(invitesSlot, renderInvitationsCard(teamId)) : null,
  ]);
}

function _stat(value, label) {
  return el("div", { style: "min-width:90px;" }, [
    el("div", { style: "font-size:1.6em; font-weight:700;" }, String(value)),
    el("div", { class: "subtitle" }, label),
  ]);
}

function renderSettingsCard(teamId, detail, isOwner) {
  const card = el("div", { class: "card" });
  card.appendChild(el("h2", {}, t("Team settings")));

  const nameField = el("div", { class: "field" }, [
    el("label", {}, t("Team name")),
    el("input", { type: "text", value: detail.name, id: "teamNameInput" }),
  ]);
  const descField = el("div", { class: "field" }, [
    el("label", {}, t("Description")),
    el("input", { type: "text", value: detail.description || "", id: "teamDescInput" }),
  ]);
  const announceField = el("div", { class: "field" }, [
    el("label", {}, [
      el("input", { type: "checkbox", id: "announceInput", ...(detail.discord_announcements_enabled ? { checked: "checked" } : {}) }),
      t(" Post team-best records to Discord"),
    ]),
  ]);
  const saveBtn = el("button", { class: "btn" }, t("Save"));
  const out = el("div");
  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    out.innerHTML = "";
    try {
      await api.updateTeam(teamId, {
        name: nameField.querySelector("input").value.trim(),
        description: descField.querySelector("input").value.trim(),
        discord_announcements_enabled: announceField.querySelector("input").checked,
      });
      out.appendChild(el("div", { class: "notice notice--success" }, t("Saved.")));
    } catch (err) {
      out.appendChild(el("div", { class: "notice notice--error" }, err.message));
    } finally {
      saveBtn.disabled = false;
    }
  });
  card.appendChild(nameField);
  card.appendChild(descField);
  card.appendChild(announceField);
  card.appendChild(saveBtn);
  card.appendChild(out);

  if (isOwner) {
    const dangerBtn = el("button", { class: "btn btn--ghost", style: "color:#c0392b; margin-top:12px;" }, t("Delete team"));
    dangerBtn.addEventListener("click", async () => {
      if (!confirm(t("Delete \"{0}\"? This can't be undone.", [detail.name]))) return;
      try {
        await api.deleteTeam(teamId);
        window.location.hash = "#/teams";
      } catch (err) {
        out.innerHTML = "";
        out.appendChild(el("div", { class: "notice notice--error" }, err.message));
      }
    });
    card.appendChild(dangerBtn);
  }

  return card;
}

function renderMembersTable(teamId, members, myRole, myDriverId, isOwner, onChanged) {
  const canManage = myRole === "owner" || myRole === "admin";
  const rows = members.map((m) => {
    const isMe = m.driver_id === myDriverId;
    const cells = [
      el("td", {}, [el("a", { href: `#/driver/${m.driver_id}` }, m.display_name), isMe ? t(" (you)") : ""]),
      el("td", {}, el("span", { class: "tag" }, roleLabel(m.role))),
      el("td", {}, el("span", { class: `tag ${m.is_active ? "tag--valid" : "tag--invalid"}` }, m.is_active ? t("active") : t("inactive"))),
      el("td", {}, String(m.lap_count)),
      el("td", {}, String(m.pb_count)),
      el("td", {}, formatDate(m.last_activity)),
    ];

    const actions = el("td", {});
    if (isMe && m.role !== "owner") {
      const leaveBtn = el("button", { class: "btn btn--ghost" }, t("Leave"));
      leaveBtn.addEventListener("click", async () => {
        await api.removeTeamMember(teamId, m.driver_id);
        onChanged();
      });
      actions.appendChild(leaveBtn);
    } else if (!isMe && canManage && m.role !== "owner" && !(myRole === "admin" && m.role === "admin")) {
      const removeBtn = el("button", { class: "btn btn--ghost" }, t("Remove"));
      removeBtn.addEventListener("click", async () => {
        if (!confirm(t("Remove {0} from the team?", [m.display_name]))) return;
        await api.removeTeamMember(teamId, m.driver_id);
        onChanged();
      });
      actions.appendChild(removeBtn);
      if (isOwner) {
        const roleBtn = el("button", { class: "btn btn--ghost" }, m.role === "admin" ? t("Demote") : t("Promote"));
        roleBtn.addEventListener("click", async () => {
          await api.changeTeamMemberRole(teamId, m.driver_id, m.role === "admin" ? "member" : "admin");
          onChanged();
        });
        actions.appendChild(roleBtn);
      }
    }
    cells.push(actions);
    return el("tr", {}, cells);
  });

  return el("table", { class: "timing-table" }, [
    el("thead", {}, el("tr", {}, [t("Driver"), t("Role"), t("Status"), t("Laps"), t("PBs"), t("Last activity"), ""].map((h) => el("th", {}, h)))),
    el("tbody", {}, rows),
  ]);
}

async function renderTeamLeaderboard(teamId) {
  const wrap = el("div", { class: "card" });
  // V0.8.9: searchable track list, grouped by venue with every layout that has
  // laps (only those can have a team board), instead of a flat list of raw names.
  const trackSearch = el("input", { type: "search", placeholder: t("Search a track, e.g. Fuji…"), autocomplete: "off" });
  const trackSelect = el("select", {}, [el("option", { value: "" }, t("Loading tracks…"))]);

  async function loadTracks(q) {
    let venues = [];
    try {
      venues = await api.catalogVenues(q);
    } catch {
      // fall through with an empty list
    }
    trackSelect.innerHTML = "";
    const withLaps = venues.filter((v) => v.layouts.length);
    trackSelect.appendChild(el("option", { value: "" }, withLaps.length ? t("Choose a track…") : q ? t("No driven track matches \"{0}\"", [q]) : t("No laps uploaded yet")));
    withLaps.forEach((v) => {
      const group = el("optgroup", { label: v.venue });
      v.layouts.forEach((l) => group.appendChild(el("option", { value: l.track_name }, l.track_name)));
      trackSelect.appendChild(group);
    });
    return withLaps;
  }
  let trackSearchTimer = null;
  trackSearch.addEventListener("input", () => {
    clearTimeout(trackSearchTimer);
    trackSearchTimer = setTimeout(() => loadTracks(trackSearch.value.trim()), 250);
  });
  const initial = await loadTracks("");
  const classSelect = el("select", {}, [el("option", { value: "" }, t("Pick a track first"))]);
  // V0.7.2 §9.5: third cascade level. "All cars in this class" keeps the
  // previous class-wide behavior as the default — picking a specific
  // model narrows further to that exact car (ignoring livery/number, see
  // server/models.py's car_model docstring).
  const carSelect = el("select", {}, [el("option", { value: "" }, t("Pick a class first"))]);
  const resultsBox = el("div");

  function resetCarSelect(placeholder) {
    carSelect.innerHTML = "";
    carSelect.appendChild(el("option", { value: "" }, placeholder));
  }

  async function runSearch() {
    const track = trackSelect.value;
    const cls = classSelect.value;
    const car = carSelect.value;
    resultsBox.innerHTML = "";
    if (!track || !cls) return;
    try {
      const entries = car
        ? await api.teamCarLeaderboard(teamId, track, car)
        : await api.teamClassLeaderboard(teamId, track, cls);
      resultsBox.appendChild(renderDeltaTable(entries));
    } catch (err) {
      resultsBox.appendChild(el("div", { class: "notice notice--error" }, err.message));
    }
  }

  trackSelect.addEventListener("change", async () => {
    const track = trackSelect.value;
    classSelect.innerHTML = "";
    resetCarSelect(t("Pick a class first"));
    resultsBox.innerHTML = "";
    if (!track) {
      classSelect.appendChild(el("option", { value: "" }, t("Pick a track first")));
      return;
    }
    // Every class is offered (V0.8.9), with its lap count at this track.
    const classes = await api.catalogClassOptions(track);
    classSelect.appendChild(el("option", { value: "" }, t("Choose…")));
    classes.forEach((c) =>
      classSelect.appendChild(el("option", { value: c.name }, `${c.name} · ${c.lap_count ? tp("{0} lap", "{0} laps", c.lap_count) : t("no laps yet")}`))
    );
    // V0.9.6: the class with the most laps right away
    const busiest = [...classes].filter((c) => c.lap_count > 0).sort((a, b) => b.lap_count - a.lap_count)[0];
    if (busiest) {
      classSelect.value = busiest.name;
      classSelect.dispatchEvent(new Event("change"));
    }
  });

  classSelect.addEventListener("change", async () => {
    const track = trackSelect.value;
    const cls = classSelect.value;
    resetCarSelect(t("All cars in this class"));
    if (!track || !cls) {
      resultsBox.innerHTML = "";
      return;
    }
    // Only models actually driven in THIS track+class combination — the
    // whole point of §9.5's "nur gültige Kombinationen anzeigen".
    const cars = await api.catalogCars(track, cls);
    cars.forEach((c) => carSelect.appendChild(el("option", { value: c }, c)));
    await runSearch();
  });

  carSelect.addEventListener("change", runSearch);

  wrap.appendChild(el("div", { class: "inline-form" }, [
    el("div", { class: "field" }, [el("label", {}, t("Find a track")), trackSearch]),
    el("div", { class: "field" }, [el("label", {}, t("Track")), trackSelect]),
    el("div", { class: "field" }, [el("label", {}, t("Class")), classSelect]),
    el("div", { class: "field" }, [el("label", {}, t("Car")), carSelect]),
  ]));
  wrap.appendChild(resultsBox);
  // V0.9.6: open on the busiest track instead of an empty form
  const busiestLayout = initial.flatMap((v) => v.layouts).sort((a, b) => b.lap_count - a.lap_count)[0];
  if (busiestLayout) {
    trackSelect.value = busiestLayout.track_name;
    trackSelect.dispatchEvent(new Event("change"));
  }
  return wrap;
}

const _STATS_WINDOWS = [
  ["today", t("Today")],
  ["7d", t("7 Days")],
  ["30d", t("30 Days")],
  ["all", t("All Time")],
];

// V0.9.6: the overview and the time-windowed statistics in ONE card (they were two, half the
// numbers twice): members and team bests held, then laps / PBs / active drivers / new team bests
// for the chosen period.
async function renderTeamStatsCard(teamId, detail) {
  const card = el("div", { class: "card team-overview" });
  card.appendChild(el("div", { class: "team-overview__fixed" }, [
    _stat(detail.member_count, t("Members")),
    _stat(detail.team_best_count, t("Team bests")),
    _stat(detail.pbs_this_week, t("PBs this week")),
  ]));

  const toggle = el(
    "div",
    { class: "inline-form" },
    _STATS_WINDOWS.map(([value, label]) => el("button", { class: "btn btn--ghost", "data-window": value }, label))
  );
  const statsBox = el("div", { style: "display:flex; gap:24px; flex-wrap:wrap; margin-top:12px;" });
  card.appendChild(toggle);
  card.appendChild(statsBox);

  async function loadWindow(window) {
    toggle.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.window === window));
    statsBox.innerHTML = "";
    try {
      const stats = await api.teamStats(teamId, window);
      statsBox.appendChild(_stat(stats.laps, t("Laps")));
      statsBox.appendChild(_stat(stats.pbs, t("PBs")));
      statsBox.appendChild(_stat(stats.active_drivers, t("Active drivers")));
      statsBox.appendChild(_stat(stats.records, t("New team bests")));
    } catch (err) {
      statsBox.appendChild(el("div", { class: "notice notice--error" }, err.message));
    }
  }

  toggle.querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => loadWindow(b.dataset.window))
  );

  await loadWindow("all");
  return card;
}

function renderDeltaTable(entries) {
  if (!entries.length) return el("div", { class: "empty-state" }, t("No valid laps from this team here yet."));
  const rows = entries.map((e, i) =>
    el("tr", {}, [
      el("td", {}, String(i + 1)),
      el("td", {}, el("a", { href: `#/driver/${e.driver_id}` }, e.driver_name)),
      el("td", {}, formatLapTime(e.lap_time)),
      el("td", {}, i === 0 ? "—" : `+${e.delta_to_leader.toFixed(3)}`),
    ])
  );
  return el("table", { class: "timing-table" }, [
    el("thead", {}, el("tr", {}, ["#", t("Driver"), t("Lap"), t("Gap")].map((h) => el("th", {}, h)))),
    el("tbody", {}, rows),
  ]);
}

async function renderTeamRecords(teamId) {
  try {
    const records = await api.teamRecords(teamId);
    if (!records.length) return el("div", { class: "empty-state" }, t("No team bests yet."));
    const rows = records.map((r) => {
      // §21 "You vs Team": null means no lap of your own on this combo
      // (never shown as a misleading "0.000" tie).
      let youCell;
      if (r.you_hold_it) {
        youCell = el("span", { class: "tag tag--valid" }, t("You hold this"));
      } else if (r.gap != null) {
        youCell = el("span", { class: "lap-time" }, `+${r.gap.toFixed(3)}`);
      } else {
        youCell = el("span", { class: "subtitle" }, t("no lap yet"));
      }
      return el("tr", {}, [
        el("td", {}, r.track_name),
        el("td", {}, r.car_class || "—"),
        el("td", {}, r.car_model),
        el("td", {}, formatLapTime(r.lap_time)),
        el("td", {}, r.driver_name),
        el("td", {}, youCell),
      ]);
    });
    return el("table", { class: "timing-table" }, [
      el("thead", {}, el("tr", {}, [t("Track"), t("Class"), t("Car"), t("Time"), t("Driver"), t("You")].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows),
    ]);
  } catch (err) {
    return el("div", { class: "notice notice--error" }, err.message);
  }
}

async function renderActivity(teamId) {
  try {
    const activity = await api.teamActivity(teamId);
    // V0.9.6: one row per lap with all its records (activity.js) instead of one sentence per record
    return renderActivityList(activity, { showDriver: true, limit: 15, emptyText: t("Nothing yet — go set some times.") });
  } catch (err) {
    return el("div", { class: "notice notice--error" }, err.message);
  }
}

async function renderInvitationsCard(teamId) {
  const card = el("div", { class: "card" });

  // --- send a new invitation ---
  const nameField = el("div", { class: "field" }, [
    el("label", {}, t("Driver's display name")),
    el("input", { type: "text", placeholder: t("exact name, e.g. Nito") }),
  ]);
  const sendBtn = el("button", { class: "btn" }, t("Invite"));
  const sendOut = el("div");

  sendBtn.addEventListener("click", async () => {
    const name = nameField.querySelector("input").value.trim();
    if (!name) return;
    sendBtn.disabled = true;
    sendOut.innerHTML = "";
    try {
      const matches = await api.lookupDriverByName(name);
      if (matches.length === 0) {
        sendOut.appendChild(el("div", { class: "notice notice--error" }, t("No driver named \"{0}\" found — names must match exactly.", [name])));
        return;
      }
      if (matches.length > 1) {
        sendOut.appendChild(
          el("div", { class: "notice notice--error" }, t("{0} drivers share that exact name — ask them their driver ID to disambiguate.", [matches.length]))
        );
        return;
      }
      await api.createTeamInvitation(teamId, matches[0].driver_id);
      sendOut.appendChild(el("div", { class: "notice notice--success" }, t("Invited {0}.", [matches[0].display_name])));
      nameField.querySelector("input").value = "";
      await refreshList();
    } catch (err) {
      sendOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
    } finally {
      sendBtn.disabled = false;
    }
  });

  card.appendChild(el("div", { class: "inline-form" }, [nameField, sendBtn]));
  card.appendChild(sendOut);

  // --- existing invitations ---
  const listBox = el("div", { style: "margin-top:16px;" });
  card.appendChild(listBox);

  async function refreshList() {
    listBox.innerHTML = "";
    let invites;
    try {
      invites = await api.teamInvitations(teamId);
    } catch (err) {
      listBox.appendChild(el("div", { class: "notice notice--error" }, err.message));
      return;
    }
    if (!invites.length) {
      listBox.appendChild(el("div", { class: "empty-state" }, t("No invitations sent yet.")));
      return;
    }
    invites.forEach((inv) => {
      const actions = el("div", { class: "inline-form" });
      if (inv.status === "pending" || inv.status === "expired") {
        const resendBtn = el("button", { class: "btn btn--ghost" }, t("Resend"));
        resendBtn.addEventListener("click", async () => {
          resendBtn.disabled = true;
          try {
            await api.resendTeamInvitation(teamId, inv.id);
            await refreshList();
          } catch (err) {
            listBox.appendChild(el("div", { class: "notice notice--error" }, err.message));
            resendBtn.disabled = false;
          }
        });
        actions.appendChild(resendBtn);
      }
      if (inv.status === "pending") {
        const revokeBtn = el("button", { class: "btn btn--ghost" }, t("Revoke"));
        revokeBtn.addEventListener("click", async () => {
          revokeBtn.disabled = true;
          try {
            await api.revokeTeamInvitation(teamId, inv.id);
            await refreshList();
          } catch (err) {
            listBox.appendChild(el("div", { class: "notice notice--error" }, err.message));
            revokeBtn.disabled = false;
          }
        });
        actions.appendChild(revokeBtn);
      }
      listBox.appendChild(
        el("div", { class: "card-row" }, [
          el("div", {}, [
            el("strong", {}, inv.invited_display_name),
            el("div", { class: "subtitle", style: "margin:2px 0 0;" }, t("by {0} — {1}", [inv.created_by_display_name, formatDate(inv.created_at)])),
          ]),
          el("span", { class: `tag ${inv.status === "accepted" ? "tag--valid" : inv.status === "pending" ? "" : "tag--invalid"}` }, inviteStatusLabel(inv.status)),
          actions,
        ])
      );
    });
  }

  await refreshList();
  return card;
}
