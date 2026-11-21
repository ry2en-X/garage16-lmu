"""
client_ip.py — who is really calling.

The rate limits (server/core/rate_limit.py) count per client IP. Up to V0.9.7 that IP was the FIRST
entry of X-Forwarded-For whenever the header was present — a header the caller writes himself:
`X-Forwarded-For: 1.2.3.<n>` with a new n per request walked around every limit.

Now a forwarding header is only believed when the request comes FROM a configured proxy
(GARAGE16_TRUSTED_PROXIES), and then it is read from the RIGHT: each proxy appends the address
it received the request from, so the entries a client made up are on the left. Walking from the
right and skipping our own proxies, the first address that is not one of ours is the client —
anything further left is the client's own claim and ignored.

    direct request (no trusted proxy)          -> the socket address, headers ignored
    Caddy (trusted) -> app                     -> what Caddy appended
    Cloudflare -> cloudflared -> Caddy -> app  -> Cloudflare appends the visitor's address;
                                                  cloudflared and Caddy are private addresses
                                                  (trusted), so the walk stops at the visitor

GARAGE16_TRUSTED_PROXIES: comma-separated addresses / networks, or "private" for the private
and loopback ranges (the Docker network the app sits on — the server publishes no port, only the
reverse proxy can reach it). Empty (the default) trusts nobody: the socket address is used.
"""

from __future__ import annotations

import ipaddress
from typing import Iterable, List, Optional, Sequence, Union

Network = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]

PRIVATE = ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7", "fe80::/10")


def parse_trusted(spec: Union[str, Iterable[str], None]) -> List[Network]:
    """'private', '172.18.0.0/16, 10.0.0.5' ... -> networks. Unparseable entries raise ValueError
    (a typo must not silently trust nobody — or everybody)."""
    if spec is None:
        return []
    items = spec.split(",") if isinstance(spec, str) else list(spec)
    out: List[Network] = []
    for item in (i.strip() for i in items):
        if not item:
            continue
        if item.lower() == "private":
            out.extend(ipaddress.ip_network(n) for n in PRIVATE)
        else:
            out.append(ipaddress.ip_network(item, strict=False))
    return out


def _ip(value: str) -> Optional[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]]:
    value = value.strip().strip('"')
    if value.startswith("[") and "]" in value:          # [v6]:port
        value = value[1:value.index("]")]
    elif value.count(":") == 1:                          # v4:port
        value = value.split(":", 1)[0]
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return None
    return addr.ipv4_mapped if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped else addr


def _trusted(addr, networks: Sequence[Network]) -> bool:
    return addr is not None and any(addr in n for n in networks)


def resolve(peer: Optional[str], forwarded_for: Optional[str], networks: Sequence[Network]) -> str:
    """The client address for a request from `peer` carrying `forwarded_for` (X-Forwarded-For)."""
    peer_ip = _ip(peer or "")
    if peer_ip is None:
        return peer or "unknown"
    if not forwarded_for or not _trusted(peer_ip, networks):
        return str(peer_ip)
    hops = [h for h in forwarded_for.split(",") if h.strip()]
    candidate = peer_ip
    for hop in reversed(hops):
        addr = _ip(hop)
        if addr is None:          # garbage in the chain: stop, keep the last address we can vouch for
            break
        candidate = addr
        if not _trusted(addr, networks):
            break
    return str(candidate)


def edge_client_ip(headers, edge_key: Optional[str]) -> Optional[str]:
    """The visitor's address as the Cloudflare Pages function reports it.

    With the website on Cloudflare Pages, a browser's API request reaches the NAS through the
    Pages function (web/functions/_middleware.js) — and every such request then arrives from
    Cloudflare's Workers address, not the visitor's: all visitors would share one rate limit. The
    function therefore sends the visitor's address (CF-Connecting-IP, which only Cloudflare sets) in
    X-Garage16-Client-IP, together with the secret both sides know (GARAGE16_EDGE_KEY in the Pages
    project and in .env). Without the right key the header is ignored: anybody else could write it."""
    import hmac

    if not edge_key:
        return None
    sent_key = headers.get("x-garage16-edge-key") or ""
    if not hmac.compare_digest(sent_key.encode("utf-8"), edge_key.encode("utf-8")):
        return None
    addr = _ip(headers.get("x-garage16-client-ip") or "")
    return str(addr) if addr is not None else None


def client_ip(request) -> str:
    from server.core.config import settings

    from_edge = edge_client_ip(request.headers, settings.edge_key)
    if from_edge:
        return from_edge

    peer = request.client.host if request.client else None
    return resolve(peer, request.headers.get("x-forwarded-for"), settings.trusted_proxies)
