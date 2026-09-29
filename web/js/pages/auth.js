// pages/auth.js — three ways in: email+password sign-in (V0.7.2 —
// establishes an HttpOnly cookie session, see server/sessions.py, for a
// driver who's set a password via the Account page at least once),
// registering a fresh driver identity (mints a desktop-client auth_token
// + client_secret — see server/security.py — not a web session), or
// restoring a session by pasting back in a previously-saved auth_token
// (legacy path, still bearer-token-based, for drivers without a password
// set yet).

import { api, ApiError } from "../api.js";
import { store } from "../state.js";
import { el } from "../format.js";

export function renderSignInPrompt(container, message = "Sign in to see this.") {
  const notice = el("div", { class: "notice" }, message);
  const link = el("a", { href: "#/account", class: "btn", style: "margin-top:12px;display:inline-block;" }, "Go to Account");
  container.appendChild(el("h1", {}, "Sign in required"));
  container.appendChild(notice);
  container.appendChild(link);
}

function renderLoginCard() {
  const card = el("div", { class: "card" });
  card.appendChild(el("h2", {}, "Sign in"));

  const emailField = el("div", { class: "field" }, [
    el("label", { for: "loginEmail" }, "Email"),
    el("input", { id: "loginEmail", type: "email", autocomplete: "email" }),
  ]);
  const pwField = el("div", { class: "field" }, [
    el("label", { for: "loginPw" }, "Password"),
    el("input", { id: "loginPw", type: "password", autocomplete: "current-password" }),
  ]);
  const loginBtn = el("button", { class: "btn" }, "Sign in");
  const out = el("div");

  loginBtn.addEventListener("click", async () => {
    const email = emailField.querySelector("input").value.trim();
    const password = pwField.querySelector("input").value;
    if (!email || !password) return;
    loginBtn.disabled = true;
    out.innerHTML = "";
    try {
      const res = await api.login(email, password);
      store.setSession({ driverId: res.driver_id, displayName: res.display_name, viaCookie: true });
      out.appendChild(el("div", { class: "notice notice--success" }, "Signed in."));
      window.location.hash = "#/dashboard";
    } catch (err) {
      out.appendChild(el("div", { class: "notice notice--error" }, err instanceof ApiError ? err.message : "Sign-in failed."));
    } finally {
      loginBtn.disabled = false;
    }
  });

  const forgotLink = el("a", { href: "#", style: "font-size:13px;display:inline-block;margin-top:4px;" }, "Forgot password?");
  const forgotOut = el("div");
  forgotLink.addEventListener("click", async (evt) => {
    evt.preventDefault();
    const email = emailField.querySelector("input").value.trim();
    forgotOut.innerHTML = "";
    if (!email) {
      forgotOut.appendChild(el("div", { class: "notice notice--error" }, "Enter your email above first."));
      return;
    }
    try {
      await api.requestPasswordReset(email);
      forgotOut.appendChild(
        el("div", { class: "notice notice--success" }, "If that email has an account with a password set, a reset link is on its way.")
      );
    } catch (err) {
      forgotOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
    }
  });

  card.appendChild(emailField);
  card.appendChild(pwField);
  card.appendChild(loginBtn);
  card.appendChild(out);
  card.appendChild(forgotLink);
  card.appendChild(forgotOut);
  return card;
}

export function renderAuth(container) {
  container.appendChild(el("h1", {}, "Account"));
  container.appendChild(
    el(
      "p",
      { class: "subtitle" },
      "Sign in with email + password if you've set one, register a new driver profile, or restore a session with a saved token."
    )
  );

  container.appendChild(renderLoginCard());

  // --- register ---
  const registerCard = el("div", { class: "card" });
  registerCard.appendChild(el("h2", {}, "New driver"));

  const nameField = el("div", { class: "field" }, [
    el("label", { for: "displayName" }, "Display name"),
    el("input", { id: "displayName", type: "text", placeholder: "e.g. Mika H." }),
  ]);
  const regSecretField = el("div", { class: "field" }, [
    el("label", { for: "regSecret" }, "Registration secret (only if this server requires one)"),
    el("input", { id: "regSecret", type: "password", placeholder: "leave blank if you don't have one" }),
  ]);
  const registerBtn = el("button", { class: "btn" }, "Register");
  const registerOut = el("div");

  registerBtn.addEventListener("click", async () => {
    const name = nameField.querySelector("input").value.trim();
    const regSecret = regSecretField.querySelector("input").value.trim();
    if (!name) return;
    registerBtn.disabled = true;
    registerOut.innerHTML = "";
    try {
      const res = await api.registerDriver(name, regSecret || undefined);
      // V0.7.2: this auth_token is the DESKTOP CLIENT's credential (see
      // server/security.py) — it is deliberately NOT persisted as this
      // browser's ongoing web session (that would be exactly the
      // localStorage-token pattern this version moves away from, see
      // state.js). It's set here only transiently, for the current page
      // load, so the "Email & Password" card below can use it once to
      // attach real login credentials to this brand-new driver — after
      // that, use "Sign in" above like any other visit.
      store.setSession({ driverId: res.driver_id, displayName: res.display_name, authToken: res.auth_token });
      registerOut.appendChild(el("div", { class: "notice notice--success" }, "Registered."));
      const box = el("div", { class: "credential-box" }, [
        "Save this client secret now — it's shown once and needed to configure the desktop recorder client:",
        el("code", {}, res.client_secret),
      ]);
      registerOut.appendChild(box);
      registerOut.appendChild(
        el(
          "div",
          { class: "notice", style: "margin-top:12px;" },
          "You're set up as a driver now. Head to Account → Email & Password to set a password — that's what lets you sign in on this or any browser afterwards, instead of pasting a token."
        )
      );
    } catch (err) {
      registerOut.appendChild(el("div", { class: "notice notice--error" }, err.message));
    } finally {
      registerBtn.disabled = false;
    }
  });

  registerCard.appendChild(nameField);
  registerCard.appendChild(regSecretField);
  registerCard.appendChild(registerBtn);
  registerCard.appendChild(registerOut);

  // --- restore session ---
  const restoreCard = el("div", { class: "card" });
  restoreCard.appendChild(el("h2", {}, "Already have a token?"));
  const tokenField = el("div", { class: "field" }, [
    el("label", { for: "authToken" }, "Auth token"),
    el("input", { id: "authToken", type: "password", placeholder: "paste your saved auth_token" }),
  ]);
  const restoreBtn = el("button", { class: "btn btn--ghost" }, "Restore session");
  const restoreOut = el("div");

  restoreBtn.addEventListener("click", async () => {
    const token = tokenField.querySelector("input").value.trim();
    if (!token) return;
    restoreBtn.disabled = true;
    restoreOut.innerHTML = "";
    // Transient placeholder session just so api.js's auth:true check
    // passes for the whoami() call itself — replaced immediately below
    // with the real driver_id/display_name (V0.7.2: the dashboard needs
    // a real driver_id to load "your" profile data, which a free-text
    // name field could never have provided reliably anyway).
    store.setSession({ driverId: null, displayName: "…", authToken: token });
    try {
      const who = await api.whoami();
      store.setSession({ driverId: who.driver_id, displayName: who.display_name, authToken: token });
      restoreOut.appendChild(el("div", { class: "notice notice--success" }, "Session restored."));
    } catch (err) {
      store.signOut();
      restoreOut.appendChild(
        el("div", { class: "notice notice--error" }, err instanceof ApiError ? err.message : "Could not verify that token.")
      );
    } finally {
      restoreBtn.disabled = false;
    }
  });

  restoreCard.appendChild(tokenField);
  restoreCard.appendChild(restoreBtn);
  restoreCard.appendChild(restoreOut);

  container.appendChild(registerCard);
  container.appendChild(restoreCard);
}
