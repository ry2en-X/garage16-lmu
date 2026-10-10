// pages/reset_password.js — the landing page for the link in a
// password-reset email (#/reset-password?token=...). See
// server/routers/accounts.py's confirm_password_reset.

import { api, ApiError } from "../api.js";
import { el } from "../format.js";

import { t } from "../i18n.js";
export async function renderResetPassword(container, token) {
  container.appendChild(el("h1", {}, t("Reset your password")));

  if (!token) {
    container.appendChild(
      el(
        "div",
        { class: "notice notice--error" },
        t("No reset token found in this link. Request a new one from the Account page.")
      )
    );
    return;
  }

  container.appendChild(
    el("p", { class: "subtitle" }, t("Choose a new password for your Garage16 account."))
  );

  const card = el("div", { class: "card" });
  const pwField = el("div", { class: "field" }, [
    el("label", { for: "newPw" }, t("New password")),
    el("input", { id: "newPw", type: "password", autocomplete: "new-password" }),
  ]);
  const pwConfirmField = el("div", { class: "field" }, [
    el("label", { for: "newPwConfirm" }, t("Confirm new password")),
    el("input", { id: "newPwConfirm", type: "password", autocomplete: "new-password" }),
  ]);
  const submitBtn = el("button", { class: "btn" }, t("Set new password"));
  const out = el("div");

  submitBtn.addEventListener("click", async () => {
    const pw = pwField.querySelector("input").value;
    const pwConfirm = pwConfirmField.querySelector("input").value;
    out.innerHTML = "";
    if (pw !== pwConfirm) {
      out.appendChild(el("div", { class: "notice notice--error" }, t("Passwords don't match.")));
      return;
    }
    submitBtn.disabled = true;
    try {
      await api.confirmPasswordReset(token, pw);
      out.appendChild(
        el(
          "div",
          { class: "notice notice--success" },
          t("Password updated. You can sign in with it now.")
        )
      );
      const link = el("a", { href: "#/account", class: "btn", style: "margin-top:12px;display:inline-block;" }, t("Go to sign in"));
      out.appendChild(link);
    } catch (err) {
      out.appendChild(
        el("div", { class: "notice notice--error" }, err instanceof ApiError ? err.message : t("Something went wrong."))
      );
    } finally {
      submitBtn.disabled = false;
    }
  });

  card.appendChild(pwField);
  card.appendChild(pwConfirmField);
  card.appendChild(submitBtn);
  card.appendChild(out);
  container.appendChild(card);
}
