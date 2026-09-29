// pages/team_detail.js — the Team Dashboard (V0.6.8): overview, member
// list, team-scoped leaderboard, team records, and an activity console.
// Reuses the same api.js/el() patterns as the rest of the app.

import { api } from "../api.js";
import { store } from "../state.js";
import { el, formatLapTime, formatDate } from "../format.js";
import { renderSignInPrompt } from "./auth.js";

export async function renderTeamDetail(container, teamId) {
  if (!store.isSignedIn()) {
    renderSignInPrompt(container, "Sign in to view this team.");
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

  container.appendChild(el("a", { href: "#/teams", class: "subtitle" }, "← All teams"));
  container.appendChild(el("h1", {}, detail.name));
  if (detail.description) container.appendChild(el("p", { class: "subtitle" }, detail.description));

  // --- Overview strip ---
  const overview = el("div", { class: "card", style: "display:flex; gap:24px; flex-wrap:wrap;" }, [
    _stat(detail.member_count, "Members"),
    _stat(detail.active_driver_count, "Active drivers"),
    _stat(detail.total_laps, "Laps"),
    _stat(detail.pbs_this_week, "PBs this week"),
    _stat(detail.team_best_count, "Team bests"),
  ]);
  container.appendChild(overview);

  // --- Time-windowed stats (V0.7.2 §9.4) ---
  container.appendChild(await renderTeamStatsCard(teamId));

  // --- Settings (owner/admin only) ---
  if (canManage) {
    container.appendChild(renderSettingsCard(teamId, detail, isOwner));
  }

  // --- Members ---
  container.appendChild(el("h2", {}, "Members"));
  container.appendChild(renderMembersTable(teamId, members, detail.my_role, myDriverId, isOwner, () => refreshMembers()));

  const membersBox = { current: container.lastChild };
  async function refreshMembers() {
    const fresh = await api.teamMembers(teamId);
    const rebuilt = renderMembersTable(teamId, fresh, detail.my_role, myDriverId, isOwner, refreshMembers);
    membersBox.current.replaceWith(rebuilt);
    membersBox.current = rebuilt;
  }

  // --- Invitations (owner/admin only, V0.7.2 §9.2) ---
  if (canManage) {
    container.appendChild(el("h2", {}, "Invitations"));
    container.appendChild(await renderInvitationsCard(teamId));
  }

  // --- Team leaderboard ---
  container.appendChild(el("h2", {}, "Team leaderboard"));
  container.appendChild(await renderTeamLeaderboard(teamId));

  // --- Team records ---
  container.appendChild(el("h2", {}, "Team bests"));
  container.appendChild(await renderTeamRecords(teamId));

  // --- Activity console ---
  container.appendChild(el("h2", {}, "Recent activity"));
  container.appendChild(await renderActivity(teamId));
}

function _stat(value, label) {
  return el("div", { style: "min-width:90px;" }, [
    el("div", { style: "font-size:1.6em; font-weight:700;" }, String(value)),
    el("div", { class: "subtitle" }, label),
  ]);
}

function renderSettingsCard(teamId, detail, isOwner) {
  const card = el("div", { class: "card" });
  card.appendChild(el("h2", {}, "Team settings"));

  const nameField = el("div", { class: "field" }, [
    el("label", {}, "Team name"),
    el("input", { type: "text", value: detail.name, id: "teamNameInput" }),
  ]);
  const descField = el("div", { class: "field" }, [
    el("label", {}, "Description"),
    el("input", { type: "text", value: detail.description || "", id: "teamDescInput" }),
  ]);
  const announceField = el("div", { class: "field" }, [
    el("label", {}, [
      el("input", { type: "checkbox", id: "announceInput", ...(detail.discord_announcements_enabled ? { checked: "checked" } : {}) }),
      " Post team-best records to Discord",
    ]),
  ]);
  const saveBtn = el("button", { class: "btn" }, "Save");
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
      out.appendChild(el("div", { class: "notice notice--success" }, "Saved."));
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
    const dangerBtn = el("button", { class: "btn btn--ghost", style: "color:#c0392b; margin-top:12px;" }, "Delete team");
    dangerBtn.addEventListener("click", async () => {
      if (!confirm(`Delete "${detail.name}"? This can't be undone.`)) return;
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
      el("td", {}, [el("a", { href: `#/driver/${m.driver_id}` }, m.display_name), isMe ? " (you)" : ""]),
      el("td", {}, el("span", { class: "tag" }, m.role)),
      el("td", {}, el("span", { class: `tag ${m.is_active ? "tag--valid" : "tag--invalid"}` }, m.is_active ? "active" : "inactive")),
      el("td", {}, String(m.lap_count)),
      el("td", {}, String(m.pb_count)),
      el("td", {}, formatDate(m.last_activity)),
    ];

    const actions = el("td", {});
    if (isMe && m.role !== "owner") {
      const leaveBtn = el("button", { class: "btn btn--ghost" }, "Leave");
      leaveBtn.addEventListener("click", async () => {
        await api.removeTeamMember(teamId, m.driver_id);
        onChanged();
      });
      actions.appendChild(leaveBtn);
    } else if (!isMe && canManage && m.role !== "owner" && !(myRole === "admin" && m.role === "admin")) {
      const removeBtn = el("button", { class: "btn btn--ghost" }, "Remove");
      removeBtn.addEventListener("click", async () => {
        if (!confirm(`Remove ${m.display_name} from the team?`)) return;
        await api.removeTeamMember(teamId, m.driver_id);
        onChanged();
      });
      actions.appendChild(removeBtn);
      if (isOwner) {
        const roleBtn = el("button", { class: "btn btn--ghost" }, m.role === "admin" ? "Demote" : "Promote");
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
    el("thead", {}, el("tr", {}, ["Driver", "Role", "Status", "Laps", "PBs", "Last activity", ""].map((h) => el("th", {}, h)))),
    el("tbody", {}, rows),
  ]);
}

async function renderTeamLeaderboard(teamId) {
  const wrap = el("div", { class: "card" });
  // V0.8.9: searchable track list, grouped by venue with every layout that has
  // laps (only those can have a team board), instead of a flat list of raw names.
  const trackSearch = el("input", { type: "search", placeholder: "Search a track, e.g. Fuji…", autocomplete: "off" });
  const trackSelect = el("select", {}, [el("option", { value: "" }, "Loading tracks…")]);

  async function loadTracks(q) {
    let venues = [];
    try {
      venues = await api.catalogVenues(q);
    } catch {
      // fall through with an empty list
    }
    trackSelect.innerHTML = "";
    const withLaps = venues.filter((v) => v.layouts.length);
    trackSelect.appendChild(el("option", { value: "" }, withLaps.length ? "Choose a track…" : q ? `No driven track matches "${q}"` : "No laps uploaded yet"));
    withLaps.forEach((v) => {
      const group = el("optgroup", { label: v.venue });
      v.layouts.forEach((l) => group.appendChild(el("option", { value: l.track_name }, l.track_name)));
      trackSelect.appendChild(group);
    });
  }
  let trackSearchTimer = null;
  trackSearch.addEventListener("input", () => {
    clearTimeout(trackSearchTimer);
    trackSearchTimer = setTimeout(() => loadTracks(trackSearch.value.trim()), 250);
  });
  await loadTracks("");
  const classSelect = el("select", {}, [el("option", { value: "" }, "Pick a track first")]);
  // V0.7.2 §9.5: third cascade level. "All cars in this class" keeps the
  // previous class-wide behavior as the default — picking a specific
  // model narrows further to that exact car (ignoring livery/number, see
  // server/models.py's car_model docstring).
  const carSelect = el("select", {}, [el("option", { value: "" }, "Pick a class first")]);
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
    resetCarSelect("Pick a class first");
    resultsBox.innerHTML = "";
    if (!track) {
      classSelect.appendChild(el("option", { value: "" }, "Pick a track first"));
      return;
    }
    // Every class is offered (V0.8.9), with its lap count at this track.
    const classes = await api.catalogClassOptions(track);
    classSelect.appendChild(el("option", { value: "" }, "Choose…"));
    classes.forEach((c) =>
      classSelect.appendChild(el("option", { value: c.name }, `${c.name} · ${c.lap_count ? `${c.lap_count} laps` : "no laps yet"}`))
    );
  });

  classSelect.addEventListener("change", async () => {
    const track = trackSelect.value;
    const cls = classSelect.value;
    resetCarSelect("All cars in this class");
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
    el("div", { class: "field" }, [el("label", {}, "Find a track"), trackSearch]),
    el("div", { class: "field" }, [el("label", {}, "Track"), trackSelect]),
    el("div", { class: "field" }, [el("label", {}, "Class"), classSelect]),
    el("div", { class: "field" }, [el("label", {}, "Car"), carSelect]),
  ]));
  wrap.appendChild(resultsBox);
  return wrap;
}

const _STATS_WINDOWS = [
  ["today", "Today"],
  ["7d", "7 Days"],
  ["30d", "30 Days"],
  ["all", "All Time"],
];

async function renderTeamStatsCard(teamId) {
  const card = el("div", { class: "card" });
  card.appendChild(el("h2", {}, "Team Statistics"));

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
      statsBox.appendChild(_stat(stats.laps, "Laps"));
      statsBox.appendChild(_stat(stats.pbs, "PBs"));
      statsBox.appendChild(_stat(stats.active_drivers, "Active drivers"));
      statsBox.appendChild(_stat(stats.records, "Team records"));
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
  if (!entries.length) return el("div", { class: "empty-state" }, "No valid laps from this team here yet.");
  const rows = entries.map((e, i) =>
    el("tr", {}, [
      el("td", {}, String(i + 1)),
      el("td", {}, el("a", { href: `#/driver/${e.driver_id}` }, e.driver_name)),
      el("td", {}, formatLapTime(e.lap_time)),
      el("td", {}, i === 0 ? "—" : `+${e.delta_to_leader.toFixed(3)}`),
    ])
  );
  return el("table", { class: "timing-table" }, [
    el("thead", {}, el("tr", {}, ["#", "Driver", "Lap", "Gap"].map((h) => el("th", {}, h)))),
    el("tbody", {}, rows),
  ]);
}

async function renderTeamRecords(teamId) {
  try {
    const records = await api.teamRecords(teamId);
    if (!records.length) return el("div", { class: "empty-state" }, "No team bests yet.");
    const rows = records.map((r) => {
      // §21 "You vs Team": null means no lap of your own on this combo
      // (never shown as a misleading "0.000" tie).
      let youCell;
      if (r.you_hold_it) {
        youCell = el("span", { class: "tag tag--valid" }, "You hold this");
      } else if (r.gap != null) {
        youCell = el("span", { class: "lap-time" }, `+${r.gap.toFixed(3)}`);
      } else {
        youCell = el("span", { class: "subtitle" }, "no lap yet");
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
      el("thead", {}, el("tr", {}, ["Track", "Class", "Car", "Time", "Driver", "You"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows),
    ]);
  } catch (err) {
    return el("div", { class: "notice notice--error" }, err.message);
  }
}

async function renderActivity(teamId) {
  try {
    const activity = await api.teamActivity(teamId);
    if (!activity.length) return el("div", { class: "empty-state" }, "Nothing yet — go set some times.");
    const lines = activity.map((a) => {
      const icon = a.record_type === "WR" ? "🌍" : a.record_type === "TEAM_BEST" ? "🏁" : "⏱";
      const label = a.record_type === "PR" ? "new personal best" : a.record_type === "TEAM_BEST" ? "new team best" : "new world record";
      return el(
        "div",
        { class: "card-row" },
        `${icon} ${a.driver_name} set a ${label} at ${a.track_name} (${a.car_name}): ${formatLapTime(a.lap_time)} — ${formatDate(a.created_at)}`
      );
    });
    return el("div", { class: "card" }, lines);
  } catch (err) {
    return el("div", { class: "notice notice--error" }, err.message);
  }
}

async function renderInvitationsCard(teamId) {
  const card = el("div", { class: "card" });

  // --- send a new invitation ---
  const nameField = el("div", { class: "field" }, [
    el("label", {}, "Driver's display name"),
    el("input", { type: "text", placeholder: "exact name, e.g. Nito" }),
  ]);
  const sendBtn = el("button", { class: "btn" }, "Invite");
  const sendOut = el("div");

  sendBtn.addEventListener("click", async () => {
    const name = nameField.querySelector("input").value.trim();
    if (!name) return;
    sendBtn.disabled = true;
    sendOut.innerHTML = "";
    try {
      const matches = await api.lookupDriverByName(name);
      if (matches.length === 0) {
        sendOut.appendChild(el("div", { class: "notice notice--error" }, `No driver named "${name}" found — names must match exactly.`));
        return;
      }
      if (matches.length > 1) {
        sendOut.appendChild(
          el("div", { class: "notice notice--error" }, `${matches.length} drivers share that exact name — ask them their driver ID to disambiguate.`)
        );
        return;
      }
      await api.createTeamInvitation(teamId, matches[0].driver_id);
      sendOut.appendChild(el("div", { class: "notice notice--success" }, `Invited ${matches[0].display_name}.`));
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
      listBox.appendChild(el("div", { class: "empty-state" }, "No invitations sent yet."));
      return;
    }
    invites.forEach((inv) => {
      const actions = el("div", { class: "inline-form" });
      if (inv.status === "pending" || inv.status === "expired") {
        const resendBtn = el("button", { class: "btn btn--ghost" }, "Resend");
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
        const revokeBtn = el("button", { class: "btn btn--ghost" }, "Revoke");
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
            el("div", { class: "subtitle", style: "margin:2px 0 0;" }, `by ${inv.created_by_display_name} — ${formatDate(inv.created_at)}`),
          ]),
          el("span", { class: `tag ${inv.status === "accepted" ? "tag--valid" : inv.status === "pending" ? "" : "tag--invalid"}` }, inv.status),
          actions,
        ])
      );
    });
  }

  await refreshList();
  return card;
}
