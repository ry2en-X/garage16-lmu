"""
uploader.py — Watches for locally-recorded laps that haven't been uploaded
yet, packages them (telemetry + metadata + a signature), and POSTs them to
the backend's /telemetry/upload endpoint.

Signing: the anti-cheat design in the architecture doc wants every upload
hashed/signed client-side. We don't have a real key-management story here
yet (that needs a decision: per-install keypair? account-bound API token?),
so this implements a placeholder HMAC signature using a locally-stored
client secret, clearly marked so it's swapped for the real scheme before
this ships to real users. As-is, this stops naive tampering with the
upload payload in transit, not a determined attacker with the secret. See
server/security.py's docstring: this is authentication + integrity, not
anti-cheat, even once a real key-management story replaces this
placeholder.

Retry policy: only *transient* failures are retried (network errors, 408,
429, 5xx) — the kind where trying again later might actually succeed. A
permanent 4xx (bad request, invalid signature, payload too large, client
too old, ...) will fail exactly the same way on every retry, so it's
marked `rejected` on disk (see recorder.mark_rejected) instead of retried
forever — V0.5.2/early V0.6.0 kept offering these every 15s cycle with no
way for a human to notice or intervene.

401 is handled separately from other permanent failures (see _AuthAbort):
an invalid/revoked token means every other pending lap in this cycle
would fail identically, so run_once() aborts the whole cycle rather than
burning through the queue for nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from typing import Optional

import requests

from client.telemetry.recorder import LapRecorder

logger = logging.getLogger("lmu_garage.uploader")

CLIENT_VERSION = "0.8.6"
DEFAULT_BACKEND_URL = "https://api.example-lmu-garage.invalid"  # placeholder, replace with real backend
UPLOAD_ENDPOINT = "/telemetry/upload"
MAX_RETRIES = 5
RETRY_BACKOFF_SECONDS = 3
# Statuses worth retrying: request timeout, rate limiting, and any server
# error — all can plausibly succeed on a later attempt. Everything else in
# the 4xx range (bad request, size limit, ...) means this exact request
# will never succeed, so retrying is pointless. 401 is handled separately
# (see _AuthAbort) since it means the token itself is bad, not this request.
_TRANSIENT_STATUS_CODES = {408, 429}

# Allowlist of fields the server's LapMetadata schema actually accepts
# (server/schemas.py) — NOT a denylist of local-only fields. A denylist
# has to be remembered and updated every time a new local bookkeeping
# field is added (this is exactly how the P0-1 bug happened: `uploaded`
# was added to the metadata dict and nobody remembered to exclude it
# here). An allowlist fails safe: a new local field (like V0.6.0's
# `rejected`/`rejected_reason`) is silently dropped here instead of
# crashing every upload with a 400.
#
# V0.6.3: added car_class/car_model — the reverse risk of the allowlist
# design applies here: a field ADDED to the server schema that ISN'T
# added here just silently never gets sent, no error either way. Adding
# a new server-accepted field means updating both schemas.py AND this
# set, deliberately, not automatically.
_SERVER_FIELDS = {
    "track_name", "car_name", "session_type", "lap_number", "lap_time",
    "sector_times", "is_valid", "started_at", "recorded_at",
    "ambient_temp", "track_temp", "sample_count", "telemetry_file",
    "car_class", "car_model",
}


def _is_transient_status(status_code: int) -> bool:
    return status_code in _TRANSIENT_STATUS_CODES or status_code >= 500


class _AuthAbort(Exception):
    """Internal signal: the auth token itself is bad (401) — abort the
    whole run_once() cycle rather than retrying every other pending lap
    with the same dead token."""


class UpdateRequired(Exception):
    """The server answered 426 UPDATE_REQUIRED: this client is older than
    the server's minimum. Like _AuthAbort it stops the whole upload cycle
    (every remaining lap would fail identically) — but unlike a
    permanent rejection, the lap is NOT marked rejected: nothing is wrong
    with the lap itself, so it stays pending and uploads after the friend
    updates."""

    def __init__(self, min_version, download_url):
        super().__init__("update required")
        self.min_version = min_version
        self.download_url = download_url


class Uploader:
    def __init__(
        self,
        recorder: LapRecorder,
        backend_url: str = DEFAULT_BACKEND_URL,
        client_secret: Optional[str] = None,
        auth_token: Optional[str] = None,
    ):
        self.recorder = recorder
        self.backend_url = backend_url.rstrip("/")
        # Set by run_once() when the server answers 426 UPDATE_REQUIRED;
        # client/main.py's upload loop reads and reports it (V0.8.6).
        self.update_required = None
        # Set by run_once() when the server answers 401 (login revoked/
        # invalid) — client/main.py shows a 'Reconnect account' prompt.
        self.auth_rejected = False
        # client_secret signs payloads; auth_token identifies the driver's account.
        # Both should come from a config file / secure storage, not hardcoded.
        self.client_secret = (client_secret or "unset-dev-secret").encode()
        self.auth_token = auth_token

    def _sign(self, payload_bytes: bytes) -> str:
        return hmac.new(self.client_secret, payload_bytes, hashlib.sha256).hexdigest()

    def _build_payload(self, meta_path: Path) -> dict:
        raw_metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        parquet_path = meta_path.parent / raw_metadata["telemetry_file"]
        telemetry_bytes = parquet_path.read_bytes()

        # Hash the raw telemetry bytes so the server can detect any
        # mismatch between what was recorded and what arrived.
        telemetry_hash = hashlib.sha256(telemetry_bytes).hexdigest()

        wire_metadata = {k: v for k, v in raw_metadata.items() if k in _SERVER_FIELDS}

        envelope = {
            "metadata": wire_metadata,
            "telemetry_hash": telemetry_hash,
            "client_version": CLIENT_VERSION,
            "sent_at": time.time(),
        }
        envelope_bytes = json.dumps(envelope, sort_keys=True).encode()
        signature = self._sign(envelope_bytes)

        return {
            "envelope": envelope,
            "signature": signature,
            "telemetry_path": parquet_path,
        }

    def upload_one(self, meta_path: Path) -> bool:
        payload = self._build_payload(meta_path)
        data = {
            "envelope": json.dumps(payload["envelope"]),
            "signature": payload["signature"],
        }
        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        url = f"{self.backend_url}{UPLOAD_ENDPOINT}"

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with payload["telemetry_path"].open("rb") as telemetry_file:
                    files = {
                        "telemetry": (
                            payload["telemetry_path"].name,
                            telemetry_file,
                            "application/octet-stream",
                        ),
                    }
                    resp = requests.post(url, data=data, files=files, headers=headers, timeout=30)
                if resp.status_code == 200:
                    self.recorder.mark_uploaded(meta_path)
                    logger.info("Uploaded %s", meta_path.name)
                    return True

                if resp.status_code == 401:
                    # The token itself is bad (revoked, locked account, or
                    # never was valid) — every other pending lap in this
                    # cycle would fail identically. Abort the cycle
                    # instead of burning through the whole queue for
                    # nothing; run_once() catches this and stops.
                    logger.error(
                        "Upload rejected with 401 (token invalid/revoked/locked) for "
                        "%s — aborting this upload cycle. Reconnect your account "
                        "(button in the Garage16 window, or `--reconfigure` for developers).",
                        meta_path.name,
                    )
                    raise _AuthAbort()

                if resp.status_code == 426:
                    min_version, download_url = None, None
                    try:
                        detail = resp.json().get("detail")
                        if isinstance(detail, dict):
                            min_version = detail.get("min_client_version")
                            download_url = detail.get("download_url")
                    except ValueError:
                        pass
                    logger.error(
                        "Server requires a newer client (UPDATE_REQUIRED, minimum %s) — "
                        "keeping %s pending, not marking it rejected.",
                        min_version, meta_path.name,
                    )
                    raise UpdateRequired(min_version, download_url)

                if not _is_transient_status(resp.status_code):
                    # Permanent failure: this exact request will fail the
                    # same way every time (bad signature, invalid
                    # metadata, file too large, client too old, ...).
                    # Mark it rejected so pending_uploads() stops offering
                    # it every cycle.
                    reason = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    logger.error(
                        "Upload permanently rejected for %s: %s — marking rejected, "
                        "not retrying.",
                        meta_path.name, reason,
                    )
                    self.recorder.mark_rejected(meta_path, reason)
                    return False

                logger.warning(
                    "Upload failed transiently (attempt %d/%d) for %s: HTTP %d %s",
                    attempt, MAX_RETRIES, meta_path.name, resp.status_code, resp.text[:200],
                )
            except requests.RequestException as exc:
                logger.warning(
                    "Upload failed (attempt %d/%d) for %s: %s",
                    attempt, MAX_RETRIES, meta_path.name, exc,
                )
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

        logger.error("Giving up on %s after %d attempts (will retry next cycle)", meta_path.name, MAX_RETRIES)
        return False

    def run_once(self) -> int:
        """Uploads all currently-pending laps. Returns count of successful
        uploads. Isolates exceptions per lap — a single corrupt/poisoned
        lap file (e.g. missing parquet, unreadable JSON) no longer aborts
        the whole cycle and blocks every lap after it in iteration order;
        it's logged and skipped, and the rest of the queue still runs.
        The one exception is _AuthAbort: a bad token would fail
        identically for every remaining lap, so that DOES stop the cycle
        rather than retrying it N times for nothing."""
        successes = 0
        self.update_required = None  # re-evaluated fresh every cycle
        self.auth_rejected = False
        for meta_path in self.recorder.pending_uploads():
            try:
                if self.upload_one(meta_path):
                    successes += 1
            except _AuthAbort:
                self.auth_rejected = True
                break
            except UpdateRequired as exc:
                self.update_required = exc
                break
            except Exception:
                logger.exception(
                    "Unexpected error uploading %s — skipping this lap, "
                    "continuing with the rest of the queue.",
                    meta_path.name,
                )
        return successes

    def run_forever(self, poll_seconds: int = 15) -> None:
        logger.info("Uploader watching for pending laps every %ds", poll_seconds)
        while True:
            uploaded = self.run_once()
            if uploaded:
                logger.info("Uploaded %d lap(s) this cycle", uploaded)
            time.sleep(poll_seconds)
