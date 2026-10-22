"""V0.9.8 — the API contract, from the app's own OpenAPI description.

  * tests/reference/openapi.json is the reviewed contract: every path, method, parameter and
    response schema. Any change to it fails here until the snapshot is regenerated on purpose
    (GARAGE16_UPDATE_OPENAPI=1) — so an accidental change to a response shape or a removed endpoint
    cannot slip into a release unnoticed; the diff of the snapshot is the review.
  * the endpoints the shipped desktop client (0.9.4) calls exist with the methods, fields and
    answers it relies on — checked separately, because they must not change even on purpose
    without a new client: the client is not updated with the server.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SNAPSHOT = Path(__file__).parent / "reference" / "openapi.json"

# What client 0.9.4 calls (client/uploader, client/migration, client/update_notice,
# client/analysis_window, client/config) — method, path, and the response fields it reads.
CLIENT_CONTRACT = {
    ("post", "/telemetry/upload"): {"form": {"telemetry", "envelope", "signature"}, "response": {"status", "lap_id", "duplicate"}},
    ("get", "/health"): {"query": {"client_version"}, "response": set()},
    ("post", "/accounts/app-session"): {},
    ("post", "/accounts/app-session/activate"): {},
    ("post", "/accounts/rotate-secret"): {},
    ("post", "/accounts/rotate-token"): {},
}


def _normalized():
    from server.main import app

    spec = app.openapi()
    return {"paths": spec["paths"], "components": spec.get("components", {})}


def test_the_api_matches_the_reviewed_contract():
    current = json.loads(json.dumps(_normalized(), sort_keys=True))
    if os.environ.get("GARAGE16_UPDATE_OPENAPI") == "1" or not SNAPSHOT.exists():
        SNAPSHOT.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        assert os.environ.get("GARAGE16_UPDATE_OPENAPI") == "1", f"{SNAPSHOT} did not exist — written; commit it"
        return
    reviewed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    removed = sorted(set(reviewed["paths"]) - set(current["paths"]))
    added = sorted(set(current["paths"]) - set(reviewed["paths"]))
    changed = sorted(p for p in set(reviewed["paths"]) & set(current["paths"]) if reviewed["paths"][p] != current["paths"][p])
    schemas = sorted(k for k in set(reviewed["components"].get("schemas", {})) | set(current["components"].get("schemas", {}))
                     if reviewed["components"].get("schemas", {}).get(k) != current["components"].get("schemas", {}).get(k))
    assert not (removed or added or changed or schemas), (
        f"The API changed — removed: {removed}, added: {added}, changed: {changed}, schemas: {schemas}. "
        "If intended: GARAGE16_UPDATE_OPENAPI=1 pytest tests/test_v098_api_contract.py, review the diff of "
        "tests/reference/openapi.json, note it in CHANGELOG.md.")


def test_what_the_shipped_desktop_client_calls_is_still_there():
    paths = _normalized()["paths"]
    spec = _normalized()
    for (method, path), needs in CLIENT_CONTRACT.items():
        assert path in paths and method in paths[path], f"client 0.9.4 calls {method.upper()} {path}"
        op = paths[path][method]
        params = {p["name"] for p in op.get("parameters", [])}
        assert needs.get("query", set()) <= params, (path, params)
        if "form" in needs:
            ref = op["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"].rsplit("/", 1)[1]
            fields = set(spec["components"]["schemas"][ref]["properties"])
            assert needs["form"] <= fields, (path, fields)
        if needs.get("response"):
            ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]
            assert needs["response"] <= set(spec["components"]["schemas"][ref]["properties"]), path


def test_the_error_shapes_the_client_reads_stay():
    """426 UPDATE_REQUIRED (detail.code) and 503 MAINTENANCE (transient: the lap is kept) — the
    client decides by status and detail.code."""
    import inspect

    from server.routers import telemetry

    src = inspect.getsource(telemetry)
    assert '"code": "UPDATE_REQUIRED"' in src and "HTTP_426_UPGRADE_REQUIRED" in src
    assert '"code": "MAINTENANCE"' in src and "HTTP_503_SERVICE_UNAVAILABLE" in src
    from client.uploader.uploader import _is_transient_status

    assert _is_transient_status(503) and not _is_transient_status(400)
