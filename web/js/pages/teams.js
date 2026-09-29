// pages/teams.js — POST /teams, POST /teams/join, GET /teams/mine.

import { api } from "../api.js";
import { store } from "../state.js";
import { el } from "../format.js";
import { renderSignInPrompt } from "./auth.js";

export async function renderTeams(container) {
  if (!store.isSignedIn()) {
    renderSignInPrompt(container, "Sign in to manage teams.");
    return;
  }

  container.appendChild(el("h1", {}, "Teams"));
  container.appendChild(
    el("p", { class: "subtitle" }, "Teams share a best-lap board and can get their own Discord announcements.")
  );

  // --- pending invitations addressed to me (V0.7.2 §9.2) ---
  const invitesBox = el("div", { class: "section" });
  container.appendChild(invitesBox);

  async function loadMyInvitations() {
    invitesBox.innerHTML = "";
    let invites = [];
    try {
      invites = await api.myInvitations();
    } catch {
      return; // quietly skip — this section is a convenience, not core
    }
    const pending = invites.filter((i) => i.status === "pending");
    if (!pending.length) return;

    invitesBox.appendChild(el("h2", {}, "Invitations"));
    const card = el("div", { class: "card" });
    pending.forEach((inv) => {
      const acceptBtn = el("button", { class: "btn" }, "Accept");
      const declineBtn = el("button", { class: "btn btn--ghost" }, "Decline");
      const rowOut = el("div");
      acceptBtn.addEventListener("click", async () => {
        acceptBtn.disabled = true;
        try {
          await api.acceptInvitation(inv.id);
          window.location.hash = `#/team/${inv.team_id}`;
        } catch (err) {
          rowOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
          acceptBtn.disabled = false;
        }
      });
      declineBtn.addEventListener("click", async () => {
        declineBtn.disabled = true;
        try {
          await api.declineInvitation(inv.id);
          await loadMyInvitations();
        } catch (err) {
          rowOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
          declineBtn.disabled = false;
        }
      });
      card.appendChild(
        el("div", { class: "card-row" }, [
          el("div", {}, [
            el("strong", {}, inv.team_name),
            el("div", { class: "subtitle", style: "margin:2px 0 0;" }, `Invited by ${inv.created_by_display_name}`),
          ]),
          el("div", { class: "inline-form" }, [acceptBtn, declineBtn]),
        ])
      );
      card.appendChild(rowOut);
    });
    invitesBox.appendChild(card);
  }

  const listBox = el("div", { class: "section" });
  container.appendChild(listBox);

  async function loadTeams() {
    listBox.innerHTML = "";
    listBox.appendChild(el("h2", {}, "Your teams"));
    try {
      const teams = await api.myTeams();
      if (!teams.length) {
        listBox.appendChild(el("div", { class: "empty-state" }, "You're not on a team yet — create one or join with an invite code."));
        return;
      }
      const card = el("div", { class: "card" });
      teams.forEach((t) => {
        const row = el("div", { class: "card-row" }, [
          el("div", {}, [
            el("a", { href: `#/team/${t.id}` }, [el("strong", {}, t.name)]),
            el("div", { class: "subtitle", style: "margin:2px 0 0;" }, `${t.member_count} member(s) — ${t.role}`),
          ]),
          el("span", { class: "tag" }, `invite: ${t.invite_code}`),
        ]);
        card.appendChild(row);
      });
      listBox.appendChild(card);
    } catch (err) {
      listBox.appendChild(el("div", { class: "notice notice--error" }, err.message));
    }
  }

  // --- create team ---
  const createCard = el("div", { class: "card" });
  createCard.appendChild(el("h2", {}, "Create a team"));
  const nameField = el("div", { class: "field" }, [
    el("label", { for: "teamName" }, "Team name"),
    el("input", { id: "teamName", type: "text" }),
  ]);
  const createBtn = el("button", { class: "btn" }, "Create");
  const createOut = el("div");
  createBtn.addEventListener("click", async () => {
    const name = nameField.querySelector("input").value.trim();
    if (!name) return;
    createBtn.disabled = true;
    createOut.innerHTML = "";
    try {
      const team = await api.createTeam(name);
      createOut.appendChild(el("div", { class: "notice notice--success" }, `Created "${team.name}" — invite code ${team.invite_code}`));
      nameField.querySelector("input").value = "";
      await loadTeams();
    } catch (err) {
      createOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
    } finally {
      createBtn.disabled = false;
    }
  });
  createCard.appendChild(nameField);
  createCard.appendChild(createBtn);
  createCard.appendChild(createOut);

  // --- join team ---
  const joinCard = el("div", { class: "card" });
  joinCard.appendChild(el("h2", {}, "Join a team"));
  const codeField = el("div", { class: "field" }, [
    el("label", { for: "inviteCode" }, "Invite code"),
    el("input", { id: "inviteCode", type: "text", placeholder: "e.g. 8F3KQ2LP" }),
  ]);
  const joinBtn = el("button", { class: "btn btn--ghost" }, "Join");
  const joinOut = el("div");
  joinBtn.addEventListener("click", async () => {
    const code = codeField.querySelector("input").value.trim();
    if (!code) return;
    joinBtn.disabled = true;
    joinOut.innerHTML = "";
    try {
      const team = await api.joinTeam(code);
      joinOut.appendChild(el("div", { class: "notice notice--success" }, `Joined "${team.name}".`));
      codeField.querySelector("input").value = "";
      await loadTeams();
    } catch (err) {
      joinOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
    } finally {
      joinBtn.disabled = false;
    }
  });
  joinCard.appendChild(codeField);
  joinCard.appendChild(joinBtn);
  joinCard.appendChild(joinOut);

  container.appendChild(createCard);
  container.appendChild(joinCard);

  await loadMyInvitations();
  await loadTeams();
}
