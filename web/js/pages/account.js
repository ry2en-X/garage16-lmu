// pages/account.js — signed-out: registration/restore form (auth.js).
// signed-in: driver summary, Discord account link code, sign out.

import { api } from "../api.js";
import { store } from "../state.js";
import { el } from "../format.js";
import { renderAuth } from "./auth.js";

export async function renderAccount(container) {
  if (!store.isSignedIn()) {
    renderAuth(container);
    return;
  }

  const session = store.getSession();

  container.appendChild(el("h1", {}, "Account"));
  container.appendChild(el("p", { class: "subtitle" }, "Manage your driver profile and Discord link."));

  const profileCard = el("div", { class: "card" }, [
    el("div", { class: "card-row" }, [el("span", {}, "Display name"), el("strong", {}, session.displayName)]),
    session.driverId ? el("div", { class: "card-row" }, [el("span", {}, "Driver ID"), el("strong", {}, String(session.driverId))]) : null,
  ]);
  container.appendChild(profileCard);

  // --- discord link (V0.8.7: status, one-time link code, unlink) ---
  const discordCard = el("div", { class: "card" });
  container.appendChild(discordCard);
  await renderDiscordCard(discordCard);

  // --- security: rotate / revoke credentials ---
  const securityCard = el("div", { class: "card" });
  securityCard.appendChild(el("h2", {}, "Desktop Client Credentials"));
  securityCard.appendChild(
    el(
      "p",
      { class: "subtitle" },
      "These belong to the LMU recorder client on your PC, not to this browser session (V0.7.2 keeps the two fully separate — see below for signing this browser out). " +
        "Rotate a credential if you're just being careful — the old one stops working, the new one keeps your identity. " +
        "Revoke only if a token actually leaked: it disconnects the desktop client immediately and can't be undone from here."
    )
  );

  const rotateTokenBtn = el("button", { class: "btn btn--ghost" }, "Rotate auth token");
  const rotateSecretBtn = el("button", { class: "btn btn--ghost" }, "Rotate client secret");
  const revokeBtn = el("button", { class: "btn btn--ghost" }, "Revoke desktop client access");
  const securityOut = el("div");

  rotateTokenBtn.addEventListener("click", async () => {
    rotateTokenBtn.disabled = true;
    securityOut.innerHTML = "";
    try {
      const res = await api.rotateToken();
      // Deliberately NOT merged into store.getSession(): that would leak
      // this desktop-client credential into a cookie-based web session's
      // persisted localStorage entry, exactly what V0.7.2 moves away
      // from. Show it for copying, same as rotateSecret below — nothing
      // about this browser's own sign-in state changes.
      securityOut.appendChild(
        el("div", { class: "credential-box" }, [
          "New auth token — save it and run `python -m client.main --reconfigure` to update the desktop app:",
          el("code", {}, res.auth_token),
        ])
      );
    } catch (err) {
      securityOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
    } finally {
      rotateTokenBtn.disabled = false;
    }
  });

  rotateSecretBtn.addEventListener("click", async () => {
    rotateSecretBtn.disabled = true;
    securityOut.innerHTML = "";
    try {
      const res = await api.rotateSecret();
      securityOut.appendChild(
        el("div", { class: "credential-box" }, [
          "New client secret — save it and run `python -m client.main --reconfigure` to update the desktop app:",
          el("code", {}, res.client_secret),
        ])
      );
    } catch (err) {
      securityOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
    } finally {
      rotateSecretBtn.disabled = false;
    }
  });

  revokeBtn.addEventListener("click", async () => {
    if (!window.confirm("This disconnects your desktop LMU client immediately, with no way back in for it. This browser stays signed in. Continue?")) {
      return;
    }
    revokeBtn.disabled = true;
    try {
      await api.revokeSession();
      securityOut.innerHTML = "";
      securityOut.appendChild(el("div", { class: "notice notice--success" }, "Desktop client access revoked."));
    } catch (err) {
      securityOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
    } finally {
      revokeBtn.disabled = false;
    }
  });

  securityCard.appendChild(rotateTokenBtn);
  securityCard.appendChild(rotateSecretBtn);
  securityCard.appendChild(revokeBtn);
  securityCard.appendChild(securityOut);
  container.appendChild(securityCard);

  // --- sessions (V0.7.2): this browser's own sign-in, separate from the
  // desktop client credentials above ---
  const sessionsCard = el("div", { class: "card" });
  sessionsCard.appendChild(el("h2", {}, "Sessions"));
  sessionsCard.appendChild(
    el("p", { class: "subtitle" }, "Browsers/devices currently signed in with your email and password.")
  );
  const sessionsBox = el("div");
  const logoutAllBtn = el("button", { class: "btn btn--ghost" }, "Sign out everywhere");
  const sessionsOut = el("div");

  async function loadSessions() {
    sessionsBox.innerHTML = "";
    try {
      const sessions = await api.mySessions();
      if (!sessions.length) {
        sessionsBox.appendChild(el("div", { class: "empty-state" }, "No active email/password sessions."));
        return;
      }
      const card = el("div", { class: "card" });
      sessions.forEach((s) => {
        const label = s.is_current ? "This device — now" : s.user_agent || "Unknown device";
        const row = el("div", { class: "card-row" }, [
          el("div", {}, [
            el("strong", {}, label),
            el("div", { class: "subtitle", style: "margin:2px 0 0;" },
              `Signed in ${s.created_at ? new Date(s.created_at).toLocaleString() : "—"}`),
          ]),
          s.is_current
            ? null
            : (() => {
                const btn = el("button", { class: "btn btn--ghost" }, "Sign out");
                btn.addEventListener("click", async () => {
                  btn.disabled = true;
                  try {
                    await api.revokeSessionById(s.id);
                    await loadSessions();
                  } catch (err) {
                    sessionsOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
                    btn.disabled = false;
                  }
                });
                return btn;
              })(),
        ]);
        card.appendChild(row);
      });
      sessionsBox.appendChild(card);
    } catch (err) {
      sessionsBox.appendChild(el("div", { class: "notice notice--error" }, err.message));
    }
  }

  logoutAllBtn.addEventListener("click", async () => {
    if (!window.confirm("This signs this and every other browser out immediately. Continue?")) return;
    logoutAllBtn.disabled = true;
    try {
      await api.logoutAllDevices();
    } catch {
      /* proceed to sign out locally regardless */
    }
    store.signOut();
    window.location.hash = "#/leaderboard";
  });

  sessionsCard.appendChild(sessionsBox);
  sessionsCard.appendChild(logoutAllBtn);
  sessionsCard.appendChild(sessionsOut);
  container.appendChild(sessionsCard);
  await loadSessions();

  // --- driver name (V0.8.9) ---
  // The name on leaderboards, profiles, teams and in Discord. It used to be
  // whatever was typed when registering and could never be changed.
  const nameCard = el("div", { class: "card" });
  nameCard.appendChild(el("h2", {}, "Driver name"));
  nameCard.appendChild(
    el("p", { class: "subtitle" }, "This is the name other drivers see on leaderboards, in teams and in Discord. Changing it updates it everywhere, including your past laps.")
  );
  const nameInput = el("input", { id: "driverName", type: "text", maxlength: "60" });
  nameInput.value = session?.displayName || "";
  const nameField = el("div", { class: "field" }, [el("label", { for: "driverName" }, "Display name"), nameInput]);
  const nameBtn = el("button", { class: "btn" }, "Save name");
  const nameOut = el("div");
  nameBtn.addEventListener("click", async () => {
    nameOut.innerHTML = "";
    const wanted = nameInput.value.trim();
    if (!wanted) {
      nameOut.appendChild(el("div", { class: "notice notice--error" }, "Please enter a name."));
      return;
    }
    nameBtn.disabled = true;
    try {
      const who = await api.updateDisplayName(wanted);
      store.setSession({ ...store.getSession(), displayName: who.display_name });
      nameInput.value = who.display_name;
      nameOut.appendChild(el("div", { class: "notice notice--success" }, `Saved. You are now "${who.display_name}".`));
    } catch (err) {
      nameOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
    } finally {
      nameBtn.disabled = false;
    }
  });
  nameCard.appendChild(nameField);
  nameCard.appendChild(nameBtn);
  nameCard.appendChild(nameOut);
  container.appendChild(nameCard);

  // --- email & password (V0.6.9; verification status added V0.8-NAS §2) ---
  const pwCard = el("div", { class: "card" });
  pwCard.appendChild(el("h2", {}, "Email & Password"));
  pwCard.appendChild(
    el(
      "p",
      { class: "subtitle" },
      "Set this once to unlock signing in with email+password instead of a saved token — and to be able to recover your account via \"Forgot password\" if you ever lose that token. Leave \"Current password\" blank the first time; fill it in when changing an existing password."
    )
  );

  const verificationStatus = el("div");
  pwCard.appendChild(verificationStatus);

  async function loadVerificationStatus() {
    verificationStatus.innerHTML = "";
    let who;
    try {
      who = await api.whoami();
    } catch {
      return; // not fatal — the form below still works without this
    }
    if (!who.email) return; // nothing to show yet
    const row = el("div", { class: "card-row" }, [
      el("span", {}, who.email),
      el("span", { class: `tag ${who.email_verified ? "tag--valid" : "tag--invalid"}` }, who.email_verified ? "verified" : "not verified"),
    ]);
    verificationStatus.appendChild(row);
    if (!who.email_verified) {
      const resendBtn = el("button", { class: "btn btn--ghost", style: "margin-top:8px;" }, "Resend verification email");
      const resendOut = el("div");
      resendBtn.addEventListener("click", async () => {
        resendBtn.disabled = true;
        resendOut.innerHTML = "";
        try {
          await api.resendVerificationEmail();
          resendOut.appendChild(el("div", { class: "notice notice--success" }, "Verification email sent — check your inbox."));
        } catch (err) {
          resendOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
        } finally {
          resendBtn.disabled = false;
        }
      });
      verificationStatus.appendChild(resendBtn);
      verificationStatus.appendChild(resendOut);
    }
  }
  await loadVerificationStatus();

  const pwEmailField = el("div", { class: "field" }, [
    el("label", { for: "pwEmail" }, "Email"),
    el("input", { id: "pwEmail", type: "email", autocomplete: "email" }),
  ]);
  const pwCurrentField = el("div", { class: "field" }, [
    el("label", { for: "pwCurrent" }, "Current password (leave blank if you've never set one)"),
    el("input", { id: "pwCurrent", type: "password", autocomplete: "current-password" }),
  ]);
  const pwNewField = el("div", { class: "field" }, [
    el("label", { for: "pwNew" }, "New password (min. 10 characters)"),
    el("input", { id: "pwNew", type: "password", autocomplete: "new-password" }),
  ]);
  const pwSaveBtn = el("button", { class: "btn btn--ghost" }, "Save");
  const pwOut = el("div");
  pwSaveBtn.addEventListener("click", async () => {
    const email = pwEmailField.querySelector("input").value.trim();
    const currentPassword = pwCurrentField.querySelector("input").value;
    const newPassword = pwNewField.querySelector("input").value;
    if (!email || !newPassword) return;
    pwSaveBtn.disabled = true;
    pwOut.innerHTML = "";
    try {
      await api.setPassword({ email, newPassword, currentPassword });
      pwOut.appendChild(el("div", { class: "notice notice--success" }, "Saved. If this is a new or changed email, check your inbox to verify it."));
      pwCurrentField.querySelector("input").value = "";
      pwNewField.querySelector("input").value = "";
      await loadVerificationStatus();
    } catch (err) {
      pwOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
    } finally {
      pwSaveBtn.disabled = false;
    }
  });
  pwCard.appendChild(pwEmailField);
  pwCard.appendChild(pwCurrentField);
  pwCard.appendChild(pwNewField);
  pwCard.appendChild(pwSaveBtn);
  pwCard.appendChild(pwOut);
  container.appendChild(pwCard);

  // --- data & privacy (V0.6.9: the backend has had /accounts/me/export
  // and DELETE /accounts/me since before V0.6.8 — this card is the first
  // time either is actually reachable from the web app) ---
  const privacyCard = el("div", { class: "card" });
  privacyCard.appendChild(el("h2", {}, "Data & Privacy"));
  privacyCard.appendChild(
    el(
      "p",
      { class: "subtitle" },
      "Export everything Garage16 has stored about you, or permanently delete your account."
    )
  );

  const exportBtn = el("button", { class: "btn btn--ghost" }, "Download my data (JSON)");
  const exportOut = el("div");
  exportBtn.addEventListener("click", async () => {
    exportBtn.disabled = true;
    exportOut.innerHTML = "";
    try {
      const data = await api.exportMyData();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = el("a", { href: url, download: `garage16-export-${data.driver_id}.json` });
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      exportOut.appendChild(el("div", { class: "notice notice--success" }, "Export downloaded."));
    } catch (err) {
      exportOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
    } finally {
      exportBtn.disabled = false;
    }
  });

  const deleteBtn = el("button", { class: "btn btn--danger" }, "Delete account");
  const deleteOut = el("div");
  deleteBtn.addEventListener("click", async () => {
    if (
      !window.confirm(
        "This permanently deletes your driver profile, every lap you've uploaded, team memberships, and your Discord link. There is no undo. Continue?"
      )
    ) {
      return;
    }
    deleteBtn.disabled = true;
    deleteOut.innerHTML = "";
    try {
      await api.deleteMyAccount();
      store.signOut();
      window.location.hash = "#/leaderboard";
    } catch (err) {
      deleteOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
      deleteBtn.disabled = false;
    }
  });

  privacyCard.appendChild(exportBtn);
  privacyCard.appendChild(exportOut);
  privacyCard.appendChild(el("div", { style: "height:16px;" }));
  privacyCard.appendChild(deleteBtn);
  privacyCard.appendChild(deleteOut);
  container.appendChild(privacyCard);

  // --- sign out ---
  const signOutBtn = el("button", { class: "btn btn--ghost", style: "margin-top:24px;" }, "Sign out");
  signOutBtn.addEventListener("click", async () => {
    try {
      await api.logout(); // clears the server-side session + cookie, if any
    } catch {
      /* proceed to sign out locally regardless */
    }
    store.signOut();
    window.location.hash = "#/leaderboard";
  });
  container.appendChild(signOutBtn);
}


// The Discord card: shows whether this driver is linked, and either offers
// a one-time "profile link token" to run as `/link <code>` in Discord, or
// (when linked) an Unlink button. Re-renders itself after every change.
export async function renderDiscordCard(card) {
  card.innerHTML = "";
  card.appendChild(el("h2", {}, "Link Discord"));

  let linked = false;
  try {
    linked = (await api.discordStatus()).linked;
  } catch (err) {
    card.appendChild(el("div", { class: "notice notice--error" }, err.message));
    return;
  }

  if (linked) {
    card.appendChild(
      el("div", { class: "card-row" }, [
        el("span", {}, "Discord"),
        el("span", { class: "tag tag--valid" }, "linked"),
      ])
    );
    card.appendChild(
      el(
        "p",
        { class: "subtitle" },
        "Your Discord account is linked to this driver, so the Garage16 bot's commands (/pb, /teambest, …) know who you are. " +
          "Unlink it to connect a different Discord account."
      )
    );
    const unlinkBtn = el("button", { class: "btn btn--ghost" }, "Unlink Discord");
    const out = el("div");
    unlinkBtn.addEventListener("click", async () => {
      if (!window.confirm("Unlink your Discord account from this driver?")) return;
      unlinkBtn.disabled = true;
      try {
        await api.unlinkDiscord();
        await renderDiscordCard(card);
      } catch (err) {
        out.appendChild(el("div", { class: "notice notice--error" }, err.message));
        unlinkBtn.disabled = false;
      }
    });
    card.appendChild(unlinkBtn);
    card.appendChild(out);
    return;
  }

  card.appendChild(
    el(
      "p",
      { class: "subtitle" },
      "Connect your Discord account so the Garage16 bot knows who you are. " +
        "1) Generate a link code here. 2) Paste the command it shows into a channel on your Discord server where the Garage16 bot is present — only you see the bot's reply. " +
        "The code works once and expires after 10 minutes. Only ever use codes you generated yourself here."
    )
  );
  const genBtn = el("button", { class: "btn" }, "Generate link code");
  const out = el("div");
  let pollTimer = null;
  let countdownTimer = null;

  function stopTimers() {
    if (pollTimer) clearInterval(pollTimer);
    if (countdownTimer) clearInterval(countdownTimer);
    pollTimer = countdownTimer = null;
  }

  genBtn.addEventListener("click", async () => {
    genBtn.disabled = true;
    stopTimers();
    out.innerHTML = "";
    try {
      const res = await api.createDiscordLinkCode();
      const expiresAt = Date.now() + res.expires_in_seconds * 1000;

      const remaining = el("span", { class: "subtitle" }, "");
      const copyBtn = el("button", { class: "btn btn--ghost" }, "Copy command");
      copyBtn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(res.command);
          copyBtn.textContent = "Copied";
        } catch {
          copyBtn.textContent = "Select and copy the command above";
        }
      });
      out.appendChild(
        el("div", { class: "credential-box" }, [
          el("div", {}, "Run in Discord:"),
          el("code", { style: "font-size:1.2em;" }, res.command),
          el("div", {}, [remaining]),
        ])
      );
      out.appendChild(copyBtn);

      const tick = () => {
        const left = Math.max(0, Math.round((expiresAt - Date.now()) / 1000));
        remaining.textContent = left > 0 ? `expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "expired — generate a new code";
        if (left === 0) stopTimers();
      };
      tick();
      countdownTimer = setInterval(tick, 1000);

      // Flip the card to "linked" as soon as the bot has redeemed the
      // code — no manual refresh. Stops when the card leaves the page,
      // the code expires, or the link happens.
      pollTimer = setInterval(async () => {
        if (!card.isConnected) return stopTimers();
        try {
          if ((await api.discordStatus()).linked) {
            stopTimers();
            await renderDiscordCard(card);
          }
        } catch {
          /* transient — try again on the next tick */
        }
      }, 3000);
    } catch (err) {
      out.appendChild(el("div", { class: "notice notice--error" }, err.message));
    } finally {
      genBtn.disabled = false;
    }
  });
  card.appendChild(genBtn);
  card.appendChild(out);
}
