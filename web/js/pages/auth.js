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

import { t } from "../i18n.js";
export function renderSignInPrompt(container, message = t("Sign in to see this.")) {
  const notice = el("div", { class: "notice" }, message);
  const link = el("a", { href: "#/account", class: "btn", style: "margin-top:12px;display:inline-block;" }, t("Go to Account"));
  container.appendChild(el("h1", {}, t("Sign in required")));
  container.appendChild(notice);
  container.appendChild(link);
}

function renderLoginCard() {
  const card = el("div", { class: "card" });
  card.appendChild(el("h2", {}, t("Sign in")));

  const emailField = el("div", { class: "field" }, [
    el("label", { for: "loginEmail" }, t("Email")),
    el("input", { id: "loginEmail", type: "email", autocomplete: "email" }),
  ]);
  const pwField = el("div", { class: "field" }, [
    el("label", { for: "loginPw" }, t("Password")),
    el("input", { id: "loginPw", type: "password", autocomplete: "current-password" }),
  ]);
  const loginBtn = el("button", { class: "btn" }, t("Sign in"));
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
      out.appendChild(el("div", { class: "notice notice--success" }, t("Signed in.")));
      window.location.hash = "#/dashboard";
    } catch (err) {
      out.appendChild(el("div", { class: "notice notice--error" }, err instanceof ApiError ? err.message : t("Sign-in failed.")));
    } finally {
      loginBtn.disabled = false;
    }
  });

  const forgotLink = el("a", { href: "#", style: "font-size:13px;display:inline-block;margin-top:4px;" }, t("Forgot password?"));
  const forgotOut = el("div");
  forgotLink.addEventListener("click", async (evt) => {
    evt.preventDefault();
    const email = emailField.querySelector("input").value.trim();
    forgotOut.innerHTML = "";
    if (!email) {
      forgotOut.appendChild(el("div", { class: "notice notice--error" }, t("Enter your email above first.")));
      return;
    }
    try {
      await api.requestPasswordReset(email);
      forgotOut.appendChild(
        el("div", { class: "notice notice--success" }, t("If that email has an account with a password set, a reset link is on its way."))
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
  container.appendChild(el("h1", {}, t("Account")));
  container.appendChild(
    el(
      "p",
      { class: "subtitle" },
      t("Sign in with your email and password, or create a new driver account below.")
    )
  );

  container.appendChild(renderLoginCard());

  // --- sign up (V1.0): one form — name, email, password, minimum age, terms ---
  container.appendChild(renderSignupCard());

  // --- restore session ---
  const restoreCard = el("div", { class: "card" });
  restoreCard.appendChild(el("h2", {}, t("Already have a token?")));
  const tokenField = el("div", { class: "field" }, [
    el("label", { for: "authToken" }, t("Auth token")),
    el("input", { id: "authToken", type: "password", placeholder: t("paste your saved auth_token") }),
  ]);
  const restoreBtn = el("button", { class: "btn btn--ghost" }, t("Restore session"));
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
      restoreOut.appendChild(el("div", { class: "notice notice--success" }, t("Session restored.")));
    } catch (err) {
      store.signOut();
      restoreOut.appendChild(
        el("div", { class: "notice notice--error" }, err instanceof ApiError ? err.message : t("Could not verify that token."))
      );
    } finally {
      restoreBtn.disabled = false;
    }
  });

  restoreCard.appendChild(tokenField);
  restoreCard.appendChild(restoreBtn);
  restoreCard.appendChild(restoreOut);

  // The old way in (a desktop-client token pasted into the browser) — folded away, still there
  // for drivers from before V1.0 who never set a password.
  container.appendChild(el("details", { class: "card advanced-signin" }, [
    el("summary", {}, t("Advanced: sign in with a saved auth token")),
    restoreCard,
  ]));
}

// "I accept the {0} and have read the {1}." -> text with the two links in place (the word order
// differs between the languages, so the sentence is translated whole)
function withLinks(sentence, links) {
  return sentence.split(/(\{\d+\})/).filter(Boolean).map((part) => {
    const m = /^\{(\d+)\}$/.exec(part);
    return m && links[Number(m[1])] ? links[Number(m[1])] : part;
  });
}

function checkbox(id, labelChildren) {
  return el("label", { class: "check-row", for: id }, [
    el("input", { id, type: "checkbox" }),
    el("span", {}, labelChildren),
  ]);
}

function renderSignupCard() {
  const card = el("div", { class: "card signup-card" });
  card.appendChild(el("h2", {}, t("New driver")));
  card.appendChild(el("p", { class: "subtitle" },
    t("Create your Garage16 account. You use the same email and password in the desktop client — nothing to copy.")));

  const field = (id, label, attrs = {}) => el("div", { class: "field" }, [
    el("label", { for: id }, label),
    el("input", { id, ...attrs }),
  ]);
  const nameField = field("displayName", t("Driver name (shown in the leaderboards)"), { type: "text", maxlength: "60", autocomplete: "nickname", placeholder: t("e.g. Mika H.") });
  const emailField = field("signupEmail", t("Email"), { type: "email", maxlength: "254", autocomplete: "email" });
  const pwField = field("signupPw", t("Password (at least 10 characters)"), { type: "password", maxlength: "200", autocomplete: "new-password" });
  const pw2Field = field("signupPw2", t("Repeat password"), { type: "password", maxlength: "200", autocomplete: "new-password" });
  const secretField = field("regSecret", t("Registration secret"), { type: "password", placeholder: t("from whoever runs this server") });
  secretField.hidden = true;

  const ageText = el("span", {}, t("I am at least {0} years old.", [16]));
  const ageBox = el("label", { class: "check-row", for: "signupAge" }, [el("input", { id: "signupAge", type: "checkbox" }), ageText]);
  const termsBox = checkbox("signupTerms", withLinks(t("I accept the {0} and have read the {1}."), [
    el("a", { href: "#/legal?tab=terms", target: "_blank", rel: "noopener" }, t("terms of use")),
    el("a", { href: "#/legal?tab=privacy", target: "_blank", rel: "noopener" }, t("privacy policy")),
  ]));

  const button = el("button", { class: "btn" }, t("Create account"));
  const out = el("div");

  // what this server expects: open sign-up, or a registration secret; and the minimum age
  api.legal().then((info) => {
    if (info.registration === "secret") secretField.hidden = false;
    if (info.min_age) ageText.textContent = t("I am at least {0} years old.", [info.min_age]);
  }).catch(() => { /* the form works without it; the server says what is missing */ });

  const value = (f) => f.querySelector("input").value;
  button.addEventListener("click", async () => {
    out.innerHTML = "";
    const problem = (msg) => out.appendChild(el("div", { class: "notice notice--error" }, msg));
    const displayName = value(nameField).trim();
    const email = value(emailField).trim();
    const password = value(pwField);
    if (!displayName) return problem(t("Please enter a driver name."));
    if (!email || !email.includes("@")) return problem(t("Please enter your email address."));
    if (password.length < 10) return problem(t("The password needs at least 10 characters."));
    if (password !== value(pw2Field)) return problem(t("The two passwords are not the same."));
    if (!ageBox.querySelector("input").checked) return problem(t("Please confirm your age."));
    if (!termsBox.querySelector("input").checked) return problem(t("Please accept the terms of use and the privacy policy."));
    button.disabled = true;
    try {
      const res = await api.signup({
        displayName, email, password, acceptTerms: true, confirmMinAge: true,
        registrationSecret: secretField.hidden ? undefined : value(secretField).trim() || undefined,
      });
      store.setSession({ driverId: res.driver_id, displayName: res.display_name, viaCookie: true });
      window.location.hash = "#/setup?welcome=1";
    } catch (err) {
      problem(err.message);
    } finally {
      button.disabled = false;
    }
  });

  [nameField, emailField, pwField, pw2Field, secretField, ageBox, termsBox, button, out].forEach((n) => card.appendChild(n));
  return card;
}
