"""Pairing flow for the remote bridge.

Discovery is intentionally narrow: the only automatic source of a server URL
is the ``tunnel.txt`` file published by the bridge host (see
:mod:`aurora_cli.tunnel`), and whatever it contains is validated before it is
trusted. Anything typed by hand or coming from the environment has to satisfy
the same checks.
"""
from __future__ import annotations

import os
import socket
import time

import httpx

from aurora_cli import brand, config, display, tunnel
from aurora_cli.terminal import target_label

TUNNEL_SOURCE = tunnel.api_source(tunnel.DEFAULT_PUBLISHER)
TUNNEL_RAW_SOURCE = tunnel.raw_source(tunnel.DEFAULT_PUBLISHER, tunnel.DEFAULT_BRANCH)
ALLOWED_SCHEMES = tunnel.ALLOWED_SCHEMES
_valid_url = tunnel.valid_url


def discover_tunnel() -> str:
    """Read the current bridge address from its freshest publication.

    Concurrent failures within a few seconds share one read. An empty
    publication means the PC is off (``TunnelOffline``, a ``ValueError``).
    """
    return tunnel.current_address(max_age=8.0)


def _register(url: str, key: str) -> httpx.Response:
    return httpx.post(
        f"{url}/api/cli/register",
        headers={"Authorization": f"Bearer {key}"},
        json={"device_name": socket.gethostname(), "client_key": key},
        timeout=10.0,
    )


def connect(server: str = "", api_key: str = "", *, then_interactive: bool = True) -> bool:
    """Register this device against a bridge and remember the credentials."""
    try:
        explicit = (server or os.environ.get(brand.env("server_url"))
                    or os.environ.get(brand.env_legacy("server_url")) or "")
        url = (server or config.resolve_server_url() or discover_tunnel()).rstrip("/")
        # Saved addresses and ephemeral quick tunnels follow the publication:
        # a quick-tunnel hostname is dead for good once its PC restarted.
        auto_discover = not explicit or tunnel.is_ephemeral(url)
        _valid_url(url)
        display.header('CONNEXION', 'Appairage avec le PC Aurora')
        display.kv('Cible', target_label(url))

        key = api_key or config.get("api_key")
        if not key:
            from aurora_cli.device_pairing import pair_device
            key=pair_device(url)

        try:
            started = time.monotonic()
            response = _register(url, key)
            # A cached tunnel can answer 5xx while the old edge is draining.
            if auto_discover and response.status_code in (502, 503, 504, 530):
                response.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError):
            if not auto_discover:
                raise
            display.warning("Le tunnel enregistré ne répond plus. Recherche de l'adresse actuelle...")
            refreshed = discover_tunnel()
            if refreshed == url:
                raise
            url = refreshed
            display.kv('Nouvelle cible', target_label(url))
            started = time.monotonic()
            response = _register(url, key)

        if response.status_code == 401 and not api_key:
            from aurora_cli.device_pairing import pair_device
            key=pair_device(url)
            started = time.monotonic()
            response=_register(url,key)
        if response.status_code == 401:
            display.error(
                "Clé absente, invalide ou révoquée. Fournissez une clé déjà autorisée par le pont."
            )
            return False
        response.raise_for_status()
        if not response.json().get("ok"):
            display.error("Le pont a refusé l'enregistrement de cet appareil.")
            return False

        config.set_key("server_url", url)
        config.set_key("api_key", key)
        display.success(f"Connexion établie : {target_label(url)}")
        display.kv('Aller-retour enregistrement', f'{(time.monotonic()-started)*1000:.0f} ms')

        if then_interactive:
            from aurora_cli.bridge import Bridge
            from aurora_cli.interactive import run_interactive

            bridge = Bridge(server_url=url, api_key=key, timeout=30.0)
            try:
                run_interactive(bridge)
            finally:
                bridge.close()
        return True
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        display.error(f"Connexion impossible : {exc}")
        return False
