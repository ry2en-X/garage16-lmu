// pages/verify_email.js — the landing page for the link in a
// verification email (#/verify-email?token=...). See
// server/routers/accounts.py's confirm_email_verification (V0.8-NAS §2).

import { api, ApiError } from "../api.js";
import { el } from "../format.js";

export async function renderVerifyEmail(container, token) {
  container.appendChild(el("h1", {}, "Verify your email"));

  if (!token) {
    container.appendChild(
      el("div", { class: "notice notice--error" }, "No verification token found in this link.")
    );
    return;
  }

  const card = el("div", { class: "card" });
  try {
    await api.confirmEmailVerification(token);
    card.appendChild(el("div", { class: "notice notice--success" }, "Email verified. Thanks!"));
    card.appendChild(
      el("a", { href: "#/account", class: "btn", style: "margin-top:12px;display:inline-block;" }, "Go to Account")
    );
  } catch (err) {
    card.appendChild(
      el("div", { class: "notice notice--error" }, err instanceof ApiError ? err.message : "Something went wrong.")
    );
  }
  container.appendChild(card);
}
