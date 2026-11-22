// api.js — thin wrapper around the LMU Garage backend (server/api/v1/*).
//
// The API base URL comes from window.GARAGE16_API_BASE_URL, set by
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

import { t } from "./i18n.js";
// GARAGE16_API_BASE_URL; config.js files of older deployments still set GARAGE16_API_BASE_URL
const configured = typeof window.GARAGE16_API_BASE_URL === "string" ? window.GARAGE16_API_BASE_URL : window.GARAGE16_API_BASE_URL;
export const API_BASE_URL = typeof configured === "string" ? configured : "http://localhost:8000";

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
      throw new ApiError(t("Not signed in."), 401);
    }
    // else: cookie session — no header to add, the browser
    // attaches the HttpOnly session cookie automatically as long as
    // `credentials: "include"` is set below.
  }
  if (adminToken) {
    // V0.7.2 admin UI: a completely separate credential domain from
    // driver auth (see server/api/v1/admin.py) — never mixed with the
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
      t("Could not reach the backend at {0}. Is it running, and is CORS enabled? ({1})", [API_BASE_URL, err.message]),
      0
    );
  }

  if (res.status === 401 && !adminToken) {
    store.signOut();
    throw new ApiError(t("Session expired — sign in again."), 401);
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || detail;
      // FastAPI's validation errors are a list of {msg, ...}
      if (Array.isArray(detail)) detail = detail.map((d) => (d && d.msg) || String(d)).join("; ");
      // Some answers are {code, message} (maintenance, desktop sign-in rules)
      else if (detail && typeof detail === "object") detail = detail.message || detail.code || res.statusText;
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
    // GARAGE16_REGISTRATION_SECRET set (see server/core/config.py) — omit it
    // otherwise.
    const headers = registrationSecret ? { "X-Registration-Secret": registrationSecret } : undefined;
    return request("/api/v1/accounts/register", {
      method: "POST",
      params: { display_name: displayName },
      headers,
    });
  },

  rotateToken() {
    return request("/api/v1/accounts/rotate-token", { method: "POST", auth: true });
  },

  rotateSecret() {
    return request("/api/v1/accounts/rotate-secret", { method: "POST", auth: true });
  },

  revokeSession() {
    return request("/api/v1/accounts/revoke", { method: "POST", auth: true });
  },

  // Self-service DSGVO/GDPR export (Art. 15/20) — server/api/v1/accounts.py
  // has had this endpoint since before V0.6.8; it was just never linked
  // from the UI until now.
  exportMyData() {
    return request("/api/v1/accounts/me/export", { auth: true });
  },

  // Self-service full account deletion (driver, laps, telemetry files,
  // team memberships, Discord link — see accounts.py's docstring for the
  // exact cascade). Same story as exportMyData: backend existed, UI didn't.
  deleteMyAccount() {
    return request("/api/v1/accounts/me", { method: "DELETE", auth: true });
  },

  // --- V0.6.9: email/password ---

  setPassword({ email, newPassword, currentPassword }) {
    return request("/api/v1/accounts/set-password", {
      method: "POST",
      auth: true,
      body: { email, new_password: newPassword, current_password: currentPassword || undefined },
    });
  },

  // The one-step sign-up form (name, email, password, terms, minimum age).
  // Answers {status: "check_email"} for every address (no hint whether it already had an
  // account) and does NOT sign the browser in — confirm the email, then sign in.
  // registrationSecret only if the server is not open.
  signup({ displayName, email, password, acceptTerms, confirmMinAge, registrationSecret }) {
    const headers = registrationSecret ? { "X-Registration-Secret": registrationSecret } : undefined;
    return request("/api/v1/accounts/signup", {
      method: "POST",
      headers,
      body: { display_name: displayName, email, password, accept_terms: !!acceptTerms, confirm_min_age: !!confirmMinAge },
    });
  },

  // Accept the current terms (accounts from before V1.0, or after the texts changed)
  acceptTerms() {
    return request("/api/v1/accounts/accept-terms", { method: "POST", auth: true, body: { accept_terms: true, confirm_min_age: true } });
  },

  // The PCs connected with this account (desktop client), and disconnecting one
  myDevices() {
    return request("/api/v1/accounts/devices", { auth: true });
  },

  revokeDevice(deviceId) {
    return request(`/api/v1/accounts/devices/${deviceId}`, { method: "DELETE", auth: true });
  },

  // Operator details, minimum age and registration mode for the legal pages
  // Readable links — the driver / team a /driver/<name> or /team/<name> address names
  driverBySlug(slug) {
    return request(`/api/v1/public/drivers/by-slug/${encodeURIComponent(slug)}`);
  },

  teamBySlug(slug) {
    return request(`/api/v1/public/teams/by-slug/${encodeURIComponent(slug)}`);
  },

  legal() {
    return request("/api/v1/public/legal");
  },

  login(email, password) {
    return request("/api/v1/accounts/login", { method: "POST", body: { email, password } });
  },

  logout() {
    return request("/api/v1/accounts/logout", { method: "POST" });
  },

  logoutAllDevices() {
    return request("/api/v1/accounts/logout-all", { method: "POST", auth: true });
  },

  mySessions() {
    return request("/api/v1/accounts/sessions", { auth: true });
  },

  revokeSessionById(sessionId) {
    return request(`/api/v1/accounts/sessions/${sessionId}`, { method: "DELETE", auth: true });
  },

  changePassword(currentPassword, newPassword) {
    return request("/api/v1/accounts/change-password", {
      method: "POST",
      auth: true,
      body: { current_password: currentPassword, new_password: newPassword },
    });
  },

  requestPasswordReset(email) {
    return request("/api/v1/accounts/password-reset/request", { method: "POST", body: { email } });
  },

  confirmPasswordReset(token, newPassword) {
    return request("/api/v1/accounts/password-reset/confirm", {
      method: "POST",
      body: { token, new_password: newPassword },
    });
  },

  classLeaderboard(trackName, carClass, limit = 20) {
    return request(`/api/v1/public/leaderboards/class/${encodeURIComponent(trackName)}/${encodeURIComponent(carClass)}`, {
      params: { limit },
    });
  },

  carLeaderboard(trackName, carModel, limit = 20) {
    return request(`/api/v1/public/leaderboards/car/${encodeURIComponent(trackName)}/${encodeURIComponent(carModel)}`, {
      params: { limit },
    });
  },

  // Real track/class/car names, discovered from actual uploads — see
  // server/api/v1/leaderboard.py's catalog endpoints. No manual list to
  // maintain: a new car/track appears the moment someone's first lap on
  // it lands.
  // Every track grouped by venue with all layouts (searchable), and
  // every class incl. ones nobody has driven yet. See server/catalog/catalog.py.
  catalogVenues(q) {
    return request("/api/v1/public/catalog/venues", { params: { q } });
  },

  catalogClassOptions(trackName) {
    return request("/api/v1/public/catalog/class-options", { params: { track_name: trackName } });
  },

  catalogTracks() {
    return request("/api/v1/public/catalog/tracks");
  },

  catalogClasses(trackName) {
    return request("/api/v1/public/catalog/classes", { params: { track_name: trackName } });
  },

  catalogCars(trackName, carClass) {
    return request("/api/v1/public/catalog/cars", { params: { track_name: trackName, car_class: carClass } });
  },

  myLaps({ trackName, carName, limit = 100 } = {}) {
    return request("/api/v1/laps", {
      auth: true,
      params: { track_name: trackName, car_name: carName, limit },
    });
  },

  myTeams() {
    return request("/api/v1/teams/mine", { auth: true });
  },

  createTeam(name) {
    return request("/api/v1/teams", { method: "POST", auth: true, body: { name } });
  },

  joinTeam(inviteCode) {
    return request("/api/v1/teams/join", { method: "POST", auth: true, body: { invite_code: inviteCode } });
  },

  teamDetail(teamId) {
    return request(`/api/v1/teams/${teamId}`, { auth: true });
  },

  updateTeam(teamId, fields) {
    return request(`/api/v1/teams/${teamId}`, { method: "PATCH", auth: true, body: fields });
  },

  deleteTeam(teamId) {
    return request(`/api/v1/teams/${teamId}`, { method: "DELETE", auth: true });
  },

  teamMembers(teamId) {
    return request(`/api/v1/teams/${teamId}/members`, { auth: true });
  },

  removeTeamMember(teamId, driverId) {
    return request(`/api/v1/teams/${teamId}/members/${driverId}`, { method: "DELETE", auth: true });
  },

  changeTeamMemberRole(teamId, driverId, role) {
    return request(`/api/v1/teams/${teamId}/members/${driverId}/role`, { method: "POST", auth: true, body: { role } });
  },

  transferTeamOwnership(teamId, newOwnerDriverId) {
    return request(`/api/v1/teams/${teamId}/transfer-ownership`, {
      method: "POST", auth: true, body: { new_owner_driver_id: newOwnerDriverId },
    });
  },

  teamClassLeaderboard(teamId, trackName, carClass, limit = 20) {
    return request(`/api/v1/teams/${teamId}/leaderboard/class/${encodeURIComponent(trackName)}/${encodeURIComponent(carClass)}`, {
      auth: true, params: { limit },
    });
  },

  teamCarLeaderboard(teamId, trackName, carModel, limit = 20) {
    return request(`/api/v1/teams/${teamId}/leaderboard/car/${encodeURIComponent(trackName)}/${encodeURIComponent(carModel)}`, {
      auth: true, params: { limit },
    });
  },

  teamStats(teamId, window = "all") {
    return request(`/api/v1/teams/${teamId}/stats`, { auth: true, params: { window } });
  },

  // --- V0.7.2 §9.2: real team invitations ---

  lookupDriverByName(displayName) {
    return request("/api/v1/drivers/lookup", { auth: true, params: { display_name: displayName } });
  },

  createTeamInvitation(teamId, invitedDriverId) {
    return request(`/api/v1/teams/${teamId}/invitations`, {
      method: "POST", auth: true, body: { invited_driver_id: invitedDriverId },
    });
  },

  teamInvitations(teamId) {
    return request(`/api/v1/teams/${teamId}/invitations`, { auth: true });
  },

  revokeTeamInvitation(teamId, invitationId) {
    return request(`/api/v1/teams/${teamId}/invitations/${invitationId}/revoke`, { method: "POST", auth: true });
  },

  resendTeamInvitation(teamId, invitationId) {
    return request(`/api/v1/teams/${teamId}/invitations/${invitationId}/resend`, { method: "POST", auth: true });
  },

  myInvitations() {
    return request("/api/v1/invitations/mine", { auth: true });
  },

  driverProfile(driverId) {
    return request(`/api/v1/public/drivers/${driverId}`);
  },

  // V1.0.3: best lap per day for one track + car model (no arguments: the most recently driven one)
  driverProgress(driverId, track, carModel) {
    const q = track ? `?track=${encodeURIComponent(track)}&car_model=${encodeURIComponent(carModel)}` : "";
    return request(`/api/v1/public/drivers/${driverId}/progress${q}`);
  },

  whoami() {
    return request("/api/v1/accounts/me", { auth: true });
  },

  updateDisplayName(displayName) {
    return request("/api/v1/accounts/me", { method: "PATCH", auth: true, body: { display_name: displayName } });
  },

  resendVerificationEmail() {
    return request("/api/v1/accounts/verify-email/resend", { method: "POST", auth: true });
  },

  confirmEmailVerification(token) {
    return request("/api/v1/accounts/verify-email/confirm", { method: "POST", body: { token } });
  },

  acceptInvitation(invitationId) {
    return request(`/api/v1/invitations/${invitationId}/accept`, { method: "POST", auth: true });
  },

  declineInvitation(invitationId) {
    return request(`/api/v1/invitations/${invitationId}/decline`, { method: "POST", auth: true });
  },

  teamRecords(teamId) {
    return request(`/api/v1/teams/${teamId}/records`, { auth: true });
  },

  teamActivity(teamId, limit = 30) {
    return request(`/api/v1/teams/${teamId}/activity`, { auth: true, params: { limit } });
  },

  // Discord account link — status, one-time link code, unlink.
  // (The old POST /teams/discord-link-code path still exists server-side
  // as a deprecated alias; nothing here uses it any more.)
  discordStatus() {
    return request("/api/v1/accounts/discord", { auth: true });
  },

  createDiscordLinkCode() {
    return request("/api/v1/accounts/discord/link-code", { method: "POST", auth: true });
  },

  unlinkDiscord() {
    return request("/api/v1/accounts/discord", { method: "DELETE", auth: true });
  },

  // --- V0.7.2 §15: admin UI (separate credential domain, see admin_store.js) ---

  adminListReports(adminToken, unresolvedOnly = true) {
    return request("/api/v1/admin/reports", { adminToken, params: { unresolved_only: unresolvedOnly } });
  },

  adminResolveReport(adminToken, reportId, resolution) {
    return request(`/api/v1/admin/reports/${reportId}/resolve`, { method: "POST", adminToken, params: { resolution } });
  },

  adminGetLap(adminToken, lapId) {
    return request(`/api/v1/admin/laps/${lapId}`, { adminToken });
  },

  adminInvalidateLap(adminToken, lapId, reason) {
    return request(`/api/v1/admin/laps/${lapId}/invalidate`, { method: "PATCH", adminToken, body: { reason } });
  },

  adminListDrivers(adminToken, search) {
    return request("/api/v1/admin/drivers", { adminToken, params: { search } });
  },

  adminLockDriver(adminToken, driverId, reason) {
    return request(`/api/v1/admin/drivers/${driverId}/lock`, { method: "POST", adminToken, body: { reason } });
  },

  // Track-limits review queue and what the server learned about car models
  adminReviewQueue(adminToken, status = "pending") {
    return request("/api/v1/admin/review", { adminToken, params: { status } });
  },

  adminReviewLap(adminToken, lapId, decision, note) {
    return request(`/api/v1/admin/laps/${lapId}/review`, { method: "POST", adminToken, body: { decision, note } });
  },

  // Upload outcomes and the database/file consistency
  adminOperations(adminToken, hours = 24, { clientVersion, requestId } = {}) {
    return request("/api/v1/admin/operations", { adminToken, params: { hours, client_version: clientVersion || undefined, request_id: requestId || undefined } });
  },

  adminConsistency(adminToken, deep = false) {
    return request("/api/v1/admin/consistency", { adminToken, params: { deep: deep ? "true" : undefined } });
  },

  // Integrity of the stored originals and the audit log
  adminIntegrity(adminToken) {
    return request("/api/v1/admin/integrity", { adminToken });
  },

  adminVerifyLapIntegrity(adminToken, lapId, release = false, note = undefined) {
    return request(`/api/v1/admin/laps/${lapId}/integrity`, { method: "POST", adminToken, params: { release: release ? "true" : undefined, note } });
  },

  adminAudit(adminToken, { lapId, kind, requestId, limit = 100 } = {}) {
    return request("/api/v1/admin/audit", { adminToken, params: { lap_id: lapId || undefined, kind: kind || undefined, request_id: requestId || undefined, limit } });
  },

  adminAuditVerify(adminToken) {
    return request("/api/v1/admin/audit/verify", { adminToken });
  },

  adminCarIdentity(adminToken) {
    return request("/api/v1/admin/car-identity", { adminToken });
  },

  // The track-limits rules with their tier and effect on this server
  adminTrackLimitsRules(adminToken) {
    return request("/api/v1/admin/track-limits/rules", { adminToken });
  },

  adminUnlockDriver(adminToken, driverId) {
    return request(`/api/v1/admin/drivers/${driverId}/unlock`, { method: "POST", adminToken });
  },

  // The desktop app's analysis window turns the session it got from the
  // desktop client into the usual HttpOnly cookie (see app_bridge.js)
  activateAppSession(sessionToken) {
    return request("/api/v1/accounts/app-session/activate", { method: "POST", body: { session_token: sessionToken } });
  },
  health() {
    return request("/api/v1/health");
  },

  recentActivity(limit = 15) {
    return request("/api/v1/public/activity", { params: { limit } });
  },

  // ---- Lap analysis — server/api/v1/analysis.py
  analysisChannels() {
    return request("/api/v1/public/analysis/channels");
  },

  // Two laps (or one, without lapB) on a common distance grid. `channels` is an
  // array of catalog keys; `step` is the grid spacing in metres.
  // `maxPoints` bounds the points per series (server-side downsampling);
  // `start`/`end` (metres) ask for just one stretch of track.
  // `extra` = up to two more lap ids drawn over Lap 1 / Lap 2 (sides "c", "d").
  analysisCompare({ lapA, lapB, channels, step, maxPoints, start, end, extra }) {
    return request("/api/v1/analysis/compare", {
      auth: true,
      params: {
        lap_a: lapA, lap_b: lapB, channels: channels ? channels.join(",") : undefined, step, max_points: maxPoints, start, end,
        extra: extra && extra.length ? extra.join(",") : undefined,
      },
    });
  },

  // The driven lines of a stretch in high resolution (zoomed track map)
  analysisLine({ lapA, lapB, extra, start, end, step }) {
    return request("/api/v1/analysis/line", {
      auth: true,
      params: { lap_a: lapA, lap_b: lapB, extra: extra && extra.length ? extra.join(",") : undefined, start, end, step },
    });
  },

  // Asphalt, edges and kerbs of the layout, in the map's coordinates
  analysisTrackSurface({ lapA, lapB, extra }) {
    return request("/api/v1/analysis/track-surface", {
      auth: true,
      params: { lap_a: lapA, lap_b: lapB, extra: extra && extra.length ? extra.join(",") : undefined },
    });
  },

  analysisCornerHistory(lapId, limit = 10) {
    return request("/api/v1/analysis/corner-history", { auth: true, params: { lap_id: lapId, limit } });
  },

  analysisLapSectors(lapId) {
    return request("/api/v1/analysis/lap-sectors", { auth: true, params: { lap_id: lapId } });
  },

  // The lap to compare lapId with: kind = "wr" (Garage16 Best) | "team_best" | "my_best".
  analysisReference({ kind, lapId, scope }) {
    return request("/api/v1/analysis/reference", { auth: true, params: { kind, lap_id: lapId, scope } });
  },

  // "You vs ..." in one call; without `track` the track of my most recent lap.
  analysisQuick({ mode, track, carModel, scope }) {
    return request("/api/v1/analysis/quick", { auth: true, params: { mode, track_name: track, car_model: carModel, scope } });
  },

  // How trustworthy the lap's recording is (server/analysis/telemetry_health.py)
  analysisLapHealth(lapId) {
    return request(`/api/v1/laps/${encodeURIComponent(lapId)}/health`, { auth: true });
  },

  analysisLap(lapId) {
    return request(`/api/v1/laps/${encodeURIComponent(lapId)}/analysis`, { auth: true });
  },

  analysisDriverLaps(driverId, { trackName, carModel, limit = 50 } = {}) {
    return request(`/api/v1/drivers/${encodeURIComponent(driverId)}/laps`, {
      auth: true,
      params: { track_name: trackName, car_model: carModel, limit },
    });
  },

  analysisCircuits(q) {
    return request("/api/v1/public/circuits", { params: { q } });
  },

  // ---- V0.9.3 — histograms, lap events, theoretical best, sessions
  analysisDistribution({ lapA, lapB, channels, bins, lo, hi, knee }) {
    return request("/api/v1/analysis/distribution", {
      auth: true,
      params: { lap_a: lapA, lap_b: lapB, channels: (channels || []).join(","), bins, lo, hi, knee },
    });
  },

  analysisEvents({ lapA, lapB }) {
    return request("/api/v1/analysis/events", { auth: true, params: { lap_a: lapA, lap_b: lapB } });
  },

  analysisTheoreticalBest({ trackName, carModel, driverId }) {
    return request("/api/v1/analysis/theoretical-best", {
      auth: true, params: { track_name: trackName, car_model: carModel, driver_id: driverId },
    });
  },

  analysisSessions({ trackName, carModel, limit } = {}) {
    return request("/api/v1/analysis/sessions", { auth: true, params: { track_name: trackName, car_model: carModel, limit } });
  },

  // ---- V0.9.4 — Stint Analyzer (server/api/v1/stints.py)
  stintSessions({ driverId, trackName, carModel, limit } = {}) {
    return request("/api/v1/stints/sessions", {
      auth: true, params: { driver_id: driverId, track_name: trackName, car_model: carModel, limit },
    });
  },

  stintSession(lapId) {
    return request("/api/v1/stints/session", { auth: true, params: { lap_id: lapId } });
  },

  stintDrivers({ trackName, carModel }) {
    return request("/api/v1/stints/drivers", { auth: true, params: { track_name: trackName, car_model: carModel } });
  },

  stintRuleSets(trackName) {
    return request("/api/v1/stints/rule-sets", { auth: true, params: { track_name: trackName } });
  },

  adminRuleSets(adminToken) {
    return request("/api/v1/admin/rule-sets", { adminToken });
  },

  adminSaveRuleSet(adminToken, ruleSet, id) {
    return id
      ? request(`/api/v1/admin/rule-sets/${id}`, { method: "PUT", adminToken, body: ruleSet })
      : request("/api/v1/admin/rule-sets", { method: "POST", adminToken, body: ruleSet });
  },

  adminDeleteRuleSet(adminToken, id) {
    return request(`/api/v1/admin/rule-sets/${id}`, { method: "DELETE", adminToken });
  },

  analysisOutline(trackName) {
    return request("/api/v1/circuits/outline", { auth: true, params: { track_name: trackName } });
  },
};

export { ApiError };
