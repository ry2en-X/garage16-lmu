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
import os
import time
import uuid
from pathlib import Path
from typing import Iterator, Optional

import requests

from client.telemetry.recorder import LapRecorder

logger = logging.getLogger("lmu_garage.uploader")

CLIENT_VERSION = "0.9.2"
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


# Chunk size for hashing and for streaming the multipart body (V0.9.1). A lap
# file is several MiB; reading it in 1 MiB pieces keeps the extra RAM at
# O(chunk) instead of O(file) — and the file is read straight from disk each
# time, never copied into one big bytes object.
STREAM_CHUNK_BYTES = 1024 * 1024


def sha256_file(path: Path, chunk_size: int = STREAM_CHUNK_BYTES) -> str:
    """Hex sha256 of a file, read in chunks. Same digest as
    hashlib.sha256(path.read_bytes()) without holding the file in memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


class StreamingMultipart:
    """A multipart/form-data request body that streams one file from disk.

    Why not `requests.post(files=...)`: requests assembles the whole multipart
    body as a single bytes object before sending, i.e. the file is held in RAM
    (measured: ~2x the file size at peak). This object instead exposes read()
    / __iter__ / __len__, which requests turns into a streamed body with an
    exact Content-Length (no chunked transfer encoding, so the reverse proxy
    and the server see an ordinary upload).

    The parquet file is NOT compressed again on the way: it already is
    Parquet+zstd, a second layer would cost CPU and save next to nothing.

    Use as a context manager so the file handle is always closed. Create a new
    instance for every attempt (a retry must start from byte 0).
    """

    def __init__(self, fields: dict, file_field: str, path: Path, filename: Optional[str] = None,
                 content_type: str = "application/octet-stream", chunk_size: int = STREAM_CHUNK_BYTES):
        self.boundary = uuid.uuid4().hex
        self.content_type = f"multipart/form-data; boundary={self.boundary}"
        self._chunk_size = chunk_size
        self._path = Path(path)
        self._handle = None
        name = filename or self._path.name
        head = []
        for key, value in fields.items():
            head.append(
                f"--{self.boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f"{value}\r\n"
            )
        head.append(
            f"--{self.boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field}"; filename="{name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        )
        self._head = "".join(head).encode("utf-8")
        self._tail = f"\r\n--{self.boundary}--\r\n".encode("utf-8")
        self._file_size = 0
        self._stage = 0  # 0 = head, 1 = file, 2 = tail, 3 = done
        self._head_pos = 0
        self._tail_pos = 0
        self._file_left = 0

    def __enter__(self) -> "StreamingMultipart":
        self._handle = self._path.open("rb")
        # fstat on the OPEN handle: the size we promise in Content-Length is the
        # size of exactly the file we are going to read.
        self._file_size = os.fstat(self._handle.fileno()).st_size
        self._file_left = self._file_size
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __len__(self) -> int:
        return len(self._head) + self._file_size + len(self._tail)

    def read(self, size: int = -1) -> bytes:
        if self._handle is None:
            raise ValueError("StreamingMultipart must be used inside a `with` block")
        if size is None or size < 0:
            size = len(self)
        out = bytearray()
        while len(out) < size and self._stage < 3:
            room = size - len(out)
            if self._stage == 0:
                piece = self._head[self._head_pos:self._head_pos + room]
                self._head_pos += len(piece)
                out += piece
                if self._head_pos >= len(self._head):
                    self._stage = 1
            elif self._stage == 1:
                want = min(room, self._file_left, self._chunk_size)
                piece = self._handle.read(want) if want > 0 else b""
                if want > 0 and not piece:
                    raise OSError(f"{self._path.name} became shorter while uploading")
                self._file_left -= len(piece)
                out += piece
                if self._file_left <= 0:
                    self._stage = 2
            else:
                piece = self._tail[self._tail_pos:self._tail_pos + room]
                self._tail_pos += len(piece)
                out += piece
                if self._tail_pos >= len(self._tail):
                    self._stage = 3
        return bytes(out)

    def __iter__(self) -> Iterator[bytes]:
        while True:
            chunk = self.read(self._chunk_size)
            if not chunk:
                return
            yield chunk


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
        # V0.9.2: the server's id of the lap uploaded last (the client window offers
        # "Analyse last lap" for it). None until a lap was uploaded in this run.
        self.last_uploaded_lap_id: Optional[int] = None

    def _sign(self, payload_bytes: bytes) -> str:
        return hmac.new(self.client_secret, payload_bytes, hashlib.sha256).hexdigest()

    def _build_payload(self, meta_path: Path) -> dict:
        raw_metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        parquet_path = meta_path.parent / raw_metadata["telemetry_file"]

        # Hash the raw telemetry bytes so the server can detect any
        # mismatch between what was recorded and what arrived. Streamed in
        # chunks (V0.9.1): the file is never held in memory just to be hashed.
        telemetry_hash = sha256_file(parquet_path)

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
        fields = {
            "envelope": json.dumps(payload["envelope"]),
            "signature": payload["signature"],
        }
        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        url = f"{self.backend_url}{UPLOAD_ENDPOINT}"

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                # Streamed from disk with an exact Content-Length (see
                # StreamingMultipart); a fresh body object per attempt.
                with StreamingMultipart(fields, "telemetry", payload["telemetry_path"]) as body:
                    resp = requests.post(
                        url, data=body, timeout=30,
                        headers={**headers, "Content-Type": body.content_type},
                    )
                if resp.status_code == 200:
                    self.recorder.mark_uploaded(meta_path)
                    logger.info("Uploaded %s", meta_path.name)
                    try:
                        lap_id = resp.json().get("lap_id")
                        if isinstance(lap_id, int) and not isinstance(lap_id, bool):
                            self.last_uploaded_lap_id = lap_id
                    except (ValueError, AttributeError):
                        pass  # the id is a convenience; the upload itself succeeded
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
                            from client.edition import download_url_for_this_edition

                            min_version = detail.get("min_client_version")
                            download_url = download_url_for_this_edition(detail, "download_url", "app_download_url")
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
