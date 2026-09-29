// admin_store.js — holds the shared operator admin token (V0.7.2 §15),
// completely separate from state.js's driver session store. Uses
// sessionStorage rather than localStorage on purpose: this is a
// high-privilege SHARED secret (server/routers/admin.py's single
// LMU_GARAGE_ADMIN_TOKEN), not a per-driver credential — clearing it
// when the tab closes is the safer default for something this powerful.

const KEY = "lmu_garage_admin_token";

export const adminStore = {
  getToken() {
    try {
      return sessionStorage.getItem(KEY);
    } catch {
      return null;
    }
  },
  setToken(token) {
    try {
      sessionStorage.setItem(KEY, token);
    } catch {
      /* sessionStorage unavailable (e.g. private browsing) — admin UI just won't persist across reloads */
    }
  },
  clear() {
    try {
      sessionStorage.removeItem(KEY);
    } catch {
      /* nothing to do */
    }
  },
};
