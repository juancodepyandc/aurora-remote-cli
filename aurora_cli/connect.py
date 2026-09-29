"""Pairing flow for the remote bridge.

Discovery is intentionally narrow: the only automatic source of a server URL
is the ``tunnel.txt`` file published by the bridge host, and whatever it
contains is validated before it is trusted. Anything typed by hand or coming
from the environment has to satisfy the same checks.
"""
from __future__ import annotations

import base64
import os
import socket
import time
from urllib.parse import urlsplit

import click
import httpx

from aurora_cli import brand, config, display

TUNNEL_SOURCE = (
    "https://api.github.com/repos/juancodepyandc/aurora-live/contents/tunnel.txt"
)
ALLOWED_SCHEMES = ("http", "https")


def _valid_url(url: str, *, tunnel_only: bool = False) -> str:
    """Return ``url`` if it is an acceptable bridge address, else raise."""
    parsed = urlsplit(url)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.hostname:
        raise ValueError("Adresse de pont invalide")
    if parsed.username or parsed.password:
        raise ValueError("Identifiants dans l'URL non autorisés")
    if tunnel_only and not parsed.hostname.endswith(".trycloudflare.com"):
        raise ValueError("Seuls les tunnels trycloudflare sont publiés")
    if tunnel_only and (parsed.port not in (None, 443) or parsed.path
                        or parsed.query or parsed.fragment):
        raise ValueError("Adresse de tunnel publiée invalide")
    return url


def discover_tunnel() -> str:
    """Read the current bridge address from its published file."""
    response = httpx.get(
        TUNNEL_SOURCE,
        params={"_t": str(int(time.time()))},
        headers={"Cache-Control": "no-cache", "Accept": "application/vnd.github.v3+json"},
        timeout=10.0,
    )
    response.raise_for_status()
    metadata = response.json()
    if metadata.get("encoding") != "base64":
        raise ValueError("Réponse de découverte invalide")
    published = base64.b64decode(metadata["content"]).decode("utf-8").strip().rstrip("/")
    if not published:
        raise ValueError("Le pont distant semble éteint ou son tunnel n'est pas encore publié.")
    _valid_url(published, tunnel_only=True)
    return published


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
        auto_discover = not server and not os.environ.get(brand.env_legacy("server_url"))
        url = (server or config.resolve_server_url() or discover_tunnel()).rstrip("/")
        _valid_url(url)

        key = api_key or config.get("api_key")
        if not key:
            key = click.prompt("Clé autorisée par l'administrateur du pont", hide_input=True)

        try:
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
            response = _register(url, key)

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
        display.success(f"Connexion établie : {url}")

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
