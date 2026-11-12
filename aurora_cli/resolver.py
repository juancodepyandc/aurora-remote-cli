"""DNS fallback for freshly opened quick tunnels.

A new ``*.trycloudflare.com`` hostname can be missing from a local resolver
cache for a few seconds (negative caching), which makes a server that just
restarted look dead. Only for those hostnames, and only after the system
resolver failed, the address is asked to public DNS-over-HTTPS resolvers.
Every other hostname keeps the system resolution untouched.
"""
from __future__ import annotations

import socket

_ORIGINAL = socket.getaddrinfo
_installed = False


def _doh_lookup(host: str) -> list[str]:
    import httpx

    for endpoint in ("https://1.1.1.1/dns-query", "https://dns.google/resolve"):
        try:
            response = httpx.get(endpoint, params={"name": host, "type": "A"},
                                 headers={"accept": "application/dns-json"}, timeout=5.0)
            records = [item for item in response.json().get("Answer", [])
                       if isinstance(item, dict) and isinstance(item.get("data"), str)]
            # A records first; a CNAME target is still resolvable by the system.
            answers = [item["data"] for item in records if item.get("type") == 1]
            answers += [item["data"].rstrip(".") for item in records if item.get("type") == 5]
        except Exception:
            continue
        if answers:
            return answers
    return []


def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    try:
        return _ORIGINAL(host, port, family, type, proto, flags)
    except socket.gaierror:
        name = host.decode() if isinstance(host, bytes) else str(host or "")
        if not name.lower().endswith(".trycloudflare.com"):
            raise
        for address in _doh_lookup(name):
            try:
                return _ORIGINAL(address, port, family, type, proto, flags)
            except socket.gaierror:
                continue
        raise


def install() -> None:
    """Idempotent: installing twice never chains two wrappers."""
    global _installed
    if not _installed:
        socket.getaddrinfo = getaddrinfo
        _installed = True
