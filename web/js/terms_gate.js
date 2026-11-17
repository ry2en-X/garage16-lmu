// terms_gate.js — V1.0.1: a signed-in driver who has not accepted the CURRENT terms of use and
// privacy policy (accounts from before V1.0, or everybody after the texts changed) is asked once,
// right after signing in or opening the site. GET /accounts/me says terms_required; accepting calls
// POST /accounts/accept-terms. Until then the dialog stays — the way out is signing out.

import { api } from "./api.js";
import { store } from "./state.js";
import { el } from "./format.js";
import { t } from "./i18n.js";

let dialogOpen = false;
let checkedFor = null;

function sessionKey() {
  const session = store.getSession();
  return session ? `${session.driverId ?? "?"}:${session.viaCookie ? "cookie" : "token"}` : null;
}

export async function checkTerms() {
  const key = sessionKey();
  if (!key || !store.isSignedIn()) {
    checkedFor = null;
    return;
  }
  if (dialogOpen || checkedFor === key) return;
  checkedFor = key;
  let who;
  try {
    who = await api.whoami();
  } catch {
    checkedFor = null;     // ask again next time; never block the page on a failed request
    return;
  }
  if (who && who.terms_required) showTermsDialog();
}

export function showTermsDialog(parent = document.body) {
  dialogOpen = true;
  const overlay = el("div", { class: "lang-overlay terms-overlay", "data-terms-dialog": "1" });
  const ageBox = el("input", { id: "termsAge", type: "checkbox" });
  const termsBox = el("input", { id: "termsAccept", type: "checkbox" });
  const out = el("div");
  const accept = el("button", { class: "btn", type: "button" }, t("Accept and continue"));
  const signOut = el("button", { class: "btn btn--ghost", type: "button" }, t("Sign out"));
  const link = (tab, label) => el("a", { href: `#/legal?tab=${tab}`, target: "_blank", rel: "noopener" }, label);

  accept.addEventListener("click", async () => {
    out.innerHTML = "";
    if (!ageBox.checked || !termsBox.checked) {
      out.appendChild(el("div", { class: "notice notice--error" }, t("Please tick both boxes.")));
      return;
    }
    accept.disabled = true;
    try {
      await api.acceptTerms();
      overlay.remove();
      dialogOpen = false;
    } catch (err) {
      out.appendChild(el("div", { class: "notice notice--error" }, err.message));
      accept.disabled = false;
    }
  });
  signOut.addEventListener("click", async () => {
    try {
      await api.logout();
    } catch {
      /* sign out locally regardless */
    }
    overlay.remove();
    dialogOpen = false;
    store.signOut();
    window.location.hash = "#/";
  });

  const box = el("div", { class: "lang-popup terms-popup", role: "dialog", "aria-modal": "true", "aria-labelledby": "termsTitle" }, [
    el("h2", { id: "termsTitle" }, t("Terms of use and privacy policy")),
    el("p", {}, t("To keep using Garage16, please read and accept the current terms of use and privacy policy.")),
    el("p", {}, [link("terms", t("terms of use")), " · ", link("privacy", t("privacy policy"))]),
    el("label", { class: "check-row", for: "termsAge" }, [ageBox, el("span", {}, t("I am at least {0} years old.", [16]))]),
    el("label", { class: "check-row", for: "termsAccept" }, [termsBox, el("span", {}, t("I accept the terms of use and have read the privacy policy."))]),
    out,
    el("div", { class: "lang-popup__buttons" }, [accept, signOut]),
  ]);
  overlay.appendChild(box);
  parent.appendChild(overlay);
  // the real minimum age of this server
  api.legal().then((info) => {
    if (info && info.min_age) box.querySelector("label[for=termsAge] span").textContent = t("I am at least {0} years old.", [info.min_age]);
  }).catch(() => {});
  return overlay;
}
