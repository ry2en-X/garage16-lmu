// state.js — small pub/sub store for the signed-in driver's session.
//
// Two distinct session shapes (V0.7.2 — see server/sessions.py):
//   - Cookie session (email+password login): { driverId, displayName,
//     viaCookie: true }. No secret is stored here at all — the actual
//     credential is an HttpOnly cookie the browser holds and JS can't
//     read, which is the whole point (immune to XSS token theft).
//   - Bearer-token session (legacy "paste my auth_token" restore, or
//     right after registering a fresh token-only driver): { driverId,
//     displayName, authToken }. This is the desktop client's own
//     credential being reused to drive the web app directly — still
//     supported for drivers who haven't set a password yet, but this
//     value in localStorage IS the thing V0.7.2 is moving away from as
//     the default path.
//
// Persisted to localStorage so a refresh doesn't sign the driver out
// (for the cookie case, only the non-secret display bits are persisted;
// the browser's own cookie jar is what actually keeps them signed in).

const STORAGE_KEY = "lmu_garage_session";

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function persist(session) {
  if (session) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
}

let session = load();
const listeners = new Set();

function notify() {
  listeners.forEach((fn) => fn(session));
}

export const store = {
  getSession() {
    return session;
  },
  isSignedIn() {
    return !!session?.authToken || !!session?.viaCookie;
  },
  setSession(next) {
    session = next;
    persist(session);
    notify();
  },
  signOut() {
    session = null;
    persist(null);
    notify();
  },
  subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
};
