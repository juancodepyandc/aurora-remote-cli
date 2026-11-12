"""Find the current public address of the Aurora bridge.

The Aurora PC opens a Cloudflare *quick tunnel* each time it starts. Quick
tunnels receive a brand new ``*.trycloudflare.com`` hostname on every start,
and the PC publishes it in ``tunnel.txt`` of its public ``aurora-live``
repository. Whenever the saved address stops answering, the client re-reads
that publication instead of asking the user to reconfigure anything.

Readers, freshest first:

1. ``git`` — the smart-HTTP ref advertisement of the repository (served with
   ``Cache-Control: no-store`` and without API quota) gives the current
   commit; ``raw.githubusercontent.com/<commit>/tunnel.txt`` is addressed by
   that commit, so a CDN can never return an older address for it.
2. ``api`` — the GitHub contents API with a persisted ETag: an unchanged
   file answers ``304``, which does not consume the anonymous quota.
3. ``raw`` — the branch file; the CDN may serve it up to five minutes late.

Published content is validated before use: only ``https://<label>.trycloudflare.com``
is accepted, without credentials, port, path, query or fragment. An empty file
means the publisher (or the external watchdog) declared the PC offline.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
import re
import threading
import time
from urllib.parse import urlsplit

import httpx

DEFAULT_PUBLISHER = "juancodepyandc/aurora-live"
DEFAULT_BRANCH = "main"
ALLOWED_SCHEMES = ("http", "https")
_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
_TUNNEL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_SHA = re.compile(rb"[0-9a-f]{40}")
_HEADERS = {"User-Agent": "jobia-cli (tunnel discovery)", "Cache-Control": "no-cache"}


class TunnelOffline(ValueError):
    """The publication is reachable and says that no tunnel is open."""


@dataclass(frozen=True)
class Publication:
    url: str
    source: str
    commit: str = ""


def valid_url(url: str, *, tunnel_only: bool = False) -> str:
    """Return ``url`` when it is an acceptable bridge address, else raise."""
    parsed = urlsplit(url)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.hostname:
        raise ValueError("Adresse de pont invalide")
    if parsed.username or parsed.password:
        raise ValueError("Identifiants dans l'URL non autorisés")
    if tunnel_only and not _TUNNEL.fullmatch(url):
        raise ValueError("Seuls les tunnels trycloudflare sont publiés")
    if tunnel_only and (parsed.port not in (None, 443) or parsed.path
                        or parsed.query or parsed.fragment):
        raise ValueError("Adresse de tunnel publiée invalide")
    return url


def is_ephemeral(url: str) -> bool:
    """Quick-tunnel hostnames die with the process that opened them."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host.endswith(".trycloudflare.com")


def publisher() -> tuple[str, str]:
    """``(owner/repo, branch)`` of the discovery publication.

    ``JOBIA_LIVE_REPO`` (or the ``live_repo`` configuration key) lets a fork
    or a second PC publish its own address without patching the client.
    """
    repo, branch = DEFAULT_PUBLISHER, DEFAULT_BRANCH
    try:
        from aurora_cli import config
        configured = str(config.load().get("live_repo") or "").strip()
    except Exception:
        configured = ""
    candidate = os.environ.get("JOBIA_LIVE_REPO", "").strip() or configured
    if candidate:
        name, _, ref = candidate.partition("@")
        if _REPO.fullmatch(name) and (not ref or re.fullmatch(r"[A-Za-z0-9_./-]{1,100}", ref)):
            repo, branch = name, ref or DEFAULT_BRANCH
    return repo, branch


def api_source(repo: str | None = None) -> str:
    return f"https://api.github.com/repos/{repo or publisher()[0]}/contents/tunnel.txt"


def raw_source(repo: str | None = None, ref: str | None = None) -> str:
    name, branch = publisher()
    return f"https://raw.githubusercontent.com/{repo or name}/{ref or branch}/tunnel.txt"


def _pkt_lines(data: bytes):
    """Decode git pkt-lines; malformed framing raises ``ValueError``."""
    index = 0
    while index + 4 <= len(data):
        size = int(data[index:index + 4], 16)
        if size == 0:
            index += 4
            continue
        if size < 4 or index + size > len(data):
            raise ValueError("Annonce Git tronquée")
        yield data[index + 4:index + size].rstrip(b"\n")
        index += size


def head_commit(client: httpx.Client, repo: str, branch: str) -> str:
    """Current commit of ``branch`` from the uncached git ref advertisement."""
    response = client.get(f"https://github.com/{repo}.git/info/refs",
                          params={"service": "git-upload-pack"}, headers=_HEADERS)
    response.raise_for_status()
    if len(response.content) > 1_048_576:
        raise ValueError("Annonce Git trop volumineuse")
    wanted = f"refs/heads/{branch}".encode()
    for line in _pkt_lines(response.content):
        sha, _, rest = line.partition(b" ")
        if rest.split(b"\0", 1)[0].strip() == wanted and _SHA.fullmatch(sha):
            return sha.decode("ascii")
    raise ValueError("Branche de publication introuvable")


def _published_text(raw: bytes) -> str:
    if len(raw) > 2048:
        raise ValueError("Réponse de découverte trop volumineuse")
    return raw.decode("utf-8").strip().rstrip("/")


def _read_git(client: httpx.Client, repo: str, branch: str) -> Publication:
    commit = head_commit(client, repo, branch)
    response = client.get(raw_source(repo, commit), headers=_HEADERS)
    response.raise_for_status()
    return Publication(_published_text(response.content), "git", commit)


def _etag_store():
    try:
        from aurora_cli.core import locations
        return locations.data_dir() / "tunnel-discovery.json"
    except Exception:
        return None


def _read_api(client: httpx.Client, repo: str) -> Publication:
    store = _etag_store()
    saved: dict = {}
    if store is not None:
        try:
            saved = json.loads(store.read_text("utf-8"))
        except (OSError, ValueError):
            saved = {}
    headers = {**_HEADERS, "Accept": "application/vnd.github.v3+json"}
    if isinstance(saved, dict) and saved.get("repo") == repo and isinstance(saved.get("etag"), str):
        headers["If-None-Match"] = saved["etag"]
    response = client.get(api_source(repo), headers=headers)
    if response.status_code == 304 and isinstance(saved.get("url"), str):
        return Publication(saved["url"], "api")
    response.raise_for_status()
    if len(response.content) > 16384:
        raise ValueError("Réponse de découverte trop volumineuse")
    metadata = response.json()
    if (not isinstance(metadata, dict) or metadata.get("encoding") != "base64"
            or not isinstance(metadata.get("content"), str)):
        raise ValueError("Réponse de découverte invalide")
    content = "".join(metadata["content"].split())
    published = _published_text(base64.b64decode(content, validate=True))
    etag = response.headers.get("ETag")
    if store is not None and etag:
        try:
            store.parent.mkdir(parents=True, exist_ok=True)
            tmp = store.with_suffix(".tmp")
            tmp.write_text(json.dumps({"repo": repo, "etag": etag, "url": published}), "utf-8")
            os.replace(tmp, store)
        except OSError:
            pass
    return Publication(published, "api")


def _read_raw(client: httpx.Client, repo: str, branch: str) -> Publication:
    response = client.get(raw_source(repo, branch), headers=_HEADERS)
    response.raise_for_status()
    return Publication(_published_text(response.content), "raw")


_memo: tuple[float, Publication] | None = None
_memo_lock = threading.Lock()


def read_publication(*, timeout: float = 8.0, max_age: float = 0.0,
                     client_factory=None) -> Publication:
    """Read the published address, freshest reader first.

    ``max_age`` lets many concurrent failures share one network read. Transport
    and HTTP errors move to the next reader; an answer (even empty) from a
    reader is authoritative, and invalid content is always refused.
    """
    global _memo
    with _memo_lock:
        if max_age > 0 and _memo is not None and time.monotonic() - _memo[0] < max_age:
            return _memo[1]
        repo, branch = publisher()
        factory = client_factory or (lambda: httpx.Client(timeout=timeout, follow_redirects=True))
        errors = []
        with factory() as client:
            for reader in (lambda: _read_git(client, repo, branch),
                           lambda: _read_api(client, repo),
                           lambda: _read_raw(client, repo, branch)):
                try:
                    publication = reader()
                except (httpx.HTTPError, ValueError, UnicodeError) as exc:
                    # This reader failed (transport, status, framing, JSON):
                    # the next one reads the same publication another way.
                    errors.append(f"{type(exc).__name__}: {exc}")
                    continue
                if publication.url:
                    # The publication itself is authoritative: invalid content
                    # is refused, never replaced by an older cached copy.
                    valid_url(publication.url, tunnel_only=True)
                _memo = (time.monotonic(), publication)
                return publication
        raise httpx.ConnectError("Publication du tunnel injoignable · " + " | ".join(errors[-3:]))


def current_address(*, timeout: float = 8.0, max_age: float = 0.0, client_factory=None) -> str:
    """Return the published address or raise ``TunnelOffline``."""
    publication = read_publication(timeout=timeout, max_age=max_age, client_factory=client_factory)
    if not publication.url:
        raise TunnelOffline("Le pont distant semble éteint ou son tunnel n'est pas encore publié.")
    return publication.url


def forget() -> None:
    """Drop the in-process memo, e.g. after a server restart was observed."""
    global _memo
    with _memo_lock:
        _memo = None


def health(url: str, *, timeout: float = 6.0, transport=None) -> dict:
    """Unauthenticated reachability of an Aurora bridge, with real latency.

    ``/api/health`` and ``/api/ping`` are public on the bridge; both identify
    the service. A positive answer proves the bridge process answers, not that
    the mission daemon or the inference engines are ready.
    """
    started = time.perf_counter()
    options = {"timeout": httpx.Timeout(timeout, connect=min(timeout, 6.0)), "follow_redirects": True}
    if transport is not None:
        options["transport"] = transport
    last = "pont injoignable"
    try:
        with httpx.Client(**options) as client:
            for path in ("/api/health", "/api/ping"):
                try:
                    response = client.get(url.rstrip("/") + path)
                except httpx.HTTPError as exc:
                    last = f"{type(exc).__name__}: {exc}".strip()
                    break
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                if (response.status_code == 200 and isinstance(payload, dict)
                        and payload.get("service") == "aurora-bridge"
                        and (payload.get("ok") is True or payload.get("status") == "ok")):
                    return {"ok": True, "latency_ms": (time.perf_counter() - started) * 1000,
                            "status": response.status_code}
                last = f"HTTP {response.status_code}"
                if response.status_code in (502, 503, 504, 530):
                    break
    except (httpx.HTTPError, OSError, ValueError) as exc:
        last = f"{type(exc).__name__}: {exc}"
    return {"ok": False, "latency_ms": (time.perf_counter() - started) * 1000, "error": last}
