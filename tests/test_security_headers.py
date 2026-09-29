"""Tests for server/main.py's V0.7.2 security-headers middleware and the
production docs-gating (§14 of the completion-sprint audit).

Deliberately avoids importlib.reload(server.main) / reload(server.config):
that re-executes module-level code (including `app = FastAPI(...)` and
`Base.metadata.create_all(...)`), but every OTHER already-imported module
in this process (routers, rate_limit, etc.) holds its own `from .config
import settings` reference to the ORIGINAL Settings instance — reloading
config.py replaces config_module.settings with a new object that those
modules never see, silently forking global state for the rest of the
test session. (Found the hard way: it broke an unrelated upload test
that only ever failed when run after this file.) Settings is a plain
(non-frozen) dataclass and every module shares the one singleton
instance, so monkeypatching an attribute directly on it is visible
everywhere immediately, with automatic teardown — no reload needed for
anything that reads `settings.<field>` at call time rather than at
import time.
"""

from __future__ import annotations

from server.config import settings
from server.main import _docs_urls


def test_security_headers_present_on_every_response(client):
    resp = client.get("/health")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_security_headers_present_on_error_responses_too(client):
    resp = client.get("/telemetry/laps")  # 401, no auth
    assert resp.status_code == 401
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_hsts_absent_when_require_https_is_off(client):
    resp = client.get("/health")
    assert "strict-transport-security" not in resp.headers


def test_hsts_present_when_require_https_is_on(client, monkeypatch):
    # security_headers() reads settings.require_https fresh on every
    # request, so mutating the shared singleton takes effect immediately
    # for the app this test's `client` fixture already wraps.
    monkeypatch.setattr(settings, "require_https", True)
    resp = client.get("/health", headers={"x-forwarded-proto": "https"})
    assert "max-age=31536000" in resp.headers["strict-transport-security"]


# --- docs gating: tested as a pure decision, not via a real app/reload ---

class _FakeSettings:
    def __init__(self, *, is_production: bool, enable_api_docs: bool = False):
        self.is_production = is_production
        self.enable_api_docs = enable_api_docs


def test_docs_urls_enabled_in_development():
    urls = _docs_urls(_FakeSettings(is_production=False))
    assert urls["docs_url"] == "/docs"
    assert urls["openapi_url"] == "/openapi.json"


def test_docs_urls_disabled_in_production_by_default():
    urls = _docs_urls(_FakeSettings(is_production=True))
    assert urls["docs_url"] is None
    assert urls["redoc_url"] is None
    assert urls["openapi_url"] is None


def test_docs_urls_enabled_in_production_when_explicitly_requested():
    urls = _docs_urls(_FakeSettings(is_production=True, enable_api_docs=True))
    assert urls["docs_url"] == "/docs"


def test_docs_route_actually_absent_when_urls_disabled():
    """One end-to-end check that FastAPI really honors docs_url=None (not
    just that our own function returns None) — built as a throwaway app,
    independent of the real server.main.app singleton."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    bare_app = FastAPI(**_docs_urls(_FakeSettings(is_production=True)))
    with TestClient(bare_app) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404


def test_docs_route_actually_present_when_urls_enabled():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    bare_app = FastAPI(**_docs_urls(_FakeSettings(is_production=False)))
    with TestClient(bare_app) as c:
        assert c.get("/docs").status_code == 200


def test_docs_enabled_in_this_projects_dev_test_configuration(client):
    """The real app, as tests actually run it (development env) — docs
    should be reachable, matching _docs_urls' development branch."""
    resp = client.get("/docs")
    assert resp.status_code == 200
