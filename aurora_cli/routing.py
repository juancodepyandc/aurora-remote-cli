"""Observe remote readiness before accepting work on either host."""
from __future__ import annotations


def remote_readiness(bridge_factory=None):
    from .bridge import Bridge
    factory = bridge_factory or Bridge
    try:
        with factory(timeout=8) as client:
            response = client.doctor()
        ready = bool(response.get('ok') and response.get('ready') is True)
        reason = response.get('error') or '; '.join(
            str(check.get('name', 'Service'))+': '+str(check.get('detail') or 'indisponible')
            for check in response.get('checks', []) if check.get('ok') is False)
        return ready, reason or ('Services disponibles' if ready else 'Services distants indisponibles')
    except Exception as exc:
        return False, str(exc) or type(exc).__name__
