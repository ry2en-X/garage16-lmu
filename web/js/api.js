// api.js — thin wrapper around the LMU Garage backend (server/routers/*).
//
// The API base URL comes from window.LMU_GARAGE_API_BASE_URL, set by
// config.js (loaded before this module — see index.html). Two cases:
//   - Not set at all (config.js missing, e.g. running api.js directly
//     without the rest of the page during dev): falls back to
//     http://localhost:8000, the local dev server.
//   - Explicitly set (including to "" for same-origin deployments, where
//     Caddy serves both the web/ files and the API on one domain via
//     path-based routing — see docker/Caddyfile): that value is used
//     as-is. `""` must NOT fall through to the dev default here — a
//     naive `window.X || default` would treat an intentional empty
//     string the same as "unset", which is exactly wrong for the
//     same-origin production case.
import { store } from "./state.js";

export const API_BASE_URL =
  typeof window.LMU_GARAGE_API_BASE_URL === "string"
    ? window.LMU_GARAGE_API_BASE_URL
    : "http://localhost:8000";

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, { method = "GET", params, body, auth = false, adminToken, headers: extraHeaders } = {}) {
  let url = `${API_BASE_URL}${path}`;
  if (params) {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
    ).toString();
    if (qs) url += `?${qs}`;
  }

  const headers = { "Content-Type": "application/json", ...extraHeaders };
  if (auth) {
    const session = store.getSession();
    if (session?.authToken) {
      // Legacy path: a pasted-in desktop-client auth_token driving the
      // web app directly (see state.js). Explicit header, as before.
      headers.Authorization = `Bearer ${session.authToken}`;
    } else if (!session?.viaCookie) {
      // Neither a bearer token nor a cookie session — genuinely signed out.
      throw new ApiError("Not signed in.", 401);
    }
    // else: cookie session (V0.7.2) — no header to add, the browser
    // attaches the HttpOnly session cookie automatically as long as
    // `credentials: "include"` is set below.
  }
  if (adminToken) {
    // V0.7.2 admin UI: a completely separate credential domain from
    // driver auth (see server/routers/admin.py) — never mixed with the
    // Authorization header/cookie above, and never causes
    // store.signOut() below (that would sign out this browser's driver
    // session, which has nothing to do with the admin token being wrong).
    headers["X-Admin-Token"] = adminToken;
  }

  let res;
  try {
    res = await fetch(url, {
      method,
      headers,
      credentials: "include",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    throw new ApiError(
      `Could not reach the backend at ${API_BASE_URL}. Is it running, and is CORS enabled? (${err.message})`,
      0
    );
  }

  if (res.status === 401 && !adminToken) {
    store.signOut();
    throw new ApiError("Session expired — sign in again.", 401);
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch {
      /* body wasn't JSON */
    }
    throw new ApiError(detail, res.status);
  }

  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  registerDriver(displayName, registrationSecret) {
    // accounts.py takes display_name as a query param, not a JSON body.
    // registrationSecret is only required if the backend has
    // LMU_GARAGE_REGISTRATION_SECRET set (see server/config.py) — omit it
    // otherwise.
    const headers = registrationSecret ? { "X-Registration-Secret": registrationSecret } : undefined;
    return request("/accounts/register", {
      method: "POST",
      params: { display_name: displayName },
      headers,
    });
  },

  rotateToken() {
    return request("/accounts/rotate-token", { method: "POST", auth: true });
  },

  rotateSecret() {
    return request("/accounts/rotate-secret", { method: "POST", auth: true });
  },

  revokeSession() {
    return request("/accounts/revoke", { method: "POST", auth: true });
  },

  // Self-service DSGVO/GDPR export (Art. 15/20) — server/routers/accounts.py
  // has had this endpoint since before V0.6.8; it was just never linked
  // from the UI until now (V0.6.9).
  exportMyData() {
    return request("/accounts/me/export", { auth: true });
  },

  // Self-service full account deletion (driver, laps, telemetry files,
  // team memberships, Discord link — see accounts.py's docstring for the
  // exact cascade). Same story as exportMyData: backend existed, UI didn't.
  deleteMyAccount() {
    return request("/accounts/me", { method: "DELETE", auth: true });
  },

  // --- V0.6.9: email/password ---

  setPassword({ email, newPassword, currentPassword }) {
    return request("/accounts/set-password", {
      method: "POST",
      auth: true,
      body: { email, new_password: newPassword, current_password: currentPassword || undefined },
    });
  },

  login(email, password) {
    return request("/accounts/login", { method: "POST", body: { email, password } });
  },

  logout() {
    return request("/accounts/logout", { method: "POST" });
  },

  logoutAllDevices() {
    return request("/accounts/logout-all", { method: "POST", auth: true });
  },

  mySessions() {
    return request("/accounts/sessions", { auth: true });
  },

  revokeSessionById(sessionId) {
    return request(`/accounts/sessions/${sessionId}`, { method: "DELETE", auth: true });
  },

  changePassword(currentPassword, newPassword) {
    return request("/accounts/change-password", {
      method: "POST",
      auth: true,
      body: { current_password: currentPassword, new_password: newPassword },
    });
  },

  requestPasswordReset(email) {
    return request("/accounts/password-reset/request", { method: "POST", body: { email } });
  },

  confirmPasswordReset(token, newPassword) {
    return request("/accounts/password-reset/confirm", {
      method: "POST",
      body: { token, new_password: newPassword },
    });
  },

  classLeaderboard(trackName, carClass, limit = 20) {
    return request(`/leaderboard/class/${encodeURIComponent(trackName)}/${encodeURIComponent(carClass)}`, {
      params: { limit },
    });
  },

  carLeaderboard(trackName, carModel, limit = 20) {
    return request(`/leaderboard/car/${encodeURIComponent(trackName)}/${encodeURIComponent(carModel)}`, {
      params: { limit },
    });
  },

  // Real track/class/car names, discovered from actual uploads — see
  // server/routers/leaderboard.py's catalog endpoints. No manual list to
  // maintain: a new car/track appears the moment someone's first lap on
  // it lands.
  // V0.8.9: every track grouped by venue with all layouts (searchable), and
  // every class incl. ones nobody has driven yet. See server/catalog.py.
  catalogVenues(q) {
    return request("/leaderboard/catalog/venues", { params: { q } });
  },

  catalogClassOptions(trackName) {
    return request("/leaderboard/catalog/class-options", { params: { track_name: trackName } });
  },

  catalogTracks() {
    return request("/leaderboard/catalog/tracks");
  },

  catalogClasses(trackName) {
    return request("/leaderboard/catalog/classes", { params: { track_name: trackName } });
  },

  catalogCars(trackName, carClass) {
    return request("/leaderboard/catalog/cars", { params: { track_name: trackName, car_class: carClass } });
  },

  myLaps({ trackName, carName, limit = 100 } = {}) {
    return request("/telemetry/laps", {
      auth: true,
      params: { track_name: trackName, car_name: carName, limit },
    });
  },

  myTeams() {
    return request("/teams/mine", { auth: true });
  },

  createTeam(name) {
    return request("/teams", { method: "POST", auth: true, body: { name } });
  },

  joinTeam(inviteCode) {
    return request("/teams/join", { method: "POST", auth: true, body: { invite_code: inviteCode } });
  },

  teamDetail(teamId) {
    return request(`/teams/${teamId}`, { auth: true });
  },

  updateTeam(teamId, fields) {
    return request(`/teams/${teamId}`, { method: "PATCH", auth: true, body: fields });
  },

  deleteTeam(teamId) {
    return request(`/teams/${teamId}`, { method: "DELETE", auth: true });
  },

  teamMembers(teamId) {
    return request(`/teams/${teamId}/members`, { auth: true });
  },

  removeTeamMember(teamId, driverId) {
    return request(`/teams/${teamId}/members/${driverId}`, { method: "DELETE", auth: true });
  },

  changeTeamMemberRole(teamId, driverId, role) {
    return request(`/teams/${teamId}/members/${driverId}/role`, { method: "POST", auth: true, body: { role } });
  },

  transferTeamOwnership(teamId, newOwnerDriverId) {
    return request(`/teams/${teamId}/transfer-ownership`, {
      method: "POST", auth: true, body: { new_owner_driver_id: newOwnerDriverId },
    });
  },

  teamClassLeaderboard(teamId, trackName, carClass, limit = 20) {
    return request(`/teams/${teamId}/leaderboard/class/${encodeURIComponent(trackName)}/${encodeURIComponent(carClass)}`, {
      auth: true, params: { limit },
    });
  },

  teamCarLeaderboard(teamId, trackName, carModel, limit = 20) {
    return request(`/teams/${teamId}/leaderboard/car/${encodeURIComponent(trackName)}/${encodeURIComponent(carModel)}`, {
      auth: true, params: { limit },
    });
  },

  teamStats(teamId, window = "all") {
    return request(`/teams/${teamId}/stats`, { auth: true, params: { window } });
  },

  // --- V0.7.2 §9.2: real team invitations ---

  lookupDriverByName(displayName) {
    return request("/drivers/lookup", { auth: true, params: { display_name: displayName } });
  },

  createTeamInvitation(teamId, invitedDriverId) {
    return request(`/teams/${teamId}/invitations`, {
      method: "POST", auth: true, body: { invited_driver_id: invitedDriverId },
    });
  },

  teamInvitations(teamId) {
    return request(`/teams/${teamId}/invitations`, { auth: true });
  },

  revokeTeamInvitation(teamId, invitationId) {
    return request(`/teams/${teamId}/invitations/${invitationId}/revoke`, { method: "POST", auth: true });
  },

  resendTeamInvitation(teamId, invitationId) {
    return request(`/teams/${teamId}/invitations/${invitationId}/resend`, { method: "POST", auth: true });
  },

  myInvitations() {
    return request("/invitations/mine", { auth: true });
  },

  driverProfile(driverId) {
    return request(`/drivers/${driverId}/public`);
  },

  whoami() {
    return request("/accounts/me", { auth: true });
  },

  updateDisplayName(displayName) {
    return request("/accounts/me", { method: "PATCH", auth: true, body: { display_name: displayName } });
  },

  resendVerificationEmail() {
    return request("/accounts/verify-email/resend", { method: "POST", auth: true });
  },

  confirmEmailVerification(token) {
    return request("/accounts/verify-email/confirm", { method: "POST", body: { token } });
  },

  acceptInvitation(invitationId) {
    return request(`/invitations/${invitationId}/accept`, { method: "POST", auth: true });
  },

  declineInvitation(invitationId) {
    return request(`/invitations/${invitationId}/decline`, { method: "POST", auth: true });
  },

  teamRecords(teamId) {
    return request(`/teams/${teamId}/records`, { auth: true });
  },

  teamActivity(teamId, limit = 30) {
    return request(`/teams/${teamId}/activity`, { auth: true, params: { limit } });
  },

  // V0.8.7: Discord account link — status, one-time link code, unlink.
  // (The old POST /teams/discord-link-code path still exists server-side
  // as a deprecated alias; nothing here uses it any more.)
  discordStatus() {
    return request("/accounts/discord", { auth: true });
  },

  createDiscordLinkCode() {
    return request("/accounts/discord/link-code", { method: "POST", auth: true });
  },

  unlinkDiscord() {
    return request("/accounts/discord", { method: "DELETE", auth: true });
  },

  // --- V0.7.2 §15: admin UI (separate credential domain, see admin_store.js) ---

  adminListReports(adminToken, unresolvedOnly = true) {
    return request("/admin/reports", { adminToken, params: { unresolved_only: unresolvedOnly } });
  },

  adminResolveReport(adminToken, reportId, resolution) {
    return request(`/admin/reports/${reportId}/resolve`, { method: "POST", adminToken, params: { resolution } });
  },

  adminGetLap(adminToken, lapId) {
    return request(`/admin/laps/${lapId}`, { adminToken });
  },

  adminInvalidateLap(adminToken, lapId, reason) {
    return request(`/admin/laps/${lapId}/invalidate`, { method: "PATCH", adminToken, body: { reason } });
  },

  adminListDrivers(adminToken, search) {
    return request("/admin/drivers", { adminToken, params: { search } });
  },

  adminLockDriver(adminToken, driverId, reason) {
    return request(`/admin/drivers/${driverId}/lock`, { method: "POST", adminToken, body: { reason } });
  },

  adminUnlockDriver(adminToken, driverId) {
    return request(`/admin/drivers/${driverId}/unlock`, { method: "POST", adminToken });
  },

  health() {
    return request("/health");
  },

  recentActivity(limit = 15) {
    return request("/leaderboard/recent", { params: { limit } });
  },
};

export { ApiError };
