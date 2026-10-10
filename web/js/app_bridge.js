// app_bridge.js — the web app inside the desktop client's analysis window (V0.9.2).
//
// The "Client + analysis app" edition shows this same web app in its own window
// (client/analysis_window.py, pywebview). There the page finds window.pywebview.api, a
// bridge to the desktop client. When the page is not signed in, it asks the bridge for a
// web session (the client gets one from the server with its own credential) and turns it
// into the usual HttpOnly cookie (POST /accounts/app-session/activate). The token passes
// through this code once and is never stored by it.
//
// In a normal browser there is no window.pywebview and nothing here does anything: no
// waiting, no requests. The bridge announces itself with the "pywebviewready" event,
// which may come after the first render — the page then re-renders signed in.

import { api as defaultApi } from "./api.js";
import { store as defaultStore } from "./state.js";

const RETRY_PAUSE_MS = 60_000; // after a failed sign-in, do not ask again for a minute

export function bridgeApi(win = typeof window !== "undefined" ? window : undefined) {
  const bridge = win && win.pywebview && win.pywebview.api;
  return bridge && typeof bridge.app_session === "function" ? bridge : null;
}

export function inDesktopApp(win = typeof window !== "undefined" ? window : undefined) {
  return !!(win && win.pywebview);
}

/**
 * Starts watching for the desktop bridge. Signs in once it is there and the page is
 * signed out — at start, and again whenever the session ends (e.g. it expired after a
 * month). onSignedIn() runs after a successful sign-in (the router re-renders).
 * Returns a function that stops watching (tests).
 */
export function startDesktopAppBridge({ win = window, api = defaultApi, store = defaultStore, onSignedIn = () => {},
  onError = () => {}, now = () => Date.now() } = {}) {
  let busy = false;
  let pausedUntil = 0;

  async function signIn() {
    const bridge = bridgeApi(win);
    if (!bridge || busy || store.isSignedIn() || now() < pausedUntil) return false;
    busy = true;
    try {
      const answer = await bridge.app_session();
      if (!answer || answer.error || !answer.session_token) {
        throw new Error((answer && answer.error) || "No answer from the Garage16 app.");
      }
      const me = await api.activateAppSession(answer.session_token);
      store.setSession({ driverId: me.driver_id, displayName: me.display_name, viaCookie: true });
      onSignedIn();
      return true;
    } catch (err) {
      pausedUntil = now() + RETRY_PAUSE_MS;
      onError(err);
      return false;
    } finally {
      busy = false;
    }
  }

  const onReady = () => { signIn(); };
  win.addEventListener("pywebviewready", onReady);
  const unsubscribe = store.subscribe((session) => {
    if (!session) signIn();
  });
  if (bridgeApi(win)) signIn();

  return () => {
    win.removeEventListener("pywebviewready", onReady);
    unsubscribe();
  };
}
