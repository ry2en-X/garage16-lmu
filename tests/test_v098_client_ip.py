"""V0.9.8 (P0.1) — a made-up X-Forwarded-For can no longer change the address the rate limits
count. Unit level (every shape of chain), app level (direct requests with spoofed headers) and
through the real Caddy with the shipped Caddyfile."""

from __future__ import annotations

import pytest
import requests

from server.client_ip import parse_trusted, resolve

PRIVATE = parse_trusted("private")


@pytest.mark.parametrize("peer,xff,trusted,expected", [
    ("203.0.113.9", None, PRIVATE, "203.0.113.9"),                              # no header
    ("203.0.113.9", "1.2.3.4", PRIVATE, "203.0.113.9"),                         # direct + spoofed: ignored
    ("203.0.113.9", "1.2.3.4", [], "203.0.113.9"),                              # nobody trusted
    ("172.18.0.5", "198.51.100.7", PRIVATE, "198.51.100.7"),                    # Caddy -> app
    ("172.18.0.5", "1.2.3.4, 198.51.100.7", PRIVATE, "198.51.100.7"),           # client prepended a fake
    ("172.18.0.5", "1.2.3.4, 198.51.100.7, 172.18.0.9", PRIVATE, "198.51.100.7"),  # cloudflared -> Caddy -> app
    ("172.18.0.5", "10.0.0.3, 172.18.0.9", PRIVATE, "10.0.0.3"),                # all ours: the leftmost
    ("172.18.0.5", "garbage, 198.51.100.7", PRIVATE, "198.51.100.7"),
    ("172.18.0.5", "198.51.100.7, not-an-ip", PRIVATE, "172.18.0.5"),           # broken chain: keep the proxy
    ("172.18.0.5", "198.51.100.7:4711", PRIVATE, "198.51.100.7"),               # with port
    ("172.18.0.5", "[2001:db8::1]:443", PRIVATE, "2001:db8::1"),
    ("::ffff:172.18.0.5", "198.51.100.7", PRIVATE, "198.51.100.7"),             # IPv4-mapped peer
    ("172.18.0.5", "198.51.100.7", parse_trusted("172.18.0.0/16"), "198.51.100.7"),
    ("172.19.0.5", "198.51.100.7", parse_trusted("172.18.0.0/16"), "172.19.0.5"),  # not that network
])
def test_the_client_is_the_first_untrusted_address_from_the_right(peer, xff, trusted, expected):
    assert resolve(peer, xff, trusted) == expected


def test_a_typo_in_the_trusted_list_is_an_error_not_silent():
    with pytest.raises(ValueError):
        parse_trusted("172.18.0.0/16, privat")


def test_spoofed_headers_on_direct_requests_do_not_reset_the_limit(client, monkeypatch):
    from server.config import settings

    monkeypatch.setitem(settings.rate_limits, "register", (3, 3600.0))
    codes = [client.post("/accounts/register", params={"display_name": f"Spoof {i}"},
                         headers={"X-Forwarded-For": f"1.2.3.{i}"}).status_code for i in range(6)]
    assert codes[:3] == [200, 200, 200] and set(codes[3:]) == {429}, codes


def test_behind_a_trusted_proxy_different_clients_have_their_own_limit(client, monkeypatch):
    """TestClient's socket address is 'testclient' — so trust everything for this test and let the
    chain decide: two real visitors behind the proxy, each with the limit of one."""
    from server.config import settings

    monkeypatch.setattr(settings, "trusted_proxies", parse_trusted("0.0.0.0/0, ::/0"))
    monkeypatch.setitem(settings.rate_limits, "register", (2, 3600.0))
    import server.client_ip as ci

    monkeypatch.setattr(ci, "_ip", lambda v, _orig=ci._ip: _orig("10.0.0.1") if v.strip() == "testclient" else _orig(v))
    a = [client.post("/accounts/register", params={"display_name": f"A{i}"}, headers={"X-Forwarded-For": "198.51.100.1"}).status_code for i in range(3)]
    b = [client.post("/accounts/register", params={"display_name": f"B{i}"}, headers={"X-Forwarded-For": "198.51.100.2"}).status_code for i in range(2)]
    assert a == [200, 200, 429] and b == [200, 200]


# ---------------------------------------------------------------- through the real Caddy

from tests.test_reverse_proxy import needs_caddy, proxied  # noqa: E402,F401  (fixture)


@needs_caddy
@pytest.mark.parametrize("proxied", ["10.255.255.0/24"], indirect=True)
def test_variant_b_caddy_on_the_internet_discards_the_visitors_header(proxied, monkeypatch):
    """Our test client (127.0.0.1) is NOT one of Caddy's trusted proxies here — like a visitor on
    the internet in front of the VPS: Caddy drops his X-Forwarded-For and writes the real address."""
    from server.config import settings

    base, _direct = proxied
    monkeypatch.setattr(settings, "trusted_proxies", parse_trusted("private"))   # as docker-compose sets it
    monkeypatch.setitem(settings.rate_limits, "register", (3, 3600.0))
    codes = [requests.post(base + "/accounts/register", params={"display_name": f"Caddy B {i}"},
                           headers={"X-Forwarded-For": f"203.0.113.{i}"}, timeout=5).status_code for i in range(5)]
    assert codes[:3] == [200, 200, 200] and set(codes[3:]) == {429}, codes


@needs_caddy
def test_variant_a_behind_cloudflare_only_the_appended_address_counts(proxied, monkeypatch):
    """Shipped Caddyfile; our client plays cloudflared (a private address Caddy trusts) forwarding
    what Cloudflare builds: whatever the visitor claimed, then the address Cloudflare saw. The
    visitor varies his claim — the limit still counts his real address."""
    from server.config import settings

    base, _direct = proxied
    monkeypatch.setattr(settings, "trusted_proxies", parse_trusted("private"))
    monkeypatch.setitem(settings.rate_limits, "register", (3, 3600.0))
    codes = [requests.post(base + "/accounts/register", params={"display_name": f"Caddy A {i}"},
                           headers={"X-Forwarded-For": f"203.0.113.{i}, 198.51.100.50"}, timeout=5).status_code for i in range(5)]
    assert codes[:3] == [200, 200, 200] and set(codes[3:]) == {429}, codes
    # a second real visitor has his own limit
    other = requests.post(base + "/accounts/register", params={"display_name": "Caddy A other"},
                          headers={"X-Forwarded-For": "198.51.100.51"}, timeout=5)
    assert other.status_code == 200
