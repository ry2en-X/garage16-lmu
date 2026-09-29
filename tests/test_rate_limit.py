"""Tests for the rate limiter (server/rate_limit.py) as applied to real
endpoints. Uses a low limit injected directly into settings.rate_limits
rather than waiting out the real hour-long windows."""

from server.config import settings


def test_register_endpoint_rate_limited_per_ip(client):
    original = settings.rate_limits["register"]
    settings.rate_limits["register"] = (2, 3600)  # 2 per hour for this test
    try:
        r1 = client.post("/accounts/register", params={"display_name": "A"})
        r2 = client.post("/accounts/register", params={"display_name": "B"})
        r3 = client.post("/accounts/register", params={"display_name": "C"})
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r3.status_code == 429
    finally:
        settings.rate_limits["register"] = original


def test_rate_limit_is_scoped_per_bucket_not_global():
    """Hitting the 'register' limit must not affect an unrelated bucket."""
    from server.rate_limit import _check, reset_all

    reset_all()
    assert _check("register", "ip:1.2.3.4", 1, 3600) is True
    assert _check("register", "ip:1.2.3.4", 1, 3600) is False  # exhausted
    assert _check("upload", "ip:1.2.3.4", 1, 3600) is True  # different bucket, unaffected


def test_window_expiry_resets_the_limit():
    import time
    from server.rate_limit import _check, reset_all

    reset_all()
    assert _check("test_bucket", "ip:5.5.5.5", 1, 0.05) is True
    assert _check("test_bucket", "ip:5.5.5.5", 1, 0.05) is False
    time.sleep(0.1)
    assert _check("test_bucket", "ip:5.5.5.5", 1, 0.05) is True  # window rolled over
